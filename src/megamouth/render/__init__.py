"""Back-ends (ARCHITECTURE §4 `render/`): outputs are throw-away and always rebuilt.

Every renderer is a plugin found through the `megamouth.render` entry-point group
and writes into one static site, build/site/, so the outputs link to each other:

    build/site/index.html                    process explorer (render.explorer)
    build/site/replay/<proc>.<lang>.html     click-along replay (render.replay)
    build/site/media/<proc>.<lang>.mp4       smart-zoom video   (render.motion)
    build/site/media/<proc>.<lang>.<S>.gif   one GIF per step   (render.motion)

Renderers read the Living Spec plus grounding evidence (where each control was
on screen, when each step started, the recording). They never invent UI pixels.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import Protocol

from megamouth.core.evidence import Evidence, EvidenceStore
from megamouth.core.spec import LivingSpec
from megamouth.core.workspace import Workspace


@dataclass
class GroundedRun:
    """The latest grounding run of one procedure in one language, with its media."""
    procedure: str
    lang: str
    passed: bool
    viewport: dict
    captions: bool
    media: dict[str, Path]
    steps: list[dict]          # ground.step data, in run order: step, status, box, start, voice, shot, action


@dataclass
class RenderCtx:
    ws: Workspace
    spec: LivingSpec
    site: Path
    runs: dict[tuple[str, str], GroundedRun]
    log: list[str] = field(default_factory=list)

    def cached(self, out: Path, key_parts: list) -> bool:
        """Content-addressed skip: True if `out` was built from exactly these inputs."""
        key = hashlib.sha256(json.dumps(key_parts, sort_keys=True, default=str).encode()).hexdigest()
        stamp = out.with_name(out.name + ".key")
        if out.exists() and stamp.exists() and stamp.read_text() == key:
            return True
        self._pending = (stamp, key)
        return False

    def done(self) -> None:
        stamp, key = self._pending
        stamp.write_text(key)


class RenderPlugin(Protocol):
    name: str
    order: int                 # renderers run in order; the explorer last, so it can link the others

    def run(self, ctx: RenderCtx) -> list[Path]: ...


def renderers() -> dict[str, RenderPlugin]:
    out = {ep.name: ep.load()() for ep in entry_points(group="megamouth.render")}
    return dict(sorted(out.items(), key=lambda kv: kv[1].order))


def grounded_runs(store: EvidenceStore) -> dict[tuple[str, str], GroundedRun]:
    runs: dict[tuple[str, str], Evidence] = {}
    steps: dict[tuple[str, str], list[dict]] = {}
    for a in store.current("ground."):
        key = (a.data["procedure"], a.data["lang"])
        if a.kind == "ground.run":
            runs[key] = a
        else:
            steps.setdefault(key, []).append({**a.data, "shot_path": store.blob_path(a.data["shot"]) if a.data.get("shot") else None})
    out = {}
    for key, r in runs.items():
        out[key] = GroundedRun(
            procedure=key[0], lang=key[1], passed=r.data["passed"],
            viewport=r.data.get("viewport") or {"width": 1280, "height": 720},
            captions=r.data.get("captions", True),
            media={k: Path(v) for k, v in (r.data.get("media") or {}).items() if Path(v).exists()},
            steps=sorted(steps.get(key, []), key=lambda s: (s.get("start") is None, s.get("start") or 0)))
    return out


def render(ws: Workspace, spec: LivingSpec, names: list[str] | None = None) -> tuple[list[Path], list[str]]:
    site = ws.root / "build" / "site"
    site.mkdir(parents=True, exist_ok=True)
    ctx = RenderCtx(ws, spec, site, grounded_runs(ws.store))
    written: list[Path] = []
    for name, plugin in renderers().items():
        if names and name not in names:
            continue
        written += plugin.run(ctx)
    return written, ctx.log
