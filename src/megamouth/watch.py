"""`megamouth watch`: the file-drop watcher (ARCHITECTURE §3 "watchers").

Polls the inbox. When a file is added or changes it re-ingests (only that source
gets new atoms), re-synthesizes, and prints the semantic diff, so you see
"S5 changed its target; the walk-through will rebuild" seconds after saving the
SOP. With --verify it also re-runs grounding for the procedures that changed.
Polling keeps it dependency-free and works on network drives and synced folders.
Figma and ticket watchers arrive with their ingesters (P3).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from megamouth.core import spec as specmod
from megamouth.core.workspace import Workspace
from megamouth.diff import diff, to_markdown
from megamouth.ingest.run import expand, ingest
from megamouth import pipeline


def snapshot(paths: list[Path]) -> dict[str, tuple[float, int]]:
    out = {}
    for p in expand(paths):
        st = p.stat()
        out[str(p)] = (st.st_mtime, st.st_size)
    return out


def tick(ws: Workspace, inbox: list[Path], verify: bool, say: Callable[[str], None]) -> bool:
    """One pass: ingest, and if anything changed, synthesize and report. Returns True if it changed."""
    rep = ingest(ws, inbox)
    if not rep.added:
        return False
    for sid, plugin, n in rep.added:
        say(f"ingested {sid} ({plugin}, {n} atoms)")
    path = pipeline.proposed_path(ws, pipeline.spec_id(ws))
    before = specmod.load(path) if path.exists() else None
    spec, path = pipeline.synth(ws)
    d = diff(before, spec) if before else None
    if verify and d and d.rebuild:
        from megamouth.verify import verify as run_verify

        vr = run_verify(ws, spec, procedures=d.rebuild)
        for r in vr.results:
            say(f"grounding {r.procedure} [{r.lang}]: {'PASS' if r.passed else 'FAIL'} {r.detail}")
        spec, path = pipeline.synth(ws)
        d = diff(before, spec)
    say(to_markdown(d, spec) if d else f"first synthesis written to {path}")
    return True


def watch(ws: Workspace, inbox: list[Path], interval: float, verify: bool, once: bool,
          say: Callable[[str], None]) -> None:
    last: dict = {}
    while True:
        snap = snapshot(inbox)
        if snap != last:
            tick(ws, inbox, verify, say)
            last = snap
        if once:
            return
        time.sleep(interval)
