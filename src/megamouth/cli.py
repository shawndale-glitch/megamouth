"""`python -m megamouth …` (ARCHITECTURE §7). P0 commands: init, doctor, ingest, synth, lower, explain, schema."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from megamouth.core import spec as specmod
from megamouth.core.workspace import Workspace

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="Megamouth: anything in, Living Spec out.")
con = Console()
WS = typer.Option(Path("."), "--workspace", "-w", help="Workspace folder")


def _ws(p: Path) -> Workspace:
    try:
        return Workspace.open(p)
    except FileNotFoundError as e:
        con.print(f"[red]{e}[/red]")
        raise typer.Exit(2)


@app.command()
def init(path: Path = typer.Argument(Path("."))):
    """Create a workspace: config, folders, .gitignore."""
    ws = Workspace.init(path)
    con.print(f"Workspace ready at [bold]{ws.root}[/bold]. Drop sources into inbox/ and run `megamouth ingest inbox`.")


@app.command()
def doctor():
    """Check every plugin's requirements."""
    from megamouth.core.plugins import ingesters

    t = Table("plugin", "requirement", "status", "fix")
    bad = 0
    for p in ingesters():
        for r in p.requires or []:
            ok = r.met()
            bad += (not ok and not r.optional)
            t.add_row(f"{p.name}@{p.version}", f"{r.kind}:{r.name}",
                      "[green]ok[/green]" if ok else ("[yellow]optional[/yellow]" if r.optional else "[red]missing[/red]"),
                      "" if ok else r.hint)
        if not p.requires:
            t.add_row(f"{p.name}@{p.version}", "-", "[green]ok[/green]", "")
    import importlib.util as iu

    llm_ok = iu.find_spec("anthropic") is not None
    t.add_row("core.llm", "python:anthropic", "[green]ok[/green]" if llm_ok else "[yellow]optional[/yellow]",
              "" if llm_ok else "uv sync --extra llm (otherwise model calls run in manual mode)")
    con.print(t)
    raise typer.Exit(1 if bad else 0)


@app.command()
def ingest(paths: list[Path] = typer.Argument(..., help="Files or folders"), workspace: Path = WS):
    """Read sources into the evidence graph (append-only; unchanged sources are skipped)."""
    from megamouth.ingest.run import ingest as run

    ws = _ws(workspace)
    rep = run(ws, paths)
    for sid, plugin, n in rep.added:
        cloud = "cloud ok" if ws.store.latest_sources()[sid].cloud else "local only"
        con.print(f"  [green]+[/green] {sid:<36} {plugin:<16} {n:>4} atoms  ({cloud})")
    for sid in rep.unchanged:
        con.print(f"  [dim]= {sid} unchanged[/dim]")
    for s in rep.skipped:
        con.print(f"  [yellow]?[/yellow] no plugin reads {s}")
    for line in rep.log:
        con.print(f"  [yellow]![/yellow] {line}")


@app.command()
def synth(
    workspace: Path = WS,
    spec_id: str = typer.Option("living-spec", "--id"),
    title: str = typer.Option("Living Spec", "--title"),
    vlm: bool = typer.Option(False, "--vlm", help="Ask a vision model to read keyframes (policy-gated)"),
    out: Optional[Path] = typer.Option(None, "--out"),
):
    """Propose a Living Spec from all current evidence. Writes spec/proposed/<id>.yaml; never merges by itself."""
    from ruamel.yaml import YAML

    from megamouth.core.llm import Gateway
    from megamouth.synth.build import synthesize

    ws = _ws(workspace)
    overlay_path = ws.root / "spec" / "overlay.yaml"
    overlay = YAML(typ="safe").load(overlay_path.read_text(encoding="utf-8")) if overlay_path.exists() else None
    gw = Gateway(ws.root, model=ws.llm.get("model", "claude-opus-5-5"),
                 effort=ws.llm.get("effort", "medium"), mode=ws.llm.get("mode", "auto"))
    s = synthesize(ws.store, title=title, spec_id=spec_id, languages=ws.languages,
                   environment=ws.environment, overlay=overlay, gateway=gw, vlm=vlm)
    dest = out or ws.root / "spec" / "proposed" / f"{spec_id}.yaml"
    specmod.save(s, dest)
    con.print(f"Proposed spec: [bold]{dest}[/bold]")
    con.print(f"  {len(s.processes)} process(es), {len(s.procedures)} procedure(s), "
              f"{sum(len(p.steps) for p in s.procedures)} step(s)")
    if s.provenance.conflicts:
        con.print(f"[red]  {len(s.provenance.conflicts)} conflict(s) block merging:[/red]")
        for c in s.provenance.conflicts:
            con.print(f"    {c.id} {c.about}: {c.summary}")
    if s.provenance.review:
        con.print(f"[yellow]  {len(s.provenance.review)} item(s) need review:[/yellow]")
        for r in s.provenance.review:
            con.print(f"    {r.id} {r.about}: {r.reason}")


@app.command()
def lower(
    spec_path: Path = typer.Argument(..., help="Living Spec YAML"),
    procedure: str = typer.Option(..., "--procedure", "-p"),
    to: str = typer.Option("demoshark:1", "--to"),
    out: Optional[Path] = typer.Option(None, "--out"),
):
    """Compile one procedure to a lowering target (P0: demoshark:1)."""
    from megamouth.lowering import demoshark

    if to != "demoshark:1":
        con.print(f"[red]unknown target {to}; P0 supports demoshark:1[/red]")
        raise typer.Exit(2)
    s = specmod.load(spec_path)
    low = demoshark.lower(s, procedure)
    dest = out or spec_path.parent.parent.parent / "build" / "demoshark" / f"{procedure}.yaml"
    demoshark.write(low, dest)
    con.print(f"Wrote [bold]{dest}[/bold]")
    for w in low.warnings:
        con.print(f"  [yellow]warn[/yellow] {w}")
    for b in low.blockers:
        con.print(f"  [red]blocker[/red] {b}")
    raise typer.Exit(1 if low.blockers else 0)


@app.command()
def explain(element: str = typer.Argument(..., help="e.g. approve-an-invoice.S3"),
            spec_path: Path = typer.Option(..., "--spec"), workspace: Path = WS):
    """Print the evidence trail of one spec element."""
    ws = _ws(workspace)
    s = specmod.load(spec_path)
    el = s.find(element)
    if el is None:
        con.print(f"[red]no element {element}[/red]")
        raise typer.Exit(2)
    con.print(f"[bold]{element}[/bold]  status={el.status}  confidence={el.confidence}")
    srcs = ws.store.latest_sources()
    for ev_id in el.evidence:
        ev = ws.store.get(ev_id)
        if ev is None:
            con.print(f"  {ev_id}  [dim](human decision or older source version)[/dim]")
            continue
        src = srcs.get(ev.source_id)
        brief = ev.data.get("text") or ev.data.get("name") or ev.data.get("blob") or json.dumps(ev.data, ensure_ascii=False)[:80]
        con.print(f"  {ev.kind:<14} {ev.source_id}  {ev.locator:<16} {brief}")
        if src:
            con.print(f"  {'':<14} [dim]{src.uri}  sha256:{src.sha256[:12]}  via {ev.extractor}[/dim]")
    for c in s.provenance.conflicts:
        if c.about.startswith(element):
            con.print(f"  [red]conflict {c.id}[/red]: {c.summary}")
    for r in s.provenance.review:
        if r.about.startswith(element):
            con.print(f"  [yellow]review {r.id}[/yellow]: {r.reason}")


@app.command()
def schema(out: Path = typer.Option(Path("schema/megamouth.schema.json"), "--out")):
    """Export the Living Spec JSON Schema (for editor validation)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(specmod.json_schema(), indent=2, ensure_ascii=False), encoding="utf-8")
    con.print(f"Wrote {out}")
