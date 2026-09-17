"""Container-local heartbeat; never contains mailbox data or credentials."""

import os
import time
from pathlib import Path


def pulse(role):
    if role not in {"assistant", "actions", "sync"}:
        raise ValueError("Unknown worker role")
    path = Path("/tmp") / ("threadly-" + role + "-heartbeat")
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        temporary.write_text(str(time.time()))
        temporary.replace(path)
    except OSError:
        pass  # Observability cannot interrupt a durable dispatch or publication.


def check(role):
    path = Path("/tmp") / ("threadly-" + role + "-heartbeat")
    return (
        role in {"assistant", "actions", "sync"}
        and path.exists()
        and 0 <= time.time() - path.stat().st_mtime < 300
    )


if __name__ == "__main__":
    import sys

    raise SystemExit(0 if len(sys.argv) == 2 and check(sys.argv[1]) else 1)
