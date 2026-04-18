# -*- coding: utf-8 -*-
"""
每日房市新聞抓取 → Telegram
來源一：Google News RSS 關鍵字搜尋（多媒體聚合）
來源二：直接 RSS feeds（經濟日報、自由、ETtoday、壹蘋、中央社），標題含關鍵字才保留
兩者合併後去重推送。
"""

import difflib
import json
import os
import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

import jieba
import requests
import yaml

# 停用詞：分詞後排除這些字，避免「的」「了」影響相似度
STOPWORDS = set("的了是在也都和與及或但卻對於到從把被讓使其這那有沒無不很更最就還")

def seg(title: str) -> set[str]:
    """jieba 分詞後去停用詞，回傳詞集。"""
    words = jieba.lcut(normalize_title(title))
    return {w for w in words if w and w not in STOPWORDS and len(w) > 1}

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
KEYWORDS_YML  = os.path.join(BASE_DIR, "news_keywords.yml")
SENT_JSON     = os.path.join(BASE_DIR, "sent_news.json")

TG_TOKEN   = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

TW_TZ = timezone(timedelta(hours=8))
WEEKDAY_ZH = ["一", "二", "三", "四", "五", "六", "日"]


# ── 工具函式 ──────────────────────────────────────────────

def load_sent() -> dict:
    """載入已發送紀錄 {url: {date, title_norm}, ...}"""
    if os.path.exists(SENT_JSON):
        with open(SENT_JSON, encoding="utf-8") as f:
            data = json.load(f)
        # 相容舊格式
        migrated = {}
        for url, val in data.items():
            if isinstance(val, str):
                migrated[url] = {"date": val, "title_norm": "", "words": []}
            elif "words" not in val:
                val["words"] = []
                migrated[url] = val
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

        title = (title_el.text or "").strip()
        link  = (link_el.text or "").strip()

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

        results.append({"title": title, "url": link})

    return results


def fetch_url(url: str) -> bytes | None:
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        print(f"  [WARN] 抓取失敗: {e}")
        return None


def title_matches_keywords(title: str, keywords: list[str]) -> bool:
    """標題是否含任一關鍵字（不分大小寫）。"""
    t = title.lower()
    return any(kw.lower() in t for kw in keywords)


def send_message(text: str):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("❌ TG_TOKEN 或 TG_CHAT_ID 未設定，僅列印訊息。")
        return
    url  = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=15,
    )
    if resp.ok:
        print("✅ Telegram 訊息發送成功")
    else:
        print(f"❌ 發送失敗: {resp.text}")


# ── 主流程 ────────────────────────────────────────────────

def main():
    config      = load_config()
    settings    = config.get("settings", {})
    max_per_kw  = settings.get("max_per_keyword", 3)
    max_total   = settings.get("max_total", 20)
    news_days   = settings.get("news_days", 1)
    keyword_map = config.get("keywords", {})
    rss_feeds   = config.get("rss_feeds", [])
    kw_flat     = all_keywords(keyword_map)

    now      = datetime.now(TW_TZ)
    weekday  = WEEKDAY_ZH[now.weekday()]
    date_str = f"{now.year}/{now.month:02d}/{now.day:02d} ({weekday})"
    today    = now.date()
    oldest   = today - timedelta(days=news_days - 1)

    print(f"📅 {date_str} 開始抓取新聞...\n")

    # 載入歷史發送紀錄
    sent_history = prune_sent(load_sent())
    print(f"📋 歷史紀錄：{len(sent_history)} 則已排除\n")

    seen_urls   = set(sent_history.keys())
    seen_titles = set()
    # 歷史詞集清單，用於跨天 jieba 比對
    seen_words  = [set(v["words"]) for v in sent_history.values() if v.get("words")]
    news_list   = []
    DIFFLIB_THRESHOLD  = 0.65
    COVERAGE_THRESHOLD = 0.70

    def is_dup(title: str, title_norm: str) -> bool:
        words_new = seg(title)
        for w_old in seen_words:
            if not w_old or not words_new:
                continue
            shorter = min(words_new, w_old, key=len)
            longer  = words_new if len(words_new) >= len(w_old) else w_old
            coverage = len(shorter & longer) / len(shorter)
            if coverage >= COVERAGE_THRESHOLD:
                return True
        # difflib 備援（應對極短標題 jieba 詞太少的情況）
        return any(
            difflib.SequenceMatcher(None, title_norm, t).ratio() >= DIFFLIB_THRESHOLD
            for t in (normalize_title(v["title_norm"]) for v in sent_history.values() if v.get("title_norm"))
        )

    def try_add(item: dict) -> bool:
        if len(news_list) >= max_total:
            return False
        url_key   = item["url"]
        title_key = normalize_title(item["title"])
        if url_key in seen_urls or title_key in seen_titles:
            return False
        if is_dup(item["title"], title_key):
            return False
        seen_urls.add(url_key)
        seen_titles.add(title_key)
        seen_words.append(seg(item["title"]))
        news_list.append(item)
        return True

    # ── 來源一：Google News RSS 關鍵字搜尋 ──
    print("【來源一：Google News 關鍵字搜尋】")
    for category, keywords in keyword_map.items():
        print(f"  [{category}]")
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
            print(f"    {kw}: 抓到 {len(items)} 則，新增 {added} 則")
        if len(news_list) >= max_total:
            break

    # ── 來源二：直接 RSS feeds，按關鍵字過濾 ──
    print("\n【來源二：直接 RSS feeds】")
    for feed in rss_feeds:
        if len(news_list) >= max_total:
            break
        name = feed.get("name", "未知")
        url  = feed.get("url", "")
        print(f"  [{name}]")
        content = fetch_url(url)
        if not content:
            continue
        items = parse_rss(content, today, oldest=oldest)
        matched = [i for i in items if title_matches_keywords(i["title"], kw_flat)]
        added   = sum(1 for item in matched if try_add(item))
        print(f"    共 {len(items)} 則，關鍵字符合 {len(matched)} 則，新增 {added} 則")

    if not news_list:
        print("\n⚠️  無當天新聞，不推送。")
        return

    # ── 縮網址 ──
    print(f"\n共 {len(news_list)} 則，開始縮網址...")
    for item in news_list:
        item["short_url"] = shorten_url(item["url"])

    # ── 組訊息 ──
    lines = [
        "=====================================",
        f"{date_str} 早安",
        "",
        "🏠 今日房市新聞",
    ]
    for item in news_list:
        lines.append("")
        lines.append(item["title"])
        lines.append(item["short_url"])

    message = "\n".join(lines)
    print("\n" + message)
    send_message(message)

    # 發送後更新歷史紀錄
    today_iso = today.isoformat()
    for item in news_list:
        sent_history[item["url"]] = {
            "date": today_iso,
            "title_norm": normalize_title(item["title"]),
            "words": list(seg(item["title"])),
        }
    save_sent(sent_history)
    print(f"\n💾 已更新 sent_news.json（共 {len(sent_history)} 則）")


if __name__ == "__main__":
    main()
