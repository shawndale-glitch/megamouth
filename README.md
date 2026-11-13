# Megamouth 🦈

**Anything in, Living Spec out.** Megamouth reads what a software team already has about how its product works: SOP spreadsheets, process diagrams and screen recordings. It reconciles them into one provenance-tracked **Living Spec** and compiles that spec into documentation. Its companion, [DEMOShark](https://github.com/shawndale-glitch/demoshark), turns a spec into narrated video, decks and guides. Megamouth writes that spec for it.

Status: **P0 spike.** It has three ingesters, deterministic synthesis, a human overlay, and lowering to `demoshark: 1`.

## How it works

```text
 SOP .csv/.xlsx ──┐                    ┌──────────────┐                  ┌─────────────────┐
 BPMN .bpmn ──────┼─► ingest ─► EVIDENCE ─► synth ─► │ LIVING SPEC  │ ─► lower ─►      │ demoshark: 1    │ ─► DEMOShark
 FigJam board ────┤      (atoms with provenance)     │ + conflicts  │                  │ (video·deck·    │
 MP4 + .vtt ──────┘                                  │ + review     │                  │  guide)         │
                                                     └──────▲───────┘                  └─────────────────┘
                                                spec/overlay.yaml (human decisions)
```

- **Evidence** is append-only and content-addressed. Each atom records where it came from: source, row, timestamp or node id, extractor and confidence. Re-ingesting an unchanged file does nothing. A changed file adds a new version, and only that file's atoms change.
- **Synthesis** works deterministically first. It merges a process drawn in both BPMN and FigJam and reports every difference as a **conflict**. It turns SOP rows into executable steps (action, target and expectation). It aligns recording narration to those steps in order, so each step gets its clip and keyframe. Steps the narration skips, and narration the SOP lacks, become conflicts or review items.
- **Human decisions** live in `spec/overlay.yaml` and are reapplied after every synthesis. Each one is cited as `ev:overlay#…`, so re-ingesting never loses them.
- **Lowering** compiles one procedure to `demoshark: 1` and validates it against DEMOShark's schema. If something is missing, Megamouth lists it as a blocker. It never invents content.
- **Models are optional and gated.** `--vlm` reads keyframes with Claude through one gateway. That gateway returns schema-validated output only and refuses any source the policy doesn't clear for cloud use. Without credentials it falls back to manual mode: it writes the prompt to `manual/` and waits for you to paste the answer back.

## Try it

```bash
uv sync --extra all
cd examples/paydesk
uv run megamouth ingest inbox
uv run megamouth synth --id paydesk --title "Paydesk accounts payable"
uv run megamouth lower spec/proposed/paydesk.yaml -p approve-an-invoice
uv run megamouth explain approve-an-invoice.S5 --spec spec/proposed/paydesk.yaml
```

The Paydesk sources describe the same invoice approval in four different ways, and they disagree on purpose. The FigJam board puts *Approve invoice* in an "AP Lead" lane and has no *Query supplier* task. The training recording narrates a due-date check that the SOP doesn't include. `synth` reports all of these. `lower` then produces a walk-through that passes DEMOShark's DS1–DS10 checks with zero findings.

| Command | What it does |
|---|---|
| `megamouth init <dir>` | Create a workspace: config, folders, .gitignore |
| `megamouth doctor` | Check every plugin's requirements |
| `megamouth ingest <paths…>` | Sources → evidence graph |
| `megamouth synth [--vlm]` | Evidence → `spec/proposed/<id>.yaml` + conflicts + review queue |
| `megamouth lower <spec> -p <procedure>` | Procedure → `build/demoshark/<procedure>.yaml` |
| `megamouth explain <element> --spec <spec>` | The full evidence trail of one step or node |
| `megamouth schema` | Export the Living Spec JSON Schema |

## Layout

| Path | What |
|---|---|
| `src/megamouth/core/` | Evidence store, Living Spec models, plugin contracts, workspace policy, model gateway |
| `src/megamouth/ingest/` | `sheets` (SOP), `diagrams` (BPMN, FigJam), `av` (recordings: scene keyframes + subtitles / faster-whisper) |
| `src/megamouth/synth/` | Alignment, instruction parsing, reconciliation |
| `src/megamouth/lowering/` | `demoshark: 1` target and its vendored schema |
| `examples/paydesk/` | Fictional sources and overlay for the demo |

Plugins register through entry points (`megamouth.ingest`), so a new source family is its own package and needs no change to the core.

## Tests

```bash
uv run pytest
```
