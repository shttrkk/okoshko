import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DB_URL = os.getenv("DB_URL", "postgresql://okno:okno@localhost:5432/okno")
LLM_MODE = os.getenv("LLM_MODE", "offline")
STATIC = Path(os.getenv("STATIC_DIR", str(ROOT / "frontend" / "dist")))

