try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass

import os
import re as _re

# Fallback: load tokens from config.py if env vars not set
try:
    import config as _cfg
    for _k in ("TELEGRAM_BOT_TOKEN", "COC_EMAIL", "COC_PASSWORD", "COC_API_KEY", "COC_PROXY"):
        if not os.environ.get(_k):
            val = getattr(_cfg, _k, "")
            if val:
                os.environ[_k] = val
except ImportError:
    pass

# ── Proxy setup for Clash of Clans API (static IP) ──────────────────────────
# Set COC_PROXY in config.py or env to a static HTTP/SOCKS proxy URL.
# Example: http://user:pass@proxy.example.com:8080
# This keeps the same IP across restarts so the API key never changes.
_COC_PROXY = os.environ.get("COC_PROXY") or os.environ.get("QUOTAGUARD_URL")
if _COC_PROXY:
    os.environ.setdefault("HTTP_PROXY",  _COC_PROXY)
    os.environ.setdefault("HTTPS_PROXY", _COC_PROXY)
    os.environ.setdefault("http_proxy",  _COC_PROXY)
    os.environ.setdefault("https_proxy", _COC_PROXY)
    # Patch aiohttp (used by coc.py) to honour env-based proxy
    try:
        import aiohttp as _aiohttp
        _orig_session = _aiohttp.ClientSession.__init__
        def _proxy_session(self, *a, **kw):
            kw.setdefault("trust_env", True)
            _orig_session(self, *a, **kw)
        _aiohttp.ClientSession.__init__ = _proxy_session
    except Exception:
        pass

import asyncio
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from collections import defaultdict
import coc
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, BotCommand, MenuButtonCommands
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ContextTypes, TypeHandler, ApplicationHandlerStop,
)
import storage
import stats_storage
import excel_builder
import card_builder
import fmt

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

IS_PRODUCTION = os.environ.get("REPLIT_DEPLOYMENT") == "1"

TOKEN        = os.environ["TELEGRAM_BOT_TOKEN"]
COC_EMAIL    = os.environ.get("COC_EMAIL", "")
COC_PASSWORD = os.environ.get("COC_PASSWORD", "")
COC_API_KEY  = os.environ.get("COC_API_KEY", "")
CLAN_TAG       = "#2R02GGRUJ"
CLAN_WEBSITE   = "https://www.warfilcoc.ru"
TG_GROUP_LINK  = "https://t.me/warfil_clan"   # ← замени на реальную ссылку беседы
WAR_NOTIFY_USERNAME = "feolar"                 # username получателя авто-рассылки войны

# ── Защита доступа: ID беседы клана ──────────────────────────────────────────
# Заполни после первого /addchat — возьми ID из /listchats.
# Если 0 — бот автоматически возьмёт первый зарегистрированный чат из /addchat.
CLAN_GROUP_CHAT_ID: int = 0

ROLE_ORDER = {
    "leader": 0,
    "coLeader": 1,
    "admin": 2,
    "member": 3,
}


def _norm_name(name: str) -> str:
    import unicodedata as _ud
    return _ud.normalize("NFC", name).lower()

coc_client = coc.Client()


def _extract_ip_from_error(err_str: str) -> str | None:
    """Извлекаем реальный исходящий IP из текста ошибки CoC."""
    m = _re.search(r'from IP (\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', err_str)
    return m.group(1) if m else None


# IP → unix-timestamp последнего появления (сохраняется между перезапусками)
_known_server_ips: dict[str, float] = {}

_COC_DEV_URL = "https://developer.clashofclans.com/api"
_KEY_NAME     = "warfil_bot"
_KNOWN_IPS_FILE = os.path.join("data", "known_ips.json")


def _load_known_ips() -> dict[str, float]:
    """Загружаем сохранённые IP; поддерживаем старый формат (list) → конвертируем."""
    try:
        os.makedirs("data", exist_ok=True)
        if os.path.exists(_KNOWN_IPS_FILE):
            import json as _json
            with open(_KNOWN_IPS_FILE, "r") as f:
                raw = _json.load(f)
            if isinstance(raw, list):          # старый формат — просто список IP
                return {ip: 0.0 for ip in raw}
            if isinstance(raw, dict):
                return {k: float(v) for k, v in raw.items()}
    except Exception:
        pass
    return {}


def _save_known_ips():
    """Сохраняем IP с временными метками."""
    try:
        import json as _json
        os.makedirs("data", exist_ok=True)
        with open(_KNOWN_IPS_FILE, "w") as f:
            _json.dump(_known_server_ips, f)
    except Exception as e:
        logger.warning(f"Не удалось сохранить known_ips: {e}")


def _register_ip(ip: str):
    """Регистрируем IP с текущим временем → для выбора 10 самых свежих."""
    import time as _time
    _known_server_ips[ip] = _time.time()
    _save_known_ips()


def _get_key_ips() -> list[str]:
    """Возвращает до 10 самых СВЕЖИХ индивидуальных IP для API-ключа.

    CoC API проверяет точное совпадение IP, CIDR нотация не поддерживается.
    Сортировка по timestamp последнего появления — самые свежие в начале.
    """
    by_recency = sorted(_known_server_ips, key=lambda ip: _known_server_ips[ip], reverse=True)
    return by_recency[:10]


# Загружаем сразу при старте модуля
_known_server_ips = _load_known_ips()


async def _rekey_coc(new_ip: str | None = None) -> bool:
    """Пересоздаём CoC API ключ, включая ВСЕ известные IP сервера.

    Работает напрямую через developer.clashofclans.com — не зависит от того,
    какой IP coc.py «видит» через JWT.
    Возвращает True при успехе, False при ошибке.
    """
    import aiohttp as _aio
    if not (COC_EMAIL and COC_PASSWORD):
        return False

    if new_ip:
        _register_ip(new_ip)  # сохраняем с timestamp сразу в файл

    # 10 самых свежих IP (по timestamp, а не по алфавиту)
    cidr_ranges: list[str] = _get_key_ips()
    if not cidr_ranges:
        cidr_ranges = ["127.0.0.1"]   # заглушка, не должна сработать

    logger.info(f"Пересоздаю CoC ключ с cidrRanges={cidr_ranges}")

    try:
        async with _aio.ClientSession() as s:
            # 1. Логин на developer site
            r = await s.post(f"{_COC_DEV_URL}/login",
                             json={"email": COC_EMAIL, "password": COC_PASSWORD})
            if r.status != 200:
                logger.error(f"Логин на developer site не удался: {r.status}")
                return False

            # 2. Получаем список ключей
            r = await s.post(f"{_COC_DEV_URL}/apikey/list")
            keys = (await r.json()).get("keys", [])

            # 3. Удаляем ВСЕ наши старые ключи (и от coc.py, и наш warfil_bot)
            OUR_NAMES = {_KEY_NAME, "Created with coc.py Client"}
            for key in keys:
                if key.get("name") in OUR_NAMES:
                    await s.post(f"{_COC_DEV_URL}/apikey/revoke",
                                 json={"id": key["id"]})
                    logger.info(f"Удалён ключ '{key['name']}' id={key['id']} cidr={key.get('cidrRanges')}")

            # 4. Создаём один новый ключ со всеми нужными CIDR
            payload = {
                "name"       : _KEY_NAME,
                "description": f"Warfil bot — updated {__import__('datetime').datetime.now():%Y-%m-%d %H:%M}",
                "cidrRanges" : cidr_ranges,
                "scopes"     : ["clash"],
            }
            r = await s.post(f"{_COC_DEV_URL}/apikey/create", json=payload)
            if r.status != 200:
                body = await r.text()
                logger.error(f"Создание ключа не удалось: {r.status} — {body}")
                return False

            key_data = await r.json()
            new_token = key_data["key"]["key"]
            logger.info(f"Новый ключ создан ✓ cidr={cidr_ranges}")

            # 5. Активируем новый токен через login_with_tokens —
            #    НЕ вызываем login(email,pass) чтобы coc.py не удалил наш CIDR ключ!
            await coc_client.login_with_tokens(new_token)
            logger.info("coc_client инициализирован через новый токен")
            return True
    except Exception as exc:
        logger.error(f"_rekey_coc error: {exc}", exc_info=True)
        return False


def _is_ip_error(e: Exception) -> bool:
    """True если ошибка — это IP-блокировка CoC API, а не реальный PrivateWarLog."""
    return "invalidIp" in str(e) or "accessDenied.invalidIp" in str(e)


# Замок чтобы только одна корутина пересоздавала ключ в один момент времени
_rekey_lock: asyncio.Lock | None = None


def _get_rekey_lock() -> asyncio.Lock:
    global _rekey_lock
    if _rekey_lock is None:
        _rekey_lock = asyncio.Lock()
    return _rekey_lock


async def _coc_safe(fn, *args, **kwargs):
    """Call a coc API function; on IP-Forbidden retry indefinitely until success.

    Каждый провал регистрирует новый IP, пересоздаёт ключ (используя /24 CIDR —
    одна запись покрывает всю подсеть Yandex Cloud), и повторяет запрос.
    Глобальный замок гарантирует что несколько параллельных команд не перетирают
    ключ одновременно — все ждут пока один пересоздаст.
    Также перехватывает coc.PrivateWarLog когда coc.py неверно оборачивает
    403/invalidIp (это происходит для get_league_group).
    """
    attempt = 0
    last_exc: Exception | None = None

    while True:
        try:
            return await fn(*args, **kwargs)
        except (coc.errors.Forbidden, coc.errors.HTTPException, coc.errors.PrivateWarLog) as e:
            if not (_is_ip_error(e) and COC_EMAIL and COC_PASSWORD):
                raise  # не IP-ошибка → пробрасываем как есть

            bad_ip = _extract_ip_from_error(str(e))
            attempt += 1
            last_exc = e

            logger.warning(f"CoC IP ошибка попытка {attempt} (IP={bad_ip}) — обновляю ключ...")

            # Только одна корутина пересоздаёт ключ, остальные ждут
            async with _get_rekey_lock():
                # Пока ждали замок — возможно другая корутина уже пересоздала ключ.
                # Сначала регистрируем IP, потом пробуем пересоздать.
                if bad_ip:
                    _register_ip(bad_ip)

                rekey_ok = False
                for rekey_attempt in range(3):  # до 3 попыток пересоздания (portal может временно глючить)
                    try:
                        rekey_ok = await _rekey_coc()
                        if rekey_ok:
                            break
                        logger.warning(f"Пересоздание ключа не удалось (попытка {rekey_attempt+1}/3), жду 3с...")
                        await asyncio.sleep(3)
                    except Exception as rekey_err:
                        logger.error(f"_rekey_coc ошибка: {rekey_err}", exc_info=True)
                        await asyncio.sleep(3)

                if not rekey_ok:
                    logger.error("Все попытки пересоздать ключ провалились — жду 10с перед следующим запросом")
                    await asyncio.sleep(10)
                    continue

            # Небольшая пауза чтобы новый токен "осел" в CoC
            await asyncio.sleep(1)


# Active auto-update tasks: chat_id -> asyncio.Task
_kv_tasks: dict[int, asyncio.Task] = {}

CLAN_TAG_ENCODED = CLAN_TAG.replace("#", "%23")
KV_BUTTONS = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("🌐 Сайт клана", url=CLAN_WEBSITE),
        InlineKeyboardButton("⚔️ Открыть игру", url=f"https://link.clashofclans.com/en?action=OpenClanProfile&tag={CLAN_TAG_ENCODED}"),
    ]
])


async def build_war_message() -> tuple[str, bool]:
    """Fetch current war and return (text, should_keep_updating)."""
    war = await _coc_safe(coc_client.get_current_war, CLAN_TAG)

    if war is None or war.state == "notInWar":
        return "🏳️ Клан сейчас не в клановой войне.", False

    if war.state == "preparation":
        start = war.start_time.time.strftime("%d.%m в %H:%M") if war.start_time else "скоро"
        return f"⚙️ Идёт подготовка к войне. Бой начнётся {start} (UTC).", False

    attacks_per_member = war.attacks_per_member or 2
    pending = []
    for member in war.clan.members:
        used = len(member.attacks) if member.attacks else 0
        remaining = attacks_per_member - used
        if remaining > 0:
            pending.append((member, used, remaining))
    pending.sort(key=lambda x: x[1])

    state_label = "⚔️ ВОЙНА ИДЁТ" if war.state == "inWar" else "🏁 ВОЙНА ЗАВЕРШЕНА"
    our_stars = war.clan.stars
    their_stars = war.opponent.stars

    time_str = ""
    if war.state == "inWar" and war.end_time:
        now = datetime.utcnow()
        diff = war.end_time.time - now
        total_sec = max(int(diff.total_seconds()), 0)
        h, m = divmod(total_sec // 60, 60)
        time_str = f"\n⏱ До конца: <b>{h}ч {m}мин</b>"

    lines = [
        f"<b>{state_label}</b>",
        fmt.DIV,
        f"🏰 <b>{war.clan.name}</b>  ⚔️  <b>{war.opponent.name}</b>",
        f"👥 {war.team_size}v{war.team_size}  ·  ⭐ {our_stars} vs {their_stars}" + time_str,
    ]

    # Attack progress bar
    total_attacks = war.team_size * attacks_per_member
    used_attacks  = total_attacks - sum(r for _, _, r in pending)
    lines.append(f"\n⚔️ Атаки: {fmt.attacks_bar(used_attacks, total_attacks)}  <i>{used_attacks}/{total_attacks}</i>")

    if not pending:
        lines.append(f"\n{fmt.ok('<b>Все использовали атаки!</b>')}")
    else:
        zero_used = [(mb, r) for mb, u, r in pending if u == 0]
        one_used  = [(mb, r) for mb, u, r in pending if u == 1]
        tg_map = storage.get_tg_username_map()

        if zero_used:
            lines.append(f"\n🔴 <b>Нет атак ({len(zero_used)}):</b>")
            for member, _ in zero_used:
                tg = tg_map.get(_norm_name(member.name))
                lines.append(fmt.member_line(member.name, tg=tg))

        if one_used:
            lines.append(f"\n🟡 <b>Осталась 1 ({len(one_used)}):</b>")
            for member, _ in one_used:
                tg = tg_map.get(_norm_name(member.name))
                lines.append(fmt.member_line(member.name, tg=tg))

    lines.append(fmt.footer())
    keep_updating = war.state == "inWar"
    return "\n".join(lines), keep_updating


def format_last_seen(last_seen_str: str | None) -> str:
    if not last_seen_str:
        return ""
    try:
        dt = datetime.fromisoformat(last_seen_str)
        now = datetime.now()
        diff = now - dt
        minutes = int(diff.total_seconds() // 60)
        if minutes < 1:
            return " • 🟢 только что"
        elif minutes < 60:
            return f" • 🕐 {minutes} мин. назад"
        elif minutes < 1440:
            hours = minutes // 60
            return f" • 🕐 {hours} ч. назад"
        else:
            days = minutes // 1440
            return f" • 💤 {days} дн. назад"
    except Exception:
        return ""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.first_name or "боец"

    # Save chat_id for the war broadcast target when they DM the bot
    if (update.effective_chat.type == "private"
            and user.username
            and user.username.lower() == WAR_NOTIFY_USERNAME.lower()):
        storage.save_notify_chat(update.effective_chat.id)
        logger.info(f"Saved war notify chat_id: {update.effective_chat.id}")

    # Fetch live clan data for welcome card
    try:
        clan = await _coc_safe(coc_client.get_clan, CLAN_TAG)
        stats_block = (
            f"\n{fmt.DIVs}\n\n"
            f"{fmt.stat_line('Уровень клана', str(clan.level))}\n"
            f"{fmt.stat_line('Состав', f'{clan.member_count} / 50')}\n"
            f"{fmt.stat_line('Побед в войнах', str(clan.war_wins))}\n"
        )
    except Exception:
        stats_block = ""

    text = (
        f"👋 Привет, <b>{name}</b>!\n"
        f"{fmt.DIV}\n"
        f"{fmt.clan_header()}\n\n"
        "<i>Warfil — это не просто клан.\n"
        "Это боевое братство, где стратегия\n"
        "и честь важнее, чем числа на экране.\n"
        "Мы атакуем вместе. Побеждаем достойно.</i>"
        f"{stats_block}"
        f"\n{fmt.DIV}\n"
        "Все команды — в меню ниже 👇\n"
        f"{fmt.footer()}"
    )

    inline_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌐 Сайт клана", url=CLAN_WEBSITE),
            InlineKeyboardButton("💬 Беседа клана", url=TG_GROUP_LINK),
        ]
    ])

    await update.message.reply_text(text, parse_mode="HTML", reply_markup=inline_kb)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        f"📖 <b>Команды бота Warfil</b>\n"
        f"{fmt.DIV}\n\n"
        f"📋 /team\n"
        f"   Список участников клана\n\n"
        f"⚔️ /kv\n"
        f"   Атаки в клановой войне\n\n"
        f"🏆 /cwl\n"
        f"   Лига войн клана\n\n"
        f"📊 /statistic\n"
        f"   Статистика клана (Excel)\n\n"
        f"🎨 /card\n"
        f"   Карточка клана\n\n"
        f"🔗 /register &lt;ник&gt;\n"
        f"   Привязать аккаунт CoC\n"
        f"   <i>Пример: /register WarriorKing</i>\n\n"
        f"{fmt.DIV}\n"
        f"🛡 <b>Для администраторов</b>\n\n"
        f"▸ /link @tg НикВCoC\n"
        f"▸ /unlink НикВCoC\n"
        f"▸ /links — все привязки\n"
        f"{fmt.footer()}"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def register_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import unicodedata
    from html import escape as _esc

    # Берём ник напрямую из текста сообщения — context.args иногда теряет спецсимволы
    raw_text = update.message.text or ""
    # Убираем команду (/register или /register@botname) и пробел после неё
    parts = raw_text.split(None, 1)
    coc_name_raw = parts[1].strip() if len(parts) > 1 else ""

    if not coc_name_raw:
        await update.message.reply_text(
            "Укажи своё имя в игре.\n"
            "Пример: /register WarriorKing"
        )
        return

    # NFC нормализация — обязательна для Unicode никнеймов с со спецсимволами
    coc_name = unicodedata.normalize("NFC", coc_name_raw)

    user_id = update.effective_user.id
    tg_username = update.effective_user.username
    storage.register_player(user_id, coc_name, tg_username)
    storage.update_last_seen(user_id)
    linked = f" · @{tg_username}" if tg_username else ""
    await update.message.reply_text(
        f"🔗 <b>Аккаунт привязан</b>\n"
        f"{fmt.DIV}\n\n"
        f"▸ Ник в игре: <b>{_esc(coc_name)}</b>{linked}\n\n"
        f"Теперь ты виден в /team со своим Telegram.",
        parse_mode="HTML"
    )


async def online_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    coc_name = storage.update_last_seen(user_id)
    if coc_name:
        await update.message.reply_text(
            f"✅ <b>{coc_name}</b>, твоя активность отмечена!",
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            "Ты ещё не зарегистрирован.\n"
            "Используй /register <имя в игре> чтобы привязать аккаунт."
        )


async def team_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Загружаю список клана...")
    try:
        clan = await _coc_safe(coc_client.get_clan, CLAN_TAG)
        members_sorted = sorted(
            clan.members,
            key=lambda m: (ROLE_ORDER.get(m.role.value, 9), m.name.lower())
        )

        # Group members by role (store full member objects)
        groups: dict[str, list] = defaultdict(list)
        for member in members_sorted:
            groups[member.role.value].append(member)

        ROLE_HEADERS = {
            "leader":   "👑 <b>Лидер</b>",
            "coLeader": "🔱 <b>Соруководители</b>",
            "admin":    "🌿 <b>Старейшины</b>",
            "member":   "🔹 <b>Участники</b>",
        }

        tg_map = storage.get_tg_username_map()

        lines = [
            f"🏰 <b>{clan.name}</b>  ·  👥 {clan.member_count}/50",
            fmt.DIV,
        ]

        for role_key in ["leader", "coLeader", "admin", "member"]:
            members = groups.get(role_key)
            if not members:
                continue
            lines.append("")
            count_str = f"  <i>({len(members)})</i>" if role_key != "leader" else ""
            lines.append(ROLE_HEADERS[role_key] + count_str)
            for m in members:
                tg = tg_map.get(_norm_name(m.name))
                lines.append(fmt.member_line(m.name, m.town_hall, tg))

        lines.append(f"\n{fmt.footer()}")

        await msg.edit_text("\n".join(lines), parse_mode="HTML")

    except coc.NotFound:
        await msg.edit_text("❌ Клан не найден. Проверь тег клана.")
    except Exception as e:
        logger.error(f"Ошибка при получении данных клана: {e}", exc_info=True)
        await msg.edit_text("❌ Не удалось загрузить данные. Попробуй позже.")


async def _is_admin(update: Update) -> bool:
    """True if the sender is a group admin/creator, or if used in a private chat."""
    chat = update.effective_chat
    if chat.type == "private":
        return True
    member = await chat.get_member(update.effective_user.id)
    return member.status in ("administrator", "creator")


# ── Защита доступа ────────────────────────────────────────────────────────────

async def _clan_group_id(bot) -> int | None:
    """Return the registered clan group chat ID (constant or first notify chat)."""
    if CLAN_GROUP_CHAT_ID:
        return CLAN_GROUP_CHAT_ID
    chats = storage.get_notify_chats()
    # Return only if it's actually a group (negative ID in Telegram)
    for cid in chats:
        if cid < 0:
            return cid
    return None


async def clan_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Security gate — registered in group=-1, runs before every command/message.

    Rules:
    • Group / supergroup: only the registered clan group is served, all others are silently ignored.
    • Private chat: only users who are members of the clan group may use the bot;
      others receive a rejection message with the clan website link.
    • Bot admin (@feolar) is always allowed everywhere.
    """
    if not update.effective_chat or not update.effective_user:
        return

    chat = update.effective_chat
    user = update.effective_user

    # Bot admin bypass — always allow
    if (user.username or "").lower() == WAR_NOTIFY_USERNAME.lower():
        return

    gid = await _clan_group_id(context.bot)

    # ── Группы: только беседа клана ──────────────────────────────────────────
    if chat.type in ("group", "supergroup"):
        if gid and chat.id != gid:
            raise ApplicationHandlerStop  # чужая беседа — молча игнорируем
        return  # своя беседа — разрешаем

    # ── Личные сообщения: проверяем членство в беседе клана ──────────────────
    if chat.type == "private":
        if not gid:
            return  # защита ещё не настроена (addchat не запускали)

        allowed = False
        try:
            member = await context.bot.get_chat_member(gid, user.id)
            allowed = member.status not in ("left", "kicked", "banned")
        except Exception:
            allowed = False  # не удалось проверить → блокируем

        if not allowed:
            if update.message:
                await update.message.reply_text(
                    "👋 <b>Вы не являетесь участником беседы клана Warfil.</b>\n\n"
                    "Для вступления в клан перейдите по ссылке:\n"
                    f"🌐 {CLAN_WEBSITE}\n\n"
                    "и подайте анкету на вступление в клан.",
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            raise ApplicationHandlerStop


async def link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /link @tg_username Ник в CoC"""
    from html import escape as _esc
    import unicodedata

    msg = update.message
    if not msg:
        return

    try:
        try:
            is_admin = await _is_admin(update)
        except Exception:
            is_admin = True  # при ошибке проверки — разрешаем (ЛС/ошибка API)

        if not is_admin:
            await msg.reply_text("❌ Только администраторы могут использовать эту команду.")
            return

        # Парсим полный текст — context.args может терять спецсимволы и mentions
        raw = (msg.text or "").strip()
        # Убираем команду (/link или /link@botname)
        body = raw.split(None, 1)[1].strip() if len(raw.split(None, 1)) > 1 else ""

        if not body:
            await msg.reply_text(
                "Использование: /link @telegram НикВCoC\n"
                "Примеры:\n"
                "  /link @feolar Fanon\n"
                "  /link @feolar ɢʀᴇꜱʜɴɪᴋ⇝ ™\n\n"
                "Один @telegram можно привязать к нескольким никам."
            )
            return

        # body = "@username ник" — разбиваем на части
        parts = body.split(None, 1)
        if len(parts) < 2:
            await msg.reply_text(
                "Укажи оба аргумента.\n"
                "Пример: /link @feolar Fanon"
            )
            return

        tg_username = parts[0].lstrip("@")
        coc_name = unicodedata.normalize("NFC", parts[1].strip())

        storage.link_player(coc_name, tg_username)
        logger.info(f"Привязка: {coc_name!r} → @{tg_username}")
        await msg.reply_text(
            f"✅ <b>{_esc(coc_name)}</b> привязан к @{tg_username}",
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка /link: {e}", exc_info=True)
        await msg.reply_text(f"❌ Ошибка: {e}")


async def unlink_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /unlink Ник в CoC"""
    from html import escape as _esc
    import unicodedata

    msg = update.message
    if not msg:
        return

    try:
        try:
            is_admin = await _is_admin(update)
        except Exception:
            is_admin = True

        if not is_admin:
            await msg.reply_text("❌ Только администраторы могут использовать эту команду.")
            return

        raw = (msg.text or "").strip()
        body = raw.split(None, 1)[1].strip() if len(raw.split(None, 1)) > 1 else ""

        if not body:
            await msg.reply_text("Использование: /unlink НикВCoC\nПример: /unlink Fanon")
            return

        coc_name = unicodedata.normalize("NFC", body)
        removed = storage.unlink_player(coc_name)
        if removed:
            await msg.reply_text(f"✅ Привязка для <b>{_esc(coc_name)}</b> удалена.", parse_mode="HTML")
        else:
            await msg.reply_text(f"⚠️ Привязка для <b>{_esc(coc_name)}</b> не найдена.", parse_mode="HTML")

    except Exception as e:
        logger.error(f"Ошибка /unlink: {e}", exc_info=True)
        await msg.reply_text(f"❌ Ошибка: {e}")


async def links_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all current links (admin only)."""
    if not await _is_admin(update):
        await update.message.reply_text("❌ Только администраторы могут использовать эту команду.")
        return

    all_links = storage.get_all_links()
    if not all_links:
        await update.message.reply_text("Привязок пока нет. Используй /link @telegram НикВCoC")
        return

    lines = ["📋 <b>Текущие привязки:</b>", ""]
    for coc_name, tg_user in sorted(all_links.items(), key=lambda x: x[1]):
        lines.append(f"  {coc_name} → @{tg_user}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def kv_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    # Cancel any existing auto-update task for this chat
    existing = _kv_tasks.get(chat_id)
    if existing and not existing.done():
        existing.cancel()

    msg = await update.message.reply_text("⏳ Загружаю данные войны...")

    try:
        text, keep_updating = await build_war_message()
        await msg.edit_text(text, parse_mode="HTML", reply_markup=KV_BUTTONS)
    except coc.PrivateWarLog:
        await msg.edit_text("🔒 Журнал войны клана закрыт.")
        return
    except coc.NotFound:
        await msg.edit_text("❌ Клан не найден.")
        return
    except Exception as e:
        logger.error(f"Ошибка /kv: {e}")
        await msg.edit_text("❌ Не удалось загрузить данные войны. Попробуй позже.")
        return

    if not keep_updating:
        return

    async def auto_update():
        while True:
            await asyncio.sleep(5)
            try:
                new_text, still_going = await build_war_message()
                try:
                    await msg.edit_text(new_text, parse_mode="HTML", reply_markup=KV_BUTTONS)
                except Exception:
                    pass  # Message not modified or deleted — skip silently
                if not still_going:
                    break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Ошибка авто-обновления /kv: {e}")
                break

    task = asyncio.create_task(auto_update())
    _kv_tasks[chat_id] = task


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    # Route reply-keyboard button presses to the right commands
    if text == "📋 Список клана":
        await team_command(update, context)
    elif text == "❓ Помощь":
        await help_command(update, context)
    elif text == "🔗 Привязать аккаунт":
        await update.message.reply_text(
            "Чтобы привязать свой аккаунт, напиши:\n"
            "/register &lt;твой ник в CoC&gt;\n\n"
            "<i>Пример: /register WarriorKing</i>",
            parse_mode="HTML"
        )
    else:
        pass  # ignore other text messages


async def send_war_start(bot, chat_id: int, war):
    opponent = war.opponent.name if war.opponent else "противника"
    text = (
        f"⚔️ <b>ВОЙНА НАЧАЛАСЬ!</b>\n"
        f"{fmt.DIV}\n\n"
        f"🏰 <b>Warfil</b>  vs  <b>{opponent}</b>\n"
        f"👥 {war.team_size} на {war.team_size}\n\n"
        f"{fmt.DIVs}\n"
        f"💥 Боевой день открыт!\n"
        f"▸ Атакуйте с умом\n"
        f"▸ Сражайтесь с честью\n\n"
        f"<b>Удачи бойцам Warfil! 💪</b>"
    )
    await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=KV_BUTTONS)


async def send_war_end(bot, chat_id: int, war):
    attacks_per_member = war.attacks_per_member or 2
    tg_map = storage.get_tg_username_map()

    attacked = []
    missed = []
    for member in war.clan.members:
        used = len(member.attacks) if member.attacks else 0
        if used >= attacks_per_member:
            attacked.append(member)
        else:
            missed.append((member, used))

    our_stars = war.clan.stars
    their_stars = war.opponent.stars
    if our_stars > their_stars:
        result_line = "🏆 <b>Победа!</b>"
    elif our_stars < their_stars:
        result_line = "😔 <b>Поражение.</b>"
    else:
        result_line = "🤝 <b>Ничья.</b>"

    lines = [
        f"🏁 <b>ВОЙНА ЗАВЕРШЕНА</b>",
        fmt.DIV,
        f"🏰 <b>Warfil</b>  vs  <b>{war.opponent.name}</b>",
        f"⭐ {our_stars}  vs  {their_stars}  ·  {result_line}",
    ]

    if attacked:
        lines.append(f"\n✅ <b>Атаковали ({len(attacked)}):</b>")
        for member in attacked:
            tg = tg_map.get(_norm_name(member.name))
            lines.append(fmt.member_line(member.name, tg=tg))
        lines.append("\n🔥 <i>Молодцы! Вы — гордость клана!</i>")

    if missed:
        lines.append(f"\n❌ <b>Не атаковали ({len(missed)}):</b>")
        for member, used in missed:
            tg = tg_map.get(_norm_name(member.name))
            used_str = f" ({used}/{attacks_per_member})" if used > 0 else ""
            lines.append(fmt.member_line(f"{member.name}{used_str}", tg=tg))
        lines.append(f"\n{fmt.warn('<b>Игроки без атак — кандидаты на кик!</b>')}")

    # Save stats for Excel
    result_str = "win" if our_stars > their_stars else ("lose" if our_stars < their_stars else "tie")
    end_str = war.end_time.time.strftime("%Y-%m-%dT%H:%M:%S") if war.end_time else ""
    member_stats = []
    for member in war.clan.members:
        used = len(member.attacks) if member.attacks else 0
        member_stats.append({"name": member.name, "attacks_used": used, "attacks_max": attacks_per_member})
    stats_storage.save_war_result(
        end_time=end_str, opponent=war.opponent.name,
        result=result_str, our_stars=our_stars, their_stars=their_stars,
        team_size=war.team_size, attacks_per_member=attacks_per_member,
        members=member_stats,
    )

    await bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode="HTML", reply_markup=KV_BUTTONS)


async def _try_save_war_ended(war):
    """If war is warEnded and not yet saved locally, save it now."""
    if not war or war.state != "warEnded":
        return
    try:
        clan_tag_clean = CLAN_TAG.lstrip("#").upper()
        our = war.clan if war.clan.tag.lstrip("#").upper() == clan_tag_clean else war.opponent
        opp = war.opponent if our is war.clan else war.clan
        end_iso = war.end_time.time.isoformat() if war.end_time else ""
        if not end_iso:
            return
        existing = stats_storage.get_war_history()
        if any(w.get("end_time", "")[:16] == end_iso[:16] for w in existing):
            return  # already saved
        # Determine result
        if our.stars > opp.stars:
            result = "win"
        elif our.stars < opp.stars:
            result = "lose"
        elif (our.destruction or 0) > (opp.destruction or 0):
            result = "win"
        elif (our.destruction or 0) < (opp.destruction or 0):
            result = "lose"
        else:
            result = "tie"
        members = []
        for m in (our.members or []):
            members.append({
                "name": m.name,
                "th": getattr(m, "town_hall", 0),
                "attacks_used": len(m.attacks) if m.attacks else 0,
                "attacks_max": war.attacks_per_member,
            })
        stats_storage.save_war_result(
            end_time=end_iso,
            opponent=opp.name,
            result=result,
            our_stars=our.stars,
            their_stars=opp.stars,
            team_size=war.team_size,
            attacks_per_member=war.attacks_per_member,
            members=members,
        )
        logger.info(f"Auto-saved warEnded: vs {opp.name} at {end_iso[:10]}")
    except Exception as e:
        logger.warning(f"_try_save_war_ended: {e}")


async def war_state_monitor(bot):
    """Poll war state every 60 s; send messages on transitions."""
    previous_state = storage.get_war_state()  # restore across restarts

    # On startup: if war is already ended, save data immediately (bot may have missed the transition)
    try:
        startup_war = await _coc_safe(coc_client.get_current_war, CLAN_TAG)
        if startup_war and startup_war.state == "warEnded":
            await _try_save_war_ended(startup_war)
    except Exception:
        pass

    while True:
        await asyncio.sleep(60)
        try:
            chat_ids = storage.get_notify_chats()
            if not chat_ids:
                continue

            war = await _coc_safe(coc_client.get_current_war, CLAN_TAG)
            current_state = war.state.value if war and war.state else "notInWar"

            if previous_state is not None and previous_state != current_state:
                for chat_id in chat_ids:
                    try:
                        if previous_state == "preparation" and current_state == "inWar":
                            await send_war_start(bot, chat_id, war)
                        elif previous_state == "inWar" and current_state == "warEnded":
                            await send_war_end(bot, chat_id, war)
                    except Exception as e:
                        logger.warning(f"war_state_watcher: ошибка отправки в {chat_id}: {e}")

            # Also auto-save if we see warEnded state (belt-and-suspenders)
            if current_state == "warEnded":
                await _try_save_war_ended(war)

            if current_state != previous_state:
                storage.save_war_state(current_state)
                previous_state = current_state

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"war_state_monitor: {e}")


async def war_auto_broadcast(bot):
    """Send a new war status message to WAR_NOTIFY_CHAT every 7200 seconds while war is active."""
    while True:
        await asyncio.sleep(7200)  # 2 часа — ждём СНАЧАЛА, чтобы не слать при каждом перезапуске
        try:
            chat_ids = storage.get_notify_chats()
            if not chat_ids:
                logger.debug("war_auto_broadcast: chat_ids not set yet, waiting...")
                continue
            text, keep_going = await build_war_message()
            if keep_going:
                for chat_id in chat_ids:
                    try:
                        await bot.send_message(
                            chat_id=chat_id,
                            text=text,
                            parse_mode="HTML",
                            reply_markup=KV_BUTTONS,
                        )
                    except Exception as e:
                        logger.warning(f"war_auto_broadcast: ошибка отправки в {chat_id}: {e}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"war_auto_broadcast: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  CAPITAL RAIDS  —  рейды столицы клана
# ══════════════════════════════════════════════════════════════════════════════

async def _get_current_raid():
    """Fetch the most recent raid log entry. Returns None if no raids found."""
    try:
        raid_log = await _coc_safe(coc_client.get_raid_log, CLAN_TAG, limit=1)
        async for entry in raid_log:
            return entry
    except Exception:
        pass
    return None


async def build_raid_message() -> tuple[str, bool]:
    """Build capital raid status message. Returns (text, keep_updating)."""
    raid = await _get_current_raid()

    if raid is None:
        return "🏛 Нет данных о рейдах столицы.", False

    state = getattr(raid, "state", "ended")
    if state != "ongoing":
        return "🏛 Рейды столицы сейчас не идут.", False

    total_loot = getattr(raid, "total_loot", 0) or 0
    members = list(getattr(raid, "members", None) or [])
    attack_count = getattr(raid, "attack_count", 0) or 0
    districts_destroyed = getattr(raid, "enemy_districts_destroyed", 0) or 0

    time_str = ""
    if getattr(raid, "end_time", None):
        from datetime import datetime as _dt
        diff = raid.end_time.time - _dt.utcnow()
        total_sec = max(int(diff.total_seconds()), 0)
        h, m = divmod(total_sec // 60, 60)
        time_str = f"⏱ До конца: <b>{h}ч {m}мин</b>\n"

    start_str = ""
    if getattr(raid, "start_time", None):
        start_str = f"🗓 Начались: <b>{raid.start_time.time.strftime('%d.%m')}</b>\n"

    # Sort members by loot descending
    members_sorted = sorted(
        members,
        key=lambda m: getattr(m, "capital_resources_looted", 0) or 0,
        reverse=True,
    )

    tg_map = storage.get_tg_username_map()

    lines = [
        "🏛 <b>РЕЙДЫ СТОЛИЦЫ ИДУТ</b>",
        fmt.DIV,
        start_str + time_str +
        f"💰 Собрано: <b>{total_loot:,}".replace(",", " ") + " золота</b>\n"
        f"⚔️ Атак: <b>{attack_count}</b>  ·  🏰 Районов взято: <b>{districts_destroyed}</b>",
    ]

    if members_sorted:
        lines.append(f"\n👥 <b>Участников: {len(members_sorted)}</b>")
        for i, m in enumerate(members_sorted[:10], 1):
            name = getattr(m, "name", "?")
            loot = getattr(m, "capital_resources_looted", 0) or 0
            atks = getattr(m, "attacks", 0) or 0
            atk_limit = (getattr(m, "attack_limit", 5) or 5) + (getattr(m, "bonus_attack_limit", 0) or 0)
            tg = tg_map.get(_norm_name(name))
            tag = f" (@{tg})" if tg else ""
            lines.append(f"  {i}. {name}{tag} — {loot:,}💎 · {atks}/{atk_limit}atk".replace(",", " "))

    lines.append(fmt.footer())
    return "\n".join(lines), True


async def send_raid_start(bot, chat_id: int, raid):
    """Notification when raid weekend begins."""
    start_str = ""
    if getattr(raid, "end_time", None):
        start_str = f"⏳ Закончатся: {raid.end_time.time.strftime('%d.%m в %H:%M')} (UTC)\n"

    text = (
        f"🏛 <b>РЕЙДЫ СТОЛИЦЫ НАЧАЛИСЬ!</b>\n"
        f"{fmt.DIV}\n\n"
        f"{start_str}\n"
        f"{fmt.DIVs}\n"
        f"💎 Время грабить вражескую столицу!\n"
        f"▸ У каждого минимум <b>5 атак</b>\n"
        f"▸ Сначала добиваем незавершённые районы\n"
        f"▸ Не теряем ни одной атаки!\n\n"
        f"<b>Warfil — в атаку! 💪</b>"
    )
    await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")


async def send_raid_end(bot, chat_id: int, raid):
    """Notification when raid weekend ends with summary."""
    total_loot = getattr(raid, "total_loot", 0) or 0
    off_reward = getattr(raid, "offensive_reward", 0) or 0
    def_reward = getattr(raid, "defensive_reward", 0) or 0
    attack_count = getattr(raid, "attack_count", 0) or 0
    districts = getattr(raid, "enemy_districts_destroyed", 0) or 0
    members = list(getattr(raid, "members", None) or [])

    members_sorted = sorted(
        members,
        key=lambda m: getattr(m, "capital_resources_looted", 0) or 0,
        reverse=True,
    )
    tg_map = storage.get_tg_username_map()

    # Кто не атаковал вообще
    try:
        clan = await _coc_safe(coc_client.get_clan, CLAN_TAG)
        clan_names = {_norm_name(m.name) for m in (clan.members or [])}
        raided_names = {_norm_name(getattr(m, "name", "")) for m in members}
        missed_names = clan_names - raided_names
    except Exception:
        missed_names = set()

    lines = [
        "🏁 <b>РЕЙДЫ СТОЛИЦЫ ЗАВЕРШЕНЫ</b>",
        fmt.DIV,
        f"💰 Собрано: <b>{total_loot:,}".replace(",", " ") + " золота</b>",
        f"⚔️ Атак: <b>{attack_count}</b>  ·  🏰 Районов взято: <b>{districts}</b>",
        f"🎁 Награда: <b>{off_reward}</b> атак. / <b>{def_reward}</b> защ.",
    ]

    if members_sorted:
        lines.append(f"\n✅ <b>Атаковали ({len(members_sorted)}):</b>")
        for m in members_sorted:
            name = getattr(m, "name", "?")
            loot = getattr(m, "capital_resources_looted", 0) or 0
            atks = getattr(m, "attacks", 0) or 0
            atk_limit = (getattr(m, "attack_limit", 5) or 5) + (getattr(m, "bonus_attack_limit", 0) or 0)
            tg = tg_map.get(_norm_name(name))
            lines.append(fmt.member_line(f"{name} — {loot:,}💎 · {atks}/{atk_limit}atk".replace(",", " "), tg=tg))
        lines.append(f"\n🔥 <i>Отличная работа, участники!</i>")

    if missed_names:
        lines.append(f"\n❌ <b>Не участвовали ({len(missed_names)}):</b>")
        for norm in sorted(missed_names):
            tg = tg_map.get(norm)
            display = norm
            for m in members_sorted:
                if _norm_name(getattr(m, "name", "")) == norm:
                    display = m.name
                    break
            lines.append(fmt.member_line(display, tg=tg))
        lines.append(f"\n{fmt.warn('<b>Без рейда — кандидаты на проверку!</b>')}")

    lines.append(fmt.footer())
    await bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode="HTML")


async def raid_state_monitor(bot):
    """Poll capital raid state every 5 min; notify on start/end transitions."""
    saved = storage.get_raid_state()
    prev_state = saved.get("state", "ended")
    prev_start = saved.get("start_iso", "")

    while True:
        await asyncio.sleep(300)  # 5 минут
        try:
            chat_ids = storage.get_notify_chats()
            if not chat_ids:
                continue

            raid = await _get_current_raid()
            if raid is None:
                continue

            current_state = getattr(raid, "state", "ended")
            current_start = ""
            if getattr(raid, "start_time", None):
                current_start = raid.start_time.time.isoformat()

            # Определяем переход состояния по state и start_time (чтобы не слать повторно)
            new_raid_started = (
                current_state == "ongoing"
                and (prev_state == "ended" or current_start != prev_start)
            )
            raid_ended = (prev_state == "ongoing" and current_state == "ended")

            if new_raid_started:
                for chat_id in chat_ids:
                    try:
                        await send_raid_start(bot, chat_id, raid)
                    except Exception as e:
                        logger.warning(f"raid_monitor: ошибка отправки в {chat_id}: {e}")

            elif raid_ended:
                for chat_id in chat_ids:
                    try:
                        await send_raid_end(bot, chat_id, raid)
                    except Exception as e:
                        logger.warning(f"raid_monitor: ошибка отправки в {chat_id}: {e}")

            if current_state != prev_state or current_start != prev_start:
                storage.save_raid_state(current_state, current_start)
                prev_state = current_state
                prev_start = current_start

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"raid_state_monitor: {e}")


async def raid_auto_broadcast(bot):
    """Send raid status every 4 hours while raid is active."""
    while True:
        await asyncio.sleep(14400)  # 4 часа
        try:
            chat_ids = storage.get_notify_chats()
            if not chat_ids:
                continue
            text, keep_going = await build_raid_message()
            if keep_going:
                for chat_id in chat_ids:
                    try:
                        await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
                    except Exception as e:
                        logger.warning(f"raid_auto_broadcast: ошибка отправки в {chat_id}: {e}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"raid_auto_broadcast: {e}")


def _is_feolar(update: Update) -> bool:
    user = update.effective_user
    return user is not None and (user.username or "").lower() == WAR_NOTIFY_USERNAME.lower()


async def teststart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_feolar(update):
        await update.message.reply_text("❌ Нет доступа.")
        return
    try:
        war = await _coc_safe(coc_client.get_current_war, CLAN_TAG)
        if war is None or war.state == "notInWar":
            await update.message.reply_text("⚠️ Клан не в войне — нет данных для теста.")
            return
        await send_war_start(context.bot, update.effective_chat.id, war)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def testend_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_feolar(update):
        await update.message.reply_text("❌ Нет доступа.")
        return
    try:
        war = await _coc_safe(coc_client.get_current_war, CLAN_TAG)
        if war is None or war.state == "notInWar":
            await update.message.reply_text("⚠️ Клан не в войне — нет данных для теста.")
            return
        await send_war_end(context.bot, update.effective_chat.id, war)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def statistic_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Собираю статистику, подождите...")
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
                if cw.state == "warEnded":
                    await _try_save_war_ended(cw)
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
            caption=(
                f"📊 <b>Статистика Warfil</b>\n"
                f"{fmt.DIV}\n\n"
                f"⚔️ КВ — {len(war_history)} войн\n"
                f"🏆 ЛВК — {len(cwl_seasons)} сезон(а), {n_cwl_rounds} раундов\n"
                f"🏛 Рейды столицы — последний рейд\n"
                f"{fmt.footer()}"
            ),
            parse_mode="HTML",
        )
        await msg.delete()
    except Exception as e:
        logger.error(f"Ошибка /statistic: {e}", exc_info=True)
        await msg.edit_text("❌ Не удалось сгенерировать файл. Попробуй позже.")


async def get_cwl_clan_war(war_round=coc.WarRound.current_war):
    """Return (war, group, round_num) for our clan in the requested CWL round, or (None, group, 0)."""
    try:
        group = await _coc_safe(coc_client.get_league_group, CLAN_TAG)
    except (coc.NotFound, Exception):
        return None, None, 0

    round_num = len(group.rounds)
    try:
        async for war in group.get_wars(war_round):
            clan_tag_clean = CLAN_TAG.lstrip("#").upper()
            if (war.clan.tag.lstrip("#").upper() == clan_tag_clean
                    or war.opponent.tag.lstrip("#").upper() == clan_tag_clean):
                return war, group, round_num
    except Exception:
        pass
    return None, group, round_num


async def build_cwl_message() -> tuple[str, bool]:
    """Build CWL status message. Returns (text, keep_updating)."""
    war, group, round_num = await get_cwl_clan_war()

    if group is None:
        return "🏳️ Клан не участвует в Лиге войн клана.", False

    if war is None or group.state == "preparation":
        return f"⚙️ ЛВК — идёт подготовка. Раундов сыграно: {round_num}.", False

    if group.state == "ended":
        return "🏁 Сезон Лиги войн клана завершён.", False

    clan_tag_clean = CLAN_TAG.lstrip("#").upper()
    our_side = war.clan if war.clan.tag.lstrip("#").upper() == clan_tag_clean else war.opponent
    opp_side = war.opponent if our_side is war.clan else war.clan

    attacks_per_member = 1
    pending = []
    for member in our_side.members:
        used = len(member.attacks) if member.attacks else 0
        if used < attacks_per_member:
            pending.append(member)

    state_label = "⚔️ ЛВК идёт" if group.state == "inWar" else "🏁 ЛВК раунд завершён"
    our_stars = our_side.stars
    their_stars = opp_side.stars

    time_str = ""
    if group.state == "inWar" and war.end_time:
        now = datetime.utcnow()
        diff = war.end_time.time - now
        total_sec = max(int(diff.total_seconds()), 0)
        h, m = divmod(total_sec // 60, 60)
        time_str = f"\n⏱ До конца раунда: <b>{h}ч {m}мин</b>"

    lines = [
        f"<b>{state_label} — Раунд {round_num}</b>",
        fmt.DIV,
        f"🏰 <b>{our_side.name}</b>  ⚔️  <b>{opp_side.name}</b>",
        f"⭐ {our_stars}  vs  {their_stars}" + time_str,
    ]

    if not pending:
        lines.append(f"\n{fmt.ok('<b>Все атаковали в раунде!</b>')}")
    else:
        lines.append(f"\n🔴 <b>Не атаковали — {len(pending)} чел.</b>")
        tg_map = storage.get_tg_username_map()
        for member in pending:
            tg = tg_map.get(_norm_name(member.name))
            lines.append(fmt.member_line(member.name, tg=tg))

    lines.append(fmt.footer())
    keep_updating = group.state == "inWar"
    return "\n".join(lines), keep_updating


async def send_cwl_start(bot, chat_id: int, war, group, round_num: int):
    clan_tag_clean = CLAN_TAG.lstrip("#").upper()
    our_side = war.clan if war.clan.tag.lstrip("#").upper() == clan_tag_clean else war.opponent
    opp_side = war.opponent if our_side is war.clan else war.clan
    opponent_name = opp_side.name if opp_side else "противника"

    text = (
        f"🏆 <b>ЛВК — РАУНД {round_num} НАЧАЛСЯ!</b>\n"
        f"{fmt.DIV}\n\n"
        f"🏰 <b>Warfil</b>  vs  <b>{opponent_name}</b>\n\n"
        f"{fmt.DIVs}\n"
        f"💥 Помните: в ЛВК только <b>1 атака</b>!\n"
        f"▸ Выбирайте цели тщательно\n"
        f"▸ Атакуйте с умом\n\n"
        f"<b>Удачи бойцам Warfil! 💪</b>"
    )
    await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=KV_BUTTONS)


async def send_cwl_end(bot, chat_id: int, war, round_num: int):
    clan_tag_clean = CLAN_TAG.lstrip("#").upper()
    our_side = war.clan if war.clan.tag.lstrip("#").upper() == clan_tag_clean else war.opponent
    opp_side = war.opponent if our_side is war.clan else war.clan
    tg_map = storage.get_tg_username_map()

    attacked = []
    missed = []
    for member in our_side.members:
        used = len(member.attacks) if member.attacks else 0
        if used >= 1:
            attacked.append(member)
        else:
            missed.append(member)

    our_stars = our_side.stars
    their_stars = opp_side.stars
    if our_stars > their_stars:
        result_line = "🏆 <b>Победа в раунде!</b>"
    elif our_stars < their_stars:
        result_line = "😔 <b>Поражение в раунде.</b>"
    else:
        result_line = "🤝 <b>Ничья в раунде.</b>"

    lines = [
        f"🏁 <b>ЛВК — РАУНД {round_num} ЗАВЕРШЁН</b>",
        fmt.DIV,
        f"🏰 <b>Warfil</b>  vs  <b>{opp_side.name}</b>",
        f"⭐ {our_stars}  vs  {their_stars}  ·  {result_line}",
    ]

    if attacked:
        lines.append(f"\n✅ <b>Атаковали ({len(attacked)}):</b>")
        for member in attacked:
            tg = tg_map.get(_norm_name(member.name))
            lines.append(fmt.member_line(member.name, tg=tg))
        lines.append("\n🔥 <i>Молодцы! Продолжайте в том же духе!</i>")

    if missed:
        lines.append(f"\n❌ <b>Не атаковали ({len(missed)}):</b>")
        for member in missed:
            tg = tg_map.get(_norm_name(member.name))
            lines.append(fmt.member_line(member.name, tg=tg))
        lines.append(f"\n{fmt.warn('<b>Игроки без атак — кандидаты на кик!</b>')}")

    # Save CWL round stats for Excel
    # Determine current season from CWL group if possible (use YYYY-MM format)
    try:
        group = await _coc_safe(coc_client.get_league_group, CLAN_TAG)
        season = group.season or "unknown"
    except Exception:
        season = "unknown"
    member_stats = []
    for m in attacked:
        member_stats.append({"name": m.name, "attacked": True})
    for m in missed:
        member_stats.append({"name": m.name, "attacked": False})
    stats_storage.save_cwl_round(
        season=season, round_num=round_num,
        opponent=opp_side.name,
        our_stars=our_stars, their_stars=their_stars,
        members=member_stats,
    )

    await bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode="HTML", reply_markup=KV_BUTTONS)


async def cwl_state_monitor(bot):
    """Poll CWL state every 60s; send messages on round start/end transitions."""
    saved = storage.get_cwl_state()
    prev_state = saved.get("state") if saved else None
    prev_round = saved.get("round_count", 0) if saved else 0

    while True:
        await asyncio.sleep(60)
        try:
            chat_ids = storage.get_notify_chats()
            if not chat_ids:
                continue

            group = await _coc_safe(coc_client.get_league_group, CLAN_TAG)
            current_state = group.state or "notInWar"
            current_round = len(group.rounds)

            # New round started (inWar AND round count increased)
            if current_state == "inWar" and (prev_state != "inWar" or current_round != prev_round):
                war, _, _ = await get_cwl_clan_war()
                if war:
                    for chat_id in chat_ids:
                        try:
                            await send_cwl_start(bot, chat_id, war, group, current_round)
                        except Exception as e:
                            logger.warning(f"cwl_watcher: ошибка отправки в {chat_id}: {e}")

            # Round ended
            elif current_state == "warEnded" and prev_state == "inWar":
                war, _, _ = await get_cwl_clan_war(coc.WarRound.previous_war)
                if war:
                    for chat_id in chat_ids:
                        try:
                            await send_cwl_end(bot, chat_id, war, prev_round)
                        except Exception as e:
                            logger.warning(f"cwl_watcher: ошибка отправки в {chat_id}: {e}")

            if current_state != prev_state or current_round != prev_round:
                storage.save_cwl_state(current_state, current_round)
                prev_state = current_state
                prev_round = current_round

        except coc.NotFound:
            pass  # Clan not in CWL this season
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"cwl_state_monitor: {e}")


async def cwl_auto_broadcast(bot):
    """Send CWL status every 2 hours during active CWL round (same as war_auto_broadcast)."""
    while True:
        await asyncio.sleep(7200)  # 2 часа
        try:
            chat_ids = storage.get_notify_chats()
            if not chat_ids:
                continue
            text, keep_going = await build_cwl_message()
            if keep_going:
                for chat_id in chat_ids:
                    try:
                        await bot.send_message(
                            chat_id=chat_id,
                            text=text,
                            parse_mode="HTML",
                            reply_markup=KV_BUTTONS,
                        )
                    except Exception as e:
                        logger.warning(f"cwl_auto_broadcast: ошибка отправки в {chat_id}: {e}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"cwl_auto_broadcast: {e}")


async def cwl_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Загружаю данные Лиги войн...")
    try:
        text, _ = await build_cwl_message()
        await msg.edit_text(text, parse_mode="HTML", reply_markup=KV_BUTTONS)
    except Exception as e:
        logger.error(f"Ошибка /cwl: {e}")
        await msg.edit_text("❌ Не удалось загрузить данные ЛВК. Попробуй позже.")


async def testcwlstart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_feolar(update):
        await update.message.reply_text("❌ Нет доступа.")
        return
    try:
        war, group, round_num = await get_cwl_clan_war()
        if war is None:
            await update.message.reply_text("⚠️ Клан не в ЛВК или нет активного раунда.")
            return
        await send_cwl_start(context.bot, update.effective_chat.id, war, group, round_num)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def testcwlend_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_feolar(update):
        await update.message.reply_text("❌ Нет доступа.")
        return
    try:
        war, group, round_num = await get_cwl_clan_war()
        if war is None:
            await update.message.reply_text("⚠️ Клан не в ЛВК или нет активного раунда.")
            return
        await send_cwl_end(context.bot, update.effective_chat.id, war, round_num)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def raid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current capital raid status."""
    msg = await update.message.reply_text("⏳ Загружаю данные рейдов столицы...")
    try:
        text, _ = await build_raid_message()
        await msg.edit_text(text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Ошибка /raid: {e}")
        await msg.edit_text("❌ Не удалось загрузить данные рейдов. Попробуй позже.")


async def testraidstart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Test: send a raid start notification to current chat."""
    if not _is_feolar(update):
        await update.message.reply_text("❌ Нет доступа.")
        return
    try:
        raid = await _get_current_raid()
        if raid is None:
            await update.message.reply_text("⚠️ Нет данных о рейдах.")
            return
        await send_raid_start(context.bot, update.effective_chat.id, raid)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def testraidend_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Test: send a raid end notification to current chat."""
    if not _is_feolar(update):
        await update.message.reply_text("❌ Нет доступа.")
        return
    try:
        raid = await _get_current_raid()
        if raid is None:
            await update.message.reply_text("⚠️ Нет данных о рейдах.")
            return
        await send_raid_end(context.bot, update.effective_chat.id, raid)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")


CARD_BACKGROUNDS = [
    "card_background.png",
    "card_bg_2.png",
    "card_bg_3.png",
    "card_bg_4.png",
]

async def card_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🎨 Генерирую карточку клана, подождите...")
    try:
        clan = await _coc_safe(coc_client.get_clan, CLAN_TAG)
        raids = []
        try:
            async for r in await _coc_safe(coc_client.get_raid_log, CLAN_TAG, limit=4):
                raids.append(r)
        except Exception:
            pass
        available = [p for p in CARD_BACKGROUNDS if os.path.exists(p)]
        bg_path = __import__("random").choice(available) if available else None
        buf = await card_builder.build_card(clan, raids=raids, background_path=bg_path)
        await update.message.reply_photo(
            photo=buf,
            caption=(
                f"🏰 <b>{clan.name}</b>  ·  Уровень {clan.level}\n"
                f"👥 {clan.member_count} участников  ·  "
                f"⚔️ {clan.war_wins}П / {clan.war_losses}П"
            ),
            parse_mode="HTML",
        )
        await msg.delete()
    except Exception as e:
        import traceback
        traceback.print_exc()
        await msg.edit_text(f"❌ Ошибка генерации карточки: {e}")


async def addchat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_feolar(update):
        await update.message.reply_text("⛔ Только для администратора.")
        return
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    chat_title = update.effective_chat.title or "личка"
    added = storage.add_notify_chat(chat_id)
    if added:
        await update.message.reply_text(
            f"✅ Чат добавлен в рассылку!\n"
            f"<b>{chat_title}</b> (ID: <code>{chat_id}</code>, тип: {chat_type})\n\n"
            "Теперь уведомления о КВ и ЛВК будут приходить сюда.",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            f"ℹ️ Этот чат уже в списке рассылки.\n"
            f"ID: <code>{chat_id}</code>",
            parse_mode="HTML",
        )


async def removechat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_feolar(update):
        await update.message.reply_text("⛔ Только для администратора.")
        return
    chat_id = update.effective_chat.id
    chat_title = update.effective_chat.title or "личка"
    removed = storage.remove_notify_chat(chat_id)
    if removed:
        await update.message.reply_text(
            f"✅ Чат удалён из рассылки.\n"
            f"<b>{chat_title}</b> (ID: <code>{chat_id}</code>)",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            f"ℹ️ Этот чат не был в списке рассылки.\n"
            f"ID: <code>{chat_id}</code>",
            parse_mode="HTML",
        )


async def listchats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_feolar(update):
        await update.message.reply_text("⛔ Только для администратора.")
        return
    chats = storage.get_notify_chats()
    if not chats:
        await update.message.reply_text("📭 Список чатов для рассылки пуст.")
        return
    lines = [f"📬 <b>Чаты для рассылки ({len(chats)}):</b>"]
    for i, cid in enumerate(chats, 1):
        lines.append(f"  {i}. <code>{cid}</code>")
    lines.append("\nℹ️ /addchat — добавить текущий чат\n/removechat — убрать текущий чат")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def _detect_real_outbound_ip() -> str | None:
    """Определяем реальный исходящий IP сервера через внешний сервис."""
    import aiohttp
    for url in ("https://api.ipify.org", "https://checkip.amazonaws.com", "https://icanhazip.com"):
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                    ip = (await r.text()).strip()
                    if _re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', ip):
                        return ip
        except Exception:
            continue
    return None


async def post_init(application):
    if COC_API_KEY:
        await coc_client.login_with_tokens(COC_API_KEY)
        logger.info("CoC клиент авторизован через API ключ")
    else:
        # 1) Определяем реальный исходящий IP сервера
        real_ip = await _detect_real_outbound_ip()
        if real_ip:
            logger.info(f"Реальный исходящий IP сервера: {real_ip}")
            # _rekey_coc ниже вызовет _register_ip(real_ip) сам — дублировать не нужно
        else:
            logger.warning("Не удалось определить исходящий IP")

        # 2) Создаём/обновляем ключ с CIDR диапазоном (/24) для каждого известного IP
        #    _rekey_coc делает login_with_tokens внутри — НЕ вызываем coc_client.login()
        #    чтобы coc.py не удалил наш CIDR-ключ (coc.py умеет только точное IP сравнение)
        ok = await _rekey_coc(new_ip=real_ip)
        if ok:
            logger.info("CoC клиент авторизован через CIDR-ключ (email/пароль)")
        else:
            # Fallback: обычный логин через coc.py
            logger.warning("_rekey_coc не удался — обычный логин через coc.py")
            await coc_client.login(COC_EMAIL, COC_PASSWORD)
            logger.info("CoC клиент авторизован через email/пароль (fallback)")

    asyncio.create_task(war_auto_broadcast(application.bot))
    asyncio.create_task(war_state_monitor(application.bot))
    asyncio.create_task(cwl_state_monitor(application.bot))
    asyncio.create_task(cwl_auto_broadcast(application.bot))
    asyncio.create_task(raid_state_monitor(application.bot))
    asyncio.create_task(raid_auto_broadcast(application.bot))
    logger.info("Авто-рассылка войны, ЛВК и рейдов запущена")

    # Register bot commands (shown in Telegram command menu)
    await application.bot.set_my_commands([
        BotCommand("start",    "🏰 Главное меню"),
        BotCommand("team",     "📋 Список участников клана"),
        BotCommand("kv",       "⚔️ Атаки в клановой войне"),
        BotCommand("cwl",      "🏆 Статус Лиги войн клана"),
        BotCommand("raid",     "🏛 Рейды столицы клана"),
        BotCommand("statistic","📊 Статистика клана (Excel)"),
        BotCommand("card",     "🎨 Карточка клана"),
        BotCommand("register", "🔗 Привязать свой аккаунт CoC"),
        BotCommand("help",     "❓ Помощь по командам"),
        BotCommand("link",       "🛡 [Адм] Привязать игрока к Telegram"),
        BotCommand("unlink",     "🛡 [Адм] Убрать привязку игрока"),
        BotCommand("links",      "🛡 [Адм] Список всех привязок"),
        BotCommand("addchat",    "🛡 [Адм] Добавить чат в рассылку"),
        BotCommand("removechat", "🛡 [Адм] Убрать чат из рассылки"),
        BotCommand("listchats",  "🛡 [Адм] Список чатов рассылки"),
    ])
    await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    logger.info("Команды бота зарегистрированы")


async def post_shutdown(application):
    try:
        if coc_client.http is not None:
            await coc_client.close()
    except Exception:
        pass


async def conflict_error_handler(update, context):
    from telegram.error import Conflict
    if isinstance(context.error, Conflict):
        logger.warning("Конфликт: другой экземпляр бота работает. Жду освобождения...")
    else:
        logger.error(f"Ошибка: {context.error}")


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass


def _start_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
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

    # Защита: проверяется ДО всех команд
    app.add_handler(TypeHandler(Update, clan_guard), group=-1)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("team", team_command))
    app.add_handler(CommandHandler("register", register_command))
    app.add_handler(CommandHandler("online", online_command))
    app.add_handler(CommandHandler("kv", kv_command))
    app.add_handler(CommandHandler("link", link_command))
    app.add_handler(CommandHandler("unlink", unlink_command))
    app.add_handler(CommandHandler("links", links_command))
    app.add_handler(CommandHandler("teststart", teststart_command))
    app.add_handler(CommandHandler("testend", testend_command))
    app.add_handler(CommandHandler("cwl", cwl_command))
    app.add_handler(CommandHandler("raid", raid_command))
    app.add_handler(CommandHandler("statistic", statistic_command))
    app.add_handler(CommandHandler("testcwlstart", testcwlstart_command))
    app.add_handler(CommandHandler("testcwlend", testcwlend_command))
    app.add_handler(CommandHandler("testraidstart", testraidstart_command))
    app.add_handler(CommandHandler("testraidend", testraidend_command))
    app.add_handler(CommandHandler("card", card_command))
    app.add_handler(CommandHandler("addchat", addchat_command))
    app.add_handler(CommandHandler("removechat", removechat_command))
    app.add_handler(CommandHandler("listchats", listchats_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    app.add_error_handler(conflict_error_handler)

    logger.info("Бот запущен...")
    app.run_polling()


if __name__ == "__main__":
    main()
