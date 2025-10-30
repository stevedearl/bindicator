import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_CACHE_FILE = os.path.join(_DATA_DIR, "cache.json")

_lock = threading.Lock()
_cache: Dict[str, Dict[str, Any]] = {}


def _ensure_paths() -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    if not os.path.exists(_CACHE_FILE):
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            f.write("{}")


def _pretty_postcode(pc: str) -> str:
    s = (pc or "").strip().upper().replace("  ", " ")
    if " " in s:
        return s
    # naive pretty format: insert a space before last 3 chars when length permits
    return s[:-3] + " " + s[-3:] if len(s) > 3 else s


def _normalize_key(key: str) -> str:
    """Normalize a cache key.
    - For keys starting with 'uprn:' or 'pc:' (case-insensitive), keep prefix and normalize remainder.
    - Otherwise, treat as postcode and pretty-format.
    """
    if not key:
        return key
    k = key.strip()
    lower = k.lower()
    if lower.startswith("uprn:"):
        return f"uprn:{k.split(':', 1)[1].strip()}"
    if lower.startswith("pc:"):
        return f"pc:{_pretty_postcode(k.split(':', 1)[1])}"
    # default: postcode-only key
    return _pretty_postcode(k)


def load_cache() -> None:
    global _cache
    _ensure_paths()
    try:
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            _cache = json.load(f) or {}
            if not isinstance(_cache, dict):
                _cache = {}
    except FileNotFoundError:
        _cache = {}
    except Exception:
        # If the cache file is corrupted, start fresh
        _cache = {}


def save_cache() -> None:
    _ensure_paths()
    tmp = _CACHE_FILE + ".tmp"
    with _lock:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_cache, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _CACHE_FILE)


def get_cached(postcode: str) -> Optional[Dict[str, Any]]:
    key = _pretty_postcode(postcode)
    with _lock:
        item = _cache.get(key)
        if not item or not isinstance(item, dict):
            return None
        return {"key": key, **item}


def get_cached_key(key: str) -> Optional[Dict[str, Any]]:
    norm = _normalize_key(key)
    with _lock:
        item = _cache.get(norm)
        if not item or not isinstance(item, dict):
            return None
        return {"key": norm, **item}


def update_cache(postcode: str, data: Dict[str, Any], **extras: Any) -> None:
    key = _pretty_postcode(postcode)
    # preserve existing mixed_routes metadata unless explicitly provided
    existing = _cache.get(key) or {}
    record = {
        "data": data,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "mixed_routes": extras.get("mixed_routes", existing.get("mixed_routes", None)),
        "mixed_routes_checked": extras.get("mixed_routes_checked", existing.get("mixed_routes_checked", False)),
        "mixed_routes_checked_at": extras.get("mixed_routes_checked_at", existing.get("mixed_routes_checked_at")),
        "mixed_routes_details": extras.get("mixed_routes_details", existing.get("mixed_routes_details")),
    }
    with _lock:
        _cache[key] = record
    save_cache()


def update_cache_key(key: str, data: Dict[str, Any], **extras: Any) -> None:
    norm = _normalize_key(key)
    # preserve existing mixed_routes metadata unless explicitly provided
    existing = _cache.get(norm) or {}
    record = {
        "data": data,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "mixed_routes": extras.get("mixed_routes", existing.get("mixed_routes", None)),
        "mixed_routes_checked": extras.get("mixed_routes_checked", existing.get("mixed_routes_checked", False)),
        "mixed_routes_checked_at": extras.get("mixed_routes_checked_at", existing.get("mixed_routes_checked_at")),
        "mixed_routes_details": extras.get("mixed_routes_details", existing.get("mixed_routes_details")),
    }
    with _lock:
        _cache[norm] = record
    save_cache()


def clean_old_entries(max_days: int = 30) -> int:
    """Remove entries older than max_days (by fetched_at date). Returns removed count."""
    if max_days <= 0:
        return 0
    today = datetime.now(timezone.utc).date()
    rem = 0
    with _lock:
        keys = list(_cache.keys())
        for k in keys:
            try:
                ts = _cache[k].get("fetched_at")
                d = datetime.fromisoformat(ts.replace("Z", "+00:00")).date() if isinstance(ts, str) else today
                if (today - d).days > max_days:
                    _cache.pop(k, None)
                    rem += 1
            except Exception:
                _cache.pop(k, None)
                rem += 1
    if rem:
        save_cache()
    return rem


def is_same_day_cached(postcode: str) -> bool:
    item = get_cached(postcode)
    if not item:
        return False
    ts = item.get("fetched_at")
    try:
        cached_day = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).date()
    except Exception:
        return False
    return cached_day == datetime.now(timezone.utc).date()


def is_same_day_cached_key(key: str) -> bool:
    item = get_cached_key(key)
    if not item:
        return False
    ts = item.get("fetched_at")
    try:
        cached_day = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).date()
    except Exception:
        return False
    return cached_day == datetime.now(timezone.utc).date()


def iter_cached_postcodes() -> Dict[str, Dict[str, Any]]:
    with _lock:
        return dict(_cache)


def get_entry(postcode: str) -> Optional[Dict[str, Any]]:
    key = _pretty_postcode(postcode)
    with _lock:
        return _cache.get(key)


def delete_key(key: str) -> bool:
    """Delete an entry by key.
    Accepts aliases:
      - 'uprn:123' → exact key
      - 'pc:SL6 6AH' → deletes postcode entry stored as 'SL6 6AH'
      - 'SL6 6AH' → deletes postcode entry
    Returns True if a key was removed.
    """
    norm = _normalize_key(key)
    removed = False
    with _lock:
        if norm in _cache:
            _cache.pop(norm, None)
            removed = True
        else:
            # Support deleting postcode entries addressed as 'pc:<pretty>'
            if norm.lower().startswith("pc:"):
                pretty = _pretty_postcode(norm.split(":", 1)[1])
                if pretty in _cache:
                    _cache.pop(pretty, None)
                    removed = True
    if removed:
        save_cache()
    return removed


def delete_scope(prefix: str) -> int:
    """Delete entries by scope.
    prefix may be 'uprn:' or 'pc:'.
    - 'uprn:' removes keys starting with 'uprn:'.
    - 'pc:' removes all keys that are NOT 'uprn:' (i.e., postcode entries stored without prefix).
    Returns number of removed entries.
    """
    prefix_lower = prefix.lower()
    removed = 0
    with _lock:
        if prefix_lower.startswith("uprn:"):
            keys = [k for k in list(_cache.keys()) if k.lower().startswith("uprn:")]
        elif prefix_lower.startswith("pc:"):
            keys = [k for k in list(_cache.keys()) if not k.lower().startswith("uprn:")]
        else:
            keys = [k for k in list(_cache.keys()) if k.lower().startswith(prefix_lower)]
        for k in keys:
            _cache.pop(k, None)
            removed += 1
    if removed:
        save_cache()
    return removed


def update_verification(
    postcode: str,
    *,
    mixed_routes: bool,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    key = _pretty_postcode(postcode)
    with _lock:
        entry = _cache.get(key) or {}
        entry["mixed_routes"] = mixed_routes
        entry["mixed_routes_details"] = details or {}
        entry["mixed_routes_checked"] = True
        entry["mixed_routes_checked_at"] = datetime.now(timezone.utc).isoformat()
        _cache[key] = entry
    save_cache()


def should_throttle_verify(postcode: str, *, hours: int = 24) -> bool:
    if hours <= 0:
        return False
    entry = get_entry(postcode)
    if not entry:
        return False
    ts = entry.get("mixed_routes_checked_at")
    if not ts:
        return False
    try:
        checked = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return False
    delta = datetime.now(timezone.utc) - checked
    return delta.total_seconds() < hours * 3600
