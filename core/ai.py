"""AI chat via OpenRouter — sequential requests with model rotation."""
import logging
import httpx
from config import get_api_key, count_request, force_rotate_key, OPENROUTER_KEYS

logger = logging.getLogger(__name__)


async def _try_model(model: str, system_prompt: str, user_text: str) -> str | None:
    """Try a single model with key rotation on 429. Returns response or None."""
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
                        logger.info(f"Key 429 on {model}, rotated key")
                        continue
                    return None

                if resp.status_code != 200:
                    logger.error(f"AI HTTP {resp.status_code} on {model}")
                    return None

                data = resp.json()
                if "error" in data:
                    err = data["error"]
                    if err.get("code") == 429 or "429" in str(err):
                        tried += 1
                        if force_rotate_key():
                            continue
                        return None
                    logger.error(f"AI error on {model}: {err}")
                    return None

                choices = data.get("choices") or [{}]
                content = (choices[0].get("message") or {}).get("content", "")
                if content and len(content.strip()) > 20:
                    count_request()
                    return content.strip()
                logger.warning(f"AI empty/short response from {model}")
                return None

        except httpx.TimeoutException:
            logger.error(f"AI timeout on {model}")
            return None
        except Exception as e:
            logger.error(f"AI exception on {model}: {e}")
            return None

    return None


async def ask_ai(system_prompt: str, user_text: str, model: str) -> str | None:
    """Send prompt to AI. If the given model fails, try next models in rotation.
    
    Args:
        model: The primary model to try first.
    
    Returns response text or None if all models failed.
    """
    from config import get

    models = get("models") or [model]
    if not models:
        models = [model]

    # Find the index of the current model
    try:
        start_idx = models.index(model)
    except ValueError:
        start_idx = 0

    # Try all models starting from the designated one
    for i in range(len(models)):
        idx = (start_idx + i) % len(models)
        current_model = models[idx]
        short = current_model.split("/")[-1]

        if i > 0:
            logger.info(f"🔄 Fallback to model {i+1}/{len(models)}: {short}")

        response = await _try_model(current_model, system_prompt, user_text)
        if response:
            if i > 0:
                logger.info(f"✅ Fallback model {short} responded")
            return response

        logger.warning(f"❌ Model {short} failed, trying next...")

    logger.error(f"All {len(models)} models failed!")
    return None
