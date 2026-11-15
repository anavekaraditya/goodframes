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
# A sibling's open eyes count as evidence of a blink only from a frame this comparable:
EYES_MIN_SHARP = 0.8       # sharpness relative to the frame judged (face sharpness shifts with lids)
EYES_EXPOSURE_GAP = 0.1    # exposure-score shortfall allowed
EYES_LUMA_GAP = 20         # mean-luma difference allowed: Haar finds eyes more easily once brightened
# Vision eye openness (outline height / width): about 0.3 wide open, 0.10-0.18 in a big smile.
EYES_CLOSED = 0.12         # below this a frame can be a blink, if a comparable sibling is far more open
BLINK_RATIO = 0.6          # ...at most this share of the sibling's openness
EYES_SHUT = 0.09           # below this the eyes look shut even with nothing to compare against


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

    group_sharp = [max(photos[i]["metrics"]["sharpness"] for i in g) or 1.0 for g in groups]
    group_face = [max(face_sharp(photos[i]) for i in g) or 1.0 for g in groups]

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
            # A smile narrows open eyes, so a frame is penalised as a blink only against a
            # comparable near-duplicate where the same eyes are clearly more open.
            siblings = [photos[i] for i in groups[g]]
            witness = _blink_witness(p, siblings)
            if witness:
                notes.append("possible closed eyes")
            if _openness(p) is not None:
                eyes_n = 0.6 if witness else 1.0
                if not witness and _openness(p) < EYES_SHUT:
                    notes.append("eyes look closed: check whether it's a blink or a deliberate look")
            else:  # Haar eye counts
                eyes_n = 1.0 - 0.4 * (_fewest_eyes(witness) - _fewest_eyes(p)) if witness else 1.0
                if not witness and any((_fewest_eyes(s) or 0) > (_fewest_eyes(p) or 0) for s in siblings):
                    notes.append("eyes not detected, but found on a brighter or softer near-duplicate: "
                                 "check whether they are open")
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
        if (m.get("aesthetics") or {}).get("utility"):
            notes.append("looks like a screenshot or document")

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


def _why_weaker(p, best, siblings):
    witness = _blink_witness(p, siblings)
    if witness:
        return f"eyes closed/blinking; #{witness['id']} has eyes open"
    if p["metrics"]["sharpness"] < 0.8 * best["metrics"]["sharpness"]:
        return f"softer than near-duplicate #{best['id']}"
    if p["metrics"]["exposure_score"] < best["metrics"]["exposure_score"] - 0.1:
        return f"worse exposure than near-duplicate #{best['id']}"
    return f"near-duplicate of #{best['id']}, slightly weaker"


def _openness(p):
    """How open the least-open eyes in the frame are (Vision), or None without an eye outline."""
    values = [f["eye_open"] for f in p["metrics"]["faces"] if f.get("eye_open") is not None]
    return min(values) if values else None


def _fewest_eyes(p):
    """Haar's eye count for the face with fewest, or None (also for Vision faces)."""
    return min((f["eyes_found"] for f in p["metrics"]["faces"] if "eyes_found" in f), default=None)


def _opens_more(s, p):
    """How much more open s's eyes are than p's, if s shows p as a blink; otherwise 0."""
    mine, theirs = _openness(p), _openness(s)
    if mine is not None:
        return theirs - mine if theirs is not None and mine < EYES_CLOSED and mine < BLINK_RATIO * theirs else 0
    mine, theirs = _fewest_eyes(p), _fewest_eyes(s)
    return theirs - mine if mine is not None and theirs is not None and theirs > mine else 0


def _blink_witness(p, siblings):
    """The sibling that shows p's eyes are closed, or None if none credibly does.

    Eye detectors read eyes more easily in a sharper, brighter frame, so a sibling is evidence
    only when it is about as sharp and as well exposed as p. Otherwise p's eyes are unknown.
    """
    pm = p["metrics"]
    witnesses = [s for s in siblings
                 if _opens_more(s, p) > 0
                 and s["metrics"]["sharpness"] >= EYES_MIN_SHARP * pm["sharpness"]
                 and s["metrics"]["exposure_score"] >= pm["exposure_score"] - EYES_EXPOSURE_GAP
                 and abs(s["metrics"].get("mean_luma", 0) - pm.get("mean_luma", 0)) <= EYES_LUMA_GAP]
    return max(witnesses, key=lambda s: _opens_more(s, p), default=None)


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
    softer than the burst's sharpest frame, or eyes closed where a comparably sharp and
    exposed sibling has them open.
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

        def clear_failure(p):
            if loose:
                return False
            return _blink_witness(p, alive) is not None or p["sharp_in_group"] < MIN_SHARP_IN_BURST

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
                p["reject"] = _why_weaker(p, best, alive)
