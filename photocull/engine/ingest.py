"""Decode photos, write 1024px working thumbnails, hash them and measure technical quality."""
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime

import cv2
import imagehash
import numpy as np
from PIL import Image, ImageOps

from . import quality
from .store import fingerprint

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

EXTS = {".jpg", ".jpeg", ".heic", ".heif"}
THUMB_EDGE = 1024
METRICS_VERSION = 2


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def read_exif(img, path):
    exif = img.getexif()
    sub = exif.get_ifd(0x8769)
    raw = sub.get(36867) or exif.get(306)
    taken = None
    if raw:
        try:
            taken = datetime.strptime(str(raw).strip("\x00 "), "%Y:%m:%d %H:%M:%S")
        except ValueError:
            pass
    if taken is None:
        taken = datetime.fromtimestamp(os.path.getmtime(path))
    return {
        "taken": taken.isoformat(),
        "taken_ts": taken.timestamp(),
        "exposure_time": _num(sub.get(33434)),
        "f_number": _num(sub.get(33437)),
        "iso": _num(sub.get(34855)),
        "focal_length": _num(sub.get(37386)),
        "camera": str(exif.get(272) or "").strip("\x00 ") or None,
    }


def color_hist(bgr):
    """Coarse HSV histogram (8x4x4) used to tell scenes apart when grouping moments."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h = cv2.calcHist([hsv], [0, 1, 2], None, [8, 4, 4], [0, 180, 0, 256, 0, 256])
    cv2.normalize(h, h)
    return [round(float(v), 5) for v in h.ravel()]


def process(path, thumb_path):
    """Worker: decode, write thumbnail, hash, measure. Runs in a subprocess."""
    with Image.open(path) as img:
        info = read_exif(img, path)
        img = ImageOps.exif_transpose(img).convert("RGB")
        info["width"], info["height"] = img.size
        img.thumbnail((THUMB_EDGE, THUMB_EDGE), Image.LANCZOS)
        img.save(thumb_path, "JPEG", quality=85)
        info["phash"] = str(imagehash.phash(img))
        info["dhash"] = str(imagehash.dhash(img))
        bgr = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
    info["hist"] = color_hist(bgr)
    info["metrics"] = quality.measure(bgr)
    return info


def list_photos(folder):
    names = [n for n in os.listdir(folder)
             if os.path.splitext(n)[1].lower() in EXTS and not n.startswith(".")]
    return sorted(names)


def ingest(folder, store, on_progress=lambda **kw: None):
    """Return a list of photo dicts (with 'fp' and 'file'), analysing only new or changed files."""
    names = list_photos(folder)
    results, todo = {}, []
    for name in names:
        path = os.path.join(folder, name)
        fp = fingerprint(path)
        data = store.get_photo(fp, METRICS_VERSION)
        if data and os.path.exists(store.thumb_path(fp)):
            results[name] = dict(data, fp=fp, file=name)
        else:
            todo.append((name, path, fp))
    done = 0
    for name, data in results.items():
        done += 1
        on_progress(fp=data["fp"], file=name, i=done, n=len(names), cached=True)
    if todo:
        with ProcessPoolExecutor() as pool:
            futures = {pool.submit(process, path, store.thumb_path(fp)): (name, fp) for name, path, fp in todo}
            for fut in as_completed(futures):
                name, fp = futures[fut]
                done += 1
                try:
                    data = fut.result()
                except Exception as e:  # unreadable/corrupt file: report and skip
                    on_progress(fp=fp, file=name, i=done, n=len(names), error=str(e))
                    continue
                store.put_photo(fp, name, METRICS_VERSION, data)
                results[name] = dict(data, fp=fp, file=name)
                on_progress(fp=fp, file=name, i=done, n=len(names), cached=False)
    return [results[n] for n in names if n in results]
