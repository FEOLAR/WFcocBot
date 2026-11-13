"""
Telegram-бот клана WarFil (Clash of Clans).

Запуск:  python main.py   (секреты — в .env, см. .env.example)
"""
import asyncio
import logging
import os
import random
import threading
import unicodedata
from collections import defaultdict
from http.server import HTTPServer, BaseHTTPRequestHandler

import settings as config

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

if not config.TELEGRAM_BOT_TOKEN:
    raise SystemExit(
        "Не задан TELEGRAM_BOT_TOKEN. Создай файл .env рядом с main.py "
        "(образец — .env.example) и перезапусти бота."
    )

import coc
from telegram import Update, BotCommand, MenuButtonCommands, MessageEntity
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ContextTypes, TypeHandler, ApplicationHandlerStop,
)

import storage
import stats_storage
import excel_builder
import card_builder
import fmt
import game_emoji
import views
import notifier
import coc_api
from coc_api import coc_client, _coc_safe, _norm_name, CLAN_TAG
from fmt import e, esc, title, line, quote

TOKEN = config.TELEGRAM_BOT_TOKEN
ADMIN = config.ADMIN_USERNAME.lower()

ROLE_ORDER = {"leader": 0, "coLeader": 1, "admin": 2, "member": 3}

_kv_tasks: dict[int, asyncio.Task] = {}


def _is_feolar(update: Update) -> bool:
    user = update.effective_user
    return user is not None and (user.username or "").lower() == ADMIN


async def _is_admin(update: Update) -> bool:
    """Админ беседы (или личка)."""
    chat = update.effective_chat
    if chat.type == "private" or _is_feolar(update):
        return True
    member = await chat.get_member(update.effective_user.id)
    return member.status in ("administrator", "creator")


def _args(update: Update) -> str:
    """Текст после команды целиком — context.args теряет спецсимволы ников."""
    raw = (update.message.text or "").strip()
    parts = raw.split(None, 1)
    return parts[1].strip() if len(parts) > 1 else ""


async def _deny(update: Update):
    await fmt.reply(update.message, fmt.err("Эта команда только для администраторов клана."))


# ══════════════════════════════════════════════════════════════════════════════
#  Основные команды
# ══════════════════════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = esc(user.first_name or "боец")

    if update.effective_chat.type == "private" and _is_feolar(update):
        storage.save_notify_chat(update.effective_chat.id)

    stats = ""
    try:
        clan = await _coc_safe(coc_client.get_clan, CLAN_TAG)
        league = getattr(getattr(clan, "war_league", None), "name", None)
        rows = [
            line("level", f"Уровень клана <b>{clan.level}</b>"),
            line("team", f"В составе <b>{clan.member_count}</b> из 50"),
            line("war", f"Побед в войнах <b>{clan.war_wins}</b>"),
        ]
        if league:
            rows.append(line("cwl", f"Лига войн: <b>{esc(league)}</b>"))
        stats = "\n\n" + "\n".join(rows)
    except Exception:
        pass

    text = (
        f"{e('hello')} Привет, <b>{name}</b>!\n\n"
        f"{title('clan', fmt.BRAND)}\n"
        "<i>Атакуем вместе, побеждаем достойно.</i>"
        f"{stats}\n\n"
        "Здесь видно, кто ещё не атаковал в войне, как идёт ЛВК и рейды столицы. "
        "Все команды — в меню слева от поля ввода."
    )
    await fmt.reply(update.message, text, reply_markup=fmt.welcome_buttons())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "\n".join([
        title("help", f"Команды бота {fmt.BRAND}"),
        "",
        f"{e('war')} /kv — атаки в текущей войне",
        f"{e('cwl')} /cwl — лига войн кланов",
        f"{e('capital')} /raid — рейды столицы",
        f"{e('team')} /team — состав клана",
        f"{e('stats')} /statistic — статистика в Excel",
        f"{e('card')} /card — карточка клана",
        f"{e('link')} /register ник — привязать свой аккаунт",
        "",
        "<i>Пример: /register WarriorKing</i>",
        "",
        f"{e('admin')} <b>Администраторам</b>",
        quote([
            "/link @telegram ник — привязать игрока",
            "/unlink ник — убрать привязку",
            "/links — все привязки",
            "/addchat, /removechat, /listchats — чаты для уведомлений",
            "/status — проверить, работают ли уведомления",
            "/emoji, /emojipack, /setemoji — игровые эмодзи",
        ], fold_after=20),
    ])
    await fmt.reply(update.message, text)


async def register_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    coc_name_raw = _args(update)
    if not coc_name_raw:
        await fmt.reply(update.message, "\n".join([
            title("link", "Привязка аккаунта"),
            "",
            "Напиши ник из игры после команды — так тебя будут отмечать в списках войны.",
            "<i>Пример: /register WarriorKing</i>",
        ]))
        return
    coc_name = unicodedata.normalize("NFC", coc_name_raw)
    user = update.effective_user
    storage.register_player(user.id, coc_name, user.username)
    storage.update_last_seen(user.id)
    tg = f" → @{esc(user.username)}" if user.username else ""
    hint = ("" if user.username else
            "\n\n" + fmt.warn("У тебя нет @username в Telegram — бот не сможет тебя отметить. "
                              "Задай его в настройках Telegram и привяжись снова."))
    await fmt.reply(update.message, fmt.ok(f"Аккаунт привязан: <b>{esc(coc_name)}</b>{tg}") + hint)


async def online_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    coc_name = storage.update_last_seen(update.effective_user.id)
    if coc_name:
        await fmt.reply(update.message, fmt.ok(f"<b>{esc(coc_name)}</b>, отметил тебя в сети."))
    else:
        await fmt.reply(update.message, fmt.warn("Сначала привяжи аккаунт: /register ник"))


async def team_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await fmt.reply(update.message, line("time", "Загружаю состав клана…"))
    try:
        clan = await _coc_safe(coc_client.get_clan, CLAN_TAG)
        groups: dict[str, list] = defaultdict(list)
        for m in sorted(clan.members, key=lambda m: (ROLE_ORDER.get(m.role.value, 9), m.name.lower())):
            groups[m.role.value].append(m)

        role_titles = {
            "leader": ("leader", "Глава"),
            "coLeader": ("coleader", "Соруководители"),
            "admin": ("elder", "Старейшины"),
            "member": ("member", "Участники"),
        }
        tg_map = storage.get_tg_username_map()
        lines = [title("clan", esc(clan.name)), line("team", f"{clan.member_count} из 50")]
        for role in ("leader", "coLeader", "admin", "member"):
            members = groups.get(role)
            if not members:
                continue
            key, label = role_titles[role]
            count = f": {len(members)}" if role != "leader" else ""
            rows = [fmt.player(m.name, m.town_hall, tg_map.get(_norm_name(m.name))) for m in members]
            lines.append(f"\n{e(key)} <b>{label}{count}</b>\n{quote(rows, fold_after=15)}")
        await fmt.edit(msg, "\n".join(lines), reply_markup=fmt.clan_buttons())
    except coc.NotFound:
        await fmt.edit(msg, fmt.err(f"Клан {CLAN_TAG} не найден. Проверь CLAN_TAG в .env."))
    except Exception as exc:
        logger.error(f"/team: {exc}", exc_info=True)
        await fmt.edit(msg, fmt.err("CoC API не ответил. Попробуй через пару минут."))


# ══════════════════════════════════════════════════════════════════════════════
#  Доступ
# ══════════════════════════════════════════════════════════════════════════════

async def _clan_group_id(bot) -> int | None:
    if config.CLAN_GROUP_CHAT_ID:
        return config.CLAN_GROUP_CHAT_ID
    for cid in storage.get_notify_chats():
        if cid < 0:
            return cid
    return None


async def clan_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выполняется до всех команд: бот работает только в беседе клана
    и в личке у её участников. Админ бота проходит всегда."""
    if not update.effective_chat or not update.effective_user:
        return
    chat, user = update.effective_chat, update.effective_user
    if (user.username or "").lower() == ADMIN:
        return
    gid = await _clan_group_id(context.bot)

    if chat.type in ("group", "supergroup"):
        if gid and chat.id != gid:
            raise ApplicationHandlerStop
        return

    if chat.type == "private" and gid:
        try:
            member = await context.bot.get_chat_member(gid, user.id)
            allowed = member.status not in ("left", "kicked", "banned")
        except Exception:
            allowed = False
        if not allowed:
            if update.message:
                await fmt.reply(update.message, "\n".join([
                    title("clan", f"Бот только для клана {fmt.BRAND}"),
                    "",
                    "Ты пока не в беседе клана. Заполни анкету на сайте — после вступления бот откроется.",
                ]), reply_markup=fmt.welcome_buttons())
            raise ApplicationHandlerStop


# ══════════════════════════════════════════════════════════════════════════════
#  Привязки (админ)
# ══════════════════════════════════════════════════════════════════════════════

async def link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    try:
        try:
            allowed = await _is_admin(update)
        except Exception:
            allowed = True
        if not allowed:
            await _deny(update)
            return
        parts = _args(update).split(None, 1)
        if len(parts) < 2:
            await fmt.reply(msg, "\n".join([
                title("link", "Привязать игрока"),
                "",
                "/link @telegram ник в игре",
                quote(["/link @feolar Fanon", "/link @feolar ɢʀᴇꜱʜɴɪᴋ⇝ ™"]),
                "Один @telegram можно привязать к нескольким никам — для твинков.",
            ]))
            return
        tg_username = parts[0].lstrip("@")
        coc_name = unicodedata.normalize("NFC", parts[1].strip())
        storage.link_player(coc_name, tg_username)
        logger.info(f"Привязка: {coc_name!r} → @{tg_username}")
        await fmt.reply(msg, fmt.ok(f"<b>{esc(coc_name)}</b> привязан к @{esc(tg_username)}"))
    except Exception as exc:
        logger.error(f"/link: {exc}", exc_info=True)
        await fmt.reply(msg, fmt.err(f"Не сохранил привязку: {esc(exc)}"))


async def unlink_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    try:
        try:
            allowed = await _is_admin(update)
        except Exception:
            allowed = True
        if not allowed:
            await _deny(update)
            return
        body = _args(update)
        if not body:
            await fmt.reply(msg, "Укажи ник из игры: <code>/unlink Fanon</code>")
            return
        coc_name = unicodedata.normalize("NFC", body)
        if storage.unlink_player(coc_name):
            await fmt.reply(msg, fmt.ok(f"Привязка <b>{esc(coc_name)}</b> удалена."))
        else:
            await fmt.reply(msg, fmt.warn(f"Привязки для <b>{esc(coc_name)}</b> нет — проверь написание ника в /links."))
    except Exception as exc:
        logger.error(f"/unlink: {exc}", exc_info=True)
        await fmt.reply(msg, fmt.err(f"Не удалил привязку: {esc(exc)}"))


async def links_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin(update):
        await _deny(update)
        return
    all_links = storage.get_all_links()
    if not all_links:
        await fmt.reply(update.message, "Привязок пока нет. Добавь первую: <code>/link @telegram ник</code>")
        return
    rows = [f"<b>{esc(n)}</b> → @{esc(u)}" for n, u in sorted(all_links.items(), key=lambda x: x[1].lower())]
    await fmt.reply(update.message, f"{title('link', f'Привязки: {len(rows)}')}\n{quote(rows, fold_after=20)}")


# ══════════════════════════════════════════════════════════════════════════════
#  Война / ЛВК / рейды — команды
# ══════════════════════════════════════════════════════════════════════════════

KV_REFRESH_SECONDS = 60
KV_REFRESH_LIMIT = 30   # не дольше 30 минут


async def kv_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    existing = _kv_tasks.get(chat_id)
    if existing and not existing.done():
        existing.cancel()

    msg = await fmt.reply(update.message, line("time", "Загружаю данные войны…"))
    try:
        war = await _coc_safe(coc_client.get_current_war, CLAN_TAG)
        text, keep = views.war_status(war)
        await fmt.edit(msg, text, reply_markup=fmt.clan_buttons())
    except coc.PrivateWarLog:
        await fmt.edit(msg, fmt.err("Журнал войн клана закрыт. Открыть его может глава в настройках клана."))
        return
    except coc.NotFound:
        await fmt.edit(msg, fmt.err(f"Клан {CLAN_TAG} не найден."))
        return
    except Exception as exc:
        logger.error(f"/kv: {exc}", exc_info=True)
        await fmt.edit(msg, fmt.err("CoC API не ответил. Попробуй через пару минут."))
        return
    if not keep:
        return

    async def auto_update():
        last = text
        for _ in range(KV_REFRESH_LIMIT):
            await asyncio.sleep(KV_REFRESH_SECONDS)
            try:
                w = await _coc_safe(coc_client.get_current_war, CLAN_TAG)
                new_text, still = views.war_status(w)
                if new_text != last:
                    await fmt.edit(msg, new_text, reply_markup=fmt.clan_buttons())
                    last = new_text
                if not still:
                    break
            except asyncio.CancelledError:
                break
            except Exception:
                break

    task = asyncio.create_task(auto_update())
    _kv_tasks[chat_id] = task


async def cwl_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await fmt.reply(update.message, line("time", "Загружаю лигу войн…"))
    try:
        group, wars = await notifier.cwl_wars()
        if group is None:
            text = "\n".join([title("cwl", "Лига войн кланов"), "",
                              "Сейчас клан не в ЛВК. Сезон начинается в первых числах месяца — "
                              "бот объявит каждый раунд."])
        else:
            text, _ = views.cwl_status(group.season, wars)
        await fmt.edit(msg, text, reply_markup=fmt.clan_buttons())
    except Exception as exc:
        logger.error(f"/cwl: {exc}", exc_info=True)
        await fmt.edit(msg, fmt.err("CoC API не ответил. Попробуй через пару минут."))


async def raid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await fmt.reply(update.message, line("time", "Загружаю рейды столицы…"))
    try:
        raid = await notifier.current_raid()
        text, _ = views.raid_status(raid)
        await fmt.edit(msg, text)
    except Exception as exc:
        logger.error(f"/raid: {exc}", exc_info=True)
        await fmt.edit(msg, fmt.err("CoC API не ответил. Попробуй через пару минут."))


async def _test_send(update: Update, coro_text):
    if not _is_feolar(update):
        await _deny(update)
        return
    try:
        text = await coro_text()
        if not text:
            await fmt.reply(update.message, fmt.warn("Сейчас нет подходящих данных для этого сообщения."))
            return
        await fmt.reply(update.message, text, reply_markup=fmt.clan_buttons())
    except Exception as exc:
        logger.error(f"test: {exc}", exc_info=True)
        await fmt.reply(update.message, fmt.err(esc(exc)))


async def _war_or_none():
    w = await _coc_safe(coc_client.get_current_war, CLAN_TAG)
    return None if w is None or w.state == "notInWar" else w


async def teststart_command(update, context):
    async def t():
        w = await _war_or_none()
        return w and (views.war_found(w) if w.state == "preparation" else views.war_started(w))
    await _test_send(update, t)


async def testend_command(update, context):
    async def t():
        w = await _war_or_none()
        return w and views.war_ended(w)
    await _test_send(update, t)


async def testremind_command(update, context):
    async def t():
        w = await _war_or_none()
        return w and (views.war_reminder(w) or fmt.ok("Все атаки сделаны — напоминание не нужно."))
    await _test_send(update, t)


async def _cwl_pick(state: str | None = None):
    group, wars = await notifier.cwl_wars()
    picked = [(n, w) for n, w in wars if state is None or w.state == state]
    return picked[-1] if picked else (None, None)


async def testcwlstart_command(update, context):
    async def t():
        n, w = await _cwl_pick("inWar")
        return w and views.cwl_started(w, n)
    await _test_send(update, t)


async def testcwlend_command(update, context):
    async def t():
        n, w = await _cwl_pick("warEnded")
        return w and views.cwl_ended(w, n)
    await _test_send(update, t)


async def testraidstart_command(update, context):
    async def t():
        r = await notifier.current_raid()
        return r and views.raid_started(r)
    await _test_send(update, t)


async def testraidend_command(update, context):
    async def t():
        r = await notifier.current_raid()
        return r and views.raid_summary(r, await notifier.clan_members())
    await _test_send(update, t)


async def statistic_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await fmt.reply(update.message, fmt.line("stats", "Собираю статистику за последние войны, ЛВК и рейды…"))
    try:
        clan_tag_clean = CLAN_TAG.lstrip("#").upper()

        # ── Helper: extract player data from a ClanWar object ──────────────────
        def _war_members(war_obj):
            our = war_obj.clan
            if our.tag.lstrip("#").upper() != clan_tag_clean:
                our = war_obj.opponent
            members = []
            for m in (our.members or []):
                members.append({
                    "name": m.name,
                    "th": getattr(m, "town_hall", 0),
                    "attacks_used": len(m.attacks) if m.attacks else 0,
                    "attacks_max": war_obj.attacks_per_member,
                })
            return members

        def _war_result(war_obj):
            our = war_obj.clan
            opp = war_obj.opponent
            if our.tag.lstrip("#").upper() != clan_tag_clean:
                our, opp = opp, our
            if war_obj.state != "warEnded":
                return "—"
            if our.stars > opp.stars:
                return "win"
            elif our.stars < opp.stars:
                return "lose"
            elif (our.destruction or 0) > (opp.destruction or 0):
                return "win"
            elif (our.destruction or 0) < (opp.destruction or 0):
                return "lose"
            return "tie"

        # ── Fetch clan member list (always available, used as player row fallback) ──
        clan_members: list[dict] = []
        try:
            clan = await _coc_safe(coc_client.get_clan, CLAN_TAG)
            for m in (clan.members or []):
                clan_members.append({"name": m.name, "th": getattr(m, "town_hall", 0)})
        except Exception as e:
            logger.warning(f"Clan members: {e}")


        # ── 1. КВ: war log (5 regular wars) + live player data overlay ─────────
        # Index from saved history: (end_time[:16], opponent_name) → members
        saved_wars = stats_storage.get_war_history()
        player_by_time: dict[str, list] = {}
        player_by_opp: dict[str, list] = {}
        for sw in saved_wars:
            key_t = sw.get("end_time", "")[:16]
            key_o = sw.get("opponent", "").strip().lower()
            if sw.get("members"):
                if key_t:
                    player_by_time[key_t] = sw["members"]
                if key_o:
                    player_by_opp[key_o] = sw["members"]

        # Fetch current war for live player data and auto-save if warEnded
        try:
            cw = await _coc_safe(coc_client.get_current_war, CLAN_TAG)
            if cw and cw.state in ("inWar", "warEnded"):
                cw_end = cw.end_time.time.isoformat() if cw.end_time else ""
                members_live = _war_members(cw)
                if cw_end:
                    player_by_time[cw_end[:16]] = members_live
                opp_name = (cw.opponent.name if cw.opponent else "").strip().lower()
                if opp_name:
                    player_by_opp[opp_name] = members_live
                # Auto-save warEnded war if not already saved
                if cw.state == "warEnded" and not cw.is_cwl:
                    notifier._save_war_history(cw)
        except Exception as e:
            logger.warning(f"Текущая КВ: {e}")

        # Fetch war log for the last 5 regular wars (summaries)
        war_history: list[dict] = []
        try:
            async for entry in await _coc_safe(coc_client.get_war_log, CLAN_TAG, limit=10):
                # Skip CWL entries (opponent is None)
                if entry.opponent is None:
                    continue
                end_iso = entry.end_time.time.isoformat() if entry.end_time else ""
                result_str = "—"
                try:
                    result_str = entry.result.value if hasattr(entry.result, "value") else str(entry.result).lower()
                except Exception:
                    pass
                opp_name = (entry.opponent.name or "").strip()
                # Match player data by time first, then by opponent name
                key_t = end_iso[:16]
                key_o = opp_name.lower()
                members = (player_by_time.get(key_t)
                           or player_by_opp.get(key_o)
                           or [])
                war_history.append({
                    "end_time": end_iso,
                    "opponent": opp_name or "—",
                    "result": result_str,
                    "our_stars": entry.clan.stars if entry.clan else 0,
                    "their_stars": entry.opponent.stars if entry.opponent else 0,
                    "team_size": entry.team_size or 0,
                    "attacks_per_member": entry.attacks_per_member or 2,
                    "members": members,
                })
                if len(war_history) >= 5:
                    break
        except Exception as e:
            logger.warning(f"War log error: {e}")
            war_history = saved_wars[:5]

        if not war_history:
            war_history = saved_wars[:5]

        # ── 2. ЛВК: current season (live API) + previous seasons (saved) ───────
        cwl_seasons: list[dict] = []

        # Live current season
        live_season = None
        try:
            group = await _coc_safe(coc_client.get_league_group, CLAN_TAG)
            live_season = group.season
            live_rounds = []
            round_num = 0
            async for war in group.get_wars_for_clan(CLAN_TAG):
                round_num += 1
                if war.state == "notInWar":
                    continue
                our_side = war.clan if war.clan.tag.lstrip("#").upper() == clan_tag_clean else war.opponent
                opp_side = war.opponent if our_side is war.clan else war.clan
                members = [{"name": m.name, "attacked": bool(m.attacks)}
                           for m in (our_side.members or [])]
                live_rounds.append({
                    "round": round_num,
                    "opponent": opp_side.name,
                    "our_stars": our_side.stars,
                    "their_stars": opp_side.stars,
                    "state": war.state.value if hasattr(war.state, "value") else str(war.state),
                    "members": members,
                })
            if live_rounds:
                cwl_seasons.append({
                    "season": live_season,
                    "rounds": sorted(live_rounds, key=lambda r: r["round"]),
                })
        except coc.NotFound:
            pass
        except Exception as e:
            logger.warning(f"ЛВК live error: {e}")

        # Saved seasons (include any season != current live season)
        for saved_s in stats_storage.get_cwl_seasons():
            if saved_s.get("season") != live_season and saved_s.get("rounds"):
                cwl_seasons.append(saved_s)

        # If nothing live, try all saved
        if not cwl_seasons:
            cwl_seasons = stats_storage.get_cwl_seasons()

        # ── 3. Рейды столицы: только последний рейд ────────────────────────────
        raids = []
        try:
            raid_log = await _coc_safe(coc_client.get_raid_log, CLAN_TAG, limit=1)
            async for entry in raid_log:
                raids.append(entry)
        except Exception as e:
            logger.warning(f"Рейды: {e}")

        buf = excel_builder.build_excel(
            war_history, cwl_seasons, raids,
            clan_members=clan_members,
        )
        n_cwl_rounds = sum(len(s.get("rounds", [])) for s in cwl_seasons)
        await update.message.reply_document(
            document=buf,
            filename="warfil_statistics.xlsx",
            caption=game_emoji.strip_custom(
                f"{fmt.title('stats', f'Статистика {fmt.BRAND}')}\n\n"
                f"{fmt.line('war', f'Клановые войны: {len(war_history)}')}\n"
                f"{fmt.line('cwl', f'ЛВК: сезонов {len(cwl_seasons)}, раундов {n_cwl_rounds}')}\n"
                f"{fmt.line('capital', 'Рейды столицы: последний уикенд')}"
            ),
            parse_mode="HTML",
        )
        await msg.delete()
    except Exception as e:
        logger.error(f"Ошибка /statistic: {e}", exc_info=True)
        await fmt.edit(msg, fmt.err("Не получилось собрать файл: CoC API не ответил. Попробуй через пару минут."))

CARD_BACKGROUNDS = ["card_background.png", "card_bg_2.png", "card_bg_3.png", "card_bg_4.png"]


async def card_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await fmt.reply(update.message, line("card", "Рисую карточку клана…"))
    try:
        clan = await _coc_safe(coc_client.get_clan, CLAN_TAG)
        raids = []
        try:
            async for r in await _coc_safe(coc_client.get_raid_log, CLAN_TAG, limit=4):
                raids.append(r)
        except Exception:
            pass
        base = os.path.dirname(os.path.abspath(__file__))
        available = [os.path.join(base, p) for p in CARD_BACKGROUNDS if os.path.exists(os.path.join(base, p))]
        buf = await card_builder.build_card(clan, raids=raids,
                                            background_path=random.choice(available) if available else None)
        caption = "\n".join([
            f"{game_emoji.plain('clan')} <b>{esc(clan.name)}</b>, уровень {clan.level}",
            f"{game_emoji.plain('team')} {clan.member_count} в составе",
            f"{game_emoji.plain('war')} Побед {clan.war_wins}, поражений {clan.war_losses}",
        ])
        await update.message.reply_photo(photo=buf, caption=caption, parse_mode="HTML")
        await msg.delete()
    except Exception as exc:
        logger.error(f"/card: {exc}", exc_info=True)
        await fmt.edit(msg, fmt.err(f"Карточка не собралась: {esc(exc)}"))


# ══════════════════════════════════════════════════════════════════════════════
#  Чаты рассылки и диагностика (админ бота)
# ══════════════════════════════════════════════════════════════════════════════

async def addchat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_feolar(update):
        await _deny(update)
        return
    chat = update.effective_chat
    name = esc(chat.title or "личка")
    if storage.add_notify_chat(chat.id):
        await fmt.reply(update.message, "\n".join([
            fmt.ok(f"<b>{name}</b> добавлен в рассылку"),
            "",
            "Сюда будут приходить старт и итоги войны, раунды ЛВК, рейды столицы и напоминания.",
            f"ID чата: <code>{chat.id}</code>",
        ]))
    else:
        await fmt.reply(update.message, fmt.line("chat", f"<b>{name}</b> уже в рассылке. ID: <code>{chat.id}</code>"))


async def removechat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_feolar(update):
        await _deny(update)
        return
    chat = update.effective_chat
    name = esc(chat.title or "личка")
    if storage.remove_notify_chat(chat.id):
        await fmt.reply(update.message, fmt.ok(f"<b>{name}</b> больше не получает уведомления."))
    else:
        await fmt.reply(update.message, fmt.warn(f"<b>{name}</b> и так не в рассылке."))


async def listchats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_feolar(update):
        await _deny(update)
        return
    chats = storage.get_notify_chats()
    if not chats:
        await fmt.reply(update.message, fmt.warn(
            "Рассылка пустая — уведомления никуда не уходят. Отправь /addchat в беседе клана."))
        return
    rows = []
    for cid in chats:
        try:
            c = await context.bot.get_chat(cid)
            rows.append(f"<b>{esc(c.title or c.full_name or 'личка')}</b>  <code>{cid}</code>")
        except Exception as exc:
            rows.append(f"<code>{cid}</code>  {e('warn')} бот не видит чат: {esc(exc)}")
    await fmt.reply(update.message, f"{title('chat', f'Чаты рассылки: {len(chats)}')}\n{quote(rows, 20)}")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_feolar(update):
        await _deny(update)
        return
    await fmt.reply(update.message, notifier.status_text())


async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Прогнать все мониторы прямо сейчас."""
    if not _is_feolar(update):
        await _deny(update)
        return
    msg = await fmt.reply(update.message, line("time", "Проверяю войну, ЛВК и рейды…"))
    for name, tick in (("war", notifier.war_tick), ("cwl", notifier.cwl_tick), ("raid", notifier.raid_tick)):
        h = notifier._h(name)
        try:
            await asyncio.wait_for(tick(context.bot), timeout=notifier.TICK_TIMEOUT)
            h["last_ok"] = fmt.utcnow()
        except Exception as exc:
            h["last_error"] = f"{type(exc).__name__}: {exc}"
            h["last_error_at"] = fmt.utcnow()
    await fmt.edit(msg, notifier.status_text())


# ══════════════════════════════════════════════════════════════════════════════
#  Игровые эмодзи (админ бота)
# ══════════════════════════════════════════════════════════════════════════════

async def emoji_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_feolar(update):
        await _deny(update)
        return
    mapped = game_emoji.mapped()
    rows = [f"{e(k)} <code>{k}</code> — {desc}{'' if k in mapped else ' · обычный'}"
            for k, (_, desc) in game_emoji.KEYS.items()]
    note = ("Кастомные эмодзи включены." if game_emoji.custom_enabled else
            fmt.warn("Telegram не принял кастомные эмодзи — нужен Telegram Premium у владельца бота. "
                     "Сейчас показываются обычные."))
    await fmt.reply(update.message, "\n".join([
        title("admin", f"Игровые эмодзи: {len(mapped)} из {len(game_emoji.KEYS)}"),
        note,
        quote(rows, fold_after=12),
        "",
        "Подтянуть пак: <code>/emojipack https://t.me/addemoji/имя</code>",
        "Вручную: <code>/setemoji war ⚔️ star ⭐</code> — вместо обычных вставь эмодзи из пака.",
        "Сбросить: /emojireset",
    ]))


async def emojipack_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_feolar(update):
        await _deny(update)
        return
    name = game_emoji.pack_name_from(_args(update))
    if not name:
        await fmt.reply(update.message, "Пришли ссылку на пак: <code>/emojipack https://t.me/addemoji/имя</code>")
        return
    try:
        pack = await context.bot.get_sticker_set(name)
    except Exception as exc:
        await fmt.reply(update.message, fmt.err(f"Пак <code>{esc(name)}</code> не найден: {esc(exc)}"))
        return
    found = game_emoji.auto_map_stickers(pack.stickers, overwrite=True)
    if found:
        game_emoji.set_many(found)
    missing = [k for k in game_emoji.KEYS if k not in game_emoji.mapped()]
    lines = [
        fmt.ok(f"Пак <b>{esc(pack.title)}</b>: {len(pack.stickers)} эмодзи, сопоставлено {len(found)}"),
    ]
    if found:
        lines.append(" ".join(e(k) for k in found))
    if missing:
        lines.append("\nБез пары остались: " + ", ".join(f"<code>{k}</code>" for k in missing))
        lines.append("Их можно задать вручную: <code>/setemoji ключ эмодзи</code>")
    await fmt.reply(update.message, "\n".join(lines))


async def setemoji_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setemoji war <кастомный эмодзи> star <кастомный эмодзи> ..."""
    if not _is_feolar(update):
        await _deny(update)
        return
    msg = update.message
    text = msg.text or ""
    utf16 = text.encode("utf-16-le")

    def cut(a: int, b: int) -> str:
        return utf16[a * 2:b * 2].decode("utf-16-le")

    pairs, pos = {}, 0
    ents = sorted([x for x in (msg.entities or []) if x.type == MessageEntity.CUSTOM_EMOJI],
                  key=lambda x: x.offset)
    for ent in ents:
        words = cut(pos, ent.offset).split()
        key = next((w.lower() for w in reversed(words) if w.lower() in game_emoji.KEYS), None)
        if key:
            pairs[key] = ent.custom_emoji_id
        pos = ent.offset + ent.length
    if not pairs:
        await fmt.reply(msg, "\n".join([
            "Не нашёл пар «ключ + кастомный эмодзи».",
            "Пример: <code>/setemoji war</code> ⚔️ <code>star</code> ⭐ — эмодзи вставь из пака.",
            "Список ключей — /emoji",
        ]))
        return
    game_emoji.set_many(pairs)
    await fmt.reply(msg, fmt.ok("Сохранил: " + "  ".join(f"{e(k)} <code>{k}</code>" for k in pairs)))


async def emojireset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_feolar(update):
        await _deny(update)
        return
    game_emoji.reset()
    await fmt.reply(update.message, fmt.ok("Вернул обычные эмодзи."))


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    text = update.message.text
    if text == "📋 Список клана":
        await team_command(update, context)
    elif text == "❓ Помощь":
        await help_command(update, context)
    elif text == "🔗 Привязать аккаунт":
        await fmt.reply(update.message, "Напиши ник из игры после команды: <code>/register WarriorKing</code>")


# ══════════════════════════════════════════════════════════════════════════════
#  Запуск
# ══════════════════════════════════════════════════════════════════════════════

async def post_init(application):
    await coc_api.login()

    if config.EMOJI_PACK and not game_emoji.mapped():
        try:
            pack = await application.bot.get_sticker_set(game_emoji.pack_name_from(config.EMOJI_PACK))
            found = game_emoji.auto_map_stickers(pack.stickers)
            if found:
                game_emoji.set_many(found)
            logger.info(f"Эмодзи-пак {pack.name}: сопоставлено {len(found)}")
        except Exception as exc:
            logger.warning(f"Эмодзи-пак {config.EMOJI_PACK}: {exc}")

    chats = storage.get_notify_chats()
    logger.info(f"Папка данных: {storage.DATA_DIR}; чатов рассылки: {len(chats)}")
    if not chats:
        logger.warning("Список чатов рассылки пуст! Отправь /addchat в беседе клана.")

    notifier.start(application.bot)

    await application.bot.set_my_commands([
        BotCommand("kv",        "Атаки в текущей войне"),
        BotCommand("cwl",       "Лига войн кланов"),
        BotCommand("raid",      "Рейды столицы"),
        BotCommand("team",      "Состав клана"),
        BotCommand("statistic", "Статистика в Excel"),
        BotCommand("card",      "Карточка клана"),
        BotCommand("register",  "Привязать свой аккаунт"),
        BotCommand("help",      "Все команды"),
        BotCommand("start",     "О клане"),
    ])
    await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    logger.info("Команды бота зарегистрированы")


async def post_shutdown(application):
    try:
        if coc_client.http is not None:
            await coc_client.close()
    except Exception:
        pass


async def error_handler(update, context):
    from telegram.error import Conflict
    if isinstance(context.error, Conflict):
        logger.warning("Конфликт: запущен второй экземпляр бота с тем же токеном.")
    else:
        logger.error(f"Ошибка: {context.error}", exc_info=context.error)


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass


def _start_health_server():
    port = int(os.environ.get("PORT", 8080))
    try:
        server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    except OSError as exc:
        logger.warning(f"Health-сервер на порту {port} не запущен: {exc}")
        return
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info(f"Health check server listening on port {port}")


def main():
    _start_health_server()
    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_handler(TypeHandler(Update, clan_guard), group=-1)

    for name, fn in [
        ("start", start), ("help", help_command), ("team", team_command),
        ("register", register_command), ("online", online_command),
        ("kv", kv_command), ("cwl", cwl_command), ("raid", raid_command),
        ("statistic", statistic_command), ("card", card_command),
        ("link", link_command), ("unlink", unlink_command), ("links", links_command),
        ("addchat", addchat_command), ("removechat", removechat_command),
        ("listchats", listchats_command), ("status", status_command), ("check", check_command),
        ("emoji", emoji_command), ("emojipack", emojipack_command),
        ("setemoji", setemoji_command), ("emojireset", emojireset_command),
        ("teststart", teststart_command), ("testend", testend_command),
        ("testremind", testremind_command),
        ("testcwlstart", testcwlstart_command), ("testcwlend", testcwlend_command),
        ("testraidstart", testraidstart_command), ("testraidend", testraidend_command),
    ]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(MessageHandler(filters.UpdateType.MESSAGE & filters.TEXT & ~filters.COMMAND, echo))
    app.add_error_handler(error_handler)

    logger.info("Бот запущен...")
    app.run_polling()


if __name__ == "__main__":
    main()
