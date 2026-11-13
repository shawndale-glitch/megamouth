"""Workspace layout and configuration (ARCHITECTURE §9).

    megamouth.toml     config: model, cloud policy, environment
    spec/              reviewed Living Specs (git)
    spec/proposed/     synth output awaiting review (git, on a branch)
    evidence/          SQLite index + sha256 blob store (git-ignored)
    build/             outputs, always rebuilt (git-ignored)
"""
from __future__ import annotations

import fnmatch
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from megamouth.core.evidence import EvidenceStore

CONFIG = "megamouth.toml"

DEFAULT_CONFIG = """\
# Megamouth workspace configuration
[workspace]
environment = "demo"            # test | training | demo. Production is always refused.
languages = ["en"]

[llm]
model = "claude-opus-5-5"
effort = "medium"
mode = "auto"                   # auto | cloud | manual  (manual: write prompts to manual/, paste results back)

[policy]
# Which sources may be sent to a cloud model, by glob over the source path.
# Default is deny. Fictional demo data (Paydesk) is allowed; real sources stay local.
cloud_allow = ["**/paydesk/**", "**/examples/**"]
cloud_deny  = []
"""

GITIGNORE = "evidence/\nbuild/\ncache/\nmanual/\n.env\n"

_PROD = re.compile(r"(^|[.\-/])prod(uction)?([.\-/:]|$)", re.I)


@dataclass
class Policy:
    cloud_allow: list[str] = field(default_factory=list)
    cloud_deny: list[str] = field(default_factory=list)

    def cloud_ok(self, path: str) -> bool:
        p = str(path)
        if any(fnmatch.fnmatch(p, g) for g in self.cloud_deny):
            return False
        return any(fnmatch.fnmatch(p, g) for g in self.cloud_allow)


def refuse_production(url: str) -> None:
    if _PROD.search(url or ""):
        raise ValueError(f"refusing a production-looking address: {url}")


@dataclass
class Workspace:
    root: Path
    config: dict

    @classmethod
    def open(cls, root: Path) -> "Workspace":
        root = root.resolve()
        cfg_path = root / CONFIG
        if not cfg_path.exists():
            raise FileNotFoundError(f"{root} is not a Megamouth workspace (run `megamouth init {root}`)")
        return cls(root, tomllib.loads(cfg_path.read_text(encoding="utf-8")))

    @classmethod
    def init(cls, root: Path) -> "Workspace":
        root.mkdir(parents=True, exist_ok=True)
        for d in ("spec/proposed", "inbox", "evidence", "build"):
            (root / d).mkdir(parents=True, exist_ok=True)
        if not (root / CONFIG).exists():
            (root / CONFIG).write_text(DEFAULT_CONFIG, encoding="utf-8")
        gi = root / ".gitignore"
        if not gi.exists():
            gi.write_text(GITIGNORE, encoding="utf-8")
        return cls.open(root)

    @property
    def store(self) -> EvidenceStore:
        if not hasattr(self, "_store"):
            self._store = EvidenceStore(self.root / "evidence")
        return self._store

    @property
    def policy(self) -> Policy:
        p = self.config.get("policy", {})
        return Policy(p.get("cloud_allow", []), p.get("cloud_deny", []))

    @property
    def environment(self) -> str:
        env = self.config.get("workspace", {}).get("environment", "demo")
        if env not in ("test", "training", "demo"):
            raise ValueError(f"environment '{env}' is not test, training or demo")
        return env

    @property
    def languages(self) -> list[str]:
        return self.config.get("workspace", {}).get("languages", ["en"])

    @property
    def llm(self) -> dict:
        return self.config.get("llm", {})
