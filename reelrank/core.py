"""Config + episode loading, shared paths and small helpers."""
import json, os, re, subprocess
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "assets" / "fonts"
FPS = 30


def load_config(path=None):
    p = Path(path or ROOT / "config.yaml")
    cfg = yaml.safe_load(p.read_text()) if p.exists() else {}
    cfg = cfg or {}
    cfg.setdefault("voice", {}); cfg.setdefault("brand", {})
    cfg["tmdb_token"] = os.environ.get("TMDB_TOKEN", cfg.get("tmdb_token", ""))
    cfg["omdb_key"] = os.environ.get("OMDB_KEY", cfg.get("omdb_key", ""))
    cfg["voice"].setdefault("engine", "kokoro")
    cfg["voice"].setdefault("kokoro_voice", "am_michael")
    cfg["voice"].setdefault("kokoro_speed", 1.08)
    cfg["voice"]["elevenlabs_key"] = os.environ.get("ELEVENLABS_KEY", cfg["voice"].get("elevenlabs_key", ""))
    cfg["brand"].setdefault("name", "REELRANK"); cfg["brand"].setdefault("year", 2026)
    cfg.setdefault("output_dir", "output"); cfg.setdefault("cache_dir", "cache")
    for k in ("output_dir", "cache_dir"):
        cfg[k] = str((ROOT / cfg[k]) if not os.path.isabs(cfg[k]) else Path(cfg[k]))
    if cfg.get("music") and not os.path.isabs(cfg["music"]):
        cfg["music"] = str(ROOT / cfg["music"])
    return cfg


def load_episode(path):
    ep = yaml.safe_load(Path(path).read_text())
    ep.setdefault("slug", Path(path).stem)
    films = ep.get("films") or []
    if not films: raise SystemExit("episode has no films")
    for f in films:
        for k in ("rank", "title", "vo"):
            if k not in f: raise SystemExit(f"film is missing '{k}': {f}")
        f["key"] = f"r{f['rank']}"
    ep["films"] = sorted(films, key=lambda f: -f["rank"])
    ep.setdefault("hook", {}); ep.setdefault("outro", {})
    n = len(films)
    h = ep["hook"]
    h.setdefault("overline", "Movies That Feel"); h.setdefault("headline", "TOO REAL")
    h.setdefault("left", [f"{n} FILMS", "RANKED"]); h.setdefault("right", ["HAND PICKED", "MUST WATCH"])
    h.setdefault("vo", "")
    o = ep["outro"]
    o.setdefault("question", ["Which One", "IS YOUR FAVORITE?"])
    o.setdefault("vo", "Which one is your favorite? Tell me in the comments, and follow for more.")
    return ep


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def ffprobe_json(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
