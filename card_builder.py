import io
import asyncio
import httpx
from datetime import datetime
from collections import Counter
from PIL import Image, ImageDraw, ImageFont, ImageFilter

FONT_PATH = "fonts/Nunito-Regular.ttf"

W, H = 1280, 720

# Palette
C_WHITE   = (255, 255, 255, 255)
C_GRAY    = (180, 180, 180, 255)
C_DARK    = (12,  16,  30,  255)
C_GOLD    = (255, 205,  55, 255)
C_GREEN   = ( 80, 210, 110, 255)
C_RED     = (225,  75,  75, 255)
C_ORANGE  = (245, 160,  55, 255)
C_PURPLE  = (175, 100, 240, 255)
C_PANEL   = (  0,   0,  15,  80)

TH_COLORS = {
    18: (220, 170, 40),
    17: (180, 100, 220),
    16: (80,  160, 230),
    15: (80,  200, 100),
    14: (240, 155,  50),
    13: (200,  80,  80),
    12: (140, 140, 140),
    11: (100, 100, 100),
    10: ( 80,  80,  80),
}

LEAGUE_RU = {
    "Legend League":          "Легендарная лига",
    "Titan League I":         "Лига Титанов I",
    "Titan League II":        "Лига Титанов II",
    "Titan League III":       "Лига Титанов III",
    "Champion League I":      "Чемпионская лига I",
    "Champion League II":     "Чемпионская лига II",
    "Champion League III":    "Чемпионская лига III",
    "Master League I":        "Мастер лига I",
    "Master League II":       "Мастер лига II",
    "Master League III":      "Мастер лига III",
    "Crystal League I":       "Хрустальная лига I",
    "Crystal League II":      "Хрустальная лига II",
    "Crystal League III":     "Хрустальная лига III",
    "Gold League I":          "Золотая лига I",
    "Gold League II":         "Золотая лига II",
    "Gold League III":        "Золотая лига III",
    "Silver League I":        "Серебряная лига I",
    "Silver League II":       "Серебряная лига II",
    "Silver League III":      "Серебряная лига III",
    "Bronze League I":        "Бронзовая лига I",
    "Bronze League II":       "Бронзовая лига II",
    "Bronze League III":      "Бронзовая лига III",
    "Unranked":               "Без ранга",
}

FREQ_RU = {
    "always":               "Всегда",
    "moreThanOncePerWeek":  "Часто",
    "oncePerWeek":          "Раз в неделю",
    "lessThanOncePerWeek":  "Редко",
    "never":                "Никогда",
    "unknown":              "Неизвестно",
}

TYPE_RU = {
    "open":         "открытый",
    "inviteOnly":   "по приглашению",
    "closed":       "закрытый",
}


def _ru_league(name: str) -> str:
    return LEAGUE_RU.get(str(name), str(name))


def _font(size: int, weight: int = 400) -> ImageFont.FreeTypeFont:
    try:
        font = ImageFont.truetype(FONT_PATH, size)
        font.set_variation_by_axes([weight])
        return font
    except Exception:
        return ImageFont.truetype(FONT_PATH, size)


def _panel(draw: ImageDraw.ImageDraw, x1, y1, x2, y2, color=C_PANEL, r=14):
    draw.rounded_rectangle([x1, y1, x2, y2], radius=r, fill=color)


def _bar(draw, x, y, w, h, fill_ratio, bg=(50, 50, 70, 200), fg=None):
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=bg)
    if fill_ratio > 0 and fg:
        fw = max(h, int(w * fill_ratio))
        draw.rounded_rectangle([x, y, x + fw, y + h], radius=h // 2, fill=fg)


def _txt(draw, x, y, text, size=16, weight=400, color=C_WHITE, anchor="la"):
    draw.text((x, y), str(text), font=_font(size, weight), fill=color, anchor=anchor)


async def _dl_image(url: str, size: tuple | None = None) -> Image.Image | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url)
            r.raise_for_status()
            img = Image.open(io.BytesIO(r.content)).convert("RGBA")
            if size:
                img = img.resize(size, Image.LANCZOS)
            return img
    except Exception:
        return None


async def build_card(clan, raids=None, background_path: str | None = None) -> io.BytesIO:
    # ── Background ──────────────────────────────────────────────────────────
    if background_path:
        try:
            bg = Image.open(background_path).convert("RGBA").resize((W, H), Image.LANCZOS)
        except Exception:
            bg = Image.new("RGBA", (W, H), C_DARK)
    else:
        bg = Image.new("RGBA", (W, H), C_DARK)

    overlay = Image.new("RGBA", (W, H), (0, 5, 20, 130))
    bg = Image.alpha_composite(bg, overlay)

    draw = ImageDraw.Draw(bg, "RGBA")

    # ────────────────────────────────────────────────────────────────────────
    # LEFT COLUMN  (x: 15 → 405)
    # ────────────────────────────────────────────────────────────────────────
    LX = 15

    # ── Clan badge ──────────────────────────────────────────────────────────
    BADGE_SZ = 100
    badge_img = None
    if clan.badge and clan.badge.large:
        badge_img = await _dl_image(clan.badge.large, size=(BADGE_SZ, BADGE_SZ))
    if badge_img:
        bg.paste(badge_img, (LX, 15), badge_img)

    # Level circle
    lv_x, lv_y = LX + BADGE_SZ // 2, 15 + BADGE_SZ - 14
    draw.ellipse([lv_x - 17, lv_y - 15, lv_x + 17, lv_y + 15], fill=(50, 10, 90, 240))
    _txt(draw, lv_x, lv_y, clan.level, size=14, weight=700, color=C_GOLD, anchor="mm")

    # Clan name + tag + type
    nx = LX + BADGE_SZ + 14
    _txt(draw, nx, 18,  clan.name, size=30, weight=800, color=C_WHITE)
    clan_type_ru = TYPE_RU.get(str(clan.type or "open"), str(clan.type or "open"))
    _txt(draw, nx, 56,  f"#{clan.tag.lstrip('#')}  ·  {clan_type_ru}", size=14, color=C_GRAY)
    desc = clan.description or ""
    desc_short = desc[:68] + ("…" if len(desc) > 68 else "")
    _txt(draw, nx, 78,  desc_short, size=11, color=(155, 155, 155, 210))

    # ── Members + donations ──────────────────────────────────────────────────
    _panel(draw, LX, 130, 405, 192)
    _txt(draw, LX + 14, 140, "Участники",  size=12, color=C_GRAY)
    _txt(draw, LX + 14, 158, str(clan.member_count), size=26, weight=700, color=C_WHITE)
    _txt(draw, LX + 95, 140, "Пожертвования (отдано / получено)", size=12, color=C_GRAY)
    members_list = list(clan.members or [])
    total_given    = sum(m.donations for m in members_list)
    total_received = sum(m.received  for m in members_list)
    _txt(draw, LX + 95, 158,
         f"{total_given:,} / {total_received:,}".replace(",", " "),
         size=20, weight=700, color=C_GOLD)

    # ── TH distribution ─────────────────────────────────────────────────────
    ths = Counter(m.town_hall for m in members_list)
    _panel(draw, LX, 202, 405, 710)
    _txt(draw, LX + 14, 210, "Состав по ратушам", size=13, weight=700, color=C_GOLD)

    y_th = 238
    bar_w = 210
    max_count = max(ths.values()) if ths else 1
    total_m = clan.member_count or 1

    for th in sorted(ths.keys(), reverse=True):
        count = ths[th]
        pct   = count / total_m * 100
        ratio = count / max_count
        fg    = TH_COLORS.get(th, (120, 120, 120)) + (220,)

        _txt(draw, LX + 14, y_th, f"TH{th}", size=13, weight=700, color=C_WHITE)
        _bar(draw, LX + 72, y_th + 3, bar_w, 14, ratio, fg=fg)
        _txt(draw, LX + 296, y_th, f"{count} / {pct:.0f}%", size=12, color=C_GRAY)
        y_th += 34

    # ────────────────────────────────────────────────────────────────────────
    # MIDDLE COLUMN  (x: 425 → 710)
    # ────────────────────────────────────────────────────────────────────────
    MX = 425

    # ── War Frequency ───────────────────────────────────────────────────────
    _panel(draw, MX, 15, 710, 94)
    _txt(draw, MX + 14, 24, "Частота войн", size=12, color=C_GRAY)
    freq_raw = str(clan.war_frequency or "always")
    freq_ru  = FREQ_RU.get(freq_raw, freq_raw)
    fc = C_GREEN if freq_raw == "always" else C_ORANGE if freq_raw == "moreThanOncePerWeek" else C_RED
    _txt(draw, MX + 14, 46, freq_ru, size=20, weight=700, color=fc)

    # ── War Stats ───────────────────────────────────────────────────────────
    wins   = clan.war_wins   or 0
    losses = clan.war_losses or 0
    ties   = clan.war_ties   or 0
    total_wars = wins + losses + ties

    _panel(draw, MX, 104, 710, 360)
    _txt(draw, MX + 14, 112, "Статистика войн", size=12, color=C_GRAY)

    _txt(draw, MX + 14,  136, str(wins),   size=46, weight=800, color=C_GREEN)
    _txt(draw, MX + 126, 136, str(losses), size=46, weight=800, color=C_RED)
    _txt(draw, MX + 14,  186, "побед",     size=12, color=C_GRAY)
    _txt(draw, MX + 126, 186, "поражений", size=12, color=C_GRAY)
    if ties:
        _txt(draw, MX + 228, 148, str(ties), size=32, weight=700, color=C_ORANGE)
        _txt(draw, MX + 228, 186, "ничьих",  size=12, color=C_GRAY)

    if total_wars:
        wr = wins / total_wars
        _txt(draw, MX + 14, 208, f"Винрейт: {wr*100:.1f}%", size=13, color=C_GRAY)
        _bar(draw, MX + 14, 228, 268, 14, wr,
             bg=(180, 60, 60, 160), fg=(80, 200, 100, 220))

    # Dots
    dot_x, dot_y, remaining = MX + 14, 258, 10
    for color, cnt in [(C_GREEN, wins), (C_ORANGE, ties), (C_RED, losses)]:
        for _ in range(min(cnt, remaining)):
            draw.ellipse([dot_x, dot_y, dot_x + 14, dot_y + 14], fill=color)
            dot_x += 18
            remaining -= 1
            if remaining <= 0:
                break
        if remaining <= 0:
            break

    # ── Лига ЛВК ────────────────────────────────────────────────────────────
    _panel(draw, MX, 370, 710, 470)
    _txt(draw, MX + 14, 380, "Лига клановых войн", size=12, color=C_GRAY)
    cwl_name = _ru_league(clan.war_league or "—")
    _txt(draw, MX + 14, 402, cwl_name, size=18, weight=700, color=C_PURPLE)

    # ── Столичная лига ──────────────────────────────────────────────────────
    cap_lg_raw = str(getattr(clan, "capital_league", None) or "—")
    cap_lg     = _ru_league(cap_lg_raw)

    _panel(draw, MX, 480, 710, 580)
    _txt(draw, MX + 14, 490, "Столичная лига", size=12, color=C_GRAY)
    _txt(draw, MX + 14, 512, cap_lg, size=18, weight=700, color=C_GOLD)

    # ── Итого войн (внизу средней колонки) ──────────────────────────────────
    _panel(draw, MX, 590, 710, 710)
    _txt(draw, MX + 14, 600, "Войны — итого", size=12, color=C_GRAY)
    _txt(draw, MX + 14, 620,
         f"Победы: {wins}   Поражения: {losses}",
         size=13, weight=700, color=C_WHITE)
    if total_wars:
        _txt(draw, MX + 14, 644,
             f"Ничьи: {ties}   Всего: {total_wars}   Винрейт: {wins/total_wars*100:.1f}%",
             size=12, color=C_GRAY)

    # ────────────────────────────────────────────────────────────────────────
    # RIGHT COLUMN  (x: 730 → 1265)
    # ────────────────────────────────────────────────────────────────────────
    RX = 730

    # ── Рейдовая лига + последний рейд ──────────────────────────────────────
    _panel(draw, RX, 15, RX + 535, 200)
    _txt(draw, RX + 14, 24, "Рейдовая лига", size=12, color=C_GRAY)
    _txt(draw, RX + 14, 46, cap_lg, size=20, weight=700, color=C_GOLD)

    if raids:
        loot = getattr(raids[0], "total_loot", 0) or 0
        try:
            start = raids[0].start_time.time.strftime("%d.%m.%Y")
        except Exception:
            start = "—"
        r0_members = list(getattr(raids[0], "members", None) or [])
        _txt(draw, RX + 14, 90,  "Последний рейд:", size=12, color=C_GRAY)
        _txt(draw, RX + 14, 110, f"{loot:,}".replace(",", " ") + " зол.",
             size=24, weight=700, color=C_WHITE)
        _txt(draw, RX + 14, 144, f"{start}  ·  {len(r0_members)} участников",
             size=13, color=C_GRAY)

    # ── Сайт клана ──────────────────────────────────────────────────────────
    _panel(draw, RX, 210, RX + 535, 420)
    _txt(draw, RX + 14, 222, "Сайт клана", size=12, color=C_GRAY)
    _txt(draw, RX + 14, 246, "www.warfilcoc.ru", size=22, weight=800, color=C_PURPLE)
    _txt(draw, RX + 14, 284, "Вся статистика, хроники и информация о клане —", size=13, color=C_WHITE)
    _txt(draw, RX + 14, 308, "на официальном сайте Warfil.", size=13, color=C_WHITE)
    _txt(draw, RX + 14, 344, "Вступить в клан:", size=12, color=C_GRAY)
    _txt(draw, RX + 14, 366, f"#{clan.tag.lstrip('#')}  ·  {TYPE_RU.get(str(clan.type or 'open'), str(clan.type or 'open'))}", size=14, weight=700, color=C_GOLD)

    # ── Обновлено (без панели) ───────────────────────────────────────────────
    _txt(draw, RX + 14, 432,
         f"warfilcoc.ru  ·  обновлено {datetime.now().strftime('%d.%m.%Y %H:%M')}",
         size=12, color=(130, 130, 130, 200))

    # ── Convert & return ────────────────────────────────────────────────────
    final = bg.convert("RGB")
    buf = io.BytesIO()
    final.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf
