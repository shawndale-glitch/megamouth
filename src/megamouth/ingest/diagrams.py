"""Process diagrams → `process.*` evidence. Fully deterministic (no model).

- BPMN 2.0 XML (.bpmn, or .xml with the BPMN namespace)
- FigJam boards exported by figjam_extract.js (`board.json`: instances + connectors),
  the format used by figjam-process-pipeline.

Both produce the same atom kinds, so the synthesizer never knows which tool drew it:
  process.meta  {process, name, owner}
  process.lane  {process, lane}
  process.node  {process, node, type, name, lane}
  process.edge  {process, from, to, label}
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable

from megamouth.core.evidence import Evidence, SourceRef
from megamouth.core.plugins import Ctx

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
_BPMN_TYPES = {
    "startEvent": "start", "endEvent": "end",
    "task": "task", "userTask": "task", "manualTask": "task", "serviceTask": "task",
    "sendTask": "task", "receiveTask": "task", "scriptTask": "task", "businessRuleTask": "task",
    "subProcess": "task", "callActivity": "task",
    "exclusiveGateway": "gateway", "parallelGateway": "gateway", "inclusiveGateway": "gateway",
    "eventBasedGateway": "gateway",
    "intermediateCatchEvent": "event", "intermediateThrowEvent": "event", "boundaryEvent": "event",
    "dataObjectReference": "data", "dataStoreReference": "data",
}


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _ev(source: SourceRef, extractor: str, locator: str, kind: str, data: dict) -> Evidence:
    return Evidence(source_id=source.id, source_sha256=source.sha256, locator=locator,
                    kind=kind, extractor=extractor, data=data)


class BpmnIngester:
    name = "diagrams.bpmn"
    version = "0.1"
    kind = "bpmn"
    requires: list = []

    def sniff(self, path: Path) -> float:
        if path.suffix.lower() == ".bpmn":
            return 1.0
        if path.suffix.lower() == ".xml":
            return 0.9 if BPMN_NS in path.read_text(encoding="utf-8", errors="ignore")[:4000] else 0.0
        return 0.0

    def ingest(self, path: Path, source: SourceRef, ctx: Ctx) -> Iterable[Evidence]:
        x = f"{self.name}@{self.version}"
        root = ET.parse(path).getroot()
        ns = {"b": BPMN_NS}
        # participants name the pools; map processRef → pool name
        pool = {p.get("processRef"): p.get("name", "") for p in root.iterfind(".//b:participant", ns)}
        for proc in root.iterfind("b:process", ns):
            pid = proc.get("id")
            name = proc.get("name") or pool.get(pid) or pid
            yield _ev(source, x, f"node:{pid}", "process.meta", {"process": pid, "name": name})
            lane_of: dict[str, str] = {}
            for lane in proc.iterfind(".//b:lane", ns):
                lname = lane.get("name") or lane.get("id")
                yield _ev(source, x, f"node:{lane.get('id')}", "process.lane", {"process": pid, "lane": lname})
                for ref in lane.iterfind("b:flowNodeRef", ns):
                    lane_of[(ref.text or "").strip()] = lname
            for el in proc:
                tag = el.tag.split("}")[-1]
                if tag in _BPMN_TYPES:
                    nid = el.get("id")
                    yield _ev(source, x, f"node:{nid}", "process.node", {
                        "process": pid, "node": nid, "type": _BPMN_TYPES[tag],
                        "name": (el.get("name") or "").strip(), "lane": lane_of.get(nid, ""), "bpmn": tag})
                elif tag == "sequenceFlow":
                    yield _ev(source, x, f"node:{el.get('id')}", "process.edge", {
                        "process": pid, "from": el.get("sourceRef"), "to": el.get("targetRef"),
                        "label": (el.get("name") or "").strip()})


_FIGJAM_TYPES = [
    ("Events/Start", "start"), ("Events/End", "end"), ("Events/", "event"),
    ("Activities/", "task"), ("Gateways/", "gateway"),
    ("Data and systems/System", "system"), ("Data and systems/Document", "data"),
    ("Data and systems/Annotation", "annotation"),
]


class FigJamIngester:
    name = "diagrams.figjam"
    version = "0.1"
    kind = "figjam"
    requires: list = []

    def sniff(self, path: Path) -> float:
        if path.suffix.lower() != ".json":
            return 0.0
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return 0.0
        return 0.95 if isinstance(d, dict) and "instances" in d and "connectors" in d else 0.0

    def ingest(self, path: Path, source: SourceRef, ctx: Ctx) -> Iterable[Evidence]:
        x = f"{self.name}@{self.version}"
        d = json.loads(path.read_text(encoding="utf-8"))
        inst = d.get("instances", [])
        header = next((i for i in inst if i["component"].startswith("Template/Header")), None)
        props = header["props"] if header else {}
        pid = props.get("Process ID") or slug(d.get("source", {}).get("section_name", path.stem))
        yield _ev(source, x, f"node:{header['id'] if header else 'board'}", "process.meta", {
            "process": pid, "name": props.get("Process name", d.get("source", {}).get("section_name", pid)),
            "owner": props.get("Owner", "")})

        lanes = [i for i in inst if i["component"] == "Markers/Swimlane"]
        for ln in lanes:
            yield _ev(source, x, f"node:{ln['id']}", "process.lane",
                      {"process": pid, "lane": ln["props"].get("Role") or (ln["texts"] or [""])[0]})

        def lane_at(i: dict) -> str:
            cy = i["y"] + i["h"] / 2
            for ln in lanes:
                if ln["y"] <= cy <= ln["y"] + ln["h"]:
                    return ln["props"].get("Role") or (ln["texts"] or [""])[0]
            return ""

        known = set()
        for i in inst:
            t = next((t for prefix, t in _FIGJAM_TYPES if i["component"].startswith(prefix)), None)
            if not t:
                continue
            known.add(i["id"])
            name = i["props"].get("Label") or " ".join(i.get("texts") or [])
            yield _ev(source, x, f"node:{i['id']}", "process.node", {
                "process": pid, "node": i["id"], "type": t, "name": name.strip(),
                "lane": lane_at(i), "component": i["component"]})
        for c in d.get("connectors", []):
            if c["from"] in known and c["to"] in known:
                yield _ev(source, x, f"node:{c['id']}", "process.edge", {
                    "process": pid, "from": c["from"], "to": c["to"],
                    "label": c.get("label", ""), "dashed": c.get("dashed", False)})
