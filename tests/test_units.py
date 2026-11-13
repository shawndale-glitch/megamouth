import pytest
from pydantic import ValidationError

from megamouth.core.spec import Actor, Step
from megamouth.core.workspace import Policy, refuse_production
from megamouth.ingest.av import parse_subtitles
from megamouth.synth import align
from megamouth.synth.actions import parse_action, parse_expect


@pytest.mark.parametrize("text,action,target", [
    ("Select Approve.", "click", {"role": "button", "name": "Approve"}),
    ("Click the Invoices tab", "click", {"role": "tab", "name": "Invoices"}),
    ("Select the invoice number link to open it.", "click", {"role": "link", "name": "invoice number"}),
    ("Select Search. The invoice appears in the list.", "click", {"role": "button", "name": "Search"}),
    ("Check the supplier and the amount, then select Confirm.", "click", {"role": "button", "name": "Confirm"}),
    ("Type the invoice number in Invoice number.", "fill", {"label": "Invoice number"}),
    ('Pulse "Aprobar".', "click", {"role": "button", "name": "Aprobar"}),
])
def test_parse_action(text, action, target):
    act, conf = parse_action(text)
    assert act is not None and act.action == action
    assert act.target.model_dump(exclude_none=True) == target
    assert 0 < conf < 1


def test_parse_action_unreadable_is_none():
    assert parse_action("Make sure everything looks right.") == (None, 0.0)


@pytest.mark.parametrize("text,target", [
    ("The Approve invoice window opens.", {"role": "dialog", "name": "Approve invoice"}),
    ("The Find an invoice screen opens.", {"role": "heading", "name": "Find an invoice"}),
    ('Paydesk shows "Invoice 7 approved."', {"text": "Invoice 7 approved."}),
])
def test_parse_expect(text, target):
    exp, _ = parse_expect(text)
    assert exp.target.model_dump(exclude_none=True) == target


def test_align_preserves_order_and_skips_extras():
    sop = ["Open Paydesk", "Select Search", "Select Approve", "Select Confirm"]
    rec = ["Open Paydesk now", "Select Search", "check the due date first", "Select Approve", "then Confirm it"]
    pairs = align.align_sequences(sop, rec)
    assert [(i, j) for i, j, _ in pairs] == [(0, 0), (1, 1), (2, 3), (3, 4)]


def test_every_element_needs_evidence_or_assumption():
    with pytest.raises(ValidationError, match="no evidence"):
        Actor(id="clerk", name="Clerk")
    Actor(id="clerk", name="Clerk", assumption=True)
    Step(id="S1", say="Open it", evidence=["ev:x#row:1@abcd"])


def test_policy_default_deny():
    p = Policy(cloud_allow=["**/paydesk/**"], cloud_deny=["**/secret/**"])
    assert p.cloud_ok("/w/examples/paydesk/inbox/a.csv")
    assert not p.cloud_ok("/w/internal/inbox/a.csv")
    assert not p.cloud_ok("/w/paydesk/secret/a.csv")


def test_production_refused():
    with pytest.raises(ValueError):
        refuse_production("https://prod.app.example.com")
    refuse_production("http://127.0.0.1:8765")


def test_subtitles(tmp_path):
    f = tmp_path / "x.vtt"
    f.write_text("WEBVTT\n\n00:00:01.500 --> 00:00:03.000\nSelect Approve.\n\n00:00:04,000 --> 00:00:05,250\nThen Confirm.\n")
    assert parse_subtitles(f) == [
        {"start": 1.5, "end": 3.0, "text": "Select Approve."},
        {"start": 4.0, "end": 5.25, "text": "Then Confirm."},
    ]
