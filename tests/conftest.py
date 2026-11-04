"""Synthetic shoot: a burst, a reframed pair, blur, tilt, overexposure and an eyes-open/closed pair."""
import os
import random
import shutil
import struct
from datetime import datetime, timedelta

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from photocull.engine.review import ReviewError

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _save(img, path, when):
    ex = Image.Exif()
    stamp = when.strftime("%Y:%m:%d %H:%M:%S")
    ex[306] = stamp
    ex.get_ifd(0x8769)[36867] = stamp
    img.save(path, "JPEG", quality=92, exif=ex)


def write_dng(path, rgb, orientation=1, stamp="2026:10:20 17:30:00", model="Goodframes Test"):
    """A minimal linear (demosaiced) DNG: one uncompressed 8-bit RGB strip, sRGB-ish colour."""
    h, w, _ = rgb.shape
    pixels = (np.asarray(rgb, dtype=np.uint16) * 257).astype("<u2").tobytes()
    srat = lambda xs: b"".join(struct.pack("<ii", round(x * 10000), 10000) for x in xs)
    xyz_to_srgb = (3.2404542, -1.5371385, -0.4985314, -0.9692660, 1.8760108, 0.0415560,
                   0.0556434, -0.2040259, 1.0572252)
    entries = [  # (tag, type, count, payload bytes)
        (254, 4, 1, struct.pack("<I", 0)),
        (256, 4, 1, struct.pack("<I", w)), (257, 4, 1, struct.pack("<I", h)),
        (258, 3, 3, struct.pack("<3H", 16, 16, 16)), (259, 3, 1, struct.pack("<H", 1)),
        (262, 3, 1, struct.pack("<H", 34892)), (271, 2, 0, b"Goodframes\0"), (272, 2, 0, model.encode() + b"\0"),
        (273, 4, 1, None), (274, 3, 1, struct.pack("<H", orientation)), (277, 3, 1, struct.pack("<H", 3)),
        (278, 4, 1, struct.pack("<I", h)), (279, 4, 1, struct.pack("<I", len(pixels))),
        (284, 3, 1, struct.pack("<H", 1)), (306, 2, 0, stamp.encode() + b"\0"),
        (50706, 1, 4, bytes((1, 4, 0, 0))), (50708, 2, 0, model.encode() + b"\0"),
        (50721, 10, 9, srat(xyz_to_srgb)), (50728, 5, 3, struct.pack("<6I", 1, 1, 1, 1, 1, 1)),
        (50778, 3, 1, struct.pack("<H", 21)),
    ]
    entries = [(t, ty, n or len(d or b""), d) for t, ty, n, d in entries]
    ifd_size = 2 + 12 * len(entries) + 4
    extra_at = 8 + ifd_size
    extra, ifd = b"", struct.pack("<H", len(entries))
    big = [e for e in entries if e[3] is not None and len(e[3]) > 4]
    extra_len = sum(len(e[3]) + len(e[3]) % 2 for e in big)
    strip_at = extra_at + extra_len
    for tag, typ, n, data in entries:
        if data is None:
            data = struct.pack("<I", strip_at)
        if len(data) > 4:
            ifd += struct.pack("<HHII", tag, typ, n, extra_at + len(extra))
            extra += data + b"\0" * (len(data) % 2)
        else:
            ifd += struct.pack("<HHI", tag, typ, n) + data.ljust(4, b"\0")
    ifd += struct.pack("<I", 0)
    with open(path, "wb") as f:
        f.write(b"II*\0" + struct.pack("<I", 8) + ifd + extra + pixels)


def _scene(seed, w=3000, h=2000, lines=False):
    rnd = random.Random(seed)
    arr = np.random.default_rng(seed).integers(0, 40, (h, w, 3), dtype=np.uint8)
    img = Image.fromarray(arr + np.array([rnd.randint(60, 140) for _ in range(3)], dtype=np.uint8))
    d = ImageDraw.Draw(img)
    for _ in range(40):
        x, y, r = rnd.randint(0, w), rnd.randint(0, h), rnd.randint(40, 300)
        col = tuple(rnd.randint(0, 255) for _ in range(3))
        (d.ellipse if rnd.random() < .5 else d.rectangle)(
            [x, y, x + r, y + r * rnd.uniform(.5, 1.5)], fill=col, outline=(0, 0, 0), width=6)
    if lines:
        for k in range(8):
            d.line([0, 700 + k * 120, w, 700 + k * 120], fill=(20, 20, 20), width=10)
    return img


def _shift(img, dx, dy):
    return img.transform(img.size, Image.AFFINE, (1, 0, dx, 0, 1, dy), resample=Image.BICUBIC)


def make_shoot(out):
    os.makedirs(out, exist_ok=True)
    t = datetime(2026, 10, 20, 17, 0, 0)
    base = _scene(1)
    for i in range(5):  # burst: 0 and 3 soft, 4 shaken
        f = _shift(base, i * 6, i * 3)
        if i in (0, 3):
            f = f.filter(ImageFilter.GaussianBlur(2.5))
        if i == 4:
            k = np.zeros((25, 25))
            k[12, :] = 1 / 25
            f = Image.fromarray(cv2.filter2D(np.asarray(f), -1, k))
        _save(f, os.path.join(out, f"IMG_{100 + i}.jpg"), t + timedelta(seconds=i * 0.5))
    t += timedelta(minutes=2)
    _save(_scene(2).filter(ImageFilter.GaussianBlur(14)), os.path.join(out, "IMG_200_blurred.jpg"), t)
    t += timedelta(minutes=2)
    _save(_scene(3, lines=True).rotate(3.5, resample=Image.BICUBIC, fillcolor=(90, 90, 90)),
          os.path.join(out, "IMG_300_tilted.jpg"), t)
    t += timedelta(minutes=2)
    _save(ImageEnhance.Brightness(_scene(4)).enhance(3.2), os.path.join(out, "IMG_400_overexposed.jpg"), t)
    t += timedelta(minutes=2)
    wide = _scene(5)
    _save(wide, os.path.join(out, "IMG_500_wide.jpg"), t)
    tight = wide.crop((900, 600, 2400, 1600)).resize((3000, 2000), Image.LANCZOS)  # reframe, 20 s later
    _save(tight, os.path.join(out, "IMG_501_tight.jpg"), t + timedelta(seconds=20))
    t += timedelta(minutes=2)
    _save(_scene(6), os.path.join(out, "IMG_600.jpg"), t)

    face = Image.open(os.path.join(FIXTURES, "astronaut.png")).convert("RGB").resize((1536, 1536), Image.LANCZOS)
    t += timedelta(minutes=2)
    _save(face, os.path.join(out, "IMG_700_portrait.jpg"), t)
    closed = face.copy()
    d = ImageDraw.Draw(closed)
    for cx, cy in ((607, 303), (744, 309)):
        d.ellipse([cx - 55, cy - 28, cx + 55, cy + 28], fill=(214, 170, 140))
        d.line([cx - 50, cy + 4, cx + 50, cy + 4], fill=(90, 60, 50), width=5)
    _save(closed.filter(ImageFilter.GaussianBlur(0.6)), os.path.join(out, "IMG_701_portrait.jpg"),
          t + timedelta(seconds=1))


@pytest.fixture(scope="session")
def shoot_template(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("template") / "shoot")
    make_shoot(path)
    return path


@pytest.fixture
def shoot_dir(shoot_template, tmp_path, monkeypatch):
    """A fresh copy of the synthetic shoot per test, with an isolated app-support dir."""
    monkeypatch.setenv("PHOTOCULL_HOME", str(tmp_path / "home"))
    dst = str(tmp_path / "shoot")
    shutil.copytree(shoot_template, dst, ignore=shutil.ignore_patterns(".photo-cull"))
    return dst


class FakeReviewer:
    """Stands in for ClaudeReviewer: scores by filename rules and counts calls."""
    model = "fake-model"

    def __init__(self, rules=None, model=None):
        if model:
            self.model = model
        self.calls = 0
        self.finals = 0
        self.rules = rules or {}
        self.final_edits = 0
        self.keep_count = None  # None keeps every finalist
        self.fail_final_edit = False
        self.fail_reviews = False
        self.unmet = []
        self.review_briefs = []
        self.edit_briefs = []
        self.chat = {}  # fields of the chat's next reply

    def review(self, photos, read_thumb, taste_notes="", final_round=False, read_crop=None, brief_notes=""):
        if self.fail_reviews:
            raise ReviewError("review failed")
        self.calls += 1
        self.review_briefs.append(brief_notes)
        self.finals += final_round
        results = {}
        for p in photos:
            r = {"id": p["id"], "score": 7.5, "reason": "fine", "reject": False,
                 "edit_potential": 7.5, "fixable_flaws": [], "edit_recipe": ""}
            for needle, over in self.rules.items():
                if needle in p["file"]:
                    r.update(over)
            results[p["id"]] = r
        best = max(results.values(), key=lambda r: r["score"])
        return results, ([best["id"]] if best["score"] >= 6 else []), \
            {"input": 1000, "cache_write": 0, "cache_read": 0, "output": 100}

    def final_edit(self, photos, read_preview, taste_notes="", brief_notes=""):
        self.final_edits += 1
        self.edit_briefs.append(brief_notes)
        if self.fail_final_edit:
            raise ReviewError("final edit failed")
        ranked = sorted(photos, key=lambda p: -p["final"])
        return [(p["id"], f"cut #{p['id']}") for p in ranked], self.keep_count or len(ranked), self.unmet, \
            {"input": 1000, "cache_write": 0, "cache_read": 0, "output": 100}

    def brief_chat(self, messages, sample, context):
        return {"reply": "Noted.", "shoot": "", "priorities": [], "avoid": [], "look": "", "target_count": 0,
                "per_moment": "one", "technical_tolerance": "standard", "rerun": "full_review",
                "style_suggestions": [], **self.chat}, \
            {"input": 1000, "cache_write": 0, "cache_read": 0, "output": 100}
