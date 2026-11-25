"""Command line:  python -m reelrank build episodes/my_episode.yaml"""
import argparse, sys, time
from .core import load_config, load_episode


def main():
    ap = argparse.ArgumentParser(prog="reelrank", description="Turn a ranking script into finished 16:9 and 9:16 videos.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="make the videos")
    b.add_argument("episode"); b.add_argument("--only", choices=["h", "v"], help="h = 16:9 only, v = 9:16 only")
    b.add_argument("--preview", action="store_true", help="render a few still frames instead of the full video (fast check)")
    b.add_argument("--config", default=None)
    c = sub.add_parser("check", help="look up the films and trailers without rendering")
    c.add_argument("episode"); c.add_argument("--config", default=None)
    a = ap.parse_args()

    cfg = load_config(a.config); ep = load_episode(a.episode)
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
    for p in out: print("  ", p)


if __name__ == "__main__":
    main()
