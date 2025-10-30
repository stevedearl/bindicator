Bindicator backend (FastAPI)
============================

Local dev
---------

1) Create a virtual environment (recommended)

   python -m venv .venv
   .venv\\Scripts\\activate  # PowerShell

2) Install dependencies

   pip install -r backend/requirements.txt

3) Run the server

   python backend/main.py

Your API is available at http://127.0.0.1:8000

Example:

   http://127.0.0.1:8000/api/bins?postcode=SL6%206AH

Notes
-----
- CORS is enabled for development so the frontend can call the API.
- Query param `refresh=true` forces a cache bypass and refresh.
- Response model (contract):
  {
    "postcode": string,
    "nextCollectionDate": ISO8601 date,
    "nextCollectionDay": string,
    "bins": ["blue"|"green"|"black"],
    "source": string,
    "cached": boolean,
    "fetchedAt": ISO8601 datetime
  }
- Scraper contract (internal): postcode, next_collection_date (date), bins (enum list). See `ScraperResult` in `backend/main.py`.
- Datasource toggle:
  - Set `BINDICATOR_DATASOURCE=rbwm` to use the live RBWM scraper (Playwright).
  - Default is `mock` (deterministic placeholder).
  - In `rbwm` mode, upstream failures return HTTP 502 with a helpful error; there is no silent mock fallback.
  - To test without RBWM, set `BINDICATOR_DATASOURCE=mock`.

RBWM flow (addresses + UPRN)
----------------------------

When using the RBWM datasource, the recommended flow is:

1) Look up addresses for a postcode

   GET /api/addresses?postcode=SL6%206AH

   Response: [{ "uprn": "100080366175", "address": "22 The Crescent, Maidenhead, SL6 6AH" }, ...]

2) Fetch bins by UPRN (preferred) or by postcode (fallback)

   GET /api/bins?uprn=100080366175
   or
   GET /api/bins?postcode=SL6%206AH

Notes:
- Bins are derived from the table on the UPRN page. We always return: ["blue", "black"] or ["blue", "green"] based on whether that date lists Refuse (black) or Garden (green).
- Results are cached by key: `uprn:<uprn>` or by pretty postcode (e.g., `SL6 6AH`).

Address resolution (house + postcode)
-------------------------------------

- Resolve a user's house number to a shortlist of UPRNs:

  GET /api/resolve?postcode=SL6%206AH&house=22

- Returns candidates ordered by best match: [{ uprn, address, exact, score }]
- Frontend uses this to auto-pick an exact match or present a brief chooser.

Using the RBWM scraper (Playwright)
-----------------------------------

1) Install Playwright Python package and browsers

   pip install -r backend/requirements.txt
   python -m playwright install

2) Set environment variables (PowerShell example)

   $env:BINDICATOR_DATASOURCE = "rbwm"
   # Optional: override if RBWM changes the URL
   # $env:RBWM_BIN_URL = "https://www.rbwm.gov.uk/home/bins-and-recycling/find-your-bin-day"

3) Run the API and test

   python backend/main.py
   # In another terminal:
   curl "http://127.0.0.1:8000/api/addresses?postcode=SL6%206AH"
   curl "http://127.0.0.1:8000/api/bins?uprn=100080366175"

Notes:
- The scraper uses heuristic selectors and may require tweaking if the site HTML changes.
- In `rbwm` mode, upstream failures return HTTP 502 with a friendly JSON error; there is no silent mock fallback. To test without RBWM, set `BINDICATOR_DATASOURCE=mock`.

Caching
-------

- Bindicator persists lookups to a lightweight JSON cache on disk at `backend/data/cache.json`.
- Keys:
  - Postcode entries are stored by pretty postcode, e.g. `"SL6 6AH"`.
  - UPRN entries are stored as `"uprn:100080366175"`.
- Each entry stores the full API response and a UTC `fetched_at` timestamp. Example:

  {
    "SL6 6AH": {
      "data": { ... BinResponse JSON ... },
      "fetched_at": "2025-10-27T08:15:00Z"
    },
    "uprn:100080366175": {
      "data": { ... BinResponse JSON ... },
      "fetched_at": "2025-10-27T08:16:00Z"
    }
  }

- Same‑day cache validation: requests to `/api/bins?postcode=...` return instantly if the cached
  `fetched_at` date matches today’s UTC date. Otherwise the backend refreshes and updates the cache.
- Force refresh at any time: add `&refresh=true`.
- On startup, the backend prefetches any stale postcodes found in the cache so the first request of the day is fast.
- The cache file is ignored by Git (`.gitignore`).

Testing
-------

- First request creates/updates cache:
  curl "http://127.0.0.1:8000/api/bins?postcode=SL6%206AH"

- Second request (same day) hits cache instantly:
  curl "http://127.0.0.1:8000/api/bins?postcode=SL6%206AH"

- Force refresh and overwrite cache:
  curl "http://127.0.0.1:8000/api/bins?postcode=SL6%206AH&refresh=true"

- After midnight UTC, on next app start the backend prefetches stale cached postcodes automatically.

Hybrid Postcode Logic & Lazy Verification
----------------------------------------

- Bindicator auto-selects the first address for a postcode on the RBWM forms
  site and parses that address’s schedule. Most postcodes share a single
  schedule so this is fast and accurate.
- Results are cached by postcode (persisted on disk) and refreshed once per
  day by default.
- Lazy verification is available on demand (never automatic per request):
  it checks a handful of addresses under the postcode and compares bin
  patterns to detect mixed routes.
- If multiple routes exist, the cache stores a `mixed_routes=true` flag and
  the list of addresses seen during verification; you can then fall back to
  UPRN/address selection on the frontend.

Debug endpoint (requires `BINDICATOR_DEBUG=true`)
------------------------------------------------

Run a verification, throttled to once per 24h per postcode:

  curl "http://127.0.0.1:8000/api/debug/lazy-verify?postcode=SL6%206AH"

Response includes `mixed_routes`, `checked_at`, and known `addresses` if
inconsistent. The verification is polite: it checks at most a few addresses
and includes small random waits between requests.

Cache admin (dev convenience)
-----------------------------

- Check cache:

  GET /api/cache/status

- Clear cache:

  POST /api/cache/clear              # all entries
  POST /api/cache/clear?scope=pc     # postcode entries
  POST /api/cache/clear?scope=uprn   # UPRN entries
  POST /api/cache/clear?key=SL6%206AH  # specific postcode (also accepts `key=pc:SL6%206AH`)
  POST /api/cache/clear?key=uprn:100080366175

Deployment
----------

- The app reads `HOST` and `PORT` env vars on startup (defaults to 127.0.0.1:8000).
- Render.com example (Python web service): command `python backend/main.py`, set PORT env, add `BINDICATOR_DATASOURCE=rbwm`.
- For reliability in production, prefer the HTTP scrapers (no headless browser needed). Playwright is kept as a fallback.
