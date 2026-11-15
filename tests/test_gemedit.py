import os
from io import BytesIO

import cv2
import numpy as np
from conftest import FakeReviewer, _scene
from PIL import Image, ImageEnhance

from photocull.engine import gemedit, pipeline, quality
from photocull.engine.store import Store


def _adj(recipe="", flaws=(), **metrics):
    return gemedit.adjustments({"edit_recipe": recipe, "fixable_flaws": list(flaws)}, metrics)


def test_recipe_numbers_are_read_with_their_direction():
    assert _adj("Lift exposure +1.3 and shadows +40; keep the sky warm.") == {"exposure": 1.3, "shadows": 40.0}
    assert _adj("Pull highlights 30, reduce exposure by 0.7, add contrast") == {
        "exposure": -0.7, "highlights": -30.0, "contrast": 20}
    assert _adj("Lift shadows +1, warm WB +300K, crop to 4:5") == {"shadows": 30.0, "warmth": 300.0, "crop": (4, 5)}
    assert _adj("Cool WB -400K, crop square") == {"warmth": -400.0, "crop": (1, 1)}


def test_flaws_and_measurements_fill_in_for_a_recipe_without_numbers():
    adj = _adj("", ["underexposed", "flat_contrast", "color_cast", "noise", "tilt"], mean_luma=45, tilt_deg=-3.2)
    assert 0.8 < adj["exposure"] < 1.5 and adj["contrast"] == 20 and adj["neutralize"] and adj["denoise"]
    assert adj["rotate"] == -3.2
    assert _adj("Straighten 2 degrees", tilt_deg=3.5)["rotate"] == 2.0  # the recipe's size, the measured direction
    assert "rotate" not in _adj("Straighten 2 degrees")  # no measured tilt: the direction is unknown
    assert _adj() == {} and gemedit.adjustments(None, {}) == {}
    assert gemedit.describe(adj)[0].startswith("Exposure +")


def test_render_lifts_a_dark_frame_without_clipping_and_levels_a_tilted_one():
    scene = _scene(5)
    scene.thumbnail((1024, 1024))
    dark = ImageEnhance.Brightness(scene).enhance(0.35)
    after = gemedit.render(dark, {"exposure": 1.3, "shadows": 40})
    luma = lambda im: float(np.asarray(im.convert("L")).mean())
    assert luma(after) > luma(dark) * 1.5 and after.size == dark.size
    assert (np.asarray(after) >= 254).mean() < 0.02

    tilted = _scene(3, lines=True).rotate(3.5, resample=Image.BICUBIC, fillcolor=(90, 90, 90))
    tilted.thumbnail((1024, 1024))
    gray = lambda im: cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2GRAY)
    tilt = quality.tilt_degrees(gray(tilted))
    level = gemedit.render(tilted, _adj("Straighten", tilt_deg=tilt))
    assert abs(quality.tilt_degrees(gray(level))) < 0.5
    assert level.width < tilted.width and abs(level.width / level.height - tilted.width / tilted.height) < 0.02

    wide = gemedit.render(scene, {"crop": (4, 5)})  # a landscape frame stays landscape
    assert abs(wide.width / wide.height - 5 / 4) < 0.01


def test_a_gem_serves_its_edited_preview(shoot_dir):
    shoot = pipeline.analyze(shoot_dir, Store(shoot_dir))
    pipeline.review(shoot, FakeReviewer({"IMG_600": {
        "score": 5.5, "edit_potential": 8.5, "fixable_flaws": ["underexposed"],
        "edit_recipe": "Lift exposure +1, open shadows"}}))
    gem = next(p for p in shoot.photos if p["file"] == "IMG_600.jpg")
    assert gem["status"] == "gem" and pipeline.summary(gem)["edit"] == ["Exposure +1.0", "Shadows +35"]
    edited = Image.open(BytesIO(shoot.read_edited(gem)))
    with Image.open(shoot.store.thumb_path(gem["fp"])) as thumb:
        assert edited.size == thumb.size
        assert np.asarray(edited.convert("L")).mean() > np.asarray(thumb.convert("L")).mean()
    plain = next(p for p in shoot.photos if p["file"] == "IMG_501_tight.jpg")
    assert pipeline.summary(plain)["edit"] == [] and shoot.read_edited(plain) is None
    assert os.path.getsize(os.path.join(shoot_dir, "IMG_600.jpg")) > 0  # originals untouched
