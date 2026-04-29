import json
import os

import pathlib

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

WAR_HISTORY_FILE = str(_DIR / "war_history.json")
CWL_HISTORY_FILE = str(_DIR / "cwl_history.json")

MAX_WAR_ENTRIES = 5
MAX_CWL_SEASONS = 2


def _load_json(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── Clan War (KV) history ─────────────────────────────────────────────────────

def save_war_result(end_time: str, opponent: str, result: str,
                    our_stars: int, their_stars: int,
                    team_size: int, attacks_per_member: int,
                    members: list[dict]):
    """Save a finished KV result. Keeps last MAX_WAR_ENTRIES entries."""
    history = _load_json(WAR_HISTORY_FILE) or []
    history.insert(0, {
        "end_time": end_time,
        "opponent": opponent,
        "result": result,
        "our_stars": our_stars,
        "their_stars": their_stars,
        "team_size": team_size,
        "attacks_per_member": attacks_per_member,
        "members": members,
    })
    history = history[:MAX_WAR_ENTRIES]
    _save_json(WAR_HISTORY_FILE, history)


def get_war_history() -> list[dict]:
    return _load_json(WAR_HISTORY_FILE) or []


# ── CWL history (keeps last MAX_CWL_SEASONS seasons) ─────────────────────────

def save_cwl_round(season: str, round_num: int, opponent: str,
                   our_stars: int, their_stars: int,
                   members: list[dict]):
    """Append a finished CWL round to the season history. Keeps last MAX_CWL_SEASONS seasons."""
    raw = _load_json(CWL_HISTORY_FILE)

    # Migrate old format {"season": ..., "rounds": [...]} → new list format
    if isinstance(raw, dict) and "season" in raw:
        seasons = [raw]
    elif isinstance(raw, list):
        seasons = raw
    else:
        seasons = []

    # Find existing entry for this season
    for s in seasons:
        if s.get("season") == season:
            s["rounds"].append({
                "round": round_num,
                "opponent": opponent,
                "our_stars": our_stars,
                "their_stars": their_stars,
                "members": members,
            })
            _save_json(CWL_HISTORY_FILE, seasons[:MAX_CWL_SEASONS])
            return

    # New season — prepend and trim
    seasons.insert(0, {
        "season": season,
        "rounds": [{
            "round": round_num,
            "opponent": opponent,
            "our_stars": our_stars,
            "their_stars": their_stars,
            "members": members,
        }],
    })
    _save_json(CWL_HISTORY_FILE, seasons[:MAX_CWL_SEASONS])


def get_cwl_seasons() -> list[dict]:
    """Return list of saved CWL seasons (newest first)."""
    raw = _load_json(CWL_HISTORY_FILE)
    if raw is None:
        return []
    if isinstance(raw, dict) and "season" in raw:
        return [raw]  # migrate old single-season format
    if isinstance(raw, list):
        return raw
    return []


def get_cwl_history() -> dict:
    """Backward-compat: return most recent saved season."""
    seasons = get_cwl_seasons()
    return seasons[0] if seasons else {"season": None, "rounds": []}
