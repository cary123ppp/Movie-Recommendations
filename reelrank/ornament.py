"""Flat gold laurel wreath and rating stars (supersampled vector shapes)."""
import math, functools
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

SS = 4  # supersampling

FLAT_TOP = np.array([246, 206, 120], np.float32); FLAT_BOT = np.array([214, 156, 62], np.float32)
def flat(mask_hi):
    w, h = mask_hi.size[0] // SS, mask_hi.size[1] // SS
    m = np.asarray(mask_hi.resize((w, h), Image.LANCZOS), np.float32)
    y = np.linspace(0, 1, h)[:, None, None]
    col = np.repeat(FLAT_TOP * (1 - y) + FLAT_BOT * y, w, 1)
    return Image.fromarray(np.dstack([col, m]).astype(np.uint8), "RGBA")

# ---------- laurel ----------
def _leaf(cx, cy, ang, L, Wd, bend):
    pts_top, pts_bot, n = [], [], 18
    for i in range(n + 1):
        s = i / n
        wv = Wd * math.sin(math.pi * s ** 0.75) * (1 - 0.15 * s)
        off = bend * L * s * s
        pts_top.append((s * L, off + wv)); pts_bot.append((s * L, off - wv * 0.9))
    poly = pts_top + pts_bot[::-1]
    ca, sa = math.cos(ang), math.sin(ang)
    tr = lambda p: (cx + p[0] * ca - p[1] * sa, cy + p[0] * sa + p[1] * ca)
    vein = [tr((s * L, bend * L * s * s)) for s in np.linspace(0.08, 0.82, 10)]
    return [tr(p) for p in poly], vein

@functools.lru_cache(maxsize=128)
def laurel(R, prog_q, n=9):
    prog = prog_q / 100
    size = int(R * 2.7) * SS; c = size / 2; Rs = R * SS
    m = Image.new("L", (size, size), 0); d = ImageDraw.Draw(m)
    a0, a1 = math.radians(116), math.radians(238)
    gap = int(SS * 2.2)
    for side in (1, -1):
        X = lambda x: c + side * (x - c)
        steps = 60; k = int(steps * min(1, prog))
        for i in range(k):
            aa = a0 + (a1 - a0) * i / steps; ab = a0 + (a1 - a0) * (i + 1) / steps
            d.line([(X(c + Rs * math.cos(aa)), c + Rs * math.sin(aa)), (X(c + Rs * math.cos(ab)), c + Rs * math.sin(ab))], fill=255, width=max(SS, int(Rs * 0.016)))
        leaves = []
        for i in range(n):
            gr = min(1, max(0, prog * n - i))
            if gr <= 0: continue
            t = i / (n - 1); a = a0 + (a1 - a0) * t
            tx, ty = -math.sin(a) * side, math.cos(a); base = math.atan2(ty, tx)
            L = Rs * (0.27 + 0.07 * math.sin(math.pi * t)) * (1 - 0.22 * t) * gr
            for off, scale, dt in ((0.62, 1.0, 0.0), (-0.50, 0.86, 0.05)):
                aa = a + dt; qx, qy = X(c + Rs * math.cos(aa)), c + Rs * math.sin(aa)
                o = off if side > 0 else -off
                poly, _ = _leaf(qx, qy, base + o, L * scale, L * scale * 0.26, 0.0)
                leaves.append(poly)
        for poly in leaves:            # later leaves sit on top, with a hairline gap
            d.polygon(poly, fill=255, outline=0, width=gap)
            d.polygon(poly, fill=255)
            d.line(poly + [poly[0]], fill=0, width=gap)
            d.polygon(poly, fill=255)
        if prog >= 0.999:
            a = a1 + 0.05; px, py = X(c + Rs * math.cos(a)), c + Rs * math.sin(a)
            base = math.atan2(math.cos(a), -math.sin(a) * side)
            poly, _ = _leaf(px, py, base, Rs * 0.2, Rs * 0.05, 0)
            d.polygon(poly, fill=255)
    return flat(m)

def _star_pts(cx, cy, r):
    pts = []
    for k in range(10):
        a = -math.pi / 2 + k * math.pi / 5; q = r if k % 2 == 0 else r * 0.42
        pts.append((cx + q * math.cos(a), cy + q * math.sin(a)))
    return pts

def stars(n_full, half, r, pops):
    W = int(r * 2.5 * 5) * SS; H = int(r * 2.4) * SS
    fill = Image.new("L", (W, H), 0); df = ImageDraw.Draw(fill)
    ghost = Image.new("L", (W, H), 0); dg = ImageDraw.Draw(ghost)
    for i in range(5):
        s = pops[i]
        if s <= 0.01: continue
        cx = (r * 1.25 + i * r * 2.5) * SS; cy = r * 1.2 * SS; pts = _star_pts(cx, cy, r * s * SS)
        if i < n_full: df.polygon(pts, fill=255)
        else:
            dg.polygon(pts, fill=255)
            if i == n_full and half:
                lay = Image.new("L", (W, H), 0); ImageDraw.Draw(lay).polygon(pts, fill=255)
                cut = Image.new("L", (W, H), 0); ImageDraw.Draw(cut).rectangle([0, 0, cx, H], fill=255)
                fill.paste(255, mask=Image.fromarray(np.minimum(np.asarray(lay), np.asarray(cut))))
    out = flat(fill)
    gh = np.asarray(ghost.resize((W // SS, H // SS), Image.LANCZOS), np.float32) * 0.28
    g = Image.fromarray(np.dstack([np.full(gh.shape + (3,), 230, np.float32) * [1, .85, .6], gh]).astype(np.uint8), "RGBA")
    g.alpha_composite(out)
    return g
