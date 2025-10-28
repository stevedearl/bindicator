from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from datetime import date, timedelta, datetime, timezone
from pydantic import BaseModel, Field, ConfigDict
from enum import Enum
from typing import List, Dict, Tuple
import os
import threading
import asyncio
import logging
from fastapi.responses import JSONResponse
from . import cache as disk_cache


# Logging setup with timestamps
_LOG_LEVEL = getattr(logging, os.getenv("BINDICATOR_LOG_LEVEL", "INFO").upper(), logging.INFO)
logging.basicConfig(
    level=_LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bindicator")

app = FastAPI(title="Bindicator API", version="0.1.0")

# CORS for local dev (frontend on Vite dev server)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "datasource": os.getenv("BINDICATOR_DATASOURCE", "mock").lower(),
    }


# Friendly error responses
@app.exception_handler(HTTPException)
def http_exception_handler(_, exc: HTTPException):
    datasource = os.getenv("BINDICATOR_DATASOURCE", "mock").lower()
    hint = None
    if exc.status_code == 502:
        hint = (
            "RBWM may be busy or unavailable. Please try again in a minute, "
            "or switch datasource to 'mock' for testing."
        )
    payload = {
        "error": exc.detail or "HTTP error",
        "code": exc.status_code,
        "datasource": datasource,
        "hint": hint,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return JSONResponse(status_code=exc.status_code, content=payload)


@app.exception_handler(Exception)
def unhandled_exception_handler(_, exc: Exception):
    log.exception("Unhandled server error")
    payload = {
        "error": "Internal server error",
        "code": 500,
        "datasource": os.getenv("BINDICATOR_DATASOURCE", "mock").lower(),
        "hint": "Please retry shortly. If this persists, check server logs.",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return JSONResponse(status_code=500, content=payload)


class BinType(str, Enum):
    blue = "blue"      # Recycling
    green = "green"    # Garden
    black = "black"    # Rubbish


class ScraperResult(BaseModel):
    """Contract for the scraper output before transformation to API shape."""
    postcode: str
    next_collection_date: date
    bins: List[BinType]
    # Optional raw fields to aid debugging can be added later (e.g., raw_html)


class BinResponse(BaseModel):
    """Public API response model (camelCase via aliases)."""
    model_config = ConfigDict(populate_by_name=True)

    postcode: str
    next_collection_date: date = Field(alias="nextCollectionDate")
    next_collection_day: str = Field(alias="nextCollectionDay")
    bins: List[BinType]
    source: str
    cached: bool = False
    fetched_at: datetime = Field(alias="fetchedAt")


# --- In-memory cache scaffold ---
_cache_lock = threading.Lock()
_cache: Dict[str, Tuple[datetime, BinResponse]] = {}
_DEFAULT_TTL_SECONDS = int(os.getenv("BINDICATOR_CACHE_TTL_SECONDS", "21600"))  # 6h


def _normalize_postcode(pc: str) -> str:
    return pc.strip().replace(" ", "").upper()


def _cache_get(key: str) -> BinResponse | None:
    now = datetime.now(timezone.utc)
    with _cache_lock:
        item = _cache.get(key)
        if not item:
            return None
        expires_at, value = item
        if now >= expires_at:
            # Expired
            _cache.pop(key, None)
            return None
        return value


def _cache_set(key: str, value: BinResponse, ttl_seconds: int | None = None) -> None:
    ttl = ttl_seconds if ttl_seconds is not None else _DEFAULT_TTL_SECONDS
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)
    with _cache_lock:
        _cache[key] = (expires_at, value)


def _weekday_name(d: date) -> str:
    return d.strftime("%A")


def scrape_rbwm_schedule(postcode: str) -> ScraperResult:
    """RBWM provider wrapper: uses Playwright when datasource is set to 'rbwm'.
    Falls back to mock if not configured or on error.
    """
    datasource = os.getenv("BINDICATOR_DATASOURCE", "mock").lower()
    key = _normalize_postcode(postcode)
    if datasource == "rbwm":
        try:
            from scraper.rbwm import fetch_rbwm_schedule as _async_fetch
            # Run async Playwright scraper in this sync context
            return asyncio.run(_async_fetch(key))
        except Exception:
            # In RBWM mode, do not fall back to mock — surface an upstream failure
            log.exception("RBWM postcode scrape failed")
            raise

    # Mock fallback
    # If key is empty or doesn't end with a digit, choose a deterministic default
    if key and key[-1].isdigit():
        even = int(key[-1]) % 2 == 0
    else:
        # Use current day ordinal parity for deterministic variation
        even = (date.today().toordinal() % 2 == 0)
    today = date.today()
    next_collection = today + timedelta(days=(2 if even else 3))
    # Mock follows RBWM rule: always blue + (black or green)
    bins = [BinType.blue, (BinType.black if even else BinType.green)]
    return ScraperResult(postcode=key, next_collection_date=next_collection, bins=bins)


def build_response_from_scrape(scrape: ScraperResult, *, source: str, cached: bool) -> BinResponse:
    return BinResponse(
        postcode=scrape.postcode,
        next_collection_date=scrape.next_collection_date,
        next_collection_day=_weekday_name(scrape.next_collection_date),
        bins=scrape.bins,
        source=source,
        cached=cached,
        fetched_at=datetime.now(timezone.utc),
    )


@app.get("/api/bins", response_model=BinResponse, response_model_by_alias=True)
def get_bins(
    postcode: str | None = Query(None, min_length=5, max_length=10),
    uprn: str | None = Query(None, description="RBWM Unique Property Reference Number"),
    refresh: bool = Query(False, description="Force refresh ignoring cache"),
):
    """
    Returns next collection info.
    - If `uprn` is provided and datasource=rbwm, fetch by UPRN (preferred).
    - Else if `postcode` is provided, use rbwm/mock postcode flow.
    Uses in-memory cache; set `refresh=true` to bypass.
    """
    if uprn:
        cache_key = f"uprn:{uprn}"
    elif postcode:
        cache_key = f"pc:{_normalize_postcode(postcode)}"
    else:
        raise HTTPException(status_code=400, detail="Provide either 'uprn' or 'postcode'")

    if not refresh:
        cached_value = _cache_get(cache_key)
        if cached_value is not None:
            return BinResponse(
                postcode=cached_value.postcode,
                next_collection_date=cached_value.next_collection_date,
                next_collection_day=cached_value.next_collection_day,
                bins=cached_value.bins,
                source=cached_value.source,
                cached=True,
                fetched_at=cached_value.fetched_at,
            )

    datasource = os.getenv("BINDICATOR_DATASOURCE", "mock").lower()
    if uprn and datasource == "rbwm":
        try:
            # Try fast HTTP path first
            from scraper.rbwm import fetch_rbwm_schedule_by_uprn_http as _fetch_http
            scrape = _fetch_http(uprn)
            source = "rbwm"
        except Exception:
            log.exception("RBWM UPRN HTTP fetch failed; trying Playwright")
            try:
                from scraper.rbwm import fetch_rbwm_schedule_by_uprn as _fetch_by_uprn
                scrape = asyncio.run(_fetch_by_uprn(uprn))
                source = "rbwm"
            except Exception:
                log.exception("RBWM UPRN Playwright failed; returning error (no mock fallback in rbwm mode)")
                raise HTTPException(status_code=502, detail="RBWM upstream fetch failed for UPRN")
    else:
        pc_norm = _normalize_postcode(postcode or "")
        # Persistent on-disk cache only applies to postcode lookups
        # Check disk cache (same-day validation) unless refresh=true
        if postcode and not refresh:
            try:
                if disk_cache.is_same_day_cached(postcode):
                    item = disk_cache.get_cached(postcode)
                    if item and isinstance(item.get("data"), dict):
                        data = dict(item["data"])  # shallow copy
                        data["cached"] = True
                        log.info("[cache] Hit for %s (same-day data).", item.get("key", postcode))
                        return data
            except Exception:
                log.exception("Disk cache read failed")

        if datasource == "rbwm":
            try:
                log.info("[cache] Refreshing %s (new day or refresh=true).", postcode)
                scrape = scrape_rbwm_schedule(pc_norm)
                source = "rbwm"
            except Exception:
                log.exception("RBWM postcode fetch failed; returning error (no mock fallback in rbwm mode)")
                raise HTTPException(status_code=502, detail="RBWM upstream fetch failed for postcode")
        else:
            log.info("[cache] Refreshing %s (mock mode).", postcode)
            scrape = scrape_rbwm_schedule(pc_norm)
            source = "mock"

    resp = build_response_from_scrape(scrape, source=source, cached=False)
    _cache_set(cache_key, resp)
    # Persist only postcode responses to disk cache
    try:
        if postcode:
            # store response as plain dict with alias keys
            disk_cache.update_cache(postcode, {
                "postcode": resp.postcode,
                "nextCollectionDate": resp.next_collection_date.isoformat(),
                "nextCollectionDay": resp.next_collection_day,
                "bins": [b.value for b in resp.bins],
                "source": resp.source,
                "cached": False,
                "fetchedAt": resp.fetched_at.isoformat(),
            })
    except Exception:
        log.exception("Disk cache write failed")

    return resp


class AddressItem(BaseModel):
    uprn: str
    address: str


@app.get("/api/addresses", response_model=List[AddressItem])
def get_addresses(postcode: str = Query(..., min_length=5, max_length=10)):
    """RBWM address lookup: returns a list of UPRNs for a postcode.
    Requires BINDICATOR_DATASOURCE=rbwm.
    """
    datasource = os.getenv("BINDICATOR_DATASOURCE", "mock").lower()
    if datasource != "rbwm":
        return []

    try:
        # Try fast HTTP path first
        from scraper.rbwm import fetch_rbwm_addresses_http as _fetch_http
        results = _fetch_http(postcode)
        if not results:
            raise RuntimeError("No addresses found via HTTP")
        addrs = [AddressItem(uprn=r.uprn, address=r.address) for r in results]
        log.info("RBWM HTTP addresses: %s candidates for %s", len(addrs), postcode)
        return addrs
    except Exception:
        log.exception("RBWM address HTTP lookup failed; trying Playwright")
        try:
            from scraper.rbwm import fetch_rbwm_addresses as _fetch_addrs
            results = asyncio.run(_fetch_addrs(postcode))
            addrs = [AddressItem(uprn=r.uprn, address=r.address) for r in results]
            log.info("RBWM Playwright addresses: %s candidates for %s", len(addrs), postcode)
            return addrs
        except Exception:
            log.exception("RBWM address lookup failed")
            return []


# --- Cache admin endpoints (dev convenience) ---
class CacheStatus(BaseModel):
    entries: int
    keys: List[str]
    ttl_seconds: int = Field(alias="ttlSeconds")
    now: datetime


@app.get("/api/cache/status", response_model=CacheStatus)
def cache_status(limit: int = Query(10, ge=0, le=100)):
    now = datetime.now(timezone.utc)
    with _cache_lock:
        keys = list(_cache.keys())[:limit]
        return CacheStatus(entries=len(_cache), keys=keys, ttl_seconds=_DEFAULT_TTL_SECONDS, now=now)


@app.post("/api/cache/clear")
def cache_clear(scope: str | None = Query(None, description="all|uprn|pc"), key: str | None = Query(None)):
    removed = 0
    with _cache_lock:
        if key:
            removed = 1 if _cache.pop(key, None) is not None else 0
        elif scope in {"uprn", "pc"}:
            to_del = [k for k in _cache if k.startswith(scope + ":")]
            for k in to_del:
                _cache.pop(k, None)
            removed = len(to_del)
        else:
            removed = len(_cache)
            _cache.clear()
    return {"removed": removed}


class ResolvedAddress(BaseModel):
    uprn: str
    address: str
    exact: bool = False
    score: int = 0


def _score_address_match(address: str, house: str) -> tuple[int, bool]:
    """Return a score and exact flag for how well the address matches the house input.
    Higher score is better. Exact means starts with the same house token (e.g., '22' or '22A').
    """
    a = address.strip().lower()
    h = house.strip().lower()
    if not a or not h:
        return (0, False)
    # Exact if the first token starts with the house string (to allow 22A)
    first = a.split(',')[0].split()[0]
    exact = first.startswith(h)
    score = 0
    if exact:
        score += 100
        if first == h:
            score += 20
    # Bonus if the house number appears at the start of line
    if a.startswith(h + ' '):
        score += 10
    # Minor bonus if contains elsewhere
    if (' ' + h + ' ') in a:
        score += 2
    return (score, exact)


@app.get("/api/resolve", response_model=List[ResolvedAddress])
def resolve_address(
    postcode: str = Query(..., min_length=5, max_length=10),
    house: str = Query(..., description="House number/name to match"),
):
    """Resolve a postcode and house query to one or more RBWM UPRNs.
    Returns sorted candidates with a score and exact flag. Requires datasource=rbwm.
    """
    datasource = os.getenv("BINDICATOR_DATASOURCE", "mock").lower()
    if datasource != "rbwm":
        return []

    # Use HTTP path for speed; fall back to Playwright if needed
    def _fetch_all() -> List[AddressItem]:
        from scraper.rbwm import fetch_rbwm_addresses_http as _fetch_http
        try:
            res = _fetch_http(postcode)
            if res:
                return [AddressItem(uprn=r.uprn, address=r.address) for r in res]
            raise RuntimeError("no addresses via http")
        except Exception:
            from scraper.rbwm import fetch_rbwm_addresses as _fetch_pw
            try:
                res2 = asyncio.run(_fetch_pw(postcode))
                return [AddressItem(uprn=r.uprn, address=r.address) for r in res2]
            except Exception:
                log.exception("Resolve: address lookup failed")
                return []

    items = _fetch_all()
    scored: List[ResolvedAddress] = []
    for it in items:
        s, exact = _score_address_match(it.address, house)
        if s > 0:
            scored.append(ResolvedAddress(uprn=it.uprn, address=it.address, exact=exact, score=s))

    # If nothing matched by score, return the raw list (limited) to allow manual choice
    if not scored:
        return [ResolvedAddress(uprn=it.uprn, address=it.address, exact=False, score=0) for it in items[:10]]

    # Sort by exact desc, score desc, then address asc
    scored.sort(key=lambda x: (x.exact, x.score, x.address.lower()), reverse=True)
    # Return top 10
    return scored[:10]


if __name__ == "__main__":
    import uvicorn
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    # Load cache and prefetch stale entries in background
    try:
        disk_cache.load_cache()
    except Exception:
        log.exception("Failed to load disk cache on startup")

    def _prefetch():
        entries = disk_cache.iter_cached_postcodes()
        today = datetime.now(timezone.utc).date()
        for key, item in entries.items():
            try:
                ts = item.get("fetched_at")
                d = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).date() if ts else None
                if d != today:
                    # Refresh
                    log.info("[cache] Prefetch refreshing %s (stale)", key)
                    pc_norm = _normalize_postcode(key)
                    datasource = os.getenv("BINDICATOR_DATASOURCE", "mock").lower()
                    if datasource == "rbwm":
                        try:
                            sc = scrape_rbwm_schedule(pc_norm)
                            res = build_response_from_scrape(sc, source="rbwm", cached=False)
                        except Exception:
                            log.exception("Prefetch RBWM failed for %s", key)
                            continue
                    else:
                        sc = scrape_rbwm_schedule(pc_norm)
                        res = build_response_from_scrape(sc, source="mock", cached=False)
                    disk_cache.update_cache(key, {
                        "postcode": res.postcode,
                        "nextCollectionDate": res.next_collection_date.isoformat(),
                        "nextCollectionDay": res.next_collection_day,
                        "bins": [b.value for b in res.bins],
                        "source": res.source,
                        "cached": False,
                        "fetchedAt": res.fetched_at.isoformat(),
                    })
            except Exception:
                log.exception("Prefetch processing failed for %s", key)

    threading.Thread(target=_prefetch, daemon=True).start()

    uvicorn.run(app, host=host, port=port)
