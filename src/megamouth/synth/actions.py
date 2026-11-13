"""Instruction text → executable action and expectation (deterministic heuristics).

"Select Approve."                         → click  {role: button, name: Approve}
"Click the Invoice number link"           → click  {role: link, name: Invoice number}
"Type the invoice number in Invoice number" → fill {label: Invoice number}
"The Approve invoice window opens."       → expect {role: dialog, name: Approve invoice}

Every result carries a confidence. Anything the patterns cannot read returns None,
which the synthesizer routes to the review queue (or to the model, if allowed).
"""
from __future__ import annotations

import re

from megamouth.core.spec import Action, Expect, Target

_Q = r"[\"'“‘«]?(?P<name>[^\"'”’»]+?)[\"'”’»]?"
_ROLE_WORDS = {
    "button": "button", "link": "link", "tab": "tab", "menu item": "menuitem", "menuitem": "menuitem",
    "checkbox": "checkbox", "option": "option", "radio button": "radio",
    "botón": "button", "boton": "button", "enlace": "link", "pestaña": "tab",
}
_ROLE_ALT = "|".join(sorted(map(re.escape, _ROLE_WORDS), key=len, reverse=True))

CLICK = re.compile(
    rf"^(?:click|select|press|tap|choose|hit|pulse|haga clic en|seleccione)\s+(?:on\s+)?(?:the\s+|el\s+|la\s+)?"
    rf"(?:(?P<role1>{_ROLE_ALT})\s+)?{_Q}(?:\s+(?P<role2>{_ROLE_ALT}))?"
    rf"(?:\s+(?:to|and|para)\b.*)?\s*[.!]?\s*(?:\..*)?$", re.I)
FILL = re.compile(
    rf"^(?:type|enter|fill in|input|write|escriba|introduzca)\s+(?P<what>.+?)\s+(?:in|into|en)\s+(?:the\s+|el\s+|la\s+)?"
    rf"{_Q}(?:\s+(?:box|field|textbox|casilla|campo))?\s*[.]?$", re.I)
FILL_BARE = re.compile(r"^(?:type|enter|escriba|introduzca)\s+(?:the\s+|el\s+|la\s+)?(?P<name>.+?)\s*[.]?$", re.I)
OPEN = re.compile(r"^(?:open|go to|navigate to|abra|vaya a)\b(?P<rest>.*)$", re.I)

EXP_SCREEN = re.compile(r"^(?:the\s+|la\s+|el\s+)?(?:pantalla\s+)?(?P<name>.+?)\s+(?:screen|page)\s+(?:opens|appears|is shown|loads)", re.I)
EXP_DIALOG = re.compile(r"^(?:the\s+)?(?P<name>.+?)\s+(?:window|dialog|modal|pop-?up)\s+(?:opens|appears|is shown)", re.I)
EXP_FILLED = re.compile(r"^(?:the\s+)?.+?\s+is\s+in\s+the\s+(?P<name>.+?)\s+(?:box|field)", re.I)
EXP_QUOTED = re.compile(r"[\"“](?P<text>[^\"”]+)[\"”]")


def _clean(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip(" .,:;")


def parse_action(text: str, value: str = "", screen: str = "") -> tuple[Action | None, float]:
    t = text.strip()
    first = re.split(r"(?<=[.!?])\s+", t)[0]  # "Select Search. The invoice appears…" → "Select Search."
    if m := OPEN.match(first):
        return Action(action="open", path="/"), 0.5
    if m := FILL.match(first):
        val = value or None
        return Action(action="fill", target=Target(label=_clean(m["name"])), value=val), 0.7 if val else 0.5
    if m := CLICK.match(first):
        role = _ROLE_WORDS.get((m["role1"] or m["role2"] or "").lower(), "button")
        name = _clean(m["name"])
        if len(name.split()) > 6:
            return None, 0.0
        conf = 0.75 if (m["role1"] or m["role2"]) else 0.6
        return Action(action="click", target=Target(role=role, name=name)), conf
    # "Check the supplier and the amount, then select Confirm." → act on the last clause
    if (parts := re.split(r",?\s+(?:then|and then|luego|y luego)\s+", first, flags=re.I)) and len(parts) > 1:
        act, conf = parse_action(parts[-1][:1].upper() + parts[-1][1:], value, screen)
        if act is not None:
            return act, round(conf - 0.05, 2)
    if m := FILL_BARE.match(first):
        label = _clean(re.sub(r"^(?:the|el|la)\s+", "", m["name"], flags=re.I))
        return Action(action="fill", target=Target(label=label[:1].upper() + label[1:]), value=value or None), 0.45
    return None, 0.0


def parse_expect(expected: str, value: str = "") -> tuple[Expect | None, float]:
    e = (expected or "").strip()
    if not e:
        return None, 0.0
    if value and (m := EXP_FILLED.match(e)):
        return Expect(target=Target(label=_clean(m["name"])), value=value), 0.7
    if m := EXP_QUOTED.search(e):
        return Expect(target=Target(text=m["text"])), 0.8
    if m := EXP_DIALOG.match(e):
        return Expect(target=Target(role="dialog", name=_clean(m["name"]))), 0.7
    if m := EXP_SCREEN.match(e):
        return Expect(target=Target(role="heading", name=_clean(m["name"]))), 0.7
    return None, 0.0
