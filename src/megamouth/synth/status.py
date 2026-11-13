"""The element lifecycle (ARCHITECTURE §7):

    evidence ─synth─► proposed ─review─► reviewed ─ground run pass─► verified
                         ▲                                              │
                         └──── source changed / run failed ◄── stale ◄──┘

- verified: the latest grounding run passed this step in every language it ran,
  and the step's content (say/do/expect) is identical to what was run.
- stale:    a run failed at this step, or the step changed after it passed.
- reviewed: a person decided something about it (overlay edit, accepted review item).
- proposed: nothing above applies.
A procedure is verified when all its steps are; stale when any step is.
"""
from __future__ import annotations

from collections import defaultdict

from megamouth.core.evidence import Evidence, EvidenceStore
from megamouth.core.spec import LivingSpec, Verification

RANK = {"proposed": 0, "reviewed": 1, "verified": 2, "stale": 3}


def apply_grounding(spec: LivingSpec, store: EvidenceStore, flag) -> list[Verification]:
    runs: dict[tuple[str, str], Evidence] = {}
    steps: dict[tuple[str, str], list[Evidence]] = defaultdict(list)
    for a in store.current("ground."):
        if a.kind == "ground.run":
            runs[(a.data["procedure"], a.data["lang"])] = a
        else:
            steps[(a.data["procedure"], a.data["step"])].append(a)

    for proc in spec.procedures:
        statuses = []
        for st in proc.steps:
            results = [r for r in steps.get((proc.id, st.id), []) if r.data["status"] in ("pass", "fail")]
            if not results:
                statuses.append(st.status)
                continue
            fp = st.fingerprint()
            failed = [r for r in results if r.data["status"] == "fail"]
            changed = [r for r in results if r.data["fingerprint"] != fp]
            if failed:
                st.status = "stale"
                for r in failed:
                    flag(f"{proc.id}.{st.id}", f"grounding run failed ({r.data['lang']}): {r.data['error']}")
            elif changed:
                st.status = "stale"
                flag(f"{proc.id}.{st.id}", "step changed since it was last verified; run `megamouth verify`")
            else:
                st.status = "verified"
            st.evidence = [e for e in st.evidence if not e.startswith("ev:ground-")] + [r.id for r in results]
            statuses.append(st.status)
        if statuses and all(s == "verified" for s in statuses):
            proc.status = "verified"
        elif "stale" in statuses:
            proc.status = "stale"

    return [Verification(procedure=r.data["procedure"], lang=r.data["lang"], passed=r.data["passed"],
                         started=r.data["started"], failed_at=r.data["failed_at"], error=r.data["error"],
                         evidence=r.id)
            for _, r in sorted(runs.items())]


def apply_resolutions(spec: LivingSpec, overlay: dict | None) -> None:
    """Resolutions and accepted review items recorded by a person (review console or by hand)."""
    if not overlay:
        return
    res = overlay.get("resolutions") or {}
    for c in spec.provenance.conflicts:
        if c.id in res:
            r = res[c.id] or {}
            c.status, c.resolved_by, c.resolution = "resolved", str(r.get("by", "")), str(r.get("choice", r.get("note", "")))
    accepted = set(overlay.get("accepted") or [])
    keep = []
    for item in spec.provenance.review:
        if item.id in accepted:
            el = spec.find(item.about.split(".do")[0].split(".expect")[0])
            if el is not None and RANK[el.status] < RANK["reviewed"]:
                el.status = "reviewed"
        else:
            keep.append(item)
    spec.provenance.review = keep
