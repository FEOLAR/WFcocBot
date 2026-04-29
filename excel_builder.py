import io
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

GREEN  = "FF92D050"
RED    = "FFFF0000"
ORANGE = "FFFFC000"
BLUE   = "FF4472C4"
GRAY   = "FFD9D9D9"
DARK   = "FF1F3864"
WHITE  = "FFFFFFFF"
YELLOW = "FFFFFF00"


def _hdr_fill(hex_color):
    return PatternFill("solid", fgColor=hex_color)


def _cell_fill(hex_color):
    return PatternFill("solid", fgColor=hex_color)


def _thin_border():
    s = Side(style="thin")
    return Border(left=s, right=s, top=s, bottom=s)


def _header_cell(ws, row, col, value, bg=DARK, fg=WHITE, bold=True, wrap=True):
    c = ws.cell(row=row, column=col, value=value)
    c.font = Font(bold=bold, color=fg, size=10)
    c.fill = _hdr_fill(bg)
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=wrap)
    c.border = _thin_border()
    return c


def _data_cell(ws, row, col, value, bg=None, bold=False, align="center"):
    c = ws.cell(row=row, column=col, value=value)
    c.font = Font(bold=bold, size=10)
    c.alignment = Alignment(horizontal=align, vertical="center")
    c.border = _thin_border()
    if bg:
        c.fill = _cell_fill(bg)
    return c


# ─────────────────────────────────────────────────────────────────────────────
# Sheet 1: КВ (last 5 regular wars)
# Each war = 2 columns: "1-я атака" and "2-я атака" (✅/❌ per slot)
# ─────────────────────────────────────────────────────────────────────────────

def build_kv_sheet(ws, war_history: list[dict], clan_members: list[dict] = None):
    ws.title = "КВ (Клановые войны)"

    wars = war_history[:5]
    n_wars = len(wars)

    # Total columns: 2 (player, TH) + n_wars * 3 (1st atk, 2nd atk, %)
    total_cols = max(5, 2 + n_wars * 3)

    # Row 1: Title
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=total_cols)
    _header_cell(ws, 1, 1, "⚔ КЛАНОВЫЕ ВОЙНЫ — последние 5 КВ", bg=DARK)

    # Row 2: war headers
    _header_cell(ws, 2, 1, "Игрок", bg=BLUE)
    _header_cell(ws, 2, 2, "TH", bg=BLUE)
    for i, war in enumerate(wars):
        col = 3 + i * 3
        result_emoji = {"win": "🏆", "lose": "❌", "tie": "🤝"}.get(war.get("result", ""), "")
        end = war.get("end_time", "")[:10]
        opp = war.get("opponent", "—")
        opp_short = opp[:12] + "…" if len(opp) > 14 else opp
        label = f"{result_emoji} vs {opp_short}\n{end}"
        stars_label = f"⭐{war.get('our_stars', 0)}:{war.get('their_stars', 0)}"
        _header_cell(ws, 2, col,     label + f"\n{stars_label}", bg=BLUE)
        _header_cell(ws, 2, col + 1, "1-я атака", bg=BLUE)
        _header_cell(ws, 2, col + 2, "2-я атака", bg=BLUE)

    # Gather all unique players with TH level
    # Start with clan members as base (always visible), then overlay war data
    all_players: dict[str, int] = {}
    if clan_members:
        for m in clan_members:
            all_players[m["name"]] = m.get("th", 0)
    for war in wars:
        for m in war.get("members", []):
            name = m["name"]
            if name not in all_players:
                all_players[name] = m.get("th", 0)
            elif m.get("th"):
                all_players[name] = m["th"]

    row = 3
    for player_name in sorted(all_players.keys()):
        th = all_players[player_name]
        _data_cell(ws, row, 1, player_name, align="left")
        _data_cell(ws, row, 2, th if th else "—")

        for i, war in enumerate(wars):
            col = 3 + i * 3
            members = {m["name"]: m for m in war.get("members", [])}
            apm = war.get("attacks_per_member", 2)

            if player_name in members:
                used = members[player_name].get("attacks_used", 0)
                total = members[player_name].get("attacks_max", apm)

                # 1st attack slot
                used1 = used >= 1
                bg1 = GREEN if used1 else RED
                _data_cell(ws, row, col + 1, "✅" if used1 else "❌", bg=bg1)

                # 2nd attack slot (only relevant if 2 attacks per member)
                if total >= 2:
                    used2 = used >= 2
                    bg2 = GREEN if used2 else RED
                    _data_cell(ws, row, col + 2, "✅" if used2 else "❌", bg=bg2)
                else:
                    _data_cell(ws, row, col + 2, "—", bg=GRAY)

                # Summary column (war name column) — show used/total %
                pct = int(used / total * 100) if total else 0
                bg_pct = GREEN if pct == 100 else (RED if pct == 0 else ORANGE)
                _data_cell(ws, row, col, f"{used}/{total} ({pct}%)", bg=bg_pct)
            else:
                _data_cell(ws, row, col,     "н/д", bg=GRAY)
                _data_cell(ws, row, col + 1, "—",   bg=GRAY)
                _data_cell(ws, row, col + 2, "—",   bg=GRAY)
        row += 1

    # Column widths
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 5
    for i in range(n_wars):
        ws.column_dimensions[get_column_letter(3 + i * 3)].width = 22
        ws.column_dimensions[get_column_letter(4 + i * 3)].width = 10
        ws.column_dimensions[get_column_letter(5 + i * 3)].width = 10

    ws.row_dimensions[2].height = 50
    ws.freeze_panes = "A3"


# ─────────────────────────────────────────────────────────────────────────────
# Sheet 2: ЛВК (current CWL season rounds)
# ─────────────────────────────────────────────────────────────────────────────

def build_cwl_sheet(ws, cwl_history: dict, sheet_index: int = 0, clan_members: list = None):
    season = cwl_history.get("season") or "—"
    season_label = "Текущий" if sheet_index == 0 else "Прошлый"
    ws.title = f"ЛВК {season}"[:31]

    rounds = cwl_history.get("rounds", [])
    n_rounds = len(rounds)

    total_cols = max(3, 2 + n_rounds)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=total_cols)
    _header_cell(ws, 1, 1, f"🏆 ЛВК — {season_label} сезон {season}", bg=DARK)

    _header_cell(ws, 2, 1, "Игрок", bg=BLUE)
    _header_cell(ws, 2, 2, "Атак", bg=BLUE)
    for i, rnd in enumerate(rounds):
        col = 3 + i
        opp = rnd.get("opponent", "—")
        opp_short = opp[:12] + "…" if len(opp) > 14 else opp
        our_s = rnd.get("our_stars", 0)
        their_s = rnd.get("their_stars", 0)
        state = rnd.get("state", "")
        status = "" if state in ("warEnded", "war_ended") else " ⏳"
        rnd_label = f"Раунд {rnd['round']}\nvs {opp_short}\n⭐{our_s}:{their_s}{status}"
        _header_cell(ws, 2, col, rnd_label, bg=BLUE)

    # Gather all players: start from clan roster, then overlay round data
    all_players: dict[str, list] = {}
    if clan_members:
        for m in clan_members:
            all_players[m["name"]] = [None] * n_rounds
    for rnd in rounds:
        for m in rnd.get("members", []):
            name = m["name"]
            if name not in all_players:
                all_players[name] = [None] * n_rounds

    for i, rnd in enumerate(rounds):
        for m in rnd.get("members", []):
            name = m["name"]
            if name in all_players:
                all_players[name][i] = m.get("attacked", False)

    row = 3
    for player_name in sorted(all_players.keys()):
        attacks = all_players[player_name]
        total_attacked = sum(1 for a in attacks if a is True)
        total_rounds_known = sum(1 for a in attacks if a is not None)
        _data_cell(ws, row, 1, player_name, align="left")
        if n_rounds == 0 or total_rounds_known == 0:
            _data_cell(ws, row, 2, "н/д", bg=GRAY)
        else:
            pct = int(total_attacked / total_rounds_known * 100)
            _data_cell(ws, row, 2,
                       f"{total_attacked}/{n_rounds} ({pct}%)",
                       bg=GREEN if pct == 100 else (RED if pct == 0 else ORANGE),
                       bold=True)
        for i in range(n_rounds):
            col = 3 + i
            attacked = attacks[i]
            if attacked is None:
                _data_cell(ws, row, col, "н/д", bg=GRAY)
            else:
                _data_cell(ws, row, col, "✅" if attacked else "❌",
                           bg=GREEN if attacked else RED)
        row += 1

    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 14
    for i in range(n_rounds):
        ws.column_dimensions[get_column_letter(3 + i)].width = 18
    ws.row_dimensions[2].height = 50
    ws.freeze_panes = "A3"


# ─────────────────────────────────────────────────────────────────────────────
# Sheet 3: Рейды столицы (last 2 raids, live from API)
# ─────────────────────────────────────────────────────────────────────────────

def build_raids_sheet(ws, raids: list):
    ws.title = "Рейды столицы"

    n = len(raids)

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=2 + n * 2)
    _header_cell(ws, 1, 1, "🏛 РЕЙДЫ СТОЛИЦЫ — последние рейды", bg=DARK)

    _header_cell(ws, 2, 1, "Игрок", bg=BLUE)
    _header_cell(ws, 2, 2, "Лимит", bg=BLUE)

    for i, raid in enumerate(raids):
        col = 3 + i * 2
        try:
            start = raid.start_time.time.strftime("%d.%m.%Y")
        except Exception:
            start = "—"
        try:
            loot = f"{raid.total_loot:,}".replace(",", " ")
        except Exception:
            loot = "—"
        label = f"Рейд {i + 1}\n{start}\n💰 {loot}"
        _header_cell(ws, 2, col,     label, bg=BLUE)
        _header_cell(ws, 2, col + 1, "Золото", bg=BLUE)

    # Convert members to lists once (coc.py may return iterators)
    raid_members: list[list] = []
    for raid in raids:
        try:
            raid_members.append(list(raid.members or []))
        except Exception:
            raid_members.append([])

    # Collect all unique player names
    all_members: dict[str, dict] = {}
    for members in raid_members:
        for m in members:
            if m.name not in all_members:
                all_members[m.name] = {}

    if not all_members:
        ws.cell(row=3, column=1, value="Нет данных по участникам")
        return

    row = 3
    for player_name in sorted(all_members.keys()):
        _data_cell(ws, row, 1, player_name, align="left")
        # limit column — take from first raid that has this member
        limit_shown = "—"
        for members in raid_members:
            member_map = {m.name: m for m in members}
            if player_name in member_map:
                m0 = member_map[player_name]
                limit_val = (getattr(m0, "attack_limit", 0) or 0) + (getattr(m0, "bonus_attack_limit", 0) or 0)
                limit_shown = str(limit_val)
                break
        _data_cell(ws, row, 2, limit_shown)

        for i, members in enumerate(raid_members):
            col = 3 + i * 2
            member_map = {m.name: m for m in members}

            if player_name in member_map:
                m = member_map[player_name]
                used  = getattr(m, "attack_count", 0) or 0
                limit = (getattr(m, "attack_limit", 0) or 0) + (getattr(m, "bonus_attack_limit", 0) or 0)
                gold  = getattr(m, "capital_resources_looted", 0) or 0
                pct   = int(used / limit * 100) if limit else 0
                bg    = GREEN if pct == 100 else (RED if pct == 0 else ORANGE)
                _data_cell(ws, row, col,     f"{used}/{limit} ({pct}%)", bg=bg)
                _data_cell(ws, row, col + 1, f"{gold:,}".replace(",", " "))
            else:
                _data_cell(ws, row, col,     "—", bg=GRAY)
                _data_cell(ws, row, col + 1, "—")
        row += 1

    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 8
    for i in range(n):
        ws.column_dimensions[get_column_letter(3 + i * 2)].width = 16
        ws.column_dimensions[get_column_letter(4 + i * 2)].width = 14
    ws.row_dimensions[2].height = 50
    ws.freeze_panes = "A3"


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# cwl_seasons: list of {"season": str, "rounds": [...]} – newest first
# ─────────────────────────────────────────────────────────────────────────────

def build_excel(war_history: list[dict], cwl_seasons: list[dict], raids: list,
                clan_members: list[dict] = None) -> io.BytesIO:
    wb = openpyxl.Workbook()

    ws_kv = wb.active
    build_kv_sheet(ws_kv, war_history, clan_members=clan_members)

    # One CWL sheet per season
    if cwl_seasons:
        for i, season_data in enumerate(cwl_seasons):
            ws_cwl = wb.create_sheet()
            build_cwl_sheet(ws_cwl, season_data, sheet_index=i, clan_members=clan_members)
    else:
        ws_cwl = wb.create_sheet()
        build_cwl_sheet(ws_cwl, {"season": None, "rounds": []}, sheet_index=0, clan_members=clan_members)

    ws_raids = wb.create_sheet()
    build_raids_sheet(ws_raids, raids)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
