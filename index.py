"""Vercel entrypoint. Vercel looks for a FastAPI instance named `app` in index.py at the project root;
the application itself lives in backend/app (see README, "Deploying to Vercel")."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from app.main import app  # noqa: E402,F401
