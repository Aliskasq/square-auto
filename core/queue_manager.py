"""Queue manager — handles sleep, overflow, dedup, hourly/daily limits, group 2 backfill."""
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

# Active hours per day: 05:00-01:00 MSK = 20 hours (sleep 01:00-05:00 = 4h)
ACTIVE_HOURS = 20
TARGET_DAILY_MIN = 98


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
    dedup = _load_json(DEDUP_FILE, {})
    dedup_hours = get("dedup_hours") or 4
    cutoff = time.time() - dedup_hours * 3600
    dedup = {k: v for k, v in dedup.items() if v > cutoff}
    _save_json(DEDUP_FILE, dedup)
    return ticker in dedup


def mark_posted(ticker: str):
    dedup = _load_json(DEDUP_FILE, {})
    dedup[ticker] = time.time()
    _save_json(DEDUP_FILE, dedup)


# --- Counters (hourly + daily) ---

def _load_counters() -> dict:
    return _load_json(COUNTERS_FILE, {
        "hour_key": "", "hour_count": 0,
        "day_key": "", "day_count": 0,
        "total_post_number": 0,
        "hour_history": {},  # "YYYY-MM-DD-HH": count — for pace tracking
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
    c = _load_counters()
    hour_key = _current_hour_key()
    day_key = _current_day_key()

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

    # Save hour history for pace tracking
    hh = c.get("hour_history", {})
    hh[hour_key] = c["hour_count"]
    # Cleanup old (keep last 48h)
    cutoff_dt = datetime.now(MSK) - timedelta(hours=48)
    cutoff_key = cutoff_dt.strftime("%Y-%m-%d-%H")
    hh = {k: v for k, v in hh.items() if k >= cutoff_key}
    c["hour_history"] = hh

    _save_counters(c)


def hour_slots_remaining() -> int:
    c = _load_counters()
    hour_key = _current_hour_key()
    hc = c["hour_count"] if c.get("hour_key") == hour_key else 0
    max_hour = get("posts_per_hour") or 5
    return max(0, max_hour - hc)


def day_posts_count() -> int:
    c = _load_counters()
    day_key = _current_day_key()
    return c["day_count"] if c.get("day_key") == day_key else 0


def day_slots_remaining() -> int:
    max_day = get("posts_per_day") or 100
    return max(0, max_day - day_posts_count())


def are_we_behind_pace() -> tuple[bool, int]:
    """Check if we're behind pace to reach 98 posts/day.
    
    Active hours: 05:00-01:00 MSK (20 hours).
    Target: ~5 posts/hour.
    
    Returns: (behind: bool, deficit: int)
    """
    now = datetime.now(MSK)
    
    # Calculate hours elapsed since day start (05:00 MSK, first active hour)
    day_start_hour = 5  # First active hour after sleep
    
    if now.hour >= day_start_hour:
        hours_elapsed = now.hour - day_start_hour + 1
    elif now.hour < 3:
        # 00:00-02:59 MSK = still same day, hours_elapsed = 20-24h territory
        hours_elapsed = (24 - day_start_hour) + now.hour + 1
    else:
        # 03:00-04:59 = sleep time, shouldn't be called but handle gracefully
        return False, 0
    
    hours_elapsed = min(hours_elapsed, ACTIVE_HOURS)
    
    # Expected posts by now
    posts_per_hour = get("posts_per_hour") or 5
    expected = hours_elapsed * posts_per_hour
    actual = day_posts_count()
    
    deficit = expected - actual
    behind = deficit > 0
    
    if behind:
        logger.debug(f"Pace check: {hours_elapsed}h elapsed, expected {expected}, actual {actual}, deficit {deficit}")
    
    return behind, max(0, deficit)


def get_counters_info() -> str:
    c = _load_counters()
    hour_key = _current_hour_key()
    day_key = _current_day_key()
    hc = c["hour_count"] if c.get("hour_key") == hour_key else 0
    dc = c["day_count"] if c.get("day_key") == day_key else 0
    behind, deficit = are_we_behind_pace()
    pace_str = f" ⚠️ -{deficit} behind pace" if behind else " ✅ on pace"
    return f"Hour: {hc}/{get('posts_per_hour')}, Day: {dc}/{get('posts_per_day')}{pace_str}, Total: {c.get('total_post_number', 0)}"


# --- Sleep ---

def is_sleep_time() -> bool:
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
    else:
        return now_min >= start_min or now_min < end_min


# --- Queue (overflow + sleep) ---

def add_to_queue(ticker: str, price: float, sector: str = "", source: str = "group1"):
    q = _load_json(QUEUE_FILE, [])
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


async def filter_queue_on_wake() -> tuple[int, int, list]:
    """On wake: drop >15% losers, keep only top 5 growers."""
    from core.binance_api import get_current_price
    q = _load_json(QUEUE_FILE, [])
    if not q:
        return 0, 0, []
    
    drop_pct = get("drop_pct") or 15
    candidates = []
    dropped = 0
    
    for item in q:
        if item.get("source", "").startswith("overflow"):
            candidates.append(item)
            continue
        
        current = await get_current_price(item["ticker"])
        if current and item["price"] > 0:
            change_pct = ((current - item["price"]) / item["price"]) * 100
            if change_pct < -drop_pct:
                logger.info(f"🗑 Sleep drop {item['ticker']}: {change_pct:.1f}%")
                dropped += 1
                continue
            item["current_price"] = current
            item["growth_pct"] = change_pct
        else:
            item["growth_pct"] = 0
        candidates.append(item)
    
    overflow_items = [c for c in candidates if c.get("source", "").startswith("overflow")]
    sleep_items = [c for c in candidates if not c.get("source", "").startswith("overflow")]
    
    sleep_items.sort(key=lambda x: x.get("growth_pct", 0), reverse=True)
    kept_sleep = sleep_items[:5]
    discarded = len(sleep_items) - len(kept_sleep)
    if discarded > 0:
        logger.info(f"🗑 Discarded {discarded} sleep tickers (kept top 5)")
    
    final_queue = overflow_items + kept_sleep
    _save_json(QUEUE_FILE, final_queue)
    
    kept_names = [t["ticker"] for t in kept_sleep]
    return dropped, len(kept_sleep), kept_names


async def filter_queue_by_price():
    dropped, kept, _ = await filter_queue_on_wake()
    return dropped


# --- Group 2 tickers ---

def add_group2_ticker(ticker: str, price: float, sector: str = ""):
    """Log ticker from secondary group. Kept for 48 hours."""
    g2 = _load_json(GROUP2_FILE, [])
    
    # Remove expired (>48h)
    cutoff = time.time() - 48 * 3600
    g2 = [t for t in g2 if t["timestamp"] > cutoff]
    
    # Update if exists, else add
    g2 = [t for t in g2 if t["ticker"] != ticker]
    g2.append({
        "ticker": ticker,
        "price": price,
        "sector": sector,
        "timestamp": time.time(),
    })
    
    # Keep last 500
    if len(g2) > 500:
        g2 = g2[-500:]
    _save_json(GROUP2_FILE, g2)


def remove_group2_ticker(ticker: str):
    """Remove ticker from group 2 (after posting)."""
    g2 = _load_json(GROUP2_FILE, [])
    g2 = [t for t in g2 if t["ticker"] != ticker]
    _save_json(GROUP2_FILE, g2)


async def get_top_group2_tickers(count: int) -> list[dict]:
    """Get top N tickers from group 2 sorted by growth from push price.
    
    Filters: not recently posted, not expired (48h), positive growth only.
    Returns list of dicts with current_price and growth_pct added.
    """
    from core.binance_api import get_current_price
    g2 = _load_json(GROUP2_FILE, [])
    if not g2:
        return []

    # Filter expired (>48h)
    cutoff = time.time() - 48 * 3600
    g2 = [t for t in g2 if t["timestamp"] > cutoff]

    # Check prices and calculate growth
    scored = []
    for item in g2:
        if is_recently_posted(item["ticker"]):
            continue
        current = await get_current_price(item["ticker"])
        if current and item["price"] > 0:
            growth = ((current - item["price"]) / item["price"]) * 100
            if growth > 0:  # Only growing coins
                entry = dict(item)
                entry["current_price"] = current
                entry["growth_pct"] = growth
                scored.append(entry)

    # Sort by growth descending, return top N
    scored.sort(key=lambda x: x["growth_pct"], reverse=True)
    return scored[:count]


async def get_best_group2_ticker() -> dict | None:
    """Legacy: get single best ticker."""
    top = await get_top_group2_tickers(1)
    return top[0] if top else None
