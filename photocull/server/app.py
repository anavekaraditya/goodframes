"""Local HTTP server: REST for actions, Server-Sent Events for live progress, static UI.

Bound to 127.0.0.1 only. The desktop launcher shows it in a native window; during
development it can be opened in any browser.
"""
import io
import json
import os
import queue
import subprocess
import threading
import webbrowser

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import settings
from ..engine import brief, export, pipeline, taste
from ..engine.ingest import describe_folder, memory_cards, open_photo
from ..engine.review import MODELS, SCREEN_MODEL, ClaudeReviewer, ReviewError, validate_key
from ..engine.store import Store, summary

UI_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui")

app = FastAPI(title="Goodframes")
DEMO = bool(os.environ.get("PHOTOCULL_DEMO"))  # dev only: fake reviews, no API key needed
app.state.pick_folder = None  # set by the desktop launcher (native folder dialog)


class Events:
    """Fan-out of pipeline events to every open SSE stream."""

    def __init__(self):
        self._subs = []
        self._lock = threading.Lock()

    def subscribe(self):
        q = queue.Queue()
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, event):
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            q.put(event)


events = Events()


class Session:
    shoot = None
    folder = None  # set as soon as a shoot is opened, so thumbnails load during analysis
    job = None  # name of the running background job, if any
    lock = threading.Lock()


def _start_job(name, fn):
    with Session.lock:
        if Session.job:
            raise HTTPException(409, f"Busy: {Session.job} is still running.")
        Session.job = name

    def run():
        try:
            fn()
        except Exception as e:  # surface any crash to the UI instead of dying silently
            events.publish({"type": "error", "error": str(e)})
        finally:
            Session.job = None
            events.publish({"type": "job_done", "job": name})

    threading.Thread(target=run, daemon=True).start()


def _need_shoot():
    if not Session.shoot:
        raise HTTPException(400, "Open a shoot first.")
    return Session.shoot


def _describe(est, s):
    model = s["model"]
    est["model"] = model
    est["model_label"] = MODELS[model][0]
    if est["two_tier"]:
        est["model_label"] = f"{MODELS[SCREEN_MODEL][0]}, and {MODELS[model][0]} for close calls"
    est["minutes"] = max(1, round(est["requests"] * 12 / 4 / 60))
    return est


def _estimate(shoot):
    s = settings.load()
    return _describe(pipeline.review_estimate(shoot, s["model"], settings.screen_model(s)), s)


def _reviewer():
    s = settings.load()
    if DEMO:
        from .demo import DemoReviewer
        return DemoReviewer()
    key = settings.get_api_key()
    if not key:
        raise HTTPException(400, "Add your Claude API key first.")
    return ClaudeReviewer(key, s["model"], s["effort"])


def _screener():
    """The first-pass reviewer of a two-tier review, or None."""
    s = settings.load()
    if not settings.screen_model(s):
        return None
    if DEMO:
        from .demo import DemoReviewer
        return DemoReviewer()
    return ClaudeReviewer(settings.get_api_key(), SCREEN_MODEL, s["effort"])


# ---------- settings ----------

class KeyIn(BaseModel):
    key: str


class SettingsIn(BaseModel):
    model: str | None = None
    theme: str | None = None
    two_tier: bool | None = None
    tile_size: int | None = None


class ShootSettingsIn(BaseModel):
    min_score: float | None = None
    selection_size: int | None = None
    gem_threshold: float | None = None


@app.get("/api/settings")
def get_settings():
    s = settings.load()
    return {
        "model": s["model"],
        "theme": s["theme"],
        "two_tier": s["two_tier"],
        "tile_size": s["tile_size"],
        "models": [{"id": k, "label": v[0], "in": v[1], "out": v[2]} for k, v in MODELS.items()],
        "has_key": DEMO or bool(settings.get_api_key()),
        "recent": _recent(),
        "taste_count": len(taste.load()),
        "style": taste.load_style(),
        "profile": taste.load_profile(),
        "desktop": app.state.pick_folder is not None,
    }


@app.post("/api/key")
def set_key(body: KeyIn):
    key = body.key.strip()
    try:
        validate_key(key, settings.load()["model"])
    except ReviewError as e:
        raise HTTPException(400, str(e))
    settings.set_api_key(key)
    return {"ok": True}


@app.delete("/api/key")
def delete_key():
    settings.set_api_key(None)
    return {"ok": True}


@app.post("/api/settings")
def post_settings(body: SettingsIn):
    if body.model and body.model not in MODELS:
        raise HTTPException(400, "Unknown model")
    if body.theme and body.theme not in settings.THEME_BACKGROUNDS:
        raise HTTPException(400, "Unknown theme")
    settings.save(**{k: v for k, v in body.model_dump().items() if v is not None})
    return get_settings()


@app.post("/api/taste/reset")
def reset_taste():
    taste.reset()
    return {"ok": True}


class StyleItemIn(BaseModel):
    item: str


@app.post("/api/style/forget")
def forget_style(body: StyleItemIn):
    taste.save_style([s for s in taste.load_style() if s != body.item])
    return {"style": taste.load_style()}


class ProfileIn(BaseModel):
    name: str = ""
    genres: list[str] = []
    look: list[str] = []
    keep: str = "balanced"


@app.post("/api/profile")
def post_profile(body: ProfileIn):
    return {"profile": taste.save_profile(body.model_dump())}


# ---------- shoots ----------

class FolderIn(BaseModel):
    folder: str


@app.post("/api/pick-folder")
def pick_folder():
    if not app.state.pick_folder:
        raise HTTPException(501, "No native dialog in browser mode; type the folder path.")
    return {"folder": app.state.pick_folder()}


@app.post("/api/shoot")
def open_shoot(body: FolderIn):
    folder = os.path.abspath(os.path.expanduser(body.folder.strip()))
    if not os.path.isdir(folder):
        raise HTTPException(400, "That folder doesn't exist.")
    Session.folder = folder

    def job():
        shoot = pipeline.analyze(folder, Store(folder), on_event=events.publish)
        if shoot.photos:  # an empty folder isn't a shoot to come back to
            settings.remember_folder(folder)
        Session.shoot = shoot
        events.publish({"type": "analyzed", "state": pipeline.state(shoot),
                        "estimate": _estimate(shoot) if shoot.photos else None,
                        "folder_info": None if shoot.photos else describe_folder(folder)})

    _start_job("analysis", job)
    return {"ok": True}


@app.get("/api/shoot")
def get_shoot():
    shoot = _need_shoot()
    return {"state": pipeline.state(shoot), "estimate": _estimate(shoot), "job": Session.job,
            "settings": dict(pipeline.DEFAULT_SETTINGS, **shoot.store.get_meta("settings", {}))}


# ---------- the brief ----------

class BriefMessageIn(BaseModel):
    text: str


def _draft_estimate(shoot):
    s = settings.load()
    return _describe(pipeline.brief_estimate(shoot, s["model"], settings.screen_model(s)), s)


def _brief_view(shoot):
    state = brief.load(shoot.store)
    return {"messages": state["messages"], "draft": state["draft"], "version": state["version"],
            "suggestions": state["suggestions"], "spent": state["spent"], "confirmed": bool(state["applied"]),
            "style": state["applied"]["style"] if state["applied"] else taste.lasting(),
            "estimate": _draft_estimate(shoot)}


def _chat_turn(shoot, text=None):
    reviewer = _reviewer()
    with Session.lock:
        if Session.job:
            raise HTTPException(409, f"Busy: {Session.job} is still running.")
        Session.job = "chat"
    try:
        pipeline.brief_turn(shoot, reviewer, text)
    except ReviewError as e:
        raise HTTPException(502, str(e))
    except (ValueError, KeyError, StopIteration):
        raise HTTPException(502, "Claude's reply couldn't be read. Try again.")
    finally:
        Session.job = None
    return _brief_view(shoot)


@app.get("/api/brief")
def get_brief():
    return _brief_view(_need_shoot())


@app.post("/api/brief/open")
def open_brief():
    """Claude's opening question. Does nothing if the conversation has already started."""
    shoot = _need_shoot()
    if brief.load(shoot.store)["messages"]:
        return _brief_view(shoot)
    return _chat_turn(shoot)


@app.post("/api/brief/message")
def brief_message(body: BriefMessageIn):
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Type a message first.")
    return _chat_turn(_need_shoot(), text[:brief.MAX_MESSAGE_CHARS])


@app.post("/api/brief/standard")
def brief_standard():
    shoot = _need_shoot()
    pipeline.standard_brief(shoot)
    return _brief_view(shoot)


class ReviewIn(BaseModel):
    brief_version: int | None = None
    expected_cost: float | None = None
    save_style: list[str] = []


@app.post("/api/review")
def start_review(body: ReviewIn):
    """The only way a cull starts: the photographer confirmed this exact brief at this cost."""
    shoot = _need_shoot()
    if Session.job:
        raise HTTPException(409, f"Busy: {Session.job} is still running.")
    if body.brief_version is None or body.brief_version != brief.load(shoot.store)["version"]:
        raise HTTPException(409, "The brief changed. Check it and confirm again.")
    est = _draft_estimate(shoot)
    if body.expected_cost is None or abs(body.expected_cost - est["cost"]) > 0.005:
        raise HTTPException(409, "The cost changed. Check it and confirm again.")
    reviewer = _reviewer() if est["requests"] else None
    screener = _screener() if est["requests"] else None
    pipeline.confirm_brief(shoot, body.save_style)
    if not est["requests"]:
        return {"started": False, "state": pipeline.state(shoot), "estimate": est}

    def job():
        pipeline.review(shoot, reviewer, on_event=events.publish, screener=screener)
        events.publish({"type": "state", "state": pipeline.state(shoot)})

    _start_job("review", job)
    return {"started": True, "state": pipeline.state(shoot), "estimate": est}


class OverrideIn(BaseModel):
    id: int
    action: str | None
    undo: bool = False
    forget: bool = False


@app.post("/api/override")
def override(body: OverrideIn):
    shoot = _need_shoot()
    if body.action not in (None, "pick", "unpick", "gem", "ungem", "reject"):
        raise HTTPException(400, "Unknown action")
    pipeline.set_override(shoot, body.id, body.action, undo=body.undo, forget=body.forget)
    return {"state": pipeline.state(shoot)}


@app.post("/api/shoot-settings")
def shoot_settings(body: ShootSettingsIn):
    shoot = _need_shoot()
    cur = shoot.store.get_meta("settings", {})
    cur.update({k: v for k, v in body.model_dump().items() if v is not None})
    shoot.store.set_meta("settings", cur)
    pipeline.decide(shoot)
    return {"state": pipeline.state(shoot)}


@app.post("/api/export")
def do_export():
    return export.export(_need_shoot())


@app.post("/api/reveal")
def reveal(body: FolderIn):
    if os.path.exists(body.folder):
        subprocess.run(["open", body.folder], check=False)
    return {"ok": True}


class UrlIn(BaseModel):
    url: str


@app.post("/api/open-url")
def open_url(body: UrlIn):
    if body.url.startswith("https://"):
        webbrowser.open(body.url)
    return {"ok": True}


# ---------- images ----------

def _recent():
    return [f for f in settings.load()["recent"] if os.path.isdir(f)]


@app.get("/api/cards")
def cards():
    """Camera cards that are plugged in, so the home screen can offer to cull them."""
    return memory_cards()


@app.get("/api/recent")
def recent():
    return [summary(f) for f in _recent()]


@app.get("/api/cover")
def cover(folder: str, fp: str):
    """A thumbnail from a recent shoot, for its card on the home screen."""
    if folder not in _recent():
        raise HTTPException(404)
    path = os.path.join(folder, ".photo-cull", "thumbs", os.path.basename(fp) + ".jpg")
    if not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


@app.get("/api/thumb/{fp}")
def thumb(fp: str):
    if not Session.folder:
        raise HTTPException(404)
    path = os.path.join(Session.folder, ".photo-cull", "thumbs", os.path.basename(fp) + ".jpg")
    if not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


@app.get("/api/crop/{pid}")
def crop(pid: int):
    """A 100% crop around the face (or the sharpest area) of the original file."""
    shoot = _need_shoot()
    p = shoot.by_id(pid)
    im = open_photo(os.path.join(shoot.folder, p["file"]))
    W, H = im.size
    s = max(W, H) / 1024
    faces = p["metrics"]["faces"]
    if faces:
        x, y, w, h = max(faces, key=lambda f: f["box"][2])["box"]
        cx, cy = (x + w / 2) * s, (y + h / 2) * s
    else:
        r, c = p["metrics"]["sharp_tile"]
        tw, th = W / 6, H / 6
        cx, cy = (c + 0.5) * tw, (r + 0.5) * th
    half = 400
    cx, cy = min(max(cx, half), W - half), min(max(cy, half), H - half)
    box = tuple(int(v) for v in (cx - half, cy - half, cx + half, cy + half))
    out = im.crop(box)
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=90)
    return Response(buf.getvalue(), media_type="image/jpeg")


# ---------- live events ----------

@app.get("/api/events")
def stream():
    q = events.subscribe()

    def gen():
        try:
            yield "retry: 2000\n\n"
            while True:
                try:
                    e = q.get(timeout=15)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(e)}\n\n"
        finally:
            events.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/")
def index():
    # The theme goes in the markup so a light-theme launch never paints dark first.
    with open(os.path.join(UI_DIR, "index.html"), encoding="utf-8") as f:
        html = f.read()
    return HTMLResponse(html.replace('<html lang="en">', f'<html lang="en" data-theme="{settings.load()["theme"]}">', 1))


app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")
