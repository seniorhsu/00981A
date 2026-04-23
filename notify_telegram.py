# -*- coding: utf-8 -*-
"""
00981A 持股淨變動通知 → Telegram
當天資料產生後執行，比較前一交易日，推送加碼/減碼前10大。
"""

import os
import json
import time
import requests
from datetime import datetime

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DATA_DIR   = os.path.join(BASE_DIR, "00981A_Data")
DATES_JSON = os.path.join(BASE_DIR, "dates.json")
LOG_FILE   = os.path.join(BASE_DIR, "notify_telegram_log.txt")

TG_TOKEN   = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
TOP_N      = 10
TG_MAX_LEN = 4000  # Telegram 單則上限 4096，保留緩衝


# ── LOG ───────────────────────────────────────────────────────────────────
def lg(msg: str):
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ── 資料解析 ──────────────────────────────────────────────────────────────
def parse_file(date_str):
    path = os.path.join(DATA_DIR, f"00981A_{date_str}.txt")
    if not os.path.exists(path):
        return {}
    result = {}
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        for line in lines[1:]:
            parts = line.split(",")
            if len(parts) < 4:
                continue
            code   = parts[0].strip()
            name   = parts[1].strip()
            shares = int(parts[3].strip()) if parts[3].strip().isdigit() else 0
            try:
                weight = float(parts[2].strip().replace("%", ""))
            except ValueError:
                weight = 0.0
            result[code] = {"name": name, "shares": shares, "weight": weight}
    except Exception as e:
        lg(f"❌ 解析檔案 {date_str} 出錯: {e}")
    return result


# ── Telegram 發送（單則）────────────────────────────────────────────────
def send_one(text: str) -> bool:
    if not TG_TOKEN or not TG_CHAT_ID:
        lg("❌ TG_TOKEN 或 TG_CHAT_ID 未設定")
        return False
    url  = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    resp = requests.post(url, json={
        "chat_id":    TG_CHAT_ID,
        "text":       text,
        "parse_mode": "HTML",
    }, timeout=15)
    if resp.ok:
        return True
    lg(f"❌ 發送失敗: {resp.status_code} {resp.text[:200]}")
    return False


# ── 分割並依序發送（每則間隔 1.5 秒，避免 429）────────────────────────
def send_messages(parts: list[str]):
    for i, text in enumerate(parts):
        ok = send_one(text)
        lg(f"{'✅' if ok else '❌'} 第 {i+1}/{len(parts)} 則{'成功' if ok else '失敗'}")
        if i < len(parts) - 1:
            time.sleep(1.5)


# ── 超長訊息自動分割 ──────────────────────────────────────────────────
def split_message(text: str) -> list[str]:
    if len(text) <= TG_MAX_LEN:
        return [text]
    parts, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > TG_MAX_LEN:
            parts.append(current.rstrip())
            current = line + "\n"
        else:
            current += line + "\n"
    if current.strip():
        parts.append(current.rstrip())
    return parts


# ── 主程式 ────────────────────────────────────────────────────────────────
def main():
    lg("=" * 50)
    lg("00981A Telegram 通知啟動")

    if not os.path.exists(DATES_JSON):
        lg("❌ 找不到 dates.json")
        return

    with open(DATES_JSON, encoding="utf-8") as f:
        dates = json.load(f)

    if len(dates) < 2:
        lg("日期不足兩份，無法比較")
        return

    today_str = dates[-1]
    prev_str  = dates[-2]
    lg(f"比較：{today_str} vs {prev_str}")

    today_data = parse_file(today_str)
    prev_data  = parse_file(prev_str)

    if not today_data:
        lg(f"今日資料不存在：{today_str}")
        return

    # 計算張數變化
    all_codes = set(today_data) | set(prev_data)
    changes = []
    for code in all_codes:
        t      = today_data.get(code)
        p      = prev_data.get(code)
        t_lots = round(t["shares"] / 1000) if t else 0
        p_lots = round(p["shares"] / 1000) if p else 0
        diff   = t_lots - p_lots
        if diff == 0:
            continue
        name   = (t or p)["name"]
        weight = t["weight"] if t else 0.0
        tag    = "🆕" if not p else ("🚫" if not t else "")
        changes.append({"code": code, "name": name, "diff": diff, "weight": weight, "tag": tag})

    added   = sorted([c for c in changes if c["diff"] > 0], key=lambda x: -x["diff"])[:TOP_N]
    removed = sorted([c for c in changes if c["diff"] < 0], key=lambda x:  x["diff"])[:TOP_N]
    lg(f"加碼 {len(added)} 檔 / 減碼 {len(removed)} 檔")

    d_label = f"{today_str[:4]}/{today_str[4:6]}/{today_str[6:8]}"
    p_label = f"{prev_str[:4]}/{prev_str[4:6]}/{prev_str[6:8]}"

    def fmt_row(c):
        diff = f"+{c['diff']:,}張" if c["diff"] > 0 else f"{c['diff']:,}張"
        wt   = f" {c['weight']:.2f}%" if c["weight"] > 0 else ""
        tag  = f" {c['tag']}" if c["tag"] else ""
        return f"{c['code']} {c['name']} {diff}{wt}{tag}"

    lines = [
        f"📊 <b>00981A 持股變動</b>  {d_label}",
        f"<i>vs {p_label}</i>",
        "",
        "🔺 <b>加碼前10大</b>" if added else "🔺 加碼：無",
    ]
    for c in added:
        lines.append(fmt_row(c))
    lines.append("")
    lines.append("🔻 <b>減碼前10大</b>" if removed else "🔻 減碼：無")
    for c in removed:
        lines.append(fmt_row(c))
    lines.append("")
    lines.append(f"<i>共 {len(added)} 檔加碼 / {len(removed)} 檔減碼</i>")

    full_text = "\n".join(lines)
    parts = split_message(full_text)
    lg(f"訊息共 {len(full_text)} 字，分 {len(parts)} 則發送")

    send_messages(parts)
    lg("完成")
    lg("=" * 50)


if __name__ == "__main__":
    main()
