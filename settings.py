"""
Настройки бота WarFil.

Порядок поиска: переменные окружения (.env или панель хостинга) →
старый config.py (если в нём что-то заполнено).

Совет: перенеси токен и пароль из config.py в переменные окружения
и очисти config.py — пока репозиторий открыт, их видит кто угодно.
"""
import os
import pathlib

BASE_DIR = pathlib.Path(__file__).resolve().parent

try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass


try:
    import config as _legacy   # старый файл с секретами, если есть
except ImportError:
    _legacy = None


def _env(name: str, default: str = "") -> str:
    val = os.environ.get(name, "").strip()
    if not val and _legacy is not None:
        val = str(getattr(_legacy, name, "") or "").strip()
    return val or default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


# ── Секреты ───────────────────────────────────────────────────────────────────
# Bothost и другие хостинги называют переменную по-разному — берём любую
TELEGRAM_BOT_TOKEN = (_env("TELEGRAM_BOT_TOKEN") or _env("BOT_TOKEN")
                      or _env("TELEGRAM_TOKEN") or _env("TOKEN"))

# Вариант 1: готовый API-ключ с developer.clashofclans.com (для статического IP)
COC_API_KEY = _env("COC_API_KEY")
# Вариант 2: вход через аккаунт разработчика — бот сам пересоздаёт ключ под IP
COC_EMAIL = _env("COC_EMAIL")
COC_PASSWORD = _env("COC_PASSWORD")
# Необязательно: http://логин:пароль@адрес:порт
COC_PROXY = _env("COC_PROXY")

# ── Клан ──────────────────────────────────────────────────────────────────────
CLAN_TAG = _env("CLAN_TAG", "#2R02GGRUJ")
CLAN_BRAND = _env("CLAN_BRAND", "WarFil")
CLAN_WEBSITE = _env("CLAN_WEBSITE", "https://www.warfilcoc.ru")
TG_GROUP_LINK = _env("TG_GROUP_LINK", "https://t.me/warfil_clan")
ADMIN_USERNAME = _env("ADMIN_USERNAME", "feolar")
CLAN_GROUP_CHAT_ID = _env_int("CLAN_GROUP_CHAT_ID", 0)

# ── Время ─────────────────────────────────────────────────────────────────────
# Смещение от UTC для отображения времени в сообщениях (Москва = 3)
TZ_OFFSET_HOURS = _env_int("TZ_OFFSET_HOURS", 3)
TZ_LABEL = _env("TZ_LABEL", "МСК")

# ── Уведомления ───────────────────────────────────────────────────────────────
# Как часто (минуты) присылать сводку атак во время войны / раунда ЛВК / рейдов.
# 0 — не присылать, останутся только старт, напоминания и итоги.
WAR_DIGEST_MINUTES = _env_int("WAR_DIGEST_MINUTES", 120)
CWL_DIGEST_MINUTES = _env_int("CWL_DIGEST_MINUTES", 0)
RAID_DIGEST_MINUTES = _env_int("RAID_DIGEST_MINUTES", 0)

# Папка с данными бота (привязки, чаты рассылки, состояние уведомлений).
# Bothost сам задаёт DATA_DIR=/app/data — это постоянный диск.
# По умолчанию — data/ рядом с main.py.
DATA_DIR = _env("DATA_DIR")

# Набор кастомных эмодзи (имя из ссылки t.me/addemoji/<имя>).
# Подтягивается при старте. Работает, если у владельца бота есть Telegram Premium.
EMOJI_PACK = _env("EMOJI_PACK")
