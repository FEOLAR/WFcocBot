"""
Единая система форматирования сообщений бота Warfil.
Оптимизировано под мобильный Telegram.
"""

DIV  = "━━━━━━━━━━━━━━━━━━━━"
DIVs = "· · · · · · · · · ·"
BULL = "▸"
DOT  = "◆"


def clan_header(extra: str = "") -> str:
    base = "🏰 <b>WARFIL</b>"
    return f"{base}  ·  {extra}" if extra else base


def section(icon: str, title: str) -> str:
    return f"\n{icon} <b>{title}</b>"


def stat_line(label: str, value: str, icon: str = DOT) -> str:
    """Строка статистики: ◆ Уровень  ·  13"""
    return f"{icon} {label}  ·  <b>{value}</b>"


def member_line(name: str, th: int | None = None, tg: str | None = None) -> str:
    th_part = f"<code>ТХ{th}</code>" if th else ""
    tg_part = f"  <i>@{tg}</i>" if tg else ""
    if th_part:
        return f"  {BULL} {th_part} {name}{tg_part}"
    return f"  {BULL} {name}{tg_part}"


def attacks_bar(used: int, total: int, width: int = 10) -> str:
    """Полоска прогресса атак: ◼◼◼◼◼◻◻◻◻◻"""
    filled = round(used / total * width) if total else 0
    bar = "◼" * filled + "◻" * (width - filled)
    pct = round(used / total * 100) if total else 0
    return f"<code>{bar}</code>  {pct}%"


def ok(text: str) -> str:
    return f"✅ {text}"


def err(text: str) -> str:
    return f"❌ {text}"


def warn(text: str) -> str:
    return f"⚠️ {text}"


def footer() -> str:
    return f"\n{DIVs}\n🌐 <i>warfilcoc.ru</i>"
