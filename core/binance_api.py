"""Binance Futures API — klines + current price + futures symbol check."""
import logging
import asyncio
import time
import httpx

logger = logging.getLogger(__name__)

LAST_WEIGHT_WARNING = 0


async def fetch_klines(symbol: str, interval: str, limit: int = 1500) -> list | None:
    """Fetch klines from Binance Futures."""
    url = f"https://fapi.binance.com/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                raw = resp.json()
                if not raw:
                    return None
                return [
                    {
                        "open_time": int(c[0]),
                        "open": float(c[1]),
                        "high": float(c[2]),
                        "low": float(c[3]),
                        "close": float(c[4]),
                        "volume": float(c[5]),
                        "taker_buy_volume": float(c[9]) if len(c) > 9 else float(c[5]) * 0.5,
                    }
                    for c in raw
                ]
            elif resp.status_code == 429:
                logger.warning(f"Binance 429 for {symbol}, waiting 30s")
                await asyncio.sleep(30)
                return None
            else:
                logger.error(f"Binance klines {symbol} HTTP {resp.status_code}")
                return None
    except Exception as e:
        logger.error(f"Binance klines error {symbol}: {e}")
        return None


async def fetch_current_price(symbol: str) -> float | None:
    """Fetch current price from Binance Futures."""
    url = "https://fapi.binance.com/fapi/v1/ticker/price"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params={"symbol": symbol}, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return float(data.get("price", 0))
    except Exception as e:
        logger.error(f"Price fetch error {symbol}: {e}")
    return None


async def fetch_price_mexc(symbol: str) -> float | None:
    """Fetch price from MEXC as fallback."""
    url = f"https://api.mexc.com/api/v3/ticker/price"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params={"symbol": symbol}, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return float(data.get("price", 0))
    except Exception:
        pass
    return None


async def get_current_price(symbol: str) -> float | None:
    """Get price, trying MEXC first then Binance."""
    price = await fetch_price_mexc(symbol)
    if price and price > 0:
        return price
    return await fetch_current_price(symbol)


async def is_futures_symbol(symbol: str) -> bool:
    """Check if symbol exists on Binance Futures."""
    url = "https://fapi.binance.com/fapi/v1/ticker/price"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params={"symbol": symbol}, timeout=10)
            return resp.status_code == 200
    except Exception:
        return False


async def fetch_funding_rate(symbol: str) -> str:
    """Fetch current funding rate."""
    url = "https://fapi.binance.com/fapi/v1/premiumIndex"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params={"symbol": symbol}, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                rate = float(data.get("lastFundingRate", 0)) * 100
                return f"{rate:.4f}%"
    except Exception:
        pass
    return "Unknown"
