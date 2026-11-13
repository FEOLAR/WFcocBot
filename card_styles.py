"""
Пять дополнительных стилей карточки клана (/card). Первый стиль — «Сайт» —
живёт в card_builder.render_site.

У каждого стиля своя идея, палитра и раскладка, а не перекраска одного шаблона:
  2. Летопись — пергамент военной хроники, книжный шрифт, сургучная печать.
  3. Разведка — чертёж базы: сетка, прицел вокруг значка, таблица фактов.
  4. Лава    — ночь войны: огромное число побед, раскалённое свечение.
  5. Ледник  — светлая морозная карточка, узкий шрифт, четыре больших числа.
  6. Столица — главная тема рейды: добыча столицы крупно, изумруд и золото.
"""
import math
import os
from collections import Counter
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from card_builder import FREQ_RU, TYPE_RU, _ru_league

W, H = 1280, 720
_F = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")


def font(name: str, size: int, weight: int | None = None) -> ImageFont.FreeTypeFont:
    files = {"russo": "RussoOne-Regular.ttf", "serif": "PTSerif-Bold.ttf", "mono": "JetBrainsMono.ttf",
             "oswald": "Oswald.ttf", "nunito": "Nunito-Regular.ttf"}
    f = ImageFont.truetype(os.path.join(_F, files[name]), size)
    if weight:
        try:
            f.set_variation_by_axes([weight])
        except Exception:
            pass
    return f


def fit(d, text, name, size, max_w, weight=None, min_size=14):
    while size > min_size:
        f = font(name, size, weight)
        if d.textlength(text, font=f) <= max_w:
            return f
        size -= 2
    return font(name, min_size, weight)


def num(n) -> str:
    return f"{int(n or 0):,}".replace(",", " ")


def facts(clan, raids):
    members = list(clan.members or [])
    wins, losses, ties = clan.war_wins or 0, clan.war_losses or 0, clan.war_ties or 0
    total = wins + losses + ties
    r = raids[0] if raids else None
    return {
        "name": clan.name or "", "tag": "#" + (clan.tag or "").lstrip("#"), "level": clan.level,
        "members": clan.member_count or len(members),
        "wins": wins, "losses": losses, "ties": ties, "rate": (wins / total * 100) if total else None,
        "streak": getattr(clan, "war_win_streak", 0) or 0,
        "cwl": _ru_league(getattr(clan, "war_league", None)),
        "capital": _ru_league(getattr(clan, "capital_league", None)),
        "freq": FREQ_RU.get(str(clan.war_frequency), "—"),
        "type": TYPE_RU.get(str(clan.type), ""),
        "req_th": getattr(clan, "required_townhall", None),
        "donations": sum(getattr(m, "donations", 0) or 0 for m in members),
        "ths": Counter(getattr(m, "town_hall", 0) for m in members if getattr(m, "town_hall", 0)),
        "raid_loot": getattr(r, "total_loot", 0) or 0 if r else None,
        "raid_people": len(list(getattr(r, "members", None) or [])) if r else 0,
        "raid_districts": getattr(r, "destroyed_district_count", 0) or 0 if r else 0,
        "raid_attacks": getattr(r, "attack_count", 0) or 0 if r else 0,
        "raid_medals": (getattr(r, "offensive_reward", 0) or 0) + (getattr(r, "defensive_reward", 0) or 0) if r else 0,
        "raid_live": bool(r and getattr(r, "state", "") == "ongoing"),
        "capital_points": getattr(clan, "capital_points", None),
        "updated": (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%d.%m.%Y"),
    }


def gradient(c1, c2, angle_deg=90) -> Image.Image:
    img = Image.new("RGB", (W, H))
    px = img.load()
    a = math.radians(angle_deg)
    dx, dy = math.cos(a), math.sin(a)
    span = abs(dx) * W + abs(dy) * H
    for y in range(H):
        for x in range(W):
            t = ((x * dx + y * dy) - min(0, dx * W) - min(0, dy * H)) / span
            t = max(0.0, min(1.0, t))
            px[x, y] = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
    return img.convert("RGBA")


def glow(img, box, color, blur, strength=180):
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).ellipse(box, fill=strength)
    m = m.filter(ImageFilter.GaussianBlur(blur))
    return Image.composite(Image.new("RGBA", img.size, color + (255,)), img, m)


def paste_badge(img, badge, cx, cy, size, shadow=(0, 0, 0), shadow_alpha=0.6):
    if badge is None:
        return
    b = badge.copy()
    b.thumbnail((size, size), Image.LANCZOS)
    if b.width < size:
        b = b.resize((size, int(b.height * size / b.width)), Image.LANCZOS)
    x, y = cx - b.width // 2, cy - b.height // 2
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sh.paste(shadow + (255,), (x, y + size // 18), b.split()[3].point(lambda a: int(a * shadow_alpha)))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(size // 20)))
    img.alpha_composite(b, (x, y))


def th_bar(img, d, x, y, w, h, ths, colors, radius=None, label_font=None, label_color=None, gap=0):
    if not ths:
        return y
    order = sorted(ths, reverse=True)
    total = sum(ths.values())
    bar = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bar)
    cur = 0
    for i, t in enumerate(order):
        seg = round(ths[t] / total * w) if i < len(order) - 1 else w - cur
        bd.rectangle((cur, 0, cur + seg - gap, h), fill=colors(t, i, len(order)))
        cur += seg
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius if radius is not None else h // 2, fill=255)
    img.paste(bar, (x, y), mask)
    if label_font:
        lx, ly = x, y + h + 12
        for i, t in enumerate(order):
            txt = f"ТХ{t}: {ths[t]}"
            tw = d.textlength(txt, font=label_font) + 34
            if lx + tw > x + w:
                lx, ly = x, ly + label_font.size + 10
            d.rectangle((lx, ly + 5, lx + 12, ly + 17), fill=colors(t, i, len(order)))
            d.text((lx + 18, ly), txt, font=label_font, fill=label_color)
            lx += tw
        return ly + label_font.size
    return y + h


def wrap(d, text, f, max_w, max_lines):
    words, lines, cur = (text or "").replace("\n", " ").split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textlength(t, font=f) > max_w and cur:
            lines.append(cur)
            cur = w
            if len(lines) == max_lines:
                break
        else:
            cur = t
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) == max_lines and " ".join(lines) != " ".join(words):
        lines[-1] = lines[-1].rstrip(".,;:— ") + "…"
    return lines


# ══════════════════════════════════════════════════════════════════════════════
# 2. Летопись — пергамент
# ══════════════════════════════════════════════════════════════════════════════

def render_chronicle(clan, raids, badge):
    F = facts(clan, raids)
    PAPER, PAPER2, INK, INK2, RED = (239, 228, 196), (216, 201, 160), (59, 36, 20), (110, 82, 52), (158, 43, 37)
    img = Image.new("RGBA", (W, H), PAPER + (255,))
    d = ImageDraw.Draw(img)
    for x in range(0, W, 3):   # волокна бумаги, как на сайте
        d.line((x, 0, x, H), fill=PAPER2 + (40,), width=1)
    vig = Image.new("L", (W, H), 0)
    ImageDraw.Draw(vig).rectangle((0, 0, W, H), fill=120)
    ImageDraw.Draw(vig).ellipse((-200, -160, W + 200, H + 160), fill=0)
    img = Image.composite(Image.new("RGBA", (W, H), (120, 84, 40, 255)), img, vig.filter(ImageFilter.GaussianBlur(90)))
    d = ImageDraw.Draw(img)
    d.rectangle((28, 28, W - 28, H - 28), outline=INK2, width=2)
    d.rectangle((38, 38, W - 38, H - 38), outline=INK2 + (120,), width=1)

    paste_badge(img, badge, W // 2, 150, 190, shadow=(80, 50, 20), shadow_alpha=0.45)
    d.text((W // 2, 292), F["name"], font=fit(d, F["name"], "serif", 64, 900), fill=INK, anchor="mm")
    d.text((W // 2, 342), f"Летопись клана, уровень {F['level']}, {F['tag']}", font=font("serif", 22), fill=INK2, anchor="mm")
    d.line((W // 2 - 240, 372, W // 2 + 240, 372), fill=INK2, width=1)

    cols = [("Победы", num(F["wins"])), ("Поражения", num(F["losses"])), ("Ничьи", num(F["ties"])),
            ("Процент побед", f"{F['rate']:.0f}%" if F["rate"] is not None else "—")]
    cw = (W - 200) // 4
    for i, (lab, val) in enumerate(cols):
        x = 100 + i * cw + cw // 2
        d.text((x, 410), lab, font=font("nunito", 19, 700), fill=INK2, anchor="mm")
        d.text((x, 458), val, font=font("serif", 48), fill=RED if i == 0 else INK, anchor="mm")
        if i:
            d.line((100 + i * cw, 395, 100 + i * cw, 488), fill=INK2 + (140,), width=1)

    d.line((100, 510, W - 100, 510), fill=INK2 + (140,), width=1)
    row = [("Лига войн кланов", F["cwl"]), ("Столичная лига", F["capital"]),
           ("Состав", f"{F['members']} из 50"), ("Отдано войск", num(F["donations"]))]
    for i, (lab, val) in enumerate(row):
        x = 100 + i * cw + cw // 2
        d.text((x, 538), lab, font=font("nunito", 18, 700), fill=INK2, anchor="mm")
        d.text((x, 572), val, font=fit(d, val, "serif", 28, cw - 30), fill=INK, anchor="mm")

    shades = lambda t, i, n: tuple(int(INK[k] + (PAPER2[k] - INK[k]) * (i / max(1, n - 1))) for k in range(3)) + (255,)
    th_bar(img, d, 300, 606, W - 600, 10, F["ths"], shades)
    d.text((W // 2, 634), "  ".join(f"ТХ{t}: {c}" for t, c in sorted(F["ths"].items(), reverse=True)),
           font=font("nunito", 16, 700), fill=INK2, anchor="mm")

    # сургучная печать
    sx, sy = W - 150, 150
    seal = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(seal)
    pts = [(sx + (62 + (6 if k % 2 else 0)) * math.cos(k * math.pi / 12), sy + (62 + (6 if k % 2 else 0)) * math.sin(k * math.pi / 12)) for k in range(24)]
    sd.polygon(pts, fill=RED + (255,))
    sd.ellipse((sx - 46, sy - 46, sx + 46, sy + 46), outline=(120, 25, 22, 255), width=3)
    sd.text((sx, sy - 4), "WF", font=font("serif", 40), fill=(235, 190, 170, 255), anchor="mm")
    seal = seal.rotate(-12, center=(sx, sy))
    img.alpha_composite(seal)
    d.text((W // 2, H - 52), f"warfilcoc.ru   {F['updated']}", font=font("serif", 17), fill=INK2 + (200,), anchor="mm")
    return img


# ══════════════════════════════════════════════════════════════════════════════
# 3. Разведка — чертёж базы
# ══════════════════════════════════════════════════════════════════════════════

def render_scout(clan, raids, badge):
    F = facts(clan, raids)
    BG, GRID, GRID2, CYAN, WHITE, DIM = (11, 36, 64), (24, 64, 104), (18, 52, 88), (127, 211, 255), (232, 244, 255), (130, 170, 205)
    img = Image.new("RGBA", (W, H), BG + (255,))
    d = ImageDraw.Draw(img)
    for x in range(0, W, 32):
        d.line((x, 0, x, H), fill=GRID2 if x % 160 else GRID, width=1)
    for y in range(0, H, 32):
        d.line((0, y, W, y), fill=GRID2 if y % 160 else GRID, width=1)

    # прицел со значком
    cx, cy, R = 960, 300, 200
    d.ellipse((cx - R, cy - R, cx + R, cy + R), outline=CYAN + (200,), width=2)
    d.ellipse((cx - R + 26, cy - R + 26, cx + R - 26, cy + R - 26), outline=CYAN + (90,), width=1)
    for k in range(72):
        a = k * math.pi / 36
        r1 = R - (14 if k % 6 == 0 else 7)
        d.line((cx + r1 * math.cos(a), cy + r1 * math.sin(a), cx + R * math.cos(a), cy + R * math.sin(a)), fill=CYAN + (160,), width=1)
    for a in (0, 90, 180, 270):
        r = math.radians(a)
        d.line((cx + (R + 8) * math.cos(r), cy + (R + 8) * math.sin(r), cx + (R + 40) * math.cos(r), cy + (R + 40) * math.sin(r)), fill=CYAN, width=2)
    img = glow(img, (cx - 150, cy - 150, cx + 150, cy + 150), (40, 120, 190), 60, 120)
    d = ImageDraw.Draw(img)
    paste_badge(img, badge, cx, cy, 250, shadow=(0, 10, 30))
    d.text((cx, cy + R + 56), f"Цель: {F['tag']}", font=font("mono", 20, 600), fill=CYAN, anchor="mm")

    x0 = 70
    d.text((x0, 58), "Разведданные", font=font("mono", 20, 600), fill=CYAN)
    d.text((x0 - 2, 86), F["name"], font=fit(d, F["name"], "oswald", 84, 640, 700), fill=WHITE)
    d.text((x0, 190), f"Уровень {F['level']}. {F['type'].capitalize()}" + (f", от ТХ{F['req_th']}" if F["req_th"] else ""),
           font=font("nunito", 22, 700), fill=DIM)

    rows = [("Победы в войнах", num(F["wins"])), ("Поражения", num(F["losses"])),
            ("Процент побед", f"{F['rate']:.1f}%".replace(".", ",") if F["rate"] is not None else "скрыто"),
            ("Серия побед", str(F["streak"])), ("Лига войн кланов", F["cwl"]),
            ("Столичная лига", F["capital"]), ("Состав", f"{F['members']}/50"),
            ("Частота войн", F["freq"])]
    y = 238
    fl, fv = font("mono", 19, 500), font("oswald", 25, 600)
    for lab, val in rows:
        d.text((x0, y + 4), lab, font=fl, fill=DIM)
        vw = d.textlength(val, font=fv)
        lw = d.textlength(lab, font=fl)
        dots_x, dots_end = x0 + lw + 12, x0 + 600 - vw - 12
        while dots_x < dots_end:
            d.text((dots_x, y + 4), ".", font=fl, fill=GRID)
            dots_x += 12
        d.text((x0 + 600, y), val, font=fv, fill=WHITE, anchor="ra")
        y += 37

    # гистограмма ратуш
    ths = F["ths"]
    if ths:
        bx, by = x0, 652
        mx = max(ths.values())
        order = sorted(ths, reverse=True)
        bw = min(56, (600 - 10 * (len(order) - 1)) // len(order))
        for i, t in enumerate(order):
            h = int(30 * ths[t] / mx) + 4
            xx = bx + i * (bw + 10)
            d.rectangle((xx, by - h, xx + bw, by), fill=CYAN + (220,))
            d.text((xx + bw // 2, by + 16), f"{t}", font=font("mono", 15, 600), fill=DIM, anchor="mm")
            d.text((xx + bw // 2, by - h - 14), str(ths[t]), font=font("mono", 15, 700), fill=WHITE, anchor="mm")
        d.text((bx + len(order) * (bw + 10) + 8, by - 14), "ратуши\nв составе", font=font("mono", 14), fill=DIM)
    d.text((W - 40, H - 30), f"warfilcoc.ru  {F['updated']}", font=font("mono", 16, 500), fill=DIM, anchor="ra")
    return img


# ══════════════════════════════════════════════════════════════════════════════
# 4. Лава — ночь войны
# ══════════════════════════════════════════════════════════════════════════════

def render_lava(clan, raids, badge):
    F = facts(clan, raids)
    HOT, EMBER, CREAM, ASH = (255, 122, 26), (255, 194, 74), (255, 240, 225), (176, 140, 120)
    img = gradient((16, 7, 6), (54, 14, 6), 100)
    img = glow(img, (-200, 420, 900, 1100), (190, 50, 10), 140, 200)
    img = glow(img, (820, 60, 1220, 460), (230, 90, 20), 90, 150)
    d = ImageDraw.Draw(img)
    rnd = __import__("random").Random(7)
    for _ in range(70):   # искры
        x, y = rnd.randint(0, W), rnd.randint(260, H)
        r = rnd.choice((1, 1, 2, 2, 3))
        d.ellipse((x - r, y - r, x + r, y + r), fill=EMBER + (rnd.randint(90, 220),))

    paste_badge(img, badge, 1020, 250, 300, shadow=(0, 0, 0), shadow_alpha=0.8)
    d.text((1020, 440), F["name"], font=fit(d, F["name"], "russo", 46, 420), fill=CREAM, anchor="mm")
    d.text((1020, 482), f"Уровень {F['level']}  {F['tag']}", font=font("nunito", 20, 700), fill=ASH, anchor="mm")

    d.text((70, 60), "Побед в клановых войнах", font=font("nunito", 24, 800), fill=EMBER)
    big = num(F["wins"])
    fb = fit(d, big, "russo", 230, 700)
    hot = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(hot).text((60, 90), big, font=fb, fill=HOT + (255,))
    img.alpha_composite(hot.filter(ImageFilter.GaussianBlur(18)))
    d = ImageDraw.Draw(img)
    d.text((60, 90), big, font=fb, fill=EMBER, stroke_width=2, stroke_fill=(120, 30, 0))
    if F["rate"] is not None:
        d.text((70, 350), f"{F['rate']:.0f}% побед", font=font("russo", 40), fill=CREAM)
        sub = f"поражений {num(F['losses'])}" + (f", ничьих {num(F['ties'])}" if F["ties"] else "")
        if F["streak"] > 1:
            sub += f", серия побед {F['streak']}"
        d.text((70, 404), sub, font=font("nunito", 22, 700), fill=ASH)

    y = 560
    d.line((70, y - 26, W - 70, y - 26), fill=HOT + (120,), width=2)
    cells = [("Лига войн", F["cwl"]), ("Столичная лига", F["capital"]),
             ("Состав", f"{F['members']}/50"), ("Отдано войск", num(F["donations"]))]
    cw = (W - 140) // 4
    for i, (lab, val) in enumerate(cells):
        x = 70 + i * cw
        d.text((x, y), lab, font=font("nunito", 18, 700), fill=ASH)
        d.text((x, y + 28), val, font=fit(d, val, "russo", 32, cw - 24), fill=CREAM)
    d.text((W - 70, H - 36), f"warfilcoc.ru  {F['updated']}", font=font("nunito", 16, 700), fill=ASH, anchor="ra")
    return img


# ══════════════════════════════════════════════════════════════════════════════
# 5. Ледник — светлая карточка
# ══════════════════════════════════════════════════════════════════════════════

def render_frost(clan, raids, badge):
    F = facts(clan, raids)
    NAVY, BLUE, STEEL, LINE = (15, 36, 64), (36, 116, 178), (88, 112, 140), (176, 204, 226)
    img = gradient((240, 248, 253), (205, 228, 244), 70)
    d = ImageDraw.Draw(img)
    for k in range(9):   # морозные грани
        x = 860 + k * 60
        d.polygon([(x, 0), (x + 40, 0), (x - 180, H), (x - 220, H)], fill=(255, 255, 255, 40))

    paste_badge(img, badge, 170, 170, 210, shadow=(60, 100, 140), shadow_alpha=0.35)
    d.text((310, 100), F["name"], font=fit(d, F["name"], "oswald", 84, 860, 700), fill=NAVY)
    d.text((314, 210), f"Уровень {F['level']}   {F['tag']}   {F['type']}", font=font("nunito", 24, 700), fill=STEEL)

    d.line((80, 318, W - 80, 318), fill=LINE, width=2)
    stats = [("побед в войнах", num(F["wins"])),
             ("процент побед", f"{F['rate']:.0f}%" if F["rate"] is not None else "—"),
             ("в составе", f"{F['members']}"), ("серия побед", str(F["streak"]))]
    cw = (W - 160) // 4
    for i, (lab, val) in enumerate(stats):
        x = 80 + i * cw
        d.text((x, 340), val, font=font("oswald", 92, 600), fill=BLUE if i == 0 else NAVY)
        d.text((x + 4, 462), lab, font=font("nunito", 22, 700), fill=STEEL)
    d.line((80, 512, W - 80, 512), fill=LINE, width=2)

    d.text((80, 534), f"Лига войн кланов: {F['cwl']}", font=font("nunito", 22, 800), fill=NAVY)
    d.text((640, 534), f"Столичная лига: {F['capital']}", font=font("nunito", 22, 800), fill=NAVY)
    icy = lambda t, i, n: tuple(int(a + (b - a) * (i / max(1, n - 1))) for a, b in zip((20, 70, 130), (170, 210, 240))) + (255,)
    th_bar(img, d, 80, 594, W - 160, 14, F["ths"], icy, label_font=font("nunito", 17, 700), label_color=STEEL)
    d.text((W - 80, H - 34), f"warfilcoc.ru  {F['updated']}", font=font("nunito", 16, 700), fill=STEEL, anchor="ra")
    return img


# ══════════════════════════════════════════════════════════════════════════════
# 6. Столица — рейды крупным планом
# ══════════════════════════════════════════════════════════════════════════════

def render_capital(clan, raids, badge):
    F = facts(clan, raids)
    GOLD, GOLD_D, CREAM, SAGE = (232, 193, 90), (150, 110, 30), (247, 240, 220), (150, 190, 160)
    img = gradient((8, 38, 28), (20, 72, 52), 30)
    img = glow(img, (600, -100, 1400, 600), (40, 130, 90), 120, 150)
    d = ImageDraw.Draw(img)
    # силуэт крепостной стены по низу
    base = H - 70
    wall = [(0, H)]
    x = 0
    while x <= W:
        wall += [(x, base), (x + 34, base), (x + 34, base - 22), (x + 60, base - 22), (x + 60, base)]
        x += 60
    wall += [(W, H)]
    d.polygon(wall, fill=(6, 26, 18))

    paste_badge(img, badge, 130, 120, 150, shadow=(0, 15, 8))
    d.text((230, 70), F["name"], font=fit(d, F["name"], "russo", 50, 520), fill=CREAM)
    d.text((232, 132), f"Уровень {F['level']}   {F['tag']}", font=font("nunito", 20, 700), fill=SAGE)

    if F["raid_loot"] is not None:
        d.text((70, 236), "Рейды столицы идут" if F["raid_live"] else "Последние рейды столицы",
               font=font("nunito", 24, 800), fill=GOLD)
        loot = num(F["raid_loot"])
        d.text((64, 268), loot, font=fit(d, loot, "russo", 132, 760), fill=GOLD, stroke_width=2, stroke_fill=GOLD_D)
        d.text((70, 430), "золота столицы добыто", font=font("nunito", 24, 700), fill=CREAM)
        chips = [(str(F["raid_people"]), "участников"), (str(F["raid_districts"]), "районов разрушено"),
                 (str(F["raid_attacks"]), "атак")]
        x = 70
        for v, lab in chips:
            d.text((x, 486), v, font=font("russo", 40), fill=CREAM)
            vw = d.textlength(v, font=font("russo", 40))
            d.text((x + vw + 10, 504), lab, font=font("nunito", 19, 700), fill=SAGE)
            x += vw + 10 + d.textlength(lab, font=font("nunito", 19, 700)) + 46
    else:
        d.text((70, 300), "Данных о рейдах пока нет", font=font("russo", 40), fill=CREAM)

    # колонка справа
    rx = 900
    d.line((rx - 40, 220, rx - 40, 540), fill=GOLD_D, width=1)
    side = [("Столичная лига", F["capital"]), ("Очки столицы", num(F["capital_points"]) if F["capital_points"] else "—"),
            ("Победы в войнах", num(F["wins"])), ("Лига войн кланов", F["cwl"])]
    y = 226
    for lab, val in side:
        d.text((rx, y), lab, font=font("nunito", 18, 700), fill=SAGE)
        d.text((rx, y + 26), val, font=fit(d, val, "russo", 32, 320), fill=CREAM)
        y += 80
    d.text((W - 50, H - 40), f"warfilcoc.ru  {F['updated']}", font=font("nunito", 16, 700), fill=SAGE, anchor="ra")
    return img


STYLES = [
    (render_chronicle, "Летопись"),
    (render_scout, "Разведка"),
    (render_lava, "Лава"),
    (render_frost, "Ледник"),
    (render_capital, "Столица"),
]
