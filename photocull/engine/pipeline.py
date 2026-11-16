"""Orchestrates a cull: analyse -> moments -> review -> decide picks and Hidden gems.

Every stage reports progress through `on_event(dict)` so the UI can animate it.
"""
import copy
import io
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from PIL import Image

from . import brief, gemedit, grouping, moments, people, scoring, taste
from .ingest import THUMB_EDGE, ingest, open_photo
from .raw import is_raw
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
AVATAR_EDGE = 144            # a person's face in the People strip


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
        self.faces = []       # faces grouped by person, as the grouping found them (people.group)
        self.people = []      # the same after the photographer's corrections (people.apply_edits)
        self.cast = []        # per person: how many photos, which are picks, and the frame to open
        self.coverage = []    # per shot on the shot list: the picks that show it, and other frames that do

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

    def read_edited(self, p):
        """The frame's thumbnail with its edit recipe applied, as JPEG bytes; None with nothing to apply."""
        adj = gemedit.adjustments(p.get("review"), p["metrics"])
        if not adj:
            return None
        with Image.open(self.store.thumb_path(p["fp"])) as im:
            out = gemedit.render(im, adj, [f["box"] for f in p["metrics"]["faces"]])
        return _jpeg(out)

    def original(self, p):
        """The photo's own file."""
        return os.path.join(self.folder, p["file"])

    def shareable(self, p):
        """A file to send to someone: the original, or for a RAW file a full-size JPEG of it.

        The JPEG is made once and kept with the shoot's working files, never next to the original.
        """
        path = self.original(p)
        if not is_raw(path):
            return path
        out = os.path.join(self.store.work, "shared", os.path.splitext(p["file"])[0] + ".jpg")
        if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(path):
            os.makedirs(os.path.dirname(out), exist_ok=True)
            im = open_photo(path).convert("RGB")
            im.save(out + ".part", "JPEG", quality=92)
            os.replace(out + ".part", out)
        return out

    def read_face(self, p, k):
        """A small square portrait of the k-th face in the frame, from its thumbnail."""
        x, y, w, h = p["metrics"]["faces"][k]["box"]
        with Image.open(self.store.thumb_path(p["fp"])) as im:
            half = max(w, h) * (0.5 + FACE_PADDING / 2)
            cx = min(max(x + w / 2, half), im.width - half)
            cy = min(max(y + h / 2, half), im.height - half)
            out = im.convert("RGB").crop((int(cx - half), int(cy - half), int(cx + half), int(cy + half)))
        return _jpeg(out.resize((AVATAR_EDGE, AVATAR_EDGE), Image.LANCZOS))

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
    shoot.faces = people.group(photos)
    _place_people(shoot)
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
        listed = shoot.brief.get("review_shots", [])
        for pid, r in results.items():
            r["best"] = pid in best_ids
            r["brief_key"] = shoot.brief["review_key"]
            # Saved as the shot's words, so removing or reordering the list later keeps the tags right.
            r["shots"] = [listed[n - 1] for n in dict.fromkeys(r.get("shots") or [])
                          if isinstance(n, int) and 0 < n <= len(listed)]
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
    # A frame that failed the technical check has no final score, even when it was reviewed and then picked by hand.
    strong = [p for p in shoot.photos if p.get("review") and p["status"] != "rejected"
              and p["final"] is not None and p["final"] >= FINALIST_FLOOR]
    best = [p for p in strong if p["review"].get("best")]
    # A count above the number of moments is filled from the strongest other frames.
    target = shoot.brief["brief"].get("target_count") or 0
    several = shoot.brief["per_moment"] == "few" or len(best) < target
    extra = [p for p in strong if not p["review"].get("best")] if several else []
    # The strongest frame of each shot on the shot list is compared too, even a weak one.
    cover = []
    for shot in shoot.brief["brief"].get("shots", []):
        shows = [p for p in shoot.photos if shot in p.get("shots", ()) and p.get("review")
                 and p["status"] != "rejected" and p["final"] is not None]
        if shows and not any(p in best for p in shows):
            top = max(shows, key=lambda p: p["final"])
            if top not in cover:
                cover.append(top)
    extra = [p for p in extra if p not in cover]
    return (sorted(best, key=lambda p: -p["final"]) + cover + sorted(extra, key=lambda p: -p["final"]))[:MAX_FINALISTS]


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
    shots = shoot.brief["brief"].get("shots", [])
    listed = {s.lower(): s for s in shots}
    for p in shoot.photos:
        r = reviews.get(p["fp"])
        p["review"] = r
        p["shots"] = [listed[t.lower()] for t in (r or {}).get("shots", []) if t.lower() in listed]
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
    usable = [p for p in shoot.photos if p["status"] == "candidate" and p["review"]]
    if edit:
        size = min(int(s["selection_size"]) or edit["keep_count"], len(edit["ranking"]))
        selected = set(edit["ranking"][:size])
        shoot.selection = {"size": size, "recommended": edit["keep_count"], "total": len(edit["ranking"])}
        for rank, fp in enumerate(edit["ranking"], 1):
            for p in shoot.photos:
                if p["fp"] == fp:
                    p["rank"] = rank
    else:
        selected = {p["fp"] for p in usable if p["review"].get("best") and p["final"] >= s["min_score"]}

    # The shot list: every shot that has a usable frame gets its best one into the selection.
    order = {fp: k for k, fp in enumerate(edit["ranking"])} if edit else {}
    added = {}
    for shot in shots:
        shows = [p for p in usable if shot in p["shots"] and p["override"] not in ("unpick", "reject")]
        if shows and not any(p["fp"] in selected or p["override"] == "pick" for p in shows):
            top = min(shows, key=lambda p: (order.get(p["fp"], len(order)), -p["final"]))
            selected.add(top["fp"])
            added[top["fp"]] = shot
    # With a fixed number of photos, a frame added for the list takes the place of the weakest
    # frame that isn't itself the only one of a listed shot.
    bumped = set()
    if edit and added and int(s["selection_size"]):
        by_fp = {p["fp"]: p for p in usable}
        for fp in reversed(edit["ranking"][:size]):
            if len(selected) <= size:
                break
            only = any(not any(shot in by_fp[o]["shots"] for o in selected if o != fp and o in by_fp)
                       for shot in by_fp[fp]["shots"]) if fp in by_fp else False
            if fp not in added and not only:
                selected.discard(fp)
                bumped.add(fp)
    if edit:
        cuts = {fp: edit["reasons"].get(fp) or "Just outside the selection" for fp in edit["ranking"][size:]}
        cuts.update(dict.fromkeys(bumped, "Made room for a frame from your shot list"))
        for fp in added:
            cuts.pop(fp, None)

    def chosen(p):
        return p["fp"] in selected

    for m in shoot.moments:
        cands = [p for p in candidates(shoot, m) if p["status"] == "candidate"]
        reviewed = [p for p in cands if p["review"]]
        # One pick per distinct subject Claude named (unreviewed moments get none).
        best = sorted((p for p in reviewed if chosen(p)), key=lambda p: -p["final"])
        for p in best:
            p["status"] = "pick"
            if p["fp"] in added:
                p["why"] = f"Kept for your shot list: {added[p['fp']]}"
        top = best[0] if best else None
        for p in cands:
            if p["status"] == "pick":
                continue
            if p["fp"] in cuts:
                p["status"], p["why"] = "below", cuts[p["fp"]]
            elif top:
                p["status"], p["why"] = "similar", f"Same moment as the pick {top['file']}"
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
        elif o == "reject":
            p["status"], p["why"] = "rejected", "Rejected by you"
        elif o in ("unpick", "ungem") and p["status"] in ("pick", "gem"):
            p["status"], p["why"] = "below", "Removed by you"

    _cast(shoot)

    # What became of each shot: the picks that show it, and the other frames that do.
    shoot.coverage = [{"shot": shot,
                       "picks": [p["id"] for p in shoot.photos if shot in p["shots"] and p["status"] == "pick"],
                       "others": [p["id"] for p in shoot.photos if shot in p["shots"] and p["status"] != "pick"]}
                      for shot in shots]


PEOPLE_EDITS = "people_edits"
MAX_NAME_CHARS = 40


def _place_people(shoot):
    shoot.people = people.apply_edits(shoot.faces, shoot.photos, shoot.store.get_meta(PEOPLE_EDITS))
    for p in shoot.photos:
        p["people"] = []
    for person in shoot.people:
        for pid in person["photos"]:
            shoot.by_id(pid)["people"].append(person["id"])


def _cast(shoot):
    """Who made the picks. A person with none opens on their strongest frame that could still be one."""
    shoot.cast = []
    for person in shoot.people:
        frames = [shoot.by_id(pid) for pid in person["photos"]]
        picks = sorted((p for p in frames if p["status"] == "pick"), key=lambda p: p["rank"] or 10 ** 6)
        usable = [p for p in frames if p["status"] != "rejected"]
        best = max(usable or frames, key=lambda p: (p["final"] if p["final"] is not None else -1, p["technical"]))
        shoot.cast.append({"id": person["id"], "name": person["name"], "hidden": person["hidden"], "merged": person["merged"],
                           "photos": len(frames), "frames": person["photos"], "picks": [p["id"] for p in picks],
                           "usable": len(usable), "open": (picks[0] if picks else best)["id"],
                           "cover": person["cover"], "faces": {str(k): v for k, v in person["faces"].items()}})


def edit_person(shoot, person_id, action, name=None, other=None, photo=None, face=None):
    """A correction to the grouping. Returns the id the person has afterwards.

    action: name (name="" clears it) | hide | show | merge (other joins this person) |
    remove (photo isn't this person) | restore (photo's face goes back to whoever it was grouped
    with: undoes a remove) | split (undo every merge into this person).
    """
    person = next((g for g in shoot.people if g["id"] == person_id), None)
    if person is None:
        raise KeyError(person_id)
    edits = shoot.store.get_meta(PEOPLE_EDITS) or {}
    names, hidden = edits.setdefault("names", {}), edits.setdefault("hidden", [])
    merges, removed = edits.setdefault("merges", []), edits.setdefault("removed", [])
    pinned = lambda: {f for f in person["keys"] if f in names or f in hidden or any(f in m for m in merges)}
    anchor = next(iter(pinned()), None) or people.face_key(shoot.photos, person)
    if action == "name":
        for f in person["keys"]:
            names.pop(f, None)
        if (name := " ".join((name or "").split())[:MAX_NAME_CHARS]):
            names[anchor] = name
    elif action in ("hide", "show"):
        hidden[:] = [f for f in hidden if f not in person["keys"]]
        if action == "hide":
            hidden.append(anchor)
    elif action == "merge":
        into = next((g for g in shoot.people if g["id"] == other), None)
        if into is None or into is person:
            raise KeyError(other)
        merges.append([anchor, people.face_key(shoot.photos, into)])
        if not person["name"] and into["name"]:  # the merged person keeps a name either one had
            names[anchor] = into["name"]
    elif action == "split":
        merges[:] = [m for m in merges if m[0] not in person["keys"]]
    elif action == "remove":
        if photo not in person["faces"] or len(person["faces"]) < 2:
            raise KeyError(photo)
        gone = f"{shoot.by_id(photo)['fp']}:{person['faces'][photo]}"
        removed.append(gone)
        # A correction pinned to that face moves to one this person keeps.
        keep = next(f for f in sorted(person["keys"]) if f != gone)
        for f in list(names):
            if f == gone:
                names[keep] = names.pop(f)
        hidden[:] = [keep if f == gone else f for f in hidden]
        merges[:] = [[keep if f == gone else f for f in m] for m in merges]
    elif action == "restore":
        gone = f"{shoot.by_id(photo)['fp']}:{face}"
        if gone not in removed:
            raise KeyError(photo)
        removed.remove(gone)
    else:
        raise ValueError(action)
    shoot.store.set_meta(PEOPLE_EDITS, edits)
    keys = person["keys"] - ({gone} if action == "remove" else set())
    _place_people(shoot)
    _cast(shoot)
    return next((g["id"] for g in shoot.people if g["keys"] & keys), person_id)


def set_override(shoot, pid, action, undo=False, forget=False):
    """action: pick | unpick | gem | ungem | reject | None (clear). Records taste too.

    undo: this puts back the frame's previous override, which isn't a new choice to learn from.
    forget: the choice being undone was learned, so take it back.
    """
    p = shoot.by_id(pid)
    shoot.store.set_override(p["fp"], action)
    if forget:
        taste.unrecord(p["fp"])
    if action and not undo:
        taste.record(action, p.get("review"), p.get("notes", []), p["fp"])
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
        "edit": gemedit.describe(gemedit.adjustments(r, p["metrics"])),  # what the after-edit preview applies
        "shots": p.get("shots", []), "people": p.get("people", []),
        "rank": p.get("rank"), "shoot_type": r.get("shoot_type"), "best": bool(r.get("best")),
        "subscores": {k: r[k] for k in ("impact", "moment", "light", "composition", "subject_focus") if k in r},
        "notes": p.get("notes", []), "override": p.get("override"),
        "aesthetic": round((a["score"] + 1) * 5, 1) if (a := p["metrics"].get("aesthetics")) else None,
        "w": p["width"], "h": p["height"], "taken": p["taken"],
    }


_FRAME_REF = re.compile(r"(?:#\s?|\b(?:photo |frame )?id\s?=\s?)(\d+)\b", re.IGNORECASE)


def readable(text, shoot):
    """Claude's text with its references to frames by number ("#18", "id=18") turned into file names.

    Frames go to Claude numbered by photo id, which the photographer never sees.
    """
    if not text:
        return text
    n = len(shoot.photos)
    return _FRAME_REF.sub(lambda m: shoot.by_id(int(m.group(1)))["file"] if 1 <= int(m.group(1)) <= n else m.group(0), text)


def _shown(shoot, p):
    s = summary(p)
    for k in ("reason", "why", "edit_recipe"):
        s[k] = readable(s[k], shoot)
    return s


def state(shoot):
    return {
        "folder": shoot.folder, "name": os.path.basename(shoot.folder.rstrip("/")),
        "photos": [_shown(shoot, p) for p in shoot.photos],
        "moments": shoot.moments,
        "selection": shoot.selection,
        "target": shoot.brief["brief"].get("target_count"),
        "unmet": shoot.unmet,
        "coverage": shoot.coverage,
        "people": shoot.cast,
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
    state["draft"] = brief.normalize(dict({"shots": state["draft"]["shots"]}, **data))
    state["rerun"] = data["rerun"] if data["rerun"] in brief.RERUNS else "full_review"
    state["suggestions"] = [s for s in brief.clean_list(data["style_suggestions"]) if s not in saved]
    state["spent"] = round(state["spent"] + usage_cost(usage, reviewer.model), 4)
    state["version"] += 1
    brief.save(shoot.store, state)
    return state


def standard_brief(shoot):
    """Replace the draft with the standard brief, keeping the photo count. No API call."""
    state = brief.load(shoot.store)
    state["draft"] = dict(brief.STANDARD, target_count=state["draft"]["target_count"], shots=state["draft"]["shots"])
    state["rerun"] = "full_review"
    state["suggestions"] = []
    state["version"] += 1
    brief.save(shoot.store, state)
    return state


def set_target(shoot, count):
    """Set how many photos the cull should keep (None: Claude's recommendation). No API call."""
    state = brief.load(shoot.store)
    state["draft"] = brief.normalize(dict(state["draft"], target_count=count))
    state["version"] += 1
    brief.save(shoot.store, state)
    return state


def set_shots(shoot, shots):
    """Set the shot list: the photos the cull must come away with. No API call."""
    state = brief.load(shoot.store)
    state["draft"] = brief.normalize(dict(state["draft"], shots=shots))
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
