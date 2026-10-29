"""Turn raw metrics into a 0-1 technical score, hard rejects, and the best frames of each burst."""
import statistics

import numpy as np


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

        reject = None
        # Absolute floor only: shoot-relative sharpness is skewed by high-detail scenes
        # (a daylight frame can score 20x a perfectly usable high-ISO dusk portrait).
        # Softer frames still lose to sharper near-duplicates and are judged visually.
        if m["sharpness"] < 40:
            reject = "severely blurred / missed focus"
        elif m["clip_hi"] > 0.35:
            reject = f"blown highlights ({m['clip_hi']:.0%} of frame clipped)"
        elif m["mean_luma"] < 20:
            reject = "far too dark"
        p["technical"] = round(max(tech, 0.0), 3)
        p["notes"] = notes
        p["reject"] = reject


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


def select_in_groups(photos, groups, twin_margin=0.04):
    """Mark each photo 'candidate' or 'rejected'; keep the best 1-2 frames of each burst."""
    for gi, members in enumerate(groups, 1):
        ms = [photos[i] for i in members]
        for p in ms:
            p["group"] = gi
        alive = sorted((p for p in ms if not p["reject"]), key=lambda p: -p["technical"])
        for p in ms:
            if p["reject"]:
                p["status"] = "rejected"
        if not alive:
            continue
        best = alive[0]
        best["status"] = "candidate"
        for p in alive[1:]:
            if p is alive[1] and best["technical"] - p["technical"] <= twin_margin:
                p["status"] = "candidate"  # too close to call: let the aesthetic pass decide
            else:
                p["status"] = "rejected"
                p["reject"] = _why_weaker(p, best)
