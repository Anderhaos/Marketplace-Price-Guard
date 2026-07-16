from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
PRODUCTS_PATH = DATA_DIR / "products.json"
LOG_PATH = DATA_DIR / "actions.log"
ENV_PATH = PROJECT_ROOT / ".env"


def load_env():
    values = {}

    if not ENV_PATH.exists():
        return values

    with ENV_PATH.open("r", encoding="utf-8-sig") as file:
        for line in file:
            line = line.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()

    return values


ENV = load_env()
APP_MODE = ENV.get("APP_MODE", "READ_ONLY").upper()
ENCRYPTION_KEY = ENV.get("ENCRYPTION_KEY", "dev-encryption-change-me")
DATABASE_PATH = PROJECT_ROOT / ENV.get("DATABASE_PATH", "data/app.db")
LIVE_MAX_FIXES = int(ENV.get("LIVE_MAX_FIXES", "5") or 5)
AUTO_CHECK_ENABLED = ENV.get("AUTO_CHECK_ENABLED", "1") == "1"
CHECK_INTERVAL_SECONDS = int(ENV.get("CHECK_INTERVAL_SECONDS", "600") or 600)
SMTP_HOST = ENV.get("SMTP_HOST", "")
SMTP_PORT = int(ENV.get("SMTP_PORT", "587") or 587)
SMTP_USER = ENV.get("SMTP_USER", "")
SMTP_PASSWORD = ENV.get("SMTP_PASSWORD", "")
SMTP_FROM = ENV.get("SMTP_FROM", SMTP_USER)
