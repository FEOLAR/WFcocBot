"""
Игровые эмодзи бота.

Каждый значок в сообщениях — это ключ (war, star, capital...). У ключа есть
обычный эмодзи-запасник и, если настроено, кастомный эмодзи из пака по
Clash of Clans. Кастомные эмодзи бот может отправлять, только если у
владельца бота есть Telegram Premium (Bot API 9.4). Если Telegram их не
примет, бот сам откатится на обычные — сообщения не потеряются.

Настройка из Telegram (только админ):
  /emojipack <имя или ссылка t.me/addemoji/...>  — подтянуть пак и сопоставить
  /setemoji war <эмодзи> star <эмодзи> ...       — привязать вручную
  /emoji                                         — посмотреть, что к чему
  /emojireset                                    — вернуть обычные эмодзи
"""
import html
import logging
import re

import storage

logger = logging.getLogger(__name__)

# ключ: (обычный эмодзи, что обозначает)
KEYS: dict[str, tuple[str, str]] = {
    "clan":      ("🏰", "клан, замок"),
    "war":       ("⚔️", "клановая война"),
    "prep":      ("🛡️", "день подготовки"),
    "cwl":       ("🏆", "лига войн кланов"),
    "capital":   ("🏛️", "столица клана, рейды"),
    "star":      ("⭐", "звезда"),
    "attack":    ("🗡️", "атака"),
    "boom":      ("💥", "процент разрушения"),
    "gold":      ("🪙", "золото столицы"),
    "medal":     ("🎖️", "медали, награда"),
    "district":  ("🏘️", "район столицы"),
    "time":      ("⏳", "время, таймер"),
    "date":      ("🗓️", "дата"),
    "team":      ("👥", "состав"),
    "leader":    ("👑", "глава"),
    "coleader":  ("🔱", "соруководитель"),
    "elder":     ("🌟", "старейшина"),
    "member":    ("🔹", "участник"),
    "th":        ("🏠", "ратуша"),
    "level":     ("📈", "уровень клана"),
    "trophy":    ("🏅", "лига, трофеи"),
    "win":       ("🥇", "победа"),
    "lose":      ("💀", "поражение"),
    "tie":       ("🤝", "ничья"),
    "done":      ("✅", "сделано"),
    "miss":      ("❌", "нет атак"),
    "half":      ("🟡", "осталась одна атака"),
    "alarm":     ("🔔", "напоминание"),
    "warn":      ("⚠️", "внимание"),
    "fire":      ("🔥", "лучшие, похвала"),
    "link":      ("🔗", "привязка аккаунта"),
    "stats":     ("📊", "статистика"),
    "card":      ("🖼️", "карточка клана"),
    "help":      ("📖", "помощь"),
    "admin":     ("🛠️", "админ"),
    "site":      ("🌐", "сайт"),
    "chat":      ("💬", "беседа"),
    "hello":     ("👋", "приветствие"),
}

_map: dict[str, str] = {}      # ключ -> custom_emoji_id
pack_ids: list[str] = []       # custom_emoji_id всех эмодзи пака по порядку
custom_enabled = True          # выключается, если Telegram отказал


def reload():
    global _map
    _map = {k: v for k, v in storage.load_emoji_map().items() if k in KEYS and v}


def plain(key: str) -> str:
    return KEYS.get(key, ("•", ""))[0]


def e(key: str) -> str:
    """Эмодзи для HTML-сообщения (кастомный, если настроен)."""
    fb = plain(key)
    cid = _map.get(key)
    if cid and custom_enabled:
        return f'<tg-emoji emoji-id="{html.escape(cid)}">{fb}</tg-emoji>'
    return fb


def icon_id(key: str) -> str | None:
    """custom_emoji_id для иконки на кнопке (или None)."""
    return _map.get(key) if custom_enabled else None


_TG_EMOJI_RE = re.compile(r'<tg-emoji emoji-id="[^"]*">(.*?)</tg-emoji>', re.S)


def strip_custom(text: str) -> str:
    return _TG_EMOJI_RE.sub(r"\1", text or "")


def _bare(s: str) -> str:
    return (s or "").replace("️", "").replace("‍", "").strip()


def set_many(pairs: dict[str, str]):
    data = storage.load_emoji_map()
    data.update(pairs)
    storage.save_emoji_map(data)
    reload()


def reset():
    storage.save_emoji_map({})
    reload()


def mapped() -> dict[str, str]:
    return dict(_map)


def auto_map_stickers(stickers, overwrite: bool = False) -> dict[str, str]:
    """Сопоставляет стикеры пака с ключами по их базовому эмодзи.

    stickers — список telegram.Sticker из get_sticker_set.
    Возвращает {ключ: custom_emoji_id} для найденных совпадений.
    """
    by_emoji: dict[str, list[str]] = {}
    for st in stickers:
        cid = getattr(st, "custom_emoji_id", None)
        if not cid:
            continue
        by_emoji.setdefault(_bare(st.emoji), []).append(cid)

    found: dict[str, str] = {}
    used: set[str] = set()
    for key, (fb, _) in KEYS.items():
        if not overwrite and key in _map:
            continue
        for cid in by_emoji.get(_bare(fb), []):
            if cid not in used:
                found[key] = cid
                used.add(cid)
                break
    return found


def remember_pack(stickers):
    global pack_ids
    pack_ids = [s.custom_emoji_id for s in stickers if getattr(s, "custom_emoji_id", None)]


def pack_emoji(i: int, fallback: str = "⭐") -> str:
    """Эмодзи пака по номеру (с 1) — для каталога /emojilist."""
    cid = pack_ids[i - 1]
    return f'<tg-emoji emoji-id="{html.escape(cid)}">{fallback}</tg-emoji>'


def pack_name_from(arg: str) -> str:
    arg = (arg or "").strip()
    m = re.search(r"(?:addemoji|addstickers)/([A-Za-z0-9_]+)", arg)
    return m.group(1) if m else arg.lstrip("@")


reload()
