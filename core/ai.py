"""AI chat via OpenRouter — sequential requests with model + key rotation."""
import logging
import asyncio
import httpx
from config import get_api_key, count_request, force_rotate_key, OPENROUTER_KEYS

logger = logging.getLogger(__name__)


async def _try_single_request(model: str, api_key: str, system_prompt: str, user_text: str) -> tuple[str | None, bool]:
    """Try one request with specific model + key.
    
    Returns: (response_text or None, should_rotate_key: bool)
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
                return None, True  # rate limit — rotate key

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
    1. Try all models with current key
    2. If all fail — rotate to next key, try all models again
    3. Repeat until all keys exhausted
    4. Only return None if ALL keys × ALL models failed
    """
    from config import get

    models = get("models") or [model]
    if not models:
        models = [model]

    # Find start index for model rotation
    try:
        start_idx = models.index(model)
    except ValueError:
        start_idx = 0

    total_keys = max(len(OPENROUTER_KEYS), 1)
    keys_tried = 0

    while keys_tried < total_keys:
        api_key = get_api_key()
        if not api_key:
            return None

        key_short = api_key[-6:]
        key_had_429 = False

        # Try all models with this key
        for i in range(len(models)):
            idx = (start_idx + i) % len(models)
            current_model = models[idx]
            short_name = current_model.split("/")[-1]

            if i > 0:
                logger.info(f"🔄 Fallback model {i+1}/{len(models)}: {short_name} (key ...{key_short})")

            response, should_rotate = await _try_single_request(
                current_model, api_key, system_prompt, user_text
            )

            if response:
                if i > 0:
                    logger.info(f"✅ Fallback {short_name} responded")
                return response

            if should_rotate:
                key_had_429 = True
                logger.warning(f"🔑 Key ...{key_short} got 429 on {short_name}")
                break  # Stop trying models with this key, rotate key

            # Small delay before trying next model
            await asyncio.sleep(1)

        # Rotate to next key
        keys_tried += 1
        if keys_tried < total_keys:
            if force_rotate_key():
                new_key = get_api_key()
                logger.info(f"🔑 Rotated to key ...{new_key[-6:]} (attempt {keys_tried + 1}/{total_keys})")
                await asyncio.sleep(3)
            else:
                break

    logger.error(f"All {total_keys} keys × {len(models)} models failed!")
    return None
