"""Episode -> finished 16:9 and 9:16 videos."""
import json, os, re, shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
from .core import FPS, run
from . import movies, trailer, voice, render, dialogue, semantic

CARD = 3.2          # seconds of title card per film (voice starts on the card)
VO_IN = 0.5
CLIP = 1.3          # average montage cut length
PAD = 0.15          # breathing room around an original-audio moment

WARM = "colorchannelmixer=rr=1.06:gg=0.97:bb=0.86"
GRADE = "eq=contrast=1.08:saturation=0.85:gamma=0.97,unsharp=5:5:0.5," + WARM
SHAPE = {
    ("h", "card"): "crop='min(iw,ih*16/9)':'min(ih,iw*9/16)',scale=1920:1080,gblur=sigma=2,eq=saturation=0.7," + WARM + ",vignette=PI/3.2",
    ("v", "card"): "crop='min(iw,ih*9/16)':ih,scale=1080:1920,gblur=sigma=3,eq=saturation=0.7," + WARM + ",vignette=PI/3.2",
    ("h", "mont"): "crop='min(iw,ih*2.39)':'min(ih,iw/2.39)',scale=1920:804:flags=lanczos," + GRADE + ",vignette=PI/6,noise=alls=4:allf=t,pad=1920:1080:0:138:black",
    ("v", "mont"): "crop='min(iw,ih*16/9)':'min(ih,iw*9/16)',scale=1080:608:flags=lanczos," + GRADE + ",vignette=PI/6,noise=alls=4:allf=t,pad=1080:1920:0:690:black",
}


def sentences(text):
    return [s for s in re.split(r"(?<=[.?!])\s+", text.strip()) if s]


# ---------------------------------------------------------------- films, trailers, dialogue
def prepare_films(ep, cfg):
    metas, sources = {}, {}
    ocfg = cfg.get("originals") or {}
    want_audio = bool(ocfg.get("auto")) or bool(ep.get("cold_open")) or any(f.get("originals") for f in ep["films"])
    for f in ep["films"]:
        print(f"> #{f['rank']} {f['title']}")
        m = movies.fetch_movie(cfg, f, cfg["cache_dir"])
        if f.get("trailer_file"):
            path = Path(f["trailer_file"]).expanduser()
        else:
            if not m.get("trailer_url"): raise SystemExit(f"No trailer found for {f['title']}. Add  trailer_url: <youtube link>  to the episode file.")
            path = Path(cfg["cache_dir"]) / "trailers" / f"{m.get('tmdb_id', f['key'])}.mp4"
            if want_audio and path.exists() and not dialogue.has_audio(path): path.unlink()     # older silent download
            if not path.exists(): print("  downloading trailer...")
            path = trailer.download(m["trailer_url"], path)
        info = trailer.probe(path)
        crop = f.get("crop") or trailer.detect_crop(path, info["dur"])
        shots = trailer.analyze(path, crop)
        metas[f["key"]] = m
        sources[f["key"]] = dict(key=f["key"], path=str(path), crop=crop, dur=info["dur"], shots=shots, use_range=f.get("use_range"),
                                 exclude=[tuple(x) for x in f.get("exclude", [])], card_at=f.get("card_at"), lines=None)
    return metas, sources


def _lines(src, cfg):
    if src["lines"] is None:
        cache = Path(cfg["cache_dir"]) / "dialogue" / (Path(src["path"]).stem + ".json"); cache.parent.mkdir(parents=True, exist_ok=True)
        src["all_lines"] = dialogue.transcribe(src["path"], cache)
        src["lines"] = dialogue.usable(src["all_lines"], src["dur"])
    return src["lines"]


def resolve_originals(ep, cfg, sources):
    """Fill f['_orig'] = [dict(quote, at, after_sentence)] and ep['_cold'] = dict(film, quote, at) | None."""
    ocfg = cfg.get("originals") or {}; auto = bool(ocfg.get("auto")); per = int(ocfg.get("per_film", 1))
    can_llm = bool(cfg.get("deepseek_key"))
    for f in ep["films"]:
        src = sources[f["key"]]; sents = sentences(f["vo"]); spec = f.get("originals")
        if spec is None and auto and can_llm: spec = "auto"
        out = []
        if spec == "auto":
            if not can_llm: print("  ! originals: auto needs deepseek_key, skipping")
            else:
                lines = _lines(src, cfg)
                if lines: out = dialogue.pick_moments(cfg, f, sents, lines, per)
        elif isinstance(spec, list):
            for o in spec:
                o = dict(o)
                if not o.get("at"):
                    _lines(src, cfg); hit = dialogue.find_quote(src.get("all_lines") or [], o.get("quote", ""))
                    if not hit: print(f"  ! couldn't find \"{o.get('quote')}\" in the {f['title']} trailer, skipping"); continue
                    o["at"] = [hit["t0"], hit["t1"]]; o["quote"] = o.get("quote") or hit["text"]
                o["after_sentence"] = max(2, min(len(sents), int(o.get("after_sentence", len(sents)))))
                out.append(o)
        f["_orig"] = sorted(out, key=lambda o: o["after_sentence"])
        for o in f["_orig"]: print(f"  #{f['rank']} original audio after sentence {o['after_sentence']}: \"{o['quote']}\"")
    cold = ep.get("cold_open")
    if cold is None and auto and can_llm and ocfg.get("cold_open", True): cold = "auto"
    ep["_cold"] = None
    if cold == "auto" and can_llm:
        def free(f):   # don't reuse a line already placed inside that film
            used = [o["at"] for o in f["_orig"]]
            return [l for l in _lines(sources[f["key"]], cfg) if all(l["t1"] <= a - 0.3 or l["t0"] >= b + 0.3 for a, b in used)]
        pools = {f["key"]: (f, free(f)) for f in ep["films"]}
        ep["_cold"] = dialogue.pick_cold_open(cfg, f"{ep['hook'].get('overline', '')} {ep['hook'].get('headline', '')}", pools)
    elif isinstance(cold, dict) and cold.get("quote"):
        key = cold.get("film") or ep["films"][-1]["key"]
        if isinstance(key, int) or str(key).isdigit(): key = f"r{key}"
        at = cold.get("at")
        if not at:
            hit = None
            for k in [key] + [f["key"] for f in ep["films"] if f["key"] != key]:
                _lines(sources[k], cfg); hit = dialogue.find_quote(sources[k].get("all_lines") or [], cold["quote"])
                if hit: key = k; break
            at = [hit["t0"], hit["t1"]] if hit else None
        if at: ep["_cold"] = dict(film=key, quote=cold["quote"], at=at)
    if ep["_cold"]: print(f"> cold open: \"{ep['_cold']['quote']}\"")


def voice_items(ep):
    """Split each film's narration at its original-audio moments -> list of (key, text)."""
    items = [("hook", ep["hook"]["vo"])]
    for f in ep["films"]:
        sents = sentences(f["vo"]); cuts = sorted({o["after_sentence"] for o in f["_orig"] if o["after_sentence"] < len(sents)})
        bounds = [0] + cuts + [len(sents)]; f["_parts"] = []
        for i in range(len(bounds) - 1):
            k = f"{f['key']}_p{i}"; items.append((k, " ".join(sents[bounds[i]:bounds[i + 1]]))); f["_parts"].append((k, bounds[i + 1]))
    items.append(("outro", ep["outro"]["vo"]))
    return items


# ---------------------------------------------------------------- timeline
def build_timeline(ep, vo):
    secs, t = [], 0.0
    def add(**k):
        nonlocal t
        k["start"] = round(t, 3); secs.append(k); t += k["dur"]
    c = ep.get("_cold")
    if c:
        d = (c["at"][1] - c["at"][0]) + 2 * PAD
        add(kind="cold", key="cold", dur=round(d + 0.25, 3), card=None,
            blocks=[dict(type="orig", film=c["film"], at=0.0, dur=round(d, 3), t0=c["at"][0] - PAD, quote=c["quote"])])
    add(kind="hook", key="hook", dur=round(max(5.0, vo["hook"]["dur"] + 0.6), 3), card=None,
        blocks=[dict(type="vo", key="hook", at=0.3, dur=vo["hook"]["dur"])])
    for f in ep["films"]:
        blocks, x, origs = [], VO_IN, {}
        for o in f["_orig"]: origs.setdefault(o["after_sentence"], []).append(o)
        for k, end_sent in f["_parts"]:
            blocks.append(dict(type="vo", key=k, at=round(x, 3), dur=vo[k]["dur"])); x += vo[k]["dur"]
            for o in origs.get(end_sent, []):
                x = max(x + 0.1, CARD + 0.3); d = (o["at"][1] - o["at"][0]) + 2 * PAD
                blocks.append(dict(type="orig", film=f["key"], at=round(x, 3), dur=round(d, 3), t0=o["at"][0] - PAD, quote=o["quote"])); x += d + 0.15
        add(kind="film", key=f["key"], dur=round(max(CARD + 4, x + 0.35), 3), card=CARD, blocks=blocks)
    add(kind="outro", key="outro", dur=round(max(5.2, vo["outro"]["dur"] + 1.8), 3), card=None,
        blocks=[dict(type="vo", key="outro", at=0.2, dur=vo["outro"]["dur"])])
    return dict(total=round(t, 3), frames=round(t * FPS), sections=secs)


class ShotChooser:
    """Picks montage clips for one film: matched to the narration when CLIP is available, else spread evenly."""
    def __init__(self, src, cfg, avoid):
        ex = list(src.get("exclude", ())) + avoid
        self.good, self.lo, self.hi = trailer.good_shots(src["shots"], src["dur"], use_range=src.get("use_range"), exclude=ex)
        self.card_t, card = trailer.card_shot(self.good, self.lo)
        if src.get("card_at") is not None: self.card_t = float(src["card_at"])
        self.pool = [s for s in self.good if s is not card] or self.good
        self.used = np.zeros(len(self.pool)); self.last = -1; self.emb = None
        if cfg.get("semantic_shots", True) and semantic.available():
            try:
                cache = Path(cfg["cache_dir"]) / "clip" / (Path(src["path"]).stem + f"_{len(self.pool)}.npz"); cache.parent.mkdir(parents=True, exist_ok=True)
                self.emb = semantic.shot_embeddings(src["path"], self.pool, src.get("crop"), cache)
            except Exception as e:
                print("  (shot matching unavailable:", e, ")")

    def plan(self, slots, texts):
        """slots: clip lengths; texts: the sentence being spoken during each slot. Returns trailer start times."""
        n = len(slots)
        if n == 0: return []
        if self.emb is None:
            idx = np.linspace(0, len(self.pool) - 1, n).round().astype(int) if n > 1 else [len(self.pool) // 2]
            return [trailer.clip_from(self.pool[i], L, self.lo, self.hi) for i, L in zip(idx, slots)]
        tem = semantic.text_embeddings(texts); out = []
        for i, L in enumerate(slots):
            score = self.emb @ tem[i] - 0.08 * self.used
            if self.last >= 0: score[self.last] -= 1.0
            j = int(np.argmax(score)); self.used[j] += 1; self.last = j
            out.append(trailer.clip_from(self.pool[j], L, self.lo, self.hi))
        return out


def _spoken(sec, vo):
    """[(t0, t1, sentence)] in section time, from the narration's word timings."""
    spans = []
    for b in sec["blocks"]:
        if b["type"] != "vo" or not vo[b["key"]]["words"]: continue
        cur, a = [], None
        for w, ws, we in vo[b["key"]]["words"]:
            if a is None: a = ws
            cur.append(w)
            if re.search(r"[.?!]$", w): spans.append((b["at"] + a, b["at"] + we, " ".join(cur))); cur, a = [], None
        if cur: spans.append((b["at"] + a, b["at"] + vo[b["key"]]["words"][-1][2], " ".join(cur)))
    return spans


def build_jobs(ep, tl, vo, sources, cfg):
    jobs = []; top = ep["films"][-1]["key"]; films = {f["key"]: f for f in ep["films"]}
    def frames(sec, a, b): return round((sec["start"] + b) * FPS) - round((sec["start"] + a) * FPS)
    for sec in tl["sections"]:
        if sec["kind"] in ("hook", "outro"):
            ch = ShotChooser(sources[top], cfg, [])
            t = ch.card_t if sec["kind"] == "outro" else trailer.clip_from(ch.pool[len(ch.pool) // 2], 5, ch.lo, ch.hi)
            jobs.append(dict(kind="card", src=sources[top], t=t, n=frames(sec, 0, sec["dur"]))); continue
        if sec["kind"] == "cold":
            b = sec["blocks"][0]; jobs.append(dict(kind="mont", src=sources[b["film"]], t=max(0, b["t0"]), n=frames(sec, 0, sec["dur"]))); continue
        src = sources[sec["key"]]
        avoid = [(o["at"][0] - 0.5, o["at"][1] + 0.5) for o in films[sec["key"]]["_orig"]]
        if ep.get("_cold") and ep["_cold"]["film"] == sec["key"]: avoid.append((ep["_cold"]["at"][0] - 0.5, ep["_cold"]["at"][1] + 0.5))
        ch = ShotChooser(src, cfg, avoid)
        jobs.append(dict(kind="card", src=src, t=ch.card_t, n=frames(sec, 0, CARD)))
        cuts, x = [], CARD
        for b in [b for b in sec["blocks"] if b["type"] == "orig"] + [None]:
            end = b["at"] if b else sec["dur"]
            if end - x > 0.05:
                n = max(1, round((end - x) / CLIP)); L = (end - x) / n
                cuts += [("m", x + i * L, x + (i + 1) * L, None) for i in range(n)]
            if b: cuts.append(("o", b["at"], b["at"] + b["dur"], b)); x = b["at"] + b["dur"]
        spans = _spoken(sec, vo)
        def said(tm):
            cur = [s for s in spans if s[0] <= tm + 0.3]
            return (cur[-1] if cur else spans[0])[2] if spans else films[sec["key"]]["title"]
        ms = [c for c in cuts if c[0] == "m"]
        starts = iter(ch.plan([c[2] - c[1] + 0.1 for c in ms], [said((c[1] + c[2]) / 2) for c in ms]))
        for kind, a, b, blk in cuts:
            t = next(starts) if kind == "m" else max(0, blk["t0"])
            jobs.append(dict(kind="mont", src=src, t=t, n=frames(sec, a, b)))
    total = sum(j["n"] for j in jobs)
    if total != tl["frames"]: jobs[-1]["n"] += tl["frames"] - total
    return jobs


# ---------------------------------------------------------------- footage + audio
def build_base(fmt, jobs, work):
    cdir = Path(work) / f"clips_{fmt}"; shutil.rmtree(cdir, ignore_errors=True); cdir.mkdir(parents=True)
    def one(i_j):
        i, j = i_j; out = cdir / f"c{i:03d}.mp4"
        vf = (f"crop={j['src']['crop']}," if j["src"].get("crop") else "") + SHAPE[(fmt, j["kind"])] + ",format=yuv420p"
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{j['t']:.3f}", "-i", j["src"]["path"], "-vf", vf, "-r", str(FPS), "-frames:v", str(j["n"]),
             "-an", "-c:v", "libx264", "-crf", "17", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(out) + ".tmp.mp4"])
        os.replace(str(out) + ".tmp.mp4", out); return out
    with ThreadPoolExecutor(max(1, min(4, (os.cpu_count() or 2)))) as ex:
        files = list(ex.map(one, enumerate([j for j in jobs if j["n"] > 0])))
    (cdir / "list.txt").write_text("".join(f"file '{p.name}'\n" for p in files), encoding="utf-8")
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


def mix_audio(tl, work, sources, music=None):
    w = Path(work); make_sfx(w); tot = tl["total"]
    ins = ["-f", "lavfi", "-i", f"aevalsrc='0.25*sin(2*PI*41.2*t)+0.18*sin(2*PI*61.7*t)*(0.6+0.4*sin(2*PI*0.25*t))+0.08*sin(2*PI*82.4*t)':s=48000:d={tot}"]
    fl, voices, sfx, k = [], [], [], 1
    def add(path, t, vol, group):
        nonlocal k
        ins.extend(["-i", str(path)]); fl.append(f"[{k}]aformat=sample_rates=48000:channel_layouts=stereo,adelay={int(t * 1000)}:all=1,volume={vol}[a{k}]")
        group.append(f"a{k}"); k += 1
    hook = next(s for s in tl["sections"] if s["kind"] == "hook")
    add(w / "boom.wav", hook["start"] + 0.05, 0.8, sfx)
    for s in tl["sections"]:
        if s["kind"] == "film": add(w / "whoosh.wav", max(0, s["start"] - 0.5), 0.45, sfx); add(w / "boom.wav", s["start"], 0.9, sfx)
        if s["kind"] == "outro": add(w / "boom.wav", s["start"] + 2.7, 0.7, sfx)
        for i, b in enumerate(s["blocks"]):
            if b["type"] == "vo": add(w / f"vo_{b['key']}.wav", s["start"] + b["at"], 1.0, voices)
            else:
                clip = w / f"orig_{s['key']}_{i}.wav"
                dialogue.cut_clip(sources[b["film"]]["path"], b["t0"] + PAD, b["t0"] + b["dur"] - PAD, clip, PAD)
                add(clip, s["start"] + b["at"], 1.0, voices)
    fl.insert(0, f"[0]aformat=sample_rates=48000:channel_layouts=stereo,afade=t=in:d=1.5,afade=t=out:st={max(0, tot - 2)}:d=2,volume={0.25 if music else 0.5}[a0]")
    fl.append("".join(f"[{l}]" for l in voices) + f"amix=inputs={len(voices)}:normalize=0,apad=whole_dur={tot},asplit=3[vo][key1][key2]")
    fl.append("[a0][key1]sidechaincompress=threshold=0.05:ratio=4:attack=30:release=500[bed]")
    parts = [f"[{l}]" for l in sfx] + ["[vo]", "[bed]"]
    if music:
        ins.extend(["-stream_loop", "-1", "-i", str(music)])
        fl.append(f"[{k}]aformat=sample_rates=48000:channel_layouts=stereo,atrim=0:{tot},afade=t=in:d=1,afade=t=out:st={max(0, tot - 2.5)}:d=2.5,volume=0.35[mus]")
        fl.append("[mus][key2]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[duck]")
        parts.append("[duck]")
    else:
        fl.append("[key2]anullsink")
    fl.append("".join(parts) + f"amix=inputs={len(parts)}:normalize=0,alimiter=limit=0.9,loudnorm=I=-14:TP=-1.5,aresample=48000[out]")
    out = w / "mix.wav"
    run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", ";".join(fl), "-map", "[out]", "-t", str(tot), "-c:a", "pcm_s16le", str(out)])
    return out


# ---------------------------------------------------------------- main entry
def build(ep, cfg, formats=("h", "v"), preview=False):
    slug = ep["slug"]; work = Path(cfg["cache_dir"]) / "work" / slug; work.mkdir(parents=True, exist_ok=True)
    outdir = Path(cfg["output_dir"]); outdir.mkdir(parents=True, exist_ok=True)
    metas, sources = prepare_films(ep, cfg)
    resolve_originals(ep, cfg, sources)
    print("> voice-over"); vo = voice.synth_all(voice_items(ep), cfg, work)
    tl = build_timeline(ep, vo)
    jobs = build_jobs(ep, tl, vo, sources, cfg)
    (work / "timeline.json").write_text(json.dumps(tl, indent=1), encoding="utf-8")
    print(f"> timeline: {tl['total']:.1f}s")
    results = []
    for fmt in formats:
        print(f"> footage [{fmt}]"); base = build_base(fmt, jobs, work)
        if preview:
            films = [s for s in tl["sections"] if s["kind"] == "film"]
            times = [s["start"] + 2.5 for s in tl["sections"] if s["kind"] == "hook"] + [s["start"] + 2.6 for s in films[:2]] + \
                    [s["start"] + s["card"] + 3 for s in films[:2]] + \
                    [s["start"] + b["at"] + b["dur"] / 2 for s in tl["sections"] for b in s["blocks"] if b["type"] == "orig"][:3] + \
                    [tl["sections"][-1]["start"] + 3.5]
            results += render.render(fmt, tl, ep, metas, vo, cfg["brand"], base, outdir / f"{slug}_preview_{fmt}", only=times); continue
        print(f"> rendering [{fmt}]")
        results.append(render.render(fmt, tl, ep, metas, vo, cfg["brand"], base, work / f"video_{fmt}.mp4"))
    if preview: return results
    print("> audio"); mix = mix_audio(tl, work, sources, cfg.get("music") or None)
    finals = []
    for fmt in formats:
        name = outdir / f"{slug}_{'16x9' if fmt == 'h' else '9x16'}.mp4"
        run(["ffmpeg", "-v", "error", "-y", "-i", str(work / f"video_{fmt}.mp4"), "-i", str(mix), "-map", "0:v", "-map", "1:a",
             "-c:v", "libx264", "-crf", "23", "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
             "-shortest", "-movflags", "+faststart", str(name)])
        finals.append(name)
    return finals
