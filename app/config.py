from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
DATABASE_URL = f"sqlite:///{DATA_DIR / 'quantum.db'}"
STATIC_DIR = ROOT_DIR / "static"
