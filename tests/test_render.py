"""P2 renderers: smart-zoom plan and video, GIFs, click-along replay, process explorer."""
import json
import re
import subprocess
from pathlib import Path

import pytest

from megamouth import pipeline
from megamouth.ingest.av import duration, ffmpeg_exe
from megamouth.render import GroundedRun, grounded_runs, render
from megamouth.render.explorer import layout
from megamouth.render.motion import MAX_ZOOM, plan, render_gifs, render_video
from megamouth.verify import verify

from test_living import ws  # noqa: F401  (fixture: Paydesk workspace with a fake DEMOShark)

STEPS = [
    {"step": "S1", "status": "pass", "start": 0.3, "voice": 1.0, "box": None},
    {"step": "S2", "status": "pass", "start": 1.5, "voice": 1.0, "box": {"x": 40, "y": 40, "width": 60, "height": 20}},
    {"step": "S3", "status": "pass", "start": 2.8, "voice": 1.0, "box": {"x": 520, "y": 300, "width": 60, "height": 20}},
]


def test_plan_zooms_to_controls_and_stays_in_frame():
    keys = plan(STEPS, 640, 360, total=4.5)
    assert [round(k.t, 3) for k in keys] == sorted({round(k.t, 3) for k in keys})   # strictly increasing
    assert keys[0].z == 1.0 and keys[-1].z == 1.0                                     # starts and ends wide
    assert max(k.z for k in keys) <= MAX_ZOOM and max(k.z for k in keys) > 1.2
    for k in keys:
        vw, vh = 640 / k.z, 360 / k.z
        assert vw / 2 - 1e-6 <= k.cx <= 640 - vw / 2 + 1e-6 and vh / 2 - 1e-6 <= k.cy <= 360 - vh / 2 + 1e-6
    # S2's box is top-left: the camera's centre moves toward it
    s2 = next(k for k in keys if 1.5 < k.t <= 2.3)
    assert s2.cx < 320 and s2.cy < 180
    assert {k.z for k in plan(STEPS, 640, 360, total=4.5, zoom=False)} == {1.0}


@pytest.fixture
def clip(tmp_path) -> Path:
    v = tmp_path / "rec.mp4"
    subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=size=640x360:rate=25:duration=4.5", "-pix_fmt", "yuv420p", str(v)], check=True)
    (tmp_path / "rec.vtt").write_text("WEBVTT\n\n00:00:01.500 --> 00:00:02.500\nType the number.\n")
    return v


def test_render_video_and_gifs(clip, tmp_path):
    run = GroundedRun("p", "en", True, {"width": 640, "height": 360}, captions=False,
                      media={"recording": clip, "subtitles": clip.with_suffix(".vtt")}, steps=STEPS)
    out_dir = tmp_path / "site"
    out_dir.mkdir()
    out = out_dir / "p.en.mp4"
    assert render_video(run, out) == []
    assert abs(duration(out) - 4.5) < 0.25                       # same timeline as the recording
    gifs = render_gifs(run, out, "p.en")
    assert [g.name for g in gifs] == ["p.en.S1.gif", "p.en.S2.gif", "p.en.S3.gif"]
    assert all(g.read_bytes()[:6] == b"GIF89a" for g in gifs)


def test_burned_captions_keep_the_camera_wide(clip, tmp_path):
    run = GroundedRun("p", "en", True, {"width": 640, "height": 360}, captions=True,
                      media={"recording": clip}, steps=STEPS)
    notes = render_video(run, tmp_path / "wide.mp4")
    assert any("camera stays wide" in n for n in notes)


def test_explorer_layout_ranks_lanes_and_loops(ws):  # noqa: F811
    spec, _ = pipeline.synth(ws)
    proc = spec.processes[0]
    lay = layout(proc)
    x = {k: v["x"] for k, v in lay["pos"].items()}
    assert x["start"] < x["t-register"] < x["t-match"] < x["g-match"] < x["t-approve"] < x["end"]
    loop = next(e for e in lay["edges"] if e["from"] == "t-query" and e["to"] == "t-match")
    assert loop["back"]                                            # "query supplier → match again" is a loop
    assert lay["lanes"][:2] == ["AP Clerk", "Finance Manager"]
    lane_y = lambda node: lay["pos"][node]["y"]
    assert lane_y("t-approve") > lane_y("t-register")              # Finance Manager lane is below AP Clerk
    assert lay["main"].count("M") == 5                             # start → … → approve → end, via "yes"


def test_render_site_from_a_grounding_run(ws):  # noqa: F811
    spec, _ = pipeline.synth(ws)
    verify(ws, spec)
    spec, _ = pipeline.synth(ws)
    written, log = render(ws, spec)
    site = ws.root / "build" / "site"
    names = {p.relative_to(site).as_posix() for p in written}
    assert {"index.html", "replay/approve-an-invoice.en.html"} <= names
    assert any("no passing run with a recording" in m for m in log)    # the fake runner records no video

    replay = (site / "replay" / "approve-an-invoice.en.html").read_text()
    data = json.loads(re.search(r"const D = (\{.*?\});\n", replay, re.S).group(1))
    assert [s["id"] for s in data["steps"]] == ["S1", "S2", "S3", "S4", "S5", "S6"]
    assert data["steps"][1]["box"] and data["steps"][1]["action"] == "fill"
    assert all((site / "replay" / s["img"]).exists() for s in data["steps"])

    explorer = (site / "index.html").read_text()
    d = json.loads(re.search(r"const D = (\{.*?\});\n", explorer, re.S).group(1))
    approve = next(n for n in d["processes"][0]["nodes"] if n["id"] == "t-approve")
    assert approve["procedure"] == "approve-an-invoice" and approve["status"] == "verified"
    assert any("different lanes" in c["summary"] for c in approve["conflicts"])
    assert d["procedures"]["approve-an-invoice"]["links"]["replay"] == "replay/approve-an-invoice.en.html"
    assert str(ws.root) not in explorer                                 # no machine paths in the site
    for img in re.findall(r'"image": "(evidence/[^"]+)"', explorer):
        assert (site / img).exists()
