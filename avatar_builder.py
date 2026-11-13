"""
Аватарка бота в стиле сайта warfilcoc.ru: значок клана из игры
на фиолетовом фоне с мягким свечением и тонким кольцом.

Значок берётся из CoC API, поэтому при смене значка или уровня клана
достаточно заново отправить боту /avatar.
"""
import io

from PIL import Image, ImageDraw, ImageFilter

SIZE = 640


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _background(size: int) -> Image.Image:
    # как на сайте: linear-gradient(160deg, #3A1C7A, #1A0D3C 70%)
    top, bottom = (0x3A, 0x1C, 0x7A), (0x1A, 0x0D, 0x3C)
    grad = Image.new("RGB", (size, size))
    px = grad.load()
    for y in range(size):
        for x in range(size):
            t = min(1.0, (0.34 * x + 0.94 * y) / (size * 1.28) / 0.7)
            px[x, y] = _lerp(top, bottom, t)

    # radial-gradient(circle at 50% 45%, rgba(154,98,240,.55), transparent 55%)
    glow = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(glow)
    r = int(size * 0.42)
    cx, cy = size // 2, int(size * 0.47)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=150)
    glow = glow.filter(ImageFilter.GaussianBlur(size * 0.12))
    violet = Image.new("RGB", (size, size), (154, 98, 240))
    grad = Image.composite(violet, grad, glow)

    # тонкое светлое кольцо
    ring = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring)
    rr = int(size * 0.40)
    rd.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), outline=(200, 170, 255, 60), width=max(2, size // 320))
    out = grad.convert("RGBA")
    out.alpha_composite(ring)

    # редкие «звёзды»
    sd = ImageDraw.Draw(out)
    for fx, fy, a in ((0.82, 0.12, 120), (0.12, 0.30, 90), (0.90, 0.62, 80), (0.20, 0.86, 70), (0.70, 0.90, 60)):
        x, y = int(fx * size), int(fy * size)
        sd.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(230, 220, 255, a))
    return out


def build_avatar(badge_png: bytes, size: int = SIZE) -> io.BytesIO:
    bg = _background(size)
    badge = Image.open(io.BytesIO(badge_png)).convert("RGBA")
    target = int(size * 0.66)
    badge.thumbnail((target, target), Image.LANCZOS)
    if badge.width < target:
        scale = target / badge.width
        badge = badge.resize((target, int(badge.height * scale)), Image.LANCZOS)

    x = (size - badge.width) // 2
    y = int(size * 0.47) - badge.height // 2

    shadow = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    alpha = badge.split()[3].point(lambda a: int(a * 0.55))
    shadow.paste((10, 4, 30, 255), (x, y + size // 40), alpha)
    shadow = shadow.filter(ImageFilter.GaussianBlur(size // 45))
    bg.alpha_composite(shadow)
    bg.alpha_composite(badge, (x, y))

    buf = io.BytesIO()
    bg.convert("RGB").save(buf, "PNG", optimize=True)
    buf.seek(0)
    buf.name = "warfil_avatar.png"
    return buf
