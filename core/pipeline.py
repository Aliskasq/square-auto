"""Main pipeline — fetch candles, compute indicators, send to AI, post to Square."""
import logging
import os
import re
import unicodedata
import pandas as pd
from core.binance_api import fetch_klines, fetch_funding_rate
from core.indicators import calculate_binance_indicators, format_tf_summary
from core.smc import analyze_smc
from core.ai import ask_ai
from core.square_api import post_to_square
from core import queue_manager as qm
from config import get, get_current_model

logger = logging.getLogger(__name__)

PROMPT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "Prompt.txt")


def _clean_ai_response(text: str) -> str:
    """Remove strange symbols, non-latin scripts (Chinese, Arabic, etc.), 
    and fix common AI artifacts."""
    if not text:
        return text
    
    # Remove leading $TICKER if AI added it despite instructions
    # e.g. "$ACE " or "$ACE\n" or "$ACE —"
    text = re.sub(r'^\$[A-Z]{1,15}\s*[—–\-]?\s*', '', text).strip()
    
    # Remove non-latin, non-emoji characters (Chinese, Arabic, Cyrillic, etc.)
    cleaned = []
    for ch in text:
        cat = unicodedata.category(ch)
        # Allow: ASCII, Latin Extended, common punctuation, emojis, digits, spaces
        cp = ord(ch)
        if cp < 0x0250:  # Basic Latin + Latin Extended-A
            cleaned.append(ch)
        elif 0x2000 <= cp <= 0x27FF:  # General punctuation, symbols, dingbats
            cleaned.append(ch)
        elif 0x2900 <= cp <= 0x2BFF:  # Arrows, math symbols
            cleaned.append(ch)
        elif cat.startswith('So'):  # Symbol, other (includes emojis)
            cleaned.append(ch)
        elif cat.startswith('Sk'):  # Symbol, modifier
            cleaned.append(ch)
        elif cp >= 0x1F000:  # Emoji ranges
            cleaned.append(ch)
        # Skip everything else (CJK, Cyrillic, Arabic, etc.)
    
    text = ''.join(cleaned)
    
    # Fix merged words (e.g. "theFunding" -> "the Funding")
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
    
    # Remove multiple spaces
    text = re.sub(r' {2,}', ' ', text).strip()
    
    return text


def _load_prompt() -> str:
    try:
        with open(PROMPT_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


SECTOR_PHRASES = {
    "AI": [
        "another AI coin catching momentum",
        "AI sector heating up again",
        "this AI play looks interesting",
        "AI coins are on fire lately",
    ],
    "DeFi": [
        "DeFi showing strength here",
        "this DeFi gem is moving",
        "DeFi sector pushing higher",
        "solid DeFi coin with real volume",
    ],
    "Meme": [
        "meme coin territory — high risk high reward",
        "careful with this one, it's a meme play",
        "meme sector can run hard but exits fast",
        "this meme coin could pop but keep stops tight",
    ],
    "Gaming": [
        "gaming token picking up steam",
        "gaming sector showing life",
        "this gaming play has momentum",
        "gaming coins catching bids",
    ],
    "L1": [
        "L1 play with decent structure",
        "another layer 1 making moves",
        "L1 looking strong on the chart",
        "solid L1 infrastructure play",
    ],
    "L2": [
        "L2 scaling play gaining traction",
        "layer 2 picking up volume",
        "L2 sector showing strength",
        "this L2 has momentum behind it",
    ],
    "Infrastructure": [
        "infra play building momentum",
        "infrastructure coin looking solid",
        "infra sector quietly moving",
        "infrastructure play worth watching",
    ],
    "RWA": [
        "RWA sector getting attention",
        "real world asset play heating up",
        "RWA narrative pushing this one",
        "RWA coins seeing strong flows",
    ],
}

# Mix of sectors
SECTOR_MIX_PHRASES = {
    ("AI", "DeFi"): [
        "AI meets DeFi — interesting combo",
        "cross-sector play bridging AI and DeFi",
        "AI+DeFi narrative driving this one",
        "where AI and DeFi intersect, things get interesting",
    ],
    ("AI", "Gaming"): [
        "AI gaming fusion catching bids",
        "where AI meets gaming, volatility follows",
        "AI+gaming narrative pushing this coin",
        "interesting blend of AI and gaming tech",
    ],
    ("Meme", "AI"): [
        "AI-powered meme coin — wild but it's moving",
        "meme meets AI, buckle up",
        "AI meme play, trade carefully",
        "meme coin with AI narrative, volatile stuff",
    ],
}


def _get_sector_phrase(sector: str, post_number: int) -> str:
    """Get a rotating sector phrase."""
    if not sector:
        return ""

    # Check for mix sectors (e.g. "AI, DeFi")
    parts = [s.strip() for s in sector.split(",")]
    if len(parts) > 1:
        key = tuple(sorted(parts[:2]))
        phrases = SECTOR_MIX_PHRASES.get(key, [])
        if phrases:
            return phrases[post_number % len(phrases)]

    # Single sector
    for k, phrases in SECTOR_PHRASES.items():
        if k.lower() in sector.lower():
            return phrases[post_number % len(phrases)]

    return f"{sector} sector coin showing momentum"


# Cache for prepared AI prompts — don't reload candles on model retry
_data_cache: dict[str, dict] = {}


async def prepare_data(ticker: str, price: float, sector: str = "") -> dict | None:
    """Step 1: Fetch candles (2 requests), calculate indicators, build AI prompt.
    
    Cached by ticker — if model fails, we reuse this data.
    Returns dict with system_prompt, user_message, short_name, post_number.
    """
    symbol = ticker.upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"

    # Check cache first
    if symbol in _data_cache:
        logger.info(f"📋 Using cached data for {symbol}")
        return _data_cache[symbol]

    short_name = symbol.replace("USDT", "")
    post_number = qm.get_post_number()

    logger.info(f"📊 Loading data for {symbol} (2 API calls)...")

    # 1. Fetch candles — 2 requests: 1H + 15M (1500 each)
    raw_1h = await fetch_klines(symbol, "1h", 1500)
    if not raw_1h:
        logger.error(f"Failed to fetch 1H klines for {symbol}")
        return None

    raw_15m = await fetch_klines(symbol, "15m", 1500)
    if not raw_15m:
        logger.error(f"Failed to fetch 15M klines for {symbol}")
        return None

    # 2. Calculate indicators
    df_1h = pd.DataFrame(raw_1h)
    df_15m = pd.DataFrame(raw_15m)

    indic_1h, _ = calculate_binance_indicators(df_1h, "1h")
    indic_15m, _ = calculate_binance_indicators(df_15m, "15m")

    # 3. SMC analysis
    smc_1h = analyze_smc(df_1h, "1H", symbol=symbol)
    smc_15m = analyze_smc(df_15m, "15m", symbol=symbol)

    # 4. Funding rate
    funding = await fetch_funding_rate(symbol)

    # 5. Format text
    tf_1h_text = format_tf_summary(indic_1h, "1H")
    tf_15m_text = format_tf_summary(indic_15m, "15m")
    smc_1h_text = smc_1h.get("summary", "")
    smc_15m_text = smc_15m.get("summary", "")

    # 6. Build AI prompt
    sector_phrase = _get_sector_phrase(sector, post_number)
    sector_context = f"\nSector: {sector}. {sector_phrase}" if sector else ""

    user_message = (
        f"Coin: {short_name}\n"
        f"Price: {price}\n"
        f"Funding: {funding}\n"
        f"{sector_context}\n\n"
        f"{tf_1h_text}\n\n"
        f"{smc_1h_text}\n\n"
        f"{tf_15m_text}\n\n"
        f"{smc_15m_text}"
    )

    system_prompt = _load_prompt()

    # Cache it
    data = {
        "system_prompt": system_prompt,
        "user_message": user_message,
        "short_name": short_name,
        "symbol": symbol,
        "post_number": post_number,
    }
    _data_cache[symbol] = data
    logger.info(f"💾 Cached data for {symbol}")

    return data


def clear_cache(symbol: str):
    """Remove cached data after successful post."""
    _data_cache.pop(symbol, None)


async def process_ticker(ticker: str, price: float, sector: str = "") -> str | None:
    """Full pipeline: prepare data (cached) → AI → post."""

    # Step 1: Prepare data (loads candles only if not cached)
    data = await prepare_data(ticker, price, sector)
    if not data:
        return None

    symbol = data["symbol"]
    short_name = data["short_name"]
    post_number = data["post_number"]
    model = get_current_model(post_number)

    # Step 2: Ask AI (uses cached prompt text, retries models+keys internally)
    model_short = model.split("/")[-1]
    logger.info(f"🤖 ${short_name} → AI модель: {model_short}")
    ai_response = await ask_ai(data["system_prompt"], data["user_message"], model)

    if not ai_response:
        logger.error(f"❌ ${short_name} — все модели × все ключи не ответили!")
        return "💀 ALL_MODELS_DEAD"

    # Step 3: Clean AI response + prepend $TICKER
    ai_response = _clean_ai_response(ai_response)
    if not ai_response or len(ai_response) < 50:
        logger.error(f"❌ ${short_name} — ответ AI слишком короткий после очистки")
        return None

    # Step 4: Build Square post
    hashtags = get("hashtags") or "#BinanceSquare #Write2Earn"
    square_text = f"${short_name}\n{ai_response}\n\n{hashtags}"

    if len(square_text) > 2100:
        square_text = square_text[:2097] + "..."

    # Step 5: Generate chart image
    chart_path = None
    try:
        from core.chart_generator import generate_chart_for_symbol
        chart_path = await generate_chart_for_symbol(symbol, f"/tmp/{symbol}_chart.png")
        if chart_path:
            logger.info(f"📊 Chart generated for ${short_name}")
        else:
            logger.warning(f"⚠️ Chart generation failed for ${short_name}, posting text-only")
    except Exception as e:
        logger.warning(f"⚠️ Chart error for ${short_name}: {e}")

    # Step 6: Post to Square (with chart if available)
    from config import SQUARE_API_KEY
    result = await post_to_square(square_text, SQUARE_API_KEY, image_path=chart_path)

    # Step 7: Update counters + clear cache + cleanup chart
    qm.increment_post_count()
    qm.mark_posted(ticker)
    clear_cache(symbol)

    # Cleanup temp chart file
    if chart_path:
        try:
            os.remove(chart_path)
        except Exception:
            pass

    logger.info(f"✅ ${short_name} опубликован | модель: {model_short} | {result}")

    return f"✅ модель: {model_short}"
