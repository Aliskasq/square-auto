"""Binance Square posting API."""
import logging
import httpx

logger = logging.getLogger(__name__)


async def post_to_square(text: str, api_key: str) -> str:
    """Post text to Binance Square."""
    if not api_key:
        return "❌ SQUARE_API_KEY not set"

    url = "https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add"
    headers = {
        "X-Square-OpenAPI-Key": api_key,
        "Content-Type": "application/json",
        "clienttype": "binanceSkill",
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers, json={"bodyTextOnly": text}, timeout=15)
            data = resp.json()
            if data.get("code") == "000000":
                post_id = data.get("data", {}).get("id", "unknown")
                return f"✅ Posted! https://www.binance.com/square/post/{post_id}"
            return f"❌ Square error: {data.get('message', 'unknown')}"
    except Exception as e:
        logger.error(f"Square post error: {e}")
        return f"❌ Connection error: {e}"
