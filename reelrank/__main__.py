"""Command line.

  python -m reelrank plan  scripts/my_script.txt     free-form script -> episodes/<slug>.yaml (edit plan, review it)
  python -m reelrank check episodes/x.yaml           look up films + trailers only
  python -m reelrank build episodes/x.yaml           make the 16:9 and 9:16 videos
"""
import argparse, sys, time
from pathlib import Path
from .core import ROOT, load_config, load_episode, normalize_episode


def plan(a, cfg):
    from . import planner, pipeline
    text = Path(a.script).read_text(encoding="utf-8")
    print("> reading the script with DeepSeek...")
    data = planner.parse_script(cfg, text)
    ep = normalize_episode(dict(data))
    for f in ep["films"]:
        if not f.get("originals"): f.pop("originals", None)       # no [ORIGINAL] marker -> let auto-pick decide
    if not ep.get("cold_open"): ep.pop("cold_open", None)
    if not cfg.get("tmdb_token"): sys.exit("No TMDB token in config.yaml")
    metas, sources = pipeline.prepare_films(ep, cfg)
    pipeline.resolve_originals(ep, cfg, sources)
    out = dict(slug=ep["slug"], hook=ep["hook"], cold_open=False, films=[], outro=ep["outro"])
    if ep.get("_cold"):
        c = ep["_cold"]; rank = next(f["rank"] for f in ep["films"] if f["key"] == c["film"])
        out["cold_open"] = dict(film=rank, quote=c["quote"], at=[round(x, 2) for x in c["at"]])
    for f in ep["films"]:
        m = metas[f["key"]]
        e = dict(rank=f["rank"], title=m.get("title", f["title"]), year=m.get("year") or f.get("year"), vo=f["vo"])
        if f.get("_orig"):
            e["originals"] = [dict(quote=o["quote"], at=[round(x, 2) for x in o["at"]], after_sentence=o["after_sentence"]) for o in f["_orig"]]
        else:
            e["originals"] = []
        out["films"].append(e)
    dest = ROOT / "episodes" / f"{ep['slug']}.yaml"; i = 2
    while dest.exists() and not a.force: dest = ROOT / "episodes" / f"{ep['slug']}_{i}.yaml"; i += 1
    planner.write_episode(out, dest)
    print(f"\nEdit plan written to: {dest}")
    print("Open it, check the films, narration split and original-audio moments, then run build (or drag it onto make.bat).")


def main():
    ap = argparse.ArgumentParser(prog="reelrank", description="Turn a ranking script into finished 16:9 and 9:16 videos.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="free-form script (.txt) -> editable episode plan (.yaml)")
    p.add_argument("script"); p.add_argument("--force", action="store_true", help="overwrite an existing plan with the same name")
    b = sub.add_parser("build", help="make the videos")
    b.add_argument("episode"); b.add_argument("--only", choices=["h", "v"], help="h = 16:9 only, v = 9:16 only")
    b.add_argument("--preview", action="store_true", help="render a few still frames instead of the full video (fast check)")
    c = sub.add_parser("check", help="look up the films and trailers without rendering")
    c.add_argument("episode")
    for x in (p, b, c): x.add_argument("--config", default=None)
    a = ap.parse_args()
    cfg = load_config(a.config)

    if a.cmd == "plan":
        return plan(a, cfg)
    if a.episode.lower().endswith((".txt", ".md")):
        sys.exit("That's a script, not an episode plan. Run plan on it first:  python -m reelrank plan " + a.episode)
    ep = load_episode(a.episode)
    if not cfg.get("tmdb_token") and not all(f.get("trailer_file") or f.get("trailer_url") for f in ep["films"]):
        sys.exit("No TMDB token. Copy config.example.yaml to config.yaml and fill in tmdb_token.")
    if a.cmd == "check":
        from .movies import fetch_movie
        for f in ep["films"]:
            m = fetch_movie(cfg, f, cfg["cache_dir"])
            print(f"#{f['rank']}  {m['title']} ({m.get('year')})  dir. {m.get('director')}  {m['rating_label']} {m['imdb']}  "
                  f"fact: {' / '.join(m['fact'])}\n     trailer: {f.get('trailer_file') or m.get('trailer_url')}")
        return
    from .pipeline import build
    t0 = time.time()
    out = build(ep, cfg, formats=[a.only] if a.only else ["h", "v"], preview=a.preview)
    print(f"\nDone in {(time.time() - t0) / 60:.1f} min:")
    for p_ in out: print("  ", p_)


if __name__ == "__main__":
    main()
