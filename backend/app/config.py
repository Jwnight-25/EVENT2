from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("DATA_DIR", str(ROOT / "data"))).resolve()
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'research.db'}")
API_PREFIX = "/api/v1"
MAX_UPLOAD = 20 * 1024 * 1024


def prepare_storage():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("uploads", "snapshots", "models"):
        (DATA_DIR / name).mkdir(exist_ok=True)
