"""Original movie audio: transcribe trailer dialogue, find / pick lines, cut clean clips."""
import difflib, json, re, subprocess
from pathlib import Path
from . import llm

_model = None


def has_audio(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    return bool(out)


def transcribe(trailer, cache_json):
    """Spoken lines in the trailer with start/end seconds. Cached next to the trailer."""
    cache_json = Path(cache_json)
    if cache_json.exists(): return json.loads(cache_json.read_text(encoding="utf-8"))
    if not has_audio(trailer): return []
    global _model
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise SystemExit("Original-audio moments need faster-whisper:  python -m pip install faster-whisper")
    if _model is None:
        print("  loading speech model (first time downloads ~500MB)...")
        _model = WhisperModel("small.en", compute_type="int8")
    wav = cache_json.with_suffix(".wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(trailer), "-vn", "-ac", "1", "-ar", "16000", str(wav)], check=True)
    segs, _ = _model.transcribe(str(wav), word_timestamps=True, language="en", vad_filter=True)
    lines = []
    for s in segs:
        words = [w for w in (s.words or []) if w.word.strip()]
        if not words or s.no_speech_prob > 0.6: continue
        # split long segments at sentence punctuation so each line is short
        cur = []
        for w in words:
            cur.append(w)
            if re.search(r"[.?!]$", w.word.strip()) or w is words[-1]:
                text = "".join(x.word for x in cur).strip()
                lines.append(dict(t0=round(cur[0].start, 2), t1=round(cur[-1].end, 2), text=text,
                                  conf=round(float(sum(x.probability for x in cur) / len(cur)), 3)))
                cur = []
    wav.unlink(missing_ok=True)
    cache_json.write_text(json.dumps(lines, indent=1, ensure_ascii=False), encoding="utf-8")
    return lines


def usable(lines, dur, min_len=0.7, max_len=4.8):
    lo, hi = dur * 0.03, dur * 0.95
    out = []
    for i, l in enumerate(lines):
        d = l["t1"] - l["t0"]; nw = len(l["text"].split())
        if min_len <= d <= max_len and 2 <= nw <= 16 and l["conf"] >= 0.55 and lo <= l["t0"] <= hi:
            out.append(dict(l, id=i))
    return out


def find_quote(lines, quote):
    """Best fuzzy match of a quote among transcript lines (also tries joining neighbours)."""
    q = re.sub(r"[^a-z0-9 ]", "", quote.lower()); best, score = None, 0
    for i in range(len(lines)):
        for j in range(i, min(i + 3, len(lines))):
            t = " ".join(l["text"] for l in lines[i:j + 1]); n = re.sub(r"[^a-z0-9 ]", "", t.lower())
            s = difflib.SequenceMatcher(None, q, n).ratio()
            if q and q in n: s = max(s, 0.9)
            if s > score: best, score = dict(t0=lines[i]["t0"], t1=lines[j]["t1"], text=t), s
    return best if score >= 0.6 else None


PICK_SYS = """You are the editor of a short movie-ranking video. Between narration sentences we can drop 2-4 second moments of
ORIGINAL movie dialogue taken from the trailer. Pick the best moments.
Good picks: iconic or emotionally loaded lines, understandable without context, that echo or pay off what the narration just said.
Avoid: trailer-narrator voice-overs ("In a world..."), studio/date text read aloud, mumbled or partial lines, big spoilers.
Never place a moment before sentence 2 (the title card is on screen then). Return JSON only."""


def pick_moments(cfg, film, sentences, lines, max_n=1):
    if not lines or max_n <= 0: return []
    numbered = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(sentences))
    cands = "\n".join(f"[{l['id']}] ({l['t1'] - l['t0']:.1f}s) {l['text']}" for l in lines)
    user = (f"Film: {film['title']} ({film.get('year', '')})\n\nNarration sentences:\n{numbered}\n\nTrailer dialogue lines:\n{cands}\n\n"
            f"Choose at most {max_n} line(s). For each give the line id and after_sentence (2..{len(sentences)}). "
            'If nothing is good, return an empty list. JSON: {"picks": [{"id": int, "after_sentence": int, "why": str}]}')
    res = llm.chat_json(cfg, PICK_SYS, user, temperature=0.2).get("picks") or []
    by_id = {l["id"]: l for l in lines}; out = []
    for p in res[:max_n]:
        l = by_id.get(int(p.get("id", -1)))
        if not l: continue
        k = max(2, min(len(sentences), int(p.get("after_sentence", len(sentences)))))
        out.append(dict(quote=l["text"], at=[l["t0"], l["t1"]], after_sentence=k))
    return out


COLD_SYS = """Pick ONE line of original movie dialogue to open a movie-ranking video, before any narration: the 2-4 seconds that make a
viewer stop scrolling. It must work with zero context and hint at the video's theme. Return JSON only."""


def pick_cold_open(cfg, theme, pools):
    """pools: {film_key: (film, lines)} -> dict(film_key, quote, at) or None"""
    rows = []
    for key, (f, lines) in pools.items():
        rows += [f"[{key}:{l['id']}] ({f['title']}, {l['t1'] - l['t0']:.1f}s) {l['text']}" for l in lines]
    if not rows: return None
    user = f"Video theme: {theme}\n\nCandidate lines:\n" + "\n".join(rows) + '\n\nJSON: {"pick": "filmkey:id", "why": str}'
    pick = str(llm.chat_json(cfg, COLD_SYS, user, temperature=0.2).get("pick", ""))
    if ":" not in pick: return None
    key, lid = pick.rsplit(":", 1); key = key.strip("[] ")
    f, lines = pools.get(key, (None, []))
    l = next((x for x in lines if str(x["id"]) == lid.strip("[] ")), None)
    return dict(film=key, quote=l["text"], at=[l["t0"], l["t1"]]) if l else None


def cut_clip(trailer, t0, t1, out_wav, pad=0.15):
    """Clean, level-matched audio clip of an original line."""
    a = max(0, t0 - pad); d = (t1 - t0) + 2 * pad
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.3f}", "-t", f"{d:.3f}", "-i", str(trailer), "-vn", "-ac", "2", "-ar", "48000",
                    "-af", f"loudnorm=I=-16:TP=-2:LRA=11,afade=t=in:d=0.06,afade=t=out:st={d - 0.12:.3f}:d=0.12", str(out_wav)], check=True)
    return d
