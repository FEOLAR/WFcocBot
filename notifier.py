"""
Автоуведомления в беседу клана: клановая война, ЛВК, рейды столицы.

Почему раньше всё замолкало и что изменилось:
  • Папка данных. На сервере бот брал /data — там пусто, список чатов
    рассылки пустой, и мониторы молча пропускали каждое событие.
    Теперь данные лежат в data/ рядом с ботом (см. storage._data_dir).
  • Зависание. _coc_safe ретраил бесконечно; одна блокировка по IP могла
    навсегда «подвесить» монитор. Теперь у каждого цикла таймаут, а мониторы
    работают под супервизором: упал — перезапустился, ошибка видна в /status.
  • ЛВК. Код ждал состояние группы «warEnded», которого у группы не бывает
    (у неё preparation → inWar → ended), и считал номер раунда по длине
    списка, сдвигаясь на единицу. Конец раунда не приходил никогда, а седьмой
    раунд не объявлялся. Теперь отслеживается каждая война клана по её тегу.
  • Рейды. Атаки и районы читались из несуществующих полей (всегда 0).
  • Ники с символами < > & ломали HTML — Telegram отклонял сообщение.
    Теперь всё экранируется.
  • Каждое событие отправляется один раз: состояние хранится в
    data/notify_state.json и переживает перезапуск.
"""
import asyncio
import logging
import time
from datetime import timedelta

import coc
from telegram.error import ChatMigrated, Forbidden

import settings as config
import fmt
import storage
import stats_storage
import views
from coc_api import coc_client, _coc_safe, CLAN_TAG
from fmt import utcnow

logger = logging.getLogger(__name__)

# ── Здоровье мониторов (для /status) ─────────────────────────────────────────
HEALTH: dict[str, dict] = {}
_TASKS: set[asyncio.Task] = set()
TICK_TIMEOUT = 240

START_GRACE = timedelta(hours=6)    # о старте сообщаем, если он был не раньше
END_GRACE = timedelta(hours=12)     # об итогах — если закончилось не раньше

WAR_REMINDERS = [(4 * 3600, "r4h"), (3600, "r1h")]
CWL_REMINDERS = [(3 * 3600, "r3h")]
RAID_REMINDERS = [(24 * 3600, "r24h"), (3 * 3600, "r3h")]


def _h(name: str) -> dict:
    return HEALTH.setdefault(name, {"runs": 0, "last_ok": None, "last_error": None,
                                    "last_error_at": None, "note": ""})


def _ts(t):
    return t.time if t is not None else None


# ── Отправка ─────────────────────────────────────────────────────────────────

async def broadcast(bot, text: str, markup=None) -> int:
    chats = storage.get_notify_chats()
    if not chats:
        _h("broadcast")["note"] = "Нет чатов для рассылки — отправь /addchat в беседе клана"
        logger.warning("Рассылка: список чатов пуст — уведомление не отправлено")
        return 0
    sent = 0
    for cid in chats:
        try:
            await fmt.send(bot, cid, text, reply_markup=markup)
            sent += 1
        except ChatMigrated as exc:
            storage.replace_notify_chat(cid, exc.new_chat_id)
            logger.info(f"Чат {cid} стал супергруппой {exc.new_chat_id} — обновил список")
            try:
                await fmt.send(bot, exc.new_chat_id, text, reply_markup=markup)
                sent += 1
            except Exception as exc2:
                logger.warning(f"Рассылка в {exc.new_chat_id}: {exc2}")
        except Forbidden as exc:
            _h("broadcast")["last_error"] = f"{cid}: бот удалён из чата или заблокирован ({exc})"
            _h("broadcast")["last_error_at"] = utcnow()
            logger.warning(f"Рассылка в {cid}: нет доступа — {exc}")
        except Exception as exc:
            _h("broadcast")["last_error"] = f"{cid}: {exc}"
            _h("broadcast")["last_error_at"] = utcnow()
            logger.warning(f"Рассылка в {cid}: {exc}")
    if sent:
        hb = _h("broadcast")
        hb["last_ok"] = utcnow()
        hb["note"] = f"чатов: {len(chats)}"
    return sent


class Section:
    """Состояние одного вида уведомлений (war / cwl / raid) в notify_state.json."""

    def __init__(self, name: str):
        self.name = name

    def load(self) -> dict:
        return storage.load_notify_state().get(self.name, {})

    def save(self, data: dict):
        state = storage.load_notify_state()
        state[self.name] = data
        storage.save_notify_state(state)


async def notify_once(bot, sec: dict, key: str, text: str | None, markup=None) -> bool:
    """Отправляет сообщение, если ключ ещё не отмечен. Возвращает True, если отметил."""
    sent_keys = sec.setdefault("sent", [])
    if key in sent_keys:
        return False
    if text is None:            # нечего сообщать (например, все атаки сделаны)
        sent_keys.append(key)
        return True
    tries = sec.setdefault("tries", {})
    n = await broadcast(bot, text, markup)
    tries[key] = tries.get(key, 0) + 1
    if n > 0 or tries[key] >= 5:
        sent_keys.append(key)
        return True
    return False


def _mark(sec: dict, *keys: str):
    lst = sec.setdefault("sent", [])
    for k in keys:
        if k not in lst:
            lst.append(k)


async def _maybe_digest(bot, sec: dict, minutes: int, started_at, text: str, markup=None):
    if minutes <= 0 or not started_at:
        return
    now = time.time()
    last = sec.get("last_digest") or 0
    if now - last < minutes * 60:
        return
    if (utcnow() - started_at).total_seconds() < minutes * 60:
        return      # только что прислали старт — сводка не нужна
    if last == 0:
        sec["last_digest"] = now   # первый цикл после старта бота: отсчёт с нуля
        return
    if await broadcast(bot, text, markup):
        sec["last_digest"] = now


# ══════════════════════════════════════════════════════════════════════════════
#  Клановая война (обычная)
# ══════════════════════════════════════════════════════════════════════════════

def _war_id(war) -> str:
    _, opp = views.sides(war)
    t = _ts(war.preparation_start_time) or _ts(war.start_time)
    return f"{opp.tag if opp else '?'}|{t.isoformat() if t else '?'}"


def _save_war_history(war):
    our, opp = views.sides(war)
    end = _ts(war.end_time)
    end_iso = end.strftime("%Y-%m-%dT%H:%M:%S") if end else ""
    if any(w.get("end_time", "")[:16] == end_iso[:16] for w in stats_storage.get_war_history()):
        return
    per = war.attacks_per_member or 2
    stats_storage.save_war_result(
        end_time=end_iso, opponent=opp.name, result=views.result_of(our, opp),
        our_stars=our.stars, their_stars=opp.stars, team_size=war.team_size,
        attacks_per_member=per,
        members=[{"name": m.name, "th": getattr(m, "town_hall", 0),
                  "attacks_used": len(m.attacks or []), "attacks_max": per}
                 for m in our.members or []],
    )


async def war_tick(bot):
    h = _h("war")
    try:
        war = await _coc_safe(coc_client.get_clan_war, CLAN_TAG)
    except coc.PrivateWarLog:
        h["note"] = "журнал войн клана закрыт — откройте его в настройках клана"
        return
    if war is None or war.state == "notInWar":
        h["note"] = "войны нет"
        return
    if getattr(war, "type", None) == "friendly":
        h["note"] = "идёт дружеская война — не объявляем"
        return

    sec_store = Section("war")
    sec = sec_store.load()
    wid = _war_id(war)
    if sec.get("id") != wid:
        sec = {"id": wid, "sent": [], "tries": {}}
    now = utcnow()
    start, end = _ts(war.start_time), _ts(war.end_time)
    markup = fmt.clan_buttons()
    _, opp = views.sides(war)
    h["note"] = f"{war.state} против {opp.name}"

    if war.state == "preparation":
        await notify_once(bot, sec, "prep", views.war_found(war), markup)

    elif war.state == "inWar":
        _mark(sec, "prep")
        if start and now - start > START_GRACE:
            _mark(sec, "start")
        await notify_once(bot, sec, "start", views.war_started(war), markup)

        left = (end - now).total_seconds() if end else 0
        due = [k for sec_left, k in WAR_REMINDERS if left <= sec_left and k not in sec.get("sent", [])]
        if due and left > 0:
            text = views.war_reminder(war)
            if await notify_once(bot, sec, due[-1], text, markup):
                _mark(sec, *due)
        status, _ = views.war_status(war)
        await _maybe_digest(bot, sec, config.WAR_DIGEST_MINUTES, start, status, markup)

    elif war.state == "warEnded":
        _mark(sec, "prep", "start", *[k for _, k in WAR_REMINDERS])
        try:
            _save_war_history(war)
        except Exception as exc:
            logger.warning(f"Сохранение истории войны: {exc}")
        if end and now - end > END_GRACE:
            _mark(sec, "end")
        await notify_once(bot, sec, "end", views.war_ended(war), markup)

    sec_store.save(sec)


# ══════════════════════════════════════════════════════════════════════════════
#  Лига войн кланов
# ══════════════════════════════════════════════════════════════════════════════

async def cwl_wars():
    """(группа, [(номер раунда, война нашего клана), ...]) или (None, [])."""
    try:
        group = await _coc_safe(coc_client.get_league_group, CLAN_TAG)
    except (coc.NotFound, coc.PrivateWarLog, coc.GatewayError):
        return None, []
    if group is None:
        return None, []
    round_of = {tag: i + 1 for i, tags in enumerate(group.rounds) for tag in tags}
    wars = []
    async for w in group.get_wars_for_clan(CLAN_TAG):
        wars.append((round_of.get(w.war_tag, 0), w))
    wars.sort(key=lambda x: x[0])
    return group, wars


def _save_cwl_round(season, n, war):
    our, opp = views.sides(war)
    stats_storage.save_cwl_round(
        season=season or "unknown", round_num=n, opponent=opp.name,
        our_stars=our.stars, their_stars=opp.stars,
        members=[{"name": m.name, "attacked": bool(m.attacks)} for m in our.members or []],
    )


async def cwl_tick(bot):
    h = _h("cwl")
    group, wars = await cwl_wars()
    if group is None:
        h["note"] = "клан сейчас не в ЛВК"
        return
    sec_store = Section("cwl")
    sec = sec_store.load()
    if sec.get("season") != group.season:
        sec = {"season": group.season, "sent": [], "tries": {}}
    now = utcnow()
    markup = fmt.clan_buttons()
    h["note"] = f"сезон {group.season}, группа: {group.state}, раундов с нашим кланом: {len(wars)}"

    for n, w in wars:
        k = f"{n}:{w.war_tag}"
        start, end = _ts(w.start_time), _ts(w.end_time)
        if w.state == "inWar":
            if start and now - start > START_GRACE:
                _mark(sec, f"{k}:start")
            await notify_once(bot, sec, f"{k}:start", views.cwl_started(w, n), markup)
            left = (end - now).total_seconds() if end else 0
            due = [rk for s, rk in CWL_REMINDERS if left <= s and f"{k}:{rk}" not in sec.get("sent", [])]
            if due and left > 0:
                if await notify_once(bot, sec, f"{k}:{due[-1]}", views.cwl_reminder(w, n), markup):
                    _mark(sec, *[f"{k}:{d}" for d in due])
            status, _ = views.cwl_status(group.season, wars)
            await _maybe_digest(bot, sec, config.CWL_DIGEST_MINUTES, start, status, markup)
        elif w.state == "warEnded":
            _mark(sec, f"{k}:start", *[f"{k}:{rk}" for _, rk in CWL_REMINDERS])
            if f"{k}:end" not in sec.get("sent", []):
                try:
                    _save_cwl_round(group.season, n, w)
                except Exception as exc:
                    logger.warning(f"Сохранение раунда ЛВК: {exc}")
            if end and now - end > END_GRACE:
                _mark(sec, f"{k}:end")
            await notify_once(bot, sec, f"{k}:end", views.cwl_ended(w, n), markup)

    sec_store.save(sec)


# ══════════════════════════════════════════════════════════════════════════════
#  Рейды столицы
# ══════════════════════════════════════════════════════════════════════════════

async def current_raid():
    log = await _coc_safe(coc_client.get_raid_log, CLAN_TAG, limit=1)
    async for entry in log:
        return entry
    return None


async def clan_members():
    try:
        clan = await _coc_safe(coc_client.get_clan, CLAN_TAG)
        return list(clan.members or [])
    except Exception:
        return []


async def raid_tick(bot):
    h = _h("raid")
    raid = await current_raid()
    if raid is None:
        h["note"] = "журнал рейдов пуст"
        return
    start, end = _ts(raid.start_time), _ts(raid.end_time)
    rid = start.isoformat() if start else "?"
    sec_store = Section("raid")
    sec = sec_store.load()
    if sec.get("id") != rid:
        sec = {"id": rid, "sent": [], "tries": {}}
    now = utcnow()
    h["note"] = f"{raid.state}, старт {fmt.local(start)}"

    if raid.state == "ongoing":
        if start and now - start > START_GRACE:
            _mark(sec, "start")
        await notify_once(bot, sec, "start", views.raid_started(raid))
        left = (end - now).total_seconds() if end else 0
        due = [k for s, k in RAID_REMINDERS if left <= s and k not in sec.get("sent", [])]
        if due and left > 0:
            text = views.raid_reminder(raid, await clan_members())
            if await notify_once(bot, sec, due[-1], text):
                _mark(sec, *due)
        status, _ = views.raid_status(raid)
        await _maybe_digest(bot, sec, config.RAID_DIGEST_MINUTES, start, status)
    else:
        _mark(sec, "start", *[k for _, k in RAID_REMINDERS])
        if end and now - end > END_GRACE * 2:
            _mark(sec, "end")
        if "end" not in sec.get("sent", []):
            await notify_once(bot, sec, "end", views.raid_summary(raid, await clan_members()))

    sec_store.save(sec)


# ══════════════════════════════════════════════════════════════════════════════
#  Супервизор
# ══════════════════════════════════════════════════════════════════════════════

async def _loop(name: str, tick, interval: int, bot, first_delay: int):
    h = _h(name)
    await asyncio.sleep(first_delay)
    while True:
        h["runs"] += 1
        try:
            await asyncio.wait_for(tick(bot), timeout=TICK_TIMEOUT)
            h["last_ok"] = utcnow()
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            h["last_error"] = f"цикл не уложился в {TICK_TIMEOUT} с (CoC API не отвечает?)"
            h["last_error_at"] = utcnow()
            logger.error(f"Монитор {name}: таймаут")
        except Exception as exc:
            h["last_error"] = f"{type(exc).__name__}: {exc}"
            h["last_error_at"] = utcnow()
            logger.exception(f"Монитор {name}: ошибка")
        await asyncio.sleep(interval)


def start(bot):
    """Запускает все мониторы. Ссылки на задачи храним, чтобы их не собрал GC."""
    for name, tick, interval, delay in (
        ("war", war_tick, 60, 5),
        ("cwl", cwl_tick, 90, 20),
        ("raid", raid_tick, 180, 35),
    ):
        task = asyncio.create_task(_loop(name, tick, interval, bot, delay), name=f"monitor:{name}")
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)
    logger.info("Мониторы войны, ЛВК и рейдов запущены")


def status_text() -> str:
    from fmt import e, esc, local
    lines = [fmt.title("admin", "Состояние бота"), ""]
    chats = storage.get_notify_chats()
    lines.append(fmt.line("chat", f"Чаты рассылки: <b>{len(chats)}</b>"
                                  + (f" ({', '.join(f'<code>{c}</code>' for c in chats)})" if chats
                                     else " — отправь /addchat в беседе клана")))
    lines.append(fmt.line("date", f"Папка данных: <code>{esc(storage.DATA_DIR)}</code>"))
    names = {"war": "Война", "cwl": "ЛВК", "raid": "Рейды", "broadcast": "Рассылка"}
    for key, label in names.items():
        h = HEALTH.get(key)
        if not h:
            lines.append(f"\n<b>{label}</b>: ещё не запускался")
            continue
        ok = local(h["last_ok"]) if h["last_ok"] else "ни разу"
        row = [f"\n<b>{label}</b>", f"последний успешный цикл: {ok}"]
        if h.get("note"):
            row.append(esc(h["note"]))
        if h.get("last_error"):
            row.append(f"{e('warn')} {esc(h['last_error'])[:300]} ({local(h['last_error_at'])})")
        lines.append("\n".join(row))
    alive = sum(1 for t in _TASKS if not t.done())
    lines.append(f"\nМониторов работает: <b>{alive}</b> из 3")
    return "\n".join(lines)
