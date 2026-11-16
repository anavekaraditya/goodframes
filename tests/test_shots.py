import re

from conftest import FakeReviewer

from photocull.engine import brief, pipeline, review
from photocull.engine.store import Store

MODEL = "claude-sonnet-5-5"
SHOTS = ["Wide of the venue", "The cake", "First dance"]
# IMG_500_wide is a weak frame in a moment whose pick is IMG_501_tight.
RULES = {"IMG_501": {"score": 9.0, "shots": [2]}, "IMG_600": {"score": 8.0},
         "IMG_500": {"score": 5.0, "shots": [1, 7]}}


def _cull(shoot_dir, shots=SHOTS):
    shoot = pipeline.analyze(shoot_dir, Store(shoot_dir))
    rev = FakeReviewer(RULES)
    rev.keep_count = 5  # the final edit would cut its weakest frame, the wide
    pipeline.set_shots(shoot, shots)
    pipeline.confirm_brief(shoot)
    pipeline.review(shoot, rev)
    rev.calls = rev.final_edits = 0
    return shoot, rev


def _photo(shoot, name):
    return next(p for p in shoot.photos if p["file"].startswith(name))


def _picks(shoot):
    return {p["file"] for p in shoot.photos if p["status"] == "pick"}


def test_the_shot_list_is_set_without_a_call_and_survives_the_standard_cull(shoot_dir):
    shoot = pipeline.analyze(shoot_dir, Store(shoot_dir))
    state = pipeline.set_shots(shoot, ["  Cake   cutting ", "cake cutting", "", "x" * 200] + [f"Shot {k}" for k in range(20)])
    shots = state["draft"]["shots"]
    assert shots[:2] == ["Cake cutting", "x" * brief.MAX_SHOT_CHARS] and len(shots) == brief.MAX_ITEMS
    assert state["applied"] is None and shoot.brief["review_prompt"] == ""  # a draft until confirmed
    assert pipeline.standard_brief(shoot)["draft"]["shots"] == shots
    rev = FakeReviewer()
    rev.chat = {"shots": ["First dance"]}
    assert pipeline.brief_turn(shoot, rev, "add the first dance")["draft"]["shots"] == ["First dance"]


def test_reviews_are_sent_the_list_and_every_shot_keeps_its_best_frame(shoot_dir):
    shoot, rev = _cull(shoot_dir)
    assert all("1. Wide of the venue" in b and "3. First dance" in b for b in rev.review_briefs)
    assert "Wide of the venue; The cake; First dance" in rev.edit_briefs[0]
    wide, tight = _photo(shoot, "IMG_500"), _photo(shoot, "IMG_501")
    assert wide["shots"] == ["Wide of the venue"]  # a number off the list is dropped
    assert wide["status"] == "pick" and wide["why"] == "Kept for your shot list: Wide of the venue"
    assert tight["status"] == "pick" and tight["why"] is None  # already a pick: nothing to add
    assert pipeline.state(shoot)["coverage"] == [
        {"shot": "Wide of the venue", "picks": [wide["id"]], "others": []},
        {"shot": "The cake", "picks": [tight["id"]], "others": []},
        {"shot": "First dance", "picks": [], "others": []}]
    assert pipeline.summary(wide)["shots"] == ["Wide of the venue"]


def test_without_a_list_the_cull_is_unchanged(shoot_dir):
    shoot, rev = _cull(shoot_dir, shots=[])
    assert shoot.brief["review_prompt"] == "" and shoot.brief["review_key"] == ""
    assert _photo(shoot, "IMG_500")["status"] == "similar" and pipeline.state(shoot)["coverage"] == []


def test_a_fixed_count_makes_room_for_a_listed_shot(shoot_dir):
    shoot, rev = _cull(shoot_dir)
    pipeline.set_target(shoot, 3)
    pipeline.confirm_brief(shoot)
    pipeline.review(shoot, rev)
    assert rev.calls == 0  # the count alone needs no new reviews
    picks = _picks(shoot)
    assert len(picks) == 3 and {"IMG_500_wide.jpg", "IMG_501_tight.jpg"} <= picks
    bumped = [p for p in shoot.photos if p["why"] == "Made room for a frame from your shot list"]
    assert len(bumped) == 1 and bumped[0]["status"] == "below"


def test_a_frame_you_remove_is_not_put_back(shoot_dir):
    shoot, rev = _cull(shoot_dir)
    wide = _photo(shoot, "IMG_500")
    pipeline.set_override(shoot, wide["id"], "unpick")
    assert wide["status"] != "pick"
    assert pipeline.state(shoot)["coverage"][0] == {"shot": "Wide of the venue", "picks": [], "others": [wide["id"]]}


def test_a_new_shot_reviews_again_and_a_dropped_one_does_not(shoot_dir):
    shoot, rev = _cull(shoot_dir)
    pipeline.set_shots(shoot, SHOTS[:2])
    est = pipeline.brief_estimate(shoot, MODEL)
    assert est["moments"] == 0 and est["requests"] == 1  # only the final edit
    pipeline.confirm_brief(shoot)
    pipeline.review(shoot, rev)
    assert rev.calls == 0 and [c["shot"] for c in pipeline.state(shoot)["coverage"]] == SHOTS[:2]
    assert _photo(shoot, "IMG_500")["status"] == "pick"  # its tag outlives the edit to the list

    pipeline.set_shots(shoot, SHOTS[:2] + ["The speeches"])
    assert pipeline.brief_estimate(shoot, MODEL)["moments"] == len(shoot.moments) - 1  # one moment has no usable frame
    pipeline.confirm_brief(shoot)
    pipeline.review(shoot, rev)
    assert rev.calls > 0 and "3. The speeches" in rev.review_briefs[-1]


def test_the_final_edit_is_told_which_frames_are_listed_shots(monkeypatch):
    rev = review.ClaudeReviewer("k")
    sent = {}

    def fake(system, messages, schema, **kw):
        sent["messages"] = messages
        return {"ranking": [], "keep_count": 1, "unmet": []}, {}

    monkeypatch.setattr(rev, "_call", fake)
    monkeypatch.setattr(review, "_image", lambda jpeg: {"type": "text", "text": "img"})
    rev.final_edit([{"id": 1, "moment": 1, "final": 5.0, "shots": ["The cake"]}, {"id": 2, "moment": 2, "final": 8.0}],
                   lambda p: b"")
    texts = [c["text"] for c in sent["messages"][0]["content"]]
    assert "Photo id=1, moment 1, score 5.0, shows from the shot list: The cake" in texts
    assert "Photo id=2, moment 2, score 8.0" in texts
    assert "shots" in review.OUTPUT_SCHEMA["properties"]["photos"]["items"]["required"]
    assert "shots" in review.BRIEF_CHAT_SCHEMA["required"]


def test_reasons_name_files_not_the_ids_claude_was_shown(shoot_dir):
    from photocull.engine.store import Store
    shoot = pipeline.analyze(shoot_dir, Store(shoot_dir))
    first, second = shoot.photos[0]["file"], shoot.photos[1]["file"]
    assert pipeline.readable("Sharper than #2; repeats #1 and photo id=2", shoot) == (
        f"Sharper than {second}; repeats {first} and {second}")
    assert pipeline.readable("Rank #999 stays, 4:5 crop, +300K", shoot) == "Rank #999 stays, 4:5 crop, +300K"
    assert pipeline.readable("", shoot) == "" and pipeline.readable(None, shoot) is None
    shown = pipeline.state(shoot)["photos"]
    assert not any(re.search(r"#\d", p["why"] or "") for p in shown)  # Goodframes' own notes name files too


def test_a_reviewed_reject_picked_by_hand_does_not_break_the_estimate(shoot_dir):
    shoot, _ = _cull(shoot_dir)
    blurry = _photo(shoot, "IMG_103")
    assert blurry["status"] == "rejected" and blurry["reject"]  # failed the technical check
    # An older cull reviewed it anyway; then the photographer picked it.
    shoot.store.put_reviews({blurry["fp"]: {"score": 6.0, "reject": False, "reason": "Soft but warm", "best": True}},
                            MODEL, review.RUBRIC_VERSION)
    pipeline.set_override(shoot, blurry["id"], "pick")
    assert blurry["status"] == "pick" and blurry["final"] is None
    assert pipeline.review_estimate(shoot, MODEL)["requests"] >= 0  # once raised TypeError on the missing score
    assert blurry["id"] in [p["id"] for p in pipeline.state(shoot)["photos"] if p["status"] == "pick"]
