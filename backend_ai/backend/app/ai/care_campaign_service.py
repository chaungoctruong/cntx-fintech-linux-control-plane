from __future__ import annotations

import asyncio
import hashlib
import html
import importlib
import json
import logging
import os
import pickle
import random
import re
import socket
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote_plus, urlparse

import httpx

from app.core.log_hygiene import log_periodic, noisy_log_cooldown_sec
from app.core.redis_client import get_redis
from app.repositories.control_plane_repository import ControlPlaneRepository
from app.services.store_service import get_process_store
from app.settings import settings
from app.ai.prompts import MORNING_DIGEST_SYSTEM_PROMPT

try:
    from app.ai.providers.gemini import gemini_engine
except Exception:  # pragma: no cover
    gemini_engine = None

log = logging.getLogger("ai_care_campaign")

# =========================================================
# GLOBALS
# =========================================================
_TASK: Optional[asyncio.Task] = None
_STOP_EVENT: Optional[asyncio.Event] = None
_HTTP_CLIENT: Optional[httpx.AsyncClient] = None

_CAMPAIGN_LEADER_KEY = "ai:care:campaign:leader"
_CAMPAIGN_LEADER_TTL_SEC = 300
_CAMPAIGN_LEADER_TOKEN: Optional[str] = None
_CARE_LEADER_LOGGED = False
_CARE_REDIS_GUARD_LOG_TS = 0.0
_CARE_LAST_USER_COUNT: Optional[int] = None

_LOCAL_MARKERS: dict[str, int] = {}
_LOCAL_RATE_BUCKET: dict[str, int] = {}
_LOCAL_DIGEST_CACHE: dict[str, tuple[int, "SharedDigest"]] = {}
_LOCAL_DIGEST_GEMINI_ATTEMPTED: dict[str, bool] = {}
_LOCAL_QUOTE_CACHE: dict[str, tuple[int, "MarketQuoteSnapshot"]] = {}

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_HUBBOT_STATE_PATH = _PROJECT_ROOT / "hubbot" / "hubbot_state.pickle"
_BACKEND_RECIPIENT_TABLES: tuple[tuple[str, str, str, str], ...] = (
    ("audit_logs", "telegram_id", "COALESCE(created_at, 0)", "audit_logs"),
)

_DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

_MAX_NEWS_LINKS_HARD_CAP = 10
_TELEGRAM_HTML_HARD_CAP = 3500

# Đổi CTA cho mềm hơn, đúng hướng "tin sáng + kéo về bot"
_START_CTA_HTML = "👉 Sếp gõ <b>/start</b> để mở bot và kiểm tra nhanh trạng thái nhé."

# =========================================================
# BRAND / PROMPT
# =========================================================
CNTX_MORNING_BRAND_PROMPT = """\
Bạn là CNTx labs - trợ lý trading và customer care cao cấp.
Phong cách:
- Viết tiếng Việt tự nhiên, ngắn, có lực, đúng chất trader Telegram.
- Không spam, không văn dài, không hô lệnh, không overpromise.
- Khi nói tin nóng: tóm tắt như người trong nghề, 1 câu ngắn / tin.
- Có thể dùng giọng kiểu: 'Ô kìa...', 'Pha này...', 'Coi chừng...' nhưng không lố.
- Luôn trung tính, không phán chắc giá sẽ tăng/giảm.
- Ưu tiên giải thích tác động lên market/trading/risk.
- Morning digest có thể gom tối đa 10 tin, mỗi tin 1 dòng ngắn.
- Không nhắc nội quy, không nói mình là AI.
"""

# =========================================================
# NEWS SEARCH CONFIG
# =========================================================
_NEWS_QUERY_MAP = {
    "war_markets": "Ukraine Russia Israel Iran oil gold markets",
    "macro": "Fed inflation interest rates dollar gold stock market today",
    "trading": "gold oil bitcoin forex stocks market today",
    "risk_assets": "S&P 500 Nasdaq yields dollar market today",
    "global_finance": "financial markets today geopolitics economy",
}

_HIGH_SIGNAL_KEYWORDS = {
    "strait of hormuz": 9,
    "hormuz": 8,
    "iran": 7,
    "israel": 6,
    "ukraine": 5,
    "russia": 5,
    "war": 5,
    "conflict": 4,
    "missile": 5,
    "attack": 5,
    "strike": 5,
    "fed": 6,
    "fomc": 6,
    "powell": 4,
    "cpi": 6,
    "inflation": 5,
    "nfp": 6,
    "payrolls": 5,
    "unemployment": 4,
    "rate cut": 4,
    "rates": 3,
    "oil": 6,
    "crude": 6,
    "brent": 4,
    "gold": 5,
    "bitcoin": 5,
    "crypto": 3,
    "forex": 3,
    "dollar": 4,
    "nasdaq": 2,
    "s&p 500": 2,
    "yield": 3,
    "recession": 3,
}

_SOURCE_BOOST = {
    "reuters": 4,
    "bloomberg": 4,
    "financial times": 3,
    "ft.com": 3,
    "wsj": 3,
    "wall street journal": 3,
    "associated press": 2,
    "apnews": 2,
    "cnbc": 2,
    "marketwatch": 2,
    "investing.com": 2,
    "yahoo finance": 1,
}


@dataclass
class UserCareSnapshot:
    telegram_id: str
    linked_accounts: int = 0
    running_accounts: int = 0
    last_activity_ts: int = 0
    source: str = "unknown"


@dataclass
class NewsItem:
    title: str
    link: str
    source: str
    published_ts: int
    category: str
    score: float = 0.0


@dataclass
class DigestLine:
    line: str
    link: str
    source: str


@dataclass
class SharedDigest:
    date_key: str
    headline: str
    lines: list[DigestLine]
    built_with_gemini: bool
    raw_news: list[NewsItem]


@dataclass(frozen=True)
class MarketQuoteRequest:
    label: str
    stooq_symbol: str
    display_symbol: str
    asset_class: str
    unit: str
    decimals: int
    source: str = "stooq"
    base_currency: str = ""
    quote_currency: str = ""


@dataclass
class MarketQuoteSnapshot:
    request: MarketQuoteRequest
    date_raw: str
    time_raw: str
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume_raw: str = ""


# =========================================================
# SETTINGS
# =========================================================
def _enabled() -> bool:
    return bool(getattr(settings, "AI_CARE_CAMPAIGN_ENABLED", False))


def _dry_run() -> bool:
    return bool(getattr(settings, "AI_CARE_DRY_RUN", True))


def _news_enabled() -> bool:
    return bool(getattr(settings, "AI_CARE_INCLUDE_NEWS", True))


def _news_use_gemini() -> bool:
    return bool(getattr(settings, "AI_CARE_NEWS_USE_GEMINI", False))


def _require_redis() -> bool:
    return bool(getattr(settings, "AI_CARE_REQUIRE_REDIS", True))


def _include_hubbot_chats() -> bool:
    return bool(getattr(settings, "AI_CARE_INCLUDE_HUBBOT_CHATS", True))


def _hubbot_state_path() -> Path:
    raw = str(getattr(settings, "AI_CARE_HUBBOT_STATE_PATH", "") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _DEFAULT_HUBBOT_STATE_PATH


def _row_ts(row: dict[str, Any]) -> int:
    for key in ("last_used_at", "updated_at", "created_at", "last_activity_ts"):
        try:
            val = int(row.get(key) or 0)
            if val > 0:
                return val
        except Exception:
            continue
    return 0


def _emit_user_collection_summary(count: int) -> bool:
    global _CARE_LAST_USER_COUNT
    normalized = max(0, int(count or 0))
    if _CARE_LAST_USER_COUNT == normalized:
        return False
    _CARE_LAST_USER_COUNT = normalized
    log.info("[AI_CARE] Collected %s unique telegram users", normalized)
    return True


def _local_now() -> time.struct_time:
    offset = int(getattr(settings, "AI_CARE_TIMEZONE_OFFSET_HOURS", 7))
    return time.gmtime(time.time() + (offset * 3600))


def _today_key() -> str:
    t = _local_now()
    return f"{t.tm_year:04d}{t.tm_mon:02d}{t.tm_mday:02d}"


def _in_morning_window() -> bool:
    t = _local_now()
    hour = int(getattr(settings, "AI_CARE_MORNING_HOUR", 7))
    win_min = max(20, int(getattr(settings, "AI_CARE_MORNING_WINDOW_MIN", 90)))

    current_minute = (t.tm_hour * 60) + t.tm_min
    start_minute = hour * 60
    end_minute = start_minute + win_min

    if end_minute < 24 * 60:
        return start_minute <= current_minute < end_minute

    wrapped_end = end_minute - (24 * 60)
    return current_minute >= start_minute or current_minute < wrapped_end


def _news_cache_ttl_sec() -> int:
    return max(300, int(getattr(settings, "AI_CARE_NEWS_CACHE_TTL_SEC", 1800)))


def _max_news_links() -> int:
    val = int(getattr(settings, "AI_CARE_NEWS_MAX_LINKS", 10))
    return max(1, min(_MAX_NEWS_LINKS_HARD_CAP, val))


def _news_stale_hours() -> int:
    return max(6, int(getattr(settings, "AI_CARE_NEWS_STALE_HOURS", 720)))


def _news_query_hl() -> str:
    return str(getattr(settings, "AI_CARE_GOOGLE_NEWS_HL", "en-US") or "en-US").strip()


def _news_query_gl() -> str:
    return str(getattr(settings, "AI_CARE_GOOGLE_NEWS_GL", "US") or "US").strip()


def _news_query_ceid() -> str:
    return str(getattr(settings, "AI_CARE_GOOGLE_NEWS_CEID", "US:en") or "US:en").strip()


def _telegram_batch_size() -> int:
    return max(1, min(30, int(getattr(settings, "AI_CARE_SEND_BATCH_SIZE", 25))))


def _telegram_batch_sleep_sec() -> float:
    return max(0.6, float(getattr(settings, "AI_CARE_SEND_BATCH_SLEEP_SEC", 0.9)))


def _gemini_digest_cache_ttl_sec() -> int:
    return max(1800, int(getattr(settings, "AI_CARE_DIGEST_CACHE_TTL_SEC", 36 * 3600)))


def _send_retry_backoff_sec() -> int:
    # giảm mặc định để user fail còn có cơ hội nhận lại trong cùng buổi sáng
    return max(30, int(getattr(settings, "AI_CARE_SEND_RETRY_BACKOFF_SEC", 60)))


def _telegram_hard_block_ttl_sec() -> int:
    # Telegram hard-fail như "chat not found" thường không tự hết trong vài phút.
    return max(3600, int(getattr(settings, "AI_CARE_TELEGRAM_HARD_BLOCK_TTL_SEC", 7 * 24 * 3600)))


def _extra_telegram_ids() -> list[str]:
    raw = str(getattr(settings, "AI_CARE_EXTRA_TELEGRAM_IDS", "") or "").strip()
    if not raw:
        return []
    parts = re.split(r"[,\s;|]+", raw)
    result: list[str] = []
    for item in parts:
        tg = _normalize_telegram_id(item)
        if tg:
            result.append(tg)
    return result


# =========================================================
# COMMON HELPERS
# =========================================================
def _clean_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _truncate_text(text: str, max_len: int) -> str:
    text = str(text or "")
    if len(text) <= max_len:
        return text
    return text[: max(0, max_len - 1)].rstrip() + "…"


def _ensure_start_cta_html(text: str) -> str:
    clean_text = str(text or "").strip()
    if not clean_text:
        return _START_CTA_HTML
    if "/start" in clean_text.lower():
        return clean_text
    return f"{clean_text}\n{_START_CTA_HTML}"


def _normalize_dedupe_key(text: str) -> str:
    text = html.unescape(str(text or "")).lower()
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_telegram_id(value: Any) -> str:
    text = str(value or "").strip()
    text = text.replace(" ", "")
    if text.startswith("+"):
        text = text[1:]
    return text if text.isdigit() else ""


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _safe_link(link: str) -> str:
    return html.escape(link or "#", quote=True)


def _telegram_html_size(text: str) -> int:
    raw = str(text or "")
    return max(len(raw), len(raw.encode("utf-8")))


def _telegram_html_fits(text: str) -> bool:
    return _telegram_html_size(text) <= _TELEGRAM_HTML_HARD_CAP


def _fit_telegram_html_lines(text: str) -> str:
    clean = str(text or "").strip()
    if _telegram_html_fits(clean):
        return clean

    lines = [line for line in clean.splitlines() if line.strip()]
    if not lines:
        return ""

    tail: list[str] = []
    if "/start" in lines[-1].lower():
        tail = [lines.pop()]

    kept: list[str] = []
    for line in lines:
        candidate = "\n".join([*kept, line, *tail]).strip()
        if _telegram_html_fits(candidate):
            kept.append(line)
        else:
            break

    fitted = "\n".join([*kept, *tail]).strip()
    if fitted and _telegram_html_fits(fitted):
        return fitted

    if tail and _telegram_html_fits(tail[0]):
        return tail[0]

    return html.escape(_truncate_text(re.sub(r"<[^>]*>", " ", html.unescape(clean)), 500))


# =========================================================
# REDIS / LOCK / DEDUP
# =========================================================
async def _allow_rate_send() -> bool:
    per_sec_max = _telegram_batch_size()
    second_bucket = int(time.time())
    key = f"ai:care:rate:{second_bucket}"

    redis = await get_redis(decode_responses=True)
    if redis is not None:
        try:
            cnt = int(await redis.incr(key))
            if cnt == 1:
                await redis.expire(key, 5)
            return cnt <= per_sec_max
        except Exception:
            pass

    cnt_local = int(_LOCAL_RATE_BUCKET.get(key) or 0) + 1
    _LOCAL_RATE_BUCKET[key] = cnt_local
    for old_key in list(_LOCAL_RATE_BUCKET.keys()):
        if not old_key.endswith(str(second_bucket)):
            _LOCAL_RATE_BUCKET.pop(old_key, None)
    return cnt_local <= per_sec_max


async def _ensure_campaign_leader() -> bool:
    global _CAMPAIGN_LEADER_TOKEN, _CARE_LEADER_LOGGED, _CARE_REDIS_GUARD_LOG_TS
    redis = await get_redis(decode_responses=True)
    if redis is None:
        _CAMPAIGN_LEADER_TOKEN = None
        if _require_redis():
            now = time.time()
            if (now - _CARE_REDIS_GUARD_LOG_TS) >= 60.0:
                _CARE_REDIS_GUARD_LOG_TS = now
                log.warning("[AI_CARE] Redis unavailable -> skip campaign to avoid duplicate morning broadcasts")
            return False
        # backward-compatible cho môi trường dev cũ
        return True

    token = f"{socket.gethostname()}:{os.getpid()}"
    try:
        if _CAMPAIGN_LEADER_TOKEN:
            current = await redis.get(_CAMPAIGN_LEADER_KEY)
            if current == _CAMPAIGN_LEADER_TOKEN:
                await redis.set(_CAMPAIGN_LEADER_KEY, _CAMPAIGN_LEADER_TOKEN, ex=_CAMPAIGN_LEADER_TTL_SEC)
                return True
            _CAMPAIGN_LEADER_TOKEN = None

        ok = await redis.set(_CAMPAIGN_LEADER_KEY, token, nx=True, ex=_CAMPAIGN_LEADER_TTL_SEC)
        if ok:
            _CAMPAIGN_LEADER_TOKEN = token
            if not _CARE_LEADER_LOGGED:
                _CARE_LEADER_LOGGED = True
                log.info("[AI_CARE] This worker holds campaign leader lock")
            return True
        return False
    except Exception as exc:
        log.warning("[AI_CARE] Leader ensure failed: %s", exc)
        return False


async def _release_campaign_leader() -> None:
    global _CAMPAIGN_LEADER_TOKEN, _CARE_LEADER_LOGGED
    token = _CAMPAIGN_LEADER_TOKEN
    _CAMPAIGN_LEADER_TOKEN = None
    _CARE_LEADER_LOGGED = False

    if not token:
        return

    redis = await get_redis(decode_responses=True)
    if redis is None:
        return

    try:
        current = await redis.get(_CAMPAIGN_LEADER_KEY)
        if current == token:
            await redis.delete(_CAMPAIGN_LEADER_KEY)
    except Exception:
        pass


async def _mark_once(key: str, ttl_sec: int) -> bool:
    now = int(time.time())
    redis = await get_redis(decode_responses=True)
    if redis is not None:
        try:
            ok = await redis.set(key, "1", nx=True, ex=max(1, int(ttl_sec)))
            return bool(ok)
        except Exception:
            pass

    if random.random() < 0.05:
        expired_keys = [k for k, exp in _LOCAL_MARKERS.items() if exp <= now]
        for old_key in expired_keys:
            _LOCAL_MARKERS.pop(old_key, None)

    exp = int(_LOCAL_MARKERS.get(key) or 0)
    if exp > now:
        return False

    _LOCAL_MARKERS[key] = now + max(1, int(ttl_sec))
    return True


async def _marker_exists(key: str) -> bool:
    now = int(time.time())
    redis = await get_redis(decode_responses=True)
    if redis is not None:
        try:
            return bool(await redis.get(key))
        except Exception:
            pass
    return int(_LOCAL_MARKERS.get(key) or 0) > now


async def _marker_delete(key: str) -> None:
    redis = await get_redis(decode_responses=True)
    if redis is not None:
        try:
            await redis.delete(key)
            return
        except Exception:
            pass
    _LOCAL_MARKERS.pop(key, None)


async def _cache_get_json(key: str) -> Optional[dict[str, Any]]:
    redis = await get_redis(decode_responses=True)
    if redis is not None:
        try:
            raw = await redis.get(key)
            if raw:
                return json.loads(raw)
        except Exception:
            pass
    return None


async def _cache_set_json(key: str, payload: dict[str, Any], ttl_sec: int) -> None:
    redis = await get_redis(decode_responses=True)
    if redis is not None:
        try:
            await redis.set(key, json.dumps(payload, ensure_ascii=False), ex=max(1, ttl_sec))
            return
        except Exception:
            pass


# =========================================================
# HTTP / RSS FETCH
# =========================================================
async def _get_http_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        timeout_sec = float(getattr(settings, "AI_CARE_HTTP_TIMEOUT_SEC", 12.0) or 12.0)
        _HTTP_CLIENT = httpx.AsyncClient(
            timeout=timeout_sec,
            headers={
                "User-Agent": str(getattr(settings, "AI_CARE_HTTP_USER_AGENT", _DEFAULT_UA)),
                "Accept": "application/rss+xml, application/xml, text/xml, text/html;q=0.9, */*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
            },
            limits=httpx.Limits(max_keepalive_connections=50, max_connections=200),
            follow_redirects=True,
        )
    return _HTTP_CLIENT


def _google_news_rss_url(query: str) -> str:
    q = quote_plus(query)
    return (
        f"https://news.google.com/rss/search?q={q}"
        f"&hl={quote_plus(_news_query_hl())}"
        f"&gl={quote_plus(_news_query_gl())}"
        f"&ceid={quote_plus(_news_query_ceid())}"
    )


def _iter_rss_queries() -> list[tuple[str, str]]:
    return [(category, _google_news_rss_url(query)) for category, query in _NEWS_QUERY_MAP.items()]


def _xml_localname(tag: str) -> str:
    if not tag:
        return ""
    if "}" in tag:
        return tag.rsplit("}", 1)[-1].lower()
    return tag.lower()


def _find_child_text(node: ET.Element, child_name: str) -> str:
    child_name = child_name.lower()
    for child in list(node):
        if _xml_localname(child.tag) == child_name:
            return (child.text or "").strip()
    return ""


def _find_child_attr_or_text(node: ET.Element, child_name: str, attr_name: str) -> str:
    child_name = child_name.lower()
    for child in list(node):
        if _xml_localname(child.tag) == child_name:
            return (child.attrib.get(attr_name) or child.text or "").strip()
    return ""


def _parse_pubdate_to_ts(value: str) -> int:
    if not value:
        return 0
    try:
        dt = parsedate_to_datetime(value)
        return int(dt.timestamp())
    except Exception:
        return 0


def _normalize_title(title: str) -> str:
    clean = re.sub(r"\s+", " ", (title or "").strip())
    clean = re.sub(r"\s+[\-|–—]\s+[A-Za-z0-9 .,&']{2,35}$", "", clean)
    return clean.strip(" -–—")


def _normalize_key(text: str) -> str:
    text = re.sub(r"https?://", "", (text or "").lower())
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _infer_source(link: str, explicit_source: str = "") -> str:
    if explicit_source:
        return explicit_source.strip()
    try:
        host = urlparse(link).netloc.lower().replace("www.", "")
        if host.startswith("news.google."):
            return "Google News"
        return host or "Unknown"
    except Exception:
        return "Unknown"


def _score_news_item(item: NewsItem, now_ts: int) -> float:
    age_hours = 9999.0
    if item.published_ts > 0:
        age_hours = max(0.0, (now_ts - item.published_ts) / 3600.0)

    if age_hours > float(_news_stale_hours()):
        return -1.0

    if age_hours <= 3:
        recency_score = 15.0
    elif age_hours <= 6:
        recency_score = 11.0
    elif age_hours <= 12:
        recency_score = 8.0
    elif age_hours <= 24:
        recency_score = 5.0
    else:
        recency_score = 2.0

    title_lc = f" {_normalize_key(item.title)} "
    kw_score = 0.0
    for keyword, weight in _HIGH_SIGNAL_KEYWORDS.items():
        if f" {_normalize_key(keyword)} " in title_lc:
            kw_score += float(weight)

    src_lc = item.source.lower()
    source_score = 0.0
    for src_key, weight in _SOURCE_BOOST.items():
        if src_key in src_lc:
            source_score += float(weight)
            break

    category_score = {"war_markets": 3.0, "macro": 2.0, "trading": 1.0}.get(item.category, 0.0)
    return recency_score + kw_score + source_score + category_score


async def _fetch_rss(url: str) -> str:
    client = await _get_http_client()
    response = await client.get(url)
    response.raise_for_status()
    return response.text


def _parse_rss_items(xml_text: str, category: str) -> list[NewsItem]:
    items: list[NewsItem] = []
    if not xml_text.strip():
        return items

    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return items

    for node in root.iter():
        if _xml_localname(node.tag) != "item":
            continue
        title = _normalize_title(_find_child_text(node, "title"))
        link = _find_child_text(node, "link")
        source = _find_child_text(node, "source") or _find_child_attr_or_text(node, "source", "url")
        published_ts = _parse_pubdate_to_ts(_find_child_text(node, "pubDate"))
        if not title or not link:
            continue
        items.append(
            NewsItem(
                title=title,
                link=link.strip(),
                source=_infer_source(link, source),
                published_ts=published_ts,
                category=category,
            )
        )
    return items


async def _fetch_hot_news() -> list[NewsItem]:
    if not _news_enabled():
        return []

    queries = _iter_rss_queries()
    results = await asyncio.gather(*[_fetch_rss(url) for _, url in queries], return_exceptions=True)

    now_ts = int(time.time())
    seen_titles: set[str] = set()
    all_items: list[NewsItem] = []

    for (category, _url), payload in zip(queries, results):
        if isinstance(payload, Exception):
            log.warning("[AI_CARE] News fetch failed for %s: %s", category, payload)
            continue
        for item in _parse_rss_items(payload, category):
            title_key = _normalize_key(item.title)
            if not title_key or title_key in seen_titles:
                continue
            seen_titles.add(title_key)
            item.score = _score_news_item(item, now_ts)
            if item.score >= 0:
                all_items.append(item)

    all_items.sort(key=lambda x: (x.score, x.published_ts), reverse=True)

    selected: list[NewsItem] = []
    seen_links: set[str] = set()
    for item in all_items:
        link_key = _normalize_key(item.link)
        if not link_key or link_key in seen_links:
            continue
        seen_links.add(link_key)
        selected.append(item)
        if len(selected) >= _max_news_links():
            break
    return selected


# =========================================================
# GEMINI DIGEST - 1 call/day, share to all users.
# =========================================================
def _safe_json_loads(raw_text: str) -> Optional[dict[str, Any]]:
    if not raw_text:
        return None
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        pass

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            return None
    return None


def _news_payload_hash(news: list[NewsItem]) -> str:
    base = "|".join(f"{n.title}|{n.link}|{n.source}|{n.published_ts}" for n in news[: _max_news_links()])
    return hashlib.sha1(base.encode("utf-8", errors="ignore")).hexdigest()


def _build_gemini_digest_prompt(news: list[NewsItem]) -> str:
    max_links = _max_news_links()
    news_rows = []
    for idx, item in enumerate(news[:max_links], start=1):
        news_rows.append(
            f"{idx}. title={item.title}\n"
            f"   source={item.source}\n"
            f"   link={item.link}\n"
            f"   published_ts={item.published_ts}\n"
        )

    return (
        f"{CNTX_MORNING_BRAND_PROMPT}\n"
        "Nhiệm vụ: Tạo morning digest siêu ngắn cho khách trading.\n"
        "Yêu cầu cực chặt:\n"
        "- Trả về JSON duy nhất, không thêm chữ thừa.\n"
        "- headline: 1 câu mở đầu dưới 14 từ.\n"
        f"- items: tối đa {max_links} item, ưu tiên tin ảnh hưởng trading/tài chính/chính trị/chiến sự.\n"
        "- Mỗi item có: short_line, impact.\n"
        "- short_line dưới 18 từ, tự nhiên nhưng không trẻ trâu, không kích động.\n"
        "- impact dưới 14 từ, nói tác động market/trading/risk.\n"
        "- Không hô lệnh, không đoán chắc giá.\n"
        "- Không nhắc /start trong JSON.\n"
        f"- Không quá {max_links} item.\n"
        "JSON schema:\n"
        '{"headline":"...","items":[{"short_line":"...","impact":"..."}]}\n\n'
        "Tin đầu vào:\n"
        + "\n".join(news_rows)
    )


def _fallback_digest(news: list[NewsItem], date_key: str) -> SharedDigest:
    lines: list[DigestLine] = []
    for item in news[: _max_news_links()]:
        title = _clean_spaces(item.title)
        if item.category == "war_markets":
            prefix = "Ô kìa, chiến sự lại nóng"
            impact = "dầu, vàng và USD dễ giật."
        elif item.category == "macro":
            prefix = "Pha vĩ mô đang nóng"
            impact = "lãi suất, vàng và dollar đáng canh."
        else:
            prefix = "Market có tin đáng chú ý"
            impact = "volatility đầu phiên dễ cao hơn."

        short = f"{prefix}: {title}"
        short = _truncate_text(_clean_spaces(short), 88)
        lines.append(DigestLine(line=f"{short} {impact}", link=item.link, source=item.source))

    if not lines:
        lines.append(
            DigestLine(
                line="Sáng nay chưa có headline nào thật sự vượt trội, mình ưu tiên canh risk đầu phiên.",
                link="",
                source="CNTx labs",
            )
        )

    return SharedDigest(
        date_key=date_key,
        headline="Tin sáng đáng chú ý",
        lines=lines[: _max_news_links()],
        built_with_gemini=False,
        raw_news=news[: _max_news_links()],
    )


async def _summarize_news_with_gemini(news: list[NewsItem], date_key: str) -> SharedDigest:
    if not news or gemini_engine is None or not _news_use_gemini():
        return _fallback_digest(news, date_key)

    try:
        prompt = _build_gemini_digest_prompt(news)
        raw = await gemini_engine.generate_response(
            user_query=prompt,
            system_prompt=MORNING_DIGEST_SYSTEM_PROMPT,
            use_google_search=False,
            max_output_tokens=900,
            temperature=0.45,
        )
        parsed = _safe_json_loads(raw or "")
        if not parsed:
            raise ValueError("gemini_digest_non_json")

        headline = _truncate_text(_clean_spaces(str(parsed.get("headline") or "").strip()) or "Tin sáng đáng chú ý", 80)
        items = parsed.get("items") or []
        lines: list[DigestLine] = []

        for idx, item in enumerate(items[: _max_news_links()]):
            if not isinstance(item, dict):
                continue
            short_line = _clean_spaces(str(item.get("short_line") or "").strip())
            impact = _clean_spaces(str(item.get("impact") or "").strip())
            if not short_line:
                continue
            combined = short_line
            if impact:
                combined = f"{combined} — {impact}"
            combined = _truncate_text(combined, 120)
            news_item = news[idx] if idx < len(news) else news[0]
            lines.append(DigestLine(line=combined, link=news_item.link, source=news_item.source))

        if not lines:
            raise ValueError("gemini_digest_empty")

        return SharedDigest(
            date_key=date_key,
            headline=headline,
            lines=lines[: _max_news_links()],
            built_with_gemini=True,
            raw_news=news[: _max_news_links()],
        )
    except Exception as exc:
        log.warning("[AI_CARE] Gemini digest failed, fallback deterministic: %s", exc)
        return _fallback_digest(news, date_key)


def _digest_from_cache_payload(payload: dict[str, Any], date_key: str) -> SharedDigest:
    return SharedDigest(
        date_key=str(payload.get("date_key") or date_key),
        headline=str(payload.get("headline") or "Tin sáng đáng chú ý"),
        lines=[
            DigestLine(
                line=str(x.get("line") or "").strip(),
                link=str(x.get("link") or "").strip(),
                source=str(x.get("source") or "").strip(),
            )
            for x in (payload.get("lines") or [])
            if isinstance(x, dict)
        ],
        built_with_gemini=bool(payload.get("built_with_gemini", False)),
        raw_news=[],
    )


async def _get_shared_digest(*, allow_gemini: bool = False) -> SharedDigest:
    date_key = _today_key()
    cache_key = f"ai:care:digest:{date_key}"
    fallback_local_key = f"{cache_key}:rss_fallback"
    wants_gemini = bool(allow_gemini and _news_use_gemini() and gemini_engine is not None)

    local = _LOCAL_DIGEST_CACHE.get(cache_key)
    if local and (int(time.time()) - int(local[0])) < _news_cache_ttl_sec():
        digest = local[1]
        attempted = bool(_LOCAL_DIGEST_GEMINI_ATTEMPTED.get(cache_key, False))
        if digest.built_with_gemini or not wants_gemini or attempted:
            return digest

    cached = await _cache_get_json(cache_key)
    if cached:
        digest = _digest_from_cache_payload(cached, date_key)
        attempted = bool(cached.get("gemini_attempted", False))
        if digest.built_with_gemini or not wants_gemini or attempted:
            _LOCAL_DIGEST_CACHE[cache_key] = (int(time.time()), digest)
            _LOCAL_DIGEST_GEMINI_ATTEMPTED[cache_key] = attempted
            return digest

    if not allow_gemini:
        local_fallback = _LOCAL_DIGEST_CACHE.get(fallback_local_key)
        if local_fallback and (int(time.time()) - int(local_fallback[0])) < _news_cache_ttl_sec():
            return local_fallback[1]

    news = await _fetch_hot_news()
    digest = await _summarize_news_with_gemini(news, date_key) if allow_gemini else _fallback_digest(news, date_key)
    gemini_attempted = bool(wants_gemini)

    if not allow_gemini:
        _LOCAL_DIGEST_CACHE[fallback_local_key] = (int(time.time()), digest)
        return digest

    payload = {
        "date_key": digest.date_key,
        "headline": digest.headline,
        "built_with_gemini": digest.built_with_gemini,
        "gemini_attempted": gemini_attempted,
        "lines": [{"line": x.line, "link": x.link, "source": x.source} for x in digest.lines],
        "news_hash": _news_payload_hash(news),
    }
    await _cache_set_json(cache_key, payload, _gemini_digest_cache_ttl_sec())
    _LOCAL_DIGEST_CACHE[cache_key] = (int(time.time()), digest)
    _LOCAL_DIGEST_GEMINI_ATTEMPTED[cache_key] = gemini_attempted
    log.info(
        "[AI_CARE] Shared morning digest ready date=%s gemini=%s links=%s",
        date_key,
        digest.built_with_gemini,
        len(digest.lines),
    )
    return digest


# =========================================================
# MESSAGE BUILDERS
# Chỉ gửi 1 bản tin sáng, bỏ các câu kiểu "Bot đang OFF"
# =========================================================
def _intro_line() -> str:
    return "🌤 <b>Chào buổi sáng Sếp</b>, CNTx labs gửi nhanh vài tin nóng để mình mở ngày cho gọn."


def _cta_line() -> str:
    return "👉 Sếp gõ <b>/start</b> để mở bot và kiểm tra trạng thái / cấu hình nhanh."


def _dedupe_digest_lines(lines: list[DigestLine]) -> list[DigestLine]:
    seen: set[str] = set()
    result: list[DigestLine] = []
    for item in lines:
        key = _normalize_dedupe_key(f"{item.line}|{item.link}|{item.source}")
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _format_digest_lines(digest: SharedDigest) -> str:
    lines = _dedupe_digest_lines(digest.lines)
    if not lines:
        return "📊 Sáng nay chưa có headline nào đủ mạnh, mình ưu tiên canh risk đầu phiên nhé."

    rows = [f"🔥 <b>{html.escape(_truncate_text(_clean_spaces(digest.headline), 80))}</b>"]
    max_links = _max_news_links()
    for idx, item in enumerate(lines[:max_links], start=1):
        line = html.escape(_truncate_text(_clean_spaces(item.line), 160))
        source = html.escape(_clean_spaces(item.source or "Nguồn"))
        if item.link:
            rows.append(f"• {line} <i>({source})</i> — <a href=\"{_safe_link(item.link)}\">xem {idx}</a>")
        else:
            rows.append(f"• {line} <i>({source})</i>")
    return "\n".join(rows)


def _quote_cache_ttl_sec() -> int:
    return max(30, int(getattr(settings, "AI_CARE_QUOTE_CACHE_TTL_SEC", 120) or 120))


def _normalize_market_text(text: str) -> str:
    raw = str(text or "").lower().replace("đ", "d")
    raw = unicodedata.normalize("NFD", raw)
    raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    raw = re.sub(r"[^a-z0-9./\s]", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _compact_market_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _normalize_market_text(text))


def _quote_intent_terms() -> tuple[str, ...]:
    return (
        "gia",
        "bao nhieu",
        "price",
        "quote",
        "spot",
        "hien tai",
        "bay gio",
        "luc nay",
        "ty gia",
        "ti gia",
        "ra bao nhieu",
        "dang bao nhieu",
        "muc nao",
        "dong cua",
        "mo cua",
        "cao nhat",
        "thap nhat",
    )


def _non_price_market_terms() -> tuple[str, ...]:
    return (
        "la gi",
        "giai thich",
        "nghia la gi",
        "tai sao",
        "vi sao",
        "anh huong",
        "tac dong",
        "phan tich",
        "nhan dinh",
        "du bao",
        "xu huong",
        "co nen mua",
        "nen mua",
        "tin",
        "tin tuc",
        "tin moi",
        "co gi moi",
        "moi nhat",
        "cap nhat",
        "news",
        "headline",
        "headlines",
    )


def _contract_cost_terms() -> tuple[str, ...]:
    return (
        "swap",
        "qua dem",
        "overnight",
        "rollover",
        "phi qua dem",
        "phi swap",
        "phi giu lenh",
        "giu qua dem",
        "ton bao nhieu",
        "lot",
        "margin",
        "ky quy",
        "leverage",
        "don bay",
        "commission",
        "hoa hong",
        "contract specification",
        "thong so hop dong",
    )


def _currency_alias_map() -> tuple[tuple[str, tuple[str, ...]], ...]:
    return (
        ("usd", ("usd", "dollar", "do la", "do my", "my kim")),
        ("eur", ("eur", "euro")),
        ("gbp", ("gbp", "bang anh", "pound")),
        ("jpy", ("jpy", "yen", "nhat")),
        ("chf", ("chf", "franc thuy si", "swiss franc")),
        ("cad", ("cad", "do canada", "canada")),
        ("aud", ("aud", "do uc", "australian dollar")),
        ("nzd", ("nzd", "do new zealand", "new zealand")),
        ("cny", ("cny", "nhan dan te", "yuan", "rmb")),
        ("vnd", ("vnd", "dong viet", "viet nam dong")),
    )


def _common_stock_tickers() -> set[str]:
    return {
        "aapl",
        "amd",
        "amzn",
        "dia",
        "goog",
        "googl",
        "ibm",
        "meta",
        "msft",
        "mstr",
        "nflx",
        "nvda",
        "pltr",
        "qqq",
        "shop",
        "spy",
        "tsla",
    }


def _stock_stopwords() -> set[str]:
    return {
        "gia",
        "co",
        "phieu",
        "stock",
        "ticker",
        "chung",
        "khoan",
        "hom",
        "nay",
        "bao",
        "nhieu",
        "hien",
        "tai",
        "price",
        "quote",
        "la",
        "gi",
        "ma",
        "my",
        "us",
    }


def _forex_codes() -> set[str]:
    return {"usd", "eur", "gbp", "jpy", "chf", "cad", "aud", "nzd", "cny", "vnd"}


def _build_market_quote_request(
    *,
    label: str,
    stooq_symbol: str,
    display_symbol: str,
    asset_class: str,
    unit: str,
    decimals: int,
    source: str = "stooq",
    base_currency: str = "",
    quote_currency: str = "",
) -> MarketQuoteRequest:
    return MarketQuoteRequest(
        label=label,
        stooq_symbol=stooq_symbol,
        display_symbol=display_symbol,
        asset_class=asset_class,
        unit=unit,
        decimals=decimals,
        source=source,
        base_currency=base_currency,
        quote_currency=quote_currency,
    )


def _extract_currency_alias_hits(norm_text: str) -> list[str]:
    hits: list[tuple[int, str]] = []
    for code, aliases in _currency_alias_map():
        for alias in aliases:
            for match in re.finditer(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", norm_text):
                hits.append((match.start(), code))
    ordered: list[str] = []
    seen: set[str] = set()
    for _pos, code in sorted(hits, key=lambda item: item[0]):
        if code in seen:
            continue
        seen.add(code)
        ordered.append(code)
    return ordered


def _extract_forex_pair(norm_text: str) -> Optional[tuple[str, str]]:
    fx_codes = _forex_codes()
    tokens = _normalize_market_text(norm_text).replace("/", " ").replace("-", " ").split()

    for token in tokens:
        clean = re.sub(r"[^a-z0-9]", "", token)
        if len(clean) == 6 and clean[:3] in fx_codes and clean[3:] in fx_codes:
            return clean[:3], clean[3:]

    for idx in range(len(tokens) - 1):
        left = re.sub(r"[^a-z0-9]", "", tokens[idx])
        right = re.sub(r"[^a-z0-9]", "", tokens[idx + 1])
        if len(left) == 3 and len(right) == 3 and left in fx_codes and right in fx_codes:
            return left, right

    alias_codes = _extract_currency_alias_hits(norm_text)
    if len(alias_codes) >= 2:
        return alias_codes[0], alias_codes[1]

    return None


def _looks_like_vnd_rate_request(norm_text: str) -> bool:
    if any(
        phrase in norm_text
        for phrase in (
            "vnd",
            "dong viet",
            "sang vnd",
            "ra vnd",
            "quy doi",
            "doi ra tien viet",
            "ty gia",
            "ti gia",
        )
    ):
        return True
    return any(
        phrase in norm_text
        for phrase in (
            "gia usd",
            "gia euro",
            "gia bang anh",
            "gia yen",
            "gia nhan dan te",
            "gia do la",
        )
    )


def _resolve_fx_quote_request(norm_text: str) -> Optional[MarketQuoteRequest]:
    pair = _extract_forex_pair(norm_text)
    if pair is None:
        alias_codes = _extract_currency_alias_hits(norm_text)
        if len(alias_codes) == 1 and _looks_like_vnd_rate_request(norm_text):
            pair = (alias_codes[0], "vnd")
    if pair is None:
        return None

    base, quote = pair
    if base == quote:
        return None

    if quote == "vnd":
        base_code = base.upper()
        return _build_market_quote_request(
            label=f"Tỷ giá {base_code}/VND",
            stooq_symbol="",
            display_symbol=f"{base_code}/VND",
            asset_class="fx",
            unit="VND",
            decimals=2,
            source="er_api",
            base_currency=base_code,
            quote_currency="VND",
        )

    decimals = 3 if quote == "jpy" else 5
    return _build_market_quote_request(
        label=f"Tỷ giá {base.upper()}/{quote.upper()}",
        stooq_symbol=f"{base}{quote}",
        display_symbol=f"{base.upper()}{quote.upper()}",
        asset_class="fx",
        unit=quote.upper(),
        decimals=decimals,
        source="stooq",
        base_currency=base.upper(),
        quote_currency=quote.upper(),
    )


def _extract_stock_symbol(raw_text: str, norm_text: str) -> Optional[str]:
    raw = str(raw_text or "")
    raw_tokens = [token.lower() for token in re.findall(r"\b[A-Za-z]{1,5}(?:\.us)?\b", raw)]
    allowlist = _common_stock_tickers()
    stopwords = _stock_stopwords()
    has_stock_context = any(
        phrase in norm_text for phrase in ("co phieu", "chung khoan", "stock", "ticker", "etf")
    )

    for token in raw_tokens:
        base = token[:-3] if token.endswith(".us") else token
        if base in allowlist:
            return base.upper()

    if not has_stock_context:
        return None

    for token in raw_tokens:
        base = token[:-3] if token.endswith(".us") else token
        if base in stopwords or base in _forex_codes():
            continue
        if 1 <= len(base) <= 5 and base.isalpha():
            return base.upper()
    return None


def resolve_market_quote_request(text: str) -> Optional[MarketQuoteRequest]:
    norm_text = _normalize_market_text(text)
    compact_text = _compact_market_text(text)
    tokens = set(norm_text.split())
    if not norm_text:
        return None

    if "xauusd" in compact_text or "vang" in tokens or "gold" in tokens or "spot gold" in norm_text:
        return _build_market_quote_request(
            label="Vàng spot quốc tế",
            stooq_symbol="xauusd",
            display_symbol="XAUUSD",
            asset_class="metal",
            unit="USD/oz",
            decimals=3,
        )

    if "xagusd" in compact_text or "bac" in tokens or "silver" in tokens:
        return _build_market_quote_request(
            label="Bạc spot quốc tế",
            stooq_symbol="xagusd",
            display_symbol="XAGUSD",
            asset_class="metal",
            unit="USD/oz",
            decimals=3,
        )

    if any(term in compact_text for term in ("btcusd", "btcusdt", "bitcoin")) or "btc" in tokens or "bitcoin" in tokens:
        return _build_market_quote_request(
            label="Bitcoin",
            stooq_symbol="btc.v",
            display_symbol="BTC",
            asset_class="crypto",
            unit="USD",
            decimals=2,
        )

    if any(term in compact_text for term in ("ethusd", "ethusdt", "ethereum")) or "eth" in tokens or "ethereum" in tokens:
        return _build_market_quote_request(
            label="Ethereum",
            stooq_symbol="eth.v",
            display_symbol="ETH",
            asset_class="crypto",
            unit="USD",
            decimals=2,
        )

    if any(term in compact_text for term in ("solusd", "solusdt", "solana")) or "sol" in tokens or "solana" in tokens:
        return _build_market_quote_request(
            label="Solana",
            stooq_symbol="sol.v",
            display_symbol="SOL",
            asset_class="crypto",
            unit="USD",
            decimals=4,
        )

    if any(term in compact_text for term in ("sp500", "spx", "sandp500")) or any(
        phrase in norm_text for phrase in ("s p 500", "sp 500")
    ):
        return _build_market_quote_request(
            label="Chỉ số S&P 500",
            stooq_symbol="^spx",
            display_symbol="SPX",
            asset_class="index",
            unit="điểm",
            decimals=2,
        )

    if any(term in compact_text for term in ("nasdaq100", "nasdaq", "ndq")):
        return _build_market_quote_request(
            label="Chỉ số Nasdaq",
            stooq_symbol="^ndq",
            display_symbol="NDQ",
            asset_class="index",
            unit="điểm",
            decimals=2,
        )

    if any(term in compact_text for term in ("dowjones", "dji")) or "dow jones" in norm_text:
        return _build_market_quote_request(
            label="Chỉ số Dow Jones",
            stooq_symbol="^dji",
            display_symbol="DJI",
            asset_class="index",
            unit="điểm",
            decimals=2,
        )

    if any(term in compact_text for term in ("dxy", "dxf", "dollarindex")) or any(
        phrase in norm_text for phrase in ("dollar index", "chi so usd", "chi so dollar")
    ):
        return _build_market_quote_request(
            label="Dollar Index",
            stooq_symbol="dx.f",
            display_symbol="DXY",
            asset_class="index",
            unit="điểm",
            decimals=2,
        )

    if any(term in compact_text for term in ("clf", "wti", "crude", "oil")) or any(
        phrase in norm_text for phrase in ("gia dau", "dau tho", "dau wti")
    ):
        return _build_market_quote_request(
            label="Dầu WTI",
            stooq_symbol="cl.f",
            display_symbol="CL",
            asset_class="commodity",
            unit="USD/thùng",
            decimals=2,
        )

    fx_request = _resolve_fx_quote_request(norm_text)
    if fx_request is not None:
        return fx_request

    ticker = _extract_stock_symbol(text, norm_text)
    if ticker is not None:
        return _build_market_quote_request(
            label=f"Cổ phiếu {ticker}",
            stooq_symbol=f"{ticker.lower()}.us",
            display_symbol=f"{ticker}.US",
            asset_class="equity",
            unit="USD",
            decimals=3,
        )

    return None


def looks_like_market_quote_query(text: str) -> bool:
    norm_text = _normalize_market_text(text)
    if not norm_text:
        return False

    request = resolve_market_quote_request(text)
    if request is None:
        return False

    if any(term in norm_text for term in _contract_cost_terms()):
        return False

    has_quote_intent = any(term in norm_text for term in _quote_intent_terms())
    if not has_quote_intent and any(term in norm_text for term in _non_price_market_terms()):
        return False

    if has_quote_intent:
        return True

    token_count = len(norm_text.split())
    if token_count <= 3:
        return True

    compact_text = _compact_market_text(text)
    display_key = re.sub(r"[^a-z0-9]+", "", request.display_symbol.lower())
    stooq_key = re.sub(r"[^a-z0-9]+", "", request.stooq_symbol.lower())
    return compact_text in {display_key, stooq_key}


def _quote_cache_key(request: MarketQuoteRequest) -> str:
    if request.source == "er_api":
        key_symbol = f"{request.base_currency.lower()}{request.quote_currency.lower()}"
    else:
        key_symbol = request.stooq_symbol.lower()
    return f"ai:care:quote:{request.source}:{key_symbol}"


def _serialize_quote_snapshot(snapshot: MarketQuoteSnapshot) -> dict[str, Any]:
    req = snapshot.request
    return {
        "request": {
            "label": req.label,
            "stooq_symbol": req.stooq_symbol,
            "display_symbol": req.display_symbol,
            "asset_class": req.asset_class,
            "unit": req.unit,
            "decimals": req.decimals,
            "source": req.source,
            "base_currency": req.base_currency,
            "quote_currency": req.quote_currency,
        },
        "date_raw": snapshot.date_raw,
        "time_raw": snapshot.time_raw,
        "open_price": snapshot.open_price,
        "high_price": snapshot.high_price,
        "low_price": snapshot.low_price,
        "close_price": snapshot.close_price,
        "volume_raw": snapshot.volume_raw,
    }


def _deserialize_quote_snapshot(payload: dict[str, Any]) -> Optional[MarketQuoteSnapshot]:
    try:
        raw_req = payload.get("request") or {}
        request = MarketQuoteRequest(
            label=str(raw_req.get("label") or "").strip(),
            stooq_symbol=str(raw_req.get("stooq_symbol") or "").strip(),
            display_symbol=str(raw_req.get("display_symbol") or "").strip(),
            asset_class=str(raw_req.get("asset_class") or "").strip(),
            unit=str(raw_req.get("unit") or "").strip(),
            decimals=int(raw_req.get("decimals") or 0),
            source=str(raw_req.get("source") or "stooq").strip(),
            base_currency=str(raw_req.get("base_currency") or "").strip(),
            quote_currency=str(raw_req.get("quote_currency") or "").strip(),
        )
        return MarketQuoteSnapshot(
            request=request,
            date_raw=str(payload.get("date_raw") or "").strip(),
            time_raw=str(payload.get("time_raw") or "").strip(),
            open_price=float(payload.get("open_price") or 0.0),
            high_price=float(payload.get("high_price") or 0.0),
            low_price=float(payload.get("low_price") or 0.0),
            close_price=float(payload.get("close_price") or 0.0),
            volume_raw=str(payload.get("volume_raw") or "").strip(),
        )
    except Exception:
        return None


def _safe_float(raw_value: Any) -> Optional[float]:
    text = str(raw_value or "").strip()
    if not text or text.upper() == "N/D":
        return None
    try:
        return float(text)
    except Exception:
        return None


def _parse_stooq_quote_row(request: MarketQuoteRequest, raw_text: str) -> Optional[MarketQuoteSnapshot]:
    row = [item.strip() for item in str(raw_text or "").strip().split(",")]
    if len(row) < 7:
        return None

    open_price = _safe_float(row[3])
    high_price = _safe_float(row[4])
    low_price = _safe_float(row[5])
    close_price = _safe_float(row[6])
    if None in {open_price, high_price, low_price, close_price}:
        return None

    return MarketQuoteSnapshot(
        request=request,
        date_raw=str(row[1] or "").strip(),
        time_raw=str(row[2] or "").strip(),
        open_price=float(open_price),
        high_price=float(high_price),
        low_price=float(low_price),
        close_price=float(close_price),
        volume_raw=str(row[7] if len(row) > 7 else "").strip(),
    )


def _format_quote_timestamp(date_raw: str, time_raw: str) -> str:
    date_text = str(date_raw or "").strip()
    time_text = str(time_raw or "").strip()

    if re.fullmatch(r"\d{8}", date_text):
        date_text = f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:]}"
    if re.fullmatch(r"\d{6}", time_text):
        time_text = f"{time_text[:2]}:{time_text[2:4]}:{time_text[4:]}"

    return f"{date_text} {time_text}".strip()


def _format_quote_number(value: float, decimals: int) -> str:
    return f"{float(value):,.{max(0, int(decimals))}f}"


def _format_market_quote_reply(snapshot: MarketQuoteSnapshot) -> str:
    request = snapshot.request
    close_text = _format_quote_number(snapshot.close_price, request.decimals)
    open_text = _format_quote_number(snapshot.open_price, request.decimals)
    high_text = _format_quote_number(snapshot.high_price, request.decimals)
    low_text = _format_quote_number(snapshot.low_price, request.decimals)
    stamp_text = _format_quote_timestamp(snapshot.date_raw, snapshot.time_raw)

    rows: list[str] = []
    if request.asset_class == "fx":
        base = request.base_currency or request.display_symbol[:3]
        quote = request.quote_currency or request.unit
        rows.append(f"{request.label} hiện quanh {close_text} {quote} cho 1 {base}.")
    else:
        rows.append(f"{request.label} ({request.display_symbol}) hiện quanh {close_text} {request.unit}.")

    if request.source == "stooq":
        change = snapshot.close_price - snapshot.open_price
        if abs(snapshot.open_price) > 1e-9:
            change_pct = (change / snapshot.open_price) * 100.0
        else:
            change_pct = 0.0

        if abs(change) < (10 ** (-max(0, request.decimals))):
            rows.append(f"So với giá mở phiên: gần như đi ngang quanh {open_text}.")
        else:
            direction = "tăng" if change > 0 else "giảm"
            rows.append(
                f"So với giá mở phiên: {direction} {_format_quote_number(abs(change), request.decimals)} "
                f"{request.unit} ({change_pct:+.2f}%)."
            )
        rows.append(f"Biên phiên gần nhất: cao {high_text} | thấp {low_text}.")

    if stamp_text:
        source_name = "ER-API" if request.source == "er_api" else "Stooq"
        rows.append(f"Nguồn: {source_name}, mốc dữ liệu {stamp_text}.")

    if request.display_symbol == "XAUUSD":
        rows.append("Đây là vàng spot quốc tế, không phải giá vàng SJC trong nước.")

    return "\n".join(rows)


async def _fetch_stooq_quote_snapshot(request: MarketQuoteRequest) -> Optional[MarketQuoteSnapshot]:
    if not request.stooq_symbol:
        return None
    client = await _get_http_client()
    url = f"https://stooq.com/q/l/?s={quote_plus(request.stooq_symbol.lower())}&i=d"
    response = await client.get(url, headers={"Accept": "text/plain, text/csv;q=0.9, */*;q=0.8"})
    response.raise_for_status()
    raw_line = next((line for line in response.text.splitlines() if line.strip()), "")
    return _parse_stooq_quote_row(request, raw_line)


def _utc_parts_from_payload(payload: dict[str, Any]) -> tuple[str, str]:
    ts = _safe_int(payload.get("time_last_update_unix") or 0)
    if ts > 0:
        dt = time.gmtime(ts)
        return (f"{dt.tm_year:04d}{dt.tm_mon:02d}{dt.tm_mday:02d}", f"{dt.tm_hour:02d}{dt.tm_min:02d}{dt.tm_sec:02d}")

    raw = str(payload.get("time_last_update_utc") or "").strip()
    if not raw:
        return "", ""
    try:
        dt = parsedate_to_datetime(raw)
        return dt.strftime("%Y%m%d"), dt.strftime("%H%M%S")
    except Exception:
        return "", ""


async def _fetch_er_api_quote_snapshot(request: MarketQuoteRequest) -> Optional[MarketQuoteSnapshot]:
    base = str(request.base_currency or "").strip().upper()
    quote = str(request.quote_currency or "").strip().upper()
    if not base or not quote:
        return None

    client = await _get_http_client()
    response = await client.get(
        f"https://open.er-api.com/v6/latest/{quote_plus(base)}",
        headers={"Accept": "application/json, text/plain;q=0.9, */*;q=0.8"},
    )
    response.raise_for_status()
    payload = response.json()
    if str(payload.get("result") or "").strip().lower() != "success":
        return None

    rate = _safe_float((payload.get("rates") or {}).get(quote))
    if rate is None:
        return None

    date_raw, time_raw = _utc_parts_from_payload(payload)
    return MarketQuoteSnapshot(
        request=request,
        date_raw=date_raw,
        time_raw=time_raw,
        open_price=float(rate),
        high_price=float(rate),
        low_price=float(rate),
        close_price=float(rate),
    )


async def _fetch_market_quote_snapshot(request: MarketQuoteRequest) -> Optional[MarketQuoteSnapshot]:
    if request.source == "er_api":
        return await _fetch_er_api_quote_snapshot(request)
    return await _fetch_stooq_quote_snapshot(request)


async def _get_market_quote_snapshot(request: MarketQuoteRequest) -> Optional[MarketQuoteSnapshot]:
    cache_key = _quote_cache_key(request)
    cache_ttl = _quote_cache_ttl_sec()
    now_ts = int(time.time())

    local = _LOCAL_QUOTE_CACHE.get(cache_key)
    if local and (now_ts - int(local[0])) < cache_ttl:
        return local[1]

    cached = await _cache_get_json(cache_key)
    if cached:
        snapshot = _deserialize_quote_snapshot(cached)
        if snapshot is not None:
            _LOCAL_QUOTE_CACHE[cache_key] = (now_ts, snapshot)
            return snapshot

    try:
        snapshot = await _fetch_market_quote_snapshot(request)
    except Exception as exc:
        log.warning("[AI_CARE] Quote fetch failed for %s: %s", request.display_symbol, exc)
        return None

    if snapshot is None:
        return None

    await _cache_set_json(cache_key, _serialize_quote_snapshot(snapshot), cache_ttl)
    _LOCAL_QUOTE_CACHE[cache_key] = (now_ts, snapshot)
    return snapshot


async def build_grounded_market_reply(user_query: str) -> str:
    quote_request = resolve_market_quote_request(user_query)
    if quote_request and looks_like_market_quote_query(user_query):
        snapshot = await _get_market_quote_snapshot(quote_request)
        if snapshot is not None:
            return _format_market_quote_reply(snapshot)
        return (
            f"Mình đang chưa kéo được quote sạch cho {quote_request.label} ({quote_request.display_symbol}) ở nguồn hiện tại.\n"
            "Bạn nhắn lại đúng mã như XAUUSD, EURUSD, USD/VND, AAPL hoặc BTC để mình thử kéo lại ngay."
        )

    digest = await _get_shared_digest(allow_gemini=False)
    lines = _dedupe_digest_lines(digest.lines)
    norm_query = _normalize_market_text(user_query)

    if "vang" in norm_query or "gold" in norm_query:
        intro = "Mình chốt nhanh vài ý đang tác động tới vàng:"
    elif "dau" in norm_query or "oil" in norm_query or "brent" in norm_query:
        intro = "Mình chốt nhanh vài ý đang tác động tới dầu:"
    elif "btc" in norm_query or "bitcoin" in norm_query or "crypto" in norm_query:
        intro = "Mình chốt nhanh vài ý đang tác động tới crypto:"
    else:
        intro = "Mình chốt nhanh vài headline market đáng chú ý:"

    if not lines:
        return (
            "Hiện mình chưa thấy headline nào đủ mạnh để chốt gọn cho market.\n"
            "Nếu cần, mình có thể rà tiếp theo đúng mã như vàng, dầu, BTC hoặc forex."
        )

    rows = [intro, f"🔥 {digest.headline}"]
    for item in lines[: min(2, len(lines))]:
        rows.append(f"- {item.line}")

    link_rows = []
    for item in lines[: min(2, len(lines))]:
        if item.link:
            link_rows.append(f"- {item.source}: {item.link}")
    if link_rows:
        rows.append("Nguồn nhanh:")
        rows.extend(link_rows)

    rows.append("Nếu Sếp muốn, nói rõ mã cần soi như vàng, dầu, BTC hay forex để CNTx labs chốt sát hơn.")
    return "\n".join(rows)


def _build_morning_message(user: UserCareSnapshot, digest: SharedDigest) -> str:
    _ = user  # giữ signature cũ để khỏi phá caller
    intro = _intro_line()
    cta = _cta_line()
    digest_rows = [line for line in _format_digest_lines(digest).splitlines() if line.strip()]

    selected_rows: list[str] = []
    for row in digest_rows:
        candidate = "\n".join([intro, *selected_rows, row, cta]).strip()
        if _telegram_html_fits(candidate):
            selected_rows.append(row)
        else:
            break

    if not selected_rows:
        selected_rows = ["📊 Sáng nay chưa có headline nào đủ mạnh, mình ưu tiên canh risk đầu phiên nhé."]

    return _fit_telegram_html_lines("\n".join([intro, *selected_rows, cta]).strip())


# =========================================================
# TELEGRAM SEND
# =========================================================
async def _send_user_message(telegram_id: str, text: str) -> bool:
    token = str(getattr(settings, "TELEGRAM_BOT_TOKEN", "") or "").strip()
    chat_id = _normalize_telegram_id(telegram_id)

    if not token or not chat_id:
        return False

    blocked_key = f"ai:care:telegram:blocked:{chat_id}"
    if await _marker_exists(blocked_key):
        return False

    final_text = _fit_telegram_html_lines(_ensure_start_cta_html(str(text or "").strip()))

    if _dry_run():
        log.info("[AI_CARE][DRY_RUN] to=%s msg=%s", chat_id, final_text[:260].replace("\n", " | "))
        return True

    while not await _allow_rate_send():
        await asyncio.sleep(0.05)

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": final_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        client = await _get_http_client()
        resp = await client.post(url, json=payload)
        resp.raise_for_status()

        data = {}
        try:
            data = resp.json()
        except Exception:
            data = {}

        if isinstance(data, dict) and data.get("ok") is False:
            log.warning(
                "[AI_CARE] Telegram responded not ok user=%s desc=%s",
                chat_id,
                _truncate_text(str(data.get("description") or "unknown"), 180),
            )
            return False

        return True
    except httpx.HTTPStatusError as exc:
        status_code = getattr(exc.response, "status_code", "unknown")
        detail = _truncate_text(str(getattr(exc.response, "text", "") or ""), 180)
        detail_lower = detail.lower()
        is_hard_delivery_failure = str(status_code) == "400" and (
            "chat not found" in detail_lower
            or "bot was blocked" in detail_lower
            or "user is deactivated" in detail_lower
        )
        if is_hard_delivery_failure:
            first_block = await _mark_once(blocked_key, ttl_sec=_telegram_hard_block_ttl_sec())
            if first_block:
                log.warning(
                    "[AI_CARE] Telegram delivery disabled temporarily user=%s status=%s reason=%s",
                    chat_id,
                    status_code,
                    _truncate_text(detail, 120),
                )
            return False
        log.warning("[AI_CARE] Send failed user=%s status=%s detail=%s", chat_id, status_code, detail)
        return False
    except Exception as exc:
        err = str(exc)
        if token:
            err = err.replace(token, "[REDACTED]")
        log.warning("[AI_CARE] Send failed user=%s err=%s", chat_id, err[:180])
        return False


# =========================================================
# USER COLLECTION
# Muc tieu: bam theo control plane runtime va cac nguon hoat dong generic
# =========================================================
def _merge_user(grouped: dict[str, UserCareSnapshot], user: UserCareSnapshot) -> None:
    tg = _normalize_telegram_id(user.telegram_id)
    if not tg:
        return

    current = grouped.get(tg)
    if current is None:
        grouped[tg] = UserCareSnapshot(
            telegram_id=tg,
            linked_accounts=max(0, int(user.linked_accounts or 0)),
            running_accounts=max(0, int(user.running_accounts or 0)),
            last_activity_ts=max(0, int(user.last_activity_ts or 0)),
            source=user.source or "unknown",
        )
        return

    current.linked_accounts = max(current.linked_accounts, int(user.linked_accounts or 0))
    current.running_accounts = max(current.running_accounts, int(user.running_accounts or 0))
    current.last_activity_ts = max(current.last_activity_ts, int(user.last_activity_ts or 0))
    if current.source == "unknown" and user.source:
        current.source = user.source


def _extract_tg_from_row(row: dict[str, Any]) -> str:
    for key in (
        "telegram_id",
        "telegramId",
        "telegram_chat_id",
        "telegramChatId",
        "chat_id",
        "chatId",
    ):
        tg = _normalize_telegram_id(row.get(key))
        if tg:
            return tg
    return ""


def _hubbot_state_last_activity(payload: Any) -> int:
    best = 0

    if isinstance(payload, dict):
        for key in ("last_ai_ts", "last_button_ts", "updated_at", "last_activity_ts"):
            try:
                best = max(best, int(float(payload.get(key) or 0)))
            except Exception:
                continue
        state = payload.get("state")
        if state is not None and state is not payload:
            best = max(best, _hubbot_state_last_activity(state))
        return best

    for attr in ("last_ai_ts", "last_button_ts", "updated_at", "last_activity_ts"):
        try:
            best = max(best, int(float(getattr(payload, attr, 0) or 0)))
        except Exception:
            continue
    return best


def _load_hubbot_state_payload() -> dict[str, Any]:
    state_path = _hubbot_state_path()
    if not state_path.exists():
        return {}

    hubbot_dir = state_path.parent
    hubbot_app_dir = hubbot_dir / "app"

    added_sys_path = False
    if str(hubbot_dir) not in sys.path:
        sys.path.insert(0, str(hubbot_dir))
        added_sys_path = True

    app_pkg = sys.modules.get("app")
    original_app_paths: list[str] | None = None
    if app_pkg is not None:
        try:
            original_app_paths = list(getattr(app_pkg, "__path__", []))
            if str(hubbot_app_dir) not in original_app_paths:
                app_pkg.__path__.append(str(hubbot_app_dir))
        except Exception:
            original_app_paths = None

    cwd_before = os.getcwd()
    try:
        importlib.import_module("app.state")
        os.chdir(str(hubbot_dir))
        with state_path.open("rb") as fh:
            payload = pickle.load(fh)
    finally:
        try:
            os.chdir(cwd_before)
        except Exception:
            pass
        if original_app_paths is not None:
            try:
                app_pkg.__path__[:] = original_app_paths
            except Exception:
                pass
        if added_sys_path:
            try:
                sys.path.remove(str(hubbot_dir))
            except ValueError:
                pass

    return payload if isinstance(payload, dict) else {}


async def _collect_users_from_backend_tables(store: Any, grouped: dict[str, UserCareSnapshot]) -> None:
    for table_name, telegram_col, ts_expr, source in _BACKEND_RECIPIENT_TABLES:
        def _do(con: Any, cur: Any, *, table_name: str = table_name, telegram_col: str = telegram_col, ts_expr: str = ts_expr) -> list[dict[str, Any]]:
            cur.execute(
                f"""
                SELECT {telegram_col} AS telegram_id, MAX({ts_expr}) AS last_activity_ts
                FROM {table_name}
                GROUP BY {telegram_col}
                """
            )
            return [dict(row) for row in cur.fetchall()]

        try:
            rows = await asyncio.to_thread(store._with_retry_read, _do)
        except Exception as exc:
            log.warning("[AI_CARE] recipient scan failed source=%s err=%s", source, exc)
            continue

        for row in rows or []:
            if not isinstance(row, dict):
                continue
            tg = _extract_tg_from_row(row)
            if not tg:
                continue
            _merge_user(
                grouped,
                UserCareSnapshot(
                    telegram_id=tg,
                    linked_accounts=0,
                    running_accounts=0,
                    last_activity_ts=max(0, _safe_int(row.get("last_activity_ts"))),
                    source=source,
                ),
            )


async def _collect_users_from_control_plane_runtime(store: Any, grouped: dict[str, UserCareSnapshot]) -> None:
    try:
        repo = ControlPlaneRepository(store)
        rows = await asyncio.to_thread(repo.list_user_runtime_summaries)
    except Exception as exc:
        log.warning("[AI_CARE] control plane runtime summary failed: %s", exc)
        return

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        tg = _extract_tg_from_row(row)
        if not tg:
            continue

        _merge_user(
            grouped,
            UserCareSnapshot(
                telegram_id=tg,
                linked_accounts=max(0, _safe_int(row.get("linked_accounts"))),
                running_accounts=max(0, _safe_int(row.get("running_accounts"))),
                last_activity_ts=max(0, _safe_int(row.get("last_activity_ts"))),
                source="control_plane_runtime",
            ),
        )


async def _collect_users_from_optional_store_sources(store: Any, grouped: dict[str, UserCareSnapshot]) -> None:
    candidate_methods = (
        "list_users",
        "list_telegram_users",
        "list_contacts",
        "list_customers",
        "list_clients",
    )

    for method_name in candidate_methods:
        if not hasattr(store, method_name):
            continue

        method = getattr(store, method_name, None)
        if not callable(method):
            continue

        try:
            rows = await asyncio.to_thread(method)
        except Exception:
            continue

        for row in rows or []:
            if not isinstance(row, dict):
                continue
            tg = _extract_tg_from_row(row)
            if not tg:
                continue

            _merge_user(
                grouped,
                UserCareSnapshot(
                    telegram_id=tg,
                    linked_accounts=max(0, _safe_int(row.get("linked_accounts"))),
                    running_accounts=max(0, _safe_int(row.get("running_accounts"))),
                    last_activity_ts=_row_ts(row),
                    source=method_name,
                ),
            )


async def _collect_users_from_hubbot_persistence(grouped: dict[str, UserCareSnapshot]) -> None:
    if not _include_hubbot_chats():
        return

    try:
        payload = await asyncio.to_thread(_load_hubbot_state_payload)
    except Exception as exc:
        if isinstance(exc, ModuleNotFoundError) and str(getattr(exc, "name", "") or "") == "telegram":
            log_periodic(
                log,
                logging.INFO,
                "[AI_CARE] hubbot chat-state enrichment skipped because backend env has no optional 'telegram' package",
                key="ai_care:hubbot_state_optional_telegram_missing",
                cooldown_sec=max(21600, noisy_log_cooldown_sec()),
            )
        else:
            log_periodic(
                log,
                logging.WARNING,
                "[AI_CARE] hubbot state load failed: %s",
                exc,
                key=f"ai_care:hubbot_state_load_failed:{type(exc).__name__}:{str(exc)[:160]}",
                cooldown_sec=noisy_log_cooldown_sec(),
            )
        return

    if not payload:
        return

    chat_data = payload.get("chat_data")
    if isinstance(chat_data, dict):
        for raw_chat_id, chat_payload in chat_data.items():
            tg = _normalize_telegram_id(raw_chat_id)
            if not tg:
                continue
            _merge_user(
                grouped,
                UserCareSnapshot(
                    telegram_id=tg,
                    linked_accounts=0,
                    running_accounts=0,
                    last_activity_ts=_hubbot_state_last_activity(chat_payload),
                    source="hubbot_chat_data",
                ),
            )

    user_data = payload.get("user_data")
    if isinstance(user_data, dict):
        for raw_user_id, user_payload in user_data.items():
            tg = _normalize_telegram_id(raw_user_id)
            if not tg:
                continue
            _merge_user(
                grouped,
                UserCareSnapshot(
                    telegram_id=tg,
                    linked_accounts=0,
                    running_accounts=0,
                    last_activity_ts=_hubbot_state_last_activity(user_payload),
                    source="hubbot_user_data",
                ),
            )


async def _collect_users_from_env(grouped: dict[str, UserCareSnapshot]) -> None:
    for tg in _extra_telegram_ids():
        _merge_user(
            grouped,
            UserCareSnapshot(
                telegram_id=tg,
                linked_accounts=0,
                running_accounts=0,
                last_activity_ts=0,
                source="env",
            ),
        )


async def _collect_users() -> list[UserCareSnapshot]:
    grouped: dict[str, UserCareSnapshot] = {}

    try:
        store = get_process_store()
    except Exception as exc:
        log.warning("[AI_CARE] make_store failed: %s", exc)
        store = None

    if store is not None:
        await _collect_users_from_control_plane_runtime(store, grouped)
        await _collect_users_from_backend_tables(store, grouped)
        await _collect_users_from_optional_store_sources(store, grouped)

    await _collect_users_from_hubbot_persistence(grouped)
    await _collect_users_from_env(grouped)

    users = list(grouped.values())
    users.sort(
        key=lambda x: (
            x.last_activity_ts,
            x.running_accounts,
            x.linked_accounts,
            x.telegram_id,
        ),
        reverse=True,
    )

    _emit_user_collection_summary(len(users))
    return users


# =========================================================
# CAMPAIGN EXECUTION
# =========================================================
async def _send_morning_to_user(user: UserCareSnapshot, digest: SharedDigest, day_key: str) -> bool:
    sent_key = f"ai:care:morning:sent:{day_key}:{user.telegram_id}"
    retry_key = f"ai:care:morning:retry:{day_key}:{user.telegram_id}"
    inflight_key = f"ai:care:morning:lock:{day_key}:{user.telegram_id}"

    if await _marker_exists(sent_key):
        return False

    # chặn duplicate đồng thời trong cùng vòng
    got_lock = await _mark_once(inflight_key, ttl_sec=120)
    if not got_lock:
        return False

    try:
        # retry ngắn: nếu còn backoff thì skip lượt này thôi
        if await _marker_exists(retry_key):
            return False

        ok = await _send_user_message(user.telegram_id, _build_morning_message(user, digest))
        if ok:
            await _mark_once(sent_key, ttl_sec=48 * 3600)
            await _marker_delete(retry_key)
            return True

        await _mark_once(retry_key, ttl_sec=_send_retry_backoff_sec())
        return False
    finally:
        await _marker_delete(inflight_key)


async def _broadcast_morning(users: list[UserCareSnapshot], digest: SharedDigest) -> None:
    if not users:
        return

    day_key = _today_key()
    batch_size = _telegram_batch_size()
    sleep_sec = _telegram_batch_sleep_sec()

    for i in range(0, len(users), batch_size):
        if _STOP_EVENT is not None and _STOP_EVENT.is_set():
            return
        if not _in_morning_window():
            return

        chunk = users[i : i + batch_size]
        await asyncio.gather(*[_send_morning_to_user(user, digest, day_key) for user in chunk])

        if i + batch_size < len(users):
            await asyncio.sleep(sleep_sec)


async def _run_morning_campaign(users: list[UserCareSnapshot]) -> None:
    if not _in_morning_window():
        return
    digest = await _get_shared_digest(allow_gemini=True)
    await _broadcast_morning(users, digest)


async def _campaign_loop() -> None:
    interval = max(15, int(getattr(settings, "AI_CARE_CHECK_INTERVAL_SEC", 60)))

    while True:
        if _STOP_EVENT is not None and _STOP_EVENT.is_set():
            return

        try:
            if await _ensure_campaign_leader():
                users = await _collect_users()
                if users:
                    await _run_morning_campaign(users)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            log.warning("[AI_CARE] Loop error: %s", exc)

        await asyncio.sleep(interval)


# =========================================================
# LIFECYCLE
# =========================================================
async def start_ai_care_campaign() -> None:
    global _TASK, _STOP_EVENT
    if not _enabled():
        return
    if _TASK is not None and not _TASK.done():
        return

    _STOP_EVENT = asyncio.Event()
    _TASK = asyncio.create_task(_campaign_loop())
    log.info(
        "[AI_CARE] Campaign started (dry_run=%s, morning_only=true, news=%s, gemini_news=%s) 🚀",
        _dry_run(),
        _news_enabled(),
        _news_use_gemini(),
    )


async def stop_ai_care_campaign() -> None:
    global _TASK, _HTTP_CLIENT

    if _STOP_EVENT is not None:
        _STOP_EVENT.set()

    if _TASK is not None:
        _TASK.cancel()
        try:
            await _TASK
        except (asyncio.CancelledError, Exception):
            pass
        _TASK = None

    await _release_campaign_leader()

    if _HTTP_CLIENT is not None:
        try:
            await _HTTP_CLIENT.aclose()
        except Exception:
            pass
        _HTTP_CLIENT = None
