# -*- coding: utf-8 -*-
"""
00981A 持股淨變動通知 → Telegram
當天資料產生後執行，比較前一交易日，推送加碼/減碼前10大。
"""

import os
import json
import requests

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DATA_DIR   = os.path.join(BASE_DIR, "00981A_Data")
DATES_JSON = os.path.join(BASE_DIR, "dates.json")

TG_TOKEN   = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
TOP_N      = 10


def display_width(s):
    """計算字串的顯示寬度（中文字佔 2 格）"""
    return sum(2 if ord(c) > 0x2E7F else 1 for c in s)


def rpad(s, width):
    """右補空白到指定顯示寬度"""
    return s + " " * max(0, width - display_width(s))


def lpad(s, width):
    """左補空白到指定顯示寬度"""
    return " " * max(0, width - display_width(s)) + s


def parse_file(date_str):
    path = os.path.join(DATA_DIR, f"00981A_{date_str}.txt")
    if not os.path.exists(path):
        return {}
    result = {}
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) < 5:
            continue
        code   = parts[0].strip()
        name   = parts[1].strip()
        shares = int(parts[3].strip()) if parts[3].strip().isdigit() else 0
        try:
            weight = float(parts[2].strip().replace("%", ""))
        except ValueError:
            weight = 0.0
        try:
            price = float(parts[4].strip())
        except ValueError:
            price = 0.0
        result[code] = {"name": name, "shares": shares, "weight": weight, "price": price}
    return result


def send_message(text):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("❌ TG_TOKEN 或 TG_CHAT_ID 未設定")
        return
    url  = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    resp = requests.post(url, json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=15)
    if resp.ok:
        print("✅ Telegram 訊息發送成功")
    else:
        print(f"❌ 發送失敗: {resp.text}")


def fmt_row(c):
    diff = f"+{c['diff']:,}張" if c["diff"] > 0 else f"{c['diff']:,}張"
    wt   = f" {c['weight']:.2f}%" if c["weight"] > 0 else ""
    px   = f" ${c['price']:.0f}" if c["price"] > 0 else ""
    tag  = f" {c['tag']}" if c["tag"] else ""
    return f"{c['code']} {c['name']} {diff}{wt}{px}{tag}"


def main():
    with open(DATES_JSON, encoding="utf-8") as f:
        dates = json.load(f)

    if len(dates) < 2:
        print("日期不足，無法比較")
        return

    today_str = dates[-1]
    prev_str  = dates[-2]

    today_data = parse_file(today_str)
    prev_data  = parse_file(prev_str)

    if not today_data:
        print(f"今日資料不存在：{today_str}")
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
        price  = t["price"]  if t else 0.0
        weight = t["weight"] if t else 0.0
        tag    = "🆕" if not p else ("🚫" if not t else "")
        changes.append({"code": code, "name": name, "diff": diff, "price": price, "weight": weight, "tag": tag})

    added   = sorted([c for c in changes if c["diff"] > 0], key=lambda x: -x["diff"])[:TOP_N]
    removed = sorted([c for c in changes if c["diff"] < 0], key=lambda x:  x["diff"])[:TOP_N]

    d_label = f"{today_str[:4]}/{today_str[4:6]}/{today_str[6:8]}"
    p_label = f"{prev_str[:4]}/{prev_str[4:6]}/{prev_str[6:8]}"

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

    lines.append(f"<i>共 {len(added)} 檔加碼 / {len(removed)} 檔減碼</i>")

    text = "\n".join(lines)
    print(text)
    send_message(text)


if __name__ == "__main__":
    main()
