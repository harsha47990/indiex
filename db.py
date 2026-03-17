"""
db.py — Data access layer for Indiex
═════════════════════════════════════
All reads/writes to the user store go through this module.
Each user is stored as a separate JSON file: data/users/{username}.json
This eliminates single-file bottleneck and race conditions on concurrent writes.
To migrate to SQLite or PostgreSQL, change only the function bodies below.
"""

import json
import uuid
import logging
from pathlib import Path
from time import time
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
USERS_DIR = BASE_DIR / "data" / "users"

# Ensure directory exists at import time
USERS_DIR.mkdir(parents=True, exist_ok=True)

# In-memory session cache: session_key → username
# Avoids O(n) file scan on every verify_session / heartbeat.
# Populated lazily — first lookup after restart falls back to scan.
_session_cache: dict[str, str] = {}


# ═══════════════════════════════════════════════════════════════════════════
#  LOW-LEVEL PER-USER FILE I/O  (private)
# ═══════════════════════════════════════════════════════════════════════════

def _user_path(username: str) -> Path:
    """Return the file path for a user's JSON file."""
    return USERS_DIR / f"{username}.json"


def _load_user(username: str) -> Optional[dict]:
    """Read a single user from disk. Returns None if not found."""
    path = _user_path(username)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if "coins" not in data:
            data["coins"] = 0
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to read user file %s: %s", path, e)
        return None


def _save_user(data: dict):
    """Write a single user to disk."""
    path = _user_path(data["username"])
    try:
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as e:
        logger.error("Failed to write user file %s: %s", path, e)
        raise


def _iter_all_users() -> list[dict]:
    """Read all user files from the users directory."""
    users = []
    for f in USERS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if "coins" not in data:
                data["coins"] = 0
            users.append(data)
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Skipping corrupt user file %s: %s", f, e)
    return users


# ═══════════════════════════════════════════════════════════════════════════
#  USER CRUD
# ═══════════════════════════════════════════════════════════════════════════

def get_user_by_username(username: str) -> Optional[dict]:
    """Look up one user by username. Returns full dict including hash."""
    return _load_user(username)


def get_all_users() -> list[dict]:
    """Return all users with password_hash stripped."""
    return [
        {k: v for k, v in u.items() if k != "password_hash"}
        for u in _iter_all_users()
    ]


def export_all_users() -> dict:
    """Return all users (WITH password hashes) in the old combined format.
    Used for backup/export: {"users": [...]}"""
    return {"users": _iter_all_users()}


# Required fields every user object must have for import
_REQUIRED_FIELDS = {
    "username", "password_hash", "display_name", "role",
    "must_reset_password", "coins",
}


def validate_import_data(data: dict) -> tuple[bool, str]:
    """Validate an import payload. Returns (ok, error_message)."""
    if not isinstance(data, dict) or "users" not in data:
        return False, 'JSON must have a top-level "users" array'
    users = data["users"]
    if not isinstance(users, list) or len(users) == 0:
        return False, '"users" must be a non-empty array'
    for i, u in enumerate(users):
        if not isinstance(u, dict):
            return False, f"Entry #{i+1} is not an object"
        missing = _REQUIRED_FIELDS - u.keys()
        if missing:
            return False, f"User '{u.get('username', f'#{i+1}')}' is missing: {', '.join(sorted(missing))}"
        if not isinstance(u["username"], str) or not u["username"].strip():
            return False, f"Entry #{i+1} has an empty/invalid username"
    return True, ""


def import_users(data: dict) -> dict:
    """Import users from the combined format.
    Replaces existing users present in the file; leaves others untouched.
    Returns {"imported": int, "skipped": list}."""
    users = data["users"]
    imported = 0
    skipped = []
    for u in users:
        username = u["username"].strip()
        # Ensure coins field exists
        if "coins" not in u:
            u["coins"] = 0
        try:
            _save_user(u)
            imported += 1
        except Exception as e:
            skipped.append(f"{username}: {e}")
            logger.error("Import failed for '%s': %s", username, e)
    logger.info("Import complete — %d imported, %d skipped", imported, len(skipped))
    return {"imported": imported, "skipped": skipped}


def create_user(
    username: str,
    display_name: str,
    password_hash: str,
    role: str = "user",
    created_by: str = "admin",
) -> dict:
    """Insert a new user. Returns the created user dict (without hash).
    Raises ValueError if username already exists."""
    from datetime import datetime, timezone

    if not username.isalnum():
        raise ValueError("Username must contain only letters and numbers")
    if _user_path(username).exists():
        raise ValueError(f"Username '{username}' already exists")

    new_user = {
        "id": str(uuid.uuid4()),
        "username": username,
        "password_hash": password_hash,
        "display_name": display_name,
        "role": role,
        "must_reset_password": True,
        "coins": 0,
        "session_key": None,
        "last_activity": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": created_by,
    }
    _save_user(new_user)
    logger.info("User '%s' created by '%s'", username, created_by)
    return {k: v for k, v in new_user.items() if k != "password_hash"}


def delete_user(username: str) -> bool:
    """Delete a user. Cannot delete the last admin. Raises ValueError if so."""
    user = _load_user(username)
    if not user:
        return False

    if user["role"] == "admin":
        admin_count = sum(1 for u in _iter_all_users() if u["role"] == "admin")
        if admin_count <= 1:
            raise ValueError("Cannot delete the last admin user")

    path = _user_path(username)
    try:
        path.unlink()
        logger.info("User '%s' deleted", username)
        return True
    except OSError as e:
        logger.error("Failed to delete user file %s: %s", path, e)
        return False


# ═══════════════════════════════════════════════════════════════════════════
#  PASSWORD
# ═══════════════════════════════════════════════════════════════════════════

def update_password(username: str, new_hash: str, must_reset: bool = False,
                    clear_session: bool = False) -> bool:
    """Update a user's password hash. Optionally set must_reset and kill session."""
    user = _load_user(username)
    if not user:
        return False
    user["password_hash"] = new_hash
    user["must_reset_password"] = must_reset
    if clear_session:
        old_key = user.get("session_key")
        if old_key:
            _session_cache.pop(old_key, None)
        user["session_key"] = None
        user["last_activity"] = None
    _save_user(user)
    logger.info("Password updated for '%s' (must_reset=%s)", username, must_reset)
    return True


# ═══════════════════════════════════════════════════════════════════════════
#  SESSION
# ═══════════════════════════════════════════════════════════════════════════

def get_user_by_session(session_key: str, max_age: float) -> Optional[dict]:
    """Return user dict for a valid (non-expired) session, or None.
    Uses in-memory cache for O(1) lookups; falls back to scan on cache miss."""
    if not session_key:
        return None

    # Fast path: cache hit — load only that one user file
    cached_username = _session_cache.get(session_key)
    if cached_username:
        user = _load_user(cached_username)
        if user and user.get("session_key") == session_key:
            last = user.get("last_activity")
            if last and (time() - last) < max_age:
                return user
            # Expired — clear it
            user["session_key"] = None
            user["last_activity"] = None
            _save_user(user)
        _session_cache.pop(session_key, None)
        return None

    # Slow path: cache miss (first call after server restart) — scan all
    for user in _iter_all_users():
        if user.get("session_key") == session_key:
            last = user.get("last_activity")
            if last and (time() - last) < max_age:
                _session_cache[session_key] = user["username"]  # populate cache
                return user
            # Expired — clear it
            user["session_key"] = None
            user["last_activity"] = None
            _save_user(user)
            return None
    return None


def session_exists(session_key: str, max_age: float) -> bool:
    """Return True if the session exists and is not expired."""
    return get_user_by_session(session_key, max_age) is not None


def save_session(username: str, session_key: str) -> None:
    """Write a session key + current timestamp for the user."""
    user = _load_user(username)
    if user:
        # Evict old session from cache
        old_key = user.get("session_key")
        if old_key and old_key != session_key:
            _session_cache.pop(old_key, None)
        user["session_key"] = session_key
        user["last_activity"] = time()
        _save_user(user)
        _session_cache[session_key] = username


def touch_session(session_key: str) -> None:
    """Update last_activity timestamp for the session."""
    # Fast path via cache
    cached_username = _session_cache.get(session_key)
    if cached_username:
        user = _load_user(cached_username)
        if user and user.get("session_key") == session_key:
            user["last_activity"] = time()
            _save_user(user)
            return
        _session_cache.pop(session_key, None)
        return
    # Slow fallback
    for user in _iter_all_users():
        if user.get("session_key") == session_key:
            user["last_activity"] = time()
            _save_user(user)
            _session_cache[session_key] = user["username"]
            return


def clear_session(session_key: str) -> None:
    """Remove a session on logout."""
    if not session_key:
        return
    # Fast path via cache
    cached_username = _session_cache.get(session_key)
    if cached_username:
        user = _load_user(cached_username)
        if user and user.get("session_key") == session_key:
            user["session_key"] = None
            user["last_activity"] = None
            _save_user(user)
            logger.info("Session cleared for user '%s'", cached_username)
        _session_cache.pop(session_key, None)
        return
    # Slow fallback
    for user in _iter_all_users():
        if user.get("session_key") == session_key:
            user["session_key"] = None
            user["last_activity"] = None
            _save_user(user)
            logger.info("Session cleared for user '%s'", user["username"])
            return


# ═══════════════════════════════════════════════════════════════════════════
#  COINS
# ═══════════════════════════════════════════════════════════════════════════

def get_coins(username: str) -> int:
    """Return the coin balance for the given user."""
    user = _load_user(username)
    return user.get("coins", 0) if user else 0


def update_coins(username: str, delta: int, loaded_by: str = "admin") -> int:
    """Add *delta* coins to a user. Returns new balance.
    Delta can be negative; balance cannot go below 0."""
    user = _load_user(username)
    if not user:
        raise ValueError(f"User '{username}' not found")
    current = user.get("coins", 0)
    new_balance = max(0, current + delta)
    user["coins"] = new_balance
    _save_user(user)
    logger.info(
        "Coins %+d for user '%s' by '%s' — balance: %d",
        delta, username, loaded_by, new_balance,
    )
    return new_balance


def batch_get_coins(usernames: list[str]) -> dict[str, int]:
    """Get coin balances for multiple users (one file read per user)."""
    result = {}
    for uname in usernames:
        user = _load_user(uname)
        if user:
            result[uname] = user.get("coins", 0)
    return result


def batch_update_coins(updates: dict[str, int]) -> None:
    """Persist coin balances for multiple users.
    `updates` maps username → absolute coin balance.
    Each user file is written independently — no cross-user locking needed."""
    for uname, balance in updates.items():
        user = _load_user(uname)
        if user:
            user["coins"] = max(0, balance)
            _save_user(user)
