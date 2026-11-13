"""SOP spreadsheets (csv / xlsx) → `sop.row` evidence.

Deterministic: find the header row by known column names (English and Spanish),
then emit one atom per non-empty row. The synthesizer turns rows into steps.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Iterable

from megamouth.core.evidence import Evidence, SourceRef
from megamouth.core.plugins import Ctx, Requirement

COLUMNS = {
    "process": ["process", "process id", "proceso", "procedure", "procedimiento", "task", "tarea"],
    "seq": ["#", "step", "no", "nº", "n.º", "paso", "seq", "order"],
    "text": ["action", "instruction", "description", "what to do", "acción", "accion", "instrucción", "descripción"],
    "actor": ["actor", "role", "who", "rol", "responsable"],
    "system": ["system", "application", "app", "sistema", "aplicación"],
    "screen": ["screen", "page", "pantalla"],
    "expected": ["expected", "expected result", "result", "resultado", "resultado esperado"],
    "value": ["value", "data", "input", "dato", "valor", "test data"],
    "note": ["note", "notes", "nota", "notas", "comment"],
}


def _norm(s: object) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower().rstrip(":")


def _header_map(row: list[object]) -> dict[str, int]:
    m: dict[str, int] = {}
    for i, cell in enumerate(row):
        c = _norm(cell)
        for field, names in COLUMNS.items():
            if c in names and field not in m:
                m[field] = i
    return m


def _rows(path: Path) -> list[list[object]]:
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as f:
            return [r for r in csv.reader(f)]
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    return [list(r) for r in ws.iter_rows(values_only=True)]


class SopSheetIngester:
    name = "sheets.sop"
    version = "0.1"
    kind = "sheet"
    requires = [Requirement("python", "openpyxl", "uv sync --extra sheets (only for .xlsx)", optional=True)]

    def sniff(self, path: Path) -> float:
        if path.suffix.lower() not in (".csv", ".xlsx", ".xlsm"):
            return 0.0
        try:
            rows = _rows(path)[:10]
        except Exception:
            return 0.0
        return 0.9 if any("text" in _header_map(r) for r in rows) else 0.1

    def ingest(self, path: Path, source: SourceRef, ctx: Ctx) -> Iterable[Evidence]:
        rows = _rows(path)
        hdr_idx, hmap = next(((i, _header_map(r)) for i, r in enumerate(rows) if "text" in _header_map(r)), (None, {}))
        if hdr_idx is None:
            ctx.log.append(f"{path.name}: no header row with an action/instruction column")
            return
        default_process = re.sub(r"[^a-z0-9]+", "-", path.stem.lower()).strip("-")
        ordinal = 0
        for i, row in enumerate(rows[hdr_idx + 1:], start=hdr_idx + 2):  # 1-based sheet row numbers
            get = lambda f: str(row[hmap[f]]).strip() if f in hmap and hmap[f] < len(row) and row[hmap[f]] not in (None, "") else ""
            text = get("text")
            if not text:
                continue
            ordinal += 1
            yield Evidence(
                source_id=source.id, source_sha256=source.sha256,
                locator=f"row:{i}", kind="sop.row",
                extractor=f"{self.name}@{self.version}",
                data={
                    "process": get("process") or default_process,
                    "seq": get("seq") or str(ordinal),
                    "ordinal": ordinal,
                    "text": text,
                    "actor": get("actor"), "system": get("system"), "screen": get("screen"),
                    "expected": get("expected"), "value": get("value"), "note": get("note"),
                },
            )
