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


def update_cache(postcode: str, data: Dict[str, Any]) -> None:
    key = _pretty_postcode(postcode)
    record = {
        "data": data,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    with _lock:
        _cache[key] = record
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


def iter_cached_postcodes() -> Dict[str, Dict[str, Any]]:
    with _lock:
        return dict(_cache)

