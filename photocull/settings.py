"""App-level settings and the API key (kept in the macOS Keychain, never on disk)."""
import json
import os

import keyring
from keyring.errors import KeyringError

from .engine.review import DEFAULT_MODEL, MODELS

KEYCHAIN_SERVICE = "PhotoCull"
KEYCHAIN_USER = "anthropic-api-key"
DEFAULTS = {"model": DEFAULT_MODEL, "effort": "medium", "recent": []}


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
    with open(_path(), "w") as f:
        json.dump(s, f)
    return s


def remember_folder(folder):
    recent = [folder] + [f for f in load()["recent"] if f != folder]
    return save(recent=recent[:8])


def get_api_key():
    """Keychain first; ANTHROPIC_API_KEY as a fallback for development and the CLI."""
    try:
        key = keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_USER)
    except KeyringError:
        key = None
    return key or os.environ.get("ANTHROPIC_API_KEY")


def set_api_key(key):
    if key:
        keyring.set_password(KEYCHAIN_SERVICE, KEYCHAIN_USER, key)
    else:
        try:
            keyring.delete_password(KEYCHAIN_SERVICE, KEYCHAIN_USER)
        except KeyringError:
            pass
