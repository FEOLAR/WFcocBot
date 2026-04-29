import json
import os
import unicodedata
from datetime import datetime

import pathlib


def _norm(name: str) -> str:
    """Unicode NFC normalization + lowercase для надёжного сравнения никнеймов."""
    return unicodedata.normalize("NFC", name).lower()

def _data_dir() -> pathlib.Path:
    p = pathlib.Path("/data")
    try:
        p.mkdir(exist_ok=True)
        (p / ".writable_check").touch()
        (p / ".writable_check").unlink()
        return p
    except OSError:
        local = pathlib.Path("data")
        local.mkdir(exist_ok=True)
        return local

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
    with open(NOTIFY_CHAT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
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
