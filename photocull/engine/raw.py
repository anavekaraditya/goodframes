"""Camera RAW files, decoded with macOS ImageIO (the same decoder Photos and Preview use).

ImageIO renders the camera's look, applies orientation and colour-converts to sRGB, and knows
every RAW format the installed macOS supports, so new cameras arrive with OS updates.
"""
import os
from datetime import datetime

from PIL import Image

RAW_EXTS = {".dng", ".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".raf",
            ".orf", ".rw2", ".rwl", ".pef", ".srw", ".3fr", ".fff", ".iiq", ".erf", ".mos", ".x3f"}


def is_raw(path):
    return os.path.splitext(path)[1].lower() in RAW_EXTS


def _source(path):
    import Quartz
    from Foundation import NSURL
    src = Quartz.CGImageSourceCreateWithURL(NSURL.fileURLWithPath_(path), None)
    if src is None or Quartz.CGImageSourceGetCount(src) == 0:
        raise OSError(f"cannot read RAW file {os.path.basename(path)}")
    return src


def _to_pil(cg):
    import Quartz
    w, h = Quartz.CGImageGetWidth(cg), Quartz.CGImageGetHeight(cg)
    buf = bytearray(w * h * 4)
    space = Quartz.CGColorSpaceCreateWithName(Quartz.kCGColorSpaceSRGB)
    ctx = Quartz.CGBitmapContextCreate(buf, w, h, 8, w * 4, space, Quartz.kCGImageAlphaNoneSkipLast)
    Quartz.CGContextDrawImage(ctx, Quartz.CGRectMake(0, 0, w, h), cg)
    return Image.frombuffer("RGBX", (w, h), bytes(buf), "raw", "RGBX", 0, 1).convert("RGB")


def open_raw(path, max_edge=None):
    """The rendered, upright frame as an RGB image, at most max_edge pixels on its long side."""
    import Quartz
    opts = {Quartz.kCGImageSourceCreateThumbnailFromImageAlways: True,
            Quartz.kCGImageSourceCreateThumbnailWithTransform: True}
    if max_edge:
        opts[Quartz.kCGImageSourceThumbnailMaxPixelSize] = max_edge
    else:
        w, h = raw_size(path)
        opts[Quartz.kCGImageSourceThumbnailMaxPixelSize] = max(w, h)
    cg = Quartz.CGImageSourceCreateThumbnailAtIndex(_source(path), 0, opts)
    if cg is None:
        raise OSError(f"cannot decode RAW file {os.path.basename(path)}")
    return _to_pil(cg)


def _props(path):
    import Quartz
    return Quartz.CGImageSourceCopyPropertiesAtIndex(_source(path), 0, None) or {}


def raw_size(path):
    """Upright (width, height) of the full-resolution frame."""
    p = _props(path)
    w, h = int(p.get("PixelWidth") or 0), int(p.get("PixelHeight") or 0)
    return (h, w) if int(p.get("Orientation") or 1) in (5, 6, 7, 8) else (w, h)


def _num(v):
    if v is not None and not isinstance(v, (str, int, float)):  # ISO comes as a list
        v = next(iter(v), None)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def read_raw_exif(path):
    """The same fields as ingest.read_exif, from the RAW file's metadata."""
    p = _props(path)
    exif, tiff = p.get("{Exif}") or {}, p.get("{TIFF}") or {}
    taken = None
    raw = exif.get("DateTimeOriginal") or tiff.get("DateTime")
    if raw:
        try:
            taken = datetime.strptime(str(raw).strip(), "%Y:%m:%d %H:%M:%S")
        except ValueError:
            pass
    if taken is None:
        taken = datetime.fromtimestamp(os.path.getmtime(path))
    return {
        "taken": taken.isoformat(),
        "taken_ts": taken.timestamp(),
        "exposure_time": _num(exif.get("ExposureTime")),
        "f_number": _num(exif.get("FNumber")),
        "iso": _num(exif.get("ISOSpeedRatings")),
        "focal_length": _num(exif.get("FocalLength")),
        "camera": str(tiff.get("Model") or "").strip() or None,
    }
