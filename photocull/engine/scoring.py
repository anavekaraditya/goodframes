"""Turn raw metrics into a 0-1 technical score, hard rejects, and the best frames of each burst."""
import statistics

import numpy as np

MAX_PER_BURST = 4          # frames of one burst sent for review: the main control on review cost
LOOSE_PER_BURST = 8        # the same limit when the brief asks for a loose technical pass
LOOSE_EXTREMES = 3         # extreme frames per burst that reach review under a loose pass
MIN_SHARP_IN_BURST = 0.3   # sharpness relative to the burst's sharpest below which a frame is dropped
SOFT_RATIO = 0.6           # relative sharpness below which a frame starts losing points
UNUSABLE_DARK = 6          # mean luma below which a frame holds nothing to judge
UNUSABLE_CLIPPED = 0.85    # share of clipped pixels above which a frame holds nothing to judge


def _pctl(values, q, default):
    values = [v for v in values if v]
    return float(np.percentile(values, q)) if values else default


def score(photos, groups, genre="auto"):
    sharp_ref = _pctl([p["metrics"]["sharpness"] for p in photos], 80, 1.0)
    face_ref = _pctl([max((f["sharpness"] for f in p["metrics"]["faces"]), default=0)
                      for p in photos], 80, 1.0)
    tilt_weight = {"landscape": 1.0, "auto": 0.5}.get(genre, 0.25)

    # Sharpness and eyes are compared mainly within each near-duplicate group: across
    # scenes the numbers mostly measure lighting and texture, not focus.
    group_of = {i: g for g, members in enumerate(groups) for i in members}

    def face_sharp(p):
        return max((f["sharpness"] for f in p["metrics"]["faces"]), default=0.0)

    def min_eyes(p):
        return min((f["eyes_found"] for f in p["metrics"]["faces"]), default=None)

    group_sharp = [max(photos[i]["metrics"]["sharpness"] for i in g) or 1.0 for g in groups]
    group_face = [max(face_sharp(photos[i]) for i in g) or 1.0 for g in groups]
    group_eyes = [max((e for e in (min_eyes(photos[i]) for i in g) if e is not None), default=None)
                  for g in groups]

    for idx, p in enumerate(photos):
        m, notes = p["metrics"], []
        g = group_of[idx]
        sharp_rel = m["sharpness"] / sharp_ref
        sharp_in_group = m["sharpness"] / group_sharp[g]
        sharp_n = 0.7 * sharp_in_group ** 0.5 + 0.3 * min(1.0, sharp_rel ** 0.5)
        faces = m["faces"]
        eyes_n = None
        if faces:
            face_rel = face_sharp(p) / face_ref
            face_n = 0.7 * (face_sharp(p) / group_face[g]) ** 0.5 + 0.3 * min(1.0, face_rel ** 0.5)
            sharp_n = 0.5 * sharp_n + 0.5 * face_n
            # Haar eye detection misses many open eyes (dusk, glasses, profiles), so only a
            # frame with fewer detected eyes than its near-duplicates is penalised.
            eyes_n = 1.0 - 0.4 * max(group_eyes[g] - min_eyes(p), 0)
            if eyes_n < 1.0:
                notes.append("possible closed eyes")
        noise_n = 1.0 - 0.5 * min(max((m["noise_sigma"] - 3) / 12, 0.0), 1.0)
        if eyes_n is None:
            tech = 0.55 * sharp_n + 0.30 * m["exposure_score"] + 0.15 * noise_n
        else:
            tech = 0.45 * sharp_n + 0.25 * m["exposure_score"] + 0.10 * noise_n + 0.20 * eyes_n
        tilt = m["tilt_deg"]
        if tilt is not None and 0.7 <= abs(tilt) <= 8:
            tech -= tilt_weight * 0.3 * min(abs(tilt) / 6, 1.0)
            notes.append(f"horizon/verticals tilted {tilt:+.1f}°")
        soft = sharp_in_group < 0.6 or m["sharpness"] < 120
        if m["shake_anisotropy"] > 0.6 and soft:
            tech -= 0.1
            notes.append("possible camera shake / motion blur")
        elif soft:
            notes.append("soft focus")
        et, fl = p.get("exposure_time"), p.get("focal_length")
        if et and fl and et > 1.0 / fl and soft:
            notes.append(f"slow shutter (1/{round(1 / et)}s at {fl:.0f}mm)")
        if m["clip_hi"] > 0.05:
            notes.append(f"highlights clipped ({m['clip_hi']:.0%})")
        if m["mean_luma"] < 60:
            notes.append("underexposed")
        elif m["mean_luma"] > 200:
            notes.append("overexposed")
        if m["noise_sigma"] > 10:
            notes.append("noisy")

        # Only the unusable is rejected unseen. Other extremes may be intentional (a silhouette,
        # high key, motion blur), so they go to the aesthetic pass with a note.
        reject = extreme = None
        if m["mean_luma"] < UNUSABLE_DARK:
            reject = "almost entirely black"
        elif m["clip_hi"] > UNUSABLE_CLIPPED:
            reject = f"blown highlights ({m['clip_hi']:.0%} of frame clipped)"
        elif m["sharpness"] < 40:
            extreme = "extremely soft: missed focus, or deliberate motion blur?"
        elif m["clip_hi"] > 0.35:
            extreme = f"{m['clip_hi']:.0%} of frame clipped: blown, or deliberate high key?"
        elif m["mean_luma"] < 20:
            extreme = "very dark: underexposed, or deliberate low key or silhouette?"
        if extreme:
            notes.append(extreme)
        # Softness only costs points against a sharper near-duplicate; a lone soft frame is
        # judged by eye, since across scenes the numbers mostly measure lighting and texture.
        rel = face_sharp(p) / group_face[g] if faces else sharp_in_group
        p["focus_penalty"] = round(min(1.0, max(0.0, 1 - rel / SOFT_RATIO)), 2)
        p["sharp_in_group"] = sharp_in_group
        p["technical"] = round(max(tech, 0.0), 3)
        p["notes"] = notes
        p["reject"] = reject
        p["extreme"] = bool(extreme)


def _why_weaker(p, best):
    if best["metrics"]["faces"] and p["metrics"]["faces"]:
        if min(f["eyes_found"] for f in p["metrics"]["faces"]) < min(
                f["eyes_found"] for f in best["metrics"]["faces"]):
            return f"eyes closed/blinking; #{best['id']} has eyes open"
    if p["metrics"]["sharpness"] < 0.8 * best["metrics"]["sharpness"]:
        return f"softer than near-duplicate #{best['id']}"
    if p["metrics"]["exposure_score"] < best["metrics"]["exposure_score"] - 0.1:
        return f"worse exposure than near-duplicate #{best['id']}"
    return f"near-duplicate of #{best['id']}, slightly weaker"


def _fewest_eyes(p):
    return min((f["eyes_found"] for f in p["metrics"]["faces"]), default=None)


def _spread(items, n):
    """n items evenly spaced through a time-ordered list, so a burst's whole arc gets seen."""
    if len(items) <= n:
        return items
    if n == 1:
        return items[:1]
    return [items[round(k * (len(items) - 1) / (n - 1))] for k in range(n)]


def select_in_groups(photos, groups, tolerance="standard"):
    """Mark each photo 'candidate' or 'rejected', leaving the choice of expression to the review.

    Keeps up to MAX_PER_BURST frames across each burst, dropping only clear failures: much
    softer than the burst's sharpest frame, or eyes closed where a sibling has them open.
    tolerance "loose" (from the photographer's brief) keeps more frames and no longer treats
    softness or a missing eye as a failure, since those can be exactly what they want to see.
    """
    loose = tolerance == "loose"
    per_burst, max_extremes = (LOOSE_PER_BURST, LOOSE_EXTREMES) if loose else (MAX_PER_BURST, 1)
    for gi, members in enumerate(groups, 1):
        ms = [photos[i] for i in sorted(members)]
        for p in ms:
            p["group"] = gi
            if p["reject"]:
                p["status"] = "rejected"
        alive = [p for p in ms if not p["reject"]]
        if not alive:
            continue
        best = max(alive, key=lambda p: p["technical"])
        eyes = [e for e in map(_fewest_eyes, alive) if e is not None]
        most_eyes = max(eyes, default=None)

        def clear_failure(p):
            if loose:
                return False
            closed = most_eyes is not None and _fewest_eyes(p) is not None and _fewest_eyes(p) < most_eyes
            return closed or p["sharp_in_group"] < MIN_SHARP_IN_BURST

        extremes = sorted((p for p in alive if p["extreme"]), key=lambda p: -p["technical"])[:max_extremes]
        normal = [p for p in alive if not p["extreme"] and (p is best or not clear_failure(p))]
        keep = _spread(normal, per_burst - len(extremes))
        if best in normal and best not in keep:  # always show Claude the technically best frame
            nearest = min(keep, key=lambda p: abs(ms.index(p) - ms.index(best)))
            keep[keep.index(nearest)] = best
        keep += extremes
        for p in alive:
            if p in keep:
                p["status"] = "candidate"
            else:
                p["status"] = "rejected"
                p["reject"] = _why_weaker(p, best)
