import hashlib
import os

from conftest import FakeReviewer

from photocull.engine import export, pipeline, review
from photocull.engine.store import Store


def _analyze(folder):
    return pipeline.analyze(folder, Store(folder))


def _by_file(shoot):
    return {p["file"]: p for p in shoot.photos}


def test_technical_pass(shoot_dir):
    f = _by_file(_analyze(shoot_dir))
    assert f["IMG_200_blurred.jpg"]["status"] == "rejected"
    assert "blown highlights" in f["IMG_400_overexposed.jpg"]["why"]
    assert "eyes closed" in f["IMG_701_portrait.jpg"]["why"]
    burst = [f[f"IMG_{100 + i}.jpg"] for i in range(5)]
    assert sum(p["status"] != "rejected" for p in burst) in (1, 2)  # best frame, maybe its twin
    for i in (0, 3, 4):  # soft and shaken frames lose to the sharp ones
        assert burst[i]["status"] == "rejected"


def test_moments_merge_reframes_not_scenes(shoot_dir):
    f = _by_file(_analyze(shoot_dir))
    assert len({f[f"IMG_{100 + i}.jpg"]["moment"] for i in range(5)}) == 1
    assert f["IMG_500_wide.jpg"]["moment"] == f["IMG_501_tight.jpg"]["moment"]
    distinct = ["IMG_100.jpg", "IMG_300_tilted.jpg", "IMG_500_wide.jpg", "IMG_600.jpg", "IMG_700_portrait.jpg"]
    assert len({f[n]["moment"] for n in distinct}) == len(distinct)


def test_one_pick_per_moment_and_review_cache(shoot_dir):
    shoot = _analyze(shoot_dir)
    rev = FakeReviewer({"IMG_501": {"score": 8.5}})
    pipeline.review(shoot, rev)
    first_calls = rev.calls
    assert first_calls == len({m for m, _ in [(m["id"], 0) for m in shoot.moments
                                              if pipeline.candidates(shoot, m)]})
    f = _by_file(shoot)
    assert f["IMG_501_tight.jpg"]["status"] == "pick"
    assert f["IMG_500_wide.jpg"]["status"] == "similar"
    for m in shoot.moments:
        assert sum(shoot.by_id(i)["status"] == "pick" for i in m["members"]) <= 1

    # Re-running analysis (e.g. after a threshold change) must not re-send anything.
    shoot2 = _analyze(shoot_dir)
    rev2 = FakeReviewer()
    pipeline.review(shoot2, rev2)
    assert rev2.calls == 0
    assert _by_file(shoot2)["IMG_501_tight.jpg"]["status"] == "pick"


def test_overrides_survive_rerun(shoot_dir):
    shoot = _analyze(shoot_dir)
    pipeline.review(shoot, FakeReviewer({"IMG_501": {"score": 8.5}}))
    wide = _by_file(shoot)["IMG_500_wide.jpg"]
    pipeline.set_override(shoot, wide["id"], "pick")
    f = _by_file(_analyze(shoot_dir))
    assert f["IMG_500_wide.jpg"]["status"] == "pick"
    assert f["IMG_500_wide.jpg"]["override"] == "pick"


def test_hidden_gem(shoot_dir):
    shoot = _analyze(shoot_dir)
    pipeline.review(shoot, FakeReviewer({"IMG_600": {
        "score": 5.5, "edit_potential": 8.5, "fixable_flaws": ["underexposed"],
        "edit_recipe": "Lift exposure +1"}}))
    p = _by_file(shoot)["IMG_600.jpg"]
    assert p["status"] == "gem"
    assert p["why"] == "Lift exposure +1"


def test_export_copies_and_replaces_only_its_own_files(shoot_dir):
    before = {n: hashlib.md5(open(os.path.join(shoot_dir, n), "rb").read()).hexdigest()
              for n in os.listdir(shoot_dir) if n.endswith(".jpg")}
    shoot = _analyze(shoot_dir)
    pipeline.review(shoot, FakeReviewer({"IMG_600": {
        "score": 5.5, "edit_potential": 8.5, "fixable_flaws": ["tilt"], "edit_recipe": "Straighten"}}))
    res = export.export(shoot)
    picks = sorted(os.listdir(res["picks_dir"]))
    assert picks and all(n[:3].rstrip("_").isdigit() for n in picks)
    assert "edit-recipes.txt" in os.listdir(res["gems_dir"])
    mine = os.path.join(res["picks_dir"], "my-notes.txt")
    open(mine, "w").write("keep me")
    pipeline.set_override(shoot, _by_file(shoot)[picks[0][3:]]["id"], "unpick")
    export.export(shoot)
    assert os.path.exists(mine)
    assert len(os.listdir(res["picks_dir"])) == len(picks)  # one pick fewer + my-notes.txt
    after = {n: hashlib.md5(open(os.path.join(shoot_dir, n), "rb").read()).hexdigest() for n in before}
    assert before == after


def test_estimate_and_schema():
    assert review.OUTPUT_SCHEMA["properties"]["photos"]["items"]["additionalProperties"] is False
    batch = [{"width": 6000, "height": 4000}] * 10
    cost = review.estimate_cost([batch], "claude-sonnet-5-5", lambda p: (1024, 683))
    assert 0.01 < cost < 0.2
