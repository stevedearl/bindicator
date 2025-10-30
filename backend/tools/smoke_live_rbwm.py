import os
import sys
from pathlib import Path

# Live RBWM smoke test (HTTP-first). Requires network access.

os.environ.setdefault("BINDICATOR_DATASOURCE", "rbwm")

# Ensure repository root on sys.path so 'backend' package imports cleanly
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from backend import main as m


def run(postcode: str = "SL4 1AA") -> int:
    client = TestClient(m.app)

    print(f"--- Live RBWM: addresses for {postcode} ---")
    r = client.get("/api/addresses", params={"postcode": postcode})
    print(r.status_code)
    if r.status_code != 200:
        print(r.text)
        return 2
    addrs = r.json() or []
    print(f"addresses: {len(addrs)}")
    if not addrs:
        print("No addresses returned")
        return 3
    first = addrs[0]
    uprn = first.get("uprn")
    print("first:", first)
    if not uprn:
        print("First address missing UPRN")
        return 4

    print(f"--- Live RBWM: bins by UPRN {uprn} (refresh) ---")
    b1 = client.get("/api/bins", params={"uprn": uprn, "refresh": "true"})
    print(b1.status_code)
    print(b1.json())
    if b1.status_code != 200:
        return 5

    print(f"--- Live RBWM: bins by UPRN {uprn} (cache hit expected) ---")
    b2 = client.get("/api/bins", params={"uprn": uprn})
    print(b2.status_code)
    js2 = b2.json()
    print("cached:", js2.get("cached"))
    return 0


if __name__ == "__main__":
    pc = sys.argv[1] if len(sys.argv) > 1 else "SL4 1AA"
    sys.exit(run(pc))

