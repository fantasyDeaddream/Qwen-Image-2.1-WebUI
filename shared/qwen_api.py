"""Shared HTTP API + job queue for every Qwen-Image-2.1 strategy.

The Gradio WebUI and the HTTP API are served by one FastAPI app: the JSON API
lives under ``/api`` and the Gradio UI is mounted at ``/``. Both call the same
``generate`` callable, so a feature added to one is automatically present in
the other.

Job model
---------
* ``POST /api/generate``        -> queue a job, returns ``{"job_id": ...}`` immediately
* ``GET  /api/jobs/{job_id}``   -> poll status / progress / result metadata
* ``GET  /api/jobs/{job_id}/image`` -> the rendered PNG (409 until it is ready)
* ``POST /api/generate/image``  -> synchronous: blocks and returns the PNG bytes
* ``GET  /api/health``          -> liveness + strategy + device summary
* ``GET  /api/capabilities``    -> resolution rules and limits, so a client can
                                   validate its request without guessing
* ``DELETE /api/jobs/{job_id}`` -> forget a finished job

A single worker thread consumes the queue, because one GPU can only run one
diffusion pass at a time; parallel requests queue instead of fighting for VRAM.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import os
import queue
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable

from PIL import Image

# Imported at module scope on purpose: this file uses `from __future__ import
# annotations`, so every annotation is a string that FastAPI resolves against
# the module globals with typing.get_type_hints(). A function-local import of
# `Request` is invisible to that lookup, and FastAPI then treats the parameter
# as a query field instead of the request object.
from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import JSONResponse, Response  # noqa: E402

PENDING = "pending"
RUNNING = "running"
DONE = "done"
ERROR = "error"
CANCELLED = "cancelled"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def decode_image_payload(payload: str) -> Image.Image:
    """Accept a data URL or bare base64 string and return a PIL image."""
    if not isinstance(payload, str) or not payload.strip():
        raise ValueError("image payload must be a non-empty string")
    text = payload.strip()
    if text.startswith("data:"):
        _, _, text = text.partition(",")
    try:
        raw = base64.b64decode(text, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"image payload is not valid base64: {exc}") from exc
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as exc:
        raise ValueError(f"image payload is not a decodable image: {exc}") from exc
    return img


def encode_image_png(image: Image.Image) -> str:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def load_images(items) -> list:
    """Normalize a mixed list of PIL images / data URLs / bare base64 / bytes."""
    if items is None:
        return []
    if not isinstance(items, (list, tuple)):
        items = [items]
    out = []
    for item in items:
        if item is None:
            continue
        if isinstance(item, Image.Image):
            out.append(item)
            continue
        if isinstance(item, (bytes, bytearray)):
            out.append(Image.open(io.BytesIO(bytes(item))).convert("RGB"))
            continue
        if isinstance(item, str):
            out.append(decode_image_payload(item))
            continue
        raise ValueError(f"unsupported reference image type: {type(item).__name__}")
    return out


# --------------------------------------------------------------------------
# json safety
# --------------------------------------------------------------------------
def _json_safe(obj, depth: int = 0):
    """Coerce arbitrary values to JSON primitives, summarising the rest.

    Job params are echoed back through GET /api/jobs, and strategies are allowed
    to park whatever they like in there. Anything that is not a plain primitive
    is replaced by a "<Type>" marker rather than crashing the response encoder.
    """
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if depth > 6:
        return f"<{type(obj).__name__}>"
    if isinstance(obj, dict):
        return {str(k): _json_safe(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_json_safe(v, depth + 1) for v in obj]
    return f"<{type(obj).__name__}>"


# --------------------------------------------------------------------------
# job bookkeeping
# --------------------------------------------------------------------------
@dataclass
class Job:
    job_id: str
    params: dict
    status: str = PENDING
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    step: int = 0
    total_steps: int = 0
    stage: str = ""
    image_path: str | None = None
    error: str | None = None
    meta: dict = field(default_factory=dict)

    def public(self) -> dict:
        d = asdict(self)
        d["elapsed"] = (
            (self.finished or time.time()) - (self.started or self.created)
        )
        # Strategies legitimately stash non-JSON objects in params/meta -- the
        # ComfyUI path writes decoded PIL images into params["refs"]. Those used
        # to reach FastAPI's jsonable_encoder and break the *response* with
        # "dictionary update sequence element #0 has length 4", so polling
        # returned HTTP 500 while the job itself was running fine. Flatten.
        d["params"] = _json_safe(d.get("params"))
        d["meta"] = _json_safe(d.get("meta"))
        return d


class JobRunner:
    """One worker thread; jobs run strictly one at a time."""

    def __init__(self, generate_fn: Callable[..., Any], *, max_history: int = 200):
        self._generate = generate_fn
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._queue: queue.Queue[str] = queue.Queue()
        self._max_history = max_history
        self._worker = threading.Thread(target=self._loop, name="qwen-worker", daemon=True)
        self._worker.start()

    # ---- public API ------------------------------------------------------
    def submit(self, params: dict) -> str:
        job_id = uuid.uuid4().hex[:16]
        job = Job(job_id=job_id, params=params)
        with self._lock:
            self._jobs[job_id] = job
            self._order.append(job_id)
            while len(self._order) > self._max_history:
                old = self._order.pop(0)
                self._jobs.pop(old, None)
        self._queue.put(job_id)
        return job_id

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list:
        with self._lock:
            return [self._jobs[i] for i in reversed(self._order) if i in self._jobs]

    def forget(self, job_id: str) -> bool:
        with self._lock:
            if job_id in self._jobs:
                self._jobs.pop(job_id, None)
                if job_id in self._order:
                    self._order.remove(job_id)
                return True
        return False

    def queue_depth(self) -> int:
        return self._queue.qsize()

    # ---- worker ----------------------------------------------------------
    def _loop(self):
        while True:
            job_id = self._queue.get()
            job = self.get(job_id)
            if job is None:
                continue
            job.status = RUNNING
            job.started = time.time()

            def progress(step=None, total=None, stage=None):
                if step is not None:
                    job.step = int(step)
                if total is not None:
                    job.total_steps = int(total)
                if stage is not None:
                    job.stage = str(stage)

            try:
                result = self._generate(job.params, progress)
                if isinstance(result, tuple):
                    image, meta = result
                else:
                    image, meta = result, {}
                job.image_path = meta.get("image_path")
                job.meta = {k: v for k, v in meta.items() if k != "image_path"}
                job.status = DONE
            except Exception as exc:  # noqa: BLE001 - surfaced to the client
                job.status = ERROR
                job.error = f"{type(exc).__name__}: {exc}"
                job.meta["traceback"] = traceback.format_exc()[-4000:]
            finally:
                job.finished = time.time()


# --------------------------------------------------------------------------
# FastAPI app
# --------------------------------------------------------------------------
def build_app(
    *,
    strategy: str,
    runner: JobRunner,
    capabilities: dict,
    health: Callable[[], dict],
    gradio_app=None,
    title: str | None = None,
    on_shutdown: Callable[[], None] | None = None,
    on_free: Callable[[], None] | None = None,
    pid_dir: str | os.PathLike | None = None,
):
    """Create the FastAPI app, optionally with the Gradio UI mounted at '/'.

    ``on_shutdown`` is run before the process exits, so the ComfyUI backend a
    strategy spawned is torn down with it. ``on_free`` releases transient
    caches *without* unloading models -- models deliberately stay resident
    across requests, because reloading them costs far more than any cache.
    """
    app = FastAPI(title=title or f"Qwen-Image-2.1 {strategy}", version="1.0")

    # Record where we are, so stop scripts can find this process by strategy
    # name even if the port was overridden.
    if pid_dir:
        try:
            d = Path(pid_dir)
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{strategy}.pid").write_text(
                json.dumps({"pid": os.getpid(), "strategy": strategy,
                            "started": time.strftime("%Y-%m-%d %H:%M:%S")}),
                encoding="utf-8",
            )
        except OSError:
            pass

    @app.get("/api/health")
    def api_health():
        return {"ok": True, "strategy": strategy, "queue_depth": runner.queue_depth(), **health()}

    @app.get("/api/capabilities")
    def api_capabilities():
        return {"strategy": strategy, **capabilities}

    @app.post("/api/generate")
    async def api_generate(request: Request):
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"body must be JSON: {exc}") from exc
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="body must be a JSON object")
        if not str(body.get("prompt") or "").strip():
            raise HTTPException(status_code=400, detail="'prompt' is required")
        job_id = runner.submit(body)
        return {"job_id": job_id, "status": PENDING, "poll": f"/api/jobs/{job_id}"}

    @app.get("/api/jobs")
    def api_jobs(limit: int = 20):
        return {"jobs": [j.public() for j in runner.list()[: max(1, min(limit, 200))]]}

    @app.get("/api/jobs/{job_id}")
    def api_job(job_id: str):
        job = runner.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job id")
        payload = job.public()
        if job.status == DONE:
            payload["image_url"] = f"/api/jobs/{job_id}/image"
            payload["image_base64"] = encode_image_png(Image.open(job.image_path)) if job.image_path else None
        return payload

    @app.get("/api/jobs/{job_id}/image")
    def api_job_image(job_id: str):
        job = runner.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job id")
        if job.status == ERROR:
            raise HTTPException(status_code=500, detail=job.error or "generation failed")
        if job.status != DONE or not job.image_path:
            raise HTTPException(status_code=409, detail=f"job is {job.status}")
        with open(job.image_path, "rb") as fh:
            return Response(content=fh.read(), media_type="image/png")

    @app.delete("/api/jobs/{job_id}")
    def api_job_delete(job_id: str):
        if not runner.forget(job_id):
            raise HTTPException(status_code=404, detail="unknown job id")
        return {"deleted": job_id}

    @app.post("/api/generate/image")
    async def api_generate_sync(request: Request):
        """Synchronous convenience endpoint: returns the PNG directly."""
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"body must be JSON: {exc}") from exc
        if not str(body.get("prompt") or "").strip():
            raise HTTPException(status_code=400, detail="'prompt' is required")
        job_id = runner.submit(body)
        while True:
            job = runner.get(job_id)
            if job is None:
                raise HTTPException(status_code=500, detail="job vanished")
            if job.status == DONE:
                break
            if job.status == ERROR:
                raise HTTPException(status_code=500, detail=job.error or "generation failed")
            time.sleep(0.4)
        with open(job.image_path, "rb") as fh:
            headers = {"X-Job-Id": job_id, "X-Image-Path": job.image_path or ""}
            return Response(content=fh.read(), media_type="image/png", headers=headers)

    @app.post("/api/free")
    def api_free():
        """Release transient caches but keep the models loaded.

        Deliberately does *not* unload the text encoder, diffusion model or VAE:
        they stay resident between requests, because reloading 4.5 GiB of
        weights costs far more than the cache it would reclaim.
        """
        if on_free is None:
            return {"freed": False, "reason": "no cache hook for this strategy"}
        try:
            on_free()
            return {"freed": True, "models_unloaded": False}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/shutdown")
    def api_shutdown():
        """Stop this strategy cleanly: tear down children, then exit.

        The stop scripts call this, so the start script's terminal ends on
        request instead of being force-killed.
        """
        def _bye():
            time.sleep(0.6)
            print("[shutdown] requested via /api/shutdown", flush=True)
            try:
                if on_shutdown is not None:
                    on_shutdown()
            except Exception as exc:  # noqa: BLE001
                print(f"[shutdown] cleanup error: {type(exc).__name__}: {exc}", flush=True)
            finally:
                if pid_dir:
                    try:
                        (Path(pid_dir) / f"{strategy}.pid").unlink(missing_ok=True)
                    except OSError:
                        pass
                print("[shutdown] bye", flush=True)
                os._exit(0)

        threading.Thread(target=_bye, daemon=True).start()
        return {"stopping": True, "strategy": strategy}

    @app.exception_handler(Exception)
    async def unhandled(_request, exc):  # pragma: no cover - safety net
        return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})

    if gradio_app is not None:
        import gradio as gr

        gr.mount_gradio_app(app, gradio_app, path="/")
    else:
        @app.get("/")
        def root():
            return {"strategy": strategy, "api": "/api/health", "docs": "/docs"}

    return app


def serve(app, host: str, port: int, *, inbrowser: bool = False):
    import uvicorn

    if inbrowser:
        import webbrowser

        threading.Timer(1.5, lambda: webbrowser.open(f"http://{host}:{port}/")).start()
    uvicorn.run(app, host=host, port=port, log_level="info")
