"""
routes/auth_routes.py — Login, logout, password reset
"""

import logging
from fastapi import APIRouter, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from auth import (
    authenticate, create_session, verify_session, get_user_from_session,
    clear_session, reset_password, touch_activity,
)
from dependencies import TEMPLATES_DIR

logger = logging.getLogger(__name__)
router = APIRouter()


# ═══════════════════════════════════════════════════════════════════════════
#  PAGES
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/", response_class=HTMLResponse)
async def login_page(session_key: str | None = Cookie(default=None)):
    """Serve login page. If already authenticated, redirect to dashboard."""
    user = get_user_from_session(session_key)
    if user:
        if user.get("must_reset_password"):
            return RedirectResponse("/reset-password", status_code=302)
        if user.get("role") == "admin":
            return RedirectResponse("/admin", status_code=302)
        return RedirectResponse("/home", status_code=302)
    return HTMLResponse((TEMPLATES_DIR / "login.html").read_text(encoding="utf-8"))


@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(session_key: str | None = Cookie(default=None)):
    """Serve the forced password-reset page."""
    user = get_user_from_session(session_key)
    if not user:
        return RedirectResponse("/", status_code=302)
    if not user.get("must_reset_password"):
        return RedirectResponse("/home", status_code=302)
    return HTMLResponse((TEMPLATES_DIR / "reset_password.html").read_text(encoding="utf-8"))


# ═══════════════════════════════════════════════════════════════════════════
#  API
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/api/login")
async def api_login(payload: dict):
    """Validate credentials, create session."""
    username = payload.get("username", "").strip()
    password = payload.get("password", "")

    user = authenticate(username, password)
    if not user:
        logger.warning("Login failed for '%s'", username)
        return JSONResponse(
            {"ok": False, "error": "Invalid username or password"},
            status_code=401,
        )

    key = create_session(username)
    logger.info("Login OK for '%s'", username)
    return {
        "ok": True,
        "session_key": key,
        "username": username,
        "role": user["role"],
        "must_reset_password": user.get("must_reset_password", False),
    }


@router.post("/api/logout")
async def api_logout(session_key: str | None = Cookie(default=None)):
    """Clear session."""
    clear_session(session_key)
    return {"ok": True}


@router.post("/api/reset-password")
async def api_reset_password(payload: dict, session_key: str | None = Cookie(default=None)):
    """Handle forced password reset.
    Body: { "new_password": "...", "confirm_password": "..." }"""
    user = get_user_from_session(session_key)
    if not user:
        return JSONResponse({"ok": False, "error": "Not authenticated"}, status_code=401)

    new_pw = payload.get("new_password", "")
    confirm = payload.get("confirm_password", "")

    if len(new_pw) < 6:
        return JSONResponse(
            {"ok": False, "error": "Password must be at least 6 characters"},
            status_code=400,
        )
    if new_pw != confirm:
        return JSONResponse(
            {"ok": False, "error": "Passwords do not match"},
            status_code=400,
        )

    reset_password(user["username"], new_pw)
    logger.info("Password reset complete for '%s'", user["username"])
    return {"ok": True}
