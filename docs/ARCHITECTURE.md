# MEGAMOUTH — anything-in, Living Spec-out

**Status:** v0.1 · P0 spike implemented · 2026-11-13
**Codename:** *Megamouth*, after the filter-feeding shark that swallows everything and keeps what is useful. It is a companion to DEMOShark. Rename freely.
**One line:** It ingests any artifact a software company produces about how its product and processes work (docs, designs, diagrams, spreadsheets, recordings, voice notes, GIFs, tickets, the running app itself). It reconciles them into one provenance-tracked **Living Spec**, and it compiles that spec into immersive documentation: process explorers, narrated video, interactive app replays, in-app tours, decks, guides, and a docs assistant you can talk to.

---

## 1. The core idea: a documentation compiler

DEMOShark already proved the back half: **one spec → many outputs, and the build fails when the app drifts.** Megamouth adds the front half, and borrows its structure from a compiler.

```
 FRONT-ENDS (ingest)          MIDDLE (understand)                BACK-ENDS (render)
 ───────────────────          ───────────────────                ──────────────────
 docx/pdf/md/confluence ─┐
 xls SOP checklists ─────┤    ┌──────────────┐   ┌───────────┐   ┌─► DEMOShark (video+pptx+guide)
 Figma / FigJam ─────────┤    │  EVIDENCE    │   │  LIVING   │   ├─► Process Explorer (animated BPMN)
 BPMN / drawio / vsdx ───┼──► │  GRAPH       │──►│  SPEC     │──►├─► Interactive replay (rrweb-style)
 mermaid / plantuml ─────┤    │ (atoms with  │   │ (canonical│   ├─► In-app tours (overlay JSON)
 screen recordings / GIF ┤    │  provenance) │   │  IR, YAML)│   ├─► Motion explainers (Remotion)
 voice notes / meetings ─┤    └──────┬───────┘   └─────┬─────┘   ├─► Docs site pages (Zensical)
 Jira / tickets / chat ──┤           │ synthesize      │ verify  ├─► Ask-the-docs (RAG/MCP)
 live app (DOM, a11y, ───┘           ▼                 ▼         └─► Lowered specs (demoshark:1,
  OpenAPI, HAR, traces)        conflicts → human   Playwright           procedure.yaml, …)
                               review queue        grounding run
```

Three layers, with one strict rule between them: **each layer only reads the one before it.**

| Layer | What it is | Who writes it | Mutable? |
|---|---|---|---|
| **Evidence Graph** | Atomic facts extracted from sources, each with provenance (source, page / timestamp / node-id, hash, extractor, confidence) | Ingest adapters (deterministic first, model-assisted second) | Append-only; re-ingest makes new versions |
| **Living Spec** | The canonical model: actors, systems, screens, navigation, processes, procedures, rules, glossary, media intent | Synthesizer (Claude) proposes, a human approves, git stores it | Yes, through PRs |
| **Outputs** | Video, pages, tours, decks, explorers | Renderers | Throw-away, always rebuilt |

The value is in the middle. Every output becomes cheap, re-generable and testable. Every claim in the docs can be traced back ("this step came from 00:41 of the training recording and row 17 of the SOP; the Figma frame disagrees").

---

## 2. Design principles (the non-negotiables)

1. **Provenance on everything.** No element in the spec exists without at least one evidence reference. If a fact has no evidence, it is an assumption, and it is labelled as one.
2. **Ground truth beats generation.** Generative models write narration, structure, illustrations and b-roll. **They never invent product UI pixels.** UI frames come from real captures of a non-production environment. This is what keeps "lifelike" from turning into "wrong".
3. **The spec is a test.** Every procedure can be executed (Playwright) and every process can be checked against rules (rule packs). Drift breaks the build, as it does in DEMOShark.
4. **Deterministic first, LLM second, human last.** Parse what can be parsed (BPMN XML, xls, FigJam JSON). Use models only for unstructured material. Route conflicts and low-confidence items to a person.
5. **LLM output is always schema-validated.** Synthesis uses structured output (tool-use with JSON Schema / Pydantic). Invalid output is retried, never written.
6. **Plugins, not features.** Each modality, renderer and rule pack is a plugin with a manifest. The core knows nothing about Figma or video.
7. **Content-addressed and incremental.** Every stage output is keyed by the hash of its inputs and the plugin version. Changing one SOP row rebuilds one procedure and one video, not everything.
8. **Local-first for confidential sources.** Every model-backed step has a local fallback (faster-whisper, Piper / SAPI TTS, local OCR), or is marked *cloud-only* and gated by config. Never production systems. PII redaction (blur and label) is built into the capture stage.
9. **Git is the database of record** for specs. Outputs go to artifacts / Pages / a CDN, never into git history.

---

## 3. The Living Spec (the heart)

It is a **superset** of `demoshark: 1` and of a plain `procedure.yaml`. Both become *lowering targets*: Megamouth compiles down to them, much as a compiler lowers IR to machine code. Proposed top level:

```yaml
megamouth: 1
meta:      { id, title{i18n}, owner, version, environment: test|training|demo, languages: [en] }

glossary:  [ { term, definition{i18n}, aliases, evidence: [...] } ]
actors:    [ { id: clerk, name, role, lanes_in: [...] } ]           # people and roles
systems:   [ { id: paydesk, kind: web-app|api|batch, base_url_env: PAYDESK_URL, adapter } ]

screens:                                    # the NAVIGATION MAP: a graph, not a list
  - id: find-invoice
    system: paydesk
    route_hint: "Invoices › Find"
    landmarks: [ { role: heading, name: Find an invoice } ]   # how to recognise it
    regions:   [ { id: search, role: form }, { id: results, role: table } ]
    captures:  [ { asset: cap/find-invoice@2026-11-12.png, redactions: [results.supplier] } ]
transitions:                                # edges between screens
  - { from: home, to: find-invoice, via: { role: menuitem, name: Invoices } }

processes:                                  # BPMN-like: lanes, activities, gateways, events
  - id: P2P-03
    lanes: [ap-clerk, finance-manager]
    nodes:
      - { id: A1, type: task, lane: finance-manager, name: Approve invoice, procedure: approve-an-invoice }
      - { id: G1, type: gateway, name: Amounts match?, out: [{to: A1, when: yes}, {to: A2, when: no}] }
    sla: { A1: PT10M }

procedures:                                 # the DEMOShark-compatible executable layer
  - id: approve-an-invoice
    starts_from: register-invoice
    fixtures: { invoice: {...} }
    steps:
      - id: S5
        say: { en: Select Approve. }
        do: { action: click, target: { role: button, name: Approve } }
        expect: { target: { role: dialog, name: Approve invoice } }
        media: { emphasis: spotlight, zoom: 1.4, dwell: 1.5s }    # render intent, not pixels
        evidence: [ ev:sop#row6, ev:rec-0412@00:15, ev:figjam:1:8 ]

rules:     [ { id: R-four-eyes, text, applies_to: [...], evidence } ]
personas:  [ { id: new-hire, depth: guided, skip: [glossary-basics] } ]    # output personalisation
media:                                       # global look and feel per renderer
  brand: tokens/paydesk.json
  voice: { engine: auto, preset: smooth }
  shots: { default_transition: smart-zoom }

provenance:
  sources: [ { id: rec-0412, kind: video, uri, sha256, ingested_at, adapter: av.screenrec@0.3 } ]
  conflicts: [ { id: C3, about: approve-an-invoice.S5, claims: [...], status: open|resolved, resolved_by } ]
```

What makes it **"living"**:
- **Status per element** (`proposed → reviewed → verified → stale`). *Verified* means a grounding run passed. *Stale* means a source changed, or a run failed, after verification.
- **Watchers.** A source change (new Figma version, new recording, a Jira ticket closed) re-ingests that source. Only the spec elements that cite the changed evidence are marked for re-synthesis.
- **Semantic diff.** A PR shows "step S5 changed its target; 2 videos and 1 tour will rebuild", not a YAML diff.

---

## 4. Module map (the modular application)

```
megamouth/
├── core/                 # no modality knowledge at all
│   ├── evidence.py       # Evidence atom, provenance, content hashing
│   ├── spec/             # Pydantic models + JSON Schema export + i18n helpers
│   ├── graph.py          # networkx views: process graph, navigation graph
│   ├── pipeline.py       # DAG of stages, content-addressed cache, incremental rebuild
│   ├── plugins.py        # pluggy hookspecs + entry-point discovery + manifests
│   ├── llm.py            # model gateway: structured output, retries, cost log, local fallback
│   └── policy.py         # environment guard (never prod), PII/redaction policy, cloud gating
├── ingest/               # FRONT-ENDS: one plugin per source family
│   ├── docs/             # docx, pdf, md, html, confluence export → sections, tables, steps
│   ├── sheets/           # xls/xlsx/csv SOP checklists (header detection, EN/ES column names)
│   ├── design/           # Figma frames + prototype links → screens, transitions; FigJam → processes
│   ├── diagrams/         # BPMN XML, drawio, vsdx, mermaid, plantuml (parsed); diagram images (VLM)
│   ├── av/               # video, GIF, screen recordings, audio, meetings
│   ├── app/              # LIVE APP: a11y-tree crawl, DOM snapshots, OpenAPI, HAR, Playwright traces
│   ├── tickets/          # Jira, Linear, GitLab issues, release notes → change events
│   └── code/             # routes, UI component names, i18n catalogs → labels and screens
├── synth/                # MIDDLE
│   ├── extract.py        # unstructured evidence → candidate spec fragments (LLM, schema-bound)
│   ├── align.py          # entity resolution: "Approve" == "Aprobar" == frame 1:8
│   ├── reconcile.py      # merge fragments, detect conflicts, score confidence
│   └── lower.py          # Living Spec → demoshark:1 / procedure.yaml / bpmn.xml / tour.json
├── verify/
│   ├── rules/            # rule packs (DEMOShark DS1–DS10, process and procedure rules)
│   ├── ground.py         # Playwright grounding run: every target resolves, every expect holds
│   └── drift.py          # scheduled re-runs → stale marks + issues
├── render/               # BACK-ENDS: one plugin per output
│   ├── demoshark/        # delegate: narrated MP4 + pptx + guide (existing tool)
│   ├── explorer/         # web app: animated process map → drill into step → play clip
│   ├── replay/           # interactive "click-along" replay from DOM snapshots
│   ├── tours/            # in-app overlay tours (Driver.js / Shepherd.js JSON)
│   ├── motion/           # Remotion compositions: explainer animations, smart zoom, GIFs
│   ├── site/             # Zensical / MkDocs pages with embedded media
│   └── assistant/        # RAG index + MCP server: "how do I…?" answered from the spec
├── review/               # human-in-the-loop console (FastAPI + small web UI)
└── interfaces/
    ├── cli.py            # `python -m megamouth …` (Typer)
    ├── api.py            # FastAPI: jobs, spec, review queue, webhooks
    └── mcp_server.py     # lets Claude Code / Claude drive ingest, review and render
```

**Plugin contract (sketch):**

```python
# core/plugins.py
class IngestPlugin(Protocol):
    name: str                      # "av.screenrec"
    version: str
    accepts: list[str]             # MIME types / URI schemes / globs
    requires: list[Requirement]    # binaries, models, env vars, extras → checked by `doctor`
    def sniff(self, source: SourceRef) -> float: ...           # 0..1, how sure it can read this
    def ingest(self, source: SourceRef, ctx: Ctx) -> Iterable[Evidence]: ...

class RenderPlugin(Protocol):
    name: str                      # "render.explorer"
    consumes: list[str]            # spec paths it reads, e.g. ["processes", "procedures.*.steps"]
    def plan(self, spec: LivingSpec, ctx: Ctx) -> list[Job]: ...  # lets the cache skip unchanged work
    def run(self, job: Job, ctx: Ctx) -> list[Artifact]: ...
```

Plugins are discovered through Python entry points (`megamouth.ingest`, `megamouth.render`, `megamouth.rules`). A new use case is a new package, never a core change. `megamouth doctor` reads every plugin's `requires` and tells you exactly what is missing.

---

## 5. How each source family turns into evidence

| Source | Deterministic path | Model-assisted path | What it contributes |
|---|---|---|---|
| **Screen recording / GIF / video** *(highest value)* | ffmpeg decode → scene cuts (PySceneDetect) → keyframes → OCR → cursor and click detection (frame diff around the pointer) | VLM per keyframe: "which control was activated, what screen is this"; speech → transcript (faster-whisper) aligned to frames | **Procedures** (ordered steps with timestamps), screen captures, narration intent |
| **Voice notes / meetings** | ASR + diarization (pyannote) → speaker-tagged transcript | LLM: decisions, rules, exceptions, "we always…" statements | **Rules**, gateways, glossary, open questions |
| **Figma** | REST / MCP: frames, prototype links, component names, variables | VLM only for unlabeled frames | **Screens**, **transitions** (the nav graph), brand tokens |
| **FigJam / BPMN / drawio / vsdx / mermaid** | XML / JSON parse → nodes, edges, lanes (reuse the existing FigJam validator) | Diagram *images*: VLM → graph, flagged low-confidence | **Processes**, lanes, gateways |
| **SOP spreadsheets** | openpyxl / csv → rows | LLM: split compound rows, detect actor and system | Process skeleton, procedure backlog |
| **Docs (docx/pdf/md/Confluence)** | Docling / MarkItDown → structured sections and tables | LLM: imperative sentences → steps; definitions → glossary | Procedures, rules, glossary |
| **Live app** | Playwright: accessibility-tree crawl, DOM snapshots, HAR; OpenAPI import | LLM labels screens from landmarks | **Ground truth** for targets; screen identities; API fixtures |
| **Tickets / release notes** | API pull → change events | LLM: "which screens or steps does this change affect?" | **Staleness signals** (the "living" trigger) |

**Alignment is the hard problem**, not extraction. The same act ("approve an invoice") shows up as a FigJam sticky in Spanish, a Figma frame, an SOP row, 6 seconds of a recording and a button label. `synth/align.py` combines four signals: string similarity across languages (multilingual embeddings), position in the process graph, screen identity (landmarks) and time order. It produces *alignment edges* with scores. Anything below a threshold goes to the review queue.

---

## 6. The "never experienced before" outputs

Ranked by **wow per unit of risk**. The first three are realistic within a POC.

1. **Process Explorer (living process map).** An animated BPMN / swimlane view (bpmn-js or React Flow). Tokens flow through the process; click any task to see its narrated clip, its guide steps, its rules and its *evidence trail* (which recording, which SOP row). Status badges come from the last grounding run: ✅ verified 2 h ago, ⚠ stale since the Figma change.
2. **Click-along replay.** Instead of a passive video, the viewer replays real DOM snapshots (recorded with rrweb during the grounding run). They must click the highlighted control to advance, with narration paced to them. It is a simulator built for free from the test run, without access to the real system.
3. **Smart-zoom motion video.** Remotion compositions driven by the spec's `media:` intent: cursor smoothing, auto zoom to the active region, spotlight, captions, chapter cards, a brand intro. It is the "Screen Studio" look, generated, and re-renders on drift. Exports MP4, GIF snippets per step and vertical shorts.
4. **In-app tours.** The same steps lowered to overlay JSON (Driver.js / Shepherd.js) and injected into a training environment. Docs move *into* the product.
5. **Ask-the-process.** An MCP server and RAG index over the spec plus evidence. "What happens if the amounts don't match?" is answered from gateway G1, with the clip at 00:15 and the source cited. Answers are grounded or refused, never invented.
6. **Personas and languages as compile flags.** `--persona new-hire --lang es` changes depth, pacing, glossary pop-ups and voice. Translation comes from the spec (a "translate" option), never by re-recording by hand.
7. **Generative b-roll (optional, gated).** Text-to-video or image models for intros and concept metaphors *only*, never for UI, and labelled as illustrative. An avatar presenter (Synthesia / HeyGen API) as an optional intro and outro.

---

## 7. Behaviour: CLI and lifecycle

```
python -m megamouth init ./workspace                 # creates workspace, config, .env template
python -m megamouth doctor                           # checks binaries, models, keys, plugin requirements
python -m megamouth ingest ./inbox/** figma://<key>  # any mix of sources → evidence graph
python -m megamouth synth  --scope P2P-03            # proposes spec fragments + conflicts
python -m megamouth review                           # opens review console (or `review --tui`)
python -m megamouth verify --ground                  # Playwright grounding run on non-prod
python -m megamouth render explorer demoshark tours  # any set of renderers
python -m megamouth watch                            # source watchers + scheduled drift runs
python -m megamouth lower --to demoshark:1           # export for the existing tool
python -m megamouth explain approve-an-invoice.S5            # prints the full evidence trail of one element
```

**Lifecycle of one element:**

```
evidence ──synth──► proposed ──review──► reviewed ──ground run pass──► verified
                       ▲                                                   │
                       └───────── source changed / run failed ◄── stale ◄──┘
```

**Gates (human in the loop):** Synthesis never merges by itself. It opens a branch or PR with the semantic diff plus a review checklist. Conflicts block. Low-confidence alignments need one click each. This matches how the pipeline already works (PRs, rule packs, intake).

---

## 8. Toolchain and dependencies

### 8.1 Runtime platform

| Need | Choice | Notes / risk |
|---|---|---|
| Language | **Python 3.12** (3.11 minimum) | Material for MkDocs 9.8 already needs ≥3.11 |
| Package manager | **uv** (lockfile, fast, extras) | Run as `python -m …`: `.exe` shims may be blocked on managed Windows machines |
| JS runtime | **Node 20 LTS+** | Remotion, bpmn-js builds, mermaid-cli, the explorer front-end |
| Media | **ffmpeg** (system, or `imageio-ffmpeg` fallback) | Already in use |
| Browser automation | **Playwright** (Python) | Managed Windows machines may block bundled Chromium → `PW_CHANNEL=chrome` |
| Office conversion | **LibreOffice headless** | vsdx / ppt / doc → open formats; optional |
| Diagram tools | Graphviz; Java 17+ for PlantUML; mermaid-cli (Node) | Optional per plugin |
| OCR | **RapidOCR (ONNX)** default; Tesseract as fallback | Pure pip; no system install for RapidOCR |
| GPU | Optional (CUDA 12) | Whisper and pyannote are 5–20× faster; CPU works for a POC |
| Containers | Dev container + one CI image | Pins the binaries; Windows-native stays first-class |

### 8.2 Python packages (as optional extras, so nobody installs everything)

```toml
[project.optional-dependencies]
core     = ["pydantic>=2", "ruamel.yaml", "jsonschema", "typer", "rich", "pluggy",
            "httpx", "tenacity", "networkx", "diskcache", "jinja2"]
llm      = ["anthropic"]                                         # structured output via tool use
docs     = ["docling", "markitdown", "python-docx", "openpyxl", "xlrd", "python-pptx"]
diagrams = ["lxml", "vsdx"]                                      # BPMN/drawio are XML; mermaid via node
av       = ["faster-whisper", "pyannote.audio", "scenedetect[opencv]",
            "opencv-python-headless", "rapidocr-onnxruntime", "imageio-ffmpeg"]
app      = ["playwright"]
voice    = ["edge-tts"]                                          # + SAPI fallback; Piper for offline
index    = ["lancedb"]                                           # or sqlite-vec; vectors for align + RAG
serve    = ["fastapi", "uvicorn", "mcp"]                         # review console, API, MCP server
render   = ["python-pptx"]                                       # Remotion/explorer are Node packages
all      = ["megamouth[core,llm,docs,diagrams,av,app,voice,index,serve,render]"]
```

### 8.3 Node packages (render side, in `render/web/`)
`remotion` + `@remotion/renderer` (motion video), `bpmn-js` or `reactflow` (explorer), `rrweb` + `rrweb-player` (click-along replay), `driver.js` (tours), `@mermaid-js/mermaid-cli`.

### 8.4 External services and credentials (all optional, all gated)

| Service | Used by | Env var | Fallback |
|---|---|---|---|
| Anthropic API (Claude) | synth, VLM keyframes, assistant | `ANTHROPIC_API_KEY` | Manual mode: write prompts out, paste back results |
| Figma | ingest.design | `FIGMA_TOKEN` | Exported JSON / PNG |
| Hugging Face | pyannote gated models | `HF_TOKEN` | No diarization (single speaker) |
| Jira / GitLab | tickets, watchers | `JIRA_*`, `GITLAB_TOKEN` | Manual change events |
| Premium TTS (ElevenLabs / Azure) | voice | `*_API_KEY` | edge-tts → SAPI → silent |
| Avatar / gen-video | optional intros | provider keys | Skip |
| Target apps | grounding | `<APP>_URL`, `.auth/*.json` | Demo app |

### 8.5 Dependency risks to decide early

| Risk | Detail | Mitigation |
|---|---|---|
| **Licences** | PyMuPDF is **AGPL**; some pyannote models are gated; Remotion needs a **company licence** above a small headcount | Prefer Docling / pypdf; review the Remotion terms before company use; keep AGPL out of core |
| **Cloud data egress** | Recordings and SOPs may contain personal data | `policy.py`: per-source `cloud: allowed/denied`; redaction before any model call; local ASR and OCR by default |
| **Application allow-listing** | Blocks unsigned binaries (bundled chromium, `.exe` shims, native DLLs) | `doctor` detects it; use the installed Chrome; `python -m` everywhere |
| **Model drift** | Extraction quality changes with model versions | Pin model IDs in config; golden-set eval in CI (see §10) |
| **Heavy installs** | torch (via whisper and pyannote) is GBs | `av` extra is opt-in; a CPU-only wheel index option |
| **Sanitization** | Public core vs internal use | The core and the fictional demo (Paydesk) are public, like DEMOShark; organisation-specific rule packs and specs live in separate internal packages; scan for organisation terms before any public commit |

---

## 9. Storage and runtime architecture

- **Workspace = folder + git.** `spec/` (YAML, reviewed), `evidence/` (SQLite or DuckDB index plus a blob store addressed by sha256), `cache/` (stage outputs by hash), `build/` (outputs, git-ignored).
- **Pipeline engine.** Start with an in-process DAG and content-addressed cache (make-like, which DEMOShark's ThreadPoolExecutor already approximates). Graduate to **Prefect** or **Dagster** only when watchers and schedules need a server. Avoid Temporal or Kafka until there is more than one team.
- **Model gateway (`core/llm.py`).** One choke point for every model call: structured output, retries, prompt and version stamping into provenance, token and cost log, redaction hook, offline "manual" mode.
- **Interfaces.** CLI first; FastAPI for the review console and webhooks; **MCP server** so a Claude Code session can run "ingest this folder, show me the conflicts, render the explorer". That is the same agent-driven workflow already used day to day.

---

## 10. How we'll know it works (evaluation)

- **Golden set.** 3–5 processes with hand-made "correct" specs, such as Paydesk approve-an-invoice, with verified DEMOShark specs as the reference. CI measures step recall and precision, alignment accuracy and conflict detection against them on every change.
- **Grounding pass rate.** The share of synthesized procedures that pass a Playwright run unchanged on the first try.
- **Time to first video** from a raw recording plus an SOP row, against writing the DEMOShark spec by hand.
- **Reviewer load.** Review-queue items per procedure; target below 5.

---

## 11. Phased roadmap (proposal)

| Phase | Scope | Proves |
|---|---|---|
| **P0 · Spike (≈2 wks)** | Core, Evidence, Spec models; 3 ingesters: **SOP xls**, **screen recording MP4**, **FigJam/BPMN**; `synth` for procedures only; `lower --to demoshark:1`; render through DEMOShark | Recording + SOP → executable spec → narrated video, with an evidence trail |
| **P1 · Living** | Grounding runs, stale/verified status, watchers (Figma version, file drop), semantic-diff PRs, review console v0 | The "living" loop |
| **P2 · Immersive** | Process Explorer, click-along replay (rrweb), Remotion smart-zoom, GIF per step | The wow outputs |
| **P3 · Breadth** | Docs, Confluence, Jira, voice meetings, live-app crawl, vsdx | "Anything in" |
| **P4 · Scale** | MCP server, assistant, personas, tours, orchestration server, multi-team | Platform |

The P0 spike can run entirely on the **fictional Paydesk app** from DEMOShark, so it is portfolio-safe from day one. Point it at a real (non-production) application only once the core holds.

---

## 12. Decisions (2026-11-13)

1. **Public first.** Megamouth is a public portfolio repository alongside DEMOShark, using the fictional Paydesk domain. Organisation-specific rule packs come later as separate internal packages.
2. **P0 inputs.** SOP spreadsheets, screen recordings, and FigJam / BPMN diagrams.
3. **Cloud models.** Allowed for the fictional demo data, after the policy check in `core/workspace.py`. Real sources stay local-only until cleared.
4. **Remotion licence.** Open; decide before P2.
5. **Name.** Megamouth.
