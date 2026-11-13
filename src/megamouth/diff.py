"""Semantic diff between two Living Specs (ARCHITECTURE §3 "living").

A reviewer should read "S5 now clicks Approve all instead of Approve; the
walk-through will rebuild", not a YAML diff. Each change names its impact on
outputs, so a PR says what will be re-rendered.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from megamouth.core.spec import LivingSpec, Step, Target


@dataclass
class Change:
    where: str
    what: str
    kind: str = "changed"     # added | removed | changed | status


@dataclass
class SpecDiff:
    changes: list[Change] = field(default_factory=list)
    rebuild: list[str] = field(default_factory=list)            # procedures whose outputs change
    conflicts_opened: list[str] = field(default_factory=list)
    conflicts_closed: list[str] = field(default_factory=list)
    review_added: list[str] = field(default_factory=list)
    review_cleared: list[str] = field(default_factory=list)
    open_conflicts: int = 0

    @property
    def empty(self) -> bool:
        return not (self.changes or self.conflicts_opened or self.conflicts_closed
                    or self.review_added or self.review_cleared)


def _t(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, dict):
        return " / ".join(f"{k}: {x}" for k, x in v.items())
    return str(v)


def _target(t: Target | None) -> str:
    if t is None:
        return "—"
    d = t.model_dump(exclude_none=True)
    inner = d.pop("within", None)
    s = " ".join(f"{k}={_t(v)!r}" for k, v in d.items())
    return s + (f" within ({_target(Target(**inner))})" if inner else "")


def _action(s: Step) -> str:
    if s.do is None:
        return "no action"
    bits = [s.do.action]
    if s.do.target:
        bits.append(_target(s.do.target))
    if s.do.value is not None:
        bits.append(f"value {_t(s.do.value)!r}")
    if s.do.path:
        bits.append(s.do.path)
    return " ".join(bits)


def _expect(s: Step) -> str:
    return _target(s.expect.target) + (f" = {_t(s.expect.value)!r}" if s.expect and s.expect.value else "") if s.expect else "nothing checked"


def diff(old: LivingSpec, new: LivingSpec) -> SpecDiff:
    d = SpecDiff()
    oldp = {p.id: p for p in old.procedures}
    newp = {p.id: p for p in new.procedures}
    for pid in sorted(set(oldp) | set(newp)):
        a, b = oldp.get(pid), newp.get(pid)
        if a is None:
            d.changes.append(Change(pid, f"new procedure with {len(b.steps)} steps", "added"))
            d.rebuild.append(pid)
            continue
        if b is None:
            d.changes.append(Change(pid, "procedure removed", "removed"))
            continue
        content_changed = False
        sa, sb = {s.id: s for s in a.steps}, {s.id: s for s in b.steps}
        for sid in sorted(set(sa) | set(sb), key=lambda x: (len(x), x)):
            x, y = sa.get(sid), sb.get(sid)
            w = f"{pid}.{sid}"
            if x is None:
                d.changes.append(Change(w, f"new step: {_t(y.say)!r} → {_action(y)}", "added")); content_changed = True
                continue
            if y is None:
                d.changes.append(Change(w, f"step removed: {_t(x.say)!r}", "removed")); content_changed = True
                continue
            if x.say != y.say:
                d.changes.append(Change(w, f"narration {_t(x.say)!r} → {_t(y.say)!r}")); content_changed = True
            if _action(x) != _action(y):
                d.changes.append(Change(w, f"action {_action(x)} → {_action(y)}")); content_changed = True
            if _expect(x) != _expect(y):
                d.changes.append(Change(w, f"checks {_expect(x)} → {_expect(y)}")); content_changed = True
            if x.result != y.result:
                d.changes.append(Change(w, f"result line {_t(x.result)!r} → {_t(y.result)!r}")); content_changed = True
            if x.status != y.status:
                d.changes.append(Change(w, f"{x.status} → {y.status}", "status"))
        if a.fixtures != b.fixtures or a.verify != b.verify:
            d.changes.append(Change(pid, "test data or data checks changed")); content_changed = True
        if a.status != b.status:
            d.changes.append(Change(pid, f"{a.status} → {b.status}", "status"))
        if content_changed:
            d.rebuild.append(pid)

    oldn = {(p.id, n.id): n for p in old.processes for n in p.nodes}
    newn = {(p.id, n.id): n for p in new.processes for n in p.nodes}
    for key in sorted(set(oldn) | set(newn)):
        x, y = oldn.get(key), newn.get(key)
        w = f"{key[0]}.{key[1]}"
        if x is None:
            d.changes.append(Change(w, f"new {y.type} {y.name!r}", "added"))
        elif y is None:
            d.changes.append(Change(w, f"{x.type} {x.name!r} removed", "removed"))
        else:
            if x.name != y.name:
                d.changes.append(Change(w, f"renamed {x.name!r} → {y.name!r}"))
            if x.lane != y.lane:
                d.changes.append(Change(w, f"lane {x.lane!r} → {y.lane!r}"))
            if [f.model_dump() for f in x.out] != [f.model_dump() for f in y.out]:
                d.changes.append(Change(w, "outgoing flows changed"))

    oc = {c.id: c for c in old.provenance.conflicts if c.status == "open"}
    nc = {c.id: c for c in new.provenance.conflicts if c.status == "open"}
    d.conflicts_opened = [f"{c.id} {c.about}: {c.summary}" for i, c in nc.items() if i not in oc]
    d.conflicts_closed = [f"{c.id} {c.about}: {c.summary}" for i, c in oc.items() if i not in nc]
    d.open_conflicts = len(nc)
    orv = {r.id: r for r in old.provenance.review}
    nrv = {r.id: r for r in new.provenance.review}
    d.review_added = [f"{r.id} {r.about}: {r.reason}" for i, r in nrv.items() if i not in orv]
    d.review_cleared = [f"{r.id} {r.about}: {r.reason}" for i, r in orv.items() if i not in nrv]
    return d


def to_markdown(d: SpecDiff, spec: LivingSpec) -> str:
    out = []
    if d.empty:
        return "No semantic changes.\n"
    if d.rebuild:
        langs = len(spec.meta.languages)
        out.append("### Outputs that will rebuild\n")
        out += [f"- `{p}`: walk-through video, deck and guide × {langs} language(s)" for p in d.rebuild]
        out.append("")
    content = [c for c in d.changes if c.kind != "status"]
    if content:
        out.append("### What changed\n")
        out += [f"- **{c.where}** {c.kind}: {c.what}" for c in content]
        out.append("")
    status = [c for c in d.changes if c.kind == "status"]
    if status:
        out.append("### Status\n")
        out += [f"- **{c.where}**: {c.what}" for c in status]
        out.append("")
    if d.conflicts_opened or d.conflicts_closed:
        out.append("### Conflicts\n")
        out += [f"- ❗ opened {c}" for c in d.conflicts_opened]
        out += [f"- ✅ resolved {c}" for c in d.conflicts_closed]
        out.append("")
    if d.review_added or d.review_cleared:
        out.append("### Review queue\n")
        out += [f"- [ ] {r}" for r in d.review_added]
        out += [f"- [x] ~~{r}~~" for r in d.review_cleared]
        out.append("")
    out.append(f"**{d.open_conflicts} open conflict(s)**" + (": merging is blocked until they are resolved." if d.open_conflicts else "."))
    return "\n".join(out) + "\n"


def to_json(d: SpecDiff) -> str:
    return json.dumps({**d.__dict__, "changes": [c.__dict__ for c in d.changes]}, indent=2, ensure_ascii=False)
