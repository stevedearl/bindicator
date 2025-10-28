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
- The /api/bins route returns dummy data for now.
- Query param `refresh=true` forces a cache bypass and refresh.
- In-memory cache TTL can be set via env var `BINDICATOR_CACHE_TTL_SECONDS` (default 21600 = 6 hours).
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
- Results are cached by key: `uprn:<uprn>` or `pc:<postcode>`.

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
- On scraper failure, the backend falls back to the mock source and logs a message.

Caching
-------

- Bindicator persists postcode lookups to a lightweight JSON cache on disk at `backend/data/cache.json`.
- Each entry stores the full API response and a UTC `fetched_at` timestamp:

  {
    "SL6 6AH": {
      "data": { ... BinResponse JSON ... },
      "fetched_at": "2025-10-27T08:15:00Z"
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

Cache admin (dev convenience)
-----------------------------

- Check cache:

  GET /api/cache/status

- Clear cache:

  POST /api/cache/clear            # all entries
  POST /api/cache/clear?scope=pc   # only postcode entries
  POST /api/cache/clear?scope=uprn # only UPRN entries
  POST /api/cache/clear?key=pc:SL66AH

Deployment
----------

- The app reads `HOST` and `PORT` env vars on startup (defaults to 127.0.0.1:8000).
- Render.com example (Python web service): command `python backend/main.py`, set PORT env, add `BINDICATOR_DATASOURCE=rbwm`.
- For reliability in production, prefer the HTTP scrapers (no headless browser needed). Playwright is kept as a fallback.
