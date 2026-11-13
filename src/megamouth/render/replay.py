"""Click-along replay (ARCHITECTURE §6 item 2): practise the task without access to the app.

Built from the grounding run, so every frame is a real screen: the step's
screenshot and the box where the control was. The learner must click the
control to advance; narration is shown and can be spoken (Web Speech API, in the
run's language). A static page with no server: it works from a file share or
an LMS. (A DOM-level replay with rrweb needs DOM capture in DEMOShark; the data
contract here stays the same.)
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from megamouth.render import RenderCtx

TEMPLATE = Path(__file__).with_name("replay.html")


def _text(v, lang: str) -> str:
    if v is None:
        return ""
    return v if isinstance(v, str) else v.get(lang) or next(iter(v.values()))


class ReplayRenderer:
    name = "replay"
    order = 20

    def run(self, ctx: RenderCtx) -> list[Path]:
        out_dir = ctx.site / "replay"
        img = out_dir / "img"
        img.mkdir(parents=True, exist_ok=True)
        procs = {p.id: p for p in ctx.spec.procedures}
        written = []
        for (pid, lang), run in sorted(ctx.runs.items()):
            proc = procs.get(pid)
            if proc is None or not run.passed:
                ctx.log.append(f"replay: {pid} ({lang}) has no passing run; run `megamouth verify`")
                continue
            spec_steps = {s.id: s for s in proc.steps}
            steps = []
            for g in run.steps:
                st = spec_steps.get(g["step"])
                if st is None or not g.get("shot_path") or not Path(g["shot_path"]).exists():
                    continue
                name = f"{pid}.{lang}.{g['step']}{Path(g['shot_path']).suffix}"
                shutil.copy(g["shot_path"], img / name)
                steps.append({
                    "id": g["step"], "say": _text(st.say, lang), "result": _text(st.result, lang),
                    "action": g.get("action") or (st.do.action if st.do else ""),
                    "box": g.get("box"), "img": f"img/{name}",
                    "value": _text(st.do.value, lang) if st.do and st.do.value is not None else "",
                })
            if not steps:
                ctx.log.append(f"replay: {pid} ({lang}) has no screenshots in its run")
                continue
            data = {"title": _text(proc.title, lang), "procedure": pid, "lang": lang,
                    "viewport": run.viewport, "steps": steps,
                    "video": f"../media/{pid}.{lang}.mp4" if (ctx.site / "media" / f"{pid}.{lang}.mp4").exists() else ""}
            page = out_dir / f"{pid}.{lang}.html"
            html = TEMPLATE.read_text(encoding="utf-8")
            html = html.replace("/*DATA*/null", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
            html = html.replace("<html lang=\"en\">", f"<html lang=\"{lang}\">")
            page.write_text(html, encoding="utf-8")
            written.append(page)
        return written
