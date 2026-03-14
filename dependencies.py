"""
dependencies.py — Shared FastAPI dependencies, helpers, and paths
═════════════════════════════════════════════════════════════════════
Centralised so every router imports from one place instead of
duplicating code.
"""

from pathlib import Path
from fastapi import Cookie, HTTPException

from auth import get_user_from_session, touch_activity

# ── Shared template directory ────────────────────────────────────────────
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


# ── Auth dependencies ────────────────────────────────────────────────────

async def require_user(session_key: str | None = Cookie(default=None)) -> dict:
    """FastAPI dependency — returns the user dict if logged in
    and password-reset is done."""
    user = get_user_from_session(session_key)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user.get("must_reset_password"):
        raise HTTPException(status_code=403, detail="Password reset required")
    touch_activity(session_key)
    return user


async def require_admin(session_key: str | None = Cookie(default=None)) -> dict:
    """FastAPI dependency — returns the user dict if they are an admin."""
    user = get_user_from_session(session_key)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user.get("must_reset_password"):
        raise HTTPException(status_code=403, detail="Password reset required")
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    touch_activity(session_key)
    return user


# ── Layout renderer ──────────────────────────────────────────────────────

def render_page(page_file: str) -> str:
    """Read layout.html, read the page fragment, inject it into #page-content."""
    layout = (TEMPLATES_DIR / "layout.html").read_text(encoding="utf-8")
    page = (TEMPLATES_DIR / "pages" / page_file).read_text(encoding="utf-8")
    return layout.replace(
        '<div id="page-content"></div>',
        f'<div id="page-content">{page}</div>',
    )
