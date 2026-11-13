"""The Living Spec: the canonical, provenance-tracked model (ARCHITECTURE §3).

Every element carries `evidence` (ids of Evidence atoms) and a `status`. An element
with no evidence is an assumption and must say so (`assumption: true`).
`demoshark: 1` is a lowering target of this model, not the other way round.
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

Text = Union[str, dict[str, str]]  # one string, or one per language code


class Status(str, Enum):
    proposed = "proposed"
    reviewed = "reviewed"
    verified = "verified"
    stale = "stale"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class Element(_Model):
    id: str
    status: Status = Status.proposed
    evidence: list[str] = Field(default_factory=list)
    assumption: bool = False
    confidence: float = 1.0

    @model_validator(mode="after")
    def _provenance(self) -> "Element":
        # Principle 1: no element without evidence unless it is labelled an assumption.
        if not self.evidence and not self.assumption:
            raise ValueError(f"{self.id}: no evidence; mark it `assumption: true` or cite evidence")
        return self


# ---------------------------------------------------------------- meta & vocab
class Meta(_Model):
    id: str
    title: Text
    owner: str = ""
    version: str = "0.1.0"
    environment: Literal["test", "training", "demo"] = "demo"
    languages: list[str] = Field(default_factory=lambda: ["en"])


class GlossaryTerm(Element):
    term: str
    definition: Text
    aliases: list[str] = Field(default_factory=list)


class Actor(Element):
    name: str
    role: str = ""


class System(Element):
    name: str
    kind: Literal["web-app", "api", "batch"] = "web-app"
    base_url_env: str = ""
    base_url: str = ""            # only for demo/test systems; real ones come from env
    lang_param: str = ""
    adapter: str = ""


# ---------------------------------------------------------------- navigation
class Target(_Model):
    """How a person finds a control (DEMOShark DS4): never CSS or XPath."""

    role: str | None = None
    name: Text | None = None
    label: Text | None = None
    placeholder: Text | None = None
    text: Text | None = None
    within: "Target | None" = None


class Screen(Element):
    system: str = ""
    route_hint: str = ""
    landmarks: list[Target] = Field(default_factory=list)
    captures: list[str] = Field(default_factory=list)   # blob names


class Transition(Element):
    from_: str = Field(alias="from")
    to: str
    via: Target | None = None
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ---------------------------------------------------------------- processes
class Flow(_Model):
    to: str
    when: str = ""


class ProcessNode(Element):
    type: Literal["start", "end", "task", "gateway", "event", "data", "system", "annotation"]
    name: str = ""
    lane: str = ""
    out: list[Flow] = Field(default_factory=list)
    procedure: str = ""           # id of the executable procedure that performs this task


class Process(Element):
    name: str = ""
    lanes: list[str] = Field(default_factory=list)
    nodes: list[ProcessNode] = Field(default_factory=list)


# ---------------------------------------------------------------- procedures
class Action(_Model):
    action: Literal["open", "click", "fill", "select", "press", "wait"]
    path: str | None = None
    target: Target | None = None
    value: Text | None = None
    key: str | None = None
    seconds: float | None = None


class Expect(_Model):
    target: Target
    value: Text | None = None
    visible: bool | None = None


class MediaIntent(_Model):
    """Render intent, never pixels (principle 2)."""

    emphasis: Literal["ring", "spotlight", "none"] = "ring"
    zoom: float | None = None
    dwell: str | None = None
    clip: str | None = None       # "rec-0412@00:38.2-00:41.0"


class Step(Element):
    say: Text
    do: Action | None = None
    expect: Expect | None = None
    result: Text | None = None
    media: MediaIntent | None = None


class Procedure(Element):
    title: Text = ""
    process: str = ""
    task: str = ""                # process node it implements
    system: str = ""
    starts_from: str = ""
    fixtures: dict[str, dict[str, Any]] = Field(default_factory=dict)
    steps: list[Step] = Field(default_factory=list)
    verify: list[dict[str, Any]] = Field(default_factory=list)


class Rule(Element):
    text: Text
    applies_to: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- provenance
class Claim(_Model):
    value: Any = None
    evidence: list[str]


class Conflict(_Model):
    id: str
    about: str                    # spec path: "approve-invoice.S3.do"
    summary: str
    claims: list[Claim] = Field(default_factory=list)
    status: Literal["open", "resolved"] = "open"
    resolved_by: str = ""


class ReviewItem(_Model):
    id: str
    about: str
    reason: str                   # "low-confidence alignment", "no expect", ...


class Provenance(_Model):
    sources: list[dict[str, Any]] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    review: list[ReviewItem] = Field(default_factory=list)


class LivingSpec(_Model):
    megamouth: Literal[1] = 1
    meta: Meta
    glossary: list[GlossaryTerm] = Field(default_factory=list)
    actors: list[Actor] = Field(default_factory=list)
    systems: list[System] = Field(default_factory=list)
    screens: list[Screen] = Field(default_factory=list)
    transitions: list[Transition] = Field(default_factory=list)
    processes: list[Process] = Field(default_factory=list)
    procedures: list[Procedure] = Field(default_factory=list)
    rules: list[Rule] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=Provenance)

    # lookup ------------------------------------------------------------
    def find(self, path: str) -> Element | None:
        """Resolve `proc-id`, `proc-id.S3`, `process-id` or `process-id.node-id`."""
        head, _, tail = path.partition(".")
        for coll in (self.procedures, self.processes, self.actors, self.systems,
                     self.screens, self.rules, self.glossary):
            for el in coll:
                if el.id != head:
                    continue
                if not tail:
                    return el
                children = getattr(el, "steps", None) or getattr(el, "nodes", None) or []
                return next((c for c in children if c.id == tail), None)
        return None


# ---------------------------------------------------------------- YAML I/O
def _yaml():
    from ruamel.yaml import YAML

    y = YAML()
    y.default_flow_style = False
    y.width = 120
    y.allow_unicode = True
    return y


def to_plain(spec: LivingSpec) -> dict:
    return spec.model_dump(mode="json", by_alias=True, exclude_none=True, exclude_defaults=False)


def save(spec: LivingSpec, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write("# Megamouth Living Spec (megamouth: 1). Proposed by `megamouth synth`; review before merging.\n")
        _yaml().dump(to_plain(spec), f)


def load(path: Path) -> LivingSpec:
    with path.open(encoding="utf-8") as f:
        return LivingSpec.model_validate(_yaml().load(f))


def json_schema() -> dict:
    return LivingSpec.model_json_schema(by_alias=True)
