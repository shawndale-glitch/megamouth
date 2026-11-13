"""Process Explorer (ARCHITECTURE §6 item 1): the living process map.

A swimlane view of each process with a token flowing along the main path. Each
task is coloured by the status of the procedure that performs it (verified /
stale / reviewed / proposed). Clicking a task opens its steps, each with its GIF,
its evidence trail and its open conflicts, plus links to the click-along replay
and the video. The layout is computed here (pure, testable); the page only draws.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from megamouth.core.spec import Process
from megamouth.render import RenderCtx

TEMPLATE = Path(__file__).with_name("explorer.html")
COL, LANE_H, NODE_W, NODE_H, LEFT, TOP = 200, 150, 150, 64, 150, 20


def layout(proc: Process) -> dict:
    """Lanes as rows, columns by longest path from the start (back edges ignored)."""
    nodes = {n.id: n for n in proc.nodes if n.type in ("start", "end", "task", "gateway", "event")}
    edges = [(n.id, f.to, f.when) for n in nodes.values() for f in n.out if f.to in nodes]
    succ: dict[str, list[str]] = {k: [] for k in nodes}
    for a, b, _ in edges:
        succ[a].append(b)
    # depth-first order marks back edges (loops such as "query supplier → match again")
    back, state = set(), {}

    def dfs(u):
        state[u] = 1
        for v in succ[u]:
            if state.get(v) == 1:
                back.add((u, v))
            elif v not in state:
                dfs(v)
        state[u] = 2

    starts = [k for k, n in nodes.items() if n.type == "start"] or list(nodes)[:1]
    for s in starts:
        if s not in state:
            dfs(s)
    for k in nodes:
        if k not in state:
            dfs(k)
    rank = {k: 0 for k in nodes}
    for _ in range(len(nodes)):
        changed = False
        for a, b, _ in edges:
            if (a, b) not in back and rank[b] < rank[a] + 1:
                rank[b] = rank[a] + 1
                changed = True
        if not changed:
            break

    lanes = list(proc.lanes) or [""]
    if any(n.lane not in lanes for n in nodes.values()):
        lanes.append("")
    pos, used = {}, {}
    for k, n in sorted(nodes.items(), key=lambda kv: rank[kv[0]]):
        li = lanes.index(n.lane) if n.lane in lanes else len(lanes) - 1
        slot = used.get((li, rank[k]), 0)
        used[(li, rank[k])] = slot + 1
        x = LEFT + rank[k] * COL + 20
        y = TOP + li * LANE_H + (LANE_H - NODE_H) / 2 + slot * (NODE_H + 8)
        pos[k] = {"x": x, "y": y, "w": NODE_W if n.type in ("task",) else NODE_H, "h": NODE_H}

    def anchor(k, side):
        p = pos[k]
        return (p["x"] + (p["w"] if side == "r" else 0), p["y"] + p["h"] / 2)

    paths = []
    for a, b, label in edges:
        (x1, y1), (x2, y2) = anchor(a, "r"), anchor(b, "l")
        if (a, b) in back or x2 <= x1:   # loop back: arc under both nodes
            bx1, by1 = pos[a]["x"] + pos[a]["w"] / 2, pos[a]["y"] + pos[a]["h"]
            bx2, by2 = pos[b]["x"] + pos[b]["w"] / 2, pos[b]["y"] + pos[b]["h"]
            dip = max(by1, by2) + 46
            d = f"M{bx1:.0f},{by1:.0f} C{bx1:.0f},{dip:.0f} {bx2:.0f},{dip:.0f} {bx2:.0f},{by2:.0f}"
        else:
            mx = (x1 + x2) / 2
            d = f"M{x1:.0f},{y1:.0f} C{mx:.0f},{y1:.0f} {mx:.0f},{y2:.0f} {x2:.0f},{y2:.0f}"
        paths.append({"from": a, "to": b, "label": label, "d": d, "back": (a, b) in back})

    # main path for the token: from a start, prefer "yes"/unlabelled forward edges
    main, cur, seen = [], starts[0] if starts else None, set()
    while cur and cur not in seen:
        seen.add(cur)
        fwd = [p for p in paths if p["from"] == cur and not p["back"]]
        if not fwd:
            break
        fwd.sort(key=lambda p: (p["label"].lower() not in ("", "yes", "sí", "si"), p["label"]))
        main.append(fwd[0]["d"])
        cur = fwd[0]["to"]

    width = LEFT + (max(rank.values(), default=0) + 1) * COL + 40
    return {"lanes": lanes, "lane_h": LANE_H, "left": LEFT, "top": TOP, "width": width,
            "height": TOP + len(lanes) * LANE_H + 30, "pos": pos, "edges": paths, "main": " ".join(main)}


def _text(v, lang="en") -> str:
    if v is None:
        return ""
    return v if isinstance(v, str) else v.get(lang) or next(iter(v.values()))


class ExplorerRenderer:
    name = "explorer"
    order = 90

    def run(self, ctx: RenderCtx) -> list[Path]:
        spec, site, store = ctx.spec, ctx.site, ctx.ws.store
        lang = spec.meta.languages[0]
        ev_dir = site / "evidence"
        ev_dir.mkdir(exist_ok=True)

        def ev(eid: str) -> dict:
            e = store.get(eid)
            if e is None:
                return {"kind": "decision" if "overlay" in eid else "evidence", "label": eid.split("#")[-1]}
            out = {"kind": e.kind, "source": e.source_id, "locator": e.locator,
                   "label": str(e.data.get("text") or e.data.get("name") or e.data.get("status") or "")[:120]}
            blob = e.data.get("blob") or e.data.get("shot")
            if blob and store.blob_path(blob).exists():
                if not (ev_dir / blob).exists():
                    shutil.copy(store.blob_path(blob), ev_dir / blob)
                out["image"] = f"evidence/{blob}"
            src = spec_sources.get(e.source_id)
            if src:
                out["uri"] = src
            return out

        spec_sources = {s.get("id"): s.get("uri", "") for s in spec.provenance.sources}
        procs = {}
        for p in spec.procedures:
            steps = []
            for st in p.steps:
                gif = site / "media" / f"{p.id}.{lang}.{st.id}.gif"
                steps.append({"id": st.id, "status": st.status, "say": _text(st.say, lang),
                              "result": _text(st.result, lang), "clip": st.media.clip if st.media else "",
                              "gif": f"media/{gif.name}" if gif.exists() else "",
                              "evidence": [ev(e) for e in st.evidence]})
            links = {}
            if (site / "replay" / f"{p.id}.{lang}.html").exists():
                links["replay"] = f"replay/{p.id}.{lang}.html"
            if (site / "media" / f"{p.id}.{lang}.mp4").exists():
                links["video"] = f"media/{p.id}.{lang}.mp4"
            procs[p.id] = {"id": p.id, "title": _text(p.title, lang), "status": p.status, "steps": steps,
                           "links": links}

        processes = []
        for pr in spec.processes:
            lay = layout(pr)
            nodes = []
            for n in pr.nodes:
                if n.id not in lay["pos"]:
                    continue
                conflicts = [{"id": c.id, "summary": c.summary, "status": c.status}
                             for c in spec.provenance.conflicts
                             if c.about == f"{pr.id}.{n.id}" or c.about.startswith(f"{pr.id}.{n.id}.")
                             or (n.procedure and (c.about == n.procedure or c.about.startswith(n.procedure + ".")))]
                nodes.append({"id": n.id, "type": n.type, "name": n.name, "lane": n.lane,
                              "procedure": n.procedure,
                              "status": procs[n.procedure]["status"] if n.procedure in procs else "",
                              "conflicts": conflicts,
                              "out": [{"to": f.to, "when": f.when} for f in n.out],
                              "evidence": [ev(e) for e in n.evidence[:6]], **lay["pos"][n.id]})
            processes.append({"id": pr.id, "name": pr.name, "layout": {k: v for k, v in lay.items() if k != "pos"},
                              "nodes": nodes,
                              "conflicts": [c.model_dump(mode="json") for c in spec.provenance.conflicts
                                            if c.about.startswith(pr.id) and c.status == "open"]})

        data = {"title": _text(spec.meta.title, lang), "processes": processes, "procedures": procs,
                "verification": [v.model_dump(mode="json") for v in spec.provenance.verification],
                "open_conflicts": sum(1 for c in spec.provenance.conflicts if c.status == "open"),
                "review": len(spec.provenance.review)}
        html = TEMPLATE.read_text(encoding="utf-8").replace(
            "/*DATA*/null", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
        page = site / "index.html"
        page.write_text(html, encoding="utf-8")
        return [page]
