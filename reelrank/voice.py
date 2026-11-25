"""Voice-over generation with per-word timing for captions."""
import base64, json, re, subprocess, urllib.request
from pathlib import Path
import numpy as np

SR = 24000
KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"


def _sentences(t):
    return [s for s in re.split(r"(?<=[.?!])\s+", t.strip()) if s]


def _spread(sentence, t0, dur):
    ws = sentence.split(); wt = [len(re.sub(r"\W", "", w)) + 2 for w in ws]; tot = sum(wt); out = []; acc = t0 + 0.03
    for w, k in zip(ws, wt):
        d = (dur - 0.06) * k / tot; out.append([w, round(acc, 3), round(acc + d, 3)]); acc += d
    return out


_kokoro = None
def _kokoro_model(models_dir):
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro
        md = Path(models_dir); md.mkdir(parents=True, exist_ok=True)
        for f in ("kokoro-v1.0.onnx", "voices-v1.0.bin"):
            if not (md / f).exists():
                print(f"  downloading voice model {f} (one time, ~300MB)...")
                urllib.request.urlretrieve(KOKORO_URL + f, md / f)
        _kokoro = Kokoro(str(md / "kokoro-v1.0.onnx"), str(md / "voices-v1.0.bin"))
    return _kokoro


def kokoro(text, out_wav, vcfg, models_dir):
    import soundfile as sf
    k = _kokoro_model(models_dir); parts, words, t = [], [], 0.0
    for s in _sentences(text):
        a, _ = k.create(s, voice=vcfg["kokoro_voice"], speed=float(vcfg["kokoro_speed"]), lang="en-us")
        idx = np.where(np.abs(a) > 0.01)[0]
        a = a[max(0, idx[0] - 600): idx[-1] + 1200] if len(idx) else a
        dur = len(a) / SR; words += _spread(s, t, dur)
        gap = 0.32
        parts += [a, np.zeros(int(gap * SR), dtype=a.dtype)]; t += dur + gap
    audio = np.concatenate(parts); sf.write(out_wav, audio, SR)
    return words, len(audio) / SR


def elevenlabs(text, out_wav, vcfg):
    import urllib.error
    vid = vcfg.get("elevenlabs_voice_id") or "nPczCjzI2devNBz1zQrb"      # "Brian" (deep US narrator) if none set
    body = json.dumps(dict(text=text, model_id=vcfg.get("elevenlabs_model") or "eleven_multilingual_v2",
                           voice_settings=dict(stability=float(vcfg.get("stability", 0.45)), similarity_boost=0.8,
                                               style=float(vcfg.get("style", 0.25)), use_speaker_boost=True))).encode()
    req = urllib.request.Request(f"https://api.elevenlabs.io/v1/text-to-speech/{vid}/with-timestamps",
                                 data=body, headers={"xi-api-key": vcfg["elevenlabs_key"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r: res = json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="ignore")[:300]
        hint = {401: "API key is wrong or expired (check elevenlabs_key in config.yaml)",
                402: "out of ElevenLabs credits/characters for this month",
                404: "voice ID not found (check elevenlabs_voice_id)",
                429: "too many requests, wait a minute and run again"}.get(e.code, "")
        raise SystemExit(f"ElevenLabs error {e.code}: {hint}\n{detail}")
    mp3 = Path(out_wav).with_suffix(".mp3"); mp3.write_bytes(base64.b64decode(res["audio_base64"]))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(mp3), "-ar", str(SR), "-ac", "1", str(out_wav)], check=True)
    al = res.get("alignment") or res.get("normalized_alignment")
    words, cur, start, end = [], "", None, 0
    for ch, a, b in zip(al["characters"], al["character_start_times_seconds"], al["character_end_times_seconds"]):
        if ch.isspace():
            if cur: words.append([cur, round(start, 3), round(end, 3)]); cur = ""
            continue
        if not cur: start = a
        cur += ch; end = b
    if cur: words.append([cur, round(start, 3), round(end, 3)])
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out_wav)],
                               capture_output=True, text=True).stdout)
    return words, dur


def refine_with_whisper(wav, words):
    """If faster-whisper is installed, snap word times to the real audio."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return words
    global _whisper
    try: _whisper
    except NameError: _whisper = WhisperModel("base.en", compute_type="int8")
    segs, _ = _whisper.transcribe(str(wav), word_timestamps=True, language="en")
    heard = [w for s in segs for w in (s.words or [])]
    if abs(len(heard) - len(words)) > max(2, len(words) * 0.15): return words
    # align by index within the overlapping length; keep script spelling
    out = []
    for i, (w, a, b) in enumerate(words):
        j = min(len(heard) - 1, round(i * (len(heard) - 1) / max(1, len(words) - 1)))
        out.append([w, round(heard[j].start, 3), round(heard[j].end, 3)])
    return out


def synth_all(ep, cfg, work):
    work = Path(work); work.mkdir(parents=True, exist_ok=True)
    vcfg = cfg["voice"]; models = Path(cfg["cache_dir"]) / "models"
    sig = json.dumps([vcfg.get("engine"), vcfg.get("kokoro_voice"), vcfg.get("kokoro_speed"), vcfg.get("elevenlabs_voice_id"),
                      vcfg.get("elevenlabs_model"), vcfg.get("stability"), vcfg.get("style")])
    items = [("hook", ep["hook"]["vo"])] + [(f["key"], f["vo"]) for f in ep["films"]] + [("outro", ep["outro"]["vo"])]
    meta_f = work / "vo.json"; meta = json.loads(meta_f.read_text(encoding="utf-8")) if meta_f.exists() else {}
    for key, text in items:
        wav = work / f"vo_{key}.wav"
        if meta.get(key, {}).get("text") == text and meta[key].get("sig") == sig and wav.exists(): continue
        if not text.strip():
            import soundfile as sf; sf.write(wav, np.zeros(int(SR * 0.5), np.float32), SR); meta[key] = dict(text=text, sig=sig, dur=0.5, words=[]); continue
        print(f"  voice: {key}")
        if vcfg.get("engine") == "elevenlabs":
            if not vcfg.get("elevenlabs_key"): raise SystemExit("voice.engine is elevenlabs but voice.elevenlabs_key is empty in config.yaml")
            words, dur = elevenlabs(text, wav, vcfg)
        else:
            words, dur = kokoro(text, wav, vcfg, models); words = refine_with_whisper(wav, words)
        meta[key] = dict(text=text, sig=sig, dur=round(dur, 3), words=words)
        meta_f.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    meta_f.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta
