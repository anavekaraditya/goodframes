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
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from pydantic import BaseModel

from .. import settings
from ..engine import export, pipeline, taste
from ..engine.review import MODELS, ClaudeReviewer, ReviewError, validate_key
from ..engine.store import Store

UI_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui")

app = FastAPI(title="PhotoCull")
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


def _estimate(shoot):
    model = settings.load()["model"]
    est = pipeline.review_estimate(shoot, model)
    est["model"] = model
    est["model_label"] = MODELS[model][0]
    est["minutes"] = max(1, round(est["requests"] * 12 / 4 / 60))
    return est


# ---------- settings ----------

class KeyIn(BaseModel):
    key: str


class SettingsIn(BaseModel):
    model: str | None = None


class ShootSettingsIn(BaseModel):
    min_score: float | None = None
    gem_threshold: float | None = None


@app.get("/api/settings")
def get_settings():
    s = settings.load()
    return {
        "model": s["model"],
        "models": [{"id": k, "label": v[0], "in": v[1], "out": v[2]} for k, v in MODELS.items()],
        "has_key": DEMO or bool(settings.get_api_key()),
        "recent": [f for f in s["recent"] if os.path.isdir(f)],
        "taste_count": len(taste.load()),
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
    settings.save(**({"model": body.model} if body.model else {}))
    return get_settings()


@app.post("/api/taste/reset")
def reset_taste():
    taste.reset()
    return {"ok": True}


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
    settings.remember_folder(folder)
    Session.folder = folder

    def job():
        shoot = pipeline.analyze(folder, Store(folder), on_event=events.publish)
        Session.shoot = shoot
        events.publish({"type": "analyzed", "state": pipeline.state(shoot),
                        "estimate": _estimate(shoot) if shoot.photos else None})

    _start_job("analysis", job)
    return {"ok": True}


@app.get("/api/shoot")
def get_shoot():
    shoot = _need_shoot()
    return {"state": pipeline.state(shoot), "estimate": _estimate(shoot), "job": Session.job,
            "settings": dict(pipeline.DEFAULT_SETTINGS, **shoot.store.get_meta("settings", {}))}


@app.post("/api/review")
def start_review():
    shoot = _need_shoot()
    key = settings.get_api_key()
    s = settings.load()
    if DEMO:
        from .demo import DemoReviewer
        reviewer = DemoReviewer()
    elif not key:
        raise HTTPException(400, "Add your Claude API key first.")
    else:
        reviewer = ClaudeReviewer(key, s["model"], s["effort"])

    def job():
        pipeline.review(shoot, reviewer, on_event=events.publish)
        events.publish({"type": "state", "state": pipeline.state(shoot)})

    _start_job("review", job)
    return {"ok": True}


class OverrideIn(BaseModel):
    id: int
    action: str | None


@app.post("/api/override")
def override(body: OverrideIn):
    shoot = _need_shoot()
    if body.action not in (None, "pick", "unpick", "gem", "ungem"):
        raise HTTPException(400, "Unknown action")
    pipeline.set_override(shoot, body.id, body.action)
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
    with Image.open(os.path.join(shoot.folder, p["file"])) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
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
    return FileResponse(os.path.join(UI_DIR, "index.html"))


app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")
