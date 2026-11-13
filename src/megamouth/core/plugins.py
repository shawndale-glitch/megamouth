"""Plugin contracts and discovery (ARCHITECTURE §4).

The core knows nothing about spreadsheets, BPMN or video. Every source family is
an IngestPlugin found through the `megamouth.ingest` entry-point group; built-ins
register themselves the same way in pyproject.toml.
"""
from __future__ import annotations

import importlib
import shutil
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable

from megamouth.core.evidence import Evidence, EvidenceStore, SourceRef


@dataclass
class Requirement:
    """Something a plugin needs. `megamouth doctor` checks every one."""

    kind: str          # "python" | "binary" | "env"
    name: str
    hint: str = ""
    optional: bool = False

    def met(self) -> bool:
        import os

        if self.kind == "python":
            return importlib.util.find_spec(self.name) is not None
        if self.kind == "binary":
            return shutil.which(self.name) is not None
        if self.kind == "env":
            return bool(os.environ.get(self.name))
        return False


@dataclass
class Ctx:
    store: EvidenceStore
    workspace: Path
    cloud: bool = False
    log: list[str] = field(default_factory=list)


@runtime_checkable
class IngestPlugin(Protocol):
    name: str
    version: str
    kind: str                         # SourceRef.kind it produces
    requires: list[Requirement]

    def sniff(self, path: Path) -> float: ...          # 0..1: how sure it can read this
    def ingest(self, path: Path, source: SourceRef, ctx: Ctx) -> Iterable[Evidence]: ...


def ingesters() -> list[IngestPlugin]:
    out = []
    for ep in entry_points(group="megamouth.ingest"):
        out.append(ep.load()())
    return sorted(out, key=lambda p: p.name)


def pick(path: Path, plugins: list[IngestPlugin]) -> IngestPlugin | None:
    scored = [(p.sniff(path), p) for p in plugins]
    scored = [s for s in scored if s[0] > 0]
    return max(scored, key=lambda s: s[0])[1] if scored else None
