"""Grounding runs → `ground.*` evidence (ARCHITECTURE §3 "living", principle 3).

`megamouth verify` runs each lowered procedure through DEMOShark against a
non-production app and writes `runs/<procedure>.<lang>.ground.json`: DEMOShark's
run.json plus, for every step, the spec step id and its fingerprint. That file is
an ordinary source: ingesting it records what was proven, against which content.
The latest run of a procedure+language replaces the previous one (same source id).

  ground.run   {procedure, lang, passed, started, seconds, failed_at, error}
  ground.step  {procedure, lang, step, status, error, fingerprint, shot}
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from megamouth.core.evidence import Evidence, SourceRef
from megamouth.core.plugins import Ctx

MARKER = "megamouth_ground"


class GroundRunIngester:
    name = "ground.run"
    version = "0.1"
    kind = "ground"
    requires: list = []

    def sniff(self, path: Path) -> float:
        if not path.name.endswith(".ground.json"):
            return 0.0
        try:
            return 1.0 if MARKER in json.loads(path.read_text(encoding="utf-8")) else 0.0
        except Exception:
            return 0.0

    def ingest(self, path: Path, source: SourceRef, ctx: Ctx) -> Iterable[Evidence]:
        x = f"{self.name}@{self.version}"
        d = json.loads(path.read_text(encoding="utf-8"))
        proc, lang = d["procedure"], d["lang"]
        failed = next((s for s in d["steps"] if s["status"] == "fail"), None)
        ev = lambda loc, kind, data: Evidence(source_id=source.id, source_sha256=source.sha256,
                                              locator=loc, kind=kind, extractor=x, data=data)
        yield ev("run", "ground.run", {
            "procedure": proc, "lang": lang, "passed": bool(d["passed"]),
            "started": d.get("started", ""), "seconds": d.get("seconds"),
            "failed_at": failed["step"] if failed else "", "error": failed["error"] if failed else "",
            "runner": d.get("runner", "")})
        for s in d["steps"]:
            shot = ""
            if s.get("shot"):
                p = Path(s["shot"]) if Path(s["shot"]).is_absolute() else path.parent / s["shot"]
                if p.exists():
                    shot = ctx.store.put_blob(p.read_bytes(), p.suffix)
            yield ev(f"step:{s['step']}", "ground.step", {
                "procedure": proc, "lang": lang, "step": s["step"], "status": s["status"],
                "error": s.get("error", ""), "fingerprint": s["fingerprint"], "shot": shot})
