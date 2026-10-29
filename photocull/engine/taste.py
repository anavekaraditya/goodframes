"""App-wide memory of the photographer's overrides, fed back into review prompts."""
import json
import os
import time

MAX_EXAMPLES = 10


def _path():
    base = os.environ.get("PHOTOCULL_HOME") or os.path.expanduser("~/Library/Application Support/PhotoCull")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "taste.json")


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


_VERB = {"pick": "picked a frame you scored", "unpick": "rejected a frame you picked and scored",
         "gem": "wanted as a Hidden gem a frame you scored", "ungem": "dismissed a gem you scored"}


def prompt_notes():
    lines = []
    for it in load()[-MAX_EXAMPLES:]:
        lines.append(f"- They {_VERB[it['action']]} {it['score']}: \"{it['reason']}\"")
    return "\n".join(lines)
