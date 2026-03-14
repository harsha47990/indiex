"""
main.py — Indiex FastAPI application entry point
═════════════════════════════════════════════════
Run:  python main.py
"""

import logging
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from routes.auth_routes import router as auth_router
from routes.admin_routes import router as admin_router
from routes.user_routes import router as user_router
from routes.teen_patti_routes import router as teen_patti_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Indiex", version="1.0.0")

# ── Static files ────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

# ── Register routers ────────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(user_router)
app.include_router(teen_patti_router)

# ── Ensure data folder exists ───────────────────────────────────────────
(Path(__file__).parent / "data").mkdir(exist_ok=True)


if __name__ == "__main__":
    logger.info("🚀 Starting Indiex on http://localhost:8100")
    uvicorn.run("main:app", host="0.0.0.0", port=8100)
