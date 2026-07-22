"""Telegram group listener — extracts tickers from push messages."""
import re
import logging

logger = logging.getLogger(__name__)

# Pattern: $TICKER at start of message
TICKER_PATTERN = re.compile(r'^\$([A-Z]{2,15})', re.MULTILINE)
# Price pattern: Price: 0.39360 or 💰 Price: 0.39360
PRICE_PATTERN = re.compile(r'Price:\s*\$?([\d.]+)', re.IGNORECASE)
# Sector pattern: Sector: 🏦 DeFi or 🏷 Sector: AI
SECTOR_PATTERN = re.compile(r'Sector:\s*(?:[\U0001F000-\U0001FFFF]\s*)?(.+?)$', re.MULTILINE | re.IGNORECASE)


def parse_push_message(text: str) -> dict | None:
    """Parse push message from group.
    
    Returns: {"ticker": "LDO", "price": 0.3936, "sector": "DeFi"} or None
    """
    if not text:
        return None

    ticker_match = TICKER_PATTERN.search(text)
    if not ticker_match:
        return None

    ticker = ticker_match.group(1).upper()

    # Extract price
    price = 0.0
    price_match = PRICE_PATTERN.search(text)
    if price_match:
        try:
            price = float(price_match.group(1))
        except ValueError:
            pass

    # Extract sector
    sector = ""
    sector_match = SECTOR_PATTERN.search(text)
    if sector_match:
        sector = sector_match.group(1).strip()
        # Clean emoji from sector
        sector = re.sub(r'[\U0001F000-\U0001FFFF]', '', sector).strip()

    return {
        "ticker": ticker,
        "price": price,
        "sector": sector,
    }
