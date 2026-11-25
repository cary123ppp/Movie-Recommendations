"""Episode -> finished 16:9 and 9:16 videos."""
import json, os, shutil, subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from .core import FPS, run
from . import movies, trailer, voice, render

CARD = 3.2          # seconds of title card per film (voice starts on the card)
VO_IN = 0.5
CLIP = 1.3          # average montage cut length

WARM = "colorchannelmixer=rr=1.06:gg=0.97:bb=0.86"
GRADE = "eq=contrast=1.08:saturation=0.85:gamma=0.97,unsharp=5:5:0.5," + WARM
SHAPE = {
    ("h", "card"): "crop='min(iw,ih*16/9)':'min(ih,iw*9/16)',scale=1920:1080,gblur=sigma=2,eq=saturation=0.7," + WARM + ",vignette=PI/3.2",
    ("v", "card"): "crop='min(iw,ih*9/16)':ih,scale=1080:1920,gblur=sigma=3,eq=saturation=0.7," + WARM + ",vignette=PI/3.2",
    ("h", "mont"): "crop='min(iw,ih*2.39)':'min(ih,iw/2.39)',scale=1920:804:flags=lanczos," + GRADE + ",vignette=PI/6,noise=alls=4:allf=t,pad=1920:1080:0:138:black",
    ("v", "mont"): "crop='min(iw,ih*16/9)':'min(ih,iw*9/16)',scale=1080:608:flags=lanczos," + GRADE + ",vignette=PI/6,noise=alls=4:allf=t,pad=1080:1920:0:690:black",
}


def prepare_films(ep, cfg, work):
    """Metadata + trailer + shot analysis for every film."""
    metas, sources = {}, {}
    for f in ep["films"]:
        print(f"• #{f['rank']} {f['title']}")
        m = movies.fetch_movie(cfg, f, cfg["cache_dir"])
        if f.get("trailer_file"):
            path = Path(f["trailer_file"]).expanduser()
        else:
            if not m.get("trailer_url"): raise SystemExit(f"No trailer found for {f['title']}. Add  trailer_url: <youtube link>  to the episode file.")
            print("  downloading trailer…")
            path = trailer.download(m["trailer_url"], Path(cfg["cache_dir"]) / "trailers" / f"{m.get('tmdb_id', f['key'])}.mp4")
        info = trailer.probe(path)
        crop = f.get("crop") or trailer.detect_crop(path, info["dur"])
        shots = trailer.analyze(path, crop)
        metas[f["key"]] = m
        sources[f["key"]] = dict(path=str(path), crop=crop, dur=info["dur"], shots=shots,
                                 use_range=f.get("use_range"), exclude=[tuple(x) for x in f.get("exclude", [])], card_at=f.get("card_at"))
    return metas, sources


def build_timeline(ep, vo, sources):
    secs, t = [], 0.0
    def add(**k):
        nonlocal t
        k["start"] = round(t, 3); secs.append(k); t += k["dur"]
    add(kind="hook", key="hook", dur=round(max(5.0, vo["hook"]["dur"] + 0.6), 3), vo_at=0.3, card=None)
    for f in ep["films"]:
        add(kind="film", key=f["key"], dur=round(max(CARD + 4, VO_IN + vo[f["key"]]["dur"] + 0.35), 3), vo_at=VO_IN, card=CARD)
    add(kind="outro", key="outro", dur=round(max(5.2, vo["outro"]["dur"] + 1.8), 3), vo_at=0.2, card=None)

    jobs, fpos = [], 0
    top = ep["films"][-1]["key"]
    for s in secs:
        end = round((s["start"] + s["dur"]) * FPS)
        if s["kind"] in ("hook", "outro"):
            src = sources[top]; picks, card_t = _picks(src, 4)
            jobs.append(dict(kind="card", src=src, t=(picks[len(picks) // 2] if s["kind"] == "hook" else card_t), n=end - fpos)); fpos = end; continue
        src = sources[s["key"]]
        mdur = s["dur"] - CARD; n = max(1, round(mdur / CLIP))
        picks, card_t = _picks(src, n, mdur / n + 0.1)
        cend = round((s["start"] + CARD) * FPS)
        jobs.append(dict(kind="card", src=src, t=card_t, n=cend - fpos)); fpos = cend
        for i, p in enumerate(picks):
            e = end if i == n - 1 else fpos + round(mdur * FPS / n)
            jobs.append(dict(kind="mont", src=src, t=p, n=e - fpos)); fpos = e
    return dict(total=round(t, 3), frames=fpos, sections=secs), jobs


def _picks(src, n, clen=CLIP + 0.1):
    picks, card_t = trailer.pick(src["shots"], src["dur"], n, clen, use_range=src.get("use_range"), exclude=src.get("exclude", ()))
    if src.get("card_at") is not None: card_t = float(src["card_at"])
    return picks, card_t


def build_base(fmt, jobs, work, frames):
    cdir = Path(work) / f"clips_{fmt}"; shutil.rmtree(cdir, ignore_errors=True); cdir.mkdir(parents=True)
    def one(i_j):
        i, j = i_j; out = cdir / f"c{i:03d}.mp4"
        vf = (f"crop={j['src']['crop']}," if j["src"].get("crop") else "") + SHAPE[(fmt, j["kind"])] + ",format=yuv420p"
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{j['t']:.3f}", "-i", j["src"]["path"], "-vf", vf, "-r", str(FPS), "-frames:v", str(j["n"]),
             "-an", "-c:v", "libx264", "-crf", "17", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(out) + ".tmp.mp4"])
        os.replace(str(out) + ".tmp.mp4", out); return out
    with ThreadPoolExecutor(max(1, min(4, (os.cpu_count() or 2)))) as ex:
        files = list(ex.map(one, enumerate(jobs)))
    (cdir / "list.txt").write_text("".join(f"file '{p.name}'\n" for p in files))
    base = Path(work) / f"base_{fmt}.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(cdir / "list.txt"), "-c", "copy", str(base)])
    return base


def make_sfx(work):
    w = Path(work)
    if not (w / "boom.wav").exists():
        run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "aevalsrc='0.9*sin(2*PI*(70-35*t)*t)*exp(-3.2*t)+0.35*(random(0)*2-1)*exp(-14*t)':s=48000:d=1.6",
             "-af", "lowpass=f=900,volume=1.6", str(w / "boom.wav")])
        run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anoisesrc=c=pink:d=0.6:r=48000:a=0.5",
             "-af", "bandpass=f=1800:w=1500,afade=t=in:d=0.5:curve=exp,afade=t=out:st=0.5:d=0.1,volume=0.9", str(w / "whoosh.wav")])


def mix_audio(tl, work, music=None):
    w = Path(work); make_sfx(w); tot = tl["total"]
    ins = ["-f", "lavfi", "-i", f"aevalsrc='0.25*sin(2*PI*41.2*t)+0.18*sin(2*PI*61.7*t)*(0.6+0.4*sin(2*PI*0.25*t))+0.08*sin(2*PI*82.4*t)':s=48000:d={tot}"]
    fl = [f"[0]afade=t=in:d=1.5,afade=t=out:st={tot - 2}:d=2,volume={0.25 if music else 0.5}[a0]"]; vo_lbl = []; k = 1
    def add(path, t, vol, is_vo=False):
        nonlocal k
        ins.extend(["-i", str(path)]); fl.append(f"[{k}]adelay={int(t * 1000)}:all=1,volume={vol}[a{k}]")
        if is_vo: vo_lbl.append(f"a{k}")
        k += 1
    add(w / "boom.wav", 0.05, 0.8)
    for s in tl["sections"]:
        if s["kind"] == "film": add(w / "whoosh.wav", max(0, s["start"] - 0.5), 0.45); add(w / "boom.wav", s["start"], 0.9)
        if s["kind"] == "outro": add(w / "boom.wav", s["start"] + 2.7, 0.7)
        add(w / f"vo_{s['key']}.wav", s["start"] + s["vo_at"], 1.0, True)
    sfx = [f"[a{i}]" for i in range(k) if f"a{i}" not in vo_lbl]
    fl.append("".join(f"[{l}]" for l in vo_lbl) + f"amix=inputs={len(vo_lbl)}:normalize=0,asplit=2[vo][key]")
    parts = sfx + ["[vo]"]
    if music:
        ins.extend(["-stream_loop", "-1", "-i", str(music)])
        fl.append(f"[{k}]atrim=0:{tot},afade=t=in:d=1,afade=t=out:st={tot - 2.5}:d=2.5,volume=0.35[mus];"
                  f"[mus][key]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[duck]")
        parts.append("[duck]")
    else:
        fl.append("[key]anullsink")
    fl.append("".join(parts) + f"amix=inputs={len(parts)}:normalize=0,alimiter=limit=0.9,loudnorm=I=-14:TP=-1.5,aresample=48000[out]")
    out = w / "mix.wav"
    run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", ";".join(fl), "-map", "[out]", "-t", str(tot), "-c:a", "pcm_s16le", str(out)])
    return out


def build(ep, cfg, formats=("h", "v"), preview=False):
    slug = ep["slug"]; work = Path(cfg["cache_dir"]) / "work" / slug; work.mkdir(parents=True, exist_ok=True)
    outdir = Path(cfg["output_dir"]); outdir.mkdir(parents=True, exist_ok=True)
    metas, sources = prepare_films(ep, cfg, work)
    print("• voice-over"); vo = voice.synth_all(ep, cfg, work)
    tl, jobs = build_timeline(ep, vo, sources)
    (work / "timeline.json").write_text(json.dumps(tl, indent=1))
    print(f"• timeline: {tl['total']:.1f}s")
    results = []
    for fmt in formats:
        print(f"• footage [{fmt}]"); base = build_base(fmt, jobs, work, tl["frames"])
        if preview:
            times = [2.5] + [s["start"] + 2.6 for s in tl["sections"] if s["kind"] == "film"][:2] + \
                    [s["start"] + s["card"] + 3 for s in tl["sections"] if s["kind"] == "film"][:2] + [tl["sections"][-1]["start"] + 3.5]
            results += render.render(fmt, tl, ep, metas, vo, cfg["brand"], base, outdir / f"{slug}_preview_{fmt}", only=times); continue
        print(f"• rendering [{fmt}]")
        vid = render.render(fmt, tl, ep, metas, vo, cfg["brand"], base, work / f"video_{fmt}.mp4")
        results.append(vid)
    if preview: return results
    print("• audio"); mix = mix_audio(tl, work, cfg.get("music") or None)
    finals = []
    for fmt in formats:
        name = outdir / f"{slug}_{'16x9' if fmt == 'h' else '9x16'}.mp4"
        run(["ffmpeg", "-v", "error", "-y", "-i", str(work / f"video_{fmt}.mp4"), "-i", str(mix), "-map", "0:v", "-map", "1:a",
             "-c:v", "libx264", "-crf", "23", "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
             "-shortest", "-movflags", "+faststart", str(name)])
        finals.append(name)
    return finals
