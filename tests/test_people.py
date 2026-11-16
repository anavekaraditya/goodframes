from io import BytesIO

import numpy as np
from conftest import FakeReviewer
from PIL import Image

from photocull.engine import people, pipeline
from photocull.engine.store import Store


def _faces(rng, base, n, spread=0.15):
    """n signatures of one person: a shared direction plus a little noise, as unit vectors."""
    out = []
    for _ in range(n):
        v = base + rng.normal(0, spread / np.sqrt(len(base)), len(base))
        out.append((v / np.linalg.norm(v)).tolist())
    return out


def _bases(rng, n, dim=128):
    return [b / np.linalg.norm(b) for b in rng.normal(size=(n, dim))]


def test_similar_faces_group_and_two_faces_in_one_photo_never_do():
    rng = np.random.default_rng(1)
    a, b = _bases(rng, 2)
    vectors = _faces(rng, a, 5) + _faces(rng, b, 4)
    groups = sorted(sorted(g) for g in people.cluster(vectors, owners=list(range(9))))
    assert groups == [[0, 1, 2, 3, 4], [5, 6, 7, 8]]
    # Twins in one frame: near-identical signatures, but one photo can't hold a person twice.
    twins = _faces(rng, a, 2, spread=0.01)
    assert len(people.cluster(twins, owners=[7, 7])) == 2 and len(people.cluster(twins, owners=[7, 8])) == 1
    assert people.cluster([], []) == []


def _photo(pid, *faces):
    return {"id": pid, "metrics": {"faces": [{"box": [10, 10, size, size], "quality": q, "embedding": e}
                                             for e, q, size in faces]}}


def test_people_are_those_seen_in_several_photos_most_seen_first():
    rng = np.random.default_rng(2)
    a, b, c = _bases(rng, 3)
    fa, fb, fc = _faces(rng, a, 3), _faces(rng, b, 4), _faces(rng, c, 2)
    photos = [
        _photo(1, (fa[0], 0.4, 80), (fb[0], 0.5, 60)),
        _photo(2, (fa[1], 0.9, 70), (fb[1], 0.3, 60), (fc[0], 0.5, 50)),
        _photo(3, (fa[2], 0.2, 90), (fb[2], 0.8, 60), (fc[1], 0.5, 50)),
        _photo(4, (fb[3], 0.1, 60), (None, 0.9, 200)),  # a face with no signature is left out
        _photo(5),
    ]
    found = people.group(photos)
    assert [(p["id"], p["photos"]) for p in found] == [(1, [1, 2, 3, 4]), (2, [1, 2, 3])]  # c, seen twice, is dropped
    assert found[0]["cover"] == [3, 1] and found[1]["cover"] == [2, 0]  # the best-captured face of each
    assert found[1]["faces"] == {1: 0, 2: 0, 3: 0}


def test_the_bundled_model_signs_a_face_crop():
    rng = np.random.default_rng(3)
    bgr = rng.integers(0, 255, (300, 300, 3), dtype=np.uint8)
    points = [(110, 130), (190, 130), (150, 170), (120, 210), (180, 210)]
    v = people.embed(bgr, [80, 80, 140, 160], points)
    assert len(v) == 128 and abs(np.linalg.norm(v) - 1) < 1e-2
    assert people.embed(bgr, [80, 80, 140, 160], None) is None


def test_the_cull_reports_who_is_in_the_picks(shoot_dir):
    shoot = pipeline.analyze(shoot_dir, Store(shoot_dir))
    pipeline.review(shoot, FakeReviewer({"IMG_501": {"score": 9.0}, "IMG_600": {"score": 8.0}}))
    by_file = {p["file"]: p for p in shoot.photos}
    tight, wide, lone = by_file["IMG_501_tight.jpg"], by_file["IMG_500_wide.jpg"], by_file["IMG_600.jpg"]
    blur, over = by_file["IMG_103.jpg"], by_file["IMG_400_overexposed.jpg"]
    assert tight["status"] == "pick" and wide["status"] == "similar" and blur["status"] == over["status"] == "rejected"
    for p in shoot.photos:
        p["metrics"]["faces"] = [{"box": [100, 100, 80, 80]}]
    shoot.people = [
        {"id": 1, "photos": sorted([tight["id"], wide["id"], lone["id"]]), "faces": {}, "cover": [tight["id"], 0]},
        {"id": 2, "photos": sorted([wide["id"], blur["id"]]), "faces": {}, "cover": [wide["id"], 0]},
        {"id": 3, "photos": sorted([blur["id"], over["id"]]), "faces": {}, "cover": [blur["id"], 0]},
    ]
    pipeline.decide(shoot)
    one, two, three = pipeline.state(shoot)["people"]
    assert one["picks"] == [tight["id"], lone["id"]] and one["open"] == tight["id"] and one["photos"] == 3
    assert two["picks"] == [] and two["usable"] == 1 and two["open"] == wide["id"]  # their one frame that could be a pick
    assert three["picks"] == [] and three["usable"] == 0 and three["open"] in (blur["id"], over["id"])

    pipeline.set_override(shoot, wide["id"], "pick")
    assert pipeline.state(shoot)["people"][1]["picks"] == [wide["id"]]

    avatar = Image.open(BytesIO(shoot.read_face(tight, 0)))
    assert avatar.size == (pipeline.AVATAR_EDGE, pipeline.AVATAR_EDGE)


def test_a_shoot_without_faces_has_no_people(shoot_dir):
    shoot = pipeline.analyze(shoot_dir, Store(shoot_dir))
    state = pipeline.state(shoot)
    assert state["people"] == [] and all(p["people"] == [] for p in state["photos"])
