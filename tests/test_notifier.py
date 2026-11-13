"""
Офлайн-проверка мониторов: подменяем CoC API и Telegram, прогоняем
переходы состояний и смотрим, какие сообщения ушли.
Запуск:  python -m pytest tests  (или просто python tests/test_notifier.py)
"""
import asyncio, os, sys, tempfile, json
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")

import coc
import storage, notifier, views, fmt

TAG = "#2R02GGRUJ"
NOW = datetime.now(timezone.utc).replace(tzinfo=None)


def ts(dt):
    return dt.strftime("%Y%m%dT%H%M%S.000Z")


def member(i, name, attacks):
    return {"tag": f"#M{i}", "name": name, "townhallLevel": 16, "mapPosition": i,
            "opponentAttacks": 0,
            "attacks": [{"attackerTag": f"#M{i}", "defenderTag": f"#E{i}", "stars": s,
                         "destructionPercentage": 80 + s, "order": i * 10 + k, "duration": 100}
                        for k, s in enumerate(attacks)]}


def war_json(state, start, end, members, opp_name="Bad <Guys> & Co", cwl=False, tag=None):
    d = {
        "state": state, "teamSize": len(members),
        "preparationStartTime": ts(start - timedelta(hours=23)),
        "startTime": ts(start), "endTime": ts(end),
        "clan": {"tag": TAG, "name": "WarFil", "clanLevel": 20, "attacks": 0,
                 "stars": sum(sum(a["stars"] for a in m["attacks"]) for m in members),
                 "destructionPercentage": 75.5, "members": members, "badgeUrls": {}},
        "opponent": {"tag": "#OPP", "name": opp_name, "clanLevel": 18, "attacks": 0,
                     "stars": 5, "destructionPercentage": 60.0, "members": [], "badgeUrls": {}},
    }
    if not cwl:
        d["attacksPerMember"] = 2
    if tag:
        d["tag"] = tag
    return d


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        check_html(text)
        self.sent.append(text)


class _V(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []

    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack and self.stack[-1] == tag, f"bad close {tag} in {self.stack}"
        self.stack.pop()


def check_html(text):
    v = _V()
    v.feed(text)
    assert not v.stack, f"unclosed {v.stack}"
    allowed = {"b", "i", "code", "blockquote", "tg-emoji", "u", "s", "a", "pre"}
    import re
    for t in re.findall(r"</?([a-z-]+)", text):
        assert t in allowed, t


async def passthrough(fn, *a, **k):
    return await fn(*a, **k)


notifier._coc_safe = passthrough


class State:
    war = None
    group = None
    raid = None


async def get_clan_war(tag):
    return State.war


async def get_league_group(tag):
    if State.group is None:
        raise coc.NotFound(type("R", (), {"status": 404, "reason": "nf"})(), {"reason": "notFound", "message": ""})
    return State.group


class FakeLog:
    def __init__(self, items):
        self.items = items

    def __aiter__(self):
        async def gen():
            for i in self.items:
                yield i
        return gen()


async def get_raid_log(tag, limit=1):
    return FakeLog([State.raid] if State.raid else [])


async def get_clan(tag):
    return type("C", (), {"members": [type("M", (), {"name": n, "town_hall": 15})() for n in
                                      ("Alpha", "Bravo", "Idle")]})()


class FakeClient:
    pass


_fc = FakeClient()
_fc.get_clan_war = get_clan_war
_fc.get_league_group = get_league_group
_fc.get_raid_log = get_raid_log
_fc.get_clan = get_clan
notifier.coc_client = _fc


def mk_war(*a, **k):
    return coc.ClanWar(data=war_json(*a, **k), client=None, clan_tag=TAG)


class FakeGroup:
    def __init__(self, season, state, wars):
        self.season, self.state = season, state
        self._wars = wars
        self.rounds = [[w.war_tag] for w in wars]

    def get_wars_for_clan(self, tag):
        return FakeLog(self._wars)


def raid_json(state, start, end, members):
    return coc.RaidLogEntry(data={
        "state": state, "startTime": ts(start), "endTime": ts(end),
        "capitalTotalLoot": 123456, "raidsCompleted": 3, "totalAttacks": 40,
        "enemyDistrictsDestroyed": 17, "offensiveReward": 900, "defensiveReward": 300,
        "attackLog": [], "defenseLog": [],
        "members": [{"tag": f"#R{i}", "name": n, "attacks": a, "attackLimit": 5,
                     "bonusAttackLimit": b, "capitalResourcesLooted": l}
                    for i, (n, a, b, l) in enumerate(members)],
    }, client=None)


async def run():
    storage._save_notify_chats([-100123])  # без чатов из репозитория
    bot = FakeBot()

    # ── Война: подготовка → бой → напоминания → итоги ───────────────────────
    start = NOW + timedelta(hours=5)
    ms = [member(1, "Alpha", [3, 2]), member(2, "<Bravo&>", []), member(3, "Charlie", [1])]
    State.war = mk_war("preparation", start, start + timedelta(hours=24), ms)
    await notifier.war_tick(bot); await notifier.war_tick(bot)
    assert len(bot.sent) == 1 and "Соперник найден" in bot.sent[0], bot.sent

    start = NOW - timedelta(minutes=5)
    State.war = mk_war("inWar", start, start + timedelta(hours=24), ms)
    await notifier.war_tick(bot); await notifier.war_tick(bot)
    assert len(bot.sent) == 2 and "Война началась" in bot.sent[1]
    assert "&lt;Bravo&amp;&gt;" not in bot.sent[1]  # в старте нет списков

    start = NOW - timedelta(hours=21)
    State.war = mk_war("inWar", start, start + timedelta(hours=24), ms)  # тот же id? нет — другое время
    # сохраняем тот же идентификатор войны: подготовка считается от start
    sec = storage.load_notify_state()["war"]
    sec["id"] = notifier._war_id(State.war)
    st = storage.load_notify_state(); st["war"] = sec; storage.save_notify_state(st)
    await notifier.war_tick(bot)
    assert "До конца войны" in bot.sent[-1] and "&lt;Bravo&amp;&gt;" in bot.sent[-1], bot.sent[-1]
    n = len(bot.sent)
    await notifier.war_tick(bot)
    assert len(bot.sent) == n

    State.war = mk_war("warEnded", start, NOW - timedelta(minutes=2), ms)
    await notifier.war_tick(bot); await notifier.war_tick(bot)
    assert bot.sent[-1].count("Победа") == 1 or "Поражение" in bot.sent[-1]
    assert len(bot.sent) == n + 1
    print("WAR OK:", len(bot.sent), "сообщений")

    # ── ЛВК: 7 раундов, переходы между раундами ─────────────────────────────
    State.war = None
    bot.sent.clear()
    base = NOW - timedelta(minutes=10)
    def cwl(i, state, s):
        return mk_war(state, s, s + timedelta(hours=24), [member(1, "Alpha", [3] if state != "preparation" else []),
                                                         member(2, "Bravo", [])], cwl=True, tag=f"#W{i}")
    State.group = FakeGroup("2026-11", "inWar", [cwl(1, "inWar", base), cwl(2, "preparation", base + timedelta(days=1))])
    await notifier.cwl_tick(bot); await notifier.cwl_tick(bot)
    assert len(bot.sent) == 1 and "раунд 1 начался" in bot.sent[0], bot.sent
    # раунд 1 закончился, раунд 2 начался в тот же момент
    State.group = FakeGroup("2026-11", "inWar", [cwl(1, "warEnded", base - timedelta(hours=24)),
                                                  cwl(2, "inWar", base), cwl(3, "preparation", base + timedelta(days=1))])
    await notifier.cwl_tick(bot)
    assert "раунд 1:" in bot.sent[1] and "раунд 2 начался" in bot.sent[2], bot.sent[1:]
    # последний, 7-й раунд (prep-раунда больше нет) — раньше не объявлялся
    wars = [cwl(i, "warEnded", base - timedelta(days=8 - i)) for i in range(1, 7)] + [cwl(7, "inWar", base)]
    State.group = FakeGroup("2026-11", "inWar", wars)
    await notifier.cwl_tick(bot)
    assert any("раунд 7 начался" in t for t in bot.sent), bot.sent
    # напоминание за 3 часа
    wars[-1] = cwl(7, "inWar", NOW - timedelta(hours=22))
    State.group = FakeGroup("2026-11", "inWar", wars)
    await notifier.cwl_tick(bot)
    assert "осталось" in bot.sent[-1] and "Bravo" in bot.sent[-1], bot.sent[-1]
    wars[-1] = cwl(7, "warEnded", NOW - timedelta(hours=24, minutes=1))
    State.group = FakeGroup("2026-11", "ended", wars)
    k = len(bot.sent)
    await notifier.cwl_tick(bot); await notifier.cwl_tick(bot)
    assert len(bot.sent) == k + 1 and "раунд 7:" in bot.sent[-1]
    text, _ = views.cwl_status("2026-11", [(i + 1, w) for i, w in enumerate(wars)])
    check_html(text)
    print("CWL OK:", len(bot.sent), "сообщений")

    # ── Рейды ────────────────────────────────────────────────────────────────
    bot.sent.clear()
    rs = NOW - timedelta(minutes=30)
    State.raid = raid_json("ongoing", rs, rs + timedelta(days=3), [("Alpha", 6, 1, 20000), ("Bravo", 2, 0, 5000)])
    await notifier.raid_tick(bot); await notifier.raid_tick(bot)
    assert len(bot.sent) == 1 and "Рейды столицы начались" in bot.sent[0]
    rs2 = NOW - timedelta(days=3) + timedelta(hours=2)
    State.raid = raid_json("ongoing", rs, NOW + timedelta(hours=2), [("Alpha", 6, 1, 20000), ("Bravo", 2, 0, 5000)])
    await notifier.raid_tick(bot)
    assert "До конца рейдов" in bot.sent[-1] and "Idle" in bot.sent[-1] and "осталось 3" in bot.sent[-1], bot.sent[-1]
    State.raid = raid_json("ended", rs, NOW - timedelta(minutes=1), [("Alpha", 6, 1, 20000), ("Bravo", 5, 0, 9000)])
    await notifier.raid_tick(bot); await notifier.raid_tick(bot)
    assert "завершены" in bot.sent[-1] and "17" in bot.sent[-1] and "6/6" in bot.sent[-1], bot.sent[-1]
    assert len(bot.sent) == 3
    print("RAID OK")

    # ── Без чатов рассылки — событие не теряется ────────────────────────────
    storage.remove_notify_chat(-100123)
    State.raid = raid_json("ongoing", NOW, NOW + timedelta(days=3), [])
    bot.sent.clear()
    await notifier.raid_tick(bot)
    assert not bot.sent
    storage.add_notify_chat(-100123)
    await notifier.raid_tick(bot)
    assert len(bot.sent) == 1
    check_html(notifier.status_text())
    print("STATUS OK")
    for t in []:
        print(t)
    return True


def test_all():
    assert asyncio.run(run())


if __name__ == "__main__":
    asyncio.run(run())
    print("Все проверки пройдены")
