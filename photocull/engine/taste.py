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


# The profile from first-run setup: what they shoot and how they like an edit. It reaches Claude
# with their saved style, so it shapes every shoot's brief and review.
GENRES = {"weddings": "Weddings", "portraits": "Portraits", "events": "Events", "family": "Family",
          "sports": "Sports", "wildlife": "Wildlife", "street": "Street", "travel": "Travel",
          "landscape": "Landscape", "products": "Products"}
LOOKS = {
    "candid": "Prefers candid moments over posed shots",
    "posed": "Prefers clean posed shots over candids",
    "grain_ok": "Grain and motion blur are fine when the moment is strong",
    "sharp": "Sharpness matters: soft frames rarely make the cut",
    "moody": "Likes moody, darker light",
    "airy": "Likes bright, airy light",
    "emotion": "Expression and emotion come before composition",
    "composition": "Composition and light come before expression",
}
KEEP = {"tight": "Wants a tight edit: only the strongest frames",
        "balanced": "",
        "generous": "Wants a generous edit: keep strong alternates too"}
PROFILE = {"name": "", "genres": [], "look": [], "keep": "balanced", "done": False}


def load_profile():
    try:
        with open(os.path.join(_home(), "profile.json")) as f:
            return dict(PROFILE, **json.load(f))
    except (OSError, ValueError):
        return dict(PROFILE)


def save_profile(data):
    profile = {
        "name": str(data.get("name") or "").strip()[:40],
        "genres": [g for g in GENRES if g in (data.get("genres") or [])],
        "look": [k for k in LOOKS if k in (data.get("look") or [])],
        "keep": data.get("keep") if data.get("keep") in KEEP else "balanced",
        "done": True,
    }
    with open(os.path.join(_home(), "profile.json"), "w") as f:
        json.dump(profile, f)
    return profile


def profile_lines(profile):
    lines = []
    if profile["genres"]:
        lines.append("Mostly shoots " + ", ".join(GENRES[g].lower() for g in profile["genres"]))
    lines += [LOOKS[k] for k in profile["look"]]
    if KEEP[profile["keep"]]:
        lines.append(KEEP[profile["keep"]])
    return lines


def lasting():
    """Everything Claude should know about the photographer beyond this shoot."""
    return profile_lines(load_profile()) + load_style()
