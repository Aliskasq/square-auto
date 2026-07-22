"""Configuration."""
import os
import json
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
SQUARE_API_KEY = os.getenv("SQUARE_API_KEY", "")

# Source groups
SOURCE_GROUP_ID = int(os.getenv("SOURCE_GROUP_ID", "0"))    # Primary group
SOURCE_GROUP_2_ID = int(os.getenv("SOURCE_GROUP_2_ID", "0"))  # Secondary group

# OpenRouter keys (up to 5)
OPENROUTER_KEYS = [k for k in [
    os.getenv("OPENROUTER_API_KEY", ""),
    os.getenv("OPENROUTER_API_KEY_2", ""),
    os.getenv("OPENROUTER_API_KEY_3", ""),
    os.getenv("OPENROUTER_API_KEY_4", ""),
    os.getenv("OPENROUTER_API_KEY_5", ""),
] if k]

REQUESTS_PER_KEY = 48

# Settings file
SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "data", "settings.json")

_defaults = {
    "models": ["google/gemma-3n-e4b-it:free"],
    "pause_minutes": 6,
    "sleep_start": "01:00",  # MSK
    "sleep_end": "05:00",    # MSK
    "posts_per_hour": 5,
    "posts_per_day": 100,
    "dedup_hours": 4,
    "drop_pct": 15,
    "hashtags": "#BinanceSquare #Write2Earn",
}


def _load_settings() -> dict:
    try:
        with open(SETTINGS_FILE) as f:
            saved = json.load(f)
            for k, v in _defaults.items():
                if k not in saved:
                    saved[k] = v
            return saved
    except Exception:
        return dict(_defaults)


def _save_settings(s: dict):
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, "w") as f:
        json.dump(s, f, indent=2, ensure_ascii=False)


_settings = _load_settings()


def get(key: str):
    return _settings.get(key, _defaults.get(key))


def set_val(key: str, value):
    _settings[key] = value
    _save_settings(_settings)


def get_settings() -> dict:
    return dict(_settings)


# Key rotation
_key_state = {"idx": 0, "count": 0}


def get_api_key() -> str:
    if not OPENROUTER_KEYS:
        return ""
    if _key_state["count"] >= REQUESTS_PER_KEY:
        _key_state["idx"] = (_key_state["idx"] + 1) % len(OPENROUTER_KEYS)
        _key_state["count"] = 0
    return OPENROUTER_KEYS[_key_state["idx"] % len(OPENROUTER_KEYS)]


def count_request():
    _key_state["count"] += 1


def force_rotate_key():
    if len(OPENROUTER_KEYS) <= 1:
        return False
    _key_state["idx"] = (_key_state["idx"] + 1) % len(OPENROUTER_KEYS)
    _key_state["count"] = 0
    return True


def get_current_model(post_number: int) -> str:
    """Rotate models based on post number."""
    models = _settings.get("models", ["google/gemma-3n-e4b-it:free"])
    if not models:
        return "google/gemma-3n-e4b-it:free"
    return models[post_number % len(models)]
