# -*- coding: utf-8 -*-
"""
00981A 持股淨變動通知 → Telegram
功能：比較前後兩日持股，計算張數增減，並推送到 Telegram。
優化：自動對齊欄位、移除股價、支援中英文字寬計算。
"""

import os
import json
import requests

# --- 設定區 ---
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DATA_DIR   = os.path.join(BASE_DIR, "00981A_Data")
DATES_JSON = os.path.join(BASE_DIR, "dates.json")

# 請確保環境變數中有設定以下資訊，或直接在此輸入字串
TG_TOKEN   = os.environ.get("TG_TOKEN", "你的_BOT_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "你的_CHAT_ID")
TOP_N      = 10

# --- 工具函數：處理中英文對齊 ---
def display_width(s):
    """計算字串的顯示寬度（中文字佔 2 格，英數佔 1 格）"""
    return sum(2 if ord(c) > 0x2E7F else 1 for c in s)

def rpad(s, width):
    """右補空白到指定顯示寬度"""
    return s + " " * max(0, width - display_width(s))

def lpad(s, width):
    """左補空白到指定顯示寬度"""
    return " " * max(0, width - display_width(s)) + s

# --- 資料處理 ---
def parse_file(date_str):
    path = os.path.join(DATA_DIR, f"00981A_{date_str}.txt")
    if not os.path.exists(path):
        return {}
    result = {}
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        for line in lines[1:]:  # 跳過標題列
            parts = line.split(",")
            if len(parts) < 4:
                continue
            code   = parts[0].strip()
            name   = parts[1].strip()
            # 假設 CSV 格式：代號,名稱,權重,張數(或股數值)
            shares = int(parts[3].strip()) if parts[3].strip().isdigit() else 0
            try:
                weight = float(parts[2].strip().replace("%", ""))
            except ValueError:
                weight = 0.0
            
            result[code] = {"name": name, "shares": shares, "weight": weight}
    except Exception as e:
        print(f"❌ 解析檔案 {date_str} 出錯: {e}")
    return result

def send_message(text):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("❌ TG_TOKEN 或 TG_CHAT_ID 未設定")
        return
    url  = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    resp = requests.post(url, json=payload, timeout=15)
    if resp.ok:
        print("✅ Telegram 訊息發送成功")
    else:
        print(f"❌ 發送失敗: {resp.text}")

def fmt_row(c):
    """格式化每一行持股變動，移除股價，加強對齊"""
    diff_str = f"{c['diff']:+,}張"
    wt_str   = f"{c['weight']:.2f}%" if c["weight"] > 0 else ""
    tag      = f" {c['tag']}" if c["tag"] else ""
    
    # 這裡調整欄位寬度：名稱區 18, 張數區 8, 權重區 7
    col_name   = rpad(f"{c['code']} {c['name']}", 18)
    col_diff   = lpad(diff_str, 8)
    col_weight = lpad(wt_str, 7)
    
    return f"{col_name} {col_diff} {col_weight}{tag}"

# --- 主程式 ---
def main():
    if not os.path.exists(DATES_JSON):
        print("❌ 找不到 dates.json")
        return

    with open(DATES_JSON, encoding="utf-8") as f:
        dates = json.load(f)

    if len(dates) < 2:
        print("💡 日期不足兩份，無法進行比較")
        return

    today_str = dates[-1]
    prev_str  = dates[-2]

    today_data = parse_file(today_str)
    prev_data  = parse_file(prev_str)

    if not today_data:
        print(f"❌ 今日資料不存在：{today_str}")
        return

    # 計算變化邏輯
    all_codes = set(today_data) | set(prev_data)
    changes = []
    for code in all_codes:
        t = today_data.get(code)
        p = prev_data.get(code)
        
        # 轉換為張數 (假設原始資料是股數，除以 1000 並四捨五入)
        t_lots = round(t["shares"] / 1000) if t else 0
        p_lots = round(p["shares"] / 1000) if p else 0
        
        diff = t_lots - p_lots
        if diff == 0:
            continue
            
        name   = (t or p)["name"]
        weight = t["weight"] if t else 0.0
        tag    = "🆕" if not p else ("🚫" if not t else "")
        
        changes.append({
            "code": code, "name": name, "diff": diff, 
            "weight": weight, "tag": tag
        })

    # 分類並排序
    added   = sorted([c for c in changes if c["diff"] > 0], key=lambda x: -x["diff"])[:TOP_N]
    removed = sorted([c for c in changes if c["diff"] < 0], key=lambda x:  x["diff"])[:TOP_N]

    # 格式化日期標籤 (YYYYMMDD -> YYYY/MM/DD)
    d_label = f"{today_str[:4]}/{today_str[4:6]}/{today_str[6:8]}"
    p_label = f"{prev_str[:4]}/{prev_str[4:6]}/{prev_str[6:8]}"

    # 組裝訊息文字
    lines = [
        f"📊 <b>00981A 持股變動</b>  {d_label}",
        f"<i>vs {p_label}</i>",
        "",
        "🔺 <b>加碼前10大</b>" if added else "🔺 <b>加碼：無變動</b>",
    ]
    
    if added:
        lines.append("<pre>")
        for c in added:
            lines.append(fmt_row(c))
        lines.append("</pre>")

    lines.append("\n🔻 <b>減碼前10大</b>" if removed else "\n🔻 <b>減碼：無變動</b>")
    if removed:
        lines.append("<pre>")
        for c in removed:
            lines.append(fmt_row(c))
        lines.append("</pre>")

    lines.append(f"\n<i>共 {len([c for c in changes if c['diff'] > 0])} 檔加碼 / {len([c for c in changes if c['diff'] < 0])} 檔減碼</i>")

    full_text = "\n".join(lines)
    
    # 執行輸出與發送
    print(full_text)
    send_message(full_text)

if __name__ == "__main__":
    main()