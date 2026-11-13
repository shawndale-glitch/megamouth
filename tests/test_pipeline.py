"""End to end on the fictional Paydesk sources: ingest → synth → lower, no network."""
import json
import shutil
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from megamouth.core import spec as specmod
from megamouth.core.evidence import SourceRef
from megamouth.core.llm import Gateway, ManualPending
from megamouth.core.workspace import Workspace
from megamouth.ingest.run import ingest
from megamouth.lowering import demoshark
from megamouth.synth.build import KeyframeReading, synthesize

EXAMPLE = Path(__file__).parent.parent / "examples" / "paydesk"


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "paydesk"
    shutil.copytree(EXAMPLE / "inbox", root / "inbox")
    (root / "spec").mkdir()
    shutil.copy(EXAMPLE / "spec" / "overlay.yaml", root / "spec" / "overlay.yaml")
    return Workspace.init(root)


def _synth(ws):
    overlay = YAML(typ="safe").load((ws.root / "spec" / "overlay.yaml").read_text())
    return synthesize(ws.store, title="Paydesk", spec_id="paydesk", languages=["en"],
                      environment="demo", overlay=overlay)


def test_ingest_all_four_families_and_is_incremental(ws):
    rep = ingest(ws, [ws.root / "inbox"])
    assert {p for _, p, _ in rep.added} == {"sheets.sop", "diagrams.bpmn", "diagrams.figjam", "av.screenrec"}
    assert not rep.skipped
    kinds = {e.kind for e in ws.store.current()}
    assert {"sop.row", "process.node", "process.edge", "av.keyframe", "av.utterance"} <= kinds
    assert len(list(ws.store.current("av.keyframe"))) >= 6

    again = ingest(ws, [ws.root / "inbox"])
    assert not again.added and len(again.unchanged) == 4

    csv = ws.root / "inbox" / "paydesk-sop.csv"
    csv.write_text(csv.read_text().replace("Select Approve.", "Select Approve now."))
    third = ingest(ws, [ws.root / "inbox"])
    assert [s for s, _, _ in third.added] == ["paydesk-sop"]
    assert any(e.data["text"] == "Select Approve now." for e in ws.store.current("sop.row"))
    assert not any(e.data["text"] == "Select Approve." for e in ws.store.current("sop.row"))


def test_synth_merges_diagrams_and_finds_the_planted_conflicts(ws):
    ingest(ws, [ws.root / "inbox"])
    s = _synth(ws)
    assert len(s.processes) == 1
    proc = s.processes[0]
    assert proc.id == "P2P-03"                         # BPMN wins as primary
    approve = next(n for n in proc.nodes if n.name == "Approve invoice")
    assert approve.procedure == "approve-an-invoice"   # task ↔ procedure link
    assert len({e.split("#")[0] for e in approve.evidence}) == 2   # both diagrams cite it

    summaries = " | ".join(c.summary for c in s.provenance.conflicts)
    assert "'Approve invoice' is in different lanes" in summaries          # AP Lead vs Finance Manager
    assert "'Query supplier' appears in only one diagram" in summaries
    assert "check the due date" in summaries                                # narrated, not in the SOP


def test_steps_carry_sop_recording_and_keyframe_evidence(ws):
    ingest(ws, [ws.root / "inbox"])
    s = _synth(ws)
    s5 = s.find("approve-an-invoice.S5")
    kinds = {ws.store.get(e).kind for e in s5.evidence if ws.store.get(e)}
    assert kinds == {"sop.row", "av.utterance", "av.keyframe"}
    assert s5.media.clip.startswith("approve-invoice-training@00:15")
    kf = next(ws.store.get(e) for e in s5.evidence if ws.store.get(e) and ws.store.get(e).kind == "av.keyframe")
    assert kf.data["t"] == 15.0


def test_overlay_decisions_survive_and_are_cited(ws):
    ingest(ws, [ws.root / "inbox"])
    s = _synth(ws)
    s4 = s.find("approve-an-invoice.S4")
    assert s4.status == "reviewed"
    assert any(e.startswith("ev:overlay#") for e in s4.evidence)
    assert not any(r.about.startswith("approve-an-invoice.S4.do") for r in s.provenance.review)


def test_roundtrip_and_lower_to_demoshark(ws, tmp_path):
    ingest(ws, [ws.root / "inbox"])
    path = tmp_path / "spec.yaml"
    specmod.save(_synth(ws), path)
    s = specmod.load(path)
    low = demoshark.lower(s, "approve-an-invoice")
    assert low.blockers == []
    assert demoshark.schema_errors(low.data) == []
    assert [st["do"]["action"] for st in low.data["steps"]] == ["open", "fill", "click", "click", "click", "click"]
    assert low.data["steps"][5]["do"]["target"] == {"role": "button", "name": "Confirm"}


def test_lowering_reports_what_is_missing(ws):
    ingest(ws, [ws.root / "inbox"])
    s = synthesize(ws.store, title="x", spec_id="x", languages=["en"], environment="demo")  # no overlay
    low = demoshark.lower(s, "approve-an-invoice")
    text = " ".join(low.blockers)
    assert "no base_url" in text and "undeclared fixture" in text


def test_gateway_manual_mode_and_policy(tmp_path):
    gw = Gateway(tmp_path, mode="manual")
    src = SourceRef(id="a", kind="video", uri="x", sha256="0" * 64, cloud=True)
    with pytest.raises(ManualPending) as e:
        gw.structured("keyframe", "read it", KeyframeReading, [src])
    req = json.loads(e.value.path.read_text())
    assert req["output_schema"]["title"] == "KeyframeReading"
    # pasting an answer back completes the call
    e.value.path.with_suffix(".answer.json").write_text(json.dumps(
        {"screen_heading": "Invoice details", "control_role": "button", "control_name": "Approve", "confident": True}))
    assert gw.structured("keyframe", "read it", KeyframeReading, [src]).control_name == "Approve"

    local = SourceRef(id="b", kind="video", uri="x", sha256="1" * 64, cloud=False)
    with pytest.raises(ManualPending) as e2:
        Gateway(tmp_path, mode="auto").structured("keyframe", "read it", KeyframeReading, [local])
    assert "not cleared for cloud" in json.loads(e2.value.path.read_text())["why_manual"]
