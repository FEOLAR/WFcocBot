import json
import os
import unicodedata
from datetime import datetime

import pathlib


_INVIS_RANGES = [
    (0x180B, 0x180F),   # Mongolian Free Variation Selectors
    (0xFE00, 0xFE0F),   # Variation Selectors VS1-VS16
    (0xE0100, 0xE01EF), # Variation Selectors Supplement
    (0x200B, 0x200F),   # Zero-width space, ZWNJ, ZWJ, LRM, RLM
    (0x2060, 0x2064),   # Word joiner and invisible operators
    (0xFEFF, 0xFEFF),   # BOM / ZWNBSP
    (0x00AD, 0x00AD),   # Soft hyphen
]


def _is_invis(ch: str) -> bool:
    if unicodedata.category(ch) in ("Cf", "Cc"):
        return True
    cp = ord(ch)
    return any(s <= cp <= e for s, e in _INVIS_RANGES)


def _norm(name: str) -> str:
    """NFC + lowercase + удаляем невидимые/форматирующие Unicode символы.

    Точечно убираем variation selectors (U+180B-U+180F, U+FE00-U+FE0F),
    нулевые пробелы и Cf/Cc символы, не трогая реальные диакритики (ā, ŕ, ķ...).
    """
    normalized = unicodedata.normalize("NFC", name)
    return "".join(ch for ch in normalized if not _is_invis(ch)).lower()

def _data_dir() -> pathlib.Path:
    """Папка данных: DATA_DIR из .env, иначе data/ рядом с ботом.

    Раньше бот сначала пробовал /data. На VPS под root эта папка создаётся
    пустой — бот «терял» список чатов рассылки и молча переставал присылать
    уведомления. Теперь всё лежит рядом с ботом, а старые файлы из /data
    переносятся автоматически.
    """
    import shutil
    try:
        import config as _cfg
        custom = getattr(_cfg, "DATA_DIR", "")
    except ImportError:
        custom = os.environ.get("DATA_DIR", "")
    base = pathlib.Path(__file__).resolve().parent
    target = pathlib.Path(custom) if custom else base / "data"
    target.mkdir(parents=True, exist_ok=True)

    legacy = pathlib.Path("/data")
    if legacy.resolve() != target.resolve() and legacy.is_dir():
        for src in legacy.glob("*.json"):
            dst = target / src.name
            try:
                if src.name == "notify_chat.json" and dst.exists():
                    merged = []
                    for p in (dst, src):
                        try:
                            d = json.loads(p.read_text(encoding="utf-8") or "{}")
                            merged += d.get("chat_ids", []) or ([d["chat_id"]] if d.get("chat_id") else [])
                        except Exception:
                            pass
                    dst.write_text(json.dumps({"chat_ids": list(dict.fromkeys(merged))}), encoding="utf-8")
                elif not dst.exists() or src.stat().st_mtime > dst.stat().st_mtime:
                    shutil.copy2(src, dst)
            except OSError:
                pass
    return target


_DIR = _data_dir()

DATA_FILE        = str(_DIR / "players.json")
LINKS_FILE       = str(_DIR / "links.json")
NOTIFY_CHAT_FILE = str(_DIR / "notify_chat.json")


# ── Internal helpers ────────────────────────────────────────────────────────

def _load() -> dict:
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_links() -> dict:
    """links.json: {coc_name_lower: tg_username}"""
    if not os.path.exists(LINKS_FILE):
        return {}
    with open(LINKS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_links(data: dict):
    with open(LINKS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── Self-registration (player-facing) ───────────────────────────────────────

def register_player(telegram_id: int, coc_name: str, telegram_username: str | None = None):
    data = _load()
    key = str(telegram_id)
    existing_last_seen = data.get(key, {}).get("last_seen")
    data[key] = {
        "coc_name": coc_name,
        "telegram_username": telegram_username,
        "last_seen": existing_last_seen,
    }
    _save(data)


def update_last_seen(telegram_id: int):
    data = _load()
    key = str(telegram_id)
    if key in data:
        data[key]["last_seen"] = datetime.now().isoformat()
        _save(data)
        return data[key]["coc_name"]
    return None


def get_last_seen_map() -> dict[str, str]:
    """Returns {coc_name_lower: last_seen_str}"""
    data = _load()
    result = {}
    for entry in data.values():
        name = entry.get("coc_name", "")
        last_seen = entry.get("last_seen")
        if name:
            result[_norm(name)] = last_seen
    return result


def get_player_by_telegram(telegram_id: int) -> dict | None:
    data = _load()
    return data.get(str(telegram_id))


# ── Admin-managed links ──────────────────────────────────────────────────────

def link_player(coc_name: str, tg_username: str):
    """Link a CoC player name to a Telegram username (admin action)."""
    links = _load_links()
    links[_norm(coc_name)] = tg_username.lstrip("@")
    _save_links(links)


def unlink_player(coc_name: str) -> bool:
    """Remove link for a CoC player. Returns True if it existed."""
    links = _load_links()
    key = _norm(coc_name)
    if key in links:
        del links[key]
        _save_links(links)
        return True
    return False


def get_all_links() -> dict[str, str]:
    """Returns {coc_name_lower: tg_username} from admin links."""
    return _load_links()


# ── Combined map (used by /team) ─────────────────────────────────────────────

def get_tg_username_map() -> dict[str, str]:
    """Merged {coc_name_lower: tg_username} from both self-registration and admin links.
    Admin links take priority."""
    # Self-registered players
    data = _load()
    result = {}
    for entry in data.values():
        name = entry.get("coc_name", "")
        username = entry.get("telegram_username")
        if name and username:
            result[_norm(name)] = username

    # Admin links override / supplement
    result.update(_load_links())
    return result


# ── War notification chats (multiple) ────────────────────────────────────────

def _load_notify_chats() -> list[int]:
    if not os.path.exists(NOTIFY_CHAT_FILE):
        return []
    try:
        with open(NOTIFY_CHAT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    # Backward compat: old format was {"chat_id": 123}
    if "chat_ids" in data:
        return data["chat_ids"]
    if "chat_id" in data and data["chat_id"]:
        return [data["chat_id"]]
    return []


def _save_notify_chats(chat_ids: list[int]):
    with open(NOTIFY_CHAT_FILE, "w", encoding="utf-8") as f:
        json.dump({"chat_ids": chat_ids}, f)


def get_notify_chats() -> list[int]:
    return _load_notify_chats()


def add_notify_chat(chat_id: int) -> bool:
    """Add chat_id to notification list. Returns True if added, False if already exists."""
    chats = _load_notify_chats()
    if chat_id in chats:
        return False
    chats.append(chat_id)
    _save_notify_chats(chats)
    return True


def remove_notify_chat(chat_id: int) -> bool:
    """Remove chat_id from notification list. Returns True if removed."""
    chats = _load_notify_chats()
    if chat_id not in chats:
        return False
    chats.remove(chat_id)
    _save_notify_chats(chats)
    return True


def save_notify_chat(chat_id: int):
    """Legacy: ensure chat_id is in the list."""
    add_notify_chat(chat_id)


def get_notify_chat() -> int | None:
    """Legacy: return first chat or None."""
    chats = _load_notify_chats()
    return chats[0] if chats else None


# ── War state persistence (for transition detection across restarts) ───────────

WAR_STATE_FILE = str(_DIR / "war_state.json")


def save_war_state(state: str):
    with open(WAR_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"state": state}, f)


def get_war_state() -> str | None:
    if not os.path.exists(WAR_STATE_FILE):
        return None
    try:
        with open(WAR_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("state")
    except (json.JSONDecodeError, KeyError):
        return None


# ── CWL state persistence ─────────────────────────────────────────────────────

CWL_STATE_FILE = str(_DIR / "cwl_state.json")


def save_cwl_state(state: str, round_count: int):
    with open(CWL_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"state": state, "round_count": round_count}, f)


def get_cwl_state() -> dict | None:
    if not os.path.exists(CWL_STATE_FILE):
        return None
    try:
        with open(CWL_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, KeyError):
        return None


# ── Capital Raid state persistence ────────────────────────────────────────────

RAID_STATE_FILE = str(_DIR / "raid_state.json")


def save_raid_state(state: str, start_iso: str = ""):
    with open(RAID_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"state": state, "start_iso": start_iso}, f)


def get_raid_state() -> dict:
    if not os.path.exists(RAID_STATE_FILE):
        return {}
    try:
        with open(RAID_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, KeyError):
        return {}


def replace_notify_chat(old_id: int, new_id: int):
    """Группа стала супергруппой — у неё новый ID. Подменяем в списке."""
    chats = _load_notify_chats()
    chats = [new_id if c == old_id else c for c in chats]
    _save_notify_chats(list(dict.fromkeys(chats)))


DATA_DIR = str(_DIR)


# ── Состояние уведомлений (что уже отправлено) ───────────────────────────────
NOTIFY_STATE_FILE = str(_DIR / "notify_state.json")


def load_notify_state() -> dict:
    try:
        with open(NOTIFY_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_notify_state(state: dict):
    tmp = NOTIFY_STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, NOTIFY_STATE_FILE)


# ── Кастомные эмодзи ─────────────────────────────────────────────────────────
EMOJI_FILE = str(_DIR / "emoji.json")


def load_emoji_map() -> dict:
    try:
        with open(EMOJI_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_emoji_map(data: dict):
    with open(EMOJI_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
