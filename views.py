"""
Тексты сообщений о войнах, ЛВК и рейдах.
Используются и командами (/kv, /cwl, /raid), и автоуведомлениями.
"""
import storage
from coc_api import _norm_name, is_our_tag
from fmt import (e, esc, title, line, quote, player, bar, num, pct,
                 score, local, until, plural)


def _tg_map() -> dict:
    try:
        return storage.get_tg_username_map()
    except Exception:
        return {}


def sides(war):
    """(наша сторона, соперник) — независимо от того, как их вернул API."""
    if war.clan is not None and is_our_tag(war.clan.tag):
        return war.clan, war.opponent
    return war.opponent, war.clan


def _ts(t):
    return t.time if t is not None else None


def _used(m) -> int:
    return len(m.attacks) if getattr(m, "attacks", None) else 0


def _members_sorted(side):
    return sorted(side.members or [], key=lambda m: getattr(m, "map_position", 0) or 0)


def result_of(our, opp) -> str:
    if our.stars != opp.stars:
        return "win" if our.stars > opp.stars else "lose"
    od, td = our.destruction or 0, opp.destruction or 0
    if abs(od - td) > 1e-9:
        return "win" if od > td else "lose"
    return "tie"


RESULT_TITLE = {"win": "Победа", "lose": "Поражение", "tie": "Ничья"}


def pending_blocks(side, per_member: int, mention: bool = True) -> list[str]:
    """Блоки «без атак» / «осталась одна» для нашей стороны."""
    tg = _tg_map() if mention else {}
    zero, partial = [], []
    for m in _members_sorted(side):
        u = _used(m)
        if u >= per_member:
            continue
        row = player(m.name, getattr(m, "town_hall", None), tg.get(_norm_name(m.name)))
        (zero if u == 0 else partial).append(row)
    out = []
    if zero:
        label = "Не атаковали" if per_member == 1 else "Без атак"
        out.append(f"\n{e('miss')} <b>{label}: {len(zero)}</b>\n{quote(zero)}")
    if partial:
        out.append(f"\n{e('half')} <b>Осталась одна атака: {len(partial)}</b>\n{quote(partial)}")
    return out


def attacks_line(side, per_member: int, team_size: int) -> str:
    total = (team_size or len(side.members or [])) * per_member
    used = sum(min(_used(m), per_member) for m in (side.members or []))
    return f"{e('attack')} Атаки  {bar(used, total)}  <b>{used}</b> из {total}"


def best_attackers(side, limit: int = 3) -> list[str]:
    rows = []
    for m in side.members or []:
        atks = m.attacks or []
        if not atks:
            continue
        stars = sum(a.stars for a in atks)
        dest = sum(a.destruction for a in atks) / len(atks)
        rows.append((stars, dest, m))
    rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
    out = []
    for stars, dest, m in rows[:limit]:
        out.append(f"{player(m.name, getattr(m, 'town_hall', None))}  {e('star')} {stars}  {pct(dest)}")
    return out


# ══════════════════════════════════════════════════════════════════════════════
#  Клановая война
# ══════════════════════════════════════════════════════════════════════════════

def war_kind(war) -> str:
    return "ЛВК" if getattr(war, "is_cwl", False) else "война"


def war_status(war) -> tuple[str, bool]:
    """Текст для /kv и периодической сводки. (текст, обновлять ли дальше)"""
    if war is None or war.state == "notInWar":
        return (title("clan", "Сейчас войны нет") + "\n\n"
                "Как только начнётся поиск и найдётся соперник, бот напишет в беседу."), False

    our, opp = sides(war)
    per = war.attacks_per_member or (1 if war.is_cwl else 2)
    cwl = getattr(war, "is_cwl", False)

    if war.state == "preparation":
        lines = [
            title("prep", "Подготовка к раунду ЛВК" if cwl else "Подготовка к войне"),
            score(our.name, 0, 0, opp.name),
            "",
            line("team", f"{war.team_size} на {war.team_size}"),
            line("time", f"Бой через <b>{until(_ts(war.start_time))}</b>, {local(_ts(war.start_time))}"),
        ]
        return "\n".join(lines), False

    live = war.state == "inWar"
    head = ("Раунд ЛВК идёт" if cwl else "Война идёт") if live else (
        "Раунд ЛВК завершён" if cwl else "Война завершена")
    lines = [
        title("cwl" if cwl else "war", head),
        score(our.name, our.stars, opp.stars, opp.name),
        "",
        line("boom", f"Разрушение {pct(our.destruction)} : {pct(opp.destruction)}"),
    ]
    if live:
        lines.append(line("time", f"До конца <b>{until(_ts(war.end_time))}</b>, {local(_ts(war.end_time))}"))
    lines.append(attacks_line(our, per, war.team_size))

    blocks = pending_blocks(our, per)
    if live and not blocks:
        lines.append("\n" + line("done", "<b>Все атаки сделаны.</b> Красавцы."))
    elif live:
        lines += blocks
    return "\n".join(lines), live


def war_found(war) -> str:
    our, opp = sides(war)
    return "\n".join([
        title("prep", "Соперник найден"),
        f"<b>{esc(our.name)}</b> против <b>{esc(opp.name)}</b>",
        "",
        line("team", f"{war.team_size} на {war.team_size}, по {war.attacks_per_member or 2} "
                     f"{plural(war.attacks_per_member or 2, 'атаке', 'атаки', 'атак')}"),
        line("time", f"Бой начнётся {local(_ts(war.start_time))}, через {until(_ts(war.start_time))}"),
        "",
        "Пока идёт подготовка: проверьте базу для войны и заполните войска в замке клана.",
    ])


def war_started(war) -> str:
    our, opp = sides(war)
    per = war.attacks_per_member or 2
    return "\n".join([
        title("war", "Война началась"),
        f"<b>{esc(our.name)}</b> против <b>{esc(opp.name)}</b>",
        "",
        line("team", f"{war.team_size} на {war.team_size}, у каждого {per} {plural(per, 'атака', 'атаки', 'атак')}"),
        line("time", f"Бой идёт до {local(_ts(war.end_time))}"),
        "",
        "Первая атака — по своему номеру. Второй добиваем то, что недобито. "
        "Перед атакой загляните в разведку.",
    ])


def war_reminder(war) -> str | None:
    our, _ = sides(war)
    per = war.attacks_per_member or 2
    blocks = pending_blocks(our, per)
    if not blocks:
        return None
    total = war.team_size * per
    used = sum(min(_used(m), per) for m in our.members or [])
    lines = [
        title("alarm", f"До конца войны {until(_ts(war.end_time))}"),
        f"Сделано {used} из {total} атак. Кого видите в списке — заходите и бьём.",
    ] + blocks
    return "\n".join(lines)


def war_ended(war) -> str:
    our, opp = sides(war)
    per = war.attacks_per_member or 2
    res = result_of(our, opp)
    lines = [
        title(res, RESULT_TITLE[res]),
        score(our.name, our.stars, opp.stars, opp.name),
        "",
        line("boom", f"Разрушение {pct(our.destruction)} : {pct(opp.destruction)}"),
        attacks_line(our, per, war.team_size),
    ]
    best = best_attackers(our)
    if best:
        lines.append(f"\n{e('fire')} <b>Лучшие атаки</b>\n{quote(best)}")
    tg = _tg_map()
    missed = []
    for m in _members_sorted(our):
        u = _used(m)
        if u < per:
            extra = f"({u} из {per})" if u else ""
            missed.append(player(m.name, getattr(m, "town_hall", None), tg.get(_norm_name(m.name)), extra))
    if missed:
        lines.append(f"\n{e('miss')} <b>Не добили атаки: {len(missed)}</b>\n{quote(missed)}")
        lines.append("Пропуск без предупреждения учитывается при наборе на следующую войну.")
    else:
        lines.append("\n" + line("done", "Все атаки использованы. Спасибо, бойцы."))
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
#  Лига войн кланов
# ══════════════════════════════════════════════════════════════════════════════

def cwl_started(war, round_num: int) -> str:
    our, opp = sides(war)
    tg = _tg_map()
    roster = [player(m.name, getattr(m, "town_hall", None), tg.get(_norm_name(m.name)))
              for m in _members_sorted(our)]
    lines = [
        title("cwl", f"ЛВК: раунд {round_num} начался"),
        f"<b>{esc(our.name)}</b> против <b>{esc(opp.name)}</b>",
        "",
        line("team", f"{war.team_size} на {war.team_size}, у каждого одна атака"),
        line("time", f"Раунд идёт до {local(_ts(war.end_time))}"),
    ]
    if roster:
        lines.append(f"\n{e('attack')} <b>В составе раунда</b>\n{quote(roster)}")
    lines.append("\nАтака одна — без разведки не заходим.")
    return "\n".join(lines)


def cwl_reminder(war, round_num: int) -> str | None:
    our, _ = sides(war)
    blocks = pending_blocks(our, 1)
    if not blocks:
        return None
    return "\n".join([
        title("alarm", f"ЛВК, раунд {round_num}: осталось {until(_ts(war.end_time))}"),
        "Каждая звезда идёт в медали всего клана.",
    ] + blocks)


def cwl_ended(war, round_num: int) -> str:
    our, opp = sides(war)
    res = result_of(our, opp)
    lines = [
        title(res, f"ЛВК, раунд {round_num}: {RESULT_TITLE[res].lower()}"),
        score(our.name, our.stars, opp.stars, opp.name),
        "",
        line("boom", f"Разрушение {pct(our.destruction)} : {pct(opp.destruction)}"),
        attacks_line(our, 1, war.team_size),
    ]
    best = best_attackers(our)
    if best:
        lines.append(f"\n{e('fire')} <b>Лучшие атаки</b>\n{quote(best)}")
    tg = _tg_map()
    missed = [player(m.name, getattr(m, "town_hall", None), tg.get(_norm_name(m.name)))
              for m in _members_sorted(our) if _used(m) == 0]
    if missed:
        lines.append(f"\n{e('miss')} <b>Не атаковали: {len(missed)}</b>\n{quote(missed)}")
    return "\n".join(lines)


def cwl_status(season: str, rounds: list[tuple[int, object]]) -> tuple[str, bool]:
    """rounds — список (номер раунда, война) нашего клана в сезоне."""
    if not rounds:
        return (title("cwl", "Лига войн кланов") + "\n\n"
                "Сезон стартовал, идёт подготовка к первому раунду."), False

    live = next(((n, w) for n, w in rounds if w.state == "inWar"), None)
    lines = [title("cwl", f"Лига войн кланов, сезон {esc(season)}")]

    if live:
        n, w = live
        our, opp = sides(w)
        lines += [
            "",
            f"<b>Раунд {n} идёт</b>",
            score(our.name, our.stars, opp.stars, opp.name),
            line("time", f"До конца <b>{until(_ts(w.end_time))}</b>, {local(_ts(w.end_time))}"),
            attacks_line(our, 1, w.team_size),
        ]
        lines += pending_blocks(our, 1)

    done = [(n, w) for n, w in rounds if w.state == "warEnded"]
    if done:
        total = sum(sides(w)[0].stars for _, w in done)
        rows = []
        for n, w in done:
            our, opp = sides(w)
            res = result_of(our, opp)
            rows.append(f"{n}. {e(res)} {our.stars} : {opp.stars}  {esc(opp.name)}")
        lines.append(f"\n{e('star')} <b>Сыграно раундов: {len(done)}, звёзд: {total}</b>\n{quote(rows)}")

    prep = next(((n, w) for n, w in rounds if w.state == "preparation"), None)
    if prep:
        n, w = prep
        _, opp = sides(w)
        lines.append("\n" + line("prep", f"Раунд {n} против <b>{esc(opp.name)}</b> начнётся "
                                        f"{local(_ts(w.start_time))}"))
    return "\n".join(lines), bool(live)


# ══════════════════════════════════════════════════════════════════════════════
#  Рейды столицы
# ══════════════════════════════════════════════════════════════════════════════

def _raid_attacks(m) -> tuple[int, int]:
    used = getattr(m, "attack_count", 0) or 0
    limit = (getattr(m, "attack_limit", 0) or 5) + (getattr(m, "bonus_attack_limit", 0) or 0)
    return used, limit


def _raid_top(raid, limit: int | None = None) -> list[str]:
    members = sorted(raid.members or [], key=lambda m: m.capital_resources_looted or 0, reverse=True)
    if limit:
        members = members[:limit]
    rows = []
    for i, m in enumerate(members, 1):
        used, lim = _raid_attacks(m)
        rows.append(f"{i}. {player(m.name)}  {e('gold')} {num(m.capital_resources_looted)}  "
                    f"{e('attack')} {used}/{lim}")
    return rows


def raid_started(raid) -> str:
    return "\n".join([
        title("capital", "Рейды столицы начались"),
        line("time", f"До {local(_ts(raid.end_time))}, осталось {until(_ts(raid.end_time))}"),
        "",
        "У каждого 5 атак. Добили район последним ударом — получите шестую, бонусную.",
        "Сначала добиваем начатые районы, новые не открываем.",
    ])


def raid_status(raid) -> tuple[str, bool]:
    if raid is None:
        return title("capital", "Рейдов пока не было") + "\n\nДанных о столице ещё нет.", False
    if raid.state != "ongoing":
        text = raid_summary(raid, heading="Последние рейды")
        return text + "\n\n" + line("date", "Следующие рейды — в пятницу, 10:00 МСК."), False
    lines = [
        title("capital", "Рейды столицы идут"),
        line("time", f"До конца <b>{until(_ts(raid.end_time))}</b>, {local(_ts(raid.end_time))}"),
        line("gold", f"Добыто <b>{num(raid.total_loot)}</b> золота столицы"),
        line("district", f"Районов разрушено: <b>{raid.destroyed_district_count or 0}</b>, "
                         f"атак: <b>{raid.attack_count or 0}</b>"),
    ]
    top = _raid_top(raid, 10)
    if top:
        lines.append(f"\n{e('fire')} <b>Больше всех добыли</b>\n{quote(top)}")
    return "\n".join(lines), True


def raid_left_blocks(raid, clan_members) -> list[str]:
    tg = _tg_map()
    left = []
    for m in raid.members or []:
        used, lim = _raid_attacks(m)
        if used < lim:
            left.append(player(m.name, tg=tg.get(_norm_name(m.name)), extra=f"— осталось {lim - used}"))
    raided = {_norm_name(m.name) for m in raid.members or []}
    idle = [player(m.name, getattr(m, "town_hall", None), tg.get(_norm_name(m.name)))
            for m in clan_members or [] if _norm_name(m.name) not in raided]
    out = []
    if idle:
        out.append(f"\n{e('miss')} <b>Ещё не заходили: {len(idle)}</b>\n{quote(idle)}")
    if left:
        out.append(f"\n{e('half')} <b>Есть атаки: {len(left)}</b>\n{quote(left)}")
    return out


def raid_reminder(raid, clan_members) -> str | None:
    blocks = raid_left_blocks(raid, clan_members)
    if not blocks:
        return None
    return "\n".join([
        title("alarm", f"До конца рейдов {until(_ts(raid.end_time))}"),
        "Неиспользованные атаки сгорают — а с ними и медали рейдов.",
    ] + blocks)


def raid_summary(raid, clan_members=None, heading: str = "Рейды столицы завершены") -> str:
    lines = [
        title("capital", heading),
        line("date", f"{local(_ts(raid.start_time))} — {local(_ts(raid.end_time))}"),
        "",
        line("gold", f"Добыто <b>{num(raid.total_loot)}</b> золота столицы"),
        line("district", f"Районов разрушено: <b>{raid.destroyed_district_count or 0}</b>, "
                         f"атак: <b>{raid.attack_count or 0}</b>"),
        line("medal", f"Награда за атаки: <b>{num(raid.offensive_reward)}</b>, "
                      f"за защиту: <b>{num(raid.defensive_reward)}</b>"),
    ]
    top = _raid_top(raid)
    if top:
        lines.append(f"\n{e('fire')} <b>Участники: {len(top)}</b>\n{quote(top, fold_after=10)}")
    if clan_members:
        raided = {_norm_name(m.name) for m in raid.members or []}
        tg = _tg_map()
        idle = [player(m.name, getattr(m, "town_hall", None), tg.get(_norm_name(m.name)))
                for m in clan_members if _norm_name(m.name) not in raided]
        if idle:
            lines.append(f"\n{e('miss')} <b>Не участвовали: {len(idle)}</b>\n{quote(idle)}")
    return "\n".join(lines)
