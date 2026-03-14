"""
routes/user_routes.py — Regular-user pages & API
"""

import logging
from fastapi import APIRouter, Cookie, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from auth import get_user_from_session, touch_activity, get_coins
from dependencies import TEMPLATES_DIR, render_page, require_user

logger = logging.getLogger(__name__)
router = APIRouter()


# ═══════════════════════════════════════════════════════════════════════════
#  PAGES
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/home", response_class=HTMLResponse)
async def home_page(session_key: str | None = Cookie(default=None)):
    """Home page for regular users after login."""
    user = get_user_from_session(session_key)
    if not user:
        return RedirectResponse("/", status_code=302)
    if user.get("must_reset_password"):
        return RedirectResponse("/reset-password", status_code=302)
    touch_activity(session_key)
    return HTMLResponse(render_page("home.html"))


@router.get("/games", response_class=HTMLResponse)
async def games_page(session_key: str | None = Cookie(default=None)):
    """Games lobby page."""
    user = get_user_from_session(session_key)
    if not user:
        return RedirectResponse("/", status_code=302)
    if user.get("must_reset_password"):
        return RedirectResponse("/reset-password", status_code=302)
    touch_activity(session_key)
    return HTMLResponse(render_page("games.html"))


# ═══════════════════════════════════════════════════════════════════════════
#  USER API
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/api/user/me")
async def get_my_profile(user: dict = Depends(require_user)):
    """Return the current user's profile (coins, display name, role)."""
    return {
        "ok": True,
        "username": user["username"],
        "display_name": user.get("display_name", ""),
        "role": user.get("role", "user"),
        "coins": user.get("coins", 0),
    }

