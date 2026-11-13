"""Review console v0 (ARCHITECTURE §4 `review/`): the human-in-the-loop gate.

A small local web app over the proposed spec. Every decision is written to
spec/overlay.yaml (comments preserved), then the spec is re-synthesized, so the
console never holds state of its own and every decision is reviewable in git:

  resolve a conflict    → overlay.resolutions[C-xxxxxx] = {choice, note, by}
  accept a review item  → overlay.accepted += [R-xxxxxx]
  correct a step        → overlay.procedures[<id>].steps[<S>] = {do / expect patch}
"""
from __future__ import annotations

import functools
import re
import subprocess
import threading
from pathlib import Path
from typing import Any

from megamouth.core import spec as specmod
from megamouth.core.workspace import Workspace
from megamouth import pipeline

BLOB = re.compile(r"^[0-9a-f]{64}\.(png|jpg|jpeg|webp)$")
STATIC = Path(__file__).with_name("console.html")


def reviewer(ws: Workspace) -> str:
    r = subprocess.run(["git", "-C", str(ws.root), "config", "user.name"], capture_output=True, text=True)
    return r.stdout.strip() or "reviewer"


# ------------------------------------------------------------------ overlay I/O (round-trip)
def _yaml():
    from ruamel.yaml import YAML

    y = YAML()
    y.preserve_quotes = True
    y.width = 120
    y.allow_unicode = True
    return y


def load_overlay_rt(ws: Workspace):
    from ruamel.yaml.comments import CommentedMap

    p = ws.root / pipeline.OVERLAY
    if not p.exists():
        return CommentedMap()
    return _yaml().load(p.read_text(encoding="utf-8")) or CommentedMap()


def save_overlay_rt(ws: Workspace, data) -> None:
    p = ws.root / pipeline.OVERLAY
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        _yaml().dump(data, f)


def record_resolution(ws: Workspace, cid: str, choice: str, note: str, by: str) -> None:
    ov = load_overlay_rt(ws)
    ov.setdefault("resolutions", {})[cid] = {"choice": choice, **({"note": note} if note else {}), "by": by}
    save_overlay_rt(ws, ov)


def record_accept(ws: Workspace, rid: str) -> None:
    ov = load_overlay_rt(ws)
    acc = ov.setdefault("accepted", [])
    if rid not in acc:
        acc.append(rid)
    save_overlay_rt(ws, ov)


def reopen(ws: Workspace, item_id: str) -> None:
    ov = load_overlay_rt(ws)
    if item_id in (ov.get("resolutions") or {}):
        del ov["resolutions"][item_id]
    if item_id in (ov.get("accepted") or []):
        ov["accepted"].remove(item_id)
    save_overlay_rt(ws, ov)


def record_step(ws: Workspace, procedure: str, step: str, patch: dict[str, Any]) -> None:
    clean = {k: v for k, v in patch.items() if k in ("do", "expect", "say", "result") and v}
    if not clean:
        raise ValueError("nothing to change")
    ov = load_overlay_rt(ws)
    steps = ov.setdefault("procedures", {}).setdefault(procedure, {}).setdefault("steps", {})
    steps[step] = {**(steps.get(step) or {}), **clean}
    save_overlay_rt(ws, ov)


# ------------------------------------------------------------------ state for the page
def state(ws: Workspace) -> dict:
    path = pipeline.proposed_path(ws, pipeline.spec_id(ws))
    s = specmod.load(path)
    store = ws.store

    def ev(eid: str) -> dict:
        e = store.get(eid)
        if e is None:
            return {"id": eid, "kind": "decision" if "overlay" in eid else "missing", "text": eid.split("#")[-1]}
        text = e.data.get("text") or e.data.get("name") or e.data.get("error") or e.data.get("status") or ""
        img = e.data.get("blob") or e.data.get("shot") or ""
        return {"id": eid, "kind": e.kind, "source": e.source_id, "locator": e.locator, "text": text, "image": img}

    ov = load_overlay_rt(ws)
    return {
        "spec": {"id": s.meta.id, "title": s.meta.title, "path": str(path.relative_to(ws.root))},
        "reviewer": reviewer(ws),
        "procedures": [{
            "id": p.id, "title": p.title, "status": p.status,
            "steps": [{
                "id": st.id, "status": st.status, "confidence": st.confidence, "say": st.say,
                "do": st.do.model_dump(mode="json", exclude_none=True) if st.do else None,
                "expect": st.expect.model_dump(mode="json", exclude_none=True) if st.expect else None,
                "result": st.result, "clip": st.media.clip if st.media else None,
                "evidence": [ev(e) for e in st.evidence],
            } for st in p.steps]} for p in s.procedures],
        "conflicts": [{**c.model_dump(mode="json"),
                       "claims": [{"value": cl.value, "evidence": [ev(e) for e in cl.evidence]} for cl in c.claims]}
                      for c in s.provenance.conflicts],
        "review": [r.model_dump(mode="json") for r in s.provenance.review],
        "accepted": list(ov.get("accepted") or []),
        "verification": [v.model_dump(mode="json") for v in s.provenance.verification],
    }


def create_app(ws: Workspace):
    from fastapi import Body, FastAPI, HTTPException
    from fastapi.responses import FileResponse, HTMLResponse

    app = FastAPI(title="Megamouth review")
    lock = threading.Lock()   # one writer at a time: overlay file, evidence store, spec file

    def locked(fn):
        @functools.wraps(fn)
        def run(*a, **k):
            with lock:
                return fn(*a, **k)
        return run

    def resynth() -> dict:
        pipeline.synth(ws)
        return state(ws)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return STATIC.read_text(encoding="utf-8")

    @app.get("/api/state")
    @locked
    def get_state():
        return state(ws)

    @app.get("/blob/{name}")
    def blob(name: str):
        if not BLOB.match(name):
            raise HTTPException(404)
        p = ws.store.blob_path(name)
        if not p.exists():
            raise HTTPException(404)
        return FileResponse(p)

    @app.post("/api/resolve")
    @locked
    def resolve(body: dict = Body(...)):
        if not re.fullmatch(r"C-[0-9a-f]{6}", body.get("id", "")):
            raise HTTPException(400, "bad conflict id")
        record_resolution(ws, body["id"], str(body.get("choice", "")), str(body.get("note", "")),
                          str(body.get("by") or reviewer(ws)))
        return resynth()

    @app.post("/api/accept")
    @locked
    def accept(body: dict = Body(...)):
        if not re.fullmatch(r"R-[0-9a-f]{6}", body.get("id", "")):
            raise HTTPException(400, "bad review id")
        record_accept(ws, body["id"])
        return resynth()

    @app.post("/api/reopen")
    @locked
    def reopen_(body: dict = Body(...)):
        reopen(ws, str(body.get("id", "")))
        return resynth()

    @app.post("/api/step")
    @locked
    def step(body: dict = Body(...)):
        try:
            record_step(ws, str(body["procedure"]), str(body["step"]), body.get("patch") or {})
        except (KeyError, ValueError) as e:
            raise HTTPException(400, str(e))
        return resynth()

    return app
