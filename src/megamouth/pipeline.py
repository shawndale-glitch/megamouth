"""Shared pipeline steps used by the CLI, `watch`, `propose` and the review console."""
from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

from ruamel.yaml import YAML

from megamouth.core import spec as specmod
from megamouth.core.llm import Gateway
from megamouth.core.spec import LivingSpec
from megamouth.core.workspace import Workspace
from megamouth.synth.build import synthesize

OVERLAY = Path("spec/overlay.yaml")


def spec_id(ws: Workspace, override: str | None = None) -> str:
    return override or ws.config.get("spec", {}).get("id", "living-spec")


def spec_title(ws: Workspace, override: str | None = None) -> str:
    return override or ws.config.get("spec", {}).get("title", "Living Spec")


def proposed_path(ws: Workspace, sid: str) -> Path:
    return ws.root / "spec" / "proposed" / f"{sid}.yaml"


def load_overlay(ws: Workspace) -> dict | None:
    p = ws.root / OVERLAY
    return YAML(typ="safe").load(p.read_text(encoding="utf-8")) if p.exists() else None


def gateway(ws: Workspace) -> Gateway:
    return Gateway(ws.root, model=ws.llm.get("model", "claude-opus-5-5"),
                   effort=ws.llm.get("effort", "medium"), mode=ws.llm.get("mode", "auto"))


def synth(ws: Workspace, sid: str | None = None, title: str | None = None, vlm: bool = False,
          out: Path | None = None) -> tuple[LivingSpec, Path]:
    sid = spec_id(ws, sid)
    s = synthesize(ws.store, title=spec_title(ws, title), spec_id=sid, languages=ws.languages,
                   environment=ws.environment, overlay=load_overlay(ws), gateway=gateway(ws), vlm=vlm)
    dest = out or proposed_path(ws, sid)
    specmod.save(s, dest)
    return s, dest


# ------------------------------------------------------------------ git helpers
def git(ws: Workspace, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ws.root), *args], capture_output=True, text=True, check=check)


def committed_spec(ws: Workspace, path: Path, rev: str = "HEAD") -> LivingSpec | None:
    """The spec as last committed, to diff a fresh synthesis against."""
    try:
        top = Path(git(ws, "rev-parse", "--show-toplevel").stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    rel = path.resolve().relative_to(top.resolve()).as_posix()
    r = git(ws, "show", f"{rev}:{rel}", check=False)
    if r.returncode != 0:
        return None
    return LivingSpec.model_validate(YAML(typ="safe").load(r.stdout))


def propose(ws: Workspace, spec: LivingSpec, path: Path, body: str, push: bool, pr: bool) -> str:
    """Commit the proposed spec (and run records) on a new branch; optionally push and open a PR.
    Synthesis never merges by itself (ARCHITECTURE §7): a person merges the PR."""
    branch = f"megamouth/{spec.meta.id}-{datetime.now():%Y%m%d-%H%M%S}"
    git(ws, "switch", "-c", branch)
    paths = [str(path)] + [str(p) for p in (ws.root / "runs").glob("*.ground.json")]
    git(ws, "add", "--", *paths)
    title = f"megamouth: update {spec.meta.id} spec"
    git(ws, "commit", "-m", f"{title}\n\n{body}")
    if push or pr:
        git(ws, "push", "-u", "origin", branch)
    if pr:
        subprocess.run(["gh", "pr", "create", "--title", title, "--body", body, "--head", branch],
                       cwd=ws.root, check=True)
    return branch
