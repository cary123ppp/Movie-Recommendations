"""Movie metadata from TMDB (+ IMDb rating / Oscars from OMDb)."""
import json, re
from pathlib import Path
import requests

TMDB = "https://api.themoviedb.org/3"


def _tmdb(cfg, path, **params):
    r = requests.get(TMDB + path, params=params, timeout=20,
                     headers={"Authorization": f"Bearer {cfg['tmdb_token']}", "accept": "application/json"})
    r.raise_for_status(); return r.json()


def _pick_trailer(videos):
    yt = [v for v in videos if v.get("site") == "YouTube" and v.get("type") in ("Trailer", "Teaser")]
    def score(v):
        n = v.get("name", "").lower()
        return ((v.get("type") == "Trailer") * 4 + bool(v.get("official")) * 2 + ("official" in n) * 2
                + (v.get("iso_639_1") == "en") * 2 - ("teaser" in n) - ("tv spot" in n) * 3 + (v.get("size", 0) >= 1080))
    yt.sort(key=score, reverse=True)
    return f"https://www.youtube.com/watch?v={yt[0]['key']}" if yt else None


def parse_awards(text):
    """OMDb 'Awards' text -> (big line, small line) or None."""
    if not text or text == "N/A": return None
    m = re.search(r"Won (\d+) Oscars?", text)
    if m:
        n = int(m.group(1)); return (f"{n} OSCAR{'S' if n > 1 else ''}", "ACADEMY AWARD WINNER")
    m = re.search(r"Nominated for (\d+) Oscars?", text)
    if m:
        n = int(m.group(1)); return ("OSCAR NOMINEE", f"{n} NOMINATION{'S' if n > 1 else ''}")
    m = re.search(r"(\d+) wins?", text)
    if m and int(m.group(1)) >= 3: return (f"{m.group(1)} AWARDS", "WON WORLDWIDE")
    return None


def fetch_movie(cfg, film, cache_dir):
    """Returns a dict with title, year, director, imdb (float), fact, trailer_url, runtime."""
    cdir = Path(cache_dir) / "meta"; cdir.mkdir(parents=True, exist_ok=True)
    cfile = cdir / f"{re.sub(r'[^a-z0-9]+', '-', film['title'].lower())}-{film.get('year', '')}.json"
    if cfile.exists():
        meta = json.loads(cfile.read_text(encoding="utf-8"))
    else:
        meta = dict(title=film["title"], year=film.get("year"))
        if cfg.get("tmdb_token"):
            q = dict(query=film["title"], include_adult="false")
            if film.get("year"): q["year"] = film["year"]
            res = _tmdb(cfg, "/search/movie", **q).get("results") or []
            if not res and film.get("year"): res = _tmdb(cfg, "/search/movie", query=film["title"]).get("results") or []
            if not res: raise SystemExit(f"TMDB couldn't find: {film['title']} ({film.get('year')})")
            mid = film.get("tmdb_id") or res[0]["id"]
            d = _tmdb(cfg, f"/movie/{mid}", append_to_response="videos,credits,external_ids")
            directors = [c["name"] for c in d.get("credits", {}).get("crew", []) if c.get("job") == "Director"]
            meta.update(tmdb_id=mid, title=d.get("title", film["title"]), year=int((d.get("release_date") or "0")[:4] or 0) or film.get("year"),
                        director=directors[0] if directors else "", runtime=d.get("runtime"),
                        imdb_id=d.get("external_ids", {}).get("imdb_id") or d.get("imdb_id"),
                        tmdb_rating=round(d.get("vote_average", 0), 1),
                        trailer_url=_pick_trailer(d.get("videos", {}).get("results", [])),
                        genres=[g["name"] for g in d.get("genres", [])])
        if cfg.get("omdb_key") and meta.get("imdb_id"):
            try:
                o = requests.get("https://www.omdbapi.com/", params=dict(i=meta["imdb_id"], apikey=cfg["omdb_key"]), timeout=20).json()
                if o.get("imdbRating") not in (None, "N/A"): meta["imdb"] = float(o["imdbRating"])
                meta["awards"] = o.get("Awards")
            except Exception as e:
                print("  OMDb lookup failed:", e)
        cfile.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    # episode file always wins over fetched values
    for k in ("director", "year", "imdb", "trailer_url", "fact"):
        if film.get(k) is not None: meta[k] = film[k]
    if meta.get("imdb") is None:
        meta["imdb"] = meta.get("tmdb_rating") or 0
        meta["rating_label"] = "TMDB"
    else:
        meta["rating_label"] = "IMDb"
    if not meta.get("fact"):
        meta["fact"] = parse_awards(meta.get("awards"))
    if not meta.get("fact"):
        g = (meta.get("genres") or ["FILM"])[0].upper()
        meta["fact"] = (f"{meta['runtime']} MIN" if meta.get("runtime") else g, g)
    meta["fact"] = list(meta["fact"])
    meta.setdefault("director", "")
    return meta
