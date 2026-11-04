import hashlib
import os
import shutil

import numpy as np
from conftest import FakeReviewer, _scene, write_dng

from photocull.engine import evaluate, export, ingest, pipeline, review, scoring
from photocull.engine.store import Store


def _analyze(folder):
    return pipeline.analyze(folder, Store(folder))


def _by_file(shoot):
    return {p["file"]: p for p in shoot.photos}


def test_technical_pass(shoot_dir):
    f = _by_file(_analyze(shoot_dir))
    assert "eyes closed" in f["IMG_701_portrait.jpg"]["why"]
    burst = [f[f"IMG_{100 + i}.jpg"] for i in range(5)]
    assert 2 <= sum(p["status"] != "rejected" for p in burst) <= scoring.MAX_PER_BURST
    for i in (0, 3):  # far softer than their siblings
        assert burst[i]["status"] == "rejected"
    assert burst[4]["status"] == "candidate" and burst[4]["focus_penalty"] > 0  # softer, but Claude judges it
    assert f["IMG_200_blurred.jpg"]["status"] == "candidate" and f["IMG_200_blurred.jpg"]["extreme"]  # intent?
    assert "blown highlights" in f["IMG_400_overexposed.jpg"]["why"]


def _photo(i, sharp=500.0, technical=0.8, faces=(), reject=None, extreme=False):
    m = {"sharpness": sharp, "exposure_score": 1.0, "faces": [{"eyes_found": e, "sharpness": sharp} for e in faces]}
    return {"id": i, "metrics": m, "technical": technical, "reject": reject, "extreme": extreme,
            "sharp_in_group": sharp / 500.0, "status": None, "notes": []}


def test_softer_frame_with_the_better_moment_reaches_review():
    ps = [_photo(0, 500, 0.9), _photo(1, 350, 0.6), _photo(2, 480, 0.85)]
    scoring.select_in_groups(ps, [[0, 1, 2]])
    assert ps[1]["status"] == "candidate"


def test_burst_is_capped_spread_across_time_and_keeps_the_best():
    ps = [_photo(i, 500, 0.5 + i / 100) for i in range(10)]
    scoring.select_in_groups(ps, [list(range(10))])
    kept = [p["id"] for p in ps if p["status"] == "candidate"]
    assert len(kept) == scoring.MAX_PER_BURST and 9 in kept and kept[0] == 0


def test_only_unusable_frames_are_rejected_unseen(monkeypatch):
    base = {"sharpness": 500, "sharp_tile": (0, 0), "shake_anisotropy": 0, "noise_sigma": 1, "faces": [],
            "tilt_deg": None, "clip_hi": 0, "clip_lo": 0, "exposure_score": 1, "mean_luma": 120}
    silhouette = {"metrics": dict(base, mean_luma=15)}
    high_key = {"metrics": dict(base, clip_hi=0.5)}
    black = {"metrics": dict(base, mean_luma=2)}
    photos = [silhouette, high_key, black]
    scoring.score(photos, [[0], [1], [2]])
    assert silhouette["reject"] is None and silhouette["extreme"]
    assert high_key["reject"] is None and high_key["extreme"]
    assert "black" in black["reject"]


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


def test_long_moment_final_round_keeps_one(shoot_dir, monkeypatch):
    from photocull.engine import moments
    real = moments.chunks
    monkeypatch.setattr(moments, "chunks", lambda items, size=12: real(items, size=1))
    shoot = _analyze(shoot_dir)
    rev = FakeReviewer({"IMG_501": {"score": 8.5}})
    pipeline.review(shoot, rev)
    assert rev.finals >= 1  # the wide/tight reframe moment needed a final comparison
    f = _by_file(shoot)
    assert f["IMG_501_tight.jpg"]["status"] == "pick"
    assert f["IMG_500_wide.jpg"]["status"] == "similar"


def test_claude_score_drives_final_and_softness_costs_at_most_a_point(shoot_dir):
    shoot = _analyze(shoot_dir)
    pipeline.review(shoot, FakeReviewer({"IMG_600": {"score": 8.0}}))
    p = _by_file(shoot)["IMG_600.jpg"]
    assert p["final"] == 8.0
    assert 0 <= max(q["focus_penalty"] for q in shoot.photos) <= 1


def test_face_crop_comes_from_the_rotated_original(tmp_path):
    from io import BytesIO
    from PIL import Image
    img = Image.new("RGB", (4000, 2000), (0, 0, 0))
    img.paste((255, 0, 0), (0, 0, 2000, 2000))
    ex = Image.Exif()
    ex[274] = 6  # displayed rotated 90 degrees: the red half ends up on top
    img.save(tmp_path / "r.jpg", "JPEG", exif=ex, quality=95)
    shoot = pipeline.Shoot(str(tmp_path), None)
    p = {"file": "r.jpg", "metrics": {"faces": [{"box": [150, 150, 200, 200], "eyes_found": 2, "sharpness": 1}]}}
    out = Image.open(BytesIO(shoot.read_face_crop(p)))
    assert max(out.size) <= review.FACE_CROP_EDGE
    r, g, b = out.convert("RGB").getpixel((out.width // 2, out.height // 2))
    assert r > 200 and g < 60
    assert shoot.read_face_crop({"file": "r.jpg", "metrics": {"faces": []}}) is None


def _reviewed(shoot_dir, keep=None, **kw):
    shoot = _analyze(shoot_dir)
    rev = FakeReviewer({"IMG_501": {"score": 9.0}, "IMG_600": {"score": 8.0}, **kw})
    rev.keep_count = keep
    pipeline.review(shoot, rev)
    return shoot, rev


def test_final_edit_cuts_repeats_and_gives_the_reason(shoot_dir):
    shoot, rev = _reviewed(shoot_dir, keep=2)
    assert rev.final_edits == 1
    picks = [p for p in shoot.photos if p["status"] == "pick"]
    assert len(picks) == 2 and {p["rank"] for p in picks} == {1, 2}
    cut = [p for p in shoot.photos if (p["rank"] or 0) > 2]
    assert cut and all(p["status"] == "below" and p["why"].startswith("cut #") for p in cut)
    assert [p["id"] for p in export.plan(shoot)["picks"]] == [p["id"] for p in sorted(picks, key=lambda q: q["rank"])]


def test_selection_size_makes_no_api_call_and_final_edit_is_cached(shoot_dir):
    shoot, rev = _reviewed(shoot_dir, keep=1)
    assert sum(p["status"] == "pick" for p in shoot.photos) == 1
    total = shoot.selection["total"]
    shoot.store.set_meta("settings", {"selection_size": total})
    pipeline.decide(shoot)
    assert sum(p["status"] == "pick" for p in shoot.photos) == total
    assert shoot.selection["recommended"] == 1
    again = _analyze(shoot_dir)
    rev2 = FakeReviewer()
    assert pipeline.review_estimate(again, "claude-sonnet-5-5")["requests"] == 0
    pipeline.review(again, rev2)
    assert rev2.calls == 0 and rev2.final_edits == 0


def test_overrides_beat_the_final_edit(shoot_dir):
    shoot, _ = _reviewed(shoot_dir, keep=1)
    cut = next(p for p in shoot.photos if (p["rank"] or 0) > 1)
    top = next(p for p in shoot.photos if p["rank"] == 1)
    pipeline.set_override(shoot, cut["id"], "pick")
    pipeline.set_override(shoot, top["id"], "unpick")
    assert cut["status"] == "pick" and top["status"] == "below"


def test_failed_final_edit_falls_back_to_scores_and_keeps_reviews(shoot_dir):
    shoot = _analyze(shoot_dir)
    rev = FakeReviewer({"IMG_600": {"score": 8.0}})
    rev.fail_final_edit = True
    events = []
    res = pipeline.review(shoot, rev, events.append)
    assert "final edit failed" in res["errors"]
    assert any(e["type"] == "final_edit_error" for e in events)
    assert shoot.selection is None and _by_file(shoot)["IMG_600.jpg"]["status"] == "pick"
    again = _analyze(shoot_dir)  # the retry costs only the final edit
    assert pipeline.review_estimate(again, "claude-sonnet-5-5")["moments"] == 0
    assert pipeline.review_estimate(again, "claude-sonnet-5-5")["requests"] == 1


def test_eval_reports_keepers_found_and_missed(shoot_dir, tmp_path):
    shoot, _ = _reviewed(shoot_dir, keep=1)
    top = next(p for p in shoot.photos if p["status"] == "pick")
    keepers = {top["file"].rsplit(".", 1)[0].lower(), "img_600", "not_here"}
    r = evaluate.evaluate(shoot, keepers)
    assert r["found"] == 1 and r["keepers"] == 2 and r["missed"] == ["img_600"]
    assert r["unmatched"] == ["not_here"] and r["recall"] == 0.5 and r["precision"] == 1.0


# ---------- the brief ----------

MODEL = "claude-sonnet-5-5"


def _chat(shoot, rev, text="hello", **reply):
    rev.chat = reply
    return pipeline.brief_turn(shoot, rev, text)


def _full_cull(shoot_dir, **reply):
    """A cull with a confirmed brief."""
    shoot = _analyze(shoot_dir)
    rev = FakeReviewer({"IMG_501": {"score": 9.0}, "IMG_600": {"score": 8.0}})
    _chat(shoot, rev, **reply)
    pipeline.confirm_brief(shoot)
    pipeline.review(shoot, rev)
    rev.calls = rev.final_edits = 0
    return shoot, rev


def _client():
    from fastapi.testclient import TestClient
    from photocull.server import app as server
    return server, TestClient(server.app)


def test_review_refuses_without_the_confirmed_version(shoot_dir, monkeypatch):
    server, client = _client()
    shoot = _analyze(shoot_dir)
    rev = FakeReviewer()
    monkeypatch.setattr(server, "_reviewer", lambda: rev)
    monkeypatch.setattr(server.Session, "shoot", shoot)
    monkeypatch.setattr(server.Session, "job", None)
    view = client.get("/api/brief").json()
    cost = view["estimate"]["cost"]
    assert client.post("/api/review", json={}).status_code == 409
    assert client.post("/api/review", json={"brief_version": view["version"] - 1, "expected_cost": cost}).status_code == 409
    assert client.post("/api/review", json={"brief_version": view["version"], "expected_cost": cost + 5}).status_code == 409
    assert rev.calls == 0 and not shoot.store.get_meta("brief", {}).get("applied")
    # A chat turn makes the card the user saw out of date.
    assert client.post("/api/brief/message", json={"text": "A wedding"}).status_code == 200
    assert client.post("/api/review", json={"brief_version": view["version"], "expected_cost": cost}).status_code == 409
    assert rev.calls == 0


def test_the_chat_alone_never_applies_the_brief(shoot_dir):
    shoot = _analyze(shoot_dir)
    rev = FakeReviewer()
    state = _chat(shoot, rev, priorities=["The parents"])
    assert state["draft"]["priorities"] == ["The parents"] and state["applied"] is None
    assert shoot.brief["review_prompt"] == "" and rev.calls == 0


def test_brief_reaches_both_prompts(shoot_dir):
    shoot = _analyze(shoot_dir)
    rev = FakeReviewer()
    _chat(shoot, rev, priorities=["The parents"], look="Grain is fine")
    pipeline.confirm_brief(shoot)
    pipeline.review(shoot, rev)
    assert all("The parents" in b and "Grain is fine" in b for b in rev.review_briefs)
    assert rev.edit_briefs and "The parents" in rev.edit_briefs[0]


def test_real_reviewer_sends_the_brief_text(monkeypatch):
    rev = review.ClaudeReviewer("k")
    sent = {}

    def fake(system, messages, schema, **kw):
        sent["messages"] = messages
        return {"photos": [], "best_ids": [], "ranking": [], "keep_count": 1, "unmet": []}, {}

    monkeypatch.setattr(rev, "_call", fake)
    rev.review([], lambda p: b"", brief_notes="BRIEF TEXT")
    assert "BRIEF TEXT" in sent["messages"][0]["content"][-1]["text"]
    rev.final_edit([], lambda p: b"", brief_notes="BRIEF TEXT")
    assert "BRIEF TEXT" in sent["messages"][0]["content"][-1]["text"]


def test_target_count_change_makes_no_api_call(shoot_dir):
    shoot, rev = _full_cull(shoot_dir, priorities=["The parents"])
    _chat(shoot, rev, priorities=["The parents"], target_count=1, rerun="final_edit")
    assert pipeline.brief_estimate(shoot, MODEL)["requests"] == 0
    pipeline.confirm_brief(shoot)
    pipeline.review(shoot, rev)
    assert rev.calls == 0 and rev.final_edits == 0
    assert sum(p["status"] == "pick" for p in shoot.photos) == 1


def test_rerank_correction_reruns_only_the_final_edit(shoot_dir):
    shoot, rev = _full_cull(shoot_dir, priorities=["The parents"])
    _chat(shoot, rev, priorities=["The parents", "More of the cake"], rerun="final_edit")
    est = pipeline.brief_estimate(shoot, MODEL)
    assert est["moments"] == 0 and est["requests"] == 1
    pipeline.confirm_brief(shoot)
    pipeline.review(shoot, rev)
    assert rev.calls == 0 and rev.final_edits == 1 and "More of the cake" in rev.edit_briefs[-1]


def test_new_look_reruns_every_review(shoot_dir):
    shoot, rev = _full_cull(shoot_dir, look="Clean")
    moments_reviewed = sum(bool(pipeline.candidates(shoot, m)) for m in shoot.moments)
    _chat(shoot, rev, look="Moody and dark", rerun="full_review")
    assert pipeline.brief_estimate(shoot, MODEL)["moments"] == moments_reviewed
    pipeline.confirm_brief(shoot)
    pipeline.review(shoot, rev)
    assert rev.calls == moments_reviewed and rev.final_edits == 1


def test_looser_tolerance_reviews_only_moments_with_new_frames(shoot_dir):
    shoot, rev = _full_cull(shoot_dir)
    burst = _by_file(shoot)["IMG_100.jpg"]
    assert burst["status"] == "rejected"
    _chat(shoot, rev, technical_tolerance="loose", rerun="final_edit")
    est = pipeline.brief_estimate(shoot, MODEL)
    assert burst["status"] == "rejected"  # pricing it changes nothing
    pipeline.confirm_brief(shoot)
    assert burst["status"] != "rejected" and not burst["reject"]  # old rejection isn't kept
    assert 0 < est["moments"] < len(shoot.moments)
    pipeline.review(shoot, rev)
    assert rev.calls == est["moments"]
    assert _by_file(_analyze(shoot_dir))["IMG_100.jpg"]["status"] != "rejected"  # survives reopening


def test_old_reviews_stay_valid_under_the_standard_brief(shoot_dir):
    shoot = _analyze(shoot_dir)
    pipeline.review(shoot, FakeReviewer())  # no brief at all, like a shoot from before this feature
    assert pipeline.review_estimate(_analyze(shoot_dir), MODEL)["requests"] == 0
    again = _analyze(shoot_dir)
    pipeline.standard_brief(again)
    assert pipeline.brief_estimate(again, MODEL)["requests"] == 0


def test_several_per_moment_can_select_non_best_frames(shoot_dir):
    shoot, rev = _full_cull(shoot_dir)
    _chat(shoot, rev, per_moment="few", rerun="final_edit")
    pipeline.confirm_brief(shoot)
    pipeline.review(shoot, rev)
    f = _by_file(shoot)
    assert rev.calls == 0 and f["IMG_500_wide.jpg"]["status"] == "pick" and f["IMG_501_tight.jpg"]["status"] == "pick"


def test_overrides_survive_a_correction(shoot_dir):
    shoot, rev = _full_cull(shoot_dir, look="Clean")
    wide = _by_file(shoot)["IMG_500_wide.jpg"]
    pipeline.set_override(shoot, wide["id"], "pick")
    _chat(shoot, rev, look="Moody", rerun="full_review")
    pipeline.confirm_brief(shoot)
    pipeline.review(shoot, rev)
    assert wide["status"] == "pick"


def _picks(shoot):
    return {p["file"] for p in shoot.photos if p["status"] == "pick"}


def test_failed_correction_keeps_the_previous_results(shoot_dir):
    shoot, rev = _full_cull(shoot_dir, priorities=["The parents"])
    before = _picks(shoot)
    _chat(shoot, rev, priorities=["The cake"], rerun="final_edit")
    pipeline.confirm_brief(shoot)
    assert _picks(shoot) == before  # still showing until the new run succeeds
    rev.fail_final_edit = True
    assert pipeline.review(shoot, rev)["errors"]
    assert _picks(shoot) == before and shoot.selection
    rev.fail_final_edit = False
    assert pipeline.brief_estimate(shoot, MODEL)["requests"] == 1  # the retry costs only the final edit

    _chat(shoot, rev, look="Moody", rerun="full_review")
    pipeline.confirm_brief(shoot)
    rev.fail_reviews = True
    finals = rev.final_edits
    assert pipeline.review(shoot, rev)["errors"]
    assert rev.final_edits == finals and _picks(shoot) == before


def test_unmet_priorities_are_reported(shoot_dir):
    shoot = _analyze(shoot_dir)
    rev = FakeReviewer()
    rev.unmet = ["First dance"]
    _chat(shoot, rev, priorities=["First dance"])
    pipeline.confirm_brief(shoot)
    pipeline.review(shoot, rev)
    assert pipeline.state(shoot)["unmet"] == ["First dance"]


def test_style_is_saved_only_when_approved_and_reaches_the_next_shoot(shoot_dir):
    from photocull.engine import taste
    shoot = _analyze(shoot_dir)
    rev = FakeReviewer()
    _chat(shoot, rev, style_suggestions=["Likes grain", "Likes blur"])
    assert taste.load_style() == []
    pipeline.confirm_brief(shoot, ["Likes grain", "Something the chat never suggested"])
    assert taste.load_style() == ["Likes grain"]
    pipeline.review(shoot, rev)
    assert not rev.review_briefs[0]  # already in this brief's text if it belongs there
    # Saving it doesn't make this shoot pay again.
    pipeline.standard_brief(shoot)
    assert pipeline.brief_estimate(shoot, MODEL)["moments"] == 0
    # A new shoot's standard brief carries it.
    other = _analyze(shoot_dir)
    other.store.set_meta("brief", {})
    other.brief = pipeline.brief.applied(None)
    pipeline.standard_brief(other)
    pipeline.confirm_brief(other)
    assert "Likes grain" in other.brief["review_prompt"]


def test_failed_chat_turn_saves_nothing(shoot_dir):
    shoot = _analyze(shoot_dir)

    class Failing(FakeReviewer):
        def brief_chat(self, messages, sample, context):
            raise review.ReviewError("no")

    try:
        pipeline.brief_turn(shoot, Failing(), "hello")
    except review.ReviewError:
        pass
    state = pipeline.brief.load(shoot.store)
    assert state["messages"] == [] and state["version"] == 1


def test_chat_sends_a_sample_once_cached_and_alternates_turns(monkeypatch):
    rev = review.ClaudeReviewer("k")
    sent = {}

    def fake(system, messages, schema, **kw):
        sent.update(messages=messages, kw=kw)
        return {}, {}

    monkeypatch.setattr(rev, "_call", fake)
    rev.brief_chat([{"role": "assistant", "text": "What's this?"}, {"role": "user", "text": "A wedding"}],
                   [b"x"], "Current brief: {}")
    first = sent["messages"][0]["content"]
    assert first[0]["cache_control"] == {"type": "ephemeral"} and first[0]["type"] == "image"
    assert sent["messages"][-1]["content"].startswith("A wedding") and "Current brief" in sent["messages"][-1]["content"]
    assert sent["kw"]["effort"] == "low"


def test_api_key_is_read_from_the_keychain_once(monkeypatch):
    from photocull import settings
    reads, stored = [], {"key": "sk-one"}
    monkeypatch.setattr(settings, "_cached_key", None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(settings.keyring, "get_password", lambda *a: reads.append(a) or stored["key"])
    monkeypatch.setattr(settings.keyring, "set_password", lambda *a: None)
    monkeypatch.setattr(settings.keyring, "delete_password", lambda *a: stored.update(key=None))
    assert settings.get_api_key() == settings.get_api_key() == "sk-one"
    assert len(reads) == 1
    settings.set_api_key("sk-two")
    assert settings.get_api_key() == "sk-two" and len(reads) == 1
    settings.set_api_key(None)
    assert settings.get_api_key() is None


# ---------- two-tier review ----------

def test_two_tier_rechecks_only_close_calls_with_the_main_model(shoot_dir):
    shoot = _analyze(shoot_dir)
    # IMG_600 is clearly strong, IMG_300 clearly weak, IMG_501 sits near the line for the final edit.
    rules = {"IMG_600": {"score": 9.0}, "IMG_300": {"score": 3.0}, "IMG_501": {"score": 6.2},
             "IMG_500": {"score": 3.0}, "IMG_10": {"score": 3.0}, "IMG_200": {"score": 3.0},
             "IMG_70": {"score": 3.0}}
    screener = FakeReviewer(rules, model="claude-haiku-5-5")
    main = FakeReviewer(rules)
    res = pipeline.review(shoot, main, screener=screener)
    moments = sum(bool(pipeline.candidates(shoot, m)) for m in shoot.moments)
    assert screener.calls == moments
    assert main.calls == res["close_calls"] == 1 and main.final_edits == 1 and screener.final_edits == 0
    reviews = shoot.store.reviews()
    f = _by_file(shoot)
    assert reviews[f["IMG_501_tight.jpg"]["fp"]]["model"] == "fake-model"
    assert reviews[f["IMG_600.jpg"]["fp"]]["model"] == "claude-haiku-5-5"


def test_near_tied_best_frames_are_a_close_call(shoot_dir):
    shoot = _analyze(shoot_dir)
    wide, tight = _by_file(shoot)["IMG_500_wide.jpg"], _by_file(shoot)["IMG_501_tight.jpg"]
    for p in (wide, tight):
        p["focus_penalty"] = 0
    r = lambda s: {"score": s, "reject": False}
    assert pipeline._close_call(shoot, {wide["id"]: r(8.6), tight["id"]: r(8.4)})
    assert not pipeline._close_call(shoot, {wide["id"]: r(8.9), tight["id"]: r(7.0)})
    assert not pipeline._close_call(shoot, {wide["id"]: r(4.0), tight["id"]: r(3.9)})  # neither in contention


def test_two_tier_estimate_is_much_cheaper():
    shoot = pipeline.Shoot("x", None)
    batch = [{"width": 6000, "height": 4000, "metrics": {"faces": []}}] * 8
    single = review.estimate_cost([batch] * 20, "claude-sonnet-5-5", lambda p: (1024, 683), 20)
    screen = review.estimate_cost([batch] * 20, "claude-haiku-5-5", lambda p: (1024, 683))
    assert screen < single / 15
    assert review.estimate_cost([], "claude-sonnet-5-5", lambda p: (1024, 683)) == 0  # no system prompt charge


def test_haiku_requests_carry_no_server_side_fallback(monkeypatch):
    sent = {}

    class Response:
        stop_reason = "end_turn"
        content = [type("B", (), {"type": "text", "text": "{}"})()]
        usage = type("U", (), {"input_tokens": 1, "output_tokens": 1, "cache_creation_input_tokens": 0,
                               "cache_read_input_tokens": 0})()

    for model, expect in (("claude-haiku-5-5", False), ("claude-sonnet-5-5", True)):
        rev = review.ClaudeReviewer("k", model)
        monkeypatch.setattr(rev.client.beta.messages, "create", lambda **kw: sent.update(kw) or Response())
        sent.clear()
        rev._call("s", [{"role": "user", "content": "x"}], {})
        assert ("fallbacks" in sent) is expect and sent["model"] == model


def test_screen_model_setting():
    from photocull import settings
    assert settings.screen_model({"two_tier": True, "model": "claude-opus-5-5"}) == "claude-haiku-5-5"
    assert settings.screen_model({"two_tier": False, "model": "claude-opus-5-5"}) is None
    assert settings.screen_model({"two_tier": True, "model": "claude-haiku-5-5"}) is None


def test_close_call_sends_only_contenders_to_the_main_model(shoot_dir):
    shoot = _analyze(shoot_dir)
    burst = sorted(pipeline.candidates(shoot, next(m for m in shoot.moments if len(pipeline.candidates(shoot, m)) > 1)),
                   key=lambda p: p["id"])
    scores = {p["file"].split(".")[0]: {"score": 6.0 + k * 0.1} for k, p in enumerate(burst)}
    seen = []

    class Main(FakeReviewer):
        def review(self, photos, *a, **kw):
            seen.append([p["id"] for p in photos])
            return super().review(photos, *a, **kw)

    pipeline.review(shoot, Main(scores), screener=FakeReviewer(scores, model="claude-haiku-5-5"))
    assert seen and all(len(ids) <= pipeline.MAX_CONTENDERS for ids in seen)
    picks = [p for p in burst if p["status"] == "pick"]
    assert len(picks) <= 1 and all(shoot.store.reviews()[p["fp"]]["model"] == "fake-model" for p in picks)


def test_home_card_summary_reads_without_creating_a_store(shoot_dir, tmp_path):
    from photocull.engine.store import summary
    fresh = str(tmp_path / "never-opened")
    os.makedirs(fresh)
    assert summary(fresh)["photos"] == 0 and not os.path.exists(os.path.join(fresh, ".photo-cull"))
    shoot = _analyze(shoot_dir)
    card = summary(shoot_dir)
    assert card["photos"] == len(shoot.photos) and card["reviewed"] == 0 and len(card["covers"]) == 3
    pipeline.review(shoot, FakeReviewer(), lambda e: None)
    reviews = Store(shoot_dir).reviews()
    best = max((fp for fp, r in reviews.items() if not r.get("reject")), key=lambda fp: reviews[fp]["score"])
    card = summary(shoot_dir)
    assert card["reviewed"] > 0 and card["covers"][0] == best


def test_profile_reaches_the_brief(shoot_dir):
    from photocull.engine import brief, taste
    assert taste.load_profile()["done"] is False and taste.lasting() == []
    saved = taste.save_profile({"name": " Ana ", "genres": ["portraits", "nope"], "look": ["candid", "bogus"],
                                "keep": "tight"})
    assert saved == {"name": "Ana", "genres": ["portraits"], "look": ["candid"], "keep": "tight", "done": True}
    taste.add_style(["Keep the dog in frame"])
    lasting = taste.lasting()
    assert lasting[0] == "Mostly shoots portraits" and lasting[-1] == "Keep the dog in frame"
    shoot = _analyze(shoot_dir)
    pipeline.confirm_brief(shoot)
    assert taste.LOOKS["candid"] in shoot.brief["review_prompt"]
    assert taste.KEEP["tight"] in brief.load(shoot.store)["applied"]["edit_prompt"]


def test_raw_frames_are_analysed_upright(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOCULL_HOME", str(tmp_path / "home"))
    folder = tmp_path / "raw"
    folder.mkdir()
    write_dng(str(folder / "DSC_0001.dng"), np.asarray(_scene(7, 900, 600)), orientation=6)
    p = _analyze(str(folder)).photos[0]
    assert (p["width"], p["height"]) == (600, 900)
    assert p["taken"] == "2026-10-20T17:30:00"
    assert ingest.open_photo(str(folder / "DSC_0001.dng")).size == (600, 900)


def test_raw_plus_jpeg_is_one_frame_and_exports_together(shoot_dir):
    write_dng(os.path.join(shoot_dir, "IMG_600.dng"), np.asarray(_scene(6, 600, 400)))
    shoot = _analyze(shoot_dir)
    assert "IMG_600.jpg" in _by_file(shoot) and "IMG_600.dng" not in _by_file(shoot)
    pipeline.review(shoot, FakeReviewer())
    assert _by_file(shoot)["IMG_600.jpg"]["status"] == "pick"
    picks = os.listdir(export.export(shoot)["picks_dir"])
    jpg = next(n for n in picks if n.endswith("_IMG_600.jpg"))
    assert jpg.replace(".jpg", ".dng") in picks


def test_brief_chat_sends_images_as_images():
    """The opening turn once formatted its image blocks into a string, billing ~480k tokens of base64."""
    r = review.ClaudeReviewer("test-key")
    sent = []
    r._call = lambda system, messages, schema, **kw: sent.append(messages) or ({}, {})
    jpeg = b"\xff\xd8 not really a jpeg"
    r.brief_chat([], [jpeg, jpeg], "Current brief: {}")
    r.brief_chat([{"role": "assistant", "text": "What is it?"}, {"role": "user", "text": "A wedding"}], [jpeg], "ctx")
    for messages in sent:
        assert [b["type"] for b in messages[0]["content"]][:-1] == ["image"] * (len(messages[0]["content"]) - 1)
        assert all(isinstance(m["content"], str) for m in messages[1:])
        assert not any("base64" in m["content"] for m in messages[1:])
    assert sent[0][-1]["content"].startswith("Start the conversation.")
    assert sent[1][-1]["content"] == "A wedding\n\nctx"


def test_reject_override_and_undo_takes_the_lesson_back(shoot_dir):
    from photocull.engine import taste
    shoot = _analyze(shoot_dir)
    pipeline.review(shoot, FakeReviewer())
    p = _by_file(shoot)["IMG_600.jpg"]
    learned = len(taste.load())
    pipeline.set_override(shoot, p["id"], "reject")
    assert p["status"] == "rejected" and p["why"] == "Rejected by you"
    assert len(taste.load()) == learned + 1
    pipeline.set_override(shoot, p["id"], None, undo=True, forget=True)  # undo
    assert p["status"] == "pick" and len(taste.load()) == learned
    assert pipeline.summary(p)["best"] is True


def test_memory_cards_and_empty_folders(tmp_path, shoot_template):
    vols = tmp_path / "Volumes"
    card = vols / "EOS_DIGITAL" / "DCIM" / "100CANON"
    card.mkdir(parents=True)
    (vols / "Backup" / "stuff").mkdir(parents=True)  # a drive without DCIM isn't a card
    for n in ("IMG_100.jpg", "IMG_101.jpg"):
        shutil.copy(os.path.join(shoot_template, n), card / n)
    assert ingest.memory_cards(str(vols)) == [{"name": "EOS_DIGITAL", "photos": 2, "folders": [
        {"name": "100CANON", "folder": str(card), "photos": 2}]}]
    dcim = str(vols / "EOS_DIGITAL" / "DCIM")
    open(os.path.join(dcim, "MVI_1.MOV"), "w").close()
    info = ingest.describe_folder(dcim)
    assert info["videos"] == 1 and info["subfolders"][0]["name"] == "100CANON"


def test_unreadable_raw_is_skipped_not_shown_blank(tmp_path, monkeypatch, shoot_template):
    monkeypatch.setenv("PHOTOCULL_HOME", str(tmp_path / "home"))
    folder = tmp_path / "shoot"
    folder.mkdir()
    shutil.copy(os.path.join(shoot_template, "IMG_600.jpg"), folder / "IMG_600.jpg")
    bad = folder / "DSC_0002.dng"
    write_dng(str(bad), np.asarray(_scene(8, 600, 400)))
    with open(bad, "r+b") as f:
        f.truncate(os.path.getsize(bad) // 3)
    errors = []
    shoot = pipeline.analyze(str(folder), Store(str(folder)),
                             on_event=lambda e: e.get("error") and errors.append(e["file"]))
    assert [p["file"] for p in shoot.photos] == ["IMG_600.jpg"] and errors == ["DSC_0002.dng"]
