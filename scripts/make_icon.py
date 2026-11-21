#!/usr/bin/env python3
"""Icons des Klobása-Planers aus der Vorlage data/logo_5e.svg (Entwurf 5E: Wurst mit Senfklecks, weißer Grund).

  .venv/bin/python -I scripts/make_icon.py [site] [--pruefbild DATEI]

  favicon-32.png                               Browser-Tab
  apple-touch-icon.png                         180 px, vollflächig (iOS rundet selbst)
  icon-192.png, icon-512.png                   „any“, vollflächig
  icon-maskable-192.png, icon-maskable-512.png weißer Grund vollflächig, Inhalt auf 80 % um die Mitte verkleinert
                                               (<g transform="translate(10 10) scale(0.8)">), damit Android nichts abschneidet
  logo.png                                     Logo im Seitenkopf als weiße Kachel, 144 px mit abgerundeten Ecken (transparent außerhalb)
--pruefbild: Übersicht auf hellem/dunklem Grund, auf den Kopffarben und mit Android-Masken
Benötigt cairosvg und Pillow im venv.
"""
import io, math, os, re, sys
import cairosvg
from PIL import Image, ImageDraw

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'logo_5e.svg')
SVG = open(SRC, encoding='utf-8').read()
BG = re.search(r'<rect width="100" height="100" fill="(#[0-9A-Fa-f]{6})"/>', SVG)

def maskable_svg():
    """Grund vollflächig, alles andere in <g transform="translate(10 10) scale(0.8)">."""
    head, rest = SVG.split('>', 1)
    bg = BG.group(0); inner = rest.replace(bg, '', 1).rsplit('</svg>', 1)[0]
    return f'{head}>{bg}<g transform="translate(10 10) scale(0.8)">{inner}</g></svg>'

def render(svg, size):
    return Image.open(io.BytesIO(cairosvg.svg2png(bytestring=svg.encode(), output_width=size, output_height=size))).convert('RGBA')

def rounded(im, r_frac=0.18):
    s = im.size[0] * 4
    m = Image.new('L', (s, s), 0); ImageDraw.Draw(m).rounded_rectangle([0, 0, s - 1, s - 1], radius=round(r_frac * s), fill=255)
    out = im.copy(); out.putalpha(m.resize(im.size, Image.LANCZOS)); return out

def main():
    out = next((x for x in sys.argv[1:] if not x.startswith('--') and not x.endswith('.png')), 'site')
    render(SVG, 32).save(f'{out}/favicon-32.png', optimize=True)
    render(SVG, 180).convert('RGB').save(f'{out}/apple-touch-icon.png', optimize=True)
    for s in (192, 512):
        render(SVG, s).convert('RGB').save(f'{out}/icon-{s}.png', optimize=True)
        render(maskable_svg(), s).convert('RGB').save(f'{out}/icon-maskable-{s}.png', optimize=True)
    rounded(render(SVG, 144)).save(f'{out}/logo.png', optimize=True)
    if '--pruefbild' in sys.argv:
        pruefbild(out, sys.argv[sys.argv.index('--pruefbild') + 1])
    print('Icons geschrieben nach', out)

def pruefbild(out, path):
    from PIL import ImageFont
    try: font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 18)
    except Exception: font = ImageFont.load_default()
    W = 1240
    im = Image.new('RGBA', (W, 760), (255, 255, 255, 255)); d = ImageDraw.Draw(im)
    grounds = [('hell', (255, 255, 255)), ('dunkel', (18, 18, 18)), ('Rasengrün', (23, 67, 44)), ('Senf/Wurst', (74, 40, 24)), ('Nachtblau', (20, 39, 74))]
    fav = Image.open(f'{out}/favicon-32.png'); logo = Image.open(f'{out}/logo.png').resize((72, 72), Image.LANCZOS)
    any_ = Image.open(f'{out}/icon-192.png').convert('RGBA').resize((96, 96), Image.LANCZOS)
    x = 20
    for name, col in grounds:
        d.rectangle([x, 40, x + 220, 300], fill=col)
        im.alpha_composite(fav, (x + 30, 70)); im.alpha_composite(fav.resize((16, 16), Image.LANCZOS), (x + 80, 78))
        im.alpha_composite(logo, (x + 130, 50)); im.alpha_composite(rounded(any_, .22), (x + 30, 170))
        d.text((x + 140, 210), name, fill=(255, 255, 255) if sum(col) < 380 else (0, 0, 0), font=font)
        x += 240
    d.text((20, 10), 'Favicon 32 + 16 px, Logo (Kopf), App-Icon auf hellem, dunklem Grund und den Kopffarben', fill=(0, 0, 0), font=font)
    d.text((20, 320), 'maskable unter Android-Masken (Kreis, Squircle, Tropfen, abgerundet), 108 px', fill=(0, 0, 0), font=font)
    mk = Image.open(f'{out}/icon-maskable-512.png').convert('RGBA').resize((432, 432), Image.LANCZOS)
    def mask(kind, s=432):
        m = Image.new('L', (s, s), 0); md = ImageDraw.Draw(m)
        if kind == 'kreis': md.ellipse([0, 0, s - 1, s - 1], fill=255)
        elif kind == 'abgerundet': md.rounded_rectangle([0, 0, s - 1, s - 1], radius=s // 5, fill=255)
        elif kind == 'tropfen': md.rounded_rectangle([0, 0, s - 1, s - 1], radius=s // 2, fill=255); md.rectangle([s // 2, s // 2, s - 1, s - 1], fill=255)
        else:
            md.polygon([(s / 2 + s / 2 * abs(math.cos(t)) ** .5 * (1 if math.cos(t) >= 0 else -1),
                         s / 2 + s / 2 * abs(math.sin(t)) ** .5 * (1 if math.sin(t) >= 0 else -1)) for t in [i * 2 * math.pi / 400 for i in range(400)]], fill=255)
        return m
    for row, bg in enumerate([(232, 236, 240), (24, 26, 30)]):
        y = 360 + row * 190
        d.rectangle([20, y, W - 20, y + 170], fill=bg)
        for i, kind in enumerate(['kreis', 'squircle', 'tropfen', 'abgerundet']):
            ic = Image.new('RGBA', (432, 432), (0, 0, 0, 0)); ic.paste(mk, (0, 0), mask(kind))
            im.alpha_composite(ic.resize((108, 108), Image.LANCZOS), (60 + i * 150, y + 30))
        raw = mk.resize((108, 108), Image.LANCZOS).copy(); ImageDraw.Draw(raw).ellipse([11, 11, 97, 97], outline=(255, 0, 80, 255), width=2)
        im.alpha_composite(raw, (700, y + 30))
        d.text((830, y + 70), 'ungeschnitten, rot = sichere Zone', fill=(255, 255, 255) if row else (0, 0, 0), font=font)
    im.convert('RGB').save(path)

if __name__ == '__main__':
    main()
