"""Per-image technical measurements: sharpness, shake, exposure, noise, faces/eyes, tilt.

All metrics are computed on the 1024px working thumbnail so results are comparable
across cameras and fast enough for a 300-photo shoot.
"""
import math

import cv2
import numpy as np

_CASCADES = {}


def _cascade(name):
    if name not in _CASCADES:
        _CASCADES[name] = cv2.CascadeClassifier(cv2.data.haarcascades + name)
    return _CASCADES[name]


def _lap_var(gray):
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def sharpness(gray, grid=6, top=3):
    """Mean Laplacian variance of the sharpest tiles.

    Using the sharpest tiles (not the whole frame) means a sharp subject on a
    creamy bokeh background still scores as sharp.
    """
    h, w = gray.shape
    th, tw = h // grid, w // grid
    tiles = []
    for r in range(grid):
        for c in range(grid):
            tile = gray[r * th:(r + 1) * th, c * tw:(c + 1) * tw]
            tiles.append((_lap_var(tile), r, c))
    tiles.sort(reverse=True)
    best = tiles[:top]
    return float(np.mean([t[0] for t in best])), (best[0][1], best[0][2])


def shake_anisotropy(gray):
    """0 = gradients in all directions, 1 = gradients all one way (smear)."""
    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    jxx, jyy, jxy = (gx * gx).sum(), (gy * gy).sum(), (gx * gy).sum()
    tr = jxx + jyy
    if tr <= 0:
        return 0.0
    disc = math.sqrt(max((jxx - jyy) ** 2 + 4 * jxy * jxy, 0.0))
    return float(disc / tr)


def exposure(gray):
    n = gray.size
    hist = cv2.calcHist([gray], [0], None, [256], [0, 256]).ravel()
    clip_hi = float(hist[250:].sum() / n)
    clip_lo = float(hist[:6].sum() / n)
    mean = float(gray.mean())
    score = 1.0
    score -= min(max(clip_hi - 0.02, 0) * 4, 0.6)
    score -= min(max(clip_lo - 0.05, 0) * 2, 0.4)
    if mean < 60:
        score -= (60 - mean) / 120
    elif mean > 200:
        score -= (mean - 200) / 110
    return {"clip_hi": clip_hi, "clip_lo": clip_lo, "mean_luma": mean,
            "exposure_score": float(max(score, 0.0))}


def noise_sigma(gray):
    """Immerkaer fast noise estimate (sigma in 0-255 units)."""
    h, w = gray.shape
    m = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], dtype=np.float64)
    conv = cv2.filter2D(gray.astype(np.float64), -1, m)
    return float(np.abs(conv).sum() * math.sqrt(math.pi / 2) / (6 * (w - 2) * (h - 2)))


def faces_and_eyes(gray):
    h, w = gray.shape
    min_face = max(int(min(h, w) * 0.06), 30)
    faces = _cascade("haarcascade_frontalface_default.xml").detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=6, minSize=(min_face, min_face))
    eye_det = _cascade("haarcascade_eye_tree_eyeglasses.xml")
    out = []
    for (x, y, fw, fh) in faces:
        upper = gray[y:y + int(fh * 0.55), x:x + fw]
        eyes = eye_det.detectMultiScale(upper, scaleFactor=1.1, minNeighbors=5,
                                        minSize=(max(fw // 10, 8), max(fw // 10, 8)))
        face_crop = gray[y:y + fh, x:x + fw]
        out.append({"box": [int(x), int(y), int(fw), int(fh)],
                    "eyes_found": int(min(len(eyes), 2)),
                    "sharpness": _lap_var(face_crop)})
    return out


def tilt_degrees(gray):
    """Median deviation (deg) of strong near-horizontal/vertical lines; None if too few."""
    h, w = gray.shape
    edges = cv2.Canny(gray, 60, 180)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 360, threshold=80,
                            minLineLength=int(w * 0.15), maxLineGap=8)
    if lines is None:
        return None
    devs, weights = [], []
    for x1, y1, x2, y2 in lines[:, 0]:
        ang = math.degrees(math.atan2(y2 - y1, x2 - x1))
        ang = (ang + 90) % 180 - 90  # -90..90
        if abs(ang) <= 12:
            dev = ang
        elif abs(abs(ang) - 90) <= 12:
            dev = ang - math.copysign(90, ang)
        else:
            continue
        devs.append(dev)
        weights.append(math.hypot(x2 - x1, y2 - y1))
    if len(devs) < 3:
        return None
    order = np.argsort(devs)
    cum = np.cumsum(np.array(weights)[order])
    return float(np.array(devs)[order][np.searchsorted(cum, cum[-1] / 2)])


def measure(bgr):
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    sharp, sharp_tile = sharpness(gray)
    faces = faces_and_eyes(gray)
    m = {
        "sharpness": sharp,
        "sharp_tile": sharp_tile,
        "shake_anisotropy": shake_anisotropy(gray),
        "noise_sigma": noise_sigma(gray),
        "faces": faces,
        "tilt_deg": tilt_degrees(gray),
    }
    m.update(exposure(gray))
    return m
