from __future__ import annotations

import asyncio
import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, time as dt_time
from typing import Optional, Dict, Any, List
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from fastapi import FastAPI, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

try:
    import firebase_admin
    from firebase_admin import credentials, messaging
except Exception:
    firebase_admin = None
    credentials = None
    messaging = None


APP_NAME = "VNDIRECT Full Technical Alert"

# =========================
# Timezone + market hours
# =========================
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Ho_Chi_Minh")
LOCAL_TZ = ZoneInfo(APP_TIMEZONE)

MARKET_OPEN_TIME = dt_time(9, 0)
MARKET_CLOSE_TIME = dt_time(14, 45)

DCHART_URL = "https://dchart-api.vndirect.com.vn/dchart/history"
FINFO_STOCKS_URL = "https://finfo-api.vndirect.com.vn/v4/stocks"

DEFAULT_SYMBOLS = [
    "VCB", "BID", "CTG", "TCB", "MBB", "ACB", "STB", "VPB", "HDB", "SHB",
    "SSI", "VND", "VCI", "HCM", "MBS", "FTS",
    "VIC", "VHM", "VRE", "PDR", "DXG", "DIG", "KDH", "NLG",
    "HPG", "HSG", "NKG", "GEX", "VGC", "DGC", "DPM", "DCM",
    "GAS", "PVD", "PVS", "BSR", "PLX",
    "FPT", "MWG", "FRT", "DGW", "PNJ",
    "VNM", "MSN", "SAB", "GVR",
    "REE", "POW", "NT2", "PC1",
    "GMD", "HAH", "VSC", "VJC", "HVN",
]

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def default_config() -> dict:
    return {
        "auto_scan_enabled": True,
        "scan_interval_sec": 10,
        "min_buy_score": 50,
        "min_sell_score": 50,
        "scan_symbols": [],
        "max_workers": 8,
        "alert_cooldown_sec": 900,
        "positions": [],
        "firebase_enabled": False,
        "firebase_service_account_path": "serviceAccountKey.json",
        "firebase_vapid_key": "PASTE_YOUR_WEB_PUSH_CERTIFICATE_KEY_PAIR_HERE",
        "firebase_web_config": {
            "apiKey": "PASTE_API_KEY",
            "authDomain": "PASTE_PROJECT_ID.firebaseapp.com",
            "projectId": "PASTE_PROJECT_ID",
            "storageBucket": "PASTE_PROJECT_ID.appspot.com",
            "messagingSenderId": "PASTE_MESSAGING_SENDER_ID",
            "appId": "PASTE_APP_ID"
        },
        "fcm_tokens": []
    }


def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        cfg = default_config()
        save_config(cfg)
        return cfg

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        base = default_config()
        base.update(cfg)
        return base
    except Exception:
        return default_config()


def save_config(cfg: dict) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


STATE: Dict[str, Any] = {
    "config": load_config(),
    "symbols_cache": {"ts": 0.0, "data": DEFAULT_SYMBOLS},
    "history_cache": {},
    "latest_buy_results": [],
    "latest_position_results": [],
    "alerts": [],
    "alert_dedupe": {},
    "last_scan_at": None,
    "last_scan_error": None,
    "scan_running": False,
    "scan_count": 0,
}

app = FastAPI(title=APP_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def now_str() -> str:
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")


def is_market_scan_time() -> bool:
    now = datetime.now(LOCAL_TZ)

    # Thứ 7 = 5, Chủ nhật = 6
    if now.weekday() >= 5:
        return False

    return MARKET_OPEN_TIME <= now.time() <= MARKET_CLOSE_TIME


def safe_get_json(url: str, params: Optional[dict] = None, timeout: int = 12) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://dstock.vndirect.com.vn/",
    }
    r = requests.get(url, params=params, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()


def get_symbols() -> List[str]:
    now = time.time()

    if now - STATE["symbols_cache"]["ts"] < 3600:
        return STATE["symbols_cache"]["data"]

    try:
        data = safe_get_json(
            FINFO_STOCKS_URL,
            params={"q": "status:LISTED", "size": 9999}
        )

        records = data.get("data", [])
        symbols = []

        for item in records:
            code = item.get("code") or item.get("symbol")
            if code:
                code = str(code).upper().strip()
                if code.isalnum() and 2 <= len(code) <= 5:
                    symbols.append(code)

        symbols = sorted(set(symbols))

        if symbols:
            STATE["symbols_cache"] = {"ts": now, "data": symbols}
            return symbols

    except Exception:
        pass

    STATE["symbols_cache"] = {"ts": now, "data": DEFAULT_SYMBOLS}
    return DEFAULT_SYMBOLS


def fetch_history(symbol: str, days: int = 500) -> pd.DataFrame:
    symbol = symbol.upper().strip()
    cache_key = f"{symbol}:{days}"
    now = time.time()

    cached = STATE["history_cache"].get(cache_key)
    if cached and now - cached["ts"] < 25:
        return cached["df"].copy()

    end = datetime.now(LOCAL_TZ)
    start = end - timedelta(days=days)

    params = {
        "resolution": "D",
        "symbol": symbol,
        "from": int(start.timestamp()),
        "to": int(end.timestamp()),
    }

    data = safe_get_json(DCHART_URL, params=params)

    if data.get("s") not in ("ok", "OK", None):
        return pd.DataFrame()

    t = data.get("t", [])
    c = data.get("c", [])
    o = data.get("o", [])
    h = data.get("h", [])
    l = data.get("l", [])
    v = data.get("v", [])

    if not t or not c:
        return pd.DataFrame()

    df = pd.DataFrame({
        "date": pd.to_datetime(t, unit="s"),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v,
    }).dropna().sort_values("date")

    STATE["history_cache"][cache_key] = {"ts": now, "df": df.copy()}
    return df


def to_float(x, default=None):
    try:
        if pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss.replace(0, math.nan)
    return 100 - (100 / (1 + rs))


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    o = df["open"]
    h = df["high"]
    l = df["low"]
    c = df["close"]
    v = df["volume"]

    # Candle structure
    df["range"] = (h - l).replace(0, pd.NA)
    df["body"] = (c - o).abs()
    df["green"] = c > o
    df["red"] = c < o
    df["upper_shadow"] = h - pd.concat([o, c], axis=1).max(axis=1)
    df["lower_shadow"] = pd.concat([o, c], axis=1).min(axis=1) - l
    df["body_pct"] = df["body"] / df["range"]
    df["close_pos"] = (c - l) / df["range"]
    df["avg_body20"] = df["body"].rolling(20).mean()

    # Volume
    df["vol20"] = v.rolling(20).mean()

    direction = c.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    df["obv"] = (direction * v).fillna(0).cumsum()
    df["obv_ma10"] = df["obv"].rolling(10).mean()

    typical = (h + l + c) / 3
    money_flow = typical * v

    pos_flow = money_flow.where(typical.diff() > 0, 0).rolling(14).sum()
    neg_flow = money_flow.where(typical.diff() < 0, 0).rolling(14).sum()

    mfr = pos_flow / neg_flow.replace(0, math.nan)
    df["mfi14"] = 100 - (100 / (1 + mfr))

    # Trend indicators
    for n in [5, 10, 20, 50, 100, 200]:
        df[f"sma{n}"] = c.rolling(n).mean()

    df["ema12"] = ema(c, 12)
    df["ema26"] = ema(c, 26)

    # MACD
    df["macd"] = df["ema12"] - df["ema26"]
    df["macd_signal"] = ema(df["macd"], 9)
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    # RSI
    df["rsi14"] = rsi(c, 14)

    # Bollinger Bands
    df["bb_mid"] = c.rolling(20).mean()
    bb_std = c.rolling(20).std()
    df["bb_upper"] = df["bb_mid"] + 2 * bb_std
    df["bb_lower"] = df["bb_mid"] - 2 * bb_std
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]

    # Stochastic
    low14 = l.rolling(14).min()
    high14 = h.rolling(14).max()
    df["stoch_k"] = 100 * (c - low14) / (high14 - low14).replace(0, math.nan)
    df["stoch_d"] = df["stoch_k"].rolling(3).mean()

    # ATR + ADX
    tr1 = h - l
    tr2 = (h - c.shift()).abs()
    tr3 = (l - c.shift()).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr14"] = tr.rolling(14).mean()

    up_move = h.diff()
    down_move = -l.diff()

    plus_dm = pd.Series(
        [up if up > down and up > 0 else 0 for up, down in zip(up_move, down_move)],
        index=df.index
    )

    minus_dm = pd.Series(
        [down if down > up and down > 0 else 0 for up, down in zip(up_move, down_move)],
        index=df.index
    )

    plus_di = 100 * plus_dm.rolling(14).sum() / tr.rolling(14).sum().replace(0, math.nan)
    minus_di = 100 * minus_dm.rolling(14).sum() / tr.rolling(14).sum().replace(0, math.nan)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, math.nan)

    df["plus_di"] = plus_di
    df["minus_di"] = minus_di
    df["adx14"] = dx.rolling(14).mean()

    # CCI
    tp_sma = typical.rolling(20).mean()
    mad = (typical - tp_sma).abs().rolling(20).mean()
    df["cci20"] = (typical - tp_sma) / (0.015 * mad.replace(0, math.nan))

    # Williams %R
    high14 = h.rolling(14).max()
    low14 = l.rolling(14).min()
    df["willr14"] = -100 * (high14 - c) / (high14 - low14).replace(0, math.nan)

    return df


def analyze_full_technical(symbol: str) -> dict:
    symbol = symbol.upper().strip()
    raw = fetch_history(symbol, days=500)

    if raw.empty or len(raw) < 80:
        return {
            "symbol": symbol,
            "ok": False,
            "error": "Không đủ dữ liệu."
        }

    df = compute_indicators(raw)

    latest = df.iloc[-1]
    prev = df.iloc[-2]

    price = to_float(latest["close"], 0)
    prev_close = to_float(prev["close"], 0)
    day_pct = (price / prev_close - 1) * 100 if prev_close else 0

    high20 = to_float(df["high"].iloc[-21:-1].max(), price)
    low20 = to_float(df["low"].iloc[-21:-1].min(), price)
    high10 = to_float(df["high"].iloc[-11:-1].max(), price)
    low10 = to_float(df["low"].iloc[-11:-1].min(), price)
    high50 = to_float(df["high"].iloc[-51:-1].max(), price)
    low50 = to_float(df["low"].iloc[-51:-1].min(), price)

    volume = to_float(latest["volume"], 0)
    vol20 = to_float(latest["vol20"], 0)
    vol_ratio = volume / vol20 if vol20 else 0

    sma20 = to_float(latest["sma20"])
    sma50 = to_float(latest["sma50"])
    sma100 = to_float(latest["sma100"])
    sma200 = to_float(latest["sma200"])

    ema12 = to_float(latest["ema12"])
    ema26 = to_float(latest["ema26"])

    rsi14 = to_float(latest["rsi14"])

    macd = to_float(latest["macd"])
    macd_signal = to_float(latest["macd_signal"])
    macd_hist = to_float(latest["macd_hist"])
    prev_macd_hist = to_float(prev["macd_hist"])

    bb_upper = to_float(latest["bb_upper"])
    bb_lower = to_float(latest["bb_lower"])
    bb_mid = to_float(latest["bb_mid"])
    bb_width = to_float(latest["bb_width"])

    stoch_k = to_float(latest["stoch_k"])
    stoch_d = to_float(latest["stoch_d"])

    adx14 = to_float(latest["adx14"])
    plus_di = to_float(latest["plus_di"])
    minus_di = to_float(latest["minus_di"])

    atr14 = to_float(latest["atr14"])

    obv = to_float(latest["obv"])
    obv_ma10 = to_float(latest["obv_ma10"])

    mfi14 = to_float(latest["mfi14"])
    cci20 = to_float(latest["cci20"])
    willr14 = to_float(latest["willr14"])

    green = bool(latest["green"])
    red = bool(latest["red"])

    body = to_float(latest["body"], 0)
    avg_body20 = to_float(latest["avg_body20"], 0)
    close_pos = to_float(latest["close_pos"], 0)
    body_pct = to_float(latest["body_pct"], 0)

    upper_shadow = to_float(latest["upper_shadow"], 0)
    lower_shadow = to_float(latest["lower_shadow"], 0)

    low = to_float(latest["low"], price)
    high = to_float(latest["high"], price)

    # BUY SCORE
    buy_score = 0
    buy_reasons = []

    if green and price > high20 and vol_ratio >= 1.4:
        buy_score += 28
        buy_reasons.append("Breakout đỉnh 20 phiên kèm volume")
    elif green and price > high10 and vol_ratio >= 1.25:
        buy_score += 18
        buy_reasons.append("Vượt đỉnh 10 phiên kèm volume")

    if green and avg_body20 and body >= avg_body20 * 1.4 and close_pos >= 0.68 and vol_ratio >= 1.3:
        buy_score += 14
        buy_reasons.append("Nến xanh mạnh, đóng gần cao nhất")

    if green and low <= low20 * 1.03 and close_pos >= 0.68 and lower_shadow >= max(body * 0.7, 0.01):
        buy_score += 10
        buy_reasons.append("Rút chân tại hỗ trợ")

    if vol_ratio >= 1.5:
        buy_score += 8
        buy_reasons.append("Volume tăng mạnh")
    elif vol_ratio >= 1.2:
        buy_score += 5
        buy_reasons.append("Volume trên trung bình")

    if sma20 and sma50 and price > sma20 > sma50:
        buy_score += 12
        buy_reasons.append("Giá trên SMA20/SMA50")

    if sma50 and sma100 and price > sma50 > sma100:
        buy_score += 6
        buy_reasons.append("Trend trung hạn tích cực")

    if ema12 and ema26 and ema12 > ema26:
        buy_score += 6
        buy_reasons.append("EMA12 trên EMA26")

    if rsi14 and 50 <= rsi14 <= 70:
        buy_score += 8
        buy_reasons.append("RSI trong vùng khỏe")
    elif rsi14 and 40 <= rsi14 < 50 and green:
        buy_score += 4
        buy_reasons.append("RSI hồi phục từ vùng yếu")

    if macd is not None and macd_signal is not None and macd > macd_signal:
        buy_score += 8
        buy_reasons.append("MACD trên signal")

    if macd_hist is not None and prev_macd_hist is not None and macd_hist > prev_macd_hist:
        buy_score += 4
        buy_reasons.append("MACD histogram cải thiện")

    if stoch_k and stoch_d and stoch_k > stoch_d and stoch_k < 85:
        buy_score += 6
        buy_reasons.append("Stochastic cắt lên")

    if adx14 and plus_di and minus_di and adx14 >= 18 and plus_di > minus_di:
        buy_score += 8
        buy_reasons.append("ADX xác nhận xu hướng tăng")

    if bb_upper and price > bb_upper and vol_ratio >= 1.2:
        buy_score += 6
        buy_reasons.append("Bứt lên Bollinger trên")
    elif bb_lower and low <= bb_lower and green and close_pos >= 0.65:
        buy_score += 5
        buy_reasons.append("Hồi từ Bollinger dưới")

    if obv and obv_ma10 and obv > obv_ma10:
        buy_score += 6
        buy_reasons.append("OBV ủng hộ dòng tiền")

    if mfi14 and 45 <= mfi14 <= 75:
        buy_score += 5
        buy_reasons.append("MFI dòng tiền ổn")

    if cci20 and cci20 > 0:
        buy_score += 4
        buy_reasons.append("CCI trên 0")

    if willr14 and willr14 > -50:
        buy_score += 4
        buy_reasons.append("Williams %R nghiêng về tăng")

    buy_score = min(100, buy_score)

    if buy_score >= 85:
        buy_signal = "ĐIỂM MUA MẠNH"
    elif buy_score >= 78:
        buy_signal = "ĐIỂM MUA ĐẸP"
    elif buy_score >= 65:
        buy_signal = "THEO DÕI MUA"
    else:
        buy_signal = "CHƯA CÓ ĐIỂM MUA"

    buy_stop_candidates = [
        low10,
        high20 * 0.985 if price > high20 else None,
        sma20 * 0.97 if sma20 else None,
        price - atr14 * 1.5 if atr14 else None
    ]

    buy_stop_candidates = [x for x in buy_stop_candidates if x and x < price]
    buy_stop = round(max(buy_stop_candidates) if buy_stop_candidates else price * 0.93, 2)

    buy_zone = None
    if buy_score >= 65:
        buy_zone = [round(min(price, high10), 2), round(price, 2)]

    # SELL SCORE
    sell_score = 0
    sell_reasons = []

    if red and price < low20 and vol_ratio >= 1.2:
        sell_score += 30
        sell_reasons.append("Thủng đáy 20 phiên kèm volume")
    elif red and price < low10 and vol_ratio >= 1.2:
        sell_score += 22
        sell_reasons.append("Thủng đáy 10 phiên kèm volume")

    if red and avg_body20 and body >= avg_body20 * 1.4 and close_pos <= 0.35 and vol_ratio >= 1.3:
        sell_score += 18
        sell_reasons.append("Nến đỏ phân phối")

    if upper_shadow >= max(body * 1.2, 0.01) and close_pos <= 0.45 and vol_ratio >= 1.3:
        sell_score += 12
        sell_reasons.append("Rút râu trên, có lực bán")

    if price < high20 and to_float(prev["close"], 0) > high20 and vol_ratio >= 1.1:
        sell_score += 14
        sell_reasons.append("Breakout thất bại")

    if sma20 and price < sma20:
        sell_score += 10
        sell_reasons.append("Giá dưới SMA20")

    if sma50 and price < sma50:
        sell_score += 12
        sell_reasons.append("Giá dưới SMA50")

    if ema12 and ema26 and ema12 < ema26:
        sell_score += 6
        sell_reasons.append("EMA12 dưới EMA26")

    if rsi14 and rsi14 < 45:
        sell_score += 8
        sell_reasons.append("RSI yếu")

    if rsi14 and rsi14 > 75 and red:
        sell_score += 6
        sell_reasons.append("RSI cao nhưng xuất hiện nến đỏ")

    if macd is not None and macd_signal is not None and macd < macd_signal:
        sell_score += 8
        sell_reasons.append("MACD dưới signal")

    if macd_hist is not None and prev_macd_hist is not None and macd_hist < prev_macd_hist:
        sell_score += 4
        sell_reasons.append("MACD histogram xấu đi")

    if stoch_k and stoch_d and stoch_k < stoch_d and stoch_k > 20:
        sell_score += 5
        sell_reasons.append("Stochastic cắt xuống")

    if adx14 and plus_di and minus_di and adx14 >= 18 and minus_di > plus_di:
        sell_score += 8
        sell_reasons.append("ADX nghiêng về xu hướng giảm")

    if bb_lower and price < bb_lower and vol_ratio >= 1.2:
        sell_score += 8
        sell_reasons.append("Thủng Bollinger dưới")

    if obv and obv_ma10 and obv < obv_ma10:
        sell_score += 6
        sell_reasons.append("OBV yếu")

    if mfi14 and mfi14 < 40:
        sell_score += 5
        sell_reasons.append("MFI yếu")

    if cci20 and cci20 < 0:
        sell_score += 4
        sell_reasons.append("CCI dưới 0")

    if willr14 and willr14 < -70:
        sell_score += 4
        sell_reasons.append("Williams %R yếu")

    sell_score = min(100, sell_score)

    if sell_score >= 85:
        sell_signal = "BÁN / CẮT LỖ MẠNH"
    elif sell_score >= 78:
        sell_signal = "BÁN / GIẢM TỶ TRỌNG"
    elif sell_score >= 65:
        sell_signal = "CẢNH BÁO BÁN"
    else:
        sell_signal = "CHƯA CÓ ĐIỂM BÁN"

    technical_stop_candidates = [
        low10,
        low20,
        sma20 * 0.97 if sma20 else None,
        price - atr14 * 1.5 if atr14 else None
    ]

    technical_stop_candidates = [x for x in technical_stop_candidates if x and x < price]
    technical_stop = round(max(technical_stop_candidates) if technical_stop_candidates else price * 0.93, 2)

    return {
        "symbol": symbol,
        "ok": True,
        "updated_at": now_str(),
        "price": round(price, 2),
        "open": round(to_float(latest["open"], 0), 2),
        "high": round(high, 2),
        "low": round(low, 2),
        "day_pct": round(day_pct, 2),
        "volume": int(volume),
        "vol_ratio": round(vol_ratio, 2),

        "high10": round(high10, 2),
        "low10": round(low10, 2),
        "high20": round(high20, 2),
        "low20": round(low20, 2),
        "high50": round(high50, 2),
        "low50": round(low50, 2),

        "body_pct": round(body_pct, 2) if body_pct is not None else None,
        "close_pos": round(close_pos, 2) if close_pos is not None else None,

        "indicators": {
            "sma20": round(sma20, 2) if sma20 else None,
            "sma50": round(sma50, 2) if sma50 else None,
            "sma100": round(sma100, 2) if sma100 else None,
            "sma200": round(sma200, 2) if sma200 else None,

            "ema12": round(ema12, 2) if ema12 else None,
            "ema26": round(ema26, 2) if ema26 else None,

            "rsi14": round(rsi14, 1) if rsi14 else None,

            "macd": round(macd, 3) if macd is not None else None,
            "macd_signal": round(macd_signal, 3) if macd_signal is not None else None,
            "macd_hist": round(macd_hist, 3) if macd_hist is not None else None,

            "bb_upper": round(bb_upper, 2) if bb_upper else None,
            "bb_mid": round(bb_mid, 2) if bb_mid else None,
            "bb_lower": round(bb_lower, 2) if bb_lower else None,
            "bb_width": round(bb_width, 3) if bb_width else None,

            "stoch_k": round(stoch_k, 1) if stoch_k else None,
            "stoch_d": round(stoch_d, 1) if stoch_d else None,

            "adx14": round(adx14, 1) if adx14 else None,
            "plus_di": round(plus_di, 1) if plus_di else None,
            "minus_di": round(minus_di, 1) if minus_di else None,

            "atr14": round(atr14, 2) if atr14 else None,
            "obv": round(obv, 0) if obv is not None else None,
            "mfi14": round(mfi14, 1) if mfi14 else None,
            "cci20": round(cci20, 1) if cci20 else None,
            "willr14": round(willr14, 1) if willr14 else None,
        },

        "buy_score": buy_score,
        "buy_signal": buy_signal,
        "buy_reasons": buy_reasons[:8],
        "buy_action": " | ".join(buy_reasons[:5]) if buy_reasons else "Chưa đủ xác nhận kỹ thuật để mua.",
        "buy_zone": buy_zone,
        "buy_stop": buy_stop,

        "sell_score": sell_score,
        "sell_signal": sell_signal,
        "sell_reasons": sell_reasons[:8],
        "sell_action": " | ".join(sell_reasons[:5]) if sell_reasons else "Chưa có tín hiệu bán kỹ thuật rõ.",
        "technical_stop": technical_stop,

        "vndirect_url": f"https://dstock.vndirect.com.vn/tong-quan/{symbol}",
    }


def position_decision(item: dict, position: dict) -> dict:
    buy_price = position.get("buy_price") or position.get("cost_price") or ""
    quantity = position.get("quantity") or ""
    manual_stop = position.get("manual_stop") or ""

    try:
        buy_price_f = float(buy_price) if buy_price not in ("", None) else None
    except Exception:
        buy_price_f = None

    try:
        manual_stop_f = float(manual_stop) if manual_stop not in ("", None) else None
    except Exception:
        manual_stop_f = None

    price = item.get("price", 0)
    pnl_pct = round((price / buy_price_f - 1) * 100, 2) if buy_price_f else None

    final_stop = manual_stop_f if manual_stop_f else item.get("technical_stop")

    force_sell = False
    reason = ""

    if final_stop and price <= final_stop:
        force_sell = True
        reason = f"Giá {price} đã chạm/cắt xuống stop {final_stop}."
    elif item.get("sell_score", 0) >= 78:
        force_sell = True
        reason = item.get("sell_action", "")

    status = "GIỮ / THEO DÕI"
    if force_sell:
        status = "BÁN / CẮT LỖ"

    out = dict(item)

    out.update({
        "position": {
            "buy_price": buy_price_f,
            "quantity": quantity,
            "manual_stop": manual_stop_f,
            "pnl_pct": pnl_pct,
            "final_stop": final_stop,
            "status": status,
            "reason": reason or item.get("sell_action", ""),
            "note": position.get("note", ""),
        }
    })

    return out


def init_firebase_admin() -> bool:
    cfg = STATE.get("config", {})

    if not cfg.get("firebase_enabled"):
        return False

    if firebase_admin is None or credentials is None:
        STATE["last_scan_error"] = "Chưa cài firebase-admin."
        return False

    try:
        if firebase_admin._apps:
            return True

        service_path = cfg.get("firebase_service_account_path", "serviceAccountKey.json")

        if not os.path.isabs(service_path):
            service_path = os.path.join(os.path.dirname(__file__), service_path)

        if not os.path.exists(service_path):
            STATE["last_scan_error"] = f"Không thấy file Firebase service account: {service_path}"
            return False

        cred = credentials.Certificate(service_path)
        firebase_admin.initialize_app(cred)
        return True

    except Exception as e:
        STATE["last_scan_error"] = f"Firebase init lỗi: {e}"
        return False


def send_fcm_push(title: str, body: str, data: Optional[dict] = None) -> None:
    cfg = STATE.get("config", {})

    if not cfg.get("firebase_enabled"):
        return

    tokens = list(dict.fromkeys(cfg.get("fcm_tokens", [])))

    if not tokens:
        return

    if not init_firebase_admin() or messaging is None:
        return

    clean_data = {
        str(k): str(v)
        for k, v in (data or {}).items()
        if v is not None
    }

    try:
        msg = messaging.MulticastMessage(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            data=clean_data,
            tokens=tokens,
        )

        try:
            res = messaging.send_each_for_multicast(msg)
            responses = res.responses
        except AttributeError:
            res = messaging.send_multicast(msg)
            responses = res.responses

        invalid = []

        for idx, r in enumerate(responses):
            if not r.success:
                err = str(r.exception)
                if "registration-token-not-registered" in err or "Requested entity was not found" in err:
                    invalid.append(tokens[idx])

        if invalid:
            cfg["fcm_tokens"] = [
                t for t in cfg.get("fcm_tokens", [])
                if t not in invalid
            ]

            STATE["config"] = cfg
            save_config(cfg)

    except Exception as e:
        STATE["last_scan_error"] = f"Gửi FCM lỗi: {e}"


def add_alert(kind: str, symbol: str, title: str, body: str, item: Optional[dict] = None) -> None:
    cfg = STATE["config"]
    cooldown = int(cfg.get("alert_cooldown_sec", 900))

    key = f"{kind}:{symbol}"
    now = time.time()
    last = STATE["alert_dedupe"].get(key, 0)

    if now - last < cooldown:
        return

    STATE["alert_dedupe"][key] = now

    alert = {
        "id": int(now * 1000),
        "kind": kind,
        "symbol": symbol,
        "title": title,
        "body": body,
        "created_at": now_str(),
        "item": item,
    }

    STATE["alerts"].append(alert)
    STATE["alerts"] = STATE["alerts"][-300:]

    send_fcm_push(
        title,
        body,
        {
            "kind": kind,
            "symbol": symbol,
            "url": "/"
        }
    )


def scan_buy_all(symbols: List[str], min_buy_score: int, max_workers: int) -> List[dict]:
    symbols = list(dict.fromkeys([
        s.upper().strip()
        for s in symbols
        if s.strip()
    ]))[:800]

    results = []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {
            pool.submit(analyze_full_technical, s): s
            for s in symbols
        }

        for fut in as_completed(future_map):
            try:
                item = fut.result()
                if item.get("ok"):
                    results.append(item)
            except Exception:
                pass

    results = sorted(
        results,
        key=lambda x: (
            x.get("buy_score", 0),
            x.get("vol_ratio", 0),
            x.get("day_pct", 0)
        ),
        reverse=True
    )

    for item in results:
        if item.get("buy_score", 0) >= min_buy_score:
            add_alert(
                "BUY",
                item["symbol"],
                f"{item['symbol']} có điểm mua kỹ thuật",
                f"{item['buy_signal']} {item['buy_score']}/100. Giá {item['price']}, stop {item['buy_stop']}.",
                item,
            )

    return [
        x for x in results
        if x.get("buy_score", 0) >= 50
    ][:200]


def scan_positions(positions: List[dict], min_sell_score: int, max_workers: int) -> List[dict]:
    positions = positions[:300]
    results = []

    def work(pos):
        symbol = str(pos.get("symbol", "")).upper().strip()
        item = analyze_full_technical(symbol)

        if item.get("ok"):
            return position_decision(item, pos)

        return item

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {
            pool.submit(work, p): p
            for p in positions
            if str(p.get("symbol", "")).strip()
        }

        for fut in as_completed(future_map):
            try:
                item = fut.result()
                if item.get("ok"):
                    results.append(item)
            except Exception:
                pass

    for item in results:
        pos = item.get("position", {})
        status = pos.get("status", "")
        sell_score = item.get("sell_score", 0)
        symbol = item.get("symbol", "")

        if status.startswith("BÁN") or sell_score >= min_sell_score:
            add_alert(
                "SELL",
                symbol,
                f"{symbol} có điểm bán/cắt lỗ",
                f"{item['sell_signal']} {sell_score}/100. Giá {item['price']}. Stop {pos.get('final_stop')}.",
                item,
            )

    return sorted(
        results,
        key=lambda x: (
            x.get("sell_score", 0),
            -x.get("day_pct", 0)
        ),
        reverse=True
    )


async def background_auto_scanner():
    await asyncio.sleep(2)

    while True:
        cfg = STATE["config"]
        interval = max(10, int(cfg.get("scan_interval_sec", 10)))

        if not cfg.get("auto_scan_enabled", True):
            STATE["last_scan_error"] = "Auto scan đang tắt."
            await asyncio.sleep(interval)
            continue

        # Chỉ auto scan trong giờ giao dịch VN.
        # Quét thủ công không bị giới hạn bởi đoạn này.
        if not is_market_scan_time():
            STATE["scan_running"] = False
            STATE["last_scan_error"] = (
                f"Ngoài giờ giao dịch VN "
                f"({MARKET_OPEN_TIME.strftime('%H:%M')} - {MARKET_CLOSE_TIME.strftime('%H:%M')}), "
                f"auto scan tạm dừng. Quét thủ công vẫn dùng được."
            )
            await asyncio.sleep(60)
            continue

        if STATE["scan_running"]:
            await asyncio.sleep(interval)
            continue

        STATE["scan_running"] = True
        STATE["last_scan_error"] = None

        try:
            # Nếu config.json có scan_symbols thì dùng danh sách đó.
            # Nếu scan_symbols rỗng [] thì tự lấy danh sách mã từ VNDIRECT.
            scan_symbols = cfg.get("scan_symbols") or get_symbols()
            scan_symbols = list(dict.fromkeys([
                str(s).upper().strip()
                for s in scan_symbols
                if str(s).strip()
            ]))[:200]

            buy_results = await asyncio.to_thread(
                scan_buy_all,
                scan_symbols,
                int(cfg.get("min_buy_score", 50)),
                int(cfg.get("max_workers", 8)),
            )

            pos_results = await asyncio.to_thread(
                scan_positions,
                cfg.get("positions", []),
                int(cfg.get("min_sell_score", 50)),
                int(cfg.get("max_workers", 8)),
            )

            STATE["latest_buy_results"] = buy_results
            STATE["latest_position_results"] = pos_results
            STATE["last_scan_at"] = now_str()
            STATE["scan_count"] += 1

        except Exception as e:
            STATE["last_scan_error"] = str(e)

        finally:
            STATE["scan_running"] = False

        await asyncio.sleep(interval)


@app.on_event("startup")
async def startup_event():
    init_firebase_admin()
    asyncio.create_task(background_auto_scanner())


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.head("/")
def head_index():
    return Response(status_code=200)


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(os.path.join(STATIC_DIR, "manifest.webmanifest"))


@app.get("/service-worker.js")
def sw():
    return FileResponse(os.path.join(STATIC_DIR, "service-worker.js"))


@app.get("/firebase-messaging-sw.js")
def firebase_messaging_sw():
    cfg = STATE["config"]
    firebase_config = cfg.get("firebase_web_config", {})

    # Bản này đã sửa lỗi:
    # "ServiceWorker script evaluation failed"
    # Nếu Firebase init lỗi, Service Worker vẫn không bị crash toàn bộ.
    js = f"""
try {{
  importScripts('https://www.gstatic.com/firebasejs/10.12.5/firebase-app-compat.js');
  importScripts('https://www.gstatic.com/firebasejs/10.12.5/firebase-messaging-compat.js');

  firebase.initializeApp({json.dumps(firebase_config)});

  const messaging = firebase.messaging();

  messaging.onBackgroundMessage((payload) => {{
    const notificationTitle =
      payload?.notification?.title || 'Stock Alert';

    const notificationOptions = {{
      body: payload?.notification?.body || '',
      icon: '/static/icon-192.png',
      badge: '/static/icon-192.png',
      data: payload?.data || {{}}
    }};

    self.registration.showNotification(notificationTitle, notificationOptions);
  }});
}} catch (err) {{
  console.error('firebase-messaging-sw init failed:', err);
}}

self.addEventListener('notificationclick', function(event) {{
  event.notification.close();

  event.waitUntil(
    clients.matchAll({{ type: 'window', includeUncontrolled: true }}).then(function(clientList) {{
      for (const client of clientList) {{
        if ('focus' in client) {{
          return client.focus();
        }}
      }}

      if (clients.openWindow) {{
        return clients.openWindow('/');
      }}
    }})
  );
}});
"""

    return Response(content=js, media_type="application/javascript")


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "app": APP_NAME,
        "timezone": APP_TIMEZONE,
        "time": now_str(),
        "market_scan_time": is_market_scan_time(),
        "market_open": MARKET_OPEN_TIME.strftime("%H:%M"),
        "market_close": MARKET_CLOSE_TIME.strftime("%H:%M"),
        "scan_running": STATE["scan_running"],
        "last_scan_at": STATE["last_scan_at"],
        "last_scan_error": STATE["last_scan_error"],
        "scan_count": STATE["scan_count"],
    }


@app.get("/api/config")
def api_get_config():
    return STATE["config"]


@app.post("/api/config")
def api_set_config(payload: dict = Body(...)):
    cfg = STATE["config"]

    allowed = [
        "auto_scan_enabled",
        "scan_interval_sec",
        "min_buy_score",
        "min_sell_score",
        "scan_symbols",
        "max_workers",
        "alert_cooldown_sec",
        "positions",
        "firebase_enabled",
        "firebase_service_account_path",
        "firebase_vapid_key",
        "firebase_web_config",
        "fcm_tokens",
    ]

    for k in allowed:
        if k in payload:
            cfg[k] = payload[k]

    cfg["scan_interval_sec"] = max(10, int(cfg.get("scan_interval_sec", 10)))
    cfg["min_buy_score"] = int(cfg.get("min_buy_score", 78))
    cfg["min_sell_score"] = int(cfg.get("min_sell_score", 78))
    cfg["max_workers"] = max(2, min(20, int(cfg.get("max_workers", 8))))

    if isinstance(cfg.get("scan_symbols"), str):
        cfg["scan_symbols"] = [
            x.strip().upper()
            for x in cfg["scan_symbols"].replace("\n", ",").split(",")
            if x.strip()
        ]

    cfg["scan_symbols"] = list(dict.fromkeys([
        str(s).upper().strip()
        for s in cfg.get("scan_symbols", [])
        if str(s).strip()
    ]))

    STATE["config"] = cfg
    save_config(cfg)
    return cfg


@app.get("/api/latest")
def api_latest():
    return {
        "last_scan_at": STATE["last_scan_at"],
        "scan_running": STATE["scan_running"],
        "last_scan_error": STATE["last_scan_error"],
        "scan_count": STATE["scan_count"],
        "market_scan_time": is_market_scan_time(),
        "buy_results": STATE["latest_buy_results"],
        "position_results": STATE["latest_position_results"],
    }


@app.get("/api/alerts")
def api_alerts(since_id: int = Query(default=0)):
    alerts = [
        a for a in STATE["alerts"]
        if int(a.get("id", 0)) > since_id
    ]

    return {
        "count": len(alerts),
        "alerts": alerts,
        "latest_id": max(
            [int(a.get("id", 0)) for a in STATE["alerts"]] or [since_id]
        ),
    }


@app.post("/api/alerts/clear")
def api_clear_alerts():
    STATE["alerts"] = []
    STATE["alert_dedupe"] = {}
    return {"ok": True}


@app.get("/api/analyze/{symbol}")
def api_analyze(symbol: str):
    return analyze_full_technical(symbol)


@app.post("/api/scan-now")
def api_scan_now():
    """
    Quét thủ công.
    Hàm này KHÔNG kiểm tra giờ giao dịch.
    Nghĩa là giờ nào bấm quét thủ công cũng được.
    """
    cfg = STATE["config"]

    # Nếu config.json có scan_symbols thì dùng danh sách đó.
    # Nếu scan_symbols rỗng [] thì tự lấy danh sách mã từ VNDIRECT.
    scan_symbols = cfg.get("scan_symbols") or get_symbols()
    scan_symbols = list(dict.fromkeys([
        str(s).upper().strip()
        for s in scan_symbols
        if str(s).strip()
    ]))[:200]

    buy_results = scan_buy_all(
        scan_symbols,
        int(cfg.get("min_buy_score", 50)),
        int(cfg.get("max_workers", 8)),
    )

    pos_results = scan_positions(
        cfg.get("positions", []),
        int(cfg.get("min_sell_score", 50)),
        int(cfg.get("max_workers", 8)),
    )

    STATE["latest_buy_results"] = buy_results
    STATE["latest_position_results"] = pos_results
    STATE["last_scan_at"] = now_str()
    STATE["scan_count"] += 1
    STATE["last_scan_error"] = None

    return {
        "ok": True,
        "manual_scan": True,
        "time": now_str(),
        "scan_symbols_count": len(scan_symbols),
        "buy_results": buy_results,
        "position_results": pos_results,
    }


@app.get("/api/positions")
def api_get_positions():
    return {"positions": STATE["config"].get("positions", [])}


@app.post("/api/positions")
def api_set_positions(positions: list = Body(...)):
    cfg = STATE["config"]
    clean = []

    for p in positions:
        symbol = str(p.get("symbol", "")).upper().strip()

        if not symbol:
            continue

        clean.append({
            "symbol": symbol,
            "buy_price": p.get("buy_price", ""),
            "quantity": p.get("quantity", ""),
            "buy_date": p.get("buy_date", ""),
            "manual_stop": p.get("manual_stop", ""),
            "note": p.get("note", ""),
        })

    cfg["positions"] = clean
    STATE["config"] = cfg
    save_config(cfg)

    return {"positions": clean}


@app.get("/api/fcm/config")
def api_fcm_config():
    cfg = STATE["config"]

    return {
        "enabled": bool(cfg.get("firebase_enabled")),
        "firebaseConfig": cfg.get("firebase_web_config", {}),
        "vapidKey": cfg.get("firebase_vapid_key", ""),
    }


@app.post("/api/fcm/register")
def api_fcm_register(payload: dict = Body(...)):
    token = str(payload.get("token", "")).strip()

    if not token:
        return {
            "ok": False,
            "error": "Missing token"
        }

    cfg = STATE["config"]

    tokens = list(dict.fromkeys(
        cfg.get("fcm_tokens", []) + [token]
    ))

    cfg["fcm_tokens"] = tokens[-2000:]
    STATE["config"] = cfg
    save_config(cfg)

    return {
        "ok": True,
        "token_count": len(cfg["fcm_tokens"])
    }


@app.post("/api/fcm/test")
def api_fcm_test():
    send_fcm_push(
        "Test Stock Alert",
        "Nếu thấy thông báo này thì Firebase Push đã hoạt động.",
        {
            "kind": "TEST",
            "url": "/"
        }
    )

    return {"ok": True}