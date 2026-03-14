"""
main.py — Indiex FastAPI application entry point
═════════════════════════════════════════════════
Run:  python main.py
"""

import logging
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from routes.auth_routes import router as auth_router
from routes.admin_routes import router as admin_router
from routes.user_routes import router as user_router
from routes.teen_patti_routes import router as teen_patti_router
import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)


# ── Lifespan ────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown hooks."""
    # ── Ensure data folder exists ───────────────────────────────────────
    (Path(__file__).parent / "data").mkdir(exist_ok=True)

    # ── Ensure house account exists ─────────────────────────────────────
    if db.get_user_by_username("teen_patti") is None:
        from auth import hash_password
        db.create_user(
            username="teen_patti",
            display_name="House",
            password_hash=hash_password("__H0us@__IntE9n0L__"),
            role="system",
            created_by="system",
        )
        logger.info("Created house account 'teen_patti'")
    else:
        logger.info("House account 'teen_patti' already exists — balance: %d",
                     db.get_coins("teen_patti"))

    yield  # ← app runs here

    logger.info("Indiex shutting down")


app = FastAPI(title="Indiex", version="1.0.0", lifespan=lifespan)

# ── Static files ────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

# ── Register routers ────────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(user_router)
app.include_router(teen_patti_router)


if __name__ == "__main__":
    logger.info("🚀 Starting Indiex on http://localhost:8100")
    uvicorn.run("main:app", host="0.0.0.0", port=8100)
