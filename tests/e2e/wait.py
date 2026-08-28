"""Block until every service's /healthz answers (run inside the compose network)."""
import sys
import time
import urllib.request

URLS = [
    "http://localhost:8000/healthz",
    "http://orchestrator:8010/healthz",
    "http://inference:8020/healthz",
    "http://gmail:8030/healthz",
]

for _ in range(60):
    try:
        for url in URLS:
            urllib.request.urlopen(url, timeout=2)
        print("all services healthy")
        sys.exit(0)
    except Exception:
        time.sleep(2)
print("services did not become healthy in time", file=sys.stderr)
sys.exit(1)
