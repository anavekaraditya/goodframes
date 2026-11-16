from io import BytesIO

import numpy as np
import pytest
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


def _found(pid, photos):
    """A person as the grouping would find them: face 0 in each of these photos."""
    ids = sorted(p["id"] for p in photos)
    return {"id": pid, "photos": ids, "faces": {i: 0 for i in ids}, "cover": [ids[0], 0]}


def test_the_cull_reports_who_is_in_the_picks(shoot_dir):
    shoot = pipeline.analyze(shoot_dir, Store(shoot_dir))
    pipeline.review(shoot, FakeReviewer({"IMG_501": {"score": 9.0}, "IMG_600": {"score": 8.0}}))
    by_file = {p["file"]: p for p in shoot.photos}
    tight, wide, lone = by_file["IMG_501_tight.jpg"], by_file["IMG_500_wide.jpg"], by_file["IMG_600.jpg"]
    blur, over = by_file["IMG_103.jpg"], by_file["IMG_400_overexposed.jpg"]
    assert tight["status"] == "pick" and wide["status"] == "similar" and blur["status"] == over["status"] == "rejected"
    for p in shoot.photos:
        p["metrics"]["faces"] = [{"box": [100, 100, 80, 80]}]
    shoot.faces = [_found(1, [tight, wide, lone]), _found(2, [wide, blur]), _found(3, [blur, over])]
    pipeline._place_people(shoot)
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


def _people_shoot(shoot_dir):
    shoot = pipeline.analyze(shoot_dir, Store(shoot_dir))
    pipeline.review(shoot, FakeReviewer({}))
    ps = shoot.photos
    for p in ps:
        p["metrics"]["faces"] = [{"box": [100, 100, 80, 80]}, {"box": [300, 100, 80, 80]}]
    shoot.faces = [_found(1, ps[0:4]), _found(2, ps[4:7]), _found(3, ps[7:10])]
    pipeline._place_people(shoot)
    pipeline.decide(shoot)
    return shoot


def _again(shoot):
    """The shoot reopened: the grouping runs afresh and the corrections are read back."""
    pipeline._place_people(shoot)
    pipeline.decide(shoot)
    return {c["id"]: c for c in pipeline.state(shoot)["people"]}


def test_names_hiding_and_merges_are_kept_and_undone(shoot_dir):
    shoot = _people_shoot(shoot_dir)
    ps = shoot.photos
    assert pipeline.edit_person(shoot, 1, "name", name="  Priya   Rao ") == 1
    assert pipeline.edit_person(shoot, 3, "hide") == 3
    people = _again(shoot)
    assert people[1]["name"] == "Priya Rao" and people[3]["hidden"] and not people[1]["hidden"]

    # Person 2 is really Priya too.
    assert pipeline.edit_person(shoot, 1, "merge", other=2) == 1
    people = _again(shoot)
    assert set(people) == {1, 3} and people[1]["frames"] == [p["id"] for p in ps[0:7]]
    assert all(people_of == [1] for people_of in (p["people"] for p in ps[0:7]))
    # Numbered differently the next time the grouping runs: the corrections follow the faces.
    shoot.faces = [dict(_found(5, ps[7:10])), dict(_found(6, ps[4:7])), dict(_found(7, ps[0:4]))]
    people = _again(shoot)
    assert {c["id"]: (c["name"], c["hidden"], c["photos"]) for c in people.values()} == {
        7: ("Priya Rao", False, 7), 5: ("", True, 3)}

    pipeline.edit_person(shoot, 7, "split")
    pipeline.edit_person(shoot, 5, "show")
    pipeline.edit_person(shoot, 7, "name", name="")
    people = _again(shoot)
    assert set(people) == {5, 6, 7} and not any(c["name"] or c["hidden"] for c in people.values())


def test_a_photo_taken_out_of_a_person_stays_out_and_their_name_holds(shoot_dir):
    shoot = _people_shoot(shoot_dir)
    ps = shoot.photos
    pipeline.edit_person(shoot, 1, "name", name="Sam")
    cover = shoot.people[0]["cover"][0]
    assert pipeline.edit_person(shoot, 1, "remove", photo=cover) == 1  # the face the name was pinned to
    people = _again(shoot)
    assert people[1]["name"] == "Sam" and cover not in people[1]["frames"] and people[1]["photos"] == 3
    assert shoot.by_id(cover)["people"] == []
    pipeline.edit_person(shoot, 1, "restore", photo=cover, face=0)  # undone
    assert _again(shoot)[1]["photos"] == 4 and shoot.by_id(cover)["people"] == [1]
    pipeline.edit_person(shoot, 1, "merge", other=2)
    assert _again(shoot)[1]["merged"] and not _again(shoot)[3]["merged"]
    with pytest.raises(KeyError):
        pipeline.edit_person(shoot, 1, "remove", photo=ps[9]["id"])  # not one of theirs
    with pytest.raises(KeyError):
        pipeline.edit_person(shoot, 9, "hide")
