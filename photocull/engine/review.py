"""Aesthetic review of one moment at a time with the Claude API.

Each request carries every candidate frame of a moment, so Claude compares variants
side by side and names the best one, and also rates how good each frame could become
with editing (Hidden gems).
"""
import base64
import json
import os

import anthropic

RUBRIC_VERSION = 1
DEFAULT_MODEL = "claude-sonnet-5-5"
MODELS = {
    # id: (label, $ per 1M input tokens, $ per 1M output tokens)
    "claude-sonnet-5-5": ("Claude Sonnet 5.5", 2.00, 10.00),
    "claude-opus-5-5": ("Claude Opus 5.5", 4.00, 20.00),
}
FIXABLE_FLAWS = ["underexposed", "overexposed", "white_balance", "color_cast", "tilt",
                 "crop", "noise", "flat_contrast", "distracting_edge"]

_RUBRIC = open(os.path.join(os.path.dirname(__file__), "rubric.md"), encoding="utf-8").read()

SYSTEM_PROMPT = f"""You are the photo editor for a working photographer. Their shoot has already
been culled for technical failures (missed focus, blinks, blown exposure) and near-identical
burst frames. You review one "moment" at a time: a set of frames of the same subject or scene,
shot within a minute or so.

For each frame give:
- score: 1-10, how good the photo is as shot, judged by the rubric below.
- reason: one short, concrete phrase in photographer language (12 words or fewer).
- reject: true only for a fatal flaw the technical pass missed (eyes closed, subject blocked,
  unfixable crop, mid-blink in a group shot).
- edit_potential: 1-10, how strong the photo could be after a normal Lightroom/phone edit
  (exposure, white balance, straighten, crop, contrast, noise reduction). Never lower than score.
  A frame with great moment and composition but dark, crooked or flat earns a high edit_potential.
- fixable_flaws: which of the listed flaws editing would fix (empty if none).
- edit_recipe: if edit_potential is at least 2 above score, a short concrete recipe such as
  "Lift shadows +1, warm WB +300K, straighten 2 degrees, crop to 4:5"; otherwise an empty string.

Then give best_ids: the frame the photographer should keep from this moment. A moment almost
always has exactly one. Only if the frames clearly show different subjects (the grouping can
be wrong) list the best frame of each subject, at most 3. Never list two variants of the same
subject. Leave best_ids empty if no frame scores 6 or more.

{_RUBRIC}"""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "photos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "score": {"type": "number"},
                    "reason": {"type": "string"},
                    "reject": {"type": "boolean"},
                    "edit_potential": {"type": "number"},
                    "fixable_flaws": {"type": "array", "items": {"type": "string", "enum": FIXABLE_FLAWS}},
                    "edit_recipe": {"type": "string"},
                },
                "required": ["id", "score", "reason", "reject", "edit_potential", "fixable_flaws", "edit_recipe"],
                "additionalProperties": False,
            },
        },
        "best_ids": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["photos", "best_ids"],
    "additionalProperties": False,
}


class ReviewError(Exception):
    pass


def image_tokens(width, height):
    """Approximate vision tokens for an image at its sent size."""
    return width * height / 750


def estimate_cost(moment_batches, model, thumb_dims):
    """Rough cost in dollars for reviewing the given batches (lists of photo dicts)."""
    _, price_in, price_out = MODELS[model]
    tokens_in = tokens_out = 0
    for batch in moment_batches:
        tokens_in += 300 + sum(image_tokens(*thumb_dims(p)) + 60 for p in batch)
        tokens_out += 1200 + 90 * len(batch)  # thinking + JSON
    system_tokens = len(SYSTEM_PROMPT) / 3.5
    tokens_in += system_tokens * 1.25 + system_tokens * 0.1 * max(len(moment_batches) - 1, 0)
    return tokens_in * price_in / 1e6 + tokens_out * price_out / 1e6


def validate_key(api_key, model=DEFAULT_MODEL):
    """Raise ReviewError with a readable message if the key can't use the model."""
    try:
        anthropic.Anthropic(api_key=api_key, max_retries=1).models.retrieve(model)
    except anthropic.AuthenticationError:
        raise ReviewError("That API key was rejected. Check it in the Claude Console.")
    except anthropic.PermissionDeniedError:
        raise ReviewError(f"This key can't use {MODELS.get(model, (model,))[0]}.")
    except anthropic.NotFoundError:
        raise ReviewError(f"Model {model} isn't available to this key.")
    except anthropic.APIConnectionError:
        raise ReviewError("Couldn't reach the Claude API. Check your internet connection.")


class ClaudeReviewer:
    def __init__(self, api_key, model=DEFAULT_MODEL, effort="medium"):
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=4, timeout=300.0)
        self.model = model
        self.effort = effort

    def review(self, photos, read_thumb, taste_notes=""):
        """Review one moment batch. photos: dicts with id/file/notes; read_thumb(p) -> JPEG bytes.

        Returns (result dict keyed by photo id, best_ids, usage dict).
        """
        content = []
        for p in photos:
            meta = [f"Photo id={p['id']}"]
            if p.get("notes"):
                meta.append("technical notes: " + "; ".join(p["notes"]))
            content.append({"type": "text", "text": ", ".join(meta)})
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg",
                "data": base64.standard_b64encode(read_thumb(p)).decode("ascii")}})
        ask = f"Review this moment ({len(photos)} frames)."
        if taste_notes:
            ask += "\n\nWhat this photographer has told you through past picks:\n" + taste_notes
        content.append({"type": "text", "text": ask})

        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": content}],
                output_config={"effort": self.effort,
                               "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError:
            raise ReviewError("The API key was rejected.")
        except anthropic.RateLimitError:
            raise ReviewError("Rate limited by the Claude API even after retries. Try again in a minute.")
        except anthropic.APIStatusError as e:
            raise ReviewError(f"Claude API error {e.status_code}: {e.message}")
        except anthropic.APIConnectionError:
            raise ReviewError("Lost connection to the Claude API.")

        if response.stop_reason == "refusal":
            raise ReviewError("Claude declined to review this moment.")
        if response.stop_reason == "max_tokens":
            raise ReviewError("The review was cut off (too many frames in one batch).")
        text = next(b.text for b in response.content if b.type == "text")
        data = json.loads(text)
        known = {p["id"] for p in photos}
        results = {}
        for r in data["photos"]:
            if r["id"] in known:
                r["edit_potential"] = max(r["edit_potential"], r["score"])
                results[r["id"]] = r
        u = response.usage
        usage = {
            "input": u.input_tokens,
            "cache_write": u.cache_creation_input_tokens or 0,
            "cache_read": u.cache_read_input_tokens or 0,
            "output": u.output_tokens,
        }
        return results, [i for i in data["best_ids"] if i in known], usage


def usage_cost(usage, model):
    if model not in MODELS:
        return 0.0
    _, price_in, price_out = MODELS[model]
    return (usage["input"] * price_in + usage["cache_write"] * price_in * 1.25
            + usage["cache_read"] * price_in * 0.1 + usage["output"] * price_out) / 1e6
