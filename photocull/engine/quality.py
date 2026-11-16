"""Per-image technical measurements: sharpness, shake, exposure, noise, faces/eyes, tilt.

All metrics are computed on the 1024px working thumbnail so results are comparable
across cameras and fast enough for a 300-photo shoot. Faces and eyes come from Apple's
Vision framework (the detector Photos uses), with OpenCV's Haar cascades as a fallback.
"""
import math

import cv2
import numpy as np

from . import people

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


def _min_face(gray):
    return max(int(min(gray.shape) * 0.06), 30)


def _openness(region, fw, fh):
    """Height over width of an eye's outline: about 0.3 wide open, under 0.1 shut."""
    if region is None or region.pointCount() < 4:
        return None
    pts = region.normalizedPoints()
    xs = [pts[i].x * fw for i in range(region.pointCount())]
    ys = [pts[i].y * fh for i in range(region.pointCount())]
    width = max(xs) - min(xs)
    return round((max(ys) - min(ys)) / width, 3) if width > 0 else None


def _five_points(lm, b, w, h):
    """The eyes, nose and mouth corners in thumbnail pixels, each pair left to right; None if any is missing."""
    if not lm or not (lm.leftEye() and lm.rightEye() and lm.nose() and lm.outerLips()):
        return None

    def pixels(region):
        pts = region.normalizedPoints()
        return [((b.origin.x + pts[i].x * b.size.width) * w, (1 - b.origin.y - pts[i].y * b.size.height) * h)
                for i in range(region.pointCount())]

    def centre(region):
        pts = pixels(region)
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)) if pts else None

    eyes = [centre(lm.leftEye()), centre(lm.rightEye())]
    nose, lips = centre(lm.nose()), pixels(lm.outerLips())
    if None in eyes or nose is None or len(lips) < 2:
        return None
    eyes.sort()
    return [*eyes, nose, min(lips), max(lips)]


def vision(bgr, gray):
    """(faces, aesthetics) from Apple Vision, or None where Vision isn't available.

    faces: box (thumbnail pixels, top-left origin), eye_open (the less open eye, None when no
    eye outline was found), quality (Vision's face capture quality, 0-1), sharpness and
    embedding (the face's signature for telling people apart, see people.py; None without one).
    aesthetics: Apple's overall image score (-1 to 1) and whether the frame looks like a
    screenshot or document; None before macOS 15.
    """
    try:
        import Vision
        from Foundation import NSData
    except ImportError:
        return None
    ok, buf = cv2.imencode(".bmp", bgr)
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(
        NSData.dataWithBytes_length_(buf.tobytes(), len(buf)), None)
    landmarks = Vision.VNDetectFaceLandmarksRequest.alloc().init()
    requests = [landmarks]
    scores = None
    if hasattr(Vision, "VNCalculateImageAestheticsScoresRequest"):
        scores = Vision.VNCalculateImageAestheticsScoresRequest.alloc().init()
        requests.append(scores)
    ok, _ = handler.performRequests_error_(requests, None)
    if not ok:
        return None
    found = [f for f in (landmarks.results() or [])]
    quality = Vision.VNDetectFaceCaptureQualityRequest.alloc().init()
    if found:
        quality.setInputFaceObservations_(found)
        handler.performRequests_error_([quality], None)
    qualities = [q.faceCaptureQuality() for q in (quality.results() or [])] if found else []
    h, w = gray.shape
    faces = []
    for i, f in enumerate(found):
        b = f.boundingBox()
        fw, fh = b.size.width * w, b.size.height * h
        if min(fw, fh) < _min_face(gray):
            continue  # people in the background: their eyes aren't the photo's
        x, y = max(int(b.origin.x * w), 0), max(int((1 - b.origin.y - b.size.height) * h), 0)
        lm = f.landmarks()
        eyes = [e for e in (_openness(lm.leftEye(), fw, fh), _openness(lm.rightEye(), fw, fh))
                if e is not None] if lm else []
        q = qualities[i] if i < len(qualities) else None
        faces.append({"box": [x, y, int(fw), int(fh)], "eye_open": min(eyes) if eyes else None,
                      "quality": round(float(q), 3) if q is not None else None,
                      "embedding": people.embed(bgr, [x, y, fw, fh], _five_points(lm, b, w, h)),
                      "sharpness": _lap_var(crop) if (crop := gray[y:y + int(fh), x:x + int(fw)]).size else 0.0})
    aesthetics = None
    if scores is not None and scores.results():
        r = scores.results()[0]
        aesthetics = {"score": round(float(r.overallScore()), 3), "utility": bool(r.isUtility())}
    return faces, aesthetics


def faces_and_eyes(gray):
    """Haar-cascade faces and eye counts: the fallback where Vision isn't available."""
    min_face = _min_face(gray)
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
    seen = vision(bgr, gray)
    faces, aesthetics = seen if seen else (faces_and_eyes(gray), None)
    m = {
        "sharpness": sharp,
        "sharp_tile": sharp_tile,
        "shake_anisotropy": shake_anisotropy(gray),
        "noise_sigma": noise_sigma(gray),
        "faces": faces,
        "tilt_deg": tilt_degrees(gray),
        "aesthetics": aesthetics,
    }
    m.update(exposure(gray))
    return m
