"""
auth.py — Indiex authentication & business logic
══════════════════════════════════════════════════
Password hashing, session management, user CRUD wrappers.
All data access is delegated to db.py.
"""

import uuid
import logging
from typing import Optional

import bcrypt

import db

logger = logging.getLogger(__name__)

SESSION_MAX_AGE = 24 * 60 * 60  # 24 hours


# ═══════════════════════════════════════════════════════════════════════════
#  PASSWORD HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def hash_password(plain: str) -> str:
    """Return a bcrypt hash for the given plaintext password."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Check a plaintext password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════
#  SESSION HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def create_session(username: str) -> str:
    """Generate a new session key for the user and persist it."""
    key = str(uuid.uuid4())
    db.save_session(username, key)
    return key


def verify_session(session_key: str | None) -> bool:
    """Return True if session_key exists and is not expired."""
    if not session_key:
        return False
    return db.session_exists(session_key, SESSION_MAX_AGE)


def get_user_from_session(session_key: str | None) -> Optional[dict]:
    """Return the full user dict for a valid session, or None."""
    if not session_key:
        return None
    return db.get_user_by_session(session_key, SESSION_MAX_AGE)


def touch_activity(session_key: str):
    """Update last_activity timestamp for the session."""
    db.touch_session(session_key)


def clear_session(session_key: str):
    """Remove session on logout."""
    db.clear_session(session_key)


# ═══════════════════════════════════════════════════════════════════════════
#  USER CRUD (delegated to db.py)
# ═══════════════════════════════════════════════════════════════════════════

def get_all_users() -> list[dict]:
    """Return all users with password_hash stripped."""
    return db.get_all_users()


def get_user_by_username(username: str) -> Optional[dict]:
    """Look up one user by username (full dict including hash)."""
    return db.get_user_by_username(username)


def create_user(
    username: str,
    display_name: str,
    temp_password: str,
    role: str = "user",
    created_by: str = "admin",
) -> dict:
    """Create a new user with must_reset_password=True."""
    pw_hash = hash_password(temp_password)
    return db.create_user(username, display_name, pw_hash, role, created_by)


def delete_user(username: str) -> bool:
    """Delete a user by username."""
    return db.delete_user(username)


# ═══════════════════════════════════════════════════════════════════════════
#  PASSWORD RESET
# ═══════════════════════════════════════════════════════════════════════════

def reset_password(username: str, new_password: str):
    """Set a new password and clear must_reset_password flag."""
    new_hash = hash_password(new_password)
    result = db.update_password(username, new_hash, must_reset=False)
    if result:
        logger.info("Password reset for user '%s'", username)
    return result


def admin_reset_password(username: str, new_password: str, reset_by: str = "admin"):
    """Admin resets a user's password — forces re-login."""
    new_hash = hash_password(new_password)
    result = db.update_password(username, new_hash, must_reset=True, clear_session=True)
    if result:
        logger.info("Admin password reset for '%s' by '%s'", username, reset_by)
    return result


# ═══════════════════════════════════════════════════════════════════════════
#  COINS  (thin wrappers — keep existing API for callers)
# ═══════════════════════════════════════════════════════════════════════════

def get_coins(username: str) -> int:
    """Return the coin balance for the given user."""
    return db.get_coins(username)


def load_coins(username: str, amount: int, loaded_by: str = "admin") -> int:
    """Add *amount* coins to a user. Returns the new balance."""
    return db.update_coins(username, amount, loaded_by)


def batch_get_coins(usernames: list[str]) -> dict[str, int]:
    """Get coin balances for multiple users in a single read."""
    return db.batch_get_coins(usernames)


def batch_sync_coins(updates: dict[str, int]):
    """Persist coin balances for multiple users in one write."""
    db.batch_update_coins(updates)


# ═══════════════════════════════════════════════════════════════════════════
#  AUTHENTICATION
# ═══════════════════════════════════════════════════════════════════════════

def authenticate(username: str, password: str) -> Optional[dict]:
    """Verify credentials. Returns user dict on success, None on failure."""
    user = db.get_user_by_username(username)
    if not user:
        return None
    if verify_password(password, user["password_hash"]):
        return user
    return None
