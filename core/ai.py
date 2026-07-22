"""AI chat via OpenRouter — sequential requests with model + key rotation.

Key memory: if key 2 worked last time, next post starts from key 2.
Only goes back to key 1 after key 3 fails (full circle).
"""
import logging
import asyncio
import httpx
from config import count_request, OPENROUTER_KEYS

logger = logging.getLogger(__name__)

# Persistent state: remember which key is currently working
_key_state = {"idx": 0}


def _get_key(idx: int) -> str | None:
    if not OPENROUTER_KEYS:
        return None
    return OPENROUTER_KEYS[idx % len(OPENROUTER_KEYS)]


async def _try_single_request(model: str, api_key: str, system_prompt: str, user_text: str) -> tuple[str | None, bool]:
    """Try one request with specific model + key.
    
    Returns: (response_text or None, is_429: bool)
    """
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
                return None, True

            if resp.status_code != 200:
                logger.error(f"AI HTTP {resp.status_code} on {model}")
                return None, False

            data = resp.json()
            if "error" in data:
                err = data["error"]
                if err.get("code") == 429 or "429" in str(err):
                    return None, True
                logger.error(f"AI error on {model}: {err}")
                return None, False

            choices = data.get("choices") or [{}]
            content = (choices[0].get("message") or {}).get("content", "")
            if content and len(content.strip()) > 20:
                count_request()
                return content.strip(), False
            logger.warning(f"AI empty/short response from {model}")
            return None, False

    except httpx.TimeoutException:
        logger.error(f"AI timeout on {model}")
        return None, False
    except Exception as e:
        logger.error(f"AI exception on {model}: {e}")
        return None, False


async def ask_ai(system_prompt: str, user_text: str, model: str) -> str | None:
    """Send prompt to AI with full rotation: models × keys.
    
    Flow:
    - Start with the key that worked last time (_key_state)
    - Try all models with that key
    - If all fail → move to next key, try all models
    - If that key works → remember it for next post
    - Only go back to previous key after full circle
    - If ALL keys × ALL models fail → return None (caller sends TG alert)
    """
    from config import get

    models = get("models") or [model]
    if not models:
        models = [model]

    try:
        start_model_idx = models.index(model)
    except ValueError:
        start_model_idx = 0

    total_keys = max(len(OPENROUTER_KEYS), 1)
    start_key_idx = _key_state["idx"]

    for key_attempt in range(total_keys):
        key_idx = (start_key_idx + key_attempt) % total_keys
        api_key = _get_key(key_idx)
        if not api_key:
            continue

        key_short = api_key[-6:]

        for model_attempt in range(len(models)):
            m_idx = (start_model_idx + model_attempt) % len(models)
            current_model = models[m_idx]
            short_name = current_model.split("/")[-1]

            if key_attempt > 0 or model_attempt > 0:
                logger.info(f"🔄 key ...{key_short} model {model_attempt+1}/{len(models)}: {short_name}")

            response, is_429 = await _try_single_request(
                current_model, api_key, system_prompt, user_text
            )

            if response:
                # Success! Remember this key for next post
                _key_state["idx"] = key_idx
                if key_attempt > 0 or model_attempt > 0:
                    logger.info(f"✅ key ...{key_short} / {short_name} responded")
                return response

            if is_429:
                logger.warning(f"🔑 Key ...{key_short} got 429, moving to next key")
                break  # Try next key

            await asyncio.sleep(1)

        # Small delay before trying next key
        if key_attempt < total_keys - 1:
            await asyncio.sleep(3)

    logger.error(f"💀 All {total_keys} keys × {len(models)} models failed!")
    return None
