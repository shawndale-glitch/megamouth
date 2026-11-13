"""Smart-zoom video and one GIF per step, with ffmpeg only (ARCHITECTURE §8.6: no Remotion).

1. plan()   → keyframes [(t, zoom, cx, cy)] from where each control was (grounding
               boxes) and when each step started. Pure data: testable, cacheable.
2. zoompan  → the camera: eased zoom toward the active control, pans between
               controls, pulls back for page loads and at the end.
3. captions → burned after the zoom from DEMOShark's WebVTT (libass), so zooming
               never crops them. Needs a run recorded with `--captions none`;
               with burned-in captions the camera stays wide instead.
4. GIFs     → each step's segment, palette-optimised.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from megamouth.ingest.av import duration, ffmpeg_exe
from megamouth.render import GroundedRun, RenderCtx

EASE = 0.7          # seconds a camera move takes
MAX_ZOOM = 1.8
UPSCALE = 2         # zoompan rounds to whole pixels; working at 2× hides the jitter
FPS = 30


@dataclass
class Key:
    t: float
    z: float
    cx: float
    cy: float


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _framing(box: dict, W: int, H: int) -> tuple[float, float, float]:
    """Zoom and centre that put the control comfortably in view, inside the frame."""
    bw, bh = max(box["width"], 1), max(box["height"], 1)
    z = _clamp(min(W / (bw * 4.5), H / (bh * 7)), 1.0, MAX_ZOOM)
    cx, cy = box["x"] + bw / 2, box["y"] + bh / 2
    vw, vh = W / z, H / z
    return z, _clamp(cx, vw / 2, W - vw / 2), _clamp(cy, vh / 2, H - vh / 2)


def plan(steps: list[dict], W: int, H: int, total: float, zoom: bool = True) -> list[Key]:
    keys = [Key(0.0, 1.0, W / 2, H / 2)]
    timed = [s for s in steps if s.get("start") is not None and s.get("status") == "pass"]
    for i, s in enumerate(timed):
        t = float(s["start"])
        acted = t + max(0.6, (s.get("voice") or 2) * 0.55)   # DEMOShark acts after ~55 % of the line
        nxt = float(timed[i + 1]["start"]) if i + 1 < len(timed) else min(total, acted + 1.2)
        prev = keys[-1]
        if zoom and s.get("box"):
            z, cx, cy = _framing(s["box"], W, H)
            far = abs(cx - prev.cx) > W / 3 or abs(cy - prev.cy) > H / 3
            if prev.z > 1.05 and far:  # long jump: pull back a little on the way, like a camera operator
                keys.append(Key(t + EASE / 2, max(1.0, min(prev.z, z) * 0.8), (prev.cx + cx) / 2, (prev.cy + cy) / 2))
            keys.append(Key(t + EASE, z, cx, cy))
        else:
            keys.append(Key(t + EASE, 1.0, W / 2, H / 2))
        hold_until = max(keys[-1].t, nxt - 0.05)
        keys.append(Key(hold_until, keys[-1].z, keys[-1].cx, keys[-1].cy))
    end = keys[-1]
    if end.z != 1.0:
        keys.append(Key(min(total, end.t + EASE), 1.0, W / 2, H / 2))
    # strictly increasing times for the expression builder
    out: list[Key] = []
    for k in keys:
        if out and k.t <= out[-1].t + 1e-3:
            out[-1] = Key(out[-1].t, k.z, k.cx, k.cy) if k.t <= out[-1].t else k
            continue
        out.append(k)
    return out


def _piecewise(keys: list[Key], attr: str, var: str = "it") -> str:
    """Smoothstep-eased piecewise function of time as an ffmpeg expression."""
    expr = f"{getattr(keys[-1], attr):.4f}"
    for a, b in reversed(list(zip(keys, keys[1:]))):
        va, vb = getattr(a, attr), getattr(b, attr)
        if abs(vb - va) < 1e-6:
            seg = f"{va:.4f}"
        else:
            s = f"clip(({var}-{a.t:.3f})/{b.t - a.t:.3f},0,1)"
            seg = f"({va:.4f}+({vb - va:.4f})*{s}*{s}*(3-2*{s}))"
        expr = f"if(lt({var},{b.t:.3f}),{seg},{expr})"
    return expr


def zoompan_filter(keys: list[Key], W: int, H: int) -> str:
    z = _piecewise(keys, "z")
    cx = _piecewise(keys, "cx")
    cy = _piecewise(keys, "cy")
    U = UPSCALE
    # zoompan: x,y are the top-left of the visible window in input pixels
    x = f"clip(({cx})*{U}-iw/zoom/2,0,iw-iw/zoom)"
    y = f"clip(({cy})*{U}-ih/zoom/2,0,ih-ih/zoom)"
    # fps first: zoompan emits one frame per input frame at `fps`, so the input must already run at it
    return (f"fps={FPS},scale={W * U}:{H * U}:flags=lanczos,"
            f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={W}x{H}:fps={FPS}")


# BorderStyle=3: an opaque box behind each line (libass draws it in OutlineColour); alpha 0x26 ≈ 85 % opaque
CAPTION_STYLE = ("FontName=Arial,FontSize=19,PrimaryColour=&H00FFFFFF,OutlineColour=&H26201D1B,"
                 "BackColour=&H26201D1B,BorderStyle=3,Outline=7,Shadow=0,MarginV=26,Alignment=2")


def _ffmpeg(args: list[str], cwd: Path) -> None:
    r = subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *args],
                       capture_output=True, text=True, cwd=cwd)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr[-1500:]}")


def render_video(run: GroundedRun, out: Path) -> list[str]:
    """Write the smart-zoom MP4. Returns notes about what could not be done."""
    notes = []
    src = run.media.get("video") or run.media.get("recording")
    W, H = int(run.viewport.get("width", 1280)), int(run.viewport.get("height", 720))
    total = duration(src) or 0.0
    has_boxes = any(s.get("box") for s in run.steps)
    zoom = has_boxes and not run.captions
    if not has_boxes:
        notes.append("no target boxes in this run (DEMOShark without step boxes): camera stays wide")
    elif run.captions:
        notes.append("captions are burned into the recording: camera stays wide so they are not cropped "
                     "(re-run `megamouth verify` with a DEMOShark that supports --captions none)")
    keys = plan(run.steps, W, H, total, zoom=zoom)
    vf = zoompan_filter(keys, W, H)
    work = out.parent
    subs = run.media.get("subtitles")
    if subs and not run.captions:
        shutil.copy(subs, work / "_captions.vtt")   # plain name: no escaping inside the filter graph
        vf += f",subtitles=_captions.vtt:force_style='{CAPTION_STYLE}'"
    _ffmpeg(["-i", str(src), "-vf", vf, "-map", "0:v", "-map", "0:a?", "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-crf", "22", "-preset", "veryfast", "-c:a", "aac", "-movflags", "+faststart", out.name], cwd=work)
    (work / "_captions.vtt").unlink(missing_ok=True)
    return notes


def render_gifs(run: GroundedRun, video: Path, stem: str) -> list[Path]:
    out = []
    timed = [s for s in run.steps if s.get("start") is not None and s.get("status") == "pass"]
    total = duration(video) or 0
    for i, s in enumerate(timed):
        a = float(s["start"])
        b = float(timed[i + 1]["start"]) if i + 1 < len(timed) else min(total, a + (s.get("voice") or 2) + 1.5)
        if b - a < 0.3:
            continue
        gif = video.parent / f"{stem}.{s['step']}.gif"
        _ffmpeg(["-ss", f"{a:.2f}", "-t", f"{b - a:.2f}", "-i", video.name, "-vf",
                 "fps=10,scale=640:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];"
                 "[b][p]paletteuse=dither=bayer:bayer_scale=4", "-loop", "0", gif.name], cwd=video.parent)
        out.append(gif)
    return out


class MotionRenderer:
    name = "motion"
    order = 10

    def run(self, ctx: RenderCtx) -> list[Path]:
        media = ctx.site / "media"
        media.mkdir(exist_ok=True)
        written = []
        for (proc, lang), run in sorted(ctx.runs.items()):
            src = run.media.get("video") or run.media.get("recording")
            if not run.passed or src is None:
                ctx.log.append(f"motion: {proc} ({lang}) has no passing run with a recording; run `megamouth verify`")
                continue
            stem = f"{proc}.{lang}"
            out = media / f"{stem}.mp4"
            key = [src.stat().st_size, src.stat().st_mtime, run.steps, run.captions, EASE, MAX_ZOOM, CAPTION_STYLE, 2]
            if ctx.cached(out, key):
                written += [out, *sorted(media.glob(f"{stem}.S*.gif"))]
                continue
            for note in render_video(run, out):
                ctx.log.append(f"motion: {proc} ({lang}): {note}")
            for old in media.glob(f"{stem}.S*.gif"):
                old.unlink()
            written += [out, *render_gifs(run, out, stem)]
            ctx.done()
        return written
