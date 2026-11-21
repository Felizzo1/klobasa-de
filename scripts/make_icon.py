#!/usr/bin/env python3
"""Icons des Bratwurst-Planers: senfgelbe Kartennadel mit dunkler Kontur und weißem Ball.

  .venv/bin/python scripts/make_icon.py [site] [--pruefbild DATEI]

Das Motiv hängt nicht vom Farbschema der Seite ab (Rasengrün, Senf und Wurst, Nachtblau und Gold): Gelb mit dunkler
Kontur hebt sich auf hellem und dunklem Grund ab. Deckende Icons (App, maskable, Apple) liegen auf neutralem Anthrazit.
  favicon-32.png, logo.png                     transparent (Browser-Tab, Logo im Seitenkopf)
  icon-192.png, icon-512.png                   „any“: abgerundete Kachel auf Anthrazit, Ecken transparent
  icon-maskable-192.png, icon-maskable-512.png vollflächig Anthrazit, Motiv in der sicheren Zone (Kreis mit 80 % Durchmesser)
  apple-touch-icon.png                         180 px, vollflächig
--pruefbild: Übersicht auf hellem/dunklem Grund, auf den Kopffarben und mit Android-Masken (Kreis, Squircle, Tropfen)
"""
import math, os, sys
from PIL import Image, ImageDraw, ImageFilter
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

MUSTARD = (245, 184, 0, 255)     # Senfgelb
INK = (26, 26, 24, 255)          # Kontur, Ballmuster
WHITE = (255, 255, 255, 255)
TILE = (36, 37, 34, 255)         # Anthrazit, neutral zu Grün, Braun und Nachtblau
SS = 4                            # Überabtastung

def pin_shapes(S, box):
    """Nadel in einem Quadrat box=(x0, y0, h) mit Höhe h; Rückgabe: Umriss, Kopf-Mittelpunkt, Kopfradius."""
    x0, y0, h = box
    r = 0.33 * h; cx = x0 + h / 2; cy = y0 + 0.35 * h; tip = (cx, y0 + 0.985 * h)
    d = tip[1] - cy; a = math.asin(r / d)  # Tangentenpunkte vom Spitzenpunkt an den Kreis
    t1 = (cx - r * math.cos(a), cy + r * math.sin(a)); t2 = (cx + r * math.cos(a), cy + r * math.sin(a))
    shape = unary_union([Point(cx, cy).buffer(r, 128), Polygon([t1, tip, t2])])
    return shape, (cx, cy), r

def poly(draw, geom, fill):
    for g in getattr(geom, 'geoms', [geom]):
        if g.is_empty: continue
        draw.polygon([(x, y) for x, y in g.exterior.coords], fill=fill)

def ball(draw, c, R):
    """Weißer Ball mit dunklem Fünfeck in der Mitte, Nähten und angeschnittenen Randfünfecken."""
    cx, cy = c
    disc = Point(cx, cy).buffer(R, 128)
    poly(draw, disc, WHITE)
    pent = lambda r, rot, ox=cx, oy=cy: Polygon([(ox + r * math.cos(rot + k * 2 * math.pi / 5), oy + r * math.sin(rot + k * 2 * math.pi / 5)) for k in range(5)])
    up = -math.pi / 2
    poly(draw, pent(0.36 * R, up), INK)
    for k in range(5):
        ang = up + k * 2 * math.pi / 5
        ex, ey = cx + 1.02 * R * math.cos(ang), cy + 1.02 * R * math.sin(ang)
        poly(draw, pent(0.30 * R, ang + math.pi, ex, ey).intersection(disc), INK)
        # Naht von der Mittelfünfeck-Ecke zum Randfünfeck
        sx, sy = cx + 0.36 * R * math.cos(ang), cy + 0.36 * R * math.sin(ang)
        mx, my = cx + 0.74 * R * math.cos(ang), cy + 0.74 * R * math.sin(ang)
        draw.line([(sx, sy), (mx, my)], fill=INK, width=max(1, round(0.075 * R)))
    # Kontur des Balls
    draw.ellipse([cx - R, cy - R, cx + R, cy + R], outline=INK, width=max(1, round(0.07 * R)))

def motif(size, h_frac, outline=0.075):
    """Transparentes Quadrat size×size, Nadel mit Höhe h_frac·size, mittig; Konturbreite relativ zur Nadelhöhe."""
    S = size * SS
    im = Image.new('RGBA', (S, S), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    h = h_frac * S; box = ((S - h) / 2, (S - h) / 2, h)
    shape, c, r = pin_shapes(S, box)
    w = outline * h
    poly(d, shape.buffer(w / 2, 64), INK)        # Kontur außen
    poly(d, shape.buffer(-w / 2, 64), MUSTARD)   # Füllung
    ball(d, c, r * 0.64)
    return im.resize((size, size), Image.LANCZOS)

def tile(size, h_frac, rounded=False):
    S = size * SS
    bg = Image.new('RGBA', (S, S), (0, 0, 0, 0)); d = ImageDraw.Draw(bg)
    if rounded: d.rounded_rectangle([0, 0, S - 1, S - 1], radius=round(0.22 * S), fill=TILE)
    else: d.rectangle([0, 0, S, S], fill=TILE)
    bg = bg.resize((size, size), Image.LANCZOS)
    bg.alpha_composite(motif(size, h_frac, outline=0.05))
    return bg

def main():
    out = next((x for x in sys.argv[1:] if not x.startswith('--')), 'site')
    motif(32, 0.98, outline=0.10).save(f'{out}/favicon-32.png', optimize=True)
    logo = motif(144, 1.0, outline=0.08); logo = logo.crop(logo.getbbox()); logo.save(f'{out}/logo.png', optimize=True)
    for s in (512, 192):
        tile(s, 0.72, rounded=True).save(f'{out}/icon-{s}.png', optimize=True)
        tile(s, 0.68).convert('RGB').save(f'{out}/icon-maskable-{s}.png', optimize=True)  # 68 % Höhe: bleibt im 80-%-Kreis
    tile(180, 0.70).convert('RGB').save(f'{out}/apple-touch-icon.png', optimize=True)
    if '--pruefbild' in sys.argv:
        pruefbild(out, sys.argv[sys.argv.index('--pruefbild') + 1])
    print('Icons geschrieben nach', out)

def pruefbild(out, path):
    """Übersicht: Favicon/Logo auf hell, dunkel und den drei Kopffarben; maskable unter Android-Masken."""
    from PIL import ImageFont
    try: font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 18)
    except Exception: font = ImageFont.load_default()
    W, H = 1240, 760
    im = Image.new('RGBA', (W, H), (255, 255, 255, 255)); d = ImageDraw.Draw(im)
    grounds = [('hell', (255, 255, 255)), ('dunkel', (18, 18, 18)), ('Rasengrün', (23, 67, 44)), ('Senf/Wurst', (74, 40, 24)), ('Nachtblau', (20, 39, 74))]
    fav = Image.open(f'{out}/favicon-32.png'); logo = Image.open(f'{out}/logo.png')
    logo = logo.resize((round(logo.width * 72 / logo.height), 72), Image.LANCZOS)
    x = 20
    for name, col in grounds:
        d.rectangle([x, 40, x + 220, 300], fill=col)
        im.alpha_composite(fav, (x + 30, 70)); im.alpha_composite(fav.resize((16, 16), Image.LANCZOS), (x + 80, 78))
        im.alpha_composite(logo, (x + 130, 50))
        im.alpha_composite(Image.open(f'{out}/icon-192.png').resize((96, 96), Image.LANCZOS), (x + 30, 170))
        d.text((x + 140, 210), name, fill=(255, 255, 255) if sum(col) < 380 else (0, 0, 0), font=font)
        x += 240
    d.text((20, 10), 'Favicon 32 + 16 px, Logo (Kopf), App-Icon „any“ auf hellem, dunklem Grund und den Kopffarben', fill=(0, 0, 0), font=font)
    d.text((20, 320), 'maskable unter Android-Masken (Kreis, Squircle, Tropfen, abgerundet) auf hellem und dunklem Startbildschirm, 108 px', fill=(0, 0, 0), font=font)
    mk = Image.open(f'{out}/icon-maskable-512.png').convert('RGBA').resize((432, 432), Image.LANCZOS)
    def mask(kind, s=432):
        m = Image.new('L', (s, s), 0); md = ImageDraw.Draw(m)
        if kind == 'kreis': md.ellipse([0, 0, s - 1, s - 1], fill=255)
        elif kind == 'abgerundet': md.rounded_rectangle([0, 0, s - 1, s - 1], radius=s // 5, fill=255)
        elif kind == 'tropfen': md.rounded_rectangle([0, 0, s - 1, s - 1], radius=s // 2, fill=255); md.rectangle([s // 2, s // 2, s - 1, s - 1], fill=255)
        else:  # Squircle
            pts = [(s / 2 + (s / 2) * abs(math.cos(t)) ** 0.5 * (1 if math.cos(t) >= 0 else -1), s / 2 + (s / 2) * abs(math.sin(t)) ** 0.5 * (1 if math.sin(t) >= 0 else -1)) for t in [i * 2 * math.pi / 400 for i in range(400)]]
            md.polygon(pts, fill=255)
        return m
    for row, (bgname, bg) in enumerate([('hell', (232, 236, 240)), ('dunkel', (24, 26, 30))]):
        y = 360 + row * 190
        d.rectangle([20, y, W - 20, y + 170], fill=bg)
        for i, kind in enumerate(['kreis', 'squircle', 'tropfen', 'abgerundet']):
            ic = Image.new('RGBA', (432, 432), (0, 0, 0, 0)); ic.paste(mk, (0, 0), mask(kind))
            ic = ic.resize((108, 108), Image.LANCZOS)
            im.alpha_composite(ic, (60 + i * 150, y + 30))
        # Sicherheitszone zur Kontrolle: 80-%-Kreis auf dem ungeschnittenen Icon
        raw = mk.resize((108, 108), Image.LANCZOS).copy(); rd = ImageDraw.Draw(raw)
        rd.ellipse([54 - 43, 54 - 43, 54 + 43, 54 + 43], outline=(255, 0, 80, 255), width=2)
        im.alpha_composite(raw, (700, y + 30))
        d.text((830, y + 70), 'ungeschnitten, rot = sichere Zone', fill=(255, 255, 255) if row else (0, 0, 0), font=font)
    im.convert('RGB').save(path)

if __name__ == '__main__':
    main()
