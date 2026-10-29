"""Orchestrates a cull: analyse -> moments -> review -> decide picks and Hidden gems.

Every stage reports progress through `on_event(dict)` so the UI can animate it.
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import grouping, moments, scoring, taste
from .ingest import ingest
from .review import RUBRIC_VERSION, ReviewError, estimate_cost, usage_cost

DEFAULT_SETTINGS = {
    "min_score": 7.0,        # final score a moment's best frame needs to become a pick
    "gem_threshold": 8.0,    # edit_potential a frame needs to become a Hidden gem
    "gem_uplift": 1.5,       # ...and how much better than as-shot it must get
}


def _noop(event):
    pass


class Shoot:
    def __init__(self, folder, store):
        self.folder = folder
        self.store = store
        self.photos = []      # dicts, sorted by capture time, id = 1..n
        self.groups = []      # near-duplicate groups (photo index lists)
        self.moments = []     # [{"id": k, "members": [photo ids]}]

    def by_id(self, pid):
        return self.photos[pid - 1]

    def thumb_dims(self, p):
        w, h = p["width"], p["height"]
        s = 1024 / max(w, h)
        return (w * s, h * s) if s < 1 else (w, h)

    def read_thumb(self, p):
        with open(self.store.thumb_path(p["fp"]), "rb") as f:
            return f.read()


def analyze(folder, store, genre="auto", on_event=_noop):
    """Technical pass. No API calls."""
    shoot = Shoot(folder, store)
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
    scoring.score(photos, shoot.groups, genre)
    scoring.select_in_groups(photos, shoot.groups)
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


def candidates(shoot, moment):
    return [shoot.by_id(i) for i in moment["members"] if shoot.by_id(i)["status"] != "rejected"]


def pending_batches(shoot):
    """Review batches for moments that have a candidate without a current review."""
    reviews = shoot.store.reviews()
    out = []
    for m in shoot.moments:
        cands = candidates(shoot, m)
        if not cands:
            continue
        fresh = all(p["fp"] in reviews and reviews[p["fp"]].get("rubric_version") == RUBRIC_VERSION
                    for p in cands)
        if not fresh:
            out.extend((m["id"], batch) for batch in moments.chunks(cands))
    return out


def review_estimate(shoot, model):
    batches = pending_batches(shoot)
    return {"moments": len({m for m, _ in batches}), "photos": sum(len(b) for _, b in batches),
            "requests": len(batches),
            "cost": round(estimate_cost([b for _, b in batches], model, shoot.thumb_dims), 2)}


def review(shoot, reviewer, on_event=_noop, workers=4):
    """Send unreviewed moments to Claude. Already-reviewed moments cost nothing."""
    by_moment = {}
    for m, batch in pending_batches(shoot):
        by_moment.setdefault(m, []).append(batch)
    on_event({"type": "stage", "stage": "review", "moments": len(by_moment)})
    notes = taste.prompt_notes()
    spent, errors, t0 = 0.0, [], time.time()

    def run(moment_id, batches):
        """All batches of one moment, then a final round between batch winners if needed."""
        on_event({"type": "review_start", "moment": moment_id,
                  "ids": [p["id"] for b in batches for p in b]})
        results, usages, winners = {}, [], []
        for batch in batches:
            res, best_ids, usage = reviewer.review(batch, shoot.read_thumb, notes)
            results.update(res)
            usages.append(usage)
            winners += [p for p in batch if p["id"] in best_ids]
        best_ids = [p["id"] for p in winners]
        if len(batches) > 1 and len(winners) > 1:
            res, best_ids, usage = reviewer.review(winners, shoot.read_thumb, notes, final_round=True)
            for pid, r in res.items():  # side-by-side scores are the better-calibrated ones
                results[pid].update(score=r["score"], reason=r["reason"])
            usages.append(usage)
        for pid, r in results.items():
            r["best"] = pid in best_ids
        return moment_id, results, usages

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run, m, b) for m, b in by_moment.items()]
        for fut in as_completed(futures):
            try:
                moment_id, results, usages = fut.result()
            except ReviewError as e:
                errors.append(str(e))
                on_event({"type": "review_error", "error": str(e)})
                continue
            shoot.store.put_reviews({shoot.by_id(pid)["fp"]: r for pid, r in results.items()},
                                    reviewer.model, RUBRIC_VERSION)
            spent += sum(usage_cost(u, reviewer.model) for u in usages)
            decide(shoot)
            on_event({"type": "review_done", "moment": moment_id, "spent": round(spent, 3),
                      "photos": [summary(shoot.by_id(pid)) for pid in results]})
    decide(shoot)
    on_event({"type": "reviewed", "spent": round(spent, 3), "errors": errors,
              "seconds": round(time.time() - t0, 1)})
    return {"spent": spent, "errors": errors}


def decide(shoot, settings=None):
    """Apply reviews + overrides: at most one pick per moment (per subject), then Hidden gems."""
    s = dict(DEFAULT_SETTINGS, **(settings or shoot.store.get_meta("settings", {})))
    reviews, overrides = shoot.store.reviews(), shoot.store.overrides()
    for p in shoot.photos:
        r = reviews.get(p["fp"])
        p["review"] = r
        p["final"] = None
        if p["status"] != "rejected":
            p["status"], p["why"] = "candidate", None
            if r:
                p["final"] = round(0.3 * p["technical"] * 10 + 0.7 * r["score"], 2)
                if r["reject"]:
                    p["status"], p["why"] = "rejected", r["reason"]
        else:
            p["why"] = p["reject"]
        p["override"] = overrides.get(p["fp"])

    for m in shoot.moments:
        cands = [p for p in candidates(shoot, m) if p["status"] == "candidate"]
        reviewed = [p for p in cands if p["review"]]
        # One pick per distinct subject Claude named (unreviewed moments get none).
        best = sorted((p for p in reviewed if p["review"].get("best") and p["final"] >= s["min_score"]),
                      key=lambda p: -p["final"])
        for p in best:
            p["status"] = "pick"
        top = best[0] if best else None
        for p in cands:
            if p["status"] == "pick":
                continue
            if top:
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
        "notes": p.get("notes", []), "override": p.get("override"),
        "w": p["width"], "h": p["height"], "taken": p["taken"],
    }


def state(shoot):
    return {
        "folder": shoot.folder, "name": os.path.basename(shoot.folder.rstrip("/")),
        "photos": [summary(p) for p in shoot.photos],
        "moments": shoot.moments,
        "counts": {k: sum(p["status"] == k for p in shoot.photos)
                   for k in ("pick", "gem", "similar", "below", "candidate", "rejected")},
    }
