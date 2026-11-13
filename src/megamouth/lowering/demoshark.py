"""Living Spec procedure → `demoshark: 1` walk-through (a lowering target, ARCHITECTURE §3).

The output is validated against DEMOShark's own schema (vendored as
demoshark.schema.json). Lowering never invents content: anything DEMOShark needs
that the spec does not have is reported as a blocker, and the file is still
written so a person can see exactly what is missing.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from megamouth.core.spec import LivingSpec, Procedure, Text
from megamouth.core.workspace import refuse_production

SCHEMA = Path(__file__).with_name("demoshark.schema.json")
PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\.(\w+)\s*\}\}")


@dataclass
class Lowered:
    data: dict
    blockers: list[str] = field(default_factory=list)   # must be fixed before DEMOShark can run it
    warnings: list[str] = field(default_factory=list)
    step_map: dict[str, str] = field(default_factory=dict)   # lowered id → spec step id
    fingerprints: dict[str, str] = field(default_factory=dict)  # spec step id → fingerprint


def _text(v: Text | None, langs: list[str]) -> Text | None:
    """DEMOShark DS2 wants every language: keep dicts, pass single strings through."""
    if v is None or isinstance(v, str):
        return v
    return {lg: v[lg] for lg in langs if lg in v} or v


def _strip(d):
    if isinstance(d, dict):
        return {k: _strip(v) for k, v in d.items() if v is not None}
    if isinstance(d, list):
        return [_strip(v) for v in d]
    return d


def lower(spec: LivingSpec, proc_id: str) -> Lowered:
    proc: Procedure | None = next((p for p in spec.procedures if p.id == proc_id), None)
    if proc is None:
        raise KeyError(f"no procedure '{proc_id}' (have: {[p.id for p in spec.procedures]})")
    langs = spec.meta.languages
    out = Lowered(data={})
    system = next((s for s in spec.systems if s.id == proc.system), None)

    wid = re.sub(r"[^a-z0-9-]+", "-", proc.id.lower()).strip("-")
    if len(wid) < 3:
        wid = f"{wid}-walkthrough"
    summary = _text(proc.title, langs)
    out.data = {
        "demoshark": 1,
        "walkthrough": {
            "id": wid,
            "title": _text(proc.title, langs) or proc.id,
            "summary": summary or proc.id,
            "owner": spec.meta.owner or None,
            "version": spec.meta.version if re.fullmatch(r"\d+\.\d+\.\d+", spec.meta.version) else "0.1.0",
            "environment": spec.meta.environment,
            "languages": langs,
        },
        "app": {
            "name": system.name if system else (proc.system or "UNKNOWN"),
            "base_url": (system.base_url if system and system.base_url else "http://127.0.0.1:0"),
            "lang_param": (system.lang_param or None) if system else None,
            "adapter": (system.adapter or None) if system else None,
        },
        "fixtures": proc.fixtures or None,
        "steps": [],
        "verify": proc.verify or None,
        "outputs": ["video", "deck", "guide"],
    }
    if not system or not system.base_url:
        out.blockers.append(f"system '{proc.system or '?'}' has no base_url (set it in spec/overlay.yaml for a demo/test system)")
    else:
        refuse_production(system.base_url)

    for s in proc.steps:
        if s.do is None:
            out.blockers.append(f"{s.id}: no action")
            continue
        do = s.do.model_dump(mode="json", exclude_none=True)
        if "target" in do:
            do["target"] = _strip(do["target"])
        step = {"id": s.id, "say": _text(s.say, langs), "do": do,
                "expect": _strip(s.expect.model_dump(mode="json", exclude_none=True)) if s.expect else None,
                "result": _text(s.result, langs)}
        if s.status in ("proposed",) and s.confidence < 0.75:
            out.warnings.append(f"{s.id}: still 'proposed' with confidence {s.confidence}")
        out.data["steps"].append(_strip(step))
        out.fingerprints[s.id] = s.fingerprint()

    # renumber after skipped steps so DS7 (S1..Sn in order) holds; keep a map in warnings
    for i, st in enumerate(out.data["steps"], start=1):
        out.step_map[f"S{i}"] = st["id"]
        if st["id"] != f"S{i}":
            out.warnings.append(f"{st['id']} lowered as S{i}")
            st["id"] = f"S{i}"
    for i, st in enumerate(out.data["steps"]):
        if i > 0 and "expect" not in st:
            out.blockers.append(f"{st['id']}: no expect (DEMOShark DS5)")
        if st["do"]["action"] == "fill" and "value" not in st["do"]:
            out.blockers.append(f"{st['id']}: fill with no value")
    declared = set((proc.fixtures or {}).keys())
    for m in PLACEHOLDER.finditer(json.dumps(out.data["steps"], ensure_ascii=False)):
        if m[1] not in declared:
            out.blockers.append(f"{{{{{m[1]}.{m[2]}}}}} names an undeclared fixture (DS8)")

    out.data = _strip(out.data)
    out.blockers += schema_errors(out.data)
    out.blockers = list(dict.fromkeys(out.blockers))
    return out


def schema_errors(data: dict) -> list[str]:
    import jsonschema

    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    return [f"schema: /{'/'.join(map(str, e.path))}: {e.message}"
            for e in jsonschema.Draft202012Validator(schema).iter_errors(data)]


def write(lowered: Lowered, path: Path) -> None:
    from ruamel.yaml import YAML

    y = YAML()
    y.allow_unicode = True
    y.width = 140
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write("# Lowered by Megamouth from the Living Spec. Do not edit: change the spec and re-lower.\n")
        if lowered.blockers:
            f.write("# BLOCKERS (DEMOShark will refuse this until fixed):\n")
            for b in lowered.blockers:
                f.write(f"#   - {b}\n")
        y.dump(lowered.data, f)
