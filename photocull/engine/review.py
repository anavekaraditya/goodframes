"""Aesthetic review of one moment at a time with the Claude API.

Each request carries every candidate frame of a moment, so Claude compares variants
side by side and names the best one, and also rates how good each frame could become
with editing (Hidden gems).
"""
import base64
import json
import os

import anthropic

RUBRIC_VERSION = 2
DEFAULT_MODEL = "claude-sonnet-5-5"
MODELS = {
    # id: (label, $ per 1M input tokens, $ per 1M output tokens)
    "claude-sonnet-5-5": ("Claude Sonnet 5.5", 2.00, 10.00),
    "claude-opus-5-5": ("Claude Opus 5.5", 4.00, 20.00),
    "claude-haiku-5-5": ("Claude Haiku 5.5", 0.10, 0.50),
}
SCREEN_MODEL = "claude-haiku-5-5"  # first pass of a two-tier review; close calls go to the main model
NO_FALLBACK = {"claude-haiku-5-5"}  # models the API offers no server-side refusal fallback for
FIXABLE_FLAWS = ["underexposed", "overexposed", "white_balance", "color_cast", "tilt",
                 "crop", "noise", "flat_contrast", "distracting_edge"]

_RUBRIC = open(os.path.join(os.path.dirname(__file__), "rubric.md"), encoding="utf-8").read()

SHOOT_TYPES = ["people", "event", "street", "travel", "landscape", "architecture", "other"]
MAX_FINALISTS = 100      # frames compared in one final edit request
FACE_CROP_EDGE = 512     # longest edge of a focus-check close-up, and of a final edit preview

SYSTEM_PROMPT = f"""You are the photo editor for a working photographer, choosing what they should
keep. Only unusable frames have been removed so far, so expect several versions of the same
moment and choose between them. You review one "moment" at a time: a set of frames of the same
subject or scene, shot within a minute or so. Some frames come with a full-resolution close-up of
the main face, for checking whether the eyes are really in focus.

For each frame give:
- shoot_type, impact, moment, light, composition, subject_focus: as described in the rubric below.
- score: 1-10, how good the photo is as shot, judged by the rubric below.
- reason: one short, concrete phrase in photographer language (12 words or fewer).
- reject: true only for a flaw that makes the frame unusable and no edit can fix.
- edit_potential: 1-10, how strong the photo could be after a normal Lightroom/phone edit
  (exposure, white balance, straighten, crop, contrast, noise reduction). Never lower than score.
  A frame with great moment and composition but dark, crooked or flat earns a high edit_potential.
- fixable_flaws: which of the listed flaws editing would fix (empty if none).
- edit_recipe: if edit_potential is at least 2 above score, a short concrete recipe such as
  "Lift shadows +1, warm WB +300K, straighten 2 degrees, crop to 4:5"; otherwise an empty string.

Then give best_ids: the frames the photographer should keep, exactly one per distinct subject
or moment. The grouping is generous, so a set can hold a few different subjects shot back to
back (a portrait, then the cake, then a skyline): list the best frame of each, at most 4.
Never list two variants of the same subject, angle or scene - pick the stronger one. Skip any
subject whose best frame scores below 6.

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
                    "shoot_type": {"type": "string", "enum": SHOOT_TYPES},
                    "impact": {"type": "number"},
                    "moment": {"type": "number"},
                    "light": {"type": "number"},
                    "composition": {"type": "number"},
                    "subject_focus": {"type": "number"},
                    "score": {"type": "number"},
                    "reason": {"type": "string"},
                    "reject": {"type": "boolean"},
                    "edit_potential": {"type": "number"},
                    "fixable_flaws": {"type": "array", "items": {"type": "string", "enum": FIXABLE_FLAWS}},
                    "edit_recipe": {"type": "string"},
                },
                "required": ["id", "shoot_type", "impact", "moment", "light", "composition", "subject_focus",
                             "score", "reason", "reject", "edit_potential", "fixable_flaws", "edit_recipe"],
                "additionalProperties": False,
            },
        },
        "best_ids": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["photos", "best_ids"],
    "additionalProperties": False,
}

FINAL_EDIT_PROMPT = """You are a photo editor making the final edit of a shoot. These are the best
frames of each moment, in the order they were shot. Scores were given one moment at a time, so
compare the frames directly now.

Rank every frame from strongest to weakest, as the set you would show a client:
- Remove repeats: when two frames from different moments do the same job, rank the weaker well down.
- Keep what the shoot needs: variety of subject and scale, and the frames that tell its story.
  A quieter frame the set needs can outrank a stronger repeat.
- Be selective. Keeping everything is a failure of editing, but don't cut for the sake of it:
  a shoot where everything is distinct and strong can keep a lot.

Give every frame a reason: why it earns its place, or for frames you would cut, why it goes (12
words or fewer, for example "Repeats the stronger harbour view at #41"). Then give keep_count: how
many frames from the top of your ranking belong in the final edit. Leave unmet empty unless the
request asks you to list something there."""

FINAL_EDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "ranking": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "reason": {"type": "string"}},
                "required": ["id", "reason"],
                "additionalProperties": False,
            },
        },
        "keep_count": {"type": "integer"},
        "unmet": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["ranking", "keep_count", "unmet"],
    "additionalProperties": False,
}


class ReviewError(Exception):
    pass


def image_tokens(width, height):
    """Approximate vision tokens for an image at its sent size."""
    return width * height / 750


def estimate_cost(moment_batches, model, thumb_dims, final_frames=0):
    """Rough cost in dollars for reviewing the given batches (lists of photo dicts).

    final_frames: how many frames the whole-shoot final edit is expected to compare.
    """
    _, price_in, price_out = MODELS[model]
    tokens_in = tokens_out = 0
    crop_tokens = image_tokens(FACE_CROP_EDGE, FACE_CROP_EDGE) + 30
    for batch in moment_batches:
        tokens_in += 300 + sum(image_tokens(*thumb_dims(p)) + 60 for p in batch)
        tokens_in += crop_tokens * sum(bool(p.get("metrics", {}).get("faces")) for p in batch)
        tokens_out += 1200 + 140 * len(batch)  # thinking + JSON
    if moment_batches:
        system_tokens = len(SYSTEM_PROMPT) / 3.5
        tokens_in += system_tokens * 1.25 + system_tokens * 0.1 * (len(moment_batches) - 1)
    if final_frames:
        tokens_in += len(FINAL_EDIT_PROMPT) / 3.5 + final_frames * (image_tokens(FACE_CROP_EDGE, FACE_CROP_EDGE * 2 / 3) + 20)
        tokens_out += 1000 + 40 * final_frames
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


MAX_CHAT_MESSAGES = 30   # most recent chat messages sent with each turn

BRIEF_CHAT_PROMPT = """You are the photo editor for a working photographer. Before culling a shoot
you talk with them and write a brief: what this shoot is and what they personally want kept.
Different photographers want different things from the same frames, which is why you ask.

How to talk:
- Be short and plain. Ask one question at a time, specific to what you can see in the sample.
- Two or three questions are usually enough. Stop asking once you can write a useful brief, and
  say so; they may always add more.
- Only record what they said or clearly agreed to. Don't guess names or priorities from the photos.
- You can't start the cull. They start it with the Confirm button beside the chat, which also shows
  the cost. Never say the cull has started or finished.

Every turn, return the complete brief as it stands now:
- shoot: what this shoot is, in a few words.
- priorities: people, moments or things to keep above all.
- avoid: what they don't want.
- look: the style they want, including flaws that are fine for them (grain, motion blur, dark frames).
- target_count: how many final photos they asked for, or 0 if they didn't say.
- per_moment: "one" for the single best frame of each moment, "few" if they want several variations.
- technical_tolerance: "loose" only if they worry about good frames being thrown out for softness,
  blur, blinks or exposure; otherwise "standard". Loose costs more, so mention that.
- rerun: "final_edit" if the frames' existing reviews are enough and they only need ranking again
  (keep more or fewer of something, or change the balance). "full_review" if frames must be judged
  again against new criteria (a different look, or what counts as a flaw).
- style_suggestions: preferences they stated that would apply to all their shoots, such as "Likes
  grain and motion blur". Never include names or details of this shoot. Leave empty if unsure."""

BRIEF_CHAT_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "shoot": {"type": "string"},
        "priorities": {"type": "array", "items": {"type": "string"}},
        "avoid": {"type": "array", "items": {"type": "string"}},
        "look": {"type": "string"},
        "target_count": {"type": "integer"},
        "per_moment": {"type": "string", "enum": ["one", "few"]},
        "technical_tolerance": {"type": "string", "enum": ["standard", "loose"]},
        "rerun": {"type": "string", "enum": ["final_edit", "full_review"]},
        "style_suggestions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["reply", "shoot", "priorities", "avoid", "look", "target_count", "per_moment",
                 "technical_tolerance", "rerun", "style_suggestions"],
    "additionalProperties": False,
}


class ClaudeReviewer:
    def __init__(self, api_key, model=DEFAULT_MODEL, effort="medium"):
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=4, timeout=300.0)
        self.model = model
        self.effort = effort

    def _call(self, system, messages, schema, max_tokens=16000, effort=None):
        """One structured request. Returns (parsed JSON, usage dict)."""
        fallback = {} if self.model in NO_FALLBACK else {
            "betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=messages,
                output_config={"effort": effort or self.effort,
                               "format": {"type": "json_schema", "schema": schema}},
                **fallback,
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
        u = response.usage
        return json.loads(text), {
            "input": u.input_tokens,
            "cache_write": u.cache_creation_input_tokens or 0,
            "cache_read": u.cache_read_input_tokens or 0,
            "output": u.output_tokens,
        }

    def review(self, photos, read_thumb, taste_notes="", final_round=False, read_crop=None, brief_notes=""):
        """Review one moment batch. photos: dicts with id/file/notes; read_thumb(p) -> JPEG bytes.

        final_round: the photos are the winners of several batches of one long moment; Claude
        re-scores them side by side and keeps one per distinct subject.
        read_crop(p) -> JPEG bytes of a close-up of the main face, or None.
        Returns (result dict keyed by photo id, best_ids, usage dict).
        """
        content = []
        for p in photos:
            meta = [f"Photo id={p['id']}"]
            if p.get("notes"):
                meta.append("technical notes: " + "; ".join(p["notes"]))
            content.append({"type": "text", "text": ", ".join(meta)})
            content.append(_image(read_thumb(p)))
            crop = read_crop(p) if read_crop else None
            if crop:
                content.append({"type": "text", "text": f"Full-resolution close-up of the main face in photo id={p['id']}:"})
                content.append(_image(crop))
        ask = f"Review this moment ({len(photos)} frames)."
        if final_round:
            ask = (f"These {len(photos)} frames are the winners of separate batches of one long "
                   "sequence. Compare them side by side: where two show the same subject, keep only "
                   "the stronger one in best_ids.")
        if taste_notes:
            ask += "\n\nWhat this photographer has told you through past picks:\n" + taste_notes
        if brief_notes:
            ask += "\n\n" + brief_notes
        content.append({"type": "text", "text": ask})

        data, usage = self._call(SYSTEM_PROMPT, [{"role": "user", "content": content}], OUTPUT_SCHEMA)
        known = {p["id"] for p in photos}
        results = {}
        for r in data["photos"]:
            if r["id"] in known:
                r["edit_potential"] = max(r["edit_potential"], r["score"])
                results[r["id"]] = r
        return results, [i for i in data["best_ids"] if i in known], usage

    def final_edit(self, photos, read_preview, taste_notes="", brief_notes=""):
        """Compare the frames of the whole shoot. Returns ([(id, reason)] best first, keep_count, unmet, usage).

        unmet: priorities in the brief that no frame here satisfies.
        """
        content = []
        for p in photos:
            content.append({"type": "text", "text": f"Photo id={p['id']}, moment {p.get('moment')}, score {p['final']}"})
            content.append(_image(read_preview(p)))
        ask = f"Make the final edit of these {len(photos)} frames."
        if taste_notes:
            ask += "\n\nWhat this photographer has told you through past picks:\n" + taste_notes
        if brief_notes:
            ask += "\n\n" + brief_notes
        content.append({"type": "text", "text": ask})
        data, usage = self._call(FINAL_EDIT_PROMPT, [{"role": "user", "content": content}], FINAL_EDIT_SCHEMA)
        return [(r["id"], r["reason"]) for r in data["ranking"]], data["keep_count"], data["unmet"], usage

    def brief_chat(self, messages, sample, context):
        """One turn of the chat that writes the brief. Returns (parsed BRIEF_CHAT_SCHEMA reply, usage).

        messages: [{"role", "text"}]; sample: JPEG bytes of a few frames, sent at the start and
        cached so later turns don't pay for them again; context: the current state, as text.
        """
        opening = []
        for jpeg in sample:
            opening.append(_image(jpeg))
        if opening:
            opening[-1]["cache_control"] = {"type": "ephemeral"}
        opening.append({"type": "text", "text": f"These {len(sample)} frames are a sample from across the shoot."})
        turns = [{"role": "user", "content": opening}]
        for m in messages[-MAX_CHAT_MESSAGES:]:
            turns.append({"role": m["role"], "content": m["text"]})
        if turns[-1]["role"] == "user":
            turns[-1] = {"role": "user", "content": f"{turns[-1]['content']}\n\n{context}"}
        else:  # the opening turn
            turns.append({"role": "user", "content": f"Start the conversation.\n\n{context}"})
        return self._call(BRIEF_CHAT_PROMPT, turns, BRIEF_CHAT_SCHEMA, max_tokens=3000, effort="low")


def _image(jpeg):
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.standard_b64encode(jpeg).decode("ascii")}}


def usage_cost(usage, model):
    if model not in MODELS:
        return 0.0
    _, price_in, price_out = MODELS[model]
    return (usage["input"] * price_in + usage["cache_write"] * price_in * 1.25
            + usage["cache_read"] * price_in * 0.1 + usage["output"] * price_out) / 1e6
