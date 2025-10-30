import os
import sys
from pathlib import Path
os.environ.setdefault("BINDICATOR_DATASOURCE", "mock")

# Ensure repository root on sys.path so 'backend' package imports cleanly
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from backend import main as m


def pp(label, obj):
    import json
    print(label)
    try:
        print(json.dumps(obj, indent=2))
    except Exception:
        print(obj)


def run():
    client = TestClient(m.app)

    print("--- /api/health ---")
    h = client.get("/api/health")
    print(h.status_code)
    pp("health", h.json())

    print("--- First bins request (postcode) ---")
    r1 = client.get("/api/bins", params={"postcode": "SL6 6AH"})
    print(r1.status_code)
    pp("bins1", r1.json())

    print("--- Second bins request (cache expected) ---")
    r2 = client.get("/api/bins", params={"postcode": "SL6 6AH"})
    print(r2.status_code)
    print("cached:", r2.json().get("cached"))

    print("--- Cache status ---")
    cs = client.get("/api/cache/status")
    print(cs.status_code)
    pp("cache_status", cs.json())

    print("--- Force refresh ---")
    r3 = client.get("/api/bins", params={"postcode": "SL6 6AH", "refresh": "true"})
    print(r3.status_code)
    print("cached:", r3.json().get("cached"))

    print("--- Clear postcode cache ---")
    cl = client.post("/api/cache/clear", params={"key": "SL6 6AH"})
    print(cl.status_code, cl.json())

    print("--- Cache status after clear ---")
    cs2 = client.get("/api/cache/status")
    print(cs2.status_code)
    pp("cache_status2", cs2.json())


if __name__ == "__main__":
    run()
