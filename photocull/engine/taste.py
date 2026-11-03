"""App-wide memory of the photographer's overrides, fed back into review prompts."""
import json
import os
import time

MAX_EXAMPLES = 10


def _home():
    base = os.environ.get("PHOTOCULL_HOME") or os.path.expanduser("~/Library/Application Support/PhotoCull")
    os.makedirs(base, exist_ok=True)
    return base


def _path():
    return os.path.join(_home(), "taste.json")


def load():
    try:
        with open(_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def record(action, review, notes):
    """Remember why a frame was overridden; only frames Claude has described are useful."""
    if not review:
        return
    items = load()
    items.append({"action": action, "score": review.get("score"), "reason": review.get("reason"),
                  "notes": notes[:3], "at": time.time()})
    with open(_path(), "w") as f:
        json.dump(items[-200:], f)


def reset():
    with open(_path(), "w") as f:
        json.dump([], f)


def load_style():
    """Lasting preferences the photographer approved in a brief; they apply to future shoots."""
    try:
        with open(os.path.join(_home(), "style.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def save_style(items):
    with open(os.path.join(_home(), "style.json"), "w") as f:
        json.dump(items, f)


def add_style(items):
    style = load_style()
    save_style(style + [i for i in items if i not in style])


_VERB = {"pick": "picked a frame you scored", "unpick": "rejected a frame you picked and scored",
         "gem": "wanted as a Hidden gem a frame you scored", "ungem": "dismissed a gem you scored"}


def prompt_notes():
    lines = []
    for it in load()[-MAX_EXAMPLES:]:
        lines.append(f"- They {_VERB[it['action']]} {it['score']}: \"{it['reason']}\"")
    return "\n".join(lines)
