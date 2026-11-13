"""
Карточка клана для /card — в стиле сайта warfilcoc.ru.

Слева — герой: значок клана со свечением, название игровым шрифтом,
уровень и тег. Справа — главное число (победы в войнах) и сетка
фактов, разделённая тонкими золотыми линиями, без одинаковых «плашек».
Внизу — состав по ратушам одной сегментированной полосой.
"""
import io
import os
from collections import Counter
from datetime import datetime, timedelta, timezone

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont

_BASE = os.path.dirname(os.path.abspath(__file__))
FONT_BODY = os.path.join(_BASE, "fonts", "Nunito-Regular.ttf")
FONT_DISPLAY = os.path.join(_BASE, "fonts", "RussoOne-Regular.ttf")
FONT_PATH = FONT_BODY  # совместимость

W, H = 1280, 720

# Палитра сайта
VIOLET_TOP = (0x3A, 0x1C, 0x7A)
VIOLET_BOT = (0x1A, 0x0D, 0x3C)
VIOLET_GLOW = (154, 98, 240)
INK = (0x14, 0x0A, 0x2E)
GOLD = (0xF0, 0xC3, 0x5A)
GOLD_LIGHT = (0xF7, 0xD2, 0x7A)
GOLD_DARK = (0xA8, 0x74, 0x1C)
CREAM = (0xFF, 0xF4, 0xDC)
MUTED = (0xB9, 0xA8, 0xDF)
DIM = (0x8A, 0x7A, 0xB4)
WIN = (0x7C, 0xE0, 0x8F)
LOSS = (0xF2, 0x7A, 0x7A)

TH_COLORS = {
    18: (240, 195, 90), 17: (196, 120, 240), 16: (110, 170, 245), 15: (150, 110, 230),
    14: (90, 200, 140), 13: (90, 160, 220), 12: (120, 140, 230), 11: (230, 150, 80),
    10: (220, 90, 90), 9: (150, 150, 170),
}

LEAGUE_RU = {
    "Legend League": "Легендарная лига",
    "Champion League I": "Чемпионская I", "Champion League II": "Чемпионская II",
    "Champion League III": "Чемпионская III",
    "Master League I": "Мастер I", "Master League II": "Мастер II", "Master League III": "Мастер III",
    "Crystal League I": "Кристальная I", "Crystal League II": "Кристальная II",
    "Crystal League III": "Кристальная III",
    "Gold League I": "Золотая I", "Gold League II": "Золотая II", "Gold League III": "Золотая III",
    "Silver League I": "Серебряная I", "Silver League II": "Серебряная II",
    "Silver League III": "Серебряная III",
    "Bronze League I": "Бронзовая I", "Bronze League II": "Бронзовая II",
    "Bronze League III": "Бронзовая III",
    "Titan League I": "Титановая I", "Titan League II": "Титановая II", "Titan League III": "Титановая III",
    "Unranked": "Без лиги",
}

FREQ_RU = {
    "always": "всегда", "moreThanOncePerWeek": "чаще раза в неделю",
    "oncePerWeek": "раз в неделю", "lessThanOncePerWeek": "реже раза в неделю",
    "never": "не воюет", "unknown": "не указана",
}

TYPE_RU = {"open": "открытый", "inviteOnly": "по заявке", "closed": "закрытый"}


def _ru_league(name) -> str:
    n = str(getattr(name, "name", name) or "")
    if not n or n == "None":
        return "—"
    return LEAGUE_RU.get(n, n)


def _body(size: int, weight: int = 500) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(FONT_BODY, size)
    try:
        f.set_variation_by_axes([weight])
    except Exception:
        pass
    return f


def _display(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(FONT_DISPLAY, size)
    except OSError:
        return _body(size, 900)


def _num(n) -> str:
    return f"{int(n or 0):,}".replace(",", " ")


def _fit(draw, text, font_fn, size, max_w, min_size=18):
    while size > min_size:
        f = font_fn(size)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 2
    return font_fn(min_size)


def _background() -> Image.Image:
    # linear-gradient(160deg, #3A1C7A, #1A0D3C 70%) — как на сайте
    grad = Image.new("RGB", (W, H))
    px = grad.load()
    for y in range(H):
        for x in range(W):
            t = min(1.0, (0.34 * x / W + 0.94 * y / H) / 0.9)
            px[x, y] = tuple(int(VIOLET_TOP[i] + (VIOLET_BOT[i] - VIOLET_TOP[i]) * t) for i in range(3))
    img = grad.convert("RGBA")

    glow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(glow).ellipse((20, 40, 470, 490), fill=170)
    glow = glow.filter(ImageFilter.GaussianBlur(70))
    img = Image.composite(Image.new("RGBA", (W, H), VIOLET_GLOW + (255,)), img, glow)

    # тёмная правая половина под данные
    shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    sd.rectangle((520, 0, W + 60, H), fill=INK + (140,))
    img.alpha_composite(shade.filter(ImageFilter.GaussianBlur(60)))

    d = ImageDraw.Draw(img)
    for fx, fy, a in ((0.06, 0.08, 140), (0.34, 0.06, 110), (0.42, 0.88, 90), (0.03, 0.78, 80), (0.28, 0.72, 70)):
        x, y = int(fx * W), int(fy * H)
        d.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(235, 225, 255, a))
    return img


async def _dl_image(url: str) -> Image.Image | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url)
            r.raise_for_status()
            return Image.open(io.BytesIO(r.content)).convert("RGBA")
    except Exception:
        return None


def _gold_rule(d, x1, y, x2):
    d.line((x1, y, x2, y), fill=GOLD_DARK + (170,), width=1)


def _stat(d, x, y, label, value, value_color=CREAM, size=34, max_w=200):
    d.text((x, y), label, font=_body(17, 600), fill=MUTED)
    d.text((x, y + 24), value, font=_fit(d, value, _display, size, max_w, 16), fill=value_color)


def render_site(clan, raids=None, badge: Image.Image | None = None) -> Image.Image:
    img = _background()
    d = ImageDraw.Draw(img)
    members = list(clan.members or [])

    # ── Герой слева ──────────────────────────────────────────────────────────
    cx = 250
    if badge is not None:
        b = badge.copy()
        b.thumbnail((300, 300), Image.LANCZOS)
        if b.width < 300:
            b = b.resize((300, int(b.height * 300 / b.width)), Image.LANCZOS)
        bx, by = cx - b.width // 2, 70
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sh.paste((8, 2, 24, 255), (bx, by + 16), b.split()[3].point(lambda a: int(a * 0.6)))
        img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(14)))
        img.alpha_composite(b, (bx, by))

    name = clan.name or ""
    f_name = _fit(d, name, _display, 64, 440)
    d.text((cx, 420), name, font=f_name, fill=CREAM, anchor="mm",
           stroke_width=4, stroke_fill=INK)
    sub = f"Уровень {clan.level}  #{clan.tag.lstrip('#')}"
    d.text((cx, 472), sub, font=_body(22, 700), fill=GOLD_LIGHT, anchor="mm")
    ctype = TYPE_RU.get(str(clan.type), str(clan.type or ""))
    req = getattr(clan, "required_townhall", None)
    meta = ctype.capitalize() + (f", от ТХ{req}" if req else "")
    d.text((cx, 504), meta, font=_body(19, 600), fill=MUTED, anchor="mm")

    desc = (clan.description or "").strip().replace("\n", " ")
    if desc:
        f_desc = _body(17, 500)
        words, lines, cur = desc.split(), [], ""
        for w in words:
            t = (cur + " " + w).strip()
            if d.textlength(t, font=f_desc) > 400:
                lines.append(cur)
                cur = w
            else:
                cur = t
            if len(lines) == 3:
                break
        if cur and len(lines) < 3:
            lines.append(cur)
        if len(lines) == 3 and len(" ".join(lines)) < len(desc):
            lines[-1] = lines[-1].rstrip(".,; ") + "…"
        for i, ln in enumerate(lines):
            d.text((cx, 548 + i * 24), ln, font=f_desc, fill=DIM, anchor="mm")

    # ── Правая часть ─────────────────────────────────────────────────────────
    RX, RW = 560, W - 560 - 60
    wins, losses, ties = clan.war_wins or 0, clan.war_losses or 0, clan.war_ties or 0

    d.text((RX, 56), "Побед в войнах", font=_body(20, 700), fill=MUTED)
    d.text((RX - 4, 84), _num(wins), font=_display(118), fill=GOLD,
           stroke_width=3, stroke_fill=GOLD_DARK)
    wl_x = RX + d.textlength(_num(wins), font=_display(118)) + 28
    total = wins + losses + ties
    if total:
        rate = wins / total * 100
        d.text((wl_x, 108), f"{rate:.0f}% побед", font=_display(34), fill=CREAM)
        d.text((wl_x, 152), f"поражений {_num(losses)}" + (f", ничьих {_num(ties)}" if ties else ""),
               font=_body(19, 600), fill=MUTED)
    else:
        d.text((wl_x, 120), "журнал войн скрыт", font=_body(20, 600), fill=MUTED)
    streak = getattr(clan, "war_win_streak", 0) or 0
    if streak > 1:
        d.text((wl_x, 180), f"серия побед: {streak}", font=_body(19, 700), fill=WIN)

    _gold_rule(d, RX, 236, RX + RW)

    col = RW // 3
    _stat(d, RX, 256, "Состав", f"{clan.member_count}/50")
    _stat(d, RX + col, 256, "Лига войн кланов", _ru_league(getattr(clan, "war_league", None)), size=30)
    _stat(d, RX + 2 * col, 256, "Столичная лига", _ru_league(getattr(clan, "capital_league", None)), size=30)

    given = sum(getattr(m, "donations", 0) or 0 for m in members)
    _stat(d, RX, 346, "Войска отдано за сезон", _num(given))
    _stat(d, RX + col, 346, "Частота войн",
          FREQ_RU.get(str(clan.war_frequency), str(clan.war_frequency or "—")), size=26)
    pts = getattr(clan, "capital_points", None) or getattr(clan, "points", None)
    _stat(d, RX + 2 * col, 346, "Очки столицы" if getattr(clan, "capital_points", None) else "Очки клана",
          _num(pts) if pts else "—")

    _gold_rule(d, RX, 436, RX + RW)

    # ── Последний рейд ───────────────────────────────────────────────────────
    if raids:
        r = raids[0]
        loot = getattr(r, "total_loot", 0) or 0
        n_raid = len(list(getattr(r, "members", None) or []))
        state = "идут" if getattr(r, "state", "") == "ongoing" else "прошли"
        try:
            start = (r.start_time.time + timedelta(hours=3)).strftime("%d.%m")
        except Exception:
            start = ""
        d.text((RX, 456), f"Рейды столицы {state} {start}", font=_body(17, 600), fill=MUTED)
        d.text((RX, 480), _num(loot), font=_display(34), fill=CREAM)
        lw = d.textlength(_num(loot), font=_display(34))
        d.text((RX + lw + 12, 494), f"золота столицы, участников: {n_raid}",
               font=_body(19, 600), fill=MUTED)

    # ── Состав по ратушам ────────────────────────────────────────────────────
    ths = Counter(getattr(m, "town_hall", 0) for m in members if getattr(m, "town_hall", 0))
    if ths:
        by = 572
        d.text((RX, by - 30), "Ратуши в составе", font=_body(17, 600), fill=MUTED)
        total_m = sum(ths.values())
        order = sorted(ths, reverse=True)
        bar = Image.new("RGBA", (RW, 22), (0, 0, 0, 0))
        bd = ImageDraw.Draw(bar)
        cur = 0
        for th in order:
            w = round(ths[th] / total_m * RW)
            bd.rectangle((cur, 0, cur + w, 22), fill=TH_COLORS.get(th, (150, 150, 170)) + (255,))
            cur += w
        mask = Image.new("L", (RW, 22), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, RW - 1, 21), radius=11, fill=255)
        img.paste(bar, (RX, by), mask)
        lx, ly = RX, by + 36
        for th in order:
            label = f"ТХ{th}  {ths[th]}"
            lw = d.textlength(label, font=_body(17, 700)) + 34
            if lx + lw > RX + RW:
                lx, ly = RX, ly + 28
            d.ellipse((lx, ly + 4, lx + 12, ly + 16), fill=TH_COLORS.get(th, (150, 150, 170)))
            d.text((lx + 18, ly), label, font=_body(17, 700), fill=CREAM)
            lx += lw

    # ── Подвал ───────────────────────────────────────────────────────────────
    now = datetime.now(timezone.utc) + timedelta(hours=3)
    d.text((RX, H - 40), "warfilcoc.ru", font=_display(22), fill=GOLD)
    d.text((RX + RW, H - 36), f"обновлено {now:%d.%m.%Y, %H:%M} МСК", font=_body(16, 600),
           fill=DIM, anchor="ra")
    return img


def render_card(clan, raids=None, badge: Image.Image | None = None, style: int | None = None) -> Image.Image:
    import random
    import card_styles
    styles = [(render_site, "Сайт")] + card_styles.STYLES
    idx = (style - 1) if style and 1 <= style <= len(styles) else random.randrange(len(styles))
    img = styles[idx][0](clan, raids, badge)
    img.info["style"] = f"{idx + 1}. {styles[idx][1]}"
    return img


async def build_card(clan, raids=None, background_path: str | None = None,
                     style: int | None = None) -> io.BytesIO:
    badge = None
    if getattr(clan, "badge", None) is not None:
        badge = await _dl_image(clan.badge.large or clan.badge.url)
    img = render_card(clan, raids=raids, badge=badge, style=style)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    buf.seek(0)
    buf.name = "warfil_card.png"
    buf.style = img.info.get("style", "")
    return buf
