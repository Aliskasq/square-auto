"""Queue manager — handles sleep, overflow, dedup, hourly/daily limits."""
import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta

from config import get, set_val

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
QUEUE_FILE = os.path.join(DATA_DIR, "queue.json")
GROUP2_FILE = os.path.join(DATA_DIR, "group2_tickers.json")
DEDUP_FILE = os.path.join(DATA_DIR, "dedup.json")
COUNTERS_FILE = os.path.join(DATA_DIR, "counters.json")

MSK = timezone(timedelta(hours=3))


def _ensure_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def _load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default if default is not None else []


def _save_json(path, data):
    _ensure_dir()
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# --- Dedup ---

def is_recently_posted(ticker: str) -> bool:
    """Check if ticker was posted within dedup window."""
    dedup = _load_json(DEDUP_FILE, {})
    dedup_hours = get("dedup_hours") or 4
    cutoff = time.time() - dedup_hours * 3600
    # Cleanup old entries
    dedup = {k: v for k, v in dedup.items() if v > cutoff}
    _save_json(DEDUP_FILE, dedup)
    return ticker in dedup


def mark_posted(ticker: str):
    """Mark ticker as posted."""
    dedup = _load_json(DEDUP_FILE, {})
    dedup[ticker] = time.time()
    _save_json(DEDUP_FILE, dedup)


# --- Counters (hourly + daily) ---

def _load_counters() -> dict:
    return _load_json(COUNTERS_FILE, {
        "hour_key": "", "hour_count": 0,
        "day_key": "", "day_count": 0,
        "total_post_number": 0,
    })


def _save_counters(c: dict):
    _save_json(COUNTERS_FILE, c)


def get_post_number() -> int:
    c = _load_counters()
    return c.get("total_post_number", 0)


def _current_hour_key() -> str:
    now = datetime.now(MSK)
    return now.strftime("%Y-%m-%d-%H")


def _current_day_key() -> str:
    """Day resets at 03:00 MSK."""
    now = datetime.now(MSK)
    if now.hour < 3:
        now -= timedelta(days=1)
    return now.strftime("%Y-%m-%d")


def can_post_now() -> tuple[bool, str]:
    """Check if we can post right now. Returns (allowed, reason)."""
    c = _load_counters()
    hour_key = _current_hour_key()
    day_key = _current_day_key()

    # Reset counters if new period
    if c.get("hour_key") != hour_key:
        c["hour_key"] = hour_key
        c["hour_count"] = 0
    if c.get("day_key") != day_key:
        c["day_key"] = day_key
        c["day_count"] = 0
    _save_counters(c)

    max_hour = get("posts_per_hour") or 5
    max_day = get("posts_per_day") or 100

    if c["day_count"] >= max_day:
        return False, f"daily limit ({max_day})"
    if c["hour_count"] >= max_hour:
        return False, f"hourly limit ({max_hour})"
    return True, ""


def increment_post_count():
    c = _load_counters()
    hour_key = _current_hour_key()
    day_key = _current_day_key()

    if c.get("hour_key") != hour_key:
        c["hour_key"] = hour_key
        c["hour_count"] = 0
    if c.get("day_key") != day_key:
        c["day_key"] = day_key
        c["day_count"] = 0

    c["hour_count"] += 1
    c["day_count"] += 1
    c["total_post_number"] = c.get("total_post_number", 0) + 1
    _save_counters(c)


def get_counters_info() -> str:
    c = _load_counters()
    hour_key = _current_hour_key()
    day_key = _current_day_key()
    hc = c["hour_count"] if c.get("hour_key") == hour_key else 0
    dc = c["day_count"] if c.get("day_key") == day_key else 0
    return f"Hour: {hc}/{get('posts_per_hour')}, Day: {dc}/{get('posts_per_day')}, Total: {c.get('total_post_number', 0)}"


# --- Sleep ---

def is_sleep_time() -> bool:
    """Check if current time is in sleep window (MSK)."""
    now = datetime.now(MSK)
    sleep_start = get("sleep_start") or "01:00"
    sleep_end = get("sleep_end") or "05:00"

    try:
        sh, sm = map(int, sleep_start.split(":"))
        eh, em = map(int, sleep_end.split(":"))
    except ValueError:
        return False

    start_min = sh * 60 + sm
    end_min = eh * 60 + em
    now_min = now.hour * 60 + now.minute

    if start_min <= end_min:
        return start_min <= now_min < end_min
    else:  # crosses midnight
        return now_min >= start_min or now_min < end_min


# --- Queue (overflow + sleep) ---

def add_to_queue(ticker: str, price: float, sector: str = "", source: str = "group1"):
    """Add ticker to queue (overflow or sleep)."""
    q = _load_json(QUEUE_FILE, [])
    # Don't add duplicates
    if any(item["ticker"] == ticker for item in q):
        return
    q.append({
        "ticker": ticker,
        "price": price,
        "sector": sector,
        "source": source,
        "timestamp": time.time(),
    })
    _save_json(QUEUE_FILE, q)
    logger.info(f"Queued {ticker} @ {price} ({source})")


def get_queue() -> list:
    return _load_json(QUEUE_FILE, [])


def remove_from_queue(ticker: str):
    q = _load_json(QUEUE_FILE, [])
    q = [item for item in q if item["ticker"] != ticker]
    _save_json(QUEUE_FILE, q)


def clear_queue():
    _save_json(QUEUE_FILE, [])


async def filter_queue_by_price():
    """Remove tickers from queue where price dropped >drop_pct% from push price."""
    from core.binance_api import get_current_price
    q = _load_json(QUEUE_FILE, [])
    drop_pct = get("drop_pct") or 15
    filtered = []
    for item in q:
        current = await get_current_price(item["ticker"])
        if current and item["price"] > 0:
            change_pct = ((current - item["price"]) / item["price"]) * 100
            if change_pct < -drop_pct:
                logger.info(f"Dropped {item['ticker']}: {change_pct:.1f}% from push")
                continue
        filtered.append(item)
    _save_json(QUEUE_FILE, filtered)
    return len(q) - len(filtered)


# --- Group 2 tickers ---

def add_group2_ticker(ticker: str, price: float, sector: str = ""):
    """Log ticker from secondary group."""
    g2 = _load_json(GROUP2_FILE, [])
    # Update if exists
    g2 = [t for t in g2 if t["ticker"] != ticker]
    g2.append({
        "ticker": ticker,
        "price": price,
        "sector": sector,
        "timestamp": time.time(),
    })
    # Keep last 200
    if len(g2) > 200:
        g2 = g2[-200:]
    _save_json(GROUP2_FILE, g2)


async def get_best_group2_ticker() -> dict | None:
    """Get the best performing ticker from group 2 (most growth from push price)."""
    from core.binance_api import get_current_price
    g2 = _load_json(GROUP2_FILE, [])
    if not g2:
        return None

    # Filter out old ones (>24h)
    cutoff = time.time() - 24 * 3600
    g2 = [t for t in g2 if t["timestamp"] > cutoff]

    best = None
    best_growth = -999

    for item in g2:
        if is_recently_posted(item["ticker"]):
            continue
        current = await get_current_price(item["ticker"])
        if current and item["price"] > 0:
            growth = ((current - item["price"]) / item["price"]) * 100
            if growth > best_growth and growth > 0:
                best_growth = growth
                best = item
                best["current_price"] = current
                best["growth_pct"] = growth

    return best
