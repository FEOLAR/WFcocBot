"""
Клиент Clash of Clans API: авторизация, автоматическое пересоздание ключа
под текущий IP сервера и безопасный вызов методов.
"""
import os
import re as _re
import asyncio
import logging

import config

logger = logging.getLogger(__name__)

COC_EMAIL    = config.COC_EMAIL
COC_PASSWORD = config.COC_PASSWORD
COC_API_KEY  = config.COC_API_KEY
CLAN_TAG     = config.CLAN_TAG

# ── Proxy setup for Clash of Clans API (static IP) ──────────────────────────
# Set COC_PROXY in config.py or env to a static HTTP/SOCKS proxy URL.
# Example: http://user:pass@proxy.example.com:8080
# This keeps the same IP across restarts so the API key never changes.
_COC_PROXY = config.COC_PROXY or os.environ.get("QUOTAGUARD_URL")
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

import coc

def _norm_name(name: str) -> str:
    """NFC + lowercase + удаляем невидимые/форматирующие Unicode символы.

    CoC и Telegram могут по-разному хранить невидимые символы в никах:
    монгольские вариационные селекторы (U+180B-U+180F), variation selectors
    (U+FE00-U+FE0F), нулевые пробелы (U+200B-U+200F), BOM и т.п.
    Фильтруем точечно, чтобы не затронуть реальные диакритики (ā, ŕ, ķ...).
    """
    import unicodedata as _ud
    # Диапазоны полностью невидимых символов
    _INVIS = [
        (0x180B, 0x180F),   # Mongolian Free Variation Selectors
        (0xFE00, 0xFE0F),   # Variation Selectors VS1-VS16
        (0xE0100, 0xE01EF), # Variation Selectors Supplement
        (0x200B, 0x200F),   # Zero-width space, ZWNJ, ZWJ, LRM, RLM
        (0x2060, 0x2064),   # Word joiner and invisible operators
        (0xFEFF, 0xFEFF),   # BOM / ZWNBSP
        (0x00AD, 0x00AD),   # Soft hyphen
    ]
    def _is_invis(ch: str) -> bool:
        cat = _ud.category(ch)
        if cat in ("Cf", "Cc"):
            return True
        cp = ord(ch)
        return any(s <= cp <= e for s, e in _INVIS)

    normalized = _ud.normalize("NFC", name)
    return "".join(ch for ch in normalized if not _is_invis(ch)).lower()

coc_client = coc.Client()


def _extract_ip_from_error(err_str: str) -> str | None:
    """Извлекаем реальный исходящий IP из текста ошибки CoC."""
    m = _re.search(r'from IP (\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', err_str)
    return m.group(1) if m else None


# IP → unix-timestamp последнего появления (сохраняется между перезапусками)
_known_server_ips: dict[str, float] = {}

_COC_DEV_URL = "https://developer.clashofclans.com/api"
_KEY_NAME     = "warfil_bot"
import storage as _storage
_KNOWN_IPS_FILE = os.path.join(_storage.DATA_DIR, "known_ips.json")


def _load_known_ips() -> dict[str, float]:
    """Загружаем сохранённые IP; поддерживаем старый формат (list) → конвертируем."""
    try:
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


MAX_REKEY_ROUNDS = 5   # раньше было бесконечно — и монитор мог зависнуть навсегда


async def _coc_safe(fn, *args, **kwargs):
    """Вызов CoC API. При блокировке по IP пересоздаёт ключ и повторяет,
    но не больше MAX_REKEY_ROUNDS раз — потом пробрасывает ошибку."""
    import coc
    attempt = 0
    while True:
        try:
            return await fn(*args, **kwargs)
        except (coc.errors.Forbidden, coc.errors.HTTPException, coc.errors.PrivateWarLog) as e:
            if not (_is_ip_error(e) and COC_EMAIL and COC_PASSWORD):
                raise
            attempt += 1
            if attempt > MAX_REKEY_ROUNDS:
                logger.error("CoC: не удалось обойти блокировку по IP — сдаюсь до следующего цикла")
                raise
            bad_ip = _extract_ip_from_error(str(e))
            logger.warning(f"CoC IP ошибка, попытка {attempt} (IP={bad_ip}) — обновляю ключ...")
            async with _get_rekey_lock():
                if bad_ip:
                    _register_ip(bad_ip)
                rekey_ok = False
                for rekey_attempt in range(3):
                    try:
                        rekey_ok = await _rekey_coc()
                        if rekey_ok:
                            break
                    except Exception as rekey_err:
                        logger.error(f"_rekey_coc ошибка: {rekey_err}", exc_info=True)
                    await asyncio.sleep(3)
                if not rekey_ok:
                    await asyncio.sleep(10)
                    continue
            await asyncio.sleep(1)


def norm_tag(tag: str | None) -> str:
    return (tag or "").lstrip("#").upper()


def is_our_tag(tag: str | None) -> bool:
    return norm_tag(tag) == norm_tag(CLAN_TAG)


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


async def login():
    """Авторизация клиента при старте бота."""
    if COC_API_KEY:
        await coc_client.login_with_tokens(COC_API_KEY)
        logger.info("CoC клиент авторизован через API ключ")
        return
    if not (COC_EMAIL and COC_PASSWORD):
        raise RuntimeError("Не заданы COC_API_KEY или COC_EMAIL/COC_PASSWORD в .env")
    real_ip = await _detect_real_outbound_ip()
    if real_ip:
        logger.info(f"Реальный исходящий IP сервера: {real_ip}")
    else:
        logger.warning("Не удалось определить исходящий IP")
    if await _rekey_coc(new_ip=real_ip):
        logger.info("CoC клиент авторизован через ключ под IP сервера")
    else:
        logger.warning("_rekey_coc не удался — обычный логин через coc.py")
        await coc_client.login(COC_EMAIL, COC_PASSWORD)
