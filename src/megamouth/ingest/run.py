"""Ingest driver: files → SourceRefs → plugins → evidence store."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from megamouth.core.evidence import SourceRef, sha256_file
from megamouth.core.plugins import Ctx, ingesters, pick
from megamouth.core.workspace import Workspace


@dataclass
class IngestReport:
    added: list[tuple[str, str, int]] = field(default_factory=list)   # (source id, plugin, atoms)
    unchanged: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)


def source_id(path: Path) -> str:
    stem = path.name
    for suf in (".board.json", ".json", ".bpmn"):
        if stem.endswith(suf):
            stem = stem[: -len(suf)]
            break
    else:
        stem = path.stem
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")


def _rel(path: Path, root: Path) -> str:
    """Workspace-relative URI, so specs never carry a machine's home path."""
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def expand(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        if p.is_dir():
            out += sorted(q for q in p.rglob("*") if q.is_file() and not q.name.startswith("."))
        elif p.exists():
            out.append(p)
    return out


def ingest(ws: Workspace, paths: list[Path]) -> IngestReport:
    rep = IngestReport()
    plugins = ingesters()
    for path in expand(paths):
        plugin = pick(path, plugins)
        if plugin is None:
            if path.suffix.lower() not in (".vtt", ".srt"):  # sidecars ride along with their video
                rep.skipped.append(str(path))
            continue
        src = SourceRef(
            id=source_id(path), kind=plugin.kind, uri=_rel(path, ws.root), sha256=sha256_file(path),
            adapter=f"{plugin.name}@{plugin.version}", cloud=ws.policy.cloud_ok(str(path.resolve())),
        )
        # a video's sidecar subtitles are part of the source: fold their hash in
        for side in (path.with_suffix(".vtt"), path.with_suffix(".srt")):
            if plugin.kind == "video" and side.exists():
                src.sha256 = hashlib.sha256((src.sha256 + sha256_file(side)).encode()).hexdigest()
        if ws.store.has_source(src):
            rep.unchanged.append(src.id)
            continue
        ctx = Ctx(store=ws.store, workspace=ws.root, cloud=src.cloud)
        atoms = list(plugin.ingest(path, src, ctx))  # fail before recording the source, not after
        ws.store.add_source(src)
        n = ws.store.add(atoms)
        rep.added.append((src.id, plugin.name, n))
        rep.log += ctx.log
    return rep
