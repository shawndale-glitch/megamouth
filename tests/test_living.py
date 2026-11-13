"""P1, the living loop: grounding → verified/stale, stable ids, decisions, diff, watch, propose, review console."""
import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from megamouth import pipeline
from megamouth.core import spec as specmod
from megamouth.core.workspace import Workspace
from megamouth.diff import diff, to_markdown
from megamouth.ingest.run import ingest
from megamouth.verify import verify
from megamouth.watch import tick

EXAMPLE = Path(__file__).parent.parent / "examples" / "paydesk"

# Stands in for DEMOShark: writes run.json for the lowered spec. Fails at the step
# whose say contains $FAIL_ON, like a renamed button would.
FAKE_RUNNER = textwrap.dedent("""
    import json, os, sys
    from pathlib import Path
    from ruamel.yaml import YAML
    spec, lang, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    d = YAML(typ="safe").load(Path(spec).read_text())
    run_dir = out / d["walkthrough"]["id"] / lang
    (run_dir / "shots").mkdir(parents=True, exist_ok=True)
    fail_on, failed, steps = os.environ.get("FAIL_ON", ""), False, []
    for st in d["steps"]:
        if failed:
            steps.append({"id": st["id"], "status": "not run", "error": "", "shot": ""}); continue
        bad = bool(fail_on) and fail_on in str(st["say"])
        shot = f"shots/{st['id']}{'-fail' if bad else ''}.png"
        (run_dir / shot).write_bytes(b"\\x89PNG fake " + st["id"].encode())
        box = {"x": 100.0 + 40 * len(steps), "y": 120.0, "width": 90.0, "height": 40.0} if "target" in st["do"] else None
        steps.append({"id": st["id"], "status": "fail" if bad else "pass", "start": 2.0 * len(steps),
                      "voice": 1.5, "box": box, "error": "could not find it" if bad else "", "shot": shot})
        failed = bad
    (run_dir / "run.json").write_text(json.dumps({"passed": not failed, "started": "2026-11-13T10:00:00",
                                                  "seconds": 1.0, "steps": steps, "captions": False,
                                                  "viewport": {"width": 640, "height": 360}}))
    sys.exit(1 if failed else 0)
""")


@pytest.fixture
def ws(tmp_path, monkeypatch):
    root = tmp_path / "paydesk"
    shutil.copytree(EXAMPLE / "inbox", root / "inbox")
    (root / "spec").mkdir()
    shutil.copy(EXAMPLE / "spec" / "overlay.yaml", root / "spec" / "overlay.yaml")
    runner = tmp_path / "fake_demoshark.py"
    runner.write_text(FAKE_RUNNER)
    w = Workspace.init(root)
    cfg = (root / "megamouth.toml").read_text()
    cfg = cfg.replace('id = "living-spec"', 'id = "paydesk"')
    cfg = cfg.replace('command = ["{python}", "-m", "demoshark", "run", "{spec}", "--voice", "none", "--lang", "{lang}", "--out", "{out}"]',
                      f'command = ["{sys.executable}", "{runner}", "{{spec}}", "{{lang}}", "{{out}}"]')
    (root / "megamouth.toml").write_text(cfg)
    w = Workspace.open(root)
    ingest(w, [root / "inbox"])
    return w


def _steps(spec):
    return {st.id: st for st in spec.procedures[0].steps}


def test_ids_are_stable_across_syntheses(ws):
    a, _ = pipeline.synth(ws)
    b, _ = pipeline.synth(ws)
    assert [c.id for c in a.provenance.conflicts] == [c.id for c in b.provenance.conflicts]
    assert all(c.id.startswith("C-") and len(c.id) == 8 for c in a.provenance.conflicts)
    assert [r.id for r in a.provenance.review] == [r.id for r in b.provenance.review]


def test_verify_marks_steps_verified_and_clears_answered_questions(ws):
    spec, path = pipeline.synth(ws)
    assert any(r.reason.startswith("guessed") for r in spec.provenance.review)
    rep = verify(ws, spec)
    assert rep.ok and rep.results[0].detail == "6 steps passed"

    run_file = ws.root / "runs" / "approve-an-invoice.en.ground.json"
    ground = json.loads(run_file.read_text())
    assert all(not s["shot"].startswith("/") for s in ground["steps"])   # no machine paths in git
    assert str(ws.root) not in run_file.read_text()

    spec, _ = pipeline.synth(ws)
    assert {st.status for st in spec.procedures[0].steps} == {"verified"}
    assert spec.procedures[0].status == "verified"
    assert spec.provenance.verification[0].passed
    s5 = _steps(spec)["S5"]
    assert any(ws.store.get(e).kind == "ground.step" for e in s5.evidence if ws.store.get(e))
    assert not any(r.reason.startswith("guessed") for r in spec.provenance.review)


def test_failed_run_makes_the_step_stale(ws, monkeypatch):
    spec, _ = pipeline.synth(ws)
    monkeypatch.setenv("FAIL_ON", "Approve")
    rep = verify(ws, spec)
    assert not rep.ok and "failed at S5" in rep.results[0].detail
    spec, _ = pipeline.synth(ws)
    st = _steps(spec)
    assert [st[s].status for s in ("S1", "S2", "S3", "S4")] == ["verified"] * 4
    assert st["S5"].status == "stale"
    assert st["S6"].status != "verified"                      # never ran
    assert spec.procedures[0].status == "stale"
    assert any("grounding run failed" in r.reason and r.about.endswith("S5") for r in spec.provenance.review)


def test_changed_source_makes_a_verified_step_stale_and_revert_restores_it(ws):
    verify(ws, pipeline.synth(ws)[0])
    csv = ws.root / "inbox" / "paydesk-sop.csv"
    original = csv.read_text()
    csv.write_text(original.replace(",Select Approve.,", ",Select Approve all.,"))
    assert tick(ws, [ws.root / "inbox"], verify=False, say=lambda m: None)
    spec = specmod.load(pipeline.proposed_path(ws, "paydesk"))
    assert _steps(spec)["S5"].status == "stale" and _steps(spec)["S4"].status == "verified"

    csv.write_text(original)
    assert tick(ws, [ws.root / "inbox"], verify=False, say=lambda m: None)
    spec = specmod.load(pipeline.proposed_path(ws, "paydesk"))
    assert _steps(spec)["S5"].status == "verified"


def test_semantic_diff_names_changes_and_rebuilds(ws):
    before, _ = pipeline.synth(ws)
    csv = ws.root / "inbox" / "paydesk-sop.csv"
    csv.write_text(csv.read_text().replace(",Select Approve.,", ",Select Approve all.,"))
    ingest(ws, [ws.root / "inbox"])
    after, _ = pipeline.synth(ws)
    d = diff(before, after)
    assert d.rebuild == ["approve-an-invoice"]
    whats = [c.what for c in d.changes if c.where == "approve-an-invoice.S5"]
    assert any("name='Approve all'" in w for w in whats)
    md = to_markdown(d, after)
    assert "### Outputs that will rebuild" in md and "merging is blocked" in md
    assert diff(after, after).empty


def test_resolutions_and_accepted_items_from_the_overlay(ws):
    spec, _ = pipeline.synth(ws)
    c = next(c for c in spec.provenance.conflicts if "Query supplier" in c.summary)
    r = spec.provenance.review[0]
    ov = ws.root / "spec" / "overlay.yaml"
    ov.write_text(ov.read_text() + f"resolutions:\n  {c.id}: {{choice: note, note: draft board, by: Demo Reviewer}}\n"
                  f"accepted: [{r.id}]\n")
    spec, _ = pipeline.synth(ws)
    c2 = next(x for x in spec.provenance.conflicts if x.id == c.id)
    assert (c2.status, c2.resolved_by) == ("resolved", "Demo Reviewer")
    assert r.id not in {x.id for x in spec.provenance.review}


def test_review_console_writes_decisions_to_the_overlay(ws):
    from fastapi.testclient import TestClient

    from megamouth.review import create_app

    pipeline.synth(ws)
    client = TestClient(create_app(ws))
    assert "Megamouth review" in client.get("/").text
    st = client.get("/api/state").json()
    cid = next(c["id"] for c in st["conflicts"] if c["status"] == "open")
    st = client.post("/api/resolve", json={"id": cid, "choice": "Finance Manager", "by": "Demo Reviewer"}).json()
    assert next(c for c in st["conflicts"] if c["id"] == cid)["status"] == "resolved"

    st = client.post("/api/step", json={"procedure": "approve-an-invoice", "step": "S5",
                                        "patch": {"do": {"action": "click", "target": {"role": "button", "name": "Approve"}}}}).json()
    s5 = next(s for s in st["procedures"][0]["steps"] if s["id"] == "S5")
    assert s5["status"] == "reviewed"

    rid = st["review"][0]["id"]
    st = client.post("/api/accept", json={"id": rid}).json()
    assert rid in st["accepted"] and rid not in [r["id"] for r in st["review"]]

    text = (ws.root / "spec" / "overlay.yaml").read_text()
    assert text.startswith("# Human decisions")              # comments survive the round trip
    assert "Demo Reviewer" in text and "S5:" in text

    assert client.post("/api/resolve", json={"id": "../../etc"}).status_code == 400
    assert client.get("/blob/..%2F..%2Findex.sqlite").status_code == 404


def test_propose_commits_on_a_branch_with_the_semantic_diff(ws):
    g = lambda *a: subprocess.run(["git", "-C", str(ws.root), *a], check=True, capture_output=True, text=True)
    g("init", "-q", "-b", "main")
    g("config", "user.email", "test@example.com"); g("config", "user.name", "Test")
    spec, path = pipeline.synth(ws)
    g("add", "-A"); g("commit", "-qm", "base")
    csv = ws.root / "inbox" / "paydesk-sop.csv"
    csv.write_text(csv.read_text().replace(",Select Approve.,", ",Select Approve all.,"))
    ingest(ws, [ws.root / "inbox"])
    spec, path = pipeline.synth(ws)
    d = diff(pipeline.committed_spec(ws, path), spec)
    branch = pipeline.propose(ws, spec, path, to_markdown(d, spec), push=False, pr=False)
    assert branch.startswith("megamouth/paydesk-")
    assert g("branch", "--show-current").stdout.strip() == branch
    msg = g("log", "-1", "--format=%B").stdout
    assert "Outputs that will rebuild" in msg and "approve-an-invoice.S5" in msg
