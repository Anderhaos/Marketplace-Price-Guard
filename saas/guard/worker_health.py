"""Per-container worker liveness probes; no DB writes or external requests."""
from pathlib import Path
from time import time

HEARTBEAT_DIR = Path("/tmp/nosale-worker-heartbeats")
ALLOWED = frozenset({"guard", "notifications", "reconciliation"})


def heartbeat_path(name):
    if name not in ALLOWED:
        raise ValueError("Unknown worker heartbeat")
    return HEARTBEAT_DIR / (name + ".heartbeat")


def mark_success(name):
    """A successful work cycle, even when there was no work to perform."""
    path = heartbeat_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(".tmp")
    staging.write_text(str(time()), encoding="ascii")
    staging.replace(path)


def is_healthy(name, max_age_seconds):
    """No stale/absent heartbeat is ever treated as healthy."""
    try:
        age = time() - float(heartbeat_path(name).read_text(encoding="ascii"))
        return 0 <= age <= max_age_seconds
    except (OSError, ValueError):
        return False
