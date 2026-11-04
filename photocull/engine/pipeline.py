"""Orchestrates a cull: analyse -> moments -> review -> decide picks and Hidden gems.

Every stage reports progress through `on_event(dict)` so the UI can animate it.
"""
import copy
import io
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from PIL import Image

from . import brief, grouping, moments, scoring, taste
from .ingest import THUMB_EDGE, ingest, open_photo
from .review import FACE_CROP_EDGE, MAX_FINALISTS, RUBRIC_VERSION, ReviewError, estimate_cost, usage_cost

# A two-tier review screens every moment with an inexpensive model and sends close calls to the
# main one: a moment whose best frame is near the line for the final edit, or whose best frames
# are almost tied, since that is where a wrong call keeps a weak frame or loses a good one.
CLOSE_TO_FLOOR = 1.0         # a best frame within this of FINALIST_FLOOR is a close call
CLOSE_TO_BEST = 0.5          # ...as is a runner-up within this of a contending best frame
CLOSE_CALL_SHARE = 0.4       # share of moments assumed to be close calls when estimating cost
CONTENDERS = 3               # strongest screened frames the main model compares in a close call
MAX_CONTENDERS = 6           # ...plus the screener's best of each subject, up to this many

DEFAULT_SETTINGS = {
    "min_score": 7.0,        # final score a best frame needs to be a pick when there is no final edit
    "selection_size": 0,     # how many ranked frames the final edit keeps; 0 = Claude's recommendation
    "gem_threshold": 8.0,    # edit_potential a frame needs to become a Hidden gem
    "gem_uplift": 1.5,       # ...and how much better than as-shot it must get
}

FINALIST_FLOOR = 6.0         # final score a best frame needs to enter the whole-shoot final edit
FACE_PADDING = 0.35          # share of the face's size added on each side of a focus close-up
BRIEF_SAMPLE = 12            # frames Claude sees during the brief chat


def _noop(event):
    pass


class Shoot:
    def __init__(self, folder, store):
        self.folder = folder
        self.store = store
        self.photos = []      # dicts, sorted by capture time, id = 1..n
        self.groups = []      # near-duplicate groups (photo index lists)
        self.moments = []     # [{"id": k, "members": [photo ids]}]
        self.genre = "auto"
        self.brief = brief.applied(None)  # the confirmed brief, as it applies to reviews
        self.selection = None
        self.unmet = []

    def by_id(self, pid):
        return self.photos[pid - 1]

    def thumb_dims(self, p):
        w, h = p["width"], p["height"]
        s = 1024 / max(w, h)
        return (w * s, h * s) if s < 1 else (w, h)

    def read_thumb(self, p):
        with open(self.store.thumb_path(p["fp"]), "rb") as f:
            return f.read()

    def read_preview(self, p):
        """A small JPEG of the frame for the whole-shoot final edit."""
        with Image.open(self.store.thumb_path(p["fp"])) as im:
            im = im.convert("RGB")
            im.thumbnail((FACE_CROP_EDGE, FACE_CROP_EDGE))
        return _jpeg(im)

    def read_face_crop(self, p):
        """A close-up of the largest face from the original file, or None if there's no face."""
        faces = p["metrics"]["faces"]
        if not faces:
            return None
        x, y, w, h = max(faces, key=lambda f: f["box"][2])["box"]
        try:
            im = open_photo(os.path.join(self.folder, p["file"]))
            s = max(max(im.size) / THUMB_EDGE, 1.0)  # face boxes are in thumbnail pixels
            pad = FACE_PADDING * max(w, h)
            box = (max(0, int((x - pad) * s)), max(0, int((y - pad) * s)),
                   min(im.width, int((x + w + pad) * s)), min(im.height, int((y + h + pad) * s)))
            out = im.crop(box)
            out.thumbnail((FACE_CROP_EDGE, FACE_CROP_EDGE), Image.LANCZOS)
            return _jpeg(out)
        except OSError:  # file moved or unreadable: review from the thumbnail alone
            return None


def _jpeg(im):
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def analyze(folder, store, genre="auto", on_event=_noop):
    """Technical pass. No API calls."""
    shoot = Shoot(folder, store)
    shoot.genre = genre
    shoot.brief = brief.applied(brief.load(store))
    on_event({"type": "stage", "stage": "ingest"})

    def progress(fp, file, i, n, cached=False, error=None):
        on_event({"type": "ingest", "fp": fp, "file": file, "i": i, "n": n, "error": error})

    photos = ingest(folder, store, progress)
    photos.sort(key=lambda p: (p["taken_ts"], p["file"]))
    for i, p in enumerate(photos, 1):
        p["id"] = i
        p["status"] = None
    shoot.photos = photos
    if not photos:
        on_event({"type": "analyzed", "photos": 0})
        return shoot

    on_event({"type": "stage", "stage": "technical"})
    shoot.groups = grouping.group(photos)
    _technical(shoot)
    for p in photos:
        if p["status"] == "rejected":
            on_event({"type": "tech", "id": p["id"], "fp": p["fp"], "reject": p["reject"]})

    on_event({"type": "stage", "stage": "moments"})
    for k, members in enumerate(moments.build_moments(photos, shoot.groups), 1):
        for i in members:
            photos[i]["moment"] = k
        shoot.moments.append({"id": k, "members": [photos[i]["id"] for i in members]})
    on_event({"type": "moments", "moments": shoot.moments})
    decide(shoot)
    return shoot


def _technical(shoot):
    """Score from scratch: select_in_groups writes its rejections into the photos."""
    scoring.score(shoot.photos, shoot.groups, shoot.genre)
    scoring.select_in_groups(shoot.photos, shoot.groups, shoot.brief["tolerance"])


def candidates(shoot, moment):
    """Frames that passed the technical check. A frame Claude rejected can be judged again under a new brief."""
    return [shoot.by_id(i) for i in moment["members"] if not shoot.by_id(i)["reject"]]


def pending_batches(shoot):
    """Review batches for moments that have a candidate without a current review."""
    reviews = shoot.store.reviews()
    out = []
    for m in shoot.moments:
        cands = candidates(shoot, m)
        if not cands:
            continue
        fresh = all(p["fp"] in reviews and reviews[p["fp"]].get("rubric_version") == RUBRIC_VERSION
                    and reviews[p["fp"]].get("brief_key", "") == shoot.brief["review_key"]
                    for p in cands)
        if not fresh:
            out.extend((m["id"], batch) for batch in moments.chunks(cands))
    return out


def review_estimate(shoot, model, screen_model=None):
    """screen_model: the inexpensive first-pass model of a two-tier review, if any."""
    batches = pending_batches(shoot)
    n_moments = len({m for m, _ in batches})
    # Moments about to be reviewed have no current finalists: assume about one frame from each.
    pending = {m for m, _ in batches}
    settled = [p for p in _finalists(shoot) if p.get("moment") not in pending]
    final_frames = n_moments + len(settled) if batches or _final_edit_pending(shoot) else 0
    final_frames = min(final_frames, MAX_FINALISTS)
    moment_batches = [b for _, b in batches]
    final = 1 if final_frames > 1 else 0
    if not screen_model:
        cost = estimate_cost(moment_batches, model, shoot.thumb_dims, final_frames)
        requests = len(batches) + final
    else:
        contenders = [[p for b in bs for p in b][:CONTENDERS + 1] for bs in _by_moment(batches).values()]
        cost = (estimate_cost(moment_batches, screen_model, shoot.thumb_dims)
                + CLOSE_CALL_SHARE * estimate_cost(contenders, model, shoot.thumb_dims)
                + estimate_cost([], model, shoot.thumb_dims, final_frames))
        requests = len(batches) + round(n_moments * CLOSE_CALL_SHARE) + final
    return {"moments": n_moments, "photos": sum(len(b) for _, b in batches), "requests": requests,
            "final_edit": final_frames > 1, "two_tier": bool(screen_model and batches), "cost": round(cost, 2)}


def _by_moment(batches):
    out = {}
    for m, batch in batches:
        out.setdefault(m, []).append(batch)
    return out


def _contenders(shoot, results, batches):
    """The frames a close call is decided between: the strongest few, and each subject's best."""
    frames = {p["id"]: p for b in batches for p in b}
    ranked = sorted((pid for pid, r in results.items() if not r["reject"]),
                    key=lambda pid: -(results[pid]["score"] - frames[pid].get("focus_penalty", 0)))
    ids = ranked[:CONTENDERS] + [pid for pid in ranked[CONTENDERS:] if results[pid].get("best")]
    return [frames[pid] for pid in sorted(ids[:MAX_CONTENDERS])]  # in shooting order


def _close_call(shoot, results):
    finals = sorted((r["score"] - shoot.by_id(pid).get("focus_penalty", 0)
                     for pid, r in results.items() if not r["reject"]), reverse=True)
    if not finals or finals[0] < FINALIST_FLOOR - CLOSE_TO_FLOOR:
        return False  # nothing here is in contention
    return finals[0] < FINALIST_FLOOR + CLOSE_TO_FLOOR or (
        len(finals) > 1 and finals[0] - finals[1] < CLOSE_TO_BEST)


def review(shoot, reviewer, on_event=_noop, workers=4, screener=None):
    """Send unreviewed moments to Claude. Already-reviewed moments cost nothing.

    screener: an inexpensive reviewer that judges every moment first; `reviewer` then reviews only
    the close calls again, and makes the final edit.
    """
    by_moment = _by_moment(pending_batches(shoot))
    on_event({"type": "stage", "stage": "review", "moments": len(by_moment)})
    notes = taste.prompt_notes()
    spent, errors, t0, close_calls = 0.0, [], time.time(), 0

    def judge(rv, batches):
        """All batches of one moment, then a final round between batch winners if needed."""
        results, usages, winners = {}, [], []
        for batch in batches:
            res, best_ids, usage = rv.review(batch, shoot.read_thumb, notes, read_crop=shoot.read_face_crop,
                                             brief_notes=shoot.brief["review_prompt"])
            results.update(res)
            usages.append((usage, rv.model))
            winners += [p for p in batch if p["id"] in best_ids]
        best_ids = [p["id"] for p in winners]
        if len(batches) > 1 and len(winners) > 1:
            res, best_ids, usage = rv.review(winners, shoot.read_thumb, notes, final_round=True,
                                             brief_notes=shoot.brief["review_prompt"])
            for pid, r in res.items():  # side-by-side scores are the better-calibrated ones
                results[pid].update(score=r["score"], reason=r["reason"])
            usages.append((usage, rv.model))
        for pid, r in results.items():
            r["best"] = pid in best_ids
            r["brief_key"] = shoot.brief["review_key"]
        return results, usages

    def run(moment_id, batches):
        """Returns the moment's reviews, the usage and model of each request, and which model judged each frame."""
        on_event({"type": "review_start", "moment": moment_id,
                  "ids": [p["id"] for b in batches for p in b]})
        first = screener or reviewer
        results, usages = judge(first, batches)
        models = dict.fromkeys(results, first.model)
        if screener and _close_call(shoot, results):
            # The main model decides between the contenders; the rest keep their screened review.
            deep, more = judge(reviewer, [_contenders(shoot, results, batches)])
            usages += more
            for r in results.values():
                r["best"] = False
            results.update(deep)
            models.update(dict.fromkeys(deep, reviewer.model))
        return moment_id, results, usages, models

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run, m, b) for m, b in by_moment.items()]
        for fut in as_completed(futures):
            try:
                moment_id, results, usages, models = fut.result()
            except ReviewError as e:
                errors.append(str(e))
                on_event({"type": "review_error", "error": str(e)})
                continue
            for model in set(models.values()):
                shoot.store.put_reviews({shoot.by_id(pid)["fp"]: r for pid, r in results.items()
                                         if models[pid] == model}, model, RUBRIC_VERSION)
            close_call = bool(screener) and reviewer.model in models.values()
            spent += sum(usage_cost(u, m) for u, m in usages)
            close_calls += close_call
            decide(shoot)
            on_event({"type": "review_done", "moment": moment_id, "spent": round(spent, 3),
                      "close_call": close_call,
                      "photos": [summary(shoot.by_id(pid)) for pid in results]})
    decide(shoot)
    # If a correction's reviews failed, keep the previous edit showing rather than ranking a mix.
    if _final_edit_pending(shoot) and not (errors and shoot.store.get_meta("final_edit")):
        finalists = _finalists(shoot)
        on_event({"type": "final_edit_start", "frames": len(finalists)})
        try:
            ranking, keep_count, unmet, usage = reviewer.final_edit(finalists, shoot.read_preview, notes,
                                                                    brief_notes=shoot.brief["edit_prompt"])
        except (ReviewError, ValueError, KeyError) as e:  # the moment reviews are saved; picks use scores
            errors.append(str(e))
            on_event({"type": "final_edit_error", "error": str(e)})
        else:
            _save_final_edit(shoot, finalists, ranking, keep_count, unmet)
            spent += usage_cost(usage, reviewer.model)
            decide(shoot)
    on_event({"type": "reviewed", "spent": round(spent, 3), "errors": errors, "close_calls": close_calls,
              "moments": len(by_moment), "seconds": round(time.time() - t0, 1)})
    return {"spent": spent, "errors": errors, "close_calls": close_calls}


def _finalists(shoot):
    """Each moment's best frames, strongest first: the frames the final edit compares.

    A brief that wants several frames per moment adds the other strong frames after them, so
    the cap on frames per request never pushes out a moment's best.
    """
    strong = [p for p in shoot.photos if p.get("review") and p["status"] != "rejected"
              and p["final"] >= FINALIST_FLOOR]
    best = [p for p in strong if p["review"].get("best")]
    extra = [p for p in strong if not p["review"].get("best")] if shoot.brief["per_moment"] == "few" else []
    return (sorted(best, key=lambda p: -p["final"]) + sorted(extra, key=lambda p: -p["final"]))[:MAX_FINALISTS]


def _final_edit(shoot):
    """The saved final edit, if it was made for exactly the current finalists and brief."""
    edit = shoot.store.get_meta("final_edit")
    if edit and edit.get("key", "") == shoot.brief["edit_key"] \
            and set(edit["ranking"]) == {p["fp"] for p in _finalists(shoot)}:
        return edit
    return None


def _final_edit_pending(shoot):
    return len(_finalists(shoot)) > 1 and not _final_edit(shoot)


def _shown_edit(shoot):
    """The edit to show: the current one, or the last one while a correction is still to finish."""
    edit = _final_edit(shoot)
    if edit:
        return edit
    saved = shoot.store.get_meta("final_edit")
    if saved and saved.get("key", "") != shoot.brief["edit_key"] and _final_edit_pending(shoot):
        return saved
    return None


def _save_final_edit(shoot, finalists, ranking, keep_count, unmet=()):
    """Store Claude's ranking by fingerprint. Any frame it left out goes last, strongest first."""
    by_id = {p["id"]: p for p in finalists}
    fps, reasons = [], {}
    for pid, reason in ranking:
        if pid in by_id and by_id[pid]["fp"] not in reasons:
            fps.append(by_id[pid]["fp"])
            reasons[by_id[pid]["fp"]] = reason
    fps += [p["fp"] for p in finalists if p["fp"] not in reasons]
    shoot.store.set_meta("final_edit", {"ranking": fps, "reasons": reasons, "unmet": list(unmet),
                                        "key": shoot.brief["edit_key"],
                                        "keep_count": min(max(int(keep_count), 1), len(fps))})


def decide(shoot, settings=None):
    """Apply reviews + overrides: the final edit's selection (or score threshold), then Hidden gems."""
    s = dict(DEFAULT_SETTINGS, **(settings or shoot.store.get_meta("settings", {})))
    reviews, overrides = shoot.store.reviews(), shoot.store.overrides()
    for p in shoot.photos:
        r = reviews.get(p["fp"])
        p["review"] = r
        p["final"] = None
        if p["reject"]:
            p["status"], p["why"] = "rejected", p["reject"]
        else:
            p["status"], p["why"] = "candidate", None
            if r:
                p["final"] = round(r["score"] - p.get("focus_penalty", 0), 2)
                if r["reject"]:
                    p["status"], p["why"] = "rejected", r["reason"]
        p["override"] = overrides.get(p["fp"])
        p["rank"] = None

    # With a final edit the selection is the top of its ranking, compared across the whole shoot.
    # Without one (a failed request, imported scores) each best frame is held to a fixed score.
    edit = _shown_edit(shoot)
    shoot.selection = None
    shoot.unmet = edit.get("unmet", []) if edit else []
    cuts = {}
    if edit:
        size = min(int(s["selection_size"]) or edit["keep_count"], len(edit["ranking"]))
        selected = set(edit["ranking"][:size])
        shoot.selection = {"size": size, "recommended": edit["keep_count"], "total": len(edit["ranking"])}
        for rank, fp in enumerate(edit["ranking"], 1):
            for p in shoot.photos:
                if p["fp"] == fp:
                    p["rank"] = rank
        cuts = {fp: edit["reasons"].get(fp) or "Just outside the selection" for fp in edit["ranking"][size:]}

    def chosen(p):
        if edit:
            return p["fp"] in selected
        return p["review"].get("best") and p["final"] >= s["min_score"]

    for m in shoot.moments:
        cands = [p for p in candidates(shoot, m) if p["status"] == "candidate"]
        reviewed = [p for p in cands if p["review"]]
        # One pick per distinct subject Claude named (unreviewed moments get none).
        best = sorted((p for p in reviewed if chosen(p)), key=lambda p: -p["final"])
        for p in best:
            p["status"] = "pick"
        top = best[0] if best else None
        for p in cands:
            if p["status"] == "pick":
                continue
            if p["fp"] in cuts:
                p["status"], p["why"] = "below", cuts[p["fp"]]
            elif top:
                p["status"], p["why"] = "similar", f"Same moment as pick #{top['id']}"
            elif p["review"]:
                p["status"], p["why"] = "below", "Good, but below the cut"
        # Hidden gems only in moments without a pick: one frame of strong bones, fixable flaws.
        if not top:
            gems = [p for p in reviewed if p["status"] != "rejected"
                    and p["review"]["edit_potential"] >= s["gem_threshold"]
                    and p["review"]["edit_potential"] - p["review"]["score"] >= s["gem_uplift"]
                    and p["review"]["fixable_flaws"]]
            if gems:
                g = max(gems, key=lambda p: p["review"]["edit_potential"])
                g["status"], g["why"] = "gem", g["review"]["edit_recipe"] or "Strong bones; needs an edit"

    # Overrides always win.
    for p in shoot.photos:
        o = p["override"]
        if o == "pick":
            p["status"], p["why"] = "pick", "Picked by you"
        elif o == "gem":
            p["status"], p["why"] = "gem", (p["review"] or {}).get("edit_recipe") or "Marked as gem by you"
        elif o in ("unpick", "ungem") and p["status"] in ("pick", "gem"):
            p["status"], p["why"] = "below", "Removed by you"


def set_override(shoot, pid, action):
    """action: pick | unpick | gem | ungem | None (clear). Records taste too."""
    p = shoot.by_id(pid)
    shoot.store.set_override(p["fp"], action)
    if action:
        taste.record(action, p.get("review"), p.get("notes", []))
    decide(shoot)
    return summary(p)


def summary(p):
    r = p.get("review") or {}
    return {
        "id": p["id"], "fp": p["fp"], "file": p["file"], "moment": p.get("moment"),
        "status": p["status"], "why": p.get("why"), "final": p.get("final"),
        "technical": round(p["technical"] * 10, 1), "score": r.get("score"),
        "reason": r.get("reason"), "edit_potential": r.get("edit_potential"),
        "edit_recipe": r.get("edit_recipe"), "flaws": r.get("fixable_flaws", []),
        "rank": p.get("rank"), "shoot_type": r.get("shoot_type"),
        "subscores": {k: r[k] for k in ("impact", "moment", "light", "composition", "subject_focus") if k in r},
        "notes": p.get("notes", []), "override": p.get("override"),
        "w": p["width"], "h": p["height"], "taken": p["taken"],
    }


def state(shoot):
    return {
        "folder": shoot.folder, "name": os.path.basename(shoot.folder.rstrip("/")),
        "photos": [summary(p) for p in shoot.photos],
        "moments": shoot.moments,
        "selection": shoot.selection,
        "unmet": shoot.unmet,
        "counts": {k: sum(p["status"] == k for p in shoot.photos)
                   for k in ("pick", "gem", "similar", "below", "candidate", "rejected")},
    }


# ---------- the brief ----------

def _brief_preview(shoot, state):
    """A copy of the shoot as it would be with the draft brief applied, for pricing it. Saves nothing."""
    clone = Shoot(shoot.folder, shoot.store)
    clone.photos, clone.groups, clone.moments = copy.deepcopy(shoot.photos), shoot.groups, shoot.moments
    clone.genre = shoot.genre
    clone.brief = brief.plan(state, taste.lasting(), bool(shoot.store.reviews()))
    if clone.brief["tolerance"] != shoot.brief["tolerance"]:
        _technical(clone)
    decide(clone)
    return clone


def brief_estimate(shoot, model, screen_model=None):
    """What confirming the current draft would cost, without changing anything."""
    return review_estimate(_brief_preview(shoot, brief.load(shoot.store)), model, screen_model)


def brief_sample(shoot):
    """A frame from moments spread across the shoot, so Claude can ask about what's really in it."""
    moments_with_frames = [m for m in shoot.moments if candidates(shoot, m)]
    return [candidates(shoot, m)[0] for m in scoring._spread(moments_with_frames, BRIEF_SAMPLE)]


def _chat_context(shoot, state):
    lines = ["Current brief: " + brief.as_json(state["draft"])]
    picks = sorted((p for p in shoot.photos if p["status"] == "pick"), key=lambda p: p.get("rank") or 10 ** 6)
    if picks:
        lines.append(f"The cull has run: {len(picks)} picks from {len(shoot.photos)} photos. They are asking for a "
                     "correction. Top picks and why:")
        lines += [f"- {p['file']}: {(p.get('review') or {}).get('reason', '')}" for p in picks[:40]]
    style = taste.lasting()
    if style:
        lines.append("Their profile and saved style: " + "; ".join(style))
    return "\n".join(lines)


def brief_turn(shoot, reviewer, text=None):
    """One chat turn. text=None asks Claude to open the conversation. Nothing is saved if it fails."""
    state = brief.load(shoot.store)
    messages = state["messages"] + ([{"role": "user", "text": text}] if text else [])
    sample = [shoot.read_preview(p) for p in brief_sample(shoot)]
    data, usage = reviewer.brief_chat(messages, sample, _chat_context(shoot, state))
    saved = taste.lasting()
    state["messages"] = messages + [{"role": "assistant", "text": data["reply"]}]
    state["draft"] = brief.normalize(data)
    state["rerun"] = data["rerun"] if data["rerun"] in brief.RERUNS else "full_review"
    state["suggestions"] = [s for s in brief.clean_list(data["style_suggestions"]) if s not in saved]
    state["spent"] = round(state["spent"] + usage_cost(usage, reviewer.model), 4)
    state["version"] += 1
    brief.save(shoot.store, state)
    return state


def standard_brief(shoot):
    """Replace the draft with the standard brief. No API call."""
    state = brief.load(shoot.store)
    state["draft"] = dict(brief.STANDARD)
    state["rerun"] = "full_review"
    state["suggestions"] = []
    state["version"] += 1
    brief.save(shoot.store, state)
    return state


def confirm_brief(shoot, save_style=()):
    """The photographer approved the draft: from now on it applies to reviews and the final edit."""
    state = brief.load(shoot.store)
    old_target = (state["applied"] or {}).get("brief", {}).get("target_count")
    tolerance = shoot.brief["tolerance"]
    shoot.brief = brief.plan(state, taste.lasting(), bool(shoot.store.reviews()))
    state["applied"] = shoot.brief
    state["rerun"] = "final_edit"  # until the chat changes something, confirming again reruns nothing
    target = state["draft"]["target_count"]
    if target != old_target:
        shoot.store.set_meta("settings", dict(shoot.store.get_meta("settings", {}), selection_size=target or 0))
    if save_style:
        taste.add_style([s for s in save_style if s in state["suggestions"]])
    state["suggestions"] = []
    brief.save(shoot.store, state)
    if shoot.brief["tolerance"] != tolerance:
        _technical(shoot)
    decide(shoot)
