from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[2]
env_file = ROOT / ".env"
if env_file.exists():
    # Plain KEY=value only; never execute configuration as shell code.
    for line in env_file.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
DATA_DIR = Path(os.getenv("DATA_DIR", str(ROOT / "data"))).resolve()
DATABASE_URL = os.getenv("DATABASE_URL") or f"sqlite:///{DATA_DIR / 'research.db'}"
API_PREFIX = "/api/v1"
MAX_UPLOAD = 20 * 1024 * 1024


def prepare_storage():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("uploads", "snapshots", "models"):
        (DATA_DIR / name).mkdir(exist_ok=True)
