"""Grounding runs: prove each procedure in a real (non-production) app (principle 3).

For every procedure and language: lower to demoshark:1, run DEMOShark, and write
`runs/<procedure>.<lang>.ground.json`, which is then ingested as evidence. The
next `synth` turns that evidence into verified / stale statuses.

The runner command is configured in megamouth.toml so the same code drives the
bundled Paydesk app (`demoshark demo --spec`) or a real test environment
(`demoshark run <spec> --base-url …`):

    [ground]
    command = ["{python}", "-m", "demoshark", "run", "{spec}", "--voice", "none", "--lang", "{lang}", "--out", "{out}"]

`{python}` is $DEMOSHARK_PYTHON, else this interpreter.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from megamouth.core.spec import LivingSpec
from megamouth.core.workspace import Workspace
from megamouth.ingest.run import ingest
from megamouth.lowering import demoshark

DEFAULT_COMMAND = ["{python}", "-m", "demoshark", "run", "{spec}", "--voice", "none",
                   "--lang", "{lang}", "--out", "{out}"]


@dataclass
class GroundResult:
    procedure: str
    lang: str
    passed: bool
    detail: str = ""
    run_file: Path | None = None


@dataclass
class VerifyReport:
    results: list[GroundResult] = field(default_factory=list)
    skipped: list[tuple[str, list[str]]] = field(default_factory=list)   # (procedure, blockers)

    @property
    def ok(self) -> bool:
        return all(r.passed for r in self.results) and not self.skipped


def _command(ws: Workspace, spec_file: Path, lang: str, out: Path) -> list[str]:
    tpl = ws.config.get("ground", {}).get("command", DEFAULT_COMMAND)
    py = os.environ.get("DEMOSHARK_PYTHON", sys.executable)
    cmd = [a.format(python=py, spec=str(spec_file), lang=lang, out=str(out)) for a in tpl]
    # A clean recording (no caption bar) lets the motion renderer zoom and caption it afterwards.
    if "--captions" not in cmd and _supports_captions_flag(tuple(cmd[:4])):
        cmd += ["--captions", "none"]
    return cmd


_probe: dict[tuple, bool] = {}


def _supports_captions_flag(head: tuple) -> bool:
    """DEMOShark versions before step boxes don't know --captions; ask its --help once."""
    if "demoshark" not in head:   # a custom runner: leave its arguments alone
        return False
    if head not in _probe:
        try:
            r = subprocess.run([*head, "--help"], capture_output=True, text=True, timeout=60)
            _probe[head] = "--captions" in r.stdout
        except (OSError, subprocess.TimeoutExpired):
            _probe[head] = False
    return _probe[head]


def verify(ws: Workspace, spec: LivingSpec, procedures: list[str] | None = None,
           languages: list[str] | None = None) -> VerifyReport:
    rep = VerifyReport()
    runs_dir = ws.root / "runs"
    runs_dir.mkdir(exist_ok=True)
    build = ws.root / "build" / "ground"
    for proc in spec.procedures:
        if procedures and proc.id not in procedures:
            continue
        low = demoshark.lower(spec, proc.id)
        if low.blockers:
            rep.skipped.append((proc.id, low.blockers))
            continue
        spec_file = build / f"{proc.id}.yaml"
        demoshark.write(low, spec_file)
        wid = low.data["walkthrough"]["id"]
        for lang in languages or spec.meta.languages:
            out = build / "out"
            cmd = _command(ws, spec_file, lang, out)
            proc_run = subprocess.run(cmd, capture_output=True, text=True, cwd=ws.root)
            run_json = out / wid / lang / "run.json"
            if not run_json.exists():
                rep.results.append(GroundResult(proc.id, lang, False,
                                                f"runner produced no run.json (exit {proc_run.returncode}): "
                                                f"{(proc_run.stderr or proc_run.stdout)[-400:]}"))
                continue
            run = json.loads(run_json.read_text(encoding="utf-8"))
            rel = lambda p: os.path.relpath(p, runs_dir)   # committed run files never carry a machine path
            lang_dir = run_json.parent
            ground = {
                "megamouth_ground": 1,
                "procedure": proc.id, "lang": lang, "passed": bool(run["passed"]),
                "started": run.get("started", ""), "seconds": run.get("seconds"),
                "viewport": run.get("viewport") or {}, "captions": run.get("captions", True),
                "media": {k: rel(lang_dir / f) for k, f in (("recording", "recording.webm"),
                          ("video", "walkthrough.mp4"), ("subtitles", "walkthrough.vtt")) if (lang_dir / f).exists()},
                "runner": " ".join(a for a in cmd[1:4] if not a.startswith("/")),
                "steps": [{
                    "step": low.step_map.get(s["id"], s["id"]),
                    "lowered_id": s["id"],
                    "status": s["status"],
                    "error": s.get("error", ""),
                    "fingerprint": low.fingerprints.get(low.step_map.get(s["id"], s["id"]), ""),
                    "shot": rel(lang_dir / s["shot"]) if s.get("shot") else "",
                    "box": s.get("box"), "start": s.get("start"), "voice": s.get("voice"),
                    "action": next((x["do"]["action"] for x in low.data["steps"] if x["id"] == s["id"]), ""),
                } for s in run["steps"]],
            }
            dest = runs_dir / f"{proc.id}.{lang}.ground.json"
            dest.write_text(json.dumps(ground, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            failed = next((s for s in ground["steps"] if s["status"] == "fail"), None)
            rep.results.append(GroundResult(proc.id, lang, ground["passed"],
                                            f"failed at {failed['step']}: {failed['error']}" if failed else
                                            f"{len(ground['steps'])} steps passed", dest))
    ingest(ws, [runs_dir])
    return rep
