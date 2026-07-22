"""AI chat via OpenRouter — sequential requests."""
import logging
import httpx
from config import get_api_key, count_request, force_rotate_key, OPENROUTER_KEYS

logger = logging.getLogger(__name__)


async def ask_ai(system_prompt: str, user_text: str, model: str) -> str | None:
    """Send prompt to AI, return response. Handles key rotation on 429."""
    tried = 0
    total_keys = len(OPENROUTER_KEYS)

    while tried < max(total_keys, 1):
        api_key = get_api_key()
        if not api_key:
            return None

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_text})

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"model": model, "messages": messages},
                    timeout=120,
                )

                if resp.status_code == 429:
                    tried += 1
                    if force_rotate_key():
                        logger.info(f"Key 429, rotated")
                        continue
                    return None

                if resp.status_code != 200:
                    logger.error(f"AI HTTP {resp.status_code}")
                    return None

                data = resp.json()
                if "error" in data:
                    if data["error"].get("code") == 429:
                        tried += 1
                        if force_rotate_key():
                            continue
                        return None
                    logger.error(f"AI error: {data['error']}")
                    return None

                choices = data.get("choices") or [{}]
                content = (choices[0].get("message") or {}).get("content", "")
                if content:
                    count_request()
                    return content.strip()
                return None

        except Exception as e:
            logger.error(f"AI exception: {e}")
            return None

    return None
