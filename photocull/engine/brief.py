"""The photographer's brief for a shoot: written in chat, confirmed by them, then applied to the cull.

Stored per shoot in the `meta` table under "brief". `draft` is what the chat has written so far;
`applied` only changes when the photographer confirms, and is what reviews actually use.
"""
import hashlib
import json

STANDARD = {"shoot": "", "priorities": [], "avoid": [], "look": "", "shots": [], "target_count": None,
            "per_moment": "one", "technical_tolerance": "standard"}
RERUNS = ("final_edit", "full_review")
MAX_ITEMS = 12
MAX_ITEM_CHARS = 200
MAX_SHOT_CHARS = 80   # a shot is a short phrase: "Cake cutting", "Whole family together"
MAX_MESSAGE_CHARS = 2000
MAX_TARGET = 100  # the final edit ranks at most this many frames (review.MAX_FINALISTS)


def _new_state():
    return {"messages": [], "draft": dict(STANDARD), "version": 1, "rerun": "full_review",
            "suggestions": [], "spent": 0.0, "applied": None}


def load(store):
    state = dict(_new_state(), **store.get_meta("brief", {}))
    state["draft"] = dict(STANDARD, **state["draft"])  # briefs saved before a field existed
    return state


def save(store, state):
    store.set_meta("brief", state)


def clean_list(items, chars=MAX_ITEM_CHARS):
    out = []
    for item in items or []:
        item = " ".join(str(item).split())[:chars]
        if item and item.lower() not in [o.lower() for o in out]:
            out.append(item)
    return out[:MAX_ITEMS]


def normalize(data):
    """A complete, safe brief from whatever the chat returned."""
    target = data.get("target_count")
    return {
        "shoot": str(data.get("shoot") or "").strip()[:MAX_ITEM_CHARS],
        "priorities": clean_list(data.get("priorities")),
        "avoid": clean_list(data.get("avoid")),
        "look": str(data.get("look") or "").strip()[:MAX_ITEM_CHARS],
        "shots": clean_list(data.get("shots"), MAX_SHOT_CHARS),
        "target_count": min(target, MAX_TARGET) if isinstance(target, int) and target > 0 else None,
        "per_moment": "few" if data.get("per_moment") == "few" else "one",
        "technical_tolerance": "loose" if data.get("technical_tolerance") == "loose" else "standard",
    }


def as_json(brief):
    return json.dumps(brief, ensure_ascii=False)


def _numbered(shots):
    return "\n".join(f"{i}. {s}" for i, s in enumerate(shots, 1))


def prompt_text(brief, style, shots=None):
    """The brief as sent with each review; empty when there's nothing personal to say.

    shots: the shot list the reviews tag frames against (defaults to the brief's own).
    """
    shots = brief.get("shots", []) if shots is None else shots
    lines = []
    if brief["shoot"]:
        lines.append(f"This shoot: {brief['shoot']}")
    if brief["priorities"]:
        lines.append("Keep above all: " + "; ".join(brief["priorities"]))
    if brief["avoid"]:
        lines.append("They don't want: " + "; ".join(brief["avoid"]))
    if brief["look"]:
        lines.append(f"The look they want: {brief['look']}")
    if style:
        lines.append("Their lasting style: " + "; ".join(style))
    text = "The photographer's brief for this shoot:\n" + "\n".join(lines) if lines else ""
    if shots:
        text = (text + "\n\n" if text else "") + (
            "Their shot list, the photos they must come away with:\n" + _numbered(shots)
            + "\nIn shots, give the numbers of the listed shots each frame clearly shows. Only tag a frame "
              "that really is that shot, not one that is merely nearby or related.")
    return text


def _edit_text(brief, style):
    text = prompt_text(brief, style, shots=[])
    if brief.get("shots"):
        text = (text + "\n\n" if text else "") + (
            "Their shot list, the photos they must come away with: " + "; ".join(brief["shots"])
            + ".\nFrames that show one are labelled with it. Rank the best frame of each listed shot "
              "inside your keep_count, even when it is weaker than the frames around it.")
    if brief["per_moment"] == "few":
        text = (text + "\n\n" if text else "") + ("They want several frames per moment when each adds "
                                                  "something, so frames from one moment can all stay.")
    if text and brief["priorities"]:
        text += "\nIn unmet, list any priority above that none of these frames satisfies; otherwise leave it empty."
    return text


def _key(text):
    return hashlib.sha1(text.encode()).hexdigest()[:12] if text else ""


def applied(state):
    """The brief as it applies to the cull. A shoot with none behaves exactly like the standard cull."""
    if state and state["applied"]:
        return state["applied"]
    return {"brief": dict(STANDARD), "style": [], "review_prompt": "", "review_key": "", "review_shots": [],
            "edit_prompt": "", "edit_key": "", "tolerance": "standard", "per_moment": "one"}


def plan(state, saved_style, has_reviews):
    """What `applied` becomes if the draft is confirmed.

    Saved style is fixed at a shoot's first confirmation, so saving a style later doesn't make
    a corrected shoot pay for its reviews again. A correction Claude marked as a re-rank leaves
    the existing reviews valid, and only the final edit reruns.
    """
    current = applied(state)
    draft = state["draft"]
    style = current["style"] if state["applied"] else list(saved_style)
    review_prompt = prompt_text(draft, style)
    # Reviews tag frames against the shot list they were sent, so a new shot means looking again.
    # Dropping a shot doesn't: the tags for the ones that stay are still good.
    tagged = [s.lower() for s in current.get("review_shots", [])]
    keep_reviews = has_reviews and state["rerun"] == "final_edit" and all(s.lower() in tagged for s in draft["shots"])
    edit_prompt = _edit_text(draft, style)
    return {
        "brief": draft, "style": style,
        "review_prompt": current["review_prompt"] if keep_reviews else review_prompt,
        "review_key": current["review_key"] if keep_reviews else _key(review_prompt),
        "review_shots": current.get("review_shots", []) if keep_reviews else list(draft["shots"]),
        "edit_prompt": edit_prompt, "edit_key": _key(edit_prompt),
        "tolerance": draft["technical_tolerance"], "per_moment": draft["per_moment"],
    }
