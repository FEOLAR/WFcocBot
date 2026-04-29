import io
import os
from PIL import Image, ImageDraw, ImageFont

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

TH_IMAGES_DIR = "th_images"

# Telegram-like dark theme colors
BG_COLOR      = (23, 33, 43)
ROW_ALT_COLOR = (26, 37, 48)
HEADER_BG     = (17, 25, 33)
SEP_COLOR     = (40, 55, 70)

ROLE_COLORS = {
    "leader":   (255, 200, 50),
    "coLeader": (180, 100, 255),
    "admin":    (80, 200, 120),
    "member":   (100, 160, 255),
}
ROLE_LABELS = {
    "leader":   "👑 Лидер",
    "coLeader": "🔱 Соруководители",
    "admin":    "🌿 Старейшины",
    "member":   "🔹 Участники",
}
ROLE_ORDER = ["leader", "coLeader", "admin", "member"]

_th_cache: dict[int, Image.Image] = {}


def _load_th(level: int, size: int) -> Image.Image | None:
    key = (level, size)
    if key in _th_cache:
        return _th_cache[key]
    path = os.path.join(TH_IMAGES_DIR, f"th{level}.png")
    if not os.path.exists(path):
        return None
    img = Image.open(path).convert("RGBA")
    img = img.resize((size, size), Image.LANCZOS)
    _th_cache[key] = img
    return img


def build_team_image(clan_name: str, member_count: int, groups: dict) -> io.BytesIO:
    IMG_W    = 420
    PAD      = 10
    TH_SIZE  = 40
    ROW_H    = 50
    ROLE_H   = 30
    HEADER_H = 56

    total_members   = sum(len(groups.get(r, [])) for r in ROLE_ORDER)
    total_role_bars = sum(1 for r in ROLE_ORDER if groups.get(r))
    total_h = HEADER_H + total_role_bars * ROLE_H + total_members * ROW_H + 4

    img  = Image.new("RGB", (IMG_W, total_h), BG_COLOR)
    draw = ImageDraw.Draw(img)

    try:
        f_header = ImageFont.truetype(FONT_BOLD,    20)
        f_sub    = ImageFont.truetype(FONT_REGULAR, 12)
        f_role   = ImageFont.truetype(FONT_BOLD,    13)
        f_name   = ImageFont.truetype(FONT_BOLD,    15)
        f_stat   = ImageFont.truetype(FONT_REGULAR, 12)
    except Exception:
        f_header = f_sub = f_role = f_name = f_stat = ImageFont.load_default()

    # ── Header ──────────────────────────────────────────────────
    draw.rectangle([0, 0, IMG_W, HEADER_H], fill=HEADER_BG)
    draw.text((PAD + 2, 10), clan_name, font=f_header, fill=(230, 230, 235))
    draw.text((PAD + 2, 36), f"👥 {member_count}/50 участников", font=f_sub, fill=(100, 130, 155))
    # Bottom border of header
    draw.rectangle([0, HEADER_H - 1, IMG_W, HEADER_H], fill=SEP_COLOR)

    y = HEADER_H

    global_idx = 0
    for role_key in ROLE_ORDER:
        members = groups.get(role_key)
        if not members:
            continue

        color = ROLE_COLORS[role_key]
        label = ROLE_LABELS[role_key]

        # ── Role separator ──────────────────────────────────────
        draw.rectangle([0, y, IMG_W, y + ROLE_H], fill=HEADER_BG)
        # left accent
        draw.rectangle([0, y, 3, y + ROLE_H], fill=color)
        draw.text((PAD + 6, y + 8), label, font=f_role, fill=color)
        draw.rectangle([0, y + ROLE_H - 1, IMG_W, y + ROLE_H], fill=SEP_COLOR)
        y += ROLE_H

        for member in members:
            th_level = member.town_hall
            donations = getattr(member, "donations", 0)

            row_bg = BG_COLOR if global_idx % 2 == 0 else ROW_ALT_COLOR
            draw.rectangle([0, y, IMG_W, y + ROW_H], fill=row_bg)

            # TH image (vertically centered)
            th_x = PAD
            th_y = y + (ROW_H - TH_SIZE) // 2
            th_img = _load_th(th_level, TH_SIZE)
            if th_img:
                img.paste(th_img, (th_x, th_y), th_img)

            # Vertical separator line
            sep_x = PAD + TH_SIZE + 10
            draw.line([(sep_x, y + 8), (sep_x, y + ROW_H - 8)], fill=SEP_COLOR, width=1)

            # Player name
            name_x = sep_x + 10
            name_y = y + (ROW_H // 2) - 12
            draw.text((name_x, name_y), member.name, font=f_name, fill=(215, 225, 235))

            # Donations on right side
            don_text = f"🏹 {donations}"
            bbox = draw.textbbox((0, 0), don_text, font=f_stat)
            don_w = bbox[2] - bbox[0]
            draw.text((IMG_W - don_w - PAD, y + (ROW_H // 2) - 8), don_text, font=f_stat, fill=(90, 120, 150))

            # Row bottom border
            draw.rectangle([0, y + ROW_H - 1, IMG_W, y + ROW_H], fill=SEP_COLOR)

            y += ROW_H
            global_idx += 1

    # Footer accent
    draw.rectangle([0, total_h - 3, IMG_W, total_h], fill=(40, 60, 80))

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf
