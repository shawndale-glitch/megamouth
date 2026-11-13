"""Synthesis: evidence graph → proposed Living Spec + conflicts + review queue.

Deterministic first (principle 4):
  1. processes   from process.* atoms; the same process drawn in two tools is merged
                 and every disagreement becomes a Conflict
  2. procedures  from sop.row atoms; instruction text → action/expect heuristics
  3. recordings  narration aligned to SOP steps (order-preserving); a matched step
                 gains the clip, the keyframe and the evidence; mismatches → review
  4. links       procedure ↔ process task by name
  5. overlay     human decisions from spec/overlay.yaml are applied last, so they
                 survive every re-synthesis
Model-assisted reading of keyframes is opt-in (`vlm=True`) and goes through the gateway.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from megamouth.core.evidence import Evidence, EvidenceStore, SourceRef
from megamouth.core.spec import (
    Actor, Claim, Conflict, Flow, LivingSpec, MediaIntent, Meta, Procedure, Process,
    ProcessNode, ReviewItem, Step, System, Target,
)
from megamouth.ingest.av import fmt_t
from megamouth.ingest.diagrams import slug
from megamouth.synth import align
from megamouth.synth.actions import parse_action, parse_expect


@dataclass
class SynthContext:
    store: EvidenceStore
    sources: dict[str, SourceRef]
    languages: list[str]
    environment: str
    conflicts: list[Conflict] = field(default_factory=list)
    review: list[ReviewItem] = field(default_factory=list)

    def conflict(self, about: str, summary: str, claims: list[tuple[Any, list[str]]]) -> None:
        self.conflicts.append(Conflict(id=f"C{len(self.conflicts) + 1}", about=about, summary=summary,
                                       claims=[Claim(value=v, evidence=e) for v, e in claims]))

    def flag(self, about: str, reason: str) -> None:
        self.review.append(ReviewItem(id=f"R{len(self.review) + 1}", about=about, reason=reason))


# ------------------------------------------------------------------ processes
def _process_from(atoms: list[Evidence]) -> Process:
    meta = next((a for a in atoms if a.kind == "process.meta"), None)
    pid = meta.data["process"] if meta else atoms[0].data["process"]
    nodes: dict[str, ProcessNode] = {}
    for a in atoms:
        if a.kind == "process.node":
            nodes[a.data["node"]] = ProcessNode(
                id=a.data["node"], type=a.data["type"], name=a.data["name"], lane=a.data["lane"], evidence=[a.id])
    for a in atoms:
        if a.kind == "process.edge" and a.data["from"] in nodes:
            nodes[a.data["from"]].out.append(Flow(to=a.data["to"], when=a.data.get("label", "")))
            nodes[a.data["from"]].evidence.append(a.id)
    lanes = [a.data["lane"] for a in atoms if a.kind == "process.lane"]
    formal = any(a.extractor.startswith("diagrams.bpmn") for a in atoms)
    return Process(id=pid, name=meta.data.get("name", pid) if meta else pid, lanes=lanes, confidence=1.0 if formal else 0.9,
                   nodes=list(nodes.values()), evidence=[meta.id] if meta else [atoms[0].id])


def _merge_process(primary: Process, other: Process, ctx: SynthContext) -> None:
    """Fold `other` (same process, different drawing) into `primary`, recording disagreements."""
    primary.evidence += other.evidence
    named = [n for n in primary.nodes if n.name]
    by_id = {n.id: n for n in primary.nodes}
    mapping: dict[str, str] = {}
    for n in other.nodes:
        cands = [p for p in named if p.type == n.type]
        k, s = align.best_match(n.name, [c.name for c in cands]) if n.name else (-1, 0.0)
        if k >= 0 and s >= align.THRESHOLD:
            p = cands[k]
            mapping[n.id] = p.id
            p.evidence += n.evidence
            if s < align.ACCEPT:
                ctx.flag(f"{primary.id}.{p.id}", f"low-confidence match ({s}) with '{n.name}' in another diagram")
            if n.lane and p.lane and align.similarity(n.lane, p.lane) < align.ACCEPT:
                ctx.conflict(f"{primary.id}.{p.id}.lane", f"'{p.name}' is in different lanes",
                             [(p.lane, p.evidence[:1]), (n.lane, n.evidence[:1])])
        elif n.type in ("task", "gateway"):
            ctx.conflict(f"{primary.id}.{n.id}", f"{n.type} '{n.name}' appears in only one diagram",
                         [(n.name, n.evidence[:1]), (None, primary.evidence[:1])])
    for n in primary.nodes:
        if n.type in ("task", "gateway") and n.name and n.id not in mapping.values():
            ctx.conflict(f"{primary.id}.{n.id}", f"{n.type} '{n.name}' appears in only one diagram",
                         [(n.name, n.evidence[:1]), (None, other.evidence[:1])])
    for ln in other.lanes:
        if not any(align.similarity(ln, p) >= align.ACCEPT for p in primary.lanes):
            ctx.conflict(f"{primary.id}.lanes", f"lane '{ln}' is missing from one diagram",
                         [(ln, other.evidence[:1]), (primary.lanes, primary.evidence[:1])])
    _ = by_id


def synth_processes(ctx: SynthContext) -> list[Process]:
    by_src: dict[tuple[str, str], list[Evidence]] = defaultdict(list)
    for a in ctx.store.current("process."):
        by_src[(a.source_id, a.data["process"])].append(a)
    drawn = [_process_from(v) for v in by_src.values()]
    merged: list[Process] = []
    # formal notation (BPMN) wins as the primary drawing; then the more complete one
    for p in sorted(drawn, key=lambda p: (-p.confidence, -len(p.nodes))):
        twin = next((m for m in merged if m.id == p.id or align.similarity(m.name, p.name) >= align.ACCEPT), None)
        if twin:
            _merge_process(twin, p, ctx)
        else:
            merged.append(p)
    return merged


# ------------------------------------------------------------------ procedures
def synth_procedures(ctx: SynthContext) -> list[Procedure]:
    groups: dict[str, list[Evidence]] = defaultdict(list)
    for a in ctx.store.current("sop.row"):
        groups[a.data["process"]].append(a)
    procs = []
    for name, rows in groups.items():
        rows.sort(key=lambda r: r.data["ordinal"])
        pid = slug(name)
        steps = []
        for i, r in enumerate(rows, start=1):
            d = r.data
            sid = f"S{i}"
            do, c1 = parse_action(d["text"], d.get("value", ""), d.get("screen", ""))
            exp, c2 = parse_expect(d.get("expected", ""), d.get("value", ""))
            if do is None:
                ctx.flag(f"{pid}.{sid}", f"could not read an action from: {d['text']!r}")
            elif c1 < align.ACCEPT:
                ctx.flag(f"{pid}.{sid}.do", f"guessed {do.action} ({c1}); confirm the control and its visible name")
            if do and do.action == "fill" and do.value is None:
                ctx.flag(f"{pid}.{sid}.do.value", "fill step with no value: add one to the SOP or the overlay")
            if exp is None and i > 1:
                ctx.flag(f"{pid}.{sid}.expect", "no checkable expectation: the run could not prove this step worked")
            steps.append(Step(
                id=sid, say=d["text"], do=do, expect=exp, result=d.get("expected") or None,
                evidence=[r.id], confidence=round(min(c1, c2) if exp else c1, 2)))
        system = next((r.data["system"] for r in rows if r.data.get("system")), "")
        procs.append(Procedure(id=pid, title=name, system=slug(system) if system else "", steps=steps,
                               evidence=[r.id for r in rows[:1]]))
    return procs


def _actors_and_systems(ctx: SynthContext, processes: list[Process]) -> tuple[list[Actor], list[System]]:
    found: dict[str, dict[str, tuple[str, list[str]]]] = {"actor": {}, "system": {}}

    def seen(kind: str, name: str, ev: str) -> None:
        found[kind].setdefault(slug(name), (name, []))[1].append(ev)

    for a in ctx.store.current("process.lane"):
        seen("actor", a.data["lane"], a.id)
    for a in ctx.store.current("sop.row"):
        if a.data.get("actor"):
            seen("actor", a.data["actor"], a.id)
        if a.data.get("system"):
            seen("system", a.data["system"], a.id)
    for a in ctx.store.current("process.node"):
        if a.data["type"] == "system" and a.data["name"]:
            seen("system", a.data["name"], a.id)
    actors = [Actor(id=k, name=n, evidence=ev[:5]) for k, (n, ev) in found["actor"].items()]
    systems = [System(id=k, name=n, evidence=ev[:5]) for k, (n, ev) in found["system"].items()]
    return actors, systems


# ------------------------------------------------------------------ recordings
def attach_recordings(ctx: SynthContext, procs: list[Procedure]) -> None:
    utt: dict[str, list[Evidence]] = defaultdict(list)
    kfs: dict[str, list[Evidence]] = defaultdict(list)
    for a in ctx.store.current("av.utterance"):
        utt[a.source_id].append(a)
    for a in ctx.store.current("av.keyframe"):
        kfs[a.source_id].append(a)
    for src, us in utt.items():
        us.sort(key=lambda u: u.data["start"])
        texts = [u.data["text"] for u in us]
        # which procedure does this recording show? the one it aligns with best
        scored = []
        for p in procs:
            pairs = align.align_sequences([str(s.say) for s in p.steps], texts)
            scored.append((sum(s for *_, s in pairs) / max(1, len(p.steps)), p, pairs))
        if not scored:
            continue
        score, proc, pairs = max(scored, key=lambda t: t[0])
        if score < 0.3:
            ctx.flag(f"recording:{src}", f"recording does not match any SOP procedure well (best {score:.2f})")
            continue
        matched_steps = {i for i, _, _ in pairs}
        matched_utts = {j for _, j, _ in pairs}
        frames = sorted(kfs.get(src, []), key=lambda k: k.data["t"])
        for i, j, s in pairs:
            step, u = proc.steps[i], us[j]
            step.evidence.append(u.id)
            start, end = u.data["start"], u.data["end"]
            step.media = MediaIntent(clip=f"{src}@{fmt_t(start)}-{fmt_t(end)}")
            # the screen as it looked while this step was narrated: last keyframe at or before the end
            frame = next((k for k in reversed(frames) if k.data["t"] <= end + 0.05), None)
            if frame:
                step.evidence.append(frame.id)
            if s < align.ACCEPT:
                ctx.flag(f"{proc.id}.{step.id}", f"recording narration only loosely matches ({s}): {u.data['text']!r}")
        for i, step in enumerate(proc.steps):
            if i not in matched_steps:
                ctx.flag(f"{proc.id}.{step.id}", f"not seen in recording {src}")
        for j, u in enumerate(us):
            if j not in matched_utts:
                # narrated but not in the SOP: either an extra step or the SOP order differs
                k, s = align.best_match(u.data["text"], [str(st.say) for st in proc.steps])
                if s >= align.THRESHOLD:
                    st = proc.steps[k]
                    ctx.conflict(f"{proc.id}.{st.id}.order",
                                 f"recording {src} does this step in a different order than the SOP",
                                 [(f"SOP position {k + 1}", st.evidence[:1]), (f"recording at {fmt_t(u.data['start'])}", [u.id])])
                else:
                    ctx.conflict(f"{proc.id}", f"recording {src} shows a step the SOP lacks: {u.data['text']!r}",
                                 [(u.data["text"], [u.id]), (None, proc.evidence[:1])])


def link_tasks(ctx: SynthContext, processes: list[Process], procs: list[Procedure]) -> None:
    tasks = [(p, n) for p in processes for n in p.nodes if n.type == "task" and n.name]
    for proc in procs:
        k, s = align.best_match(str(proc.title), [n.name for _, n in tasks])
        if k < 0 or s < align.THRESHOLD:
            ctx.flag(proc.id, "procedure is not linked to any process task")
            continue
        p, n = tasks[k]
        n.procedure, proc.process, proc.task = proc.id, p.id, n.id
        if s < align.ACCEPT:
            ctx.flag(f"{p.id}.{n.id}", f"linked to procedure {proc.id} with low confidence ({s})")


# ------------------------------------------------------------------ model-assisted
class KeyframeReading(BaseModel):
    """What a VLM sees on a keyframe: the control the narration refers to."""
    screen_heading: str = Field(description="The main heading of the screen or dialog shown, verbatim")
    control_role: str = Field(description="ARIA role of the control the narrator tells the user to use: button, link, textbox, menuitem, tab, checkbox, combobox")
    control_name: str = Field(description="The control's visible name or label, verbatim from the screen")
    confident: bool


def read_keyframes(ctx: SynthContext, procs: list[Procedure], gateway) -> int:
    """Ask a VLM which control each step uses; disagreements with the SOP become conflicts."""
    from megamouth.core.llm import CloudRefused, ManualPending

    asked = 0
    for proc in procs:
        for step in proc.steps:
            kf = next((ctx.store.get(e) for e in step.evidence if "#t:" in e and ctx.store.get(e).kind == "av.keyframe"), None)
            if not kf:
                continue
            src = ctx.sources[kf.source_id]
            prompt = (
                "This is a frame from a software training recording. The narrator says: "
                f"{step.say!r}. Identify the on-screen control the narrator is telling the user to use, "
                "and the screen's main heading. Use the exact visible text; do not guess text you cannot read."
            )
            try:
                r = gateway.structured("keyframe", prompt, KeyframeReading, [src], [ctx.store.blob_path(kf.data["blob"])])
            except (ManualPending, CloudRefused) as e:
                ctx.flag(f"{proc.id}.{step.id}", f"keyframe reading pending: {e}")
                continue
            asked += 1
            if not r.confident:
                continue
            seen = Target(role=r.control_role, name=r.control_name)
            if step.do is None or step.do.target is None:
                ctx.conflict(f"{proc.id}.{step.id}.do", "SOP has no readable action; the recording shows one",
                             [(None, step.evidence[:1]), (seen.model_dump(exclude_none=True), [kf.id])])
            else:
                said = step.do.target.name or step.do.target.label or ""
                if align.similarity(str(said), r.control_name) < align.ACCEPT:
                    ctx.conflict(f"{proc.id}.{step.id}.do.target", "SOP and recording name different controls",
                                 [(step.do.target.model_dump(exclude_none=True), step.evidence[:1]),
                                  (seen.model_dump(exclude_none=True), [kf.id])])
    return asked


# ------------------------------------------------------------------ overlay
def deep_merge(base: Any, over: Any) -> Any:
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = deep_merge(base.get(k), v) if k in base else v
        return out
    return over


def apply_overlay(spec_dict: dict, overlay: dict) -> dict:
    """Apply human decisions. Lists of elements are addressed by id: {procedures: {approve-invoice: {...}}}."""
    out = dict(spec_dict)
    for key, val in overlay.items():
        if key in ("meta",):
            out[key] = deep_merge(out.get(key, {}), val)
            continue
        if not isinstance(val, dict):
            out[key] = val
            continue
        items = {e["id"]: e for e in out.get(key, [])}
        for eid, patch in val.items():
            patch = dict(patch)
            steps_patch = patch.pop("steps", None)
            el = deep_merge(items.get(eid, {"id": eid}), patch)
            el.setdefault("evidence", [])
            el["evidence"] = list(el["evidence"]) + [f"ev:overlay#{key}.{eid}"]
            if steps_patch:
                for st in el.get("steps", []):
                    if st["id"] in steps_patch:
                        st.update(deep_merge(st, steps_patch[st["id"]]))
                        st["evidence"] = list(st.get("evidence", [])) + [f"ev:overlay#{key}.{eid}.{st['id']}"]
                        st["status"] = "reviewed"
            items[eid] = el
        out[key] = list(items.values())
    return out


# ------------------------------------------------------------------ entry point
def synthesize(store: EvidenceStore, *, title: str, spec_id: str, languages: list[str],
               environment: str, overlay: dict | None = None, gateway=None, vlm: bool = False) -> LivingSpec:
    sources = store.latest_sources()
    ctx = SynthContext(store, sources, languages, environment)
    processes = synth_processes(ctx)
    procs = synth_procedures(ctx)
    attach_recordings(ctx, procs)
    link_tasks(ctx, processes, procs)
    if vlm and gateway is not None:
        read_keyframes(ctx, procs, gateway)
    actors, systems = _actors_and_systems(ctx, processes)

    spec = LivingSpec(
        meta=Meta(id=spec_id, title=title, environment=environment, languages=languages),
        actors=actors, systems=systems, processes=processes, procedures=procs,
    )
    d = spec.model_dump(mode="json", by_alias=True, exclude_none=True)
    if overlay:
        d = apply_overlay(d, overlay)
        # an overlay can settle a review item: drop items about paths it now provides
        provided = _overlay_paths(overlay)
        ctx.review = [r for r in ctx.review if not any(r.about.startswith(p) for p in provided)]
    d["provenance"] = {
        "sources": [s.model_dump(mode="json", exclude={"ingested_at"}) for s in sources.values()],  # deterministic: re-synth gives a clean diff
        "conflicts": [c.model_dump(mode="json") for c in ctx.conflicts],
        "review": [r.model_dump(mode="json") for r in ctx.review],
    }
    return LivingSpec.model_validate(d)


def _overlay_paths(overlay: dict) -> list[str]:
    out = []
    for coll in ("procedures", "processes"):
        for eid, patch in (overlay.get(coll) or {}).items():
            for sid, sp in (patch.get("steps") or {}).items():
                for f in sp:
                    out.append(f"{eid}.{sid}.{f}")
                    if f == "do":
                        out.append(f"{eid}.{sid}.do")
                        if "value" in (sp.get("do") or {}):
                            out.append(f"{eid}.{sid}.do.value")
    return out
