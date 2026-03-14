"""
db.py — Data access layer for Indiex
═════════════════════════════════════
All reads/writes to the user store go through this module.
Today the backend is a JSON file (data/users.json).
To migrate to SQLite or PostgreSQL, change only the function bodies below.
"""

import json
import threading
import uuid
import logging
from pathlib import Path
from time import time
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
USERS_FILE = BASE_DIR / "data" / "users.json"

# Thread-safe lock for file operations
_file_lock = threading.Lock()


# ═══════════════════════════════════════════════════════════════════════════
#  LOW-LEVEL JSON I/O  (private — only used inside this module)
# ═══════════════════════════════════════════════════════════════════════════

def _load_users() -> list[dict]:
    """Read all users from disk."""
    if USERS_FILE.exists():
        users = json.loads(USERS_FILE.read_text(encoding="utf-8")).get("users", [])
        for u in users:
            if "coins" not in u:
                u["coins"] = 0
        return users
    return []


def _save_users(users: list[dict]):
    """Write all users to disk."""
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    USERS_FILE.write_text(
        json.dumps({"users": users}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ═══════════════════════════════════════════════════════════════════════════
#  USER CRUD
# ═══════════════════════════════════════════════════════════════════════════

def get_user_by_username(username: str) -> Optional[dict]:
    """Look up one user by username. Returns full dict including hash."""
    for u in _load_users():
        if u["username"] == username:
            return u
    return None


def get_all_users() -> list[dict]:
    """Return all users with password_hash stripped."""
    return [
        {k: v for k, v in u.items() if k != "password_hash"}
        for u in _load_users()
    ]


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

    users = _load_users()
    for u in users:
        if u["username"] == username:
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
    users.append(new_user)
    _save_users(users)
    logger.info("User '%s' created by '%s'", username, created_by)
    return {k: v for k, v in new_user.items() if k != "password_hash"}


def delete_user(username: str) -> bool:
    """Delete a user. Cannot delete the last admin. Raises ValueError if so."""
    users = _load_users()
    target = None
    for u in users:
        if u["username"] == username:
            target = u
            break
    if not target:
        return False

    if target["role"] == "admin":
        admin_count = sum(1 for u in users if u["role"] == "admin")
        if admin_count <= 1:
            raise ValueError("Cannot delete the last admin user")

    users = [u for u in users if u["username"] != username]
    _save_users(users)
    logger.info("User '%s' deleted", username)
    return True


# ═══════════════════════════════════════════════════════════════════════════
#  PASSWORD
# ═══════════════════════════════════════════════════════════════════════════

def update_password(username: str, new_hash: str, must_reset: bool = False,
                    clear_session: bool = False) -> bool:
    """Update a user's password hash. Optionally set must_reset and kill session."""
    users = _load_users()
    for u in users:
        if u["username"] == username:
            u["password_hash"] = new_hash
            u["must_reset_password"] = must_reset
            if clear_session:
                u["session_key"] = None
                u["last_activity"] = None
            _save_users(users)
            logger.info("Password updated for '%s' (must_reset=%s)", username, must_reset)
            return True
    return False


# ═══════════════════════════════════════════════════════════════════════════
#  SESSION
# ═══════════════════════════════════════════════════════════════════════════

def get_user_by_session(session_key: str, max_age: float) -> Optional[dict]:
    """Return user dict for a valid (non-expired) session, or None.
    Automatically clears expired sessions."""
    if not session_key:
        return None
    users = _load_users()
    for u in users:
        if u.get("session_key") == session_key:
            last = u.get("last_activity")
            if last and (time() - last) < max_age:
                return u
            # Expired — clear it
            u["session_key"] = None
            u["last_activity"] = None
            _save_users(users)
            return None
    return None


def session_exists(session_key: str, max_age: float) -> bool:
    """Return True if the session exists and is not expired."""
    return get_user_by_session(session_key, max_age) is not None


def save_session(username: str, session_key: str) -> None:
    """Write a session key + current timestamp for the user."""
    users = _load_users()
    for u in users:
        if u["username"] == username:
            u["session_key"] = session_key
            u["last_activity"] = time()
            break
    _save_users(users)


def touch_session(session_key: str) -> None:
    """Update last_activity timestamp for the session."""
    users = _load_users()
    for u in users:
        if u.get("session_key") == session_key:
            u["last_activity"] = time()
            _save_users(users)
            return


def clear_session(session_key: str) -> None:
    """Remove a session on logout."""
    if not session_key:
        return
    users = _load_users()
    for u in users:
        if u.get("session_key") == session_key:
            u["session_key"] = None
            u["last_activity"] = None
            _save_users(users)
            logger.info("Session cleared for user '%s'", u["username"])
            return


# ═══════════════════════════════════════════════════════════════════════════
#  COINS
# ═══════════════════════════════════════════════════════════════════════════

def get_coins(username: str) -> int:
    """Return the coin balance for the given user."""
    user = get_user_by_username(username)
    return user.get("coins", 0) if user else 0


def update_coins(username: str, delta: int, loaded_by: str = "admin") -> int:
    """Add *delta* coins to a user. Returns new balance.
    Delta can be negative; balance cannot go below 0."""
    users = _load_users()
    for u in users:
        if u["username"] == username:
            current = u.get("coins", 0)
            new_balance = max(0, current + delta)
            u["coins"] = new_balance
            _save_users(users)
            logger.info(
                "Coins %+d for user '%s' by '%s' — balance: %d",
                delta, username, loaded_by, new_balance,
            )
            return new_balance
    raise ValueError(f"User '{username}' not found")


def batch_get_coins(usernames: list[str]) -> dict[str, int]:
    """Get coin balances for multiple users in a single file read."""
    with _file_lock:
        users = _load_users()
    name_set = set(usernames)
    return {
        u["username"]: u.get("coins", 0)
        for u in users if u["username"] in name_set
    }


def batch_update_coins(updates: dict[str, int]) -> None:
    """Persist coin balances for multiple users in one read + one write.
    `updates` maps username → absolute coin balance."""
    with _file_lock:
        users = _load_users()
        for u in users:
            if u["username"] in updates:
                u["coins"] = max(0, updates[u["username"]])
        _save_users(users)
