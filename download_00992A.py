# -*- coding: utf-8 -*-
"""
00992A ETF 持股爬蟲
用法：
  python download_00992A.py            -> 下載今天（資料日期=今天，最新日期=下個營業日）
  python download_00992A.py YYYYMMDD   -> 補跑指定資料日期
"""

import sys
import os
import json
import time
import argparse
import warnings
import requests
from datetime import datetime, date

warnings.filterwarnings("ignore")

# ===== 路徑設定 =====
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
DATA_DIR     = os.path.join(BASE_DIR, "00992A_Data")
WORKDAY_FILE = os.path.join(BASE_DIR, "workday.txt")
LOG_FILE     = os.path.join(BASE_DIR, "download_00992A_log.txt")
DATES_JSON   = os.path.join(BASE_DIR, "00992A_dates.json")
FUND_ID      = "500"
PREFIX       = "00992A"

BUYBACK_URL  = "https://www.capitalfund.com.tw/CFWeb/api/etf/buyback"
HEADERS = {
    "Content-Type": "application/json",
    "Accept":       "application/json, text/plain, */*",
    "Origin":       "https://www.capitalfund.com.tw",
    "Referer":      "https://www.capitalfund.com.tw/etf/product/detail/500/portfolio",
    "User-Agent":   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"


# ===== LOG =====
def lg(msg: str):
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ===== 讀取營業日 =====
def load_workdays() -> list:
    """從 workday.txt 讀取並排序所有營業日。"""
    days = []
    with open(WORKDAY_FILE, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s:
                try:
                    days.append(datetime.strptime(s, "%Y/%m/%d").date())
                except ValueError:
                    pass
    return sorted(days)


def next_workday(d: date, workdays: list):
    """回傳 d 之後的第一個營業日；若不存在回傳 None。"""
    for wd in workdays:
        if wd > d:
            return wd
    return None


# ===== 爬取 API =====
def fetch_buyback(api_date_str: str):
    """
    呼叫 buyback API。
    api_date_str: "YYYY/MM/DD" = 最新日期（= 資料日期的下一個營業日）
    回傳 data dict；若 code!=200（無資料）則回傳 None。
    """
    payload = {"fundId": FUND_ID, "date": api_date_str}
    try:
        resp = requests.post(BUYBACK_URL, json=payload, headers=HEADERS,
                             verify=False, timeout=30)
        resp.raise_for_status()
        j = resp.json()
        if j.get("code") == 200:
            return j.get("data", {})
        lg(f"  API 回傳 code={j.get('code')}，訊息: {j.get('message','')}")
        return None
    except Exception as e:
        lg(f"  buyback API 連線失敗 ({api_date_str}): {e}")
        return None


# ===== 收盤價 =====
def get_close_price(stock_id: str, date_str: str) -> float:
    """以 FinMind API 查詢收盤價。date_str 格式 YYYY-MM-DD。"""
    params = {
        "dataset":    "TaiwanStockPrice",
        "data_id":    stock_id,
        "start_date": date_str,
        "end_date":   date_str,
    }
    try:
        resp = requests.get(FINMIND_URL, params=params, timeout=15)
        resp.raise_for_status()
        rows = resp.json().get("data", [])
        if rows:
            return float(rows[0]["close"])
        lg(f"    [{stock_id}] 查無收盤價 ({date_str})")
    except Exception as e:
        lg(f"    [{stock_id}] 收盤價查詢失敗: {e}")
    return 0.0


# ===== 儲存 TXT =====
def save_txt(stocks: list, data_date: date) -> str:
    """
    查詢各股收盤價並存成 00992A_YYYYMMDD.txt。
    格式：股票代號,股票名稱,持股權重,股數,收盤價
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    date_str     = data_date.strftime("%Y%m%d")
    date_api_str = data_date.strftime("%Y-%m-%d")
    save_path    = os.path.join(DATA_DIR, f"{PREFIX}_{date_str}.txt")

    if os.path.exists(save_path):
        lg(f"  已存在，跳過: {save_path}")
        return save_path

    total = len(stocks)
    lg(f"  開始查詢 {total} 檔股票收盤價（日期: {date_api_str}）...")
    lines = ["股票代號,股票名稱,持股權重,股數,收盤價"]

    for i, s in enumerate(stocks, 1):
        code   = str(s["stocNo"]).strip().rstrip("*")
        name   = str(s.get("stocName", "")).strip()
        weight = f"{s['weightRound']:.2f}%"
        shares = int(s["share"])
        price  = get_close_price(code, date_api_str)
        lg(f"    [{i:>2}/{total}] {code} {name}: {price}")
        lines.append(f"{code},{name},{weight},{shares},{price}")
        # 每 5 檔稍作休息，降低 API 頻率
        if i % 5 == 0:
            time.sleep(1)

    with open(save_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    lg(f"  儲存完成: {save_path}  ({total} 筆)")
    return save_path


# ===== 更新 dates.json =====
def update_dates_json():
    if not os.path.exists(DATA_DIR):
        return
    dates = sorted([
        f[7:15]
        for f in os.listdir(DATA_DIR)
        if f.startswith(f"{PREFIX}_") and f.endswith(".txt") and len(f) == 19
    ])
    with open(DATES_JSON, "w", encoding="utf-8") as f:
        json.dump(dates, f)
    lg(f"📋 00992A_dates.json 已更新，共 {len(dates)} 個交易日")


# ===== 主程式 =====
def main():
    lg("=" * 60)
    lg(f"00992A 持股爬蟲 啟動")

    # --- 解析參數 ---
    parser = argparse.ArgumentParser(description="00992A 持股爬蟲")
    parser.add_argument("date", nargs="?",
                        help="補跑用：資料日期 YYYYMMDD；不帶則下載今天")
    args = parser.parse_args()

    workdays = load_workdays()
    lg(f"載入 {len(workdays)} 個營業日（workday.txt）")

    # --- 決定資料日期 ---
    if args.date:
        try:
            data_date = datetime.strptime(args.date, "%Y%m%d").date()
            lg(f"補跑模式：資料日期 = {data_date}")
        except ValueError:
            lg(f"日期格式錯誤：{args.date}，應為 YYYYMMDD")
            sys.exit(1)
    else:
        data_date = date.today()
        lg(f"日常模式：資料日期 = {data_date}（今天）")

    # --- 取得最新日期（下一個營業日）---
    api_date = next_workday(data_date, workdays)
    if api_date is None:
        lg(f"無法取得 {data_date} 的下一個營業日（workday.txt 未涵蓋），程式停止")
        sys.exit(1)

    api_date_str = api_date.strftime("%Y/%m/%d")
    lg(f"資料日期={data_date}  →  最新日期（API參數）={api_date_str}")

    # --- 檔案已存在則直接結束 ---
    date_str  = data_date.strftime("%Y%m%d")
    save_path = os.path.join(DATA_DIR, f"{PREFIX}_{date_str}.txt")
    if os.path.exists(save_path):
        lg(f"檔案已存在，無需重新下載：{save_path}")
        sys.exit(0)

    # --- 呼叫 API ---
    lg(f"呼叫 API（最新日期={api_date_str}）...")
    data = fetch_buyback(api_date_str)
    if data is None:
        lg("無資料（尚未發布 / 非交易日 / API 異常），程式停止")
        sys.exit(0)

    # --- 驗證 date2 ---
    pcf           = data.get("pcf", {})
    returned_date2 = pcf.get("date2", "")
    if returned_date2:
        try:
            returned_d2 = datetime.strptime(returned_date2, "%Y-%m-%d").date()
            if returned_d2 != data_date:
                lg(f"警告：API date2={returned_date2}，與預期 {data_date} 不符 → 以 API 為準")
                data_date = returned_d2
                date_str  = data_date.strftime("%Y%m%d")
                save_path = os.path.join(DATA_DIR, f"{PREFIX}_{date_str}.txt")
                if os.path.exists(save_path):
                    lg(f"調整後的檔案 {save_path} 已存在，跳過")
                    sys.exit(0)
        except ValueError:
            pass

    # --- 儲存 ---
    stocks = data.get("stocks", [])
    if not stocks:
        lg("stocks 清單為空，無資料，程式停止")
        sys.exit(0)

    save_txt(stocks, data_date)
    update_dates_json()
    lg("✅ 完成！")
    lg("=" * 60)


if __name__ == "__main__":
    main()
