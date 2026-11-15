"""Preview of a Hidden gem after its edit: Claude's recipe turned into adjustments and applied.

The recipe is free text ("Lift exposure +1.3, warm WB +300K, straighten 2 degrees, crop to 4:5"),
so the numbers are read out of it; where it gives none, the flaws Claude listed and the frame's
own measurements fill in. Everything runs locally on the 1024 px thumbnail. It is a quick
preview of where the edit leads, not a finished edit.
"""
import math
import re

import cv2
import numpy as np
from PIL import Image

TARGET_LUMA = 118      # mean luma an "underexposed" frame without numbers is lifted towards
MAX_STOPS = 2.5        # exposure change beyond this is noise, not a rescue
MAX_ROTATION = 8.0     # same limit as the tilt the technical pass reports

_KNEE = 0.55           # linear level above which a lifted exposure is rolled off
_NUM = r"([+-−–]?\s*\d+(?:\.\d+)?)"
_RATIO = re.compile(r"\b(\d{1,2})\s*[:x×]\s*(\d{1,2})\b")


def _number(text, *words):
    """(number, whether it carried its own sign) next to any of the words, or (None, False).

    Reads "shadows +40", "exposure by 1.3" and "+0.7 stops of exposure".
    """
    for w in words:
        m = re.search(rf"{w}\w*\s*(?:by|to|of)?\s*{_NUM}", text) or re.search(rf"{_NUM}\s*(?:stops?|ev)?\s*(?:of\s+)?{w}", text)
        if m:
            raw = m.group(1).replace("−", "-").replace("–", "-").replace(" ", "")
            return float(raw), raw[0] in "+-"
    return None, False


def _verb_sign(text, word, default=1):
    """-1 when the recipe lowers the thing ("pull highlights 30", "reduce exposure"), else default."""
    m = re.search(rf"(\w+)\s+(?:the\s+)?{word}", text)
    verb = m.group(1) if m else ""
    if re.match(r"pull|lower|reduce|drop|recover|tame|darken|cut|decrease|bring", verb):
        return -1
    if re.match(r"lift|raise|open|boost|brighten|increase|add|push", verb):
        return 1
    return default


def adjustments(review, metrics):
    """What the preview applies, as a dict of settings; empty when there is nothing to edit.

    exposure (stops), shadows, highlights, contrast, saturation (-100..100), warmth (kelvin),
    rotate (degrees, counter-clockwise), crop ((w, h) ratio), denoise (bool).
    """
    if not review:
        return {}
    text = (review.get("edit_recipe") or "").lower()
    flaws = set(review.get("fixable_flaws") or [])
    adj = {}

    ev, signed = _number(text, "exposure")
    if ev is None:
        ev, signed = _number(text, "stops?")
    if ev is not None:
        adj["exposure"] = ev if signed else abs(ev) * _verb_sign(text, "exposure", -1 if "overexposed" in flaws else 1)
    elif "underexposed" in flaws or "overexposed" in flaws or "exposure" in text:
        luma = max(metrics.get("mean_luma") or TARGET_LUMA, 4)
        adj["exposure"] = round(math.log2(TARGET_LUMA / luma) * 0.8, 2)  # most of the way, in display terms
    if "exposure" in adj:
        adj["exposure"] = max(-MAX_STOPS, min(MAX_STOPS, adj["exposure"]))
        if abs(adj["exposure"]) < 0.1:
            del adj["exposure"]

    for key, word, default_sign, implied in (("shadows", "shadow", 1, 35), ("highlights", "highlight", -1, 35),
                                             ("contrast", "contrast", 1, 20), ("saturation", "saturation", 1, 15),
                                             ("saturation", "vibrance", 1, 15)):
        if word not in text or key in adj:
            continue
        n, signed = _number(text, word)
        size = implied if n is None else abs(n) if abs(n) > 5 else abs(n) * 30  # "shadows +1" is in stops
        sign = math.copysign(1, n) if signed else _verb_sign(text, word, default_sign)
        adj[key] = max(-100, min(100, size * sign))
    if "contrast" not in adj and "flat_contrast" in flaws:
        adj["contrast"] = 20

    wb = re.sub(r"\b(?:keep|leave|preserve)[^,;.]*", "", text)  # "keep the sky warm" isn't an instruction to warm
    k = re.search(rf"{_NUM}\s*k\b", wb)
    if k and re.search(r"wb|white balance|warm|cool|temp", wb):
        kelvin = abs(float(k.group(1).replace("−", "-").replace("–", "-").replace(" ", "")))
        adj["warmth"] = -kelvin if re.search(r"cool|colder|bluer", wb) else kelvin
    elif re.search(r"\bwarm", wb):
        adj["warmth"] = 400
    elif re.search(r"\bcool", wb):
        adj["warmth"] = -400
    elif flaws & {"white_balance", "color_cast"}:
        adj["neutralize"] = True

    tilt = metrics.get("tilt_deg")
    if "straighten" in text or "level" in text or "tilt" in flaws:
        n, _ = _number(text, "straighten", "rotate", "level")
        if tilt is not None and abs(tilt) >= 0.3:
            size = abs(n) if n else abs(tilt)
            adj["rotate"] = round(math.copysign(min(size, MAX_ROTATION), tilt), 1)

    if "crop" in text or "crop" in flaws:
        m = _RATIO.search(text)
        if m and 0 < int(m.group(1)) <= 21 and 0 < int(m.group(2)) <= 21:
            adj["crop"] = (int(m.group(1)), int(m.group(2)))
        elif "square" in text:
            adj["crop"] = (1, 1)

    if "noise" in flaws or "noise" in text or "denoise" in text:
        adj["denoise"] = True
    return adj


def describe(adj):
    """The adjustments in the words a photographer would use, for showing under the preview."""
    out = []
    if "exposure" in adj:
        out.append(f"Exposure {adj['exposure']:+.1f}")
    for key in ("shadows", "highlights", "contrast", "saturation"):
        if key in adj:
            out.append(f"{key.capitalize()} {adj[key]:+.0f}")
    if "warmth" in adj:
        out.append(f"{'Warmer' if adj['warmth'] > 0 else 'Cooler'} {abs(adj['warmth']):.0f}K")
    if adj.get("neutralize"):
        out.append("Colour cast neutralised")
    if "rotate" in adj:
        out.append(f"Straightened {abs(adj['rotate']):.1f}°")
    if "crop" in adj:
        out.append(f"Cropped to {adj['crop'][0]}:{adj['crop'][1]}")
    if adj.get("denoise"):
        out.append("Noise reduced")
    return out


def _to_linear(v):
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def _to_srgb(v):
    v = np.clip(v, 0, 1)
    return np.where(v <= 0.0031308, v * 12.92, 1.055 * v ** (1 / 2.4) - 0.055)


def _inner_box(w, h, angle):
    """Largest axis-aligned box of the frame's aspect ratio inside the frame rotated by angle."""
    a = abs(math.radians(angle))
    scale = 1 / (math.cos(a) + max(w / h, h / w) * math.sin(a))
    return w * scale, h * scale


def render(img, adj, faces=()):
    """img (PIL RGB) with the adjustments applied. faces: thumbnail-pixel boxes, to crop around."""
    v = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0
    if adj.get("denoise"):
        v = cv2.fastNlMeansDenoisingColored((v * 255).astype(np.uint8), None, 4, 6, 5, 15).astype(np.float32) / 255.0
    if adj.get("neutralize"):  # halfway to grey-world: takes the cast out without flattening a sunset
        means = v.reshape(-1, 3).mean(axis=0) + 1e-6
        v = v * (1 + 0.5 * (means.mean() / means - 1))
    if "warmth" in adj:
        t = max(-1.0, min(1.0, adj["warmth"] / 1000)) * 0.12
        v = v * np.array([1 + t, 1 + t * 0.15, 1 - t], dtype=np.float32)
    if "exposure" in adj:
        lin = _to_linear(np.clip(v, 0, 1)) * (2.0 ** adj["exposure"])
        if adj["exposure"] > 0:  # shoulder: lifted highlights roll off towards white instead of clipping
            lin = np.where(lin > _KNEE, _KNEE + (1 - _KNEE) * np.tanh((lin - _KNEE) / (1 - _KNEE)), lin)
        v = _to_srgb(lin)
    if "shadows" in adj or "highlights" in adj:
        luma = cv2.GaussianBlur(np.clip(v, 0, 1) @ np.array([0.299, 0.587, 0.114], dtype=np.float32), (0, 0), 12)
        gain = 1 + adj.get("shadows", 0) / 100 * 1.1 * (1 - luma) ** 3
        gain *= 1 + adj.get("highlights", 0) / 100 * 0.45 * luma ** 3
        v = v * gain[..., None]
    if "contrast" in adj:
        mid = float(np.clip(v, 0, 1).mean())
        v = mid + (v - mid) * (1 + adj["contrast"] / 100 * 0.6)
    if "saturation" in adj:
        grey = (v @ np.array([0.299, 0.587, 0.114], dtype=np.float32))[..., None]
        v = grey + (v - grey) * (1 + adj["saturation"] / 100)
    out = Image.fromarray((np.clip(v, 0, 1) * 255 + 0.5).astype(np.uint8))

    if "rotate" in adj:
        w, h = out.size
        out = out.rotate(adj["rotate"], resample=Image.BICUBIC)
        bw, bh = _inner_box(w, h, adj["rotate"])
        out = out.crop((round((w - bw) / 2), round((h - bh) / 2), round((w + bw) / 2), round((h + bh) / 2)))
    if "crop" in adj:
        w, h = out.size
        rw, rh = adj["crop"]
        if (w >= h) != (rw >= rh) and rw != rh:  # "4:5" on a landscape frame still means a landscape crop
            rw, rh = rh, rw
        cw, ch = (w, round(w * rh / rw)) if w * rh <= h * rw else (round(h * rw / rh), h)
        cx, cy = w / 2, h / 2
        if faces and "rotate" not in adj:  # keep the main face in the frame
            x, y, fw, fh = max(faces, key=lambda b: b[2])
            sx, sy = w / img.width, h / img.height
            cx, cy = (x + fw / 2) * sx, (y + fh / 2) * sy
        left = min(max(cx - cw / 2, 0), w - cw)
        top = min(max(cy - ch / 2, 0), h - ch)
        out = out.crop((round(left), round(top), round(left + cw), round(top + ch)))
    return out
