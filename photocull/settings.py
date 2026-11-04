"""App-level settings and the API key (kept in the macOS Keychain, never on disk)."""
import json
import os

import keyring
from keyring.errors import KeyringError

from .engine.review import DEFAULT_MODEL, MODELS, SCREEN_MODEL

KEYCHAIN_SERVICE = "PhotoCull"
KEYCHAIN_USER = "anthropic-api-key"
THEME_BACKGROUNDS = {"dark": "#171512", "light": "#f6f0e7"}  # shown by the native window before the page paints
DEFAULTS = {"model": DEFAULT_MODEL, "effort": "medium", "recent": [], "theme": "dark",
            "two_tier": True}  # screen with an inexpensive model; the chosen model takes close calls


def screen_model(s):
    """The first-pass model for these settings, or None for a single-model review."""
    return SCREEN_MODEL if s["two_tier"] and s["model"] != SCREEN_MODEL else None


def _path():
    base = os.environ.get("PHOTOCULL_HOME") or os.path.expanduser("~/Library/Application Support/PhotoCull")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "settings.json")


def load():
    try:
        with open(_path()) as f:
            return dict(DEFAULTS, **json.load(f))
    except (OSError, ValueError):
        return dict(DEFAULTS)


def save(**changes):
    s = load()
    s.update({k: v for k, v in changes.items() if k in DEFAULTS})
    if s["model"] not in MODELS:
        s["model"] = DEFAULT_MODEL
    if s["theme"] not in THEME_BACKGROUNDS:
        s["theme"] = DEFAULTS["theme"]
    with open(_path(), "w") as f:
        json.dump(s, f)
    return s


def remember_folder(folder):
    recent = [folder] + [f for f in load()["recent"] if f != folder]
    return save(recent=recent[:8])


_cached_key = None  # each Keychain read can prompt for a password, so a key found once is kept until quit


def get_api_key():
    """Keychain first; ANTHROPIC_API_KEY as a fallback for development and the CLI."""
    global _cached_key
    if _cached_key:
        return _cached_key
    try:
        _cached_key = keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_USER)
    except KeyringError:
        pass
    return _cached_key or os.environ.get("ANTHROPIC_API_KEY")


def set_api_key(key):
    global _cached_key
    _cached_key = key or None
    if key:
        keyring.set_password(KEYCHAIN_SERVICE, KEYCHAIN_USER, key)
    else:
        try:
            keyring.delete_password(KEYCHAIN_SERVICE, KEYCHAIN_USER)
        except KeyringError:
            pass
