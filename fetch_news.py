# -*- coding: utf-8 -*-
"""
每日房市新聞抓取 → Telegram
依 news_keywords.yml 設定的關鍵字查詢 Google News RSS，去重後推送。
"""

import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

import requests
import yaml

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
KEYWORDS_YML = os.path.join(BASE_DIR, "news_keywords.yml")

TG_TOKEN   = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

TW_TZ = timezone(timedelta(hours=8))

WEEKDAY_ZH = ["一", "二", "三", "四", "五", "六", "日"]


# ── 工具函式 ──────────────────────────────────────────────

def load_config():
    with open(KEYWORDS_YML, encoding="utf-8") as f:
        return yaml.safe_load(f)


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


def fetch_rss(keyword: str, max_items: int) -> list[dict]:
    """查詢 Google News RSS，回傳當天（台灣時區）的新聞列表。"""
    url = (
        "https://news.google.com/rss/search"
        f"?q={requests.utils.quote(keyword)}"
        "&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    )
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except Exception as e:
        print(f"  [WARN] 抓取失敗 ({keyword}): {e}")
        return []

    root  = ET.fromstring(resp.content)
    items = root.findall(".//item")
    today = datetime.now(TW_TZ).date()
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

        # 去掉 Google News 附加的來源後綴，例如「 - 聯合新聞網」
        title = re.sub(r"\s*-\s*[^-]+$", "", title).strip()

        # 過濾非當天新聞
        if pubdate_el is not None and pubdate_el.text:
            try:
                pub = datetime.strptime(
                    pubdate_el.text.strip(), "%a, %d %b %Y %H:%M:%S %Z"
                ).replace(tzinfo=timezone.utc).astimezone(TW_TZ).date()
                if pub != today:
                    continue
            except ValueError:
                pass

        results.append({"title": title, "url": link})

    return results


def send_message(text: str):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("❌ TG_TOKEN 或 TG_CHAT_ID 未設定")
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
    max_total   = settings.get("max_total", 15)
    keyword_map = config.get("keywords", {})

    now     = datetime.now(TW_TZ)
    weekday = WEEKDAY_ZH[now.weekday()]
    date_str = f"{now.year}/{now.month:02d}/{now.day:02d} ({weekday})"

    print(f"📅 {date_str} 開始抓取新聞...")

    seen_urls   = set()
    seen_titles = set()
    news_list   = []

    for category, keywords in keyword_map.items():
        print(f"\n【{category}】")
        for kw in keywords:
            items = fetch_rss(kw, max_per_kw)
            print(f"  {kw}: {len(items)} 則")
            for item in items:
                # 以 URL 去重，標題前 10 字也去重避免同一篇不同連結
                key = item["url"]
                title_key = item["title"][:10]
                if key in seen_urls or title_key in seen_titles:
                    continue
                seen_urls.add(key)
                seen_titles.add(title_key)
                news_list.append(item)
                if len(news_list) >= max_total:
                    break
            if len(news_list) >= max_total:
                break
        if len(news_list) >= max_total:
            break

    if not news_list:
        print("⚠️  無當天新聞，不推送。")
        return

    # 縮網址
    print(f"\n共 {len(news_list)} 則，開始縮網址...")
    for item in news_list:
        item["short_url"] = shorten_url(item["url"])

    # 組訊息
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


if __name__ == "__main__":
    main()
