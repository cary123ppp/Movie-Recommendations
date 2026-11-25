"""Trailer download, black-bar detection and shot picking."""
import json, re, subprocess
from pathlib import Path
import numpy as np
from .core import ffprobe_json


def download(url, dest):
    dest = Path(dest)
    if dest.exists() and dest.stat().st_size > 100_000: return dest
    import yt_dlp
    dest.parent.mkdir(parents=True, exist_ok=True)
    opts = dict(format="bv*[height<=1080][ext=mp4]/bv*[height<=1080]/b[height<=1080]", outtmpl=str(dest.with_suffix(".%(ext)s")),
                merge_output_format="mp4", quiet=True, no_warnings=True, noplaylist=True)
    with yt_dlp.YoutubeDL(opts) as y: y.download([url])
    got = sorted(dest.parent.glob(dest.stem + ".*"), key=lambda p: p.stat().st_size, reverse=True)
    if not got: raise SystemExit(f"download failed: {url}")
    if got[0] != dest:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(got[0]), "-an", "-c:v", "copy", str(dest)], check=True)
    return dest


def probe(path):
    info = ffprobe_json(path)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    return dict(w=int(v["width"]), h=int(v["height"]), dur=float(info["format"]["duration"]))


def detect_crop(path, dur):
    """Active picture area (removes letterbox bars). Returns 'w:h:x:y'."""
    crops = []
    for frac in (0.3, 0.5, 0.7):
        out = subprocess.run(["ffmpeg", "-ss", f"{dur * frac:.2f}", "-t", "4", "-i", str(path), "-vf", "cropdetect=24:2:0", "-f", "null", "-"],
                             capture_output=True, text=True).stderr
        m = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", out)
        if m: crops.append(tuple(map(int, m[-1])))
    if not crops: return None
    w = max(c[0] for c in crops); h = max(c[1] for c in crops)
    c = next(c for c in crops if c[0] == w) ; c2 = next(c for c in crops if c[1] == h)
    return f"{w - w % 2}:{h - h % 2}:{c[2]}:{c2[3]}"


def analyze(path, crop=None, rate=6):
    """Tiny grayscale pass: luma per sample + cut points."""
    vf = (f"crop={crop}," if crop else "") + f"fps={rate},scale=48:27"
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", vf, "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, 27, 48).astype(np.float32)
    luma = fr.mean((1, 2)); dark = (fr < 22).mean((1, 2))
    diff = np.r_[0, np.abs(np.diff(fr, axis=0)).mean((1, 2))]
    cuts = [0] + [i for i in range(1, len(fr)) if diff[i] > 28 and diff[i] > 2.5 * np.median(diff[max(0, i - 6):i + 6] + 1)] + [len(fr)]
    shots = []
    for a, b in zip(cuts[:-1], cuts[1:]):
        if b - a < 2: continue
        seg_l = luma[a:b]; seg_d = dark[a:b]; motion = diff[a + 1:b].mean() if b - a > 1 else 0
        shots.append(dict(t0=a / rate, t1=b / rate, luma=float(seg_l.mean()), dark=float(seg_d.mean()), motion=float(motion)))
    return shots


def pick(shots, dur, n, clen, skip_head=0.07, skip_tail=0.13, use_range=None, exclude=()):
    """Choose n clip start times (each clen long) spread across the trailer, plus a calm card shot."""
    lo, hi = (use_range if use_range else (dur * skip_head, dur * (1 - skip_tail)))
    def bad(s):
        if s["t1"] <= lo or s["t0"] >= hi: return True
        if s["luma"] < 28 or s["dark"] > 0.62: return True        # black / title-card frames
        return any(not (s["t1"] <= a or s["t0"] >= b) for a, b in exclude)
    good = [s for s in shots if not bad(s) and s["t1"] - s["t0"] >= 0.5]
    if not good: good = [dict(t0=lo, t1=hi, luma=60, dark=0, motion=5)]
    calm = sorted([s for s in good if s["t1"] - s["t0"] >= 2.0], key=lambda s: (s["motion"], -(s["t1"] - s["t0"])))
    card = (calm[0] if calm else max(good, key=lambda s: s["t1"] - s["t0"]))
    card_t = max(lo, card["t0"] + 0.2)
    pool = [s for s in good if s is not card] or good
    # even spread over the pool, prefer longer & livelier shots
    picks = []
    idx = np.linspace(0, len(pool) - 1, min(n, len(pool))).round().astype(int)
    for i in idx:
        s = pool[i]; mid = (s["t0"] + s["t1"]) / 2
        picks.append(max(lo, min(mid - clen / 2, hi - clen)))
    while len(picks) < n:      # trailer with few usable shots: reuse spaced offsets
        picks.append(lo + (hi - lo - clen) * (len(picks) % n) / max(1, n))
    return sorted(picks)[:n], card_t
