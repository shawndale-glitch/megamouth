"""Evidence atoms and the append-only evidence store.

An Evidence atom is one fact extracted from one place in one source, with enough
provenance to find it again: source id, locator (row, timestamp, node id), the
extractor that produced it and a confidence. Atoms are content-addressed, so
re-ingesting an unchanged source is a no-op and a changed source adds new atoms
without touching the old ones.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from pydantic import BaseModel, Field


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class SourceRef(BaseModel):
    """One ingested file or URI, pinned to its content hash."""

    id: str                       # stable, human-friendly: "sop-approve-invoice"
    kind: str                     # sheet | bpmn | figjam | video | ...
    uri: str
    sha256: str
    adapter: str = ""             # "sheets.sop@0.1"
    ingested_at: str = Field(default_factory=_now)
    cloud: bool = False           # may this source's content go to a cloud model?


class Evidence(BaseModel):
    """An atomic, provenance-tracked fact."""

    source_id: str
    locator: str                  # "row:4" | "t:00:38.200" | "node:11:244"
    kind: str                     # "sop.row" | "process.node" | "av.keyframe" | ...
    data: dict[str, Any]
    extractor: str
    confidence: float = 1.0
    source_sha256: str = ""

    @property
    def id(self) -> str:
        """`ev:<source>#<locator>@<hash8>`: readable, and unique per source version."""
        return f"ev:{self.source_id}#{self.locator}@{self.source_sha256[:8]}"


class EvidenceStore:
    """SQLite index plus a sha256-addressed blob store. Append-only."""

    def __init__(self, root: Path):
        self.root = root
        self.blobs = root / "blobs"
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(root / "index.sqlite")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS sources(
              id TEXT, sha256 TEXT, body TEXT, PRIMARY KEY(id, sha256));
            CREATE TABLE IF NOT EXISTS evidence(
              id TEXT PRIMARY KEY, source_id TEXT, source_sha256 TEXT, kind TEXT, body TEXT);
            CREATE INDEX IF NOT EXISTS ev_kind ON evidence(kind);
            """
        )

    # sources -------------------------------------------------------------
    def add_source(self, src: SourceRef) -> bool:
        """Record a source version. Returns False if this exact version is already known."""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO sources VALUES (?,?,?)", (src.id, src.sha256, src.model_dump_json())
        )
        self.db.commit()
        return cur.rowcount == 1

    def has_source(self, src: SourceRef) -> bool:
        return self.db.execute(
            "SELECT 1 FROM sources WHERE id=? AND sha256=?", (src.id, src.sha256)).fetchone() is not None

    def latest_sources(self) -> dict[str, SourceRef]:
        out: dict[str, SourceRef] = {}
        for (body,) in self.db.execute("SELECT body FROM sources ORDER BY rowid"):
            s = SourceRef.model_validate_json(body)
            out[s.id] = s  # later rows win
        return out

    # evidence ------------------------------------------------------------
    def add(self, atoms: Iterable[Evidence]) -> int:
        n = 0
        for ev in atoms:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO evidence VALUES (?,?,?,?,?)",
                (ev.id, ev.source_id, ev.source_sha256, ev.kind, ev.model_dump_json()),
            )
            n += cur.rowcount
        self.db.commit()
        return n

    def current(self, kind_prefix: str = "") -> Iterator[Evidence]:
        """Evidence from the latest version of every source, optionally filtered by kind prefix."""
        latest = {s.id: s.sha256 for s in self.latest_sources().values()}
        for (body,) in self.db.execute(
            "SELECT body FROM evidence WHERE kind LIKE ? ORDER BY rowid", (kind_prefix + "%",)
        ):
            ev = Evidence.model_validate_json(body)
            if latest.get(ev.source_id) == ev.source_sha256:
                yield ev

    def get(self, ev_id: str) -> Evidence | None:
        row = self.db.execute("SELECT body FROM evidence WHERE id=?", (ev_id,)).fetchone()
        return Evidence.model_validate_json(row[0]) if row else None

    # blobs ---------------------------------------------------------------
    def put_blob(self, data: bytes, suffix: str = "") -> str:
        digest = hashlib.sha256(data).hexdigest()
        p = self.blobs / f"{digest}{suffix}"
        if not p.exists():
            p.write_bytes(data)
        return p.name

    def blob_path(self, name: str) -> Path:
        return self.blobs / name


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)
