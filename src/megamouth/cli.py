"""`python -m megamouth …` (ARCHITECTURE §7). P0 commands: init, doctor, ingest, synth, lower, explain, schema."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.markup import escape
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
    spec_id: Optional[str] = typer.Option(None, "--id", help="default: [spec] id in megamouth.toml"),
    title: Optional[str] = typer.Option(None, "--title"),
    vlm: bool = typer.Option(False, "--vlm", help="Ask a vision model to read keyframes (policy-gated)"),
    out: Optional[Path] = typer.Option(None, "--out"),
):
    """Propose a Living Spec from all current evidence. Writes spec/proposed/<id>.yaml; never merges by itself."""
    from megamouth import pipeline

    ws = _ws(workspace)
    s, dest = pipeline.synth(ws, spec_id, title, vlm, out)
    con.print(f"Proposed spec: [bold]{dest}[/bold]")
    _summary(s)


def _summary(s) -> None:
    steps = [st for p in s.procedures for st in p.steps]
    by = {k: sum(1 for st in steps if st.status == k) for k in ("verified", "stale", "reviewed", "proposed")}
    con.print(f"  {len(s.processes)} process(es), {len(s.procedures)} procedure(s), {len(steps)} step(s): "
              + ", ".join(f"{n} {k}" for k, n in by.items() if n))
    for v in s.provenance.verification:
        mark = "[green]PASS[/green]" if v.passed else f"[red]FAIL at {v.failed_at}[/red]"
        con.print(f"  last grounding {v.procedure} ({v.lang}) {v.started}: {mark}")
    open_c = [c for c in s.provenance.conflicts if c.status == "open"]
    if open_c:
        con.print(f"[red]  {len(open_c)} open conflict(s) block merging:[/red]")
        for c in open_c:
            con.print(f"    {c.id} {c.about}: {c.summary}")
    if s.provenance.review:
        con.print(f"[yellow]  {len(s.provenance.review)} item(s) need review:[/yellow]")
        for r in s.provenance.review:
            con.print(f"    {r.id} {r.about}: {r.reason}")


@app.command()
def verify(
    workspace: Path = WS,
    procedure: Optional[list[str]] = typer.Option(None, "--procedure", "-p"),
    lang: Optional[list[str]] = typer.Option(None, "--lang"),
):
    """Grounding run: prove each procedure in the app with DEMOShark, record the result as evidence,
    and re-synthesize so steps become verified or stale."""
    from megamouth import pipeline
    from megamouth.verify import verify as run_verify

    ws = _ws(workspace)
    path = pipeline.proposed_path(ws, pipeline.spec_id(ws))
    if not path.exists():
        con.print("[red]no proposed spec yet: run `megamouth synth` first[/red]")
        raise typer.Exit(2)
    rep = run_verify(ws, specmod.load(path), procedure, lang)
    for pid, blockers in rep.skipped:
        con.print(f"  [yellow]skip[/yellow] {pid}: cannot lower ({len(blockers)} blocker(s), see `megamouth lower`)")
    for r in rep.results:
        con.print(f"  {'[green]PASS[/green]' if r.passed else '[red]FAIL[/red]'} {r.procedure} ({r.lang}) {escape(r.detail)}")
    s, _ = pipeline.synth(ws)
    _summary(s)
    raise typer.Exit(0 if rep.ok else 1)


@app.command("diff")
def diff_cmd(
    old: Optional[Path] = typer.Argument(None, help="older spec (default: the committed version of the proposed spec)"),
    new: Optional[Path] = typer.Argument(None, help="newer spec (default: the proposed spec on disk)"),
    workspace: Path = WS,
    fmt: str = typer.Option("markdown", "--format", help="markdown | json"),
):
    """Semantic diff: what changed in steps, processes, conflicts and review, and what will rebuild."""
    from megamouth import pipeline
    from megamouth.diff import diff, to_json, to_markdown

    if new is None:
        ws = _ws(workspace)
        new = pipeline.proposed_path(ws, pipeline.spec_id(ws))
    b = specmod.load(new)
    if old is None:
        a = pipeline.committed_spec(_ws(workspace), new)
        if a is None:
            con.print("[yellow]no committed version to compare with[/yellow]")
            raise typer.Exit(0)
    else:
        a = specmod.load(old)
    d = diff(a, b)
    print(to_json(d) if fmt == "json" else to_markdown(d, b))


@app.command()
def propose(
    workspace: Path = WS,
    push: bool = typer.Option(False, "--push", help="push the branch"),
    pr: bool = typer.Option(False, "--pr", help="push and open a pull request with the semantic diff (gh)"),
):
    """Synthesize, then commit the proposed spec on a new branch with the semantic diff as the message."""
    from megamouth import pipeline
    from megamouth.diff import diff, to_markdown

    ws = _ws(workspace)
    s, path = pipeline.synth(ws)
    before = pipeline.committed_spec(ws, path)
    if before is not None:
        d = diff(before, s)
        if d.empty:
            con.print("Nothing to propose: no semantic changes since the last commit.")
            raise typer.Exit(0)
        body = to_markdown(d, s)
    else:
        body = "First proposal of this spec.\n"
    branch = pipeline.propose(ws, s, path, body, push, pr)
    con.print(f"Committed on branch [bold]{branch}[/bold]")
    print(body)


@app.command()
def watch(
    paths: Optional[list[Path]] = typer.Argument(None, help="default: inbox/"),
    workspace: Path = WS,
    interval: float = typer.Option(2.0, "--interval"),
    verify: bool = typer.Option(False, "--verify", help="re-run grounding for procedures that changed"),
    once: bool = typer.Option(False, "--once", help="one pass, then exit (for cron or CI)"),
):
    """Watch the inbox: on any change, re-ingest, re-synthesize and print the semantic diff."""
    from megamouth.watch import watch as run_watch

    ws = _ws(workspace)
    inbox = paths or [ws.root / "inbox"]
    con.print(f"Watching {', '.join(map(str, inbox))} (Ctrl+C to stop)" if not once else "One pass")
    try:
        run_watch(ws, inbox, interval, verify, once, print)   # plain print: markdown, not rich markup
    except KeyboardInterrupt:
        pass


@app.command()
def review(workspace: Path = WS, port: int = typer.Option(8790, "--port"), host: str = "127.0.0.1"):
    """Open the review console: resolve conflicts, accept review items, correct steps."""
    try:
        import uvicorn
    except ImportError:
        con.print("[red]install the serve extra: uv sync --extra serve[/red]")
        raise typer.Exit(2)
    from megamouth.review import create_app

    ws = _ws(workspace)
    con.print(f"Review console on [bold]http://{host}:{port}[/bold]")
    uvicorn.run(create_app(ws), host=host, port=port, log_level="warning")


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
