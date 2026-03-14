"""
routes/admin_routes.py — Admin user management
"""

import logging
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, JSONResponse

from auth import (
    get_all_users, create_user,
    delete_user, load_coins, admin_reset_password,
)
from dependencies import TEMPLATES_DIR, require_admin

logger = logging.getLogger(__name__)
router = APIRouter()


# ═══════════════════════════════════════════════════════════════════════════
#  ADMIN PAGE
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/admin", response_class=HTMLResponse)
async def admin_page(admin: dict = Depends(require_admin)):
    """Serve the admin dashboard."""
    return HTMLResponse((TEMPLATES_DIR / "admin.html").read_text(encoding="utf-8"))


# ═══════════════════════════════════════════════════════════════════════════
#  USER MANAGEMENT API
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/api/admin/users")
async def list_users(admin: dict = Depends(require_admin)):
    """Return all users (password hashes stripped)."""
    return {"ok": True, "users": get_all_users()}


@router.post("/api/admin/users")
async def add_user(payload: dict, admin: dict = Depends(require_admin)):
    """Create a new user.
    Body: { "username": "...", "display_name": "...", "temp_password": "...", "role": "user|admin" }"""
    username = payload.get("username", "").strip()
    display_name = payload.get("display_name", "").strip()
    temp_password = payload.get("temp_password", "")
    role = payload.get("role", "user")

    if not username or not temp_password:
        return JSONResponse(
            {"ok": False, "error": "Username and temporary password are required"},
            status_code=400,
        )
    if len(temp_password) < 4:
        return JSONResponse(
            {"ok": False, "error": "Temporary password must be at least 4 characters"},
            status_code=400,
        )
    if role not in ("user", "admin"):
        return JSONResponse(
            {"ok": False, "error": "Role must be 'user' or 'admin'"},
            status_code=400,
        )

    try:
        user = create_user(
            username=username,
            display_name=display_name or username,
            temp_password=temp_password,
            role=role,
            created_by=admin["username"],
        )
        return {"ok": True, "user": user}
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)


@router.post("/api/admin/users/{username}/reset-password")
async def admin_reset_pw(username: str, payload: dict, admin: dict = Depends(require_admin)):
    """Admin resets a user's password.
    Body: { "new_password": "..." }"""
    new_password = payload.get("new_password", "")
    if not new_password or len(new_password) < 4:
        return JSONResponse(
            {"ok": False, "error": "Password must be at least 4 characters"},
            status_code=400,
        )
    ok = admin_reset_password(username, new_password, reset_by=admin["username"])
    if not ok:
        return JSONResponse(
            {"ok": False, "error": f"User '{username}' not found"},
            status_code=404,
        )
    return {"ok": True, "message": f"Password reset for '{username}' — they must change it on next login"}


@router.delete("/api/admin/users/{username}")
async def remove_user(username: str, admin: dict = Depends(require_admin)):
    """Delete a user by username."""
    if username == admin["username"]:
        return JSONResponse(
            {"ok": False, "error": "Cannot delete yourself"},
            status_code=400,
        )
    try:
        ok = delete_user(username)
        if not ok:
            return JSONResponse(
                {"ok": False, "error": f"User '{username}' not found"},
                status_code=404,
            )
        return {"ok": True}
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


# ═══════════════════════════════════════════════════════════════════════════
#  COINS API
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/api/admin/users/{username}/coins")
async def add_coins(username: str, payload: dict, admin: dict = Depends(require_admin)):
    """Load coins for a user.
    Body: { "amount": 100 }  — positive to add, negative to deduct."""
    amount = payload.get("amount")
    if amount is None or not isinstance(amount, (int, float)):
        return JSONResponse(
            {"ok": False, "error": "amount is required and must be a number"},
            status_code=400,
        )
    amount = int(amount)
    if amount == 0:
        return JSONResponse(
            {"ok": False, "error": "amount must not be zero"},
            status_code=400,
        )
    try:
        new_balance = load_coins(username, amount, loaded_by=admin["username"])
        return {"ok": True, "username": username, "coins": new_balance}
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
