
"""
每日房市新聞抓取 → Telegram
來源一：Google News RSS 關鍵字搜尋（多媒體聚合）
來源二：直接 RSS feeds（經濟日報、自由、ETtoday、壹蘋、中央社），標題含關鍵字才保留
兩者合併後去重推送。
Telegram 若超過字數限制將分批發送，每則間隔 3 秒。
"""

import json
import os
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone, timedelta

import requests
import yaml

BASE_DIR          = os.path.dirname(os.path.abspath(__file__))
KEYWORDS_YML      = os.path.join(BASE_DIR, "news_keywords.yml")
UNKNOWN_SOURCES_LOG = os.path.join(BASE_DIR, "unknown_sources.log")
SENT_JSON     = os.path.join(BASE_DIR, "sent_news.json")
LOG_FILE     = os.path.join(BASE_DIR, "News_log.txt")
TG_TOKEN   = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

TW_TZ = timezone(timedelta(hours=8))
WEEKDAY_ZH = ["一", "二", "三", "四", "五", "六", "日"]


# ── 工具函式 ──────────────────────────────────────────────


# ── LOG ───────────────────────────────────────────────────────────────────
def log(msg: str, flush: bool = False):
    """同時輸出到 stdout 與 News_log.txt（台灣時間時間戳記）。"""
    ts   = datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=flush)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_sent() -> dict:
    """載入已發送紀錄 {url: {date, title_norm}, ...}"""
    if os.path.exists(SENT_JSON):
        with open(SENT_JSON, encoding="utf-8") as f:
            data = json.load(f)
        # 相容舊格式 {url: date_str}
        migrated = {}
        for url, val in data.items():
            if isinstance(val, str):
                migrated[url] = {"date": val, "title_norm": ""}
            else:
                migrated[url] = val
        return migrated
    return {}


def save_sent(sent: dict):
    with open(SENT_JSON, "w", encoding="utf-8") as f:
        json.dump(sent, f, ensure_ascii=False, indent=2)


def prune_sent(sent: dict, keep_days: int = 3) -> dict:
    """只保留最近 keep_days 天的紀錄，避免檔案無限增長。"""
    cutoff = (datetime.now(TW_TZ).date() - timedelta(days=keep_days)).isoformat()
    return {url: v for url, v in sent.items() if v["date"] >= cutoff}


def load_config():
    with open(KEYWORDS_YML, encoding="utf-8") as f:
        return yaml.safe_load(f)


def all_keywords(keyword_map: dict) -> list[str]:
    """攤平所有關鍵字為一維清單。"""
    result = []
    for kws in keyword_map.values():
        result.extend(kws)
    return result


def normalize_title(title: str) -> str:
    """正規化標題用於去重：去標點、空白、全半形統一。"""
    title = unicodedata.normalize("NFKC", title)
    title = re.sub(r"[\s\W]", "", title)
    return title.lower()


def shorten_url(url: str) -> str:
    try:
        resp = requests.get(
            "https://tinyurl.com/api-create.php",
            params={"url": url},
            timeout=10,
        )
        if resp.ok and resp.text.startswith("http"):
            return resp.text.strip()
    except Exception:
        pass
    return url


def parse_rss(content: bytes, today, oldest=None, max_items: int = 999) -> list[dict]:
    """解析 RSS XML，回傳當天（台灣時區）的新聞列表。"""
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return []

    items = root.findall(".//item")
    results = []

    for item in items:
        if len(results) >= max_items:
            break

        title_el   = item.find("title")
        link_el    = item.find("link")
        pubdate_el = item.find("pubDate")

        if title_el is None or link_el is None:
            continue

        title     = (title_el.text or "").strip()
        link      = (link_el.text or "").strip()
        source_el = item.find("source")
        source    = (source_el.text or "").strip() if source_el is not None else ""
        # source url 屬性是真實媒體網域，不需跟隨 Google 轉址
        source_url = source_el.attrib.get("url", "").lower() if source_el is not None else ""

        # 去掉 Google News 附加的來源後綴「 - 媒體名稱」
        title = re.sub(r"\s*-\s*[^-]+$", "", title).strip()

        # 過濾非當天新聞
        if pubdate_el is not None and pubdate_el.text:
            try:
                pub = datetime.strptime(
                    pubdate_el.text.strip(), "%a, %d %b %Y %H:%M:%S %Z"
                ).replace(tzinfo=timezone.utc).astimezone(TW_TZ).date()
                cutoff = oldest if oldest is not None else today
                if not (cutoff <= pub <= today):
                    continue
            except ValueError:
                pass

        results.append({"title": title, "url": link, "source": source, "source_url": source_url})

    return results


def fetch_url(url: str) -> bytes | None:
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        log(f"  [WARN] 抓取失敗: {e}")
        return None


def title_matches_keywords(title: str, keywords: list[str]) -> bool:
    t = title.lower()
    return any(kw.lower() in t for kw in keywords)


def is_taiwan_news(item: dict, taiwan_sources: list[str], foreign_keywords: list[str]) -> tuple[bool, str]:
    """
    兩道關卡：
    1. source_url 必須在白名單（來源是台灣媒體）
    2. 標題不含外國地名（內容是台灣新聞）
    """
    source_url = item.get("source_url", "")
    in_whitelist = any(domain.lower() in source_url for domain in taiwan_sources)
    if not in_whitelist:
        log_unknown_source(item)
        return False, f"非白名單來源({item.get('source', '')} {source_url[:40]})"

    title = item.get("title", "")
    for kw in foreign_keywords:
        if kw in title:
            return False, f"外國地名({kw})"

    return True, ""


def log_unknown_source(item: dict):
    """將非白名單來源記錄到 unknown_sources.log。"""
    now        = datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M")
    source     = item.get("source", "")
    source_url = item.get("source_url", "")
    title      = item.get("title", "")
    line       = f"{now} | {source} | {source_url} | {title}\n"
    with open(UNKNOWN_SOURCES_LOG, "a", encoding="utf-8") as f:
        f.write(line)


def send_message(text: str):
    if not TG_TOKEN or not TG_CHAT_ID:
        log("❌ TG_TOKEN 或 TG_CHAT_ID 未設定，僅列印訊息。")
        return
    url  = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=15,
    )
    if resp.ok:
        log("✅ Telegram 訊息發送成功")
    else:
        log(f"❌ 發送失敗: {resp.text}")


# ── 主流程 ────────────────────────────────────────────────

def main():
    config      = load_config()
    settings    = config.get("settings", {})
    max_per_kw      = settings.get("max_per_keyword", 3)
    max_total       = settings.get("max_total", 200)
    news_days       = settings.get("news_days", 1)
    keyword_map     = config.get("keywords", {})
    rss_feeds       = config.get("rss_feeds", [])
    taiwan_sources  = config.get("taiwan_sources", [])
    foreign_keywords = config.get("foreign_keywords", [])
    kw_flat         = all_keywords(keyword_map)

    now      = datetime.now(TW_TZ)
    weekday  = WEEKDAY_ZH[now.weekday()]
    date_str = f"{now.year}/{now.month:02d}/{now.day:02d} ({weekday})"
    today    = now.date()
    oldest   = today - timedelta(days=news_days - 1)

    log(f"📅 {date_str} 開始抓取新聞...\n")

    # 載入歷史發送紀錄
    sent_history = prune_sent(load_sent())
    log(f"📋 歷史紀錄：{len(sent_history)} 則已排除\n")

    seen_urls    = set(sent_history.keys())
    seen_titles  = set()
    seen_norm_titles = [v["title_norm"] for v in sent_history.values() if v.get("title_norm")]
    news_list    = []
    CHAR_OVERLAP_THRESHOLD = 0.50

    def char_overlap(a: str, b: str) -> float:
        """兩個 normalize 後標題的字元重疊率（含重複字，取較短長度為基準）。"""
        ca, cb = Counter(a), Counter(b)
        common = sum((ca & cb).values())
        shorter = min(len(a), len(b))
        return common / shorter if shorter > 0 else 0.0

    def is_char_dup(title_norm: str) -> tuple[bool, str]:
        for t in seen_norm_titles:
            ratio = char_overlap(title_norm, t)
            if ratio >= CHAR_OVERLAP_THRESHOLD:
                return True, f"字元重疊 {ratio:.0%}"
        return False, ""

    def try_add(item: dict, verbose: bool = True) -> bool:
        if len(news_list) >= max_total:
            if verbose:
                log(f"      ✗ 已達上限 {max_total} 則：{item['title'][:20]}")
            return False
        url_key   = item["url"]
        title_key = normalize_title(item["title"])
        if url_key in seen_urls:
            if verbose:
                log(f"      ✗ URL重複：{item['title'][:30]}")
            return False
        if title_key in seen_titles:
            if verbose:
                log(f"      ✗ 標題完全相同：{item['title'][:30]}")
            return False
        keep, reason = is_taiwan_news(item, taiwan_sources, foreign_keywords)
        if not keep:
            if verbose:
                log(f"      ✗ {reason}：{item['title'][:30]}")
            return False
        dup, reason = is_char_dup(title_key)
        if dup:
            if verbose:
                log(f"      ✗ {reason}：{item['title'][:30]}")
            return False
        seen_urls.add(url_key)
        seen_titles.add(title_key)
        seen_norm_titles.append(title_key)
        news_list.append(item)
        return True

    # ── 來源一：Google News RSS 關鍵字搜尋 ──
    log("【來源一：Google News 關鍵字搜尋】")
    for category, keywords in keyword_map.items():
        log(f"  [{category}]")
        for kw in keywords:
            if len(news_list) >= max_total:
                break
            url = (
                "https://news.google.com/rss/search"
                f"?q={requests.utils.quote(kw)}"
                "&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
            )
            content = fetch_url(url)
            if not content:
                continue
            items = parse_rss(content, today, oldest=oldest, max_items=max_per_kw)
            added = sum(1 for item in items if try_add(item))
            log(f"    {kw}: 抓到 {len(items)} 則，新增 {added} 則")
        if len(news_list) >= max_total:
            break

    # ── 來源二：直接 RSS feeds ──
    log("\n【來源二：直接 RSS feeds】")
    for feed in rss_feeds:
        if len(news_list) >= max_total:
            break
        name              = feed.get("name", "未知")
        url               = feed.get("url", "")
        filter_by_keywords = feed.get("filter_by_keywords", True)
        log(f"  [{name}]")
        content = fetch_url(url)
        if not content:
            continue
        items = parse_rss(content, today, oldest=oldest)
        if filter_by_keywords:
            candidates = [i for i in items if title_matches_keywords(i["title"], kw_flat)]
            label = f"共 {len(items)} 則，關鍵字符合 {len(candidates)} 則"
        else:
            candidates = items
            label = f"共 {len(items)} 則（全部保留）"
        added = sum(1 for item in candidates if try_add(item))
        log(f"    {label}，新增 {added} 則")

    if not news_list:
        log("\n⚠️  無當天新聞，不推送。")
        return

    # ── 縮網址 ──
    log(f"\n共 {len(news_list)} 則，開始縮網址...")
    for item in news_list:
        item["short_url"] = shorten_url(item["url"])

    # ── 組訊息與分批發送 ──
    log("\n開始組裝並發送訊息...")
    MAX_TG_LENGTH = 3000  # 設定為 3000 保留一些緩衝空間，Telegram 上限為 4096
    
    header = f"=====================================\n{date_str} 早安\n\n🏠 今日房市新聞\n"
    
    msg_chunks = []
    current_chunk = header
    
    for item in news_list:
        item_text = f"\n{item['title']}\n{item['short_url']}\n"
        
        # 檢查若加上這則新聞會不會超過字數限制
        if len(current_chunk) + len(item_text) > MAX_TG_LENGTH:
            msg_chunks.append(current_chunk)
            current_chunk = item_text # 另起新的一包
        else:
            current_chunk += item_text
            
    # 把最後一包也加進去
    if current_chunk:
        msg_chunks.append(current_chunk)

    log(f"將分為 {len(msg_chunks)} 則訊息發送。")

    for index, chunk in enumerate(msg_chunks):
        log(f"\n發送第 {index + 1}/{len(msg_chunks)} 則...")
        send_message(chunk)
        
        # 如果不是最後一則，就等待 3 秒
        if index < len(msg_chunks) - 1:
            time.sleep(3)

    # 發送後更新歷史紀錄
    today_iso = today.isoformat()
    for item in news_list:
        sent_history[item["url"]] = {
            "date": today_iso,
            "title_norm": normalize_title(item["title"]),
        }
    save_sent(sent_history)
    log(f"\n💾 已更新 sent_news.json（共 {len(sent_history)} 則）")


if __name__ == "__main__":
    main()