"""
Оформление сообщений бота WarFil.

Правила стиля:
  • заголовок — игровой эмодзи + жирная фраза обычным регистром;
  • сразу под ним счёт или главное число — то, ради чего открыли сообщение;
  • каждая деталь на своей строке со своим значком, без цепочек через точки;
  • списки игроков — в цитате, длинные сворачиваются (expandable);
  • никаких разделителей и подписи сайта в каждом сообщении — сайт в кнопках.
"""
import html
import logging
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

import settings as config
import game_emoji
from game_emoji import e

logger = logging.getLogger(__name__)

BRAND = config.CLAN_BRAND


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=False)


def title(key: str, text: str) -> str:
    return f"{e(key)} <b>{text}</b>"


def line(key: str, text: str) -> str:
    return f"{e(key)} {text}"


MAX_LIST = 40   # длинные списки режем, чтобы не упереться в лимит Telegram (4096)


def quote(lines: list[str], fold_after: int = 8) -> str:
    if not lines:
        return ""
    if len(lines) > MAX_LIST:
        lines = lines[:MAX_LIST] + [f"…и ещё {len(lines) - MAX_LIST}"]
    body = "\n".join(lines)
    tag = "blockquote expandable" if len(lines) > fold_after else "blockquote"
    return f"<{tag}>{body}</{tag.split()[0]}>"


def player(name: str, th: int | None = None, tg: str | None = None, extra: str = "") -> str:
    th_part = f"ТХ{th} " if th else ""
    tg_part = f" @{esc(tg)}" if tg else ""
    extra_part = f" {extra}" if extra else ""
    return f"{th_part}<b>{esc(name)}</b>{extra_part}{tg_part}"


def bar(used: int, total: int, width: int = 10) -> str:
    filled = round(used / total * width) if total else 0
    filled = max(0, min(width, filled))
    return "▰" * filled + "▱" * (width - filled)


def num(n) -> str:
    return f"{int(n or 0):,}".replace(",", " ")


def pct(x) -> str:
    return f"{float(x or 0):.1f}".replace(".", ",") + "%"


def score(our_name: str, our: int, their: int, their_name: str) -> str:
    return (f"<b>{esc(our_name)}</b>  {e('star')} {our} : {their} {e('star')}  "
            f"<b>{esc(their_name)}</b>")


# ── Время ─────────────────────────────────────────────────────────────────────

def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def local(dt: datetime | None) -> str:
    """'15.11, 21:30 МСК' из наивного UTC."""
    if not dt:
        return "—"
    d = dt + timedelta(hours=config.TZ_OFFSET_HOURS)
    return f"{d:%d.%m, %H:%M} {config.TZ_LABEL}"


def duration(seconds: float) -> str:
    s = max(int(seconds), 0)
    days, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if days:
        return f"{days} д {h} ч"
    if h:
        return f"{h} ч {m} мин"
    return f"{m} мин"


def until(dt: datetime | None) -> str:
    return duration((dt - utcnow()).total_seconds()) if dt else "—"


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


# ── Короткие ответы ──────────────────────────────────────────────────────────

def ok(text: str) -> str:
    return f"{e('done')} {text}"


def err(text: str) -> str:
    return f"{e('miss')} {text}"


def warn(text: str) -> str:
    return f"{e('warn')} {text}"


# ── Кнопки ────────────────────────────────────────────────────────────────────

def button(text: str, url: str, icon: str | None = None) -> InlineKeyboardButton:
    """Кнопка-ссылка. Если для icon есть кастомный эмодзи — он встанет иконкой."""
    cid = game_emoji.icon_id(icon) if icon else None
    if cid:
        return InlineKeyboardButton(text, url=url, icon_custom_emoji_id=cid)
    label = f"{game_emoji.plain(icon)} {text}" if icon else text
    return InlineKeyboardButton(label, url=url)


def clan_buttons() -> InlineKeyboardMarkup:
    tag = config.CLAN_TAG.replace("#", "%23")
    return InlineKeyboardMarkup([[
        button("Сайт клана", config.CLAN_WEBSITE, "site"),
        button("Клан в игре", f"https://link.clashofclans.com/ru?action=OpenClanProfile&tag={tag}", "clan"),
    ]])


def welcome_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        button("Сайт клана", config.CLAN_WEBSITE, "site"),
        button("Беседа клана", config.TG_GROUP_LINK, "chat"),
    ]])


def _plain_markup(markup):
    if not isinstance(markup, InlineKeyboardMarkup):
        return markup
    rows = []
    for row in markup.inline_keyboard:
        new_row = []
        for b in row:
            if b.icon_custom_emoji_id:
                key = next((k for k, v in game_emoji.mapped().items() if v == b.icon_custom_emoji_id), None)
                label = f"{game_emoji.plain(key)} {b.text}" if key else b.text
                new_row.append(InlineKeyboardButton(label, url=b.url, callback_data=b.callback_data))
            else:
                new_row.append(b)
        rows.append(new_row)
    return InlineKeyboardMarkup(rows)


# ── Безопасная отправка ──────────────────────────────────────────────────────
# Если Telegram не принял кастомные эмодзи (нет Premium у владельца) —
# повторяем с обычными и больше их не используем до перезапуска.

def _is_emoji_error(exc: Exception) -> bool:
    t = str(exc).lower()
    return "emoji" in t or "entit" in t or "icon" in t


async def _with_fallback(call, text: str, kwargs: dict):
    kwargs.setdefault("parse_mode", "HTML")
    kwargs.setdefault("disable_web_page_preview", True)
    try:
        return await call(text=text, **kwargs)
    except BadRequest as exc:
        if "too long" in str(exc).lower():
            plain = game_emoji.strip_custom(text)
            cut = plain[:3900].rsplit("\n", 1)[0]
            for tag, opener in (("code", "<code>"), ("i", "<i>"), ("b", "<b>"),
                                ("blockquote", "<blockquote")):
                if cut.count(opener) > cut.count(f"</{tag}>"):
                    cut += f"</{tag}>"
            return await call(text=cut + "\n…", **kwargs)
        has_custom = "<tg-emoji" in text or (
            isinstance(kwargs.get("reply_markup"), InlineKeyboardMarkup)
            and any(b.icon_custom_emoji_id for r in kwargs["reply_markup"].inline_keyboard for b in r))
        if not has_custom or not _is_emoji_error(exc):
            raise
        logger.warning(f"Telegram не принял кастомные эмодзи ({exc}) — переключаюсь на обычные")
        game_emoji.custom_enabled = False
        kwargs["reply_markup"] = _plain_markup(kwargs.get("reply_markup"))
        return await call(text=game_emoji.strip_custom(text), **kwargs)


async def send(bot, chat_id: int, text: str, **kwargs):
    return await _with_fallback(lambda **kw: bot.send_message(chat_id=chat_id, **kw), text, kwargs)


async def reply(message, text: str, **kwargs):
    return await _with_fallback(message.reply_text, text, kwargs)


async def edit(message, text: str, **kwargs):
    return await _with_fallback(message.edit_text, text, kwargs)
