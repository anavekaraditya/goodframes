import os

import numpy as np
from conftest import _scene, write_dng
from fastapi.testclient import TestClient
from PIL import Image

from photocull.engine import pipeline
from photocull.engine.store import Store
from photocull.server import app as server


def _open(folder, monkeypatch):
    shoot = pipeline.analyze(str(folder), Store(str(folder)))
    monkeypatch.setattr(server.Session, "shoot", shoot)
    monkeypatch.setattr(server.Session, "job", None)
    return shoot, TestClient(server.app)


def test_a_copy_is_saved_where_the_photographer_chose_and_the_original_is_untouched(shoot_dir, tmp_path, monkeypatch):
    shoot, client = _open(shoot_dir, monkeypatch)
    p = next(q for q in shoot.photos if q["file"] == "IMG_600.jpg")
    src = os.path.join(shoot_dir, "IMG_600.jpg")
    before = os.stat(src).st_mtime_ns

    # In a browser the copy is a download of the original.
    got = client.get(f"/api/original/{p['id']}")
    assert got.status_code == 200 and got.content == open(src, "rb").read()
    assert 'filename="IMG_600.jpg"' in got.headers["content-disposition"]
    assert client.post("/api/save-copy", json={"id": p["id"]}).status_code == 501  # no native dialog there

    dest = tmp_path / "out" / "For Priya.jpg"
    dest.parent.mkdir()
    asked = []
    monkeypatch.setattr(server.app.state, "save_file", lambda name: asked.append(name) or str(dest))
    assert client.post("/api/save-copy", json={"id": p["id"]}).json() == {"saved": str(dest)}
    assert asked == ["IMG_600.jpg"] and dest.read_bytes() == open(src, "rb").read()
    monkeypatch.setattr(server.app.state, "save_file", lambda name: None)  # cancelled
    assert client.post("/api/save-copy", json={"id": p["id"]}).json() == {"saved": None}
    assert os.stat(src).st_mtime_ns == before
    assert client.get("/api/original/999").status_code == 404


def test_share_sends_the_original_and_a_jpeg_for_raw(tmp_path, monkeypatch):
    folder = tmp_path / "Raw shoot"
    folder.mkdir()
    write_dng(str(folder / "DSC_0001.dng"), np.asarray(_scene(7, 900, 600)))
    _scene(8, 900, 600).save(folder / "DSC_0002.jpg", "JPEG")
    shoot, client = _open(folder, monkeypatch)
    raw, jpg = sorted(shoot.photos, key=lambda p: p["file"])
    assert client.post("/api/share", json={"id": jpg["id"]}).status_code == 501  # browser: no share menu

    shared = []
    monkeypatch.setattr(server.app.state, "share", lambda path, rect: shared.append((path, rect)))
    assert client.post("/api/share", json={"id": jpg["id"], "rect": [10, 20, 80, 26]}).status_code == 200
    assert shared[-1] == (str(folder / "DSC_0002.jpg"), [10, 20, 80, 26])
    assert client.post("/api/share", json={"id": raw["id"]}).status_code == 200
    out = shared[-1][0]
    assert out.endswith("DSC_0001.jpg") and os.path.dirname(os.path.dirname(out)) == shoot.store.work
    with Image.open(out) as im:
        assert im.format == "JPEG" and max(im.size) == 900
    assert sorted(os.listdir(folder)) == [".photo-cull", "DSC_0001.dng", "DSC_0002.jpg"]  # nothing beside the originals
