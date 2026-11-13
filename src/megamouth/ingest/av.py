"""Screen recordings / GIFs → keyframes + narration (ARCHITECTURE §5, row 1).

Deterministic path, all local:
  1. ffmpeg scene detection → one keyframe per visual change (plus frame 0),
     stored as blobs → `av.keyframe {t, blob, scene_score}`
  2. narration → `av.utterance {start, end, text}` from, in order of preference:
     a sidecar subtitle file (same stem, .vtt or .srt), or local faster-whisper
     if it is installed. Nothing is sent anywhere.
The model-assisted reading of keyframes ("which control was clicked") happens in
`synth`, behind the cloud policy, never here.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable

from megamouth.core.evidence import Evidence, SourceRef
from megamouth.core.plugins import Ctx, Requirement

VIDEO = {".mp4", ".mov", ".webm", ".mkv", ".gif", ".m4v"}
SCENE_THRESHOLD = 0.03   # UI changes are small: a dialog opening moves few pixels
MIN_GAP = 0.4            # seconds; collapses bursts (animations, cursor trails) to one frame
_TS = re.compile(r"(\d+):(\d\d):(\d\d)[.,](\d{1,3})")


def ffmpeg_exe() -> str:
    import shutil

    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("no ffmpeg: install it, or `uv sync --extra av` for imageio-ffmpeg") from e


def fmt_t(t: float) -> str:
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:06.3f}"


def _secs(m: re.Match) -> float:
    h, mi, s, ms = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def parse_subtitles(path: Path) -> list[dict]:
    """Minimal WebVTT / SRT reader: [{start, end, text}]."""
    out: list[dict] = []
    blocks = re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").replace("\r\n", "\n"))
    for b in blocks:
        lines = [ln for ln in b.strip().split("\n") if ln.strip()]
        for i, ln in enumerate(lines):
            if "-->" in ln:
                ts = _TS.findall(ln.replace(",", "."))
                stamps = list(_TS.finditer(ln.replace(",", ".")))
                if len(stamps) == 2 and ts:
                    text = " ".join(lines[i + 1:]).strip()
                    if text:
                        out.append({"start": _secs(stamps[0]), "end": _secs(stamps[1]), "text": text})
                break
    return out


def keyframes(video: Path, outdir: Path, threshold: float = SCENE_THRESHOLD) -> list[dict]:
    """[{t, file, scene}] for frame 0 and every frame whose scene-change score > threshold."""
    pattern = outdir / "kf-%05d.png"
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-i", str(video),
           "-vf", f"select='eq(n\\,0)+gt(scene\\,{threshold})',showinfo",
           "-fps_mode", "vfr", "-frame_pts", "0", str(pattern)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed on {video.name}: {proc.stderr[-800:]}")
    times = [float(m) for m in re.findall(r"pts_time:([\d.]+)", proc.stderr)]
    files = sorted(outdir.glob("kf-*.png"))
    out: list[dict] = []
    for t, f in zip(times, files):
        if out and t - out[-1]["t"] < MIN_GAP:
            out[-1] = {"t": t, "file": f}   # keep the settled frame at the end of a burst
        else:
            out.append({"t": t, "file": f})
    return out


def duration(video: Path) -> float | None:
    proc = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(video)], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d\d):(\d\d)\.(\d+)", proc.stderr)
    return _secs(m) if m else None


class ScreenRecordingIngester:
    name = "av.screenrec"
    version = "0.1"
    kind = "video"
    requires = [
        Requirement("python", "imageio_ffmpeg", "uv sync --extra av (or install ffmpeg)"),
        Requirement("python", "faster_whisper", "uv sync --extra asr: transcribe recordings with no subtitle file", optional=True),
    ]

    def sniff(self, path: Path) -> float:
        return 0.9 if path.suffix.lower() in VIDEO else 0.0

    def ingest(self, path: Path, source: SourceRef, ctx: Ctx) -> Iterable[Evidence]:
        x = f"{self.name}@{self.version}"
        ev = lambda loc, kind, data, conf=1.0: Evidence(
            source_id=source.id, source_sha256=source.sha256, locator=loc, kind=kind,
            extractor=x, data=data, confidence=conf)

        yield ev("meta", "av.meta", {"duration": duration(path), "file": path.name})

        with tempfile.TemporaryDirectory() as tmp:
            for kf in keyframes(path, Path(tmp)):
                blob = ctx.store.put_blob(kf["file"].read_bytes(), ".png")
                yield ev(f"t:{fmt_t(kf['t'])}", "av.keyframe", {"t": round(kf["t"], 3), "blob": blob})

        subs = next((p for p in (path.with_suffix(".vtt"), path.with_suffix(".srt")) if p.exists()), None)
        if subs:
            utterances, how, conf = parse_subtitles(subs), f"subtitles:{subs.name}", 1.0
        else:
            utterances, how, conf = self._transcribe(path, ctx), "faster-whisper", 0.8
        for u in utterances:
            yield ev(f"t:{fmt_t(u['start'])}", "av.utterance",
                     {"start": round(u["start"], 3), "end": round(u["end"], 3), "text": u["text"], "via": how}, conf)

    def _transcribe(self, path: Path, ctx: Ctx) -> list[dict]:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            ctx.log.append(f"{path.name}: no subtitle file and faster-whisper not installed; no narration extracted")
            return []
        model = WhisperModel("small", device="auto", compute_type="int8")
        segments, _ = model.transcribe(str(path), vad_filter=True)
        return [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segments]
