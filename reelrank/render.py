"""Frame renderer: festival-style cards + montage layouts for 16:9 (h) and 9:16 (v)."""
import math, os, re, subprocess, functools
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from . import ornament as orn
from .core import FONTS, FPS

CINZEL, LIME, MONT = str(FONTS / "Cinzel.ttf"), str(FONTS / "Limelight.ttf"), str(FONTS / "Montserrat.ttf")
GOLD = (236, 186, 98)
ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII", "XIV", "XV"]

# layout constants per format
LAY = {
    "h": dict(W=1920, H=1080, band=(138, 942), cap_y=1011, cap_size=46, cap_max=1600),
    "v": dict(W=1080, H=1920, band=(690, 1298), cap_y=1450, cap_size=54, cap_max=880),
}

S = {}   # render state (set in render())


@functools.lru_cache(None)
def font(p, s, w=None):
    f = ImageFont.truetype(p, int(s))
    if w:
        try: f.set_variation_by_axes([w])
        except Exception: pass
    return f

def ease(x): x = min(1, max(0, x)); return 1 - (1 - x) ** 3
def back(x):
    x = min(1, max(0, x)); c = 1.70158
    return 1 + (c + 1) * (x - 1) ** 3 + c * (x - 1) ** 2
def ramp(t, a, b): return min(1, max(0, (t - a) / (b - a)))


# ---------- text ----------
@functools.lru_cache(maxsize=4096)
def text_mask(s, fp, size, w, track):
    f = font(fp, size, w); l, t, r, b = f.getbbox(s)
    wid = int(sum(f.getlength(c) for c in s) + track * (len(s) - 1)) + 8 if track else r - l + 8
    m = Image.new("L", (int(wid), b - t + 8), 0); d = ImageDraw.Draw(m)
    if track:
        x = 4
        for c in s: d.text((x, 4 - t), c, font=f, fill=255); x += f.getlength(c) + track
    else: d.text((4 - l, 4 - t), s, font=f, fill=255)
    return m

def tint(mask, kind, alpha=1.0, sweep=None):
    w, h = mask.size
    if kind == "gold":
        y = np.linspace(0, 1, h)[:, None, None]
        rgb = np.repeat(orn.FLAT_TOP * (1 - y) + orn.FLAT_BOT * y, w, 1)
        if sweep is not None:
            xx = np.arange(w)[None, :] + np.arange(h)[:, None] * 0.6
            rgb = rgb + np.exp(-((xx - sweep * (w + h)) / 60.0) ** 2)[..., None] * 60
    else: rgb = np.full((h, w, 3), 255 if kind == "white" else 200, np.float32)
    a = np.asarray(mask, np.float32) * alpha
    return Image.fromarray(np.dstack([np.clip(rgb, 0, 255), a]).astype(np.uint8), "RGBA")

def place(cv, im, x, y, anchor="mm", shadow=0.75, glow=None):
    if anchor == "mm": x, y = x - im.width / 2, y - im.height / 2
    elif anchor == "lm": y = y - im.height / 2
    elif anchor == "rm": x, y = x - im.width, y - im.height / 2
    x, y = int(x), int(y)
    if shadow:
        a = im.getchannel("A").filter(ImageFilter.GaussianBlur(8)).point(lambda v: int(v * shadow))
        sh = Image.new("RGBA", im.size, (0, 0, 0, 0)); sh.putalpha(a); cv.alpha_composite(sh, (x + 3, y + 5))
    if glow:
        pad = 30; ga = Image.new("L", (im.width + 2 * pad, im.height + 2 * pad), 0); ga.paste(im.getchannel("A"), (pad, pad))
        ga = ga.filter(ImageFilter.GaussianBlur(16)).point(lambda v: int(v * glow))
        g = Image.new("RGBA", ga.size, (255, 190, 90, 0)); g.putalpha(ga); cv.alpha_composite(g, (x - pad, y - pad))
    cv.alpha_composite(im, (x, y))

def txt(cv, s, fp, size, kind, x, y, w=None, track=0, anchor="mm", alpha=1.0, shadow=0.75, glow=None, sweep=None):
    m = text_mask(s, fp, int(size), w, int(track))
    place(cv, tint(m, kind, alpha, sweep), x, y, anchor, shadow * alpha, glow)

def reveal(cv, s, fp, size, kind, x, y, t, t0, dur=0.7, w=None, track=0, track_from=None, sweep_t=None, shadow=0.75, glow=None):
    p = ease(ramp(t, t0, t0 + dur))
    if p <= 0: return
    tr = track if track_from is None else track_from + (track - track_from) * p
    sw = None
    if sweep_t is not None:
        sw = ramp(t, sweep_t, sweep_t + 0.9) * 1.3 - 0.15
        if sw <= -0.1 or sw >= 1.1: sw = None
    txt(cv, s, fp, size, kind, x, y + (1 - p) * 18, w, round(tr), "mm", p, shadow, glow, sw)

def fit(s, fp, size, w, track, maxw):
    size = int(size)
    while size > 18 and text_mask(s, fp, size, w, int(track)).size[0] > maxw: size -= 4
    return size

def split2(s):
    w = s.split()
    if len(w) < 2: return [s]
    k = min(range(1, len(w)), key=lambda k: abs(len(" ".join(w[:k])) - len(" ".join(w[k:]))))
    return [" ".join(w[:k]), " ".join(w[k:])]


# ---------- embers ----------
def _init_embers():
    rng = np.random.default_rng(7); N = 170; W, H = S["W"], S["H"]
    S["P"] = dict(x=rng.uniform(-0.2, 1.0, N) * W, y=rng.uniform(0, 1.3, N) * H, vx=rng.uniform(120, 420, N), vy=-rng.uniform(140, 420, N),
                  z=rng.uniform(0.3, 1.6, N), ph=rng.uniform(0, 6.28, N))

def embers(t):
    W, H, P = S["W"], S["H"], S["P"]
    sharp = Image.new("RGB", (W, H)); d = ImageDraw.Draw(sharp); bok = Image.new("RGB", (W, H)); db = ImageDraw.Draw(bok)
    for i in range(len(P["x"])):
        z = P["z"][i]
        x = (P["x"][i] + P["vx"][i] * z * t + 30 * math.sin(t * 2 + P["ph"][i])) % (W * 1.2) - W * 0.1
        y = (P["y"][i] + P["vy"][i] * z * t) % (H * 1.3) - H * 0.15
        fl = 0.6 + 0.4 * math.sin(t * 9 + P["ph"][i] * 3)
        col = tuple(int(c * fl) for c in ((255, 214, 150) if i % 3 else (255, 150, 60)))
        if z > 1.3: r = 6 * z; db.ellipse([x - r, y - r, x + r, y + r], fill=tuple(int(c * 0.35) for c in col))
        else:
            k = 0.045 * z; d.line([(x, y), (x - P["vx"][i] * z * k, y - P["vy"][i] * z * k)], fill=col, width=max(1, int(2.2 * z)))
    return (np.asarray(sharp, np.float32) + np.asarray(sharp.filter(ImageFilter.GaussianBlur(5)), np.float32) * 1.6
            + np.asarray(bok.filter(ImageFilter.GaussianBlur(10)), np.float32))


# ---------- ornaments ----------
def stars_for(r10):
    v = round(float(r10)) / 2          # 10-point score -> 5 stars in half steps
    return int(v), (v - int(v)) >= 0.5

def rule(cv, x, y, half, p=1.0):
    if p <= 0: return
    d = ImageDraw.Draw(cv)
    d.line([(x - half * p, y), (x + half * p, y)], fill=GOLD + (int(170 * p),), width=2)
    d.polygon([(x, y - 5), (x + 5, y), (x, y + 5), (x - 5, y)], fill=GOLD + (int(255 * p),))

def side(cv, x, y, top, lines, t, t0, r=26, sz=(34, 20)):
    if top[0] == "stars":
        nf, half = stars_for(top[1]); pops = [back(ramp(t, t0 + i * 0.13, t0 + i * 0.13 + 0.35)) for i in range(5)]
        place(cv, orn.stars(nf, half, r, pops), x, y, "mm", 0.55)
    else:
        size = fit(top[1], CINZEL, top[2], 800, 4, 300 if S["fmt"] == "h" else 400)
        reveal(cv, top[1], CINZEL, size, "gold", x, y, t, t0, 0.6, w=800, track=4)
    rule(cv, x, y + 50, 150 if S["fmt"] == "h" else 130, ease(ramp(t, t0 + 0.4, t0 + 1.1)))
    for i, (s, kind) in enumerate(lines):
        size = sz[0] if i == 0 else sz[1]
        size = fit(s, MONT, size, 800, 4, 340 if S["fmt"] == "h" else 420)
        reveal(cv, s, MONT, size, kind, x, y + 92 + i * (sz[0] + 14), t, t0 + 0.55 + i * 0.12, 0.6, w=800 if i == 0 else 600, track=4, shadow=0.8)

def laurel_block(cv, x, y, R, t, t0, inner):
    place(cv, orn.laurel(R, int(round(ease(ramp(t, t0, t0 + 1.0)) * 100))), x, y, "mm", 0.6)
    for i, (s, fp, size, w, kind) in enumerate(inner):
        reveal(cv, s, fp, size, kind, x, y + (i - (len(inner) - 1) / 2) * size * 1.2, t, t0 + 0.5 + i * 0.12, 0.6, w=w, track=3, shadow=0.85)


# ---------- cards ----------
def hook_card(cv, t):
    h, W, H, brand = S["ep"]["hook"], S["W"], S["H"], S["brand"]; n = len(S["ep"]["films"])
    lau = [(brand["name"], MONT, 30, 800, "white"), ("OFFICIAL", MONT, 26, 700, "white"), ("SELECTION", MONT, 26, 700, "white"), (str(brand["year"]), MONT, 24, 600, "gold")]
    head = h["headline"].upper()
    if S["fmt"] == "h":
        reveal(cv, h["overline"], LIME, fit(h["overline"], LIME, 74, None, 6, 1600), "gold", W / 2, 215, t, 0.2, 0.9, track=6, track_from=30, sweep_t=1.3, glow=0.5)
        size = fit(head, CINZEL, 250, 900, 20, 1760)
        reveal(cv, head, CINZEL, size, "white", W / 2, 400, t, 0.55, 0.8, w=900, track=20, track_from=70, shadow=0.9)
        laurel_block(cv, W / 2, 760, 150, t, 1.2, lau)
        side(cv, 470, 700, ("stars", 10), [(h["left"][0], "white"), (h["left"][1], "gold")], t, 1.5, sz=(30, 22))
        side(cv, 1450, 700, ("stars", 10), [(h["right"][0], "white"), (h["right"][1], "gold")], t, 1.65, sz=(30, 22))
        reveal(cv, f"No. {n}  to  No. 1  ·  Watch Till The End", LIME, 40, "gold", W / 2, 1000, t, 2.4, 0.8, track=3, shadow=0.8)
    else:
        ol = split2(h["overline"]) if len(h["overline"]) > 14 else [h["overline"]]
        for i, l in enumerate(ol):
            reveal(cv, l, LIME, fit(l, LIME, 92, None, 6, 960), "gold", W / 2, 470 + i * 110 - (len(ol) - 1) * 0, t, 0.2 + i * 0.12, 0.9, track=6, track_from=30, sweep_t=1.3 + i * 0.1, glow=0.5)
        hl = split2(head) if len(head) > 7 else [head]
        size = min(fit(l, CINZEL, 230, 900, 18, 980) for l in hl)
        y0 = 470 + (len(ol) - 1) * 110 + 180
        for i, l in enumerate(hl):
            reveal(cv, l, CINZEL, size, "white", W / 2, y0 + i * size * 1.0, t, 0.55 + i * 0.13, 0.8, w=900, track=18, track_from=60, shadow=0.9)
        ly = max(1300, y0 + len(hl) * size + 120)
        laurel_block(cv, W / 2, ly, 140, t, 1.2, [(a, b, c - 2, d, e) for a, b, c, d, e in lau])
        side(cv, 290, ly + 240, ("stars", 10), [(h["left"][0], "white"), (h["left"][1], "gold")], t, 1.5, r=20, sz=(28, 20))
        side(cv, 790, ly + 240, ("stars", 10), [(h["right"][0], "white"), (h["right"][1], "gold")], t, 1.65, r=20, sz=(28, 20))

def film_card(cv, f, m, t):
    W, H = S["W"], S["H"]
    pick = f"{S['brand']['name']} TOP PICK" if f["rank"] == 1 else f"{S['brand']['name']} PICK"
    title = m["title"].upper(); sub = (f"Directed by {m['director']}  ·  {m['year']}" if m.get("director") else str(m.get("year", "")))
    left = (("stars", m["imdb"]), [(f"{m['rating_label']} {m['imdb']}", "white"), ("AUDIENCE SCORE", "gold")])
    right = (("text", m["fact"][0], 40), [(m["fact"][1], "white")])
    if S["fmt"] == "h":
        laurel_block(cv, W / 2, 330, 175, t, 0.2, [(f"Nº {f['rank']}", CINZEL, 110, 800, "gold"), (pick, MONT, 20, 700, "white")])
        reveal(cv, title, CINZEL, fit(title, CINZEL, 118, 800, 10, 1700), "white", W / 2, 640, t, 0.7, 0.8, w=800, track=10, track_from=40, shadow=0.9)
        reveal(cv, sub, LIME, fit(sub, LIME, 40, None, 3, 1500), "gold", W / 2, 745, t, 0.95, 0.8, track=3, sweep_t=1.6, glow=0.4)
        side(cv, 400, 330, left[0], left[1], t, 1.1)
        side(cv, 1520, 330, right[0], right[1], t, 1.25, sz=(26, 20))
    else:
        laurel_block(cv, W / 2, 520, 160, t, 0.2, [(f"Nº {f['rank']}", CINZEL, 100, 800, "gold"), (pick, MONT, 18, 700, "white")])
        lines = split2(title) if len(title) > 11 else [title]
        size = min(fit(l, CINZEL, 120, 800, 8, 980) for l in lines)
        for i, l in enumerate(lines):
            reveal(cv, l, CINZEL, size, "white", W / 2, 830 + i * size * 1.15, t, 0.7 + i * 0.1, 0.8, w=800, track=8, track_from=36, shadow=0.9)
        sy = 830 + (len(lines) - 1) * size * 1.15 + size * 0.95
        reveal(cv, sub, LIME, fit(sub, LIME, 38, None, 2, 980), "gold", W / 2, sy, t, 0.95, 0.8, track=2, sweep_t=1.7, glow=0.4)
        side(cv, 290, sy + 190, left[0], left[1], t, 1.1, r=20, sz=(30, 20))
        side(cv, 790, sy + 190, right[0], right[1], t, 1.25, r=20, sz=(22, 20))

def outro_card(cv, t):
    W, H = S["W"], S["H"]; q = S["ep"]["outro"]["question"]; big = q[1].upper()
    if t < 2.7:
        if S["fmt"] == "h":
            reveal(cv, q[0], LIME, 84, "gold", W / 2, 400, t, 0.15, 0.8, track=6, track_from=26, sweep_t=1.0, glow=0.5)
            reveal(cv, big, CINZEL, fit(big, CINZEL, 150, 900, 12, 1760), "white", W / 2, 560, t, 0.4, 0.8, w=900, track=12, track_from=50, shadow=0.9)
        else:
            reveal(cv, q[0], LIME, 96, "gold", W / 2, 700, t, 0.15, 0.8, track=6, track_from=26, sweep_t=1.0, glow=0.5)
            ls = split2(big) if len(big) > 9 else [big]; size = min(fit(l, CINZEL, 170, 900, 12, 980) for l in ls)
            for i, l in enumerate(ls):
                reveal(cv, l, CINZEL, size, "white", W / 2, 880 + i * size * 1.15, t, 0.4 + i * 0.1, 0.8, w=900, track=12, track_from=50, shadow=0.9)
    else:
        u = t - 2.7; cy = H / 2 - (40 if S["fmt"] == "h" else 60)
        place(cv, orn.laurel(130 if S["fmt"] == "h" else 120, int(round(ease(ramp(u, 0, 0.9)) * 100))), W / 2, cy - (230 if S["fmt"] == "h" else 260), "mm", 0.6)
        name = S["brand"]["name"]
        reveal(cv, name, CINZEL, fit(name, CINZEL, 150 if S["fmt"] == "h" else 130, 900, 16, W - 120), "white", W / 2, cy, u, 0.15, 0.8, w=900, track=16, track_from=50, shadow=0.9)
        reveal(cv, "Follow for more rankings", LIME, 46, "gold", W / 2, cy + 120, u, 0.45, 0.8, track=3, sweep_t=0.9, glow=0.4)


# ---------- montage ----------
def _chunks(words):
    out, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) == 3 or re.search(r"[.,?!;:]$", w[0]): out.append(cur); cur = []
    if cur: out.append(cur)
    res = []
    for i, c in enumerate(out):
        end = out[i + 1][0][1] if i + 1 < len(out) else c[-1][2] + 0.35
        res.append((c[0][1], min(end, c[-1][2] + 0.4), c))
    return res

def draw_caption(cv, t):
    L = S["lay"]
    for a, b, kind, c in S["caps"]:
        if not (a <= t < b): continue
        p = ease((t - a) / 0.12) * min(1, (b - t) / 0.12 + 0.2)
        if kind == "quote":
            q = "\u201c" + c.strip().strip('"').strip() + "\u201d"
            size = fit(q, CINZEL, L["cap_size"] + 6, 700, 1, L["cap_max"])
            txt(cv, q, CINZEL, size, "white", S["W"] / 2, L["cap_y"] + (1 - p) * 8, w=700, track=1, alpha=p, shadow=0.85)
            return
        words = [re.sub(r'^["\'\u201c\u201d]+|["\'\u201c\u201d]+$', "", w) for w, _, _ in c]
        size = L["cap_size"]; f = font(MONT, size, 700); sp = f.getlength(" ")
        tot = sum(f.getlength(w) for w in words) + sp * (len(words) - 1)
        if tot > L["cap_max"]:
            size = int(size * L["cap_max"] / tot); f = font(MONT, size, 700); sp = f.getlength(" ")
            tot = sum(f.getlength(w) for w in words) + sp * (len(words) - 1)
        x = S["W"] / 2 - tot / 2
        for (w, ws, we), ww in zip(c, words):
            m = text_mask(ww, MONT, size, 700, 0)
            place(cv, tint(m, "gold" if ws <= t < we + 0.02 else "white", p), x - 4, L["cap_y"] - m.size[1] / 2 + (1 - p) * 8, "tl", 0.85)
            x += f.getlength(ww) + sp
        return

def ladder(cv, cur, x, y, size):
    n = len(S["ep"]["films"]); order = list(range(n, 0, -1)); gap = size * 1.7; x0 = x - gap * (n - 1) / 2
    for i, r in enumerate(order):
        txt(cv, ROMAN[r - 1], CINZEL, size, "gold" if r == cur else "dim", x0 + i * gap, y, w=700, alpha=1.0 if r == cur else 0.45, shadow=0)

def montage(base, f, m, t, mt):
    W, H, L = S["W"], S["H"], S["lay"]
    if f is None:                                   # cold open: picture + quote only
        if S["fmt"] == "h": cv = Image.fromarray(base).convert("RGBA")
        else:
            a = S["vbg"].copy(); y0, y1 = L["band"]; a[y0:y1] = base[y0:y1]
            cv = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).convert("RGBA")
    elif S["fmt"] == "h":
        cv = Image.fromarray(base).convert("RGBA"); y = L["band"][0] / 2
        txt(cv, f"Nº {f['rank']}", CINZEL, 32, "gold", 120, y, w=800, anchor="lm", shadow=0)
        nw = text_mask(f"Nº {f['rank']}", CINZEL, 32, 800, 0).size[0]
        txt(cv, m["title"].upper(), CINZEL, 32, "white", 120 + nw + 18, y, w=700, track=4, anchor="lm", shadow=0, alpha=0.92)
        ladder(cv, f["rank"], W - 120 - 26 * 1.7 * (len(S["ep"]["films"]) - 1) / 2, y, 26)
    else:
        a = S["vbg"] + S["rng"].normal(0, 3.5, (H, W))[..., None]
        y0, y1 = L["band"]; a[y0:y1] = base[y0:y1]
        cv = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).convert("RGBA")
        ladder(cv, f["rank"], W / 2, 215, 30)
        place(cv, orn.laurel(74, 100), W / 2, 370, "mm", 0.5)
        txt(cv, f"Nº {f['rank']}", CINZEL, 54, "gold", W / 2, 378, w=800, shadow=0.6)
        title = m["title"].upper()
        txt(cv, title, CINZEL, fit(title, CINZEL, 68, 800, 4, 960), "white", W / 2, 565, w=800, track=4)
        info = "  ·  ".join(x for x in [m.get("director", ""), str(m.get("year", "")), f"{m['rating_label']} {m['imdb']}"] if x)
        txt(cv, info, LIME, fit(info, LIME, 32, None, 2, 980), "gold", W / 2, 625, track=2)
        rule(cv, W / 2, y0 - 22, 160); rule(cv, W / 2, y1 + 22, 160)
    draw_caption(cv, t)
    if mt < 0.22: cv.alpha_composite(Image.new("RGBA", (W, H), (0, 0, 0, int(255 * (1 - mt / 0.22)))))
    return cv


# ---------- frame ----------
def frame(base, t):
    tl = S["tl"]; W, H = S["W"], S["H"]
    s = next((x for x in tl["sections"] if x["start"] <= t < x["start"] + x["dur"]), tl["sections"][-1])
    lt = t - s["start"]
    if s["kind"] == "cold":
        cv = montage(base, None, None, t, lt)
        k = min(1, (s["dur"] - lt) / 0.2)
        if k < 1: cv.alpha_composite(Image.new("RGBA", (W, H), (0, 0, 0, int(255 * (1 - max(0, k))))))
        return cv.convert("RGB")
    if s["kind"] == "film" and lt >= s["card"]:
        f = S["films"][s["key"]]; return montage(base, f, S["metas"][s["key"]], t, lt - s["card"]).convert("RGB")
    a = base.astype(np.float32) * 0.34 + embers(t) * 0.9 + S["rng"].normal(0, 4, (H, W))[..., None]
    cv = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).convert("RGBA")
    if s["kind"] == "hook": hook_card(cv, lt)
    elif s["kind"] == "outro": outro_card(cv, lt)
    else: film_card(cv, S["films"][s["key"]], S["metas"][s["key"]], lt)
    fin = 1 if (s["kind"] == "hook" and s["start"] == 0) else min(1, lt / 0.2)
    end = s["card"] if s["kind"] == "film" else s["dur"]
    k = min(fin, (end - lt) / 0.25, 1)
    if k < 1: cv.alpha_composite(Image.new("RGBA", (W, H), (0, 0, 0, int(255 * (1 - max(0, k))))))
    return cv.convert("RGB")


def setup(fmt, tl, ep, metas, vo, brand):
    L = LAY[fmt]; S.clear()
    S.update(fmt=fmt, lay=L, W=L["W"], H=L["H"], tl=tl, ep=ep, metas=metas, brand=brand, rng=np.random.default_rng(11),
             films={f["key"]: f for f in ep["films"]})
    _init_embers()
    y, x = np.mgrid[0:S["H"], 0:S["W"]]
    b = 14 + 10 * np.exp(-(((x - S["W"] / 2) / 700) ** 2 + ((y - S["H"] * 0.42) / 900) ** 2))
    S["vbg"] = np.dstack([b * 1.08, b * 0.98, b * 0.88]).astype(np.float32)
    caps = []
    for s in tl["sections"]:
        lo = s["start"] + (s["card"] - 0.15 if s["kind"] == "film" else 0)
        for b in s["blocks"]:
            off = s["start"] + b["at"]
            if b["type"] == "orig":
                if s["kind"] in ("film", "cold"): caps.append((off + 0.1, off + b["dur"] - 0.05, "quote", b["quote"]))
                continue
            if s["kind"] != "film": continue
            caps += [(a + off, bb + off, "words", [(w, ws + off, we + off) for w, ws, we in c])
                     for a, bb, c in _chunks(vo[b["key"]]["words"]) if a + off >= lo]
    S["caps"] = sorted(caps, key=lambda c: -c[0])     # latest-starting caption wins on overlap


def render(fmt, tl, ep, metas, vo, brand, base_path, out_path, only=None):
    setup(fmt, tl, ep, metas, vo, brand); W, H = S["W"], S["H"]
    if only:
        outs = []
        for t in only:
            raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(base_path), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                                 capture_output=True, check=True).stdout
            p = f"{out_path}_{t:.1f}.jpg"; frame(np.frombuffer(raw, np.uint8).reshape(H, W, 3), t).save(p, quality=90); outs.append(p)
        return outs
    tmp = str(out_path) + ".tmp.mp4"
    dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(base_path), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                            "-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-pix_fmt", "yuv420p", tmp], stdin=subprocess.PIPE)
    n = W * H * 3; i = 0; total = tl["frames"]
    while True:
        b = dec.stdout.read(n)
        if len(b) < n: break
        enc.stdin.write(frame(np.frombuffer(b, np.uint8).reshape(H, W, 3), i / FPS).tobytes()); i += 1
        if i % 150 == 0: print(f"  [{fmt}] {i}/{total} frames", flush=True)
    enc.stdin.close(); enc.wait(); os.replace(tmp, out_path)
    return out_path
