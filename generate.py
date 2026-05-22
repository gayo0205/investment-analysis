#!/usr/bin/env python3
"""
智慧投資分析自動報告產生器 v2
- 資料來源：Yahoo Finance（完全免費）
- 分析方式：規則化技術指標判斷（無任何 AI API）
- 新增：買賣建議、定期定額建議、52週位置、成交量比、均線排列等
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from html import escape
from zoneinfo import ZoneInfo
import json
import os
import warnings
import time
from data_sources import fetch_finmind_public_stock_data, fetch_tw_etf_public_data, fetch_twse_stock_day_all
warnings.filterwarnings('ignore')

CACHE_DIR = Path(os.environ.get('YF_CACHE_DIR') or (Path(__file__).resolve().parent / '.yf_cache_runtime'))
CACHE_DIR.mkdir(exist_ok=True)
yf.set_tz_cache_location(str(CACHE_DIR))

TWSE_DAILY_QUOTES = None

# =============================================================
# ★ 設定區 — 可自行修改追蹤清單
# =============================================================

TW_STOCKS = {
    '2330.TW': '台積電',
    '2317.TW': '鴻海',
    '2379.TW': '瑞昱',
    '2454.TW': '聯發科',
    '2303.TW': '聯電',
    '2408.TW': '南亞科',
    '2882.TW': '國泰金',
    '2449.TW': '京元電子',
}

TW_ETFS = {
    '0050.TW':   '元大台灣50',
    '006204.TW': '永豐臺灣加權',
    '0056.TW':   '元大高股息',
    '00878.TW':  '國泰永續高股息',
    '00929.TW':  '復華台灣科技優息',
    '006208.TW': '富邦台50',
    '00919.TW':  '群益台灣精選高息',
    '0052.TW':   '富邦科技',
    '00904.TW':  '新光臺灣半導體30',
    '00881.TW':  '國泰台灣5G+',
    '00940.TW':  '元大台灣價值高息',
    '00646.TW':  '元大S&P500',
    '00662.TW':  '富邦NASDAQ',
}

US_STOCKS = {
    'NVDA': 'NVIDIA',
    'AAPL': 'Apple',
    'TSLA': 'Tesla',
    'MSFT': 'Microsoft',
    'AMD':  'AMD',
}

US_ETFS = {
    'SPY':  '標普500 ETF',
    'QQQ':  '那斯達克100 ETF',
    'SOXX': '費城半導體 ETF',
    'IWM':  '羅素2000 ETF',
    'XLK':  '科技類股 ETF',
}

BONDS = {
    'TLT': '20年期美債 ETF',
    'IEF': '7-10年期美債 ETF',
    'HYG': '高收益債 ETF',
    'GLD': '黃金 ETF',
    'USO': '原油 ETF',
    'DBC': '多元商品 ETF',
}

INDICES = {
    '^TWII': ('加權指數',   False),
    '^GSPC': ('標普500',    False),
    '^SOX':  ('費城半導體', False),
    '^VIX':  ('VIX恐慌',   True),
    '^IXIC': ('那斯達克',   False),
    '^N225': ('日經225',    False),
}

TW_LIMIT_MARKETS = set(
    list(TW_STOCKS.keys()) +
    list(TW_ETFS.keys()) +
    [tk for tk in BONDS.keys() if tk.endswith('.TW')]
)

# =============================================================
# ★ 公開範例設定 — 只用來示範試算，不代表任何人的真實資產
# =============================================================

PUBLIC_EXAMPLE_PLAN = {
# The site only uses sample values. Personal budget, positions, and broker data must stay outside the repo.
    'monthly_budget': 5000,

    # 台股定期定額常見有 1 元優惠；若你的券商不是，改成 False。
    'use_tw_dca_flat_fee': True,
    'tw_dca_flat_fee': 1,

    # 一般電子下單估算。不同券商折扣不同，可自己調整。
    'broker_fee_rate': 0.001425,
    'broker_fee_discount': 0.6,
    'regular_min_fee': 20,
    'sell_fee_discount': 0.6,

    # 台股賣出交易稅：ETF 0.1%，股票 0.3%。
    'tw_etf_sell_tax': 0.001,
    'tw_stock_sell_tax': 0.003,

    # 手續費占投入金額超過這個比例，就提醒小額下單不划算。
    'max_reasonable_fee_pct': 0.2,
    'amount_step': 100,
    'min_tw_dca_amount': 1000,
    'backtest_years': 3,
}


ETF_PROFILES = {
    '0050.TW':   dict(role='長期核心', bucket='台股核心', base_ratio=0.55, note='台灣大型權值股核心，適合長期定期定額；0050/006208 擇一即可。'),
    '006208.TW': dict(role='長期核心', bucket='台股核心', base_ratio=0.55, note='追蹤台灣50，常被拿來和0050比較；核心ETF通常擇一即可。'),
    '006204.TW': dict(role='長期核心', bucket='台股全市場', base_ratio=0.45, note='更接近整體台股加權指數，但要留意成交量與折溢價。'),
    '0056.TW':   dict(role='現金流', bucket='高股息', base_ratio=0.25, note='高股息ETF，重點不是殖利率高低，而是總報酬與配息來源。'),
    '00878.TW':  dict(role='現金流', bucket='高股息', base_ratio=0.25, note='高股息與ESG題材，適合現金流配置，不宜只因配息高而重壓。'),
    '00919.TW':  dict(role='現金流', bucket='高股息', base_ratio=0.20, note='高股息ETF，需觀察成分股輪動、配息穩定性與收益平準金。'),
    '00929.TW':  dict(role='現金流', bucket='高股息科技', base_ratio=0.15, note='科技優息，波動可能比傳統高股息更接近科技股。'),
    '00940.TW':  dict(role='現金流', bucket='高股息', base_ratio=0.15, note='價值高息型，需觀察長期總報酬與換股成本。'),
    '0052.TW':   dict(role='衛星主題', bucket='台股科技', base_ratio=0.15, note='台股科技主題，適合作為衛星配置，不建議取代核心。'),
    '00881.TW':  dict(role='衛星主題', bucket='5G/科技', base_ratio=0.12, note='科技主題ETF，適合關注AI與通訊供應鏈，但要控制比例。'),
    '00904.TW':  dict(role='衛星主題', bucket='半導體', base_ratio=0.12, note='半導體主題，景氣循環與AI熱度會讓波動放大。'),
    '00646.TW':  dict(role='海外分散', bucket='美股大型股', base_ratio=0.20, note='台幣買美股S&P500概念，仍要看匯率、內扣費用與追蹤誤差。'),
    '00662.TW':  dict(role='海外分散', bucket='美股科技', base_ratio=0.12, note='NASDAQ主題，成長性高但波動也高。'),
    '00679B.TW': dict(role='防守配置', bucket='美債長天期', base_ratio=0.15, note='長天期債券ETF，降息有利但升息時價格會受傷。'),
    '00720B.TW': dict(role='防守配置', bucket='投資級債', base_ratio=0.10, note='投資級公司債，需留意利率與信用利差。'),
    '00751B.TW': dict(role='防守配置', bucket='高評級債', base_ratio=0.10, note='高評級公司債，適合降低波動，但不是保本。'),
}


NEWS_THEMES = [
    dict(theme='AI/半導體', words=['AI', '半導體', 'HPC', '伺服器', 'HBM', 'ASIC'], watch=['0052.TW', '00904.TW', '00881.TW', '2330.TW', '2317.TW', '2454.TW', '2449.TW']),
    dict(theme='電力/重電/儲能', words=['電力', '重電', '儲能', '電網', '變壓器'], watch=['2308.TW']),
    dict(theme='疫情/醫療防疫', words=['疫情', '口罩', '疫苗', '檢測', '醫療'], watch=[]),
    dict(theme='通膨/升息', words=['通膨', '升息', '殖利率', 'Fed'], watch=['2882.TW', '00679B.TW', '00720B.TW']),
    dict(theme='降息/景氣放緩', words=['降息', '景氣放緩', '衰退'], watch=['00679B.TW', '00720B.TW', '00751B.TW']),
    dict(theme='航運/物流', words=['航運', '塞港', '運價', '物流'], watch=[]),
]

BENCHMARK_LABELS = {
    '^TWII': '加權指數',
    '^GSPC': '標普500',
    '^IXIC': '那斯達克',
    '^SOX': '費城半導體',
}

TRACKING_RULES = {
    '006204.TW': dict(benchmark='^TWII', mode='tracking', label='簡易追蹤差', hint='追蹤加權指數的粗估，非發行商正式數字'),
    '0050.TW': dict(benchmark='^TWII', mode='reference', label='大盤對照偏離', hint='0050正式追蹤台灣50，這裡用加權指數做大方向對照'),
    '006208.TW': dict(benchmark='^TWII', mode='reference', label='大盤對照偏離', hint='006208正式追蹤台灣50，這裡用加權指數做大方向對照'),
    'SPY': dict(benchmark='^GSPC', mode='tracking', label='簡易追蹤差', hint='對標普500的粗估，非發行商正式數字'),
    'SOXX': dict(benchmark='^SOX', mode='tracking', label='簡易追蹤差', hint='對費城半導體指數的粗估，非發行商正式數字'),
    'QQQ': dict(benchmark='^IXIC', mode='reference', label='指數對照偏離', hint='QQQ追蹤NASDAQ100，這裡用那斯達克綜合指數近似參考'),
}

TRACKING_UNAVAILABLE_REASONS = {
    '0056.TW': '高股息ETF有自己的追蹤指數，不能直接拿加權指數當追蹤差。',
    '00878.TW': '高股息/ESG ETF有自己的追蹤指數，資料不足時先不硬算追蹤差。',
    '00919.TW': '高股息ETF有自己的選股規則，資料不足時先不硬算追蹤差。',
    '00929.TW': '科技優息ETF有自己的追蹤指數，資料不足時先不硬算追蹤差。',
    '00940.TW': '高股息ETF有自己的追蹤指數，資料不足時先不硬算追蹤差。',
    '0052.TW': '科技主題ETF不能直接拿費城半導體當正式追蹤差。',
    '00904.TW': '半導體主題ETF不能直接拿費城半導體當正式追蹤差。',
    '00881.TW': '5G/科技ETF不能直接拿費城半導體當正式追蹤差。',
    '00646.TW': '台幣海外ETF會混入匯率影響，不能直接跟美元指數算追蹤差。',
    '00662.TW': '台幣NASDAQ ETF會混入匯率影響，不能直接跟美元指數算追蹤差。',
    'XLK': '產業ETF需要對應產業正式指數，先不拿那斯達克替代。',
    'IWM': 'IWM正式追蹤Russell 2000，目前未抓Russell 2000指數。',
}

ETF_BENCHMARKS = {ticker: rule['benchmark'] for ticker, rule in TRACKING_RULES.items()}

SECTOR_LABELS = {
    'technology': '科技',
    'financial_services': '金融',
    'communication_services': '通訊服務',
    'consumer_cyclical': '非必需消費',
    'consumer_defensive': '民生消費',
    'industrials': '工業',
    'healthcare': '醫療',
    'energy': '能源',
    'utilities': '公用事業',
    'realestate': '不動產',
    'basic_materials': '原物料',
}

DCA_SIM_TICKERS = {
    '0050.TW': '元大台灣50',
    '006208.TW': '富邦台50',
    '0056.TW': '元大高股息',
    '00878.TW': '國泰永續高股息',
    '00646.TW': '元大S&P500',
    '00662.TW': '富邦NASDAQ',
    'SPY': '標普500 ETF',
    'QQQ': '那斯達克100 ETF',
}

BENCHMARK_SERIES = {}
DCA_SERIES = {}
BUY_NOW_DATA = {}
META_CACHE = {}

# =============================================================
# 技術指標計算
# =============================================================

def calc_indicators(df):
    c = df['Close'].squeeze()
    h = df['High'].squeeze()
    l = df['Low'].squeeze()
    v = df['Volume'].squeeze() if 'Volume' in df.columns else pd.Series(dtype=float)
    res = {}

    res['ma5']  = c.rolling(5).mean()
    res['ma20'] = c.rolling(20).mean()
    res['ma60'] = c.rolling(60).mean()

    l9  = l.rolling(9).min()
    h9  = h.rolling(9).max()
    dif = (h9 - l9).replace(0, np.nan)
    rsv = (c - l9) / dif * 100
    k   = rsv.ewm(com=2, adjust=False).mean()
    d   = k.ewm(com=2, adjust=False).mean()
    res['k'] = k
    res['d'] = d

    delta = c.diff()
    gain  = delta.where(delta > 0, 0).rolling(14).mean()
    loss  = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs    = gain / loss.replace(0, np.nan)
    res['rsi'] = 100 - (100 / (1 + rs))

    e12  = c.ewm(span=12, adjust=False).mean()
    e26  = c.ewm(span=26, adjust=False).mean()
    macd = e12 - e26
    msig = macd.ewm(span=9, adjust=False).mean()
    res['macd']     = macd
    res['macd_sig'] = msig

    m20 = c.rolling(20).mean()
    s20 = c.rolling(20).std()
    res['boll_u'] = m20 + 2 * s20
    res['boll_l'] = m20 - 2 * s20

    res['dev20'] = (c - res['ma20']) / res['ma20'].replace(0, np.nan) * 100

    tr  = pd.concat([(h-l), (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    dmp = h.diff().clip(lower=0)
    dmm = (-l.diff()).clip(lower=0)
    msk = dmp >= dmm
    dmp = dmp.where(msk, 0)
    dmm = dmm.where(~msk, 0)
    atr  = tr.rolling(14).mean().replace(0, np.nan)
    dip  = 100 * dmp.rolling(14).mean() / atr
    dim  = 100 * dmm.rolling(14).mean() / atr
    dx   = 100 * (dip - dim).abs() / (dip + dim).replace(0, np.nan)
    res['adx'] = dx.rolling(14).mean()
    res['dip'] = dip
    res['dim'] = dim

    if len(v.dropna()) >= 20:
        avg_v = v.rolling(20).mean()
        res['vol_ratio'] = v / avg_v.replace(0, np.nan)
    else:
        res['vol_ratio'] = pd.Series(dtype=float)

    return res, c


def gl(s):
    v = s.dropna()
    return float(v.iloc[-1]) if len(v) else float('nan')


def is_tw_ticker(ticker):
    return ticker.endswith('.TW')


def is_etf_like(ticker):
    return ticker in TW_ETFS or ticker in US_ETFS or ticker in BONDS or ticker in ETF_PROFILES


def money(n):
    if n is None:
        return 'N/A'
    return f'{int(round(n)):,.0f}'


def h(value):
    return escape(str(value), quote=True)


def safe_num(value):
    try:
        if value is None:
            return None
        v = float(value)
        if np.isnan(v) or np.isinf(v):
            return None
        return v
    except Exception:
        return None


def fmt_ratio_pct(value, digits=1):
    v = safe_num(value)
    if v is None:
        return 'N/A'
    return f'{v * 100:.{digits}f}%'


def normalize_yield_pct(value):
    v = safe_num(value)
    if v is None or v < 0:
        return None
    if v > 25:
        return None
    if v > 1:
        return v
    if v > 0.20:
        return v
    return v * 100


def fmt_yield_pct(info):
    y = normalize_yield_pct(info.get('yield'))
    if y is not None:
        return f'{y:.1f}%'
    dy = normalize_yield_pct(info.get('dividendYield'))
    if dy is None:
        return 'N/A'
    return f'{dy:.1f}%'


def dividend_yield_value(info):
    y = normalize_yield_pct(info.get('yield'))
    if y is not None:
        return y
    return normalize_yield_pct(info.get('dividendYield'))


def fmt_expense_pct(value):
    v = safe_num(value)
    if v is None:
        return 'N/A'
    return f'{v:.2f}%'


def fmt_optional_pct(value, digits=2, signed=False):
    v = safe_num(value)
    if v is None:
        return 'N/A'
    sign = '+' if signed and v >= 0 else ''
    return f'{sign}{v:.{digits}f}%'


def short_label(value, limit=18):
    text = str(value or '').strip()
    if not text:
        return 'N/A'
    return text if len(text) <= limit else text[:limit - 1] + '…'


def fmt_plain_num(value, digits=1):
    v = safe_num(value)
    if v is None:
        return 'N/A'
    return f'{v:.{digits}f}'


def build_price_basis(close_series, latest_price, adj_close_series=None):
    """Return a price series on today's price scale, avoiding split artifacts."""
    raw = close_series.dropna() if close_series is not None else pd.Series(dtype=float)
    if raw.empty:
        return raw, '市場收盤價', False

    split_adjusted = raw.copy()
    split_fixed = False
    values = raw.astype(float)
    for idx in range(len(values) - 1, 0, -1):
        prev = safe_num(values.iloc[idx - 1])
        curr = safe_num(values.iloc[idx])
        if prev is None or curr is None or prev <= 0 or curr <= 0:
            continue
        ratio = prev / curr
        if ratio > 1.8 or ratio < 0.55:
            factor = curr / prev
            split_adjusted.iloc[:idx] = split_adjusted.iloc[:idx] * factor
            split_fixed = True
    if split_fixed:
        return split_adjusted.dropna(), '分割還原價格', True

    if adj_close_series is None:
        return raw, '市場收盤價', False
    adj = adj_close_series.dropna()
    if adj.empty:
        return raw, '市場收盤價', False
    joined = pd.concat([raw, adj], axis=1, join='inner').dropna()
    if len(joined) < 20:
        return raw, '市場收盤價', False

    raw_tail = raw.tail(min(756, len(raw)))
    ratio = (joined.iloc[:, 0] / joined.iloc[:, 1].replace(0, np.nan)).dropna()
    ratio_tail = ratio.tail(min(756, len(ratio)))
    ratio_shift = (
        ratio_tail.max() / ratio_tail.min()
        if len(ratio_tail) and ratio_tail.min() and ratio_tail.min() > 0
        else 1
    )
    last = safe_num(latest_price) or safe_num(raw.iloc[-1])
    raw_high = safe_num(raw_tail.max())
    raw_low = safe_num(raw_tail.min())
    looks_split = (
        last is not None and last > 0 and raw_high is not None and raw_low is not None
        and (raw_high > last * 1.8 or raw_low < last * 0.45 or ratio_shift > 1.35)
    )
    if not looks_split:
        return raw, '市場收盤價', False

    adj_last = safe_num(adj.iloc[-1])
    if adj_last is None or adj_last <= 0 or last is None:
        return raw, '市場收盤價', False
    scaled = adj * (last / adj_last)
    return scaled.dropna(), '還原價格', True


def scale_to_latest_price(series, latest_price):
    s = series.dropna() if series is not None else pd.Series(dtype=float)
    if s.empty:
        return pd.Series(dtype=float)
    latest = safe_num(latest_price)
    last = safe_num(s.iloc[-1])
    if latest is None or latest <= 0 or last is None or last <= 0:
        return pd.Series(dtype=float)
    return s.astype(float) * (latest / last)


def apply_price_basis_to_ohlc(df, basis_close):
    if df is None or basis_close is None or basis_close.empty or 'Close' not in df.columns:
        return df
    out = df.copy()
    raw_close = out['Close'].squeeze()
    factor = (basis_close / raw_close.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)
    for col in ['Open', 'High', 'Low', 'Close']:
        if col in out.columns:
            out[col] = out[col].squeeze().mul(factor, axis=0)
    return out


def fmt_compact(value):
    v = safe_num(value)
    if v is None:
        return 'N/A'
    units = [(1_000_000_000_000, '兆'), (100_000_000, '億'), (10_000, '萬')]
    for base, label in units:
        if abs(v) >= base:
            return f'{v / base:.1f}{label}'
    return f'{v:,.0f}'


def fmt_net_lots(value):
    v = safe_num(value)
    if v is None:
        return 'N/A'
    lots = abs(v) / 1000
    if abs(v) < 1:
        return '持平'
    side = '買超' if v > 0 else '賣超'
    return f'{side} {lots:,.0f}張'


def pct_return(series, lookback=756):
    if series is None:
        return None
    try:
        c = series.dropna()
        if len(c) < 40:
            return None
        c = c.tail(min(lookback, len(c)))
        first = safe_num(c.iloc[0])
        last = safe_num(c.iloc[-1])
        if first is None or first <= 0 or last is None:
            return None
        return round((last / first - 1) * 100, 1)
    except Exception:
        return None


def index_to_date(idx):
    try:
        return pd.Timestamp(idx).date()
    except Exception:
        return None


def exchange_clock(ticker):
    if is_tw_ticker(ticker):
        tz = ZoneInfo('Asia/Taipei')
        open_time = dt_time(9, 0)
        close_time = dt_time(13, 30)
        tz_label = '台灣時間'
    else:
        tz = ZoneInfo('America/New_York')
        open_time = dt_time(9, 30)
        close_time = dt_time(16, 0)
        tz_label = '美東時間'

    now = datetime.now(tz)
    if now.weekday() >= 5:
        status = '休市日'
    elif now.time() < open_time:
        status = '尚未開盤'
    elif now.time() <= close_time:
        status = '開盤中，價格可能延遲'
    else:
        status = '已收盤'
    return dict(date=now.date(), status=status, tz_label=tz_label)


def format_quote_time(meta):
    ts = meta.get('quote_time') if meta else None
    if ts is None:
        return ''
    try:
        tz_name = meta.get('quote_timezone') or 'UTC'
        source_tz = ZoneInfo(tz_name)
        tw_tz = ZoneInfo('Asia/Taipei')
        labels = {
            'Asia/Taipei': '台灣',
            'America/New_York': '美東',
            'America/Chicago': '美中',
            'Asia/Tokyo': '日本',
        }
        source_dt = datetime.fromtimestamp(ts, source_tz)
        tw_dt = source_dt.astimezone(tw_tz)
        tw_text = '台灣 ' + tw_dt.strftime('%m/%d %H:%M')
        if tz_name == 'Asia/Taipei':
            return tw_text
        label = labels.get(tz_name, tz_name)
        return f'{tw_text}（{label} {source_dt.strftime("%m/%d %H:%M")}）'
    except Exception:
        return ''


def quote_datetime(meta):
    ts = meta.get('quote_time') if meta else None
    if ts is None:
        return None
    try:
        tz_name = meta.get('quote_timezone') or 'UTC'
        return datetime.fromtimestamp(ts, ZoneInfo(tz_name))
    except Exception:
        return None


def market_state_label(state):
    labels = {
        'REGULAR': '開盤中',
        'PRE': '盤前',
        'POST': '盤後',
        'CLOSED': '已收盤',
        'PREPRE': '非交易時段',
        'POSTPOST': '非交易時段',
    }
    return labels.get(state or '', state or '狀態未知')


def previous_weekday(day):
    prev = day - timedelta(days=1)
    while prev.weekday() >= 5:
        prev = prev - timedelta(days=1)
    return prev


def ohlc_context(df, ticker, quote=None):
    if df is None or df.empty or 'Close' not in df.columns:
        return None
    quote = quote or {}
    rows = pd.DataFrame({
        'Open': df['Open'].squeeze() if 'Open' in df.columns else pd.Series(dtype=float),
        'High': df['High'].squeeze() if 'High' in df.columns else pd.Series(dtype=float),
        'Low': df['Low'].squeeze() if 'Low' in df.columns else pd.Series(dtype=float),
        'Close': df['Close'].squeeze(),
    })
    rows = rows.dropna(how='all', subset=['Open', 'High', 'Low', 'Close'])
    if rows.empty:
        return None

    qdt = quote_datetime(quote)
    quote_date = qdt.date() if qdt else None
    latest_idx = rows.index[-1]
    latest_date = index_to_date(latest_idx)
    session_date = quote_date or latest_date

    session_row = None
    session_idx = None
    for idx, row in rows.iterrows():
        if index_to_date(idx) == session_date:
            session_row = row
            session_idx = idx
            break
    if session_row is None:
        if quote_date and quote.get('quote_price') is not None:
            session_idx = pd.Timestamp(session_date)
            session_row = pd.Series({
                'Open': quote.get('quote_open'),
                'High': quote.get('quote_day_high'),
                'Low': quote.get('quote_day_low'),
                'Close': quote.get('quote_price'),
            })
        else:
            session_idx = latest_idx
            session_row = rows.iloc[-1]
            session_date = latest_date

    latest_close = quote.get('quote_price') or safe_num(session_row.get('Close'))
    session_open = quote.get('quote_open') or safe_num(session_row.get('Open'))
    session_high = quote.get('quote_day_high') or safe_num(session_row.get('High'))
    session_low = quote.get('quote_day_low') or safe_num(session_row.get('Low'))

    close_rows = rows.dropna(subset=['Close'])
    previous_close = quote.get('quote_previous_close')
    previous_date = None
    if previous_close is None and not close_rows.empty:
        before = []
        for idx, row in close_rows.iterrows():
            d = index_to_date(idx)
            if session_date is None or d < session_date:
                before.append((idx, row))
        if before:
            previous_idx, previous_row = before[-1]
            previous_close = safe_num(previous_row.get('Close'))
            previous_date = index_to_date(previous_idx)
    elif not close_rows.empty:
        before_dates = [index_to_date(idx) for idx in close_rows.index if session_date is None or index_to_date(idx) < session_date]
        previous_date = before_dates[-1] if before_dates else None

    if latest_close is None:
        return None
    if previous_close is None:
        previous_close = latest_close

    clock = exchange_clock(ticker)
    state = quote.get('market_state')
    state_text = market_state_label(state)
    quote_note = quote.get('quote_source') or 'Yahoo Finance'
    quote_time_text = format_quote_time(quote)
    freshness_note = f'資料屬於 {session_date.isoformat() if session_date else "N/A"} 交易日'

    if state == 'REGULAR':
        price_label = '盤中最新價'
    elif quote.get('quote_price') is not None:
        price_label = '最新成交/收盤'
    else:
        price_label = '最新收盤'

    open_note = '資料日開盤價已取得' if session_open is not None else '資料日開盤價暫無資料'

    return dict(
        latest_date=session_date.isoformat() if session_date else 'N/A',
        latest_close=latest_close,
        previous_date=previous_date.isoformat() if previous_date else None,
        previous_close=previous_close,
        reference_date=previous_date.isoformat() if previous_date else None,
        reference_close=previous_close,
        reference_label='資料日昨收',
        reference_is_current=True,
        freshness_note=freshness_note,
        today_open=session_open,
        session_open=session_open,
        session_high=session_high,
        session_low=session_low,
        quote_time_text=quote_time_text,
        has_today_row=session_date == clock['date'],
        price_label=price_label,
        market_status=state_text,
        current_market_status=clock['status'],
        tz_label=clock['tz_label'],
        open_note=open_note,
        quote_note=quote_note,
        data_source_priority=quote.get('data_source_priority'),
        twse_volume=quote.get('twse_volume'),
        twse_trade_value=quote.get('twse_trade_value'),
        twse_transaction=quote.get('twse_transaction'),
    )


def round_amount(amount, step=None):
    step = step or PUBLIC_EXAMPLE_PLAN['amount_step']
    if amount <= 0:
        return 0
    return int(round(amount / step) * step)


def tw_tick_size(price):
    if price < 10:
        return 0.01
    if price < 50:
        return 0.05
    if price < 100:
        return 0.10
    if price < 500:
        return 0.50
    if price < 1000:
        return 1.00
    return 5.00


def round_to_tick(price, direction):
    tick = tw_tick_size(price)
    scaled = price / tick
    if direction == 'up':
        return np.floor(scaled) * tick
    if direction == 'down':
        return np.ceil(scaled) * tick
    return round(scaled) * tick


def get_profile(ticker):
    if ticker in ETF_PROFILES:
        return ETF_PROFILES[ticker]
    if ticker in TW_ETFS or ticker in US_ETFS or ticker in BONDS:
        return dict(role='觀察ETF', bucket='ETF', base_ratio=0.05, note='尚未設定細分類，先當觀察標的。')
    return dict(role='個股觀察', bucket='股票', base_ratio=0.0, note='新手不建議直接用定期定額重壓個股，先從ETF建立核心部位。')


def fetch_metadata(ticker, last_price=None):
    """抓公開基本資料。失敗就回空 dict，避免公開頁硬湊假資料。"""
    if ticker in META_CACHE:
        return META_CACHE[ticker]

    info = {}
    try:
        info = yf.Ticker(ticker).get_info() or {}
    except Exception as ex:
        print(f'    {ticker} 基本資料略過：{ex}')

    price = safe_num(info.get('regularMarketPrice')) or safe_num(last_price)
    nav = safe_num(info.get('navPrice'))
    premium = None
    if price is not None and nav is not None and nav > 0:
        premium = (price - nav) / nav * 100

    top_holdings = []
    top10_weight = None
    top1_weight = None
    top_sector = None
    holdings_source = None
    if is_etf_like(ticker):
        try:
            fd = yf.Ticker(ticker).funds_data
            holdings = getattr(fd, 'top_holdings', None)
            if isinstance(holdings, pd.DataFrame) and not holdings.empty and 'Holding Percent' in holdings.columns:
                rows = holdings.reset_index().head(10)
                weights = []
                for _, row in rows.iterrows():
                    pct = safe_num(row.get('Holding Percent'))
                    if pct is None or pct <= 0:
                        continue
                    weights.append(pct)
                    symbol = str(row.get('Symbol', '')).strip()
                    top_holdings.append(dict(
                        symbol=symbol,
                        name=str(row.get('Name', '')).strip(),
                        weight=pct * 100,
                    ))
                if weights:
                    top10_weight = sum(weights) * 100
                    top1_weight = max(weights) * 100
                    holdings_source = 'Yahoo funds_data'
            sectors = getattr(fd, 'sector_weightings', None)
            if isinstance(sectors, dict) and sectors:
                key, val = max(sectors.items(), key=lambda kv: safe_num(kv[1]) or 0)
                weight = safe_num(val)
                if weight is not None and weight > 0:
                    top_sector = dict(
                        key=key,
                        label=SECTOR_LABELS.get(key, key),
                        weight=weight * 100,
                    )
        except Exception as ex:
            print(f'    {ticker} ETF持股資料略過：{ex}')

    meta = dict(
        raw=info,
        quote_type=info.get('quoteType'),
        category=info.get('category'),
        fund_family=info.get('fundFamily'),
        trailing_pe=safe_num(info.get('trailingPE')),
        forward_pe=safe_num(info.get('forwardPE')),
        pb=safe_num(info.get('priceToBook')),
        roe=safe_num(info.get('returnOnEquity')),
        gross_margin=safe_num(info.get('grossMargins')),
        profit_margin=safe_num(info.get('profitMargins')),
        revenue_growth=safe_num(info.get('revenueGrowth')),
        earnings_growth=safe_num(info.get('earningsGrowth')),
        debt_to_equity=safe_num(info.get('debtToEquity')),
        market_cap=safe_num(info.get('marketCap')),
        dividend_yield=dividend_yield_value(info),
        expense_ratio=safe_num(info.get('netExpenseRatio')),
        nav_price=nav,
        premium_discount=premium,
        total_assets=safe_num(info.get('totalAssets')),
        nav_3m_return=safe_num(info.get('trailingThreeMonthNavReturns')),
        quote_price=safe_num(info.get('regularMarketPrice')),
        quote_previous_close=safe_num(info.get('regularMarketPreviousClose') or info.get('previousClose')),
        quote_open=safe_num(info.get('regularMarketOpen') or info.get('open')),
        quote_day_high=safe_num(info.get('regularMarketDayHigh') or info.get('dayHigh')),
        quote_day_low=safe_num(info.get('regularMarketDayLow') or info.get('dayLow')),
        quote_time=safe_num(info.get('regularMarketTime')),
        quote_timezone=info.get('exchangeTimezoneName'),
        market_state=info.get('marketState'),
        quote_source=info.get('quoteSourceName') or ('Yahoo Quote' if safe_num(info.get('regularMarketPrice')) is not None else None),
        top_holdings=top_holdings,
        top10_weight=top10_weight,
        top1_weight=top1_weight,
        top_sector=top_sector,
        holdings_source=holdings_source,
        info_available=bool(info),
    )
    META_CACHE[ticker] = meta
    return meta


def fetch_twse_daily_quote(ticker):
    global TWSE_DAILY_QUOTES
    if not is_tw_ticker(ticker):
        return {}
    if TWSE_DAILY_QUOTES is None:
        TWSE_DAILY_QUOTES = fetch_twse_stock_day_all()
    code = ticker.replace('.TW', '')
    return dict(TWSE_DAILY_QUOTES.get(code, {}))


def apply_finmind_metadata(ticker, meta):
    """補台股個股的免費基本面/籌碼資料；ETF 不混用個股模型。"""
    if not is_tw_ticker(ticker) or is_etf_like(ticker):
        return meta

    code = ticker.replace('.TW', '')
    finmind = fetch_finmind_public_stock_data(code)
    if not finmind:
        return meta

    merged = dict(meta)
    merged.update(finmind)

    if finmind.get('finmind_per') is not None:
        merged['trailing_pe'] = finmind['finmind_per']
    if finmind.get('finmind_pbr') is not None:
        merged['pb'] = finmind['finmind_pbr']
    if finmind.get('finmind_dividend_yield') is not None:
        merged['dividend_yield'] = finmind['finmind_dividend_yield']
    if finmind.get('finmind_ttm_revenue_yoy') is not None:
        merged['revenue_growth'] = finmind['finmind_ttm_revenue_yoy']
    elif finmind.get('finmind_month_revenue_yoy') is not None and merged.get('revenue_growth') is None:
        merged['revenue_growth'] = finmind['finmind_month_revenue_yoy']
    if finmind.get('finmind_eps_yoy') is not None:
        merged['earnings_growth'] = finmind['finmind_eps_yoy']
    if finmind.get('finmind_gross_margin') is not None:
        merged['gross_margin'] = finmind['finmind_gross_margin']
    return merged


def apply_tw_etf_metadata(ticker, meta, last_price=None):
    """補台股 ETF 的公開專用資料，避免把 ETF 當個股看。"""
    if ticker not in TW_ETFS:
        return meta
    code = ticker.replace('.TW', '')
    official = fetch_tw_etf_public_data(code)
    if not official:
        return meta

    merged = dict(meta)
    merged.update(official)

    nav = safe_num(official.get('official_nav'))
    price = safe_num(merged.get('quote_price')) or safe_num(last_price)
    if nav is not None and nav > 0:
        merged['nav_price'] = nav
        if price is not None:
            premium = (price - nav) / nav * 100
            merged['premium_discount'] = premium
            merged['official_premium_discount'] = premium

    if official.get('official_fee_annualized_estimate') is not None:
        merged['official_fee_annualized_estimate'] = official['official_fee_annualized_estimate']
    div_12m = safe_num(official.get('official_dividend_12m_amount'))
    if div_12m is not None and price is not None and price > 0:
        merged['official_dividend_yield_12m'] = div_12m / price * 100
    return merged


def should_merge_twse_quote(ticker, twse_quote):
    if not twse_quote:
        return False, ''
    clock = exchange_clock(ticker)
    if clock['status'] == '開盤中，價格可能延遲':
        return False, '盤中優先使用 Yahoo 最新/延遲報價；TWSE STOCK_DAY_ALL 是盤後正式日資料，不覆蓋盤中價。'
    return True, ''


def fetch_public_metadata(ticker, last_price=None):
    meta = apply_finmind_metadata(ticker, fetch_metadata(ticker, last_price))
    twse_quote = fetch_twse_daily_quote(ticker)
    if twse_quote:
        quote_price = safe_num(twse_quote.get('quote_price'))
        ref_price = safe_num(last_price)
        if quote_price is not None and ref_price is not None and ref_price > 0:
            ratio = quote_price / ref_price
            if ratio < 0.5 or ratio > 1.5:
                skipped = dict(meta)
                skipped['twse_validation_note'] = f'TWSE價格尺度與Yahoo日線差異過大，暫不合併（ratio {ratio:.2f}）'
                return skipped
        should_merge, note = should_merge_twse_quote(ticker, twse_quote)
        if not should_merge:
            skipped = dict(meta)
            skipped['twse_reference_date'] = twse_quote.get('twse_date')
            skipped['twse_reference_close'] = twse_quote.get('quote_price')
            skipped['twse_validation_note'] = note
            skipped['data_source_priority'] = '盤中Yahoo報價優先，TWSE盤後日資料保留為正式收盤參考'
            return apply_tw_etf_metadata(ticker, skipped, last_price)
        merged = dict(meta)
        merged.update(twse_quote)
        merged['data_source_priority'] = 'TWSE盤後資料優先，Yahoo補基本面/歷史資料'
        return apply_tw_etf_metadata(ticker, merged, merged.get('quote_price') or last_price)
    return apply_tw_etf_metadata(ticker, meta, last_price)


def calc_buy_fee(amount, ticker):
    if amount <= 0:
        return 0
    if is_tw_ticker(ticker) and PUBLIC_EXAMPLE_PLAN['use_tw_dca_flat_fee']:
        return PUBLIC_EXAMPLE_PLAN['tw_dca_flat_fee']
    fee = amount * PUBLIC_EXAMPLE_PLAN['broker_fee_rate'] * PUBLIC_EXAMPLE_PLAN['broker_fee_discount']
    return int(max(PUBLIC_EXAMPLE_PLAN['regular_min_fee'], round(fee)))


def calc_sell_cost(amount, ticker):
    if amount <= 0:
        return 0, 0
    fee = amount * PUBLIC_EXAMPLE_PLAN['broker_fee_rate'] * PUBLIC_EXAMPLE_PLAN['sell_fee_discount']
    fee = int(max(PUBLIC_EXAMPLE_PLAN['regular_min_fee'], round(fee)))
    if is_etf_like(ticker):
        tax = amount * PUBLIC_EXAMPLE_PLAN['tw_etf_sell_tax']
    else:
        tax = amount * PUBLIC_EXAMPLE_PLAN['tw_stock_sell_tax']
    return fee, int(round(tax))


def fee_label(amount, fee):
    if amount <= 0:
        return '未估'
    pct = fee / amount * 100
    if pct <= PUBLIC_EXAMPLE_PLAN['max_reasonable_fee_pct']:
        return f'划算（{pct:.2f}%）'
    if pct <= 0.8:
        return f'尚可（{pct:.2f}%）'
    return f'偏貴（{pct:.2f}%）'


def calc_drawdown(close_series):
    c = close_series.dropna()
    if len(c) < 30:
        return None, None
    peak = c.cummax()
    dd = (c / peak - 1) * 100
    return round(float(dd.min()), 1), round(float(dd.iloc[-1]), 1)


def calc_beta(close_series, ticker):
    bench_key = 'TW' if is_tw_ticker(ticker) else 'US'
    bench = BENCHMARK_SERIES.get(bench_key)
    if bench is None or len(bench.dropna()) < 80:
        return None
    try:
        own = close_series.dropna().pct_change()
        ref = bench.dropna().pct_change()
        joined = pd.concat([own, ref], axis=1, join='inner').dropna().tail(252)
        if len(joined) < 60:
            return None
        var = float(joined.iloc[:, 1].var())
        if var == 0 or np.isnan(var):
            return None
        beta = float(joined.iloc[:, 0].cov(joined.iloc[:, 1]) / var)
        return round(beta, 2)
    except Exception:
        return None


def calc_tracking_error(return_series, ticker):
    rule = TRACKING_RULES.get(ticker)
    if not rule:
        reason = TRACKING_UNAVAILABLE_REASONS.get(ticker, '正式追蹤指數資料不足，先不硬算。')
        return None, None, '正式追蹤差', reason, 'unavailable'
    bench_key = rule.get('benchmark')
    bench = BENCHMARK_SERIES.get(bench_key)
    label = rule.get('label') or '簡易追蹤差'
    hint = rule.get('hint') or '非發行商正式追蹤誤差'
    mode = rule.get('mode') or 'reference'
    if bench_key is None or bench is None or return_series is None:
        return None, BENCHMARK_LABELS.get(bench_key, bench_key), label, '對照指數資料不足，先不硬算。', 'unavailable'
    try:
        own = return_series.dropna().pct_change()
        ref = bench.dropna().pct_change()
        joined = pd.concat([own, ref], axis=1, join='inner').dropna().tail(252)
        if len(joined) < 60:
            return None, BENCHMARK_LABELS.get(bench_key, bench_key), label, '可比資料不足，先不硬算。', 'unavailable'
        active = joined.iloc[:, 0] - joined.iloc[:, 1]
        te = float(active.std()) * (252 ** 0.5) * 100
        if np.isnan(te) or np.isinf(te):
            return None, BENCHMARK_LABELS.get(bench_key, bench_key), label, '資料異常，先不硬算。', 'unavailable'
        return round(te, 2), BENCHMARK_LABELS.get(bench_key, bench_key), label, hint, mode
    except Exception:
        return None, BENCHMARK_LABELS.get(bench_key, bench_key), label, '資料暫時無法可靠對照，先不硬算。', 'unavailable'


def _dca_points(series):
    c = series.dropna() if series is not None else pd.Series(dtype=float)
    if len(c) < 60:
        return []
    return [
        [idx.strftime('%Y-%m-%d'), round(float(px), 4)]
        for idx, px in c.tail(2520).items()
        if float(px) > 0
    ]


def store_dca_series(ticker, name, close_series, total_return_series=None, basis_note='市場收盤價'):
    if ticker not in DCA_SIM_TICKERS:
        return
    points = _dca_points(close_series)
    if points:
        payload = dict(name=name, points=points, basis=basis_note)
        total_points = _dca_points(total_return_series)
        if total_points and len(total_points) >= max(60, int(len(points) * 0.7)):
            payload['total_points'] = total_points
            payload['total_basis'] = '含息估算（Yahoo Adj Close）'
        DCA_SERIES[ticker] = payload


def confidence_text(score):
    if score >= 75:
        return '高'
    if score >= 60:
        return '中'
    if score >= 45:
        return '偏低'
    return '低'


def confidence_reason(conf, notes):
    missing = [n for n in notes if ('待補' in n or '不足' in n)]
    if not missing:
        return '資料較完整，價格、成交量、技術與風險資料都有可用；但這仍不是保證獲利，只是輔助判斷。'

    missing_text = '、'.join(missing[:3])
    if conf >= 60:
        lead = '可以當方向參考'
    elif conf >= 45:
        lead = '只能當輔助觀察'
    else:
        lead = '暫時不要把它當主要買賣依據'
    return f'{lead}，因為目前{missing_text}。'


def data_quality_note(ticker, a):
    fs = a.get('factor_scores', {})
    conf = fs.get('confidence', 50)
    notes = list(a.get('data_notes', []))
    if not notes:
        notes = ['價格、成交量與風險資料可用']
    return dict(
        confidence=confidence_text(conf),
        reason=confidence_reason(conf, notes),
        notes='；'.join(notes[:6])
    )


def stock_basic_phrase(meta):
    parts = []
    if meta.get('trailing_pe') is not None:
        parts.append(f'PE {meta["trailing_pe"]:.1f}')
    if meta.get('revenue_growth') is not None:
        parts.append(f'營收成長 {meta["revenue_growth"] * 100:.1f}%')
    if meta.get('earnings_growth') is not None:
        parts.append(f'EPS/獲利成長 {meta["earnings_growth"] * 100:.1f}%')
    if meta.get('roe') is not None:
        parts.append(f'ROE {meta["roe"] * 100:.1f}%')
    if not parts:
        return '基本面資料不足'
    return '、'.join(parts[:4])


def etf_basic_phrase(meta):
    parts = []
    if meta.get('expense_ratio') is not None:
        parts.append(f'費用率 {meta["expense_ratio"]:.2f}%')
    if meta.get('premium_discount') is not None:
        parts.append(f'折溢價 {meta["premium_discount"]:+.2f}%')
    if meta.get('dividend_yield') is not None:
        parts.append(f'殖利率 {meta["dividend_yield"]:.1f}%')
    if meta.get('total_assets') is not None:
        parts.append(f'規模 {fmt_compact(meta["total_assets"])}')
    if meta.get('top10_weight') is not None:
        parts.append(f'前十大 {meta["top10_weight"]:.1f}%')
    if not parts:
        return 'ETF專用資料不足'
    return '、'.join(parts[:4])


def buy_now_ratio(ticker, a, ext):
    profile = get_profile(ticker)
    score = a['score']
    fs = a.get('factor_scores', {})
    risk = fs.get('risk', 50)
    w_pct = ext['w_pct']

    if profile['role'] == '個股觀察':
        ratio = 0.10 if score >= 55 else 0.05 if score >= 45 else 0.0
        cap = 0.15
    elif profile['role'] == '長期核心':
        ratio = 0.35
        cap = 0.50
    elif profile['role'] in ['現金流', '海外分散', '防守配置']:
        ratio = 0.20
        cap = 0.30
    elif profile['role'] == '衛星主題':
        ratio = 0.15
        cap = 0.25
    else:
        ratio = 0.08
        cap = 0.15

    if score < 45:
        ratio *= 0.35
    elif score >= 75 and w_pct < 75:
        ratio *= 1.15

    if w_pct >= 92:
        ratio *= 0.45
    elif w_pct >= 82:
        ratio *= 0.65
    elif w_pct <= 30 and score >= 55:
        ratio *= 1.20

    if risk < 45:
        ratio *= 0.60

    ratio = max(0, min(cap, ratio))
    if 0 < ratio < 0.05:
        ratio = 0.05
    return round(ratio, 2)


def decision_texts(ticker, a, ext, rec, plan=None):
    profile = get_profile(ticker)
    is_etf = is_etf_like(ticker)
    meta = a.get('meta') or {}
    score = a['score']
    w_pct = ext['w_pct']
    ma = ext['ma_align']
    risk = a.get('factor_scores', {}).get('risk', 50)
    max_dd = ext.get('max_drawdown')
    dd_text = f'，歷史最大回撤約 {max_dd:.0f}%' if max_dd is not None else ''

    if is_etf:
        dca_txt, _, dca_reason = rec['dca']
        if risk < 45:
            conclusion = '風險偏高，只能小額觀察'
        elif w_pct >= 85:
            conclusion = '長期可持續扣款，但不追高加碼'
        elif score >= 70:
            conclusion = '可分批投入，仍保留現金'
        elif score < 45:
            conclusion = '先降低加碼衝動，等趨勢回穩'
        else:
            conclusion = dca_txt
        reason = f'價格位階在 52 週的 {w_pct:.0f}%，趨勢為「{ma}」，風險分數 {risk}{dd_text}。ETF資料：{etf_basic_phrase(meta)}。{dca_reason}'
        counter = '如果大盤跌破季線、ETF折溢價異常、費用率偏高、配息來源不穩或成分股過度集中，這個判斷要降級。'
        if plan and plan.get('amount', 0) > 0:
            action = f'定期定額照計畫，本月示範投入約 {money(plan["amount"])} 元；臨時想買請看本卡買進區間，並用上方「今天想買」分批估算。'
        else:
            action = '本月不加碼，先保留現金；臨時想買要看本卡買進區間，只用小比例試單。'
    else:
        trade_txt, _, trade_reason = rec['trade']
        if score >= 70:
            conclusion = '可小比例觀察，不建議重壓個股'
        elif score < 45:
            conclusion = '新手先不要追，等風險降低'
        else:
            conclusion = trade_txt
        reason = f'技術分數 {score}，價格位階 {w_pct:.0f}%，趨勢為「{ma}」{dd_text}。基本面：{stock_basic_phrase(meta)}。{trade_reason}'
        counter = '如果營收連續轉弱、EPS下修、毛利率下降、Forward PE轉差或跌破季線，這個觀察要立刻降級。'
        action = '新手先把個股當觀察清單；真的想買，先看本卡買進區間，用小比例分批，不要影響核心 ETF 部位。'

    return dict(conclusion=conclusion, reason=reason, counter=counter, action=action)


def store_buy_now_data(ticker, name, price, a, ext, rec, plan, zone=None):
    decision = decision_texts(ticker, a, ext, rec, plan)
    quality = data_quality_note(ticker, a)
    BUY_NOW_DATA[ticker] = dict(
        name=name,
        price=round(float(price), 4),
        ratio=buy_now_ratio(ticker, a, ext),
        score=a['score'],
        role=get_profile(ticker)['role'],
        bucket=get_profile(ticker)['bucket'],
        is_tw=is_tw_ticker(ticker),
        is_etf=is_etf_like(ticker),
        risk_score=a.get('factor_scores', {}).get('risk'),
        w_pct=round(float(ext.get('w_pct', 50)), 1),
        confidence=quality['confidence'],
        data_note=quality['reason'],
        data_items=quality['notes'],
        conclusion=decision['conclusion'],
        reason=decision['reason'],
        counter=decision['counter'],
        action=decision['action'],
        price_zone=zone or {},
    )


def simple_backtest(close_series, monthly_amount, ticker, years=None):
    years = years or PUBLIC_EXAMPLE_PLAN['backtest_years']
    c = close_series.dropna()
    if monthly_amount <= 0 or len(c) < 60:
        return None
    try:
        cutoff = c.index[-1] - pd.DateOffset(years=years)
        c = c[c.index >= cutoff]
    except Exception:
        c = c.tail(252 * years)
    if len(c) < 40:
        return None

    monthly_prices = c.groupby([c.index.year, c.index.month]).first()
    if len(monthly_prices) < 6:
        return None

    shares = 0
    fees = 0
    invested = 0
    for px in monthly_prices:
        fee = calc_buy_fee(monthly_amount, ticker)
        net = max(0, monthly_amount - fee)
        # 台股定期定額實務上以金額申購、分配股數；這裡用整股估算，對小白比較保守。
        units = int(net // float(px))
        shares += units
        fees += fee
        invested += monthly_amount

    current_value = shares * float(c.iloc[-1])
    pnl = current_value - invested
    roi = (pnl / invested * 100) if invested else 0
    return dict(
        months=len(monthly_prices),
        invested=invested,
        value=current_value,
        pnl=pnl,
        roi=roi,
        shares=shares,
        fees=fees,
    )


def calc_investment_plan(ticker, price, a, ext, hist_close):
    profile = get_profile(ticker)
    monthly_budget = PUBLIC_EXAMPLE_PLAN['monthly_budget']
    base_amount = monthly_budget * profile['base_ratio']
    rsi = a['rsi']
    w_pct = ext['w_pct']
    ma_align = ext['ma_align']
    risk_score = a.get('factor_scores', {}).get('risk', 50)
    reasons = []
    factor = 1.0

    if profile['role'] == '個股觀察':
        factor = 0
        reasons.append('新手階段先用ETF建立核心，個股只列觀察。')
    else:
        if risk_score < 45:
            factor = 0
            reasons.append('風險分數偏低，本月不加碼。')
        if w_pct >= 90:
            factor *= 0.5
            reasons.append('接近52週高點，本月不追高加碼。')
        elif w_pct >= 75:
            factor *= 0.75
            reasons.append('價格偏高，降低扣款比例。')
        elif w_pct <= 30 and ma_align != '空頭排列':
            factor *= 1.25
            reasons.append('價格相對低位，可小幅加碼。')

        if rsi is not None and rsi >= 75:
            factor *= 0.5
            reasons.append('RSI過熱，再降低投入。')
        elif rsi is not None and rsi >= 70:
            factor *= 0.75
            reasons.append('RSI偏熱，避免追高。')

        if ma_align == '空頭排列':
            factor = min(factor, 0.5)
            reasons.append('趨勢偏空，只保留小額觀察。')

    amount = round_amount(base_amount * factor)
    if amount > 0 and amount < PUBLIC_EXAMPLE_PLAN['min_tw_dca_amount'] and is_tw_ticker(ticker):
        amount = PUBLIC_EXAMPLE_PLAN['min_tw_dca_amount']
    if amount > monthly_budget:
        amount = monthly_budget

    if not reasons:
        reasons.append('價格與趨勢未出現極端訊號，按計畫扣款。')

    if not is_tw_ticker(ticker):
        return dict(
            ticker=ticker, is_tw=False,
            profile=profile, amount=0, fee=0, fee_text='海外標的需另設匯率/複委託費',
            shares=0, sell_fee=0, sell_tax=0, reasons=reasons, backtest=None
        )

    fee = calc_buy_fee(amount, ticker) if amount else 0
    shares = int(max(0, (amount - fee) // price)) if amount and price > 0 else 0
    sell_fee, sell_tax = calc_sell_cost(amount, ticker)
    backtest_amount = amount if amount > 0 else PUBLIC_EXAMPLE_PLAN['min_tw_dca_amount']
    backtest = simple_backtest(hist_close, backtest_amount, ticker)

    return dict(
        ticker=ticker,
        is_tw=True,
        profile=profile,
        amount=amount,
        fee=fee,
        fee_text=fee_label(amount, fee),
        shares=shares,
        sell_fee=sell_fee if amount else 0,
        sell_tax=sell_tax if amount else 0,
        reasons=reasons[:2],
        backtest=backtest,
    )


def zone_price(ticker, value):
    v = safe_num(value)
    if v is None or v <= 0:
        return None
    if is_tw_ticker(ticker):
        return round(float(round_to_tick(v, 'nearest')), 2)
    return round(float(v), 2)


def fmt_zone_price(value):
    v = safe_num(value)
    if v is None:
        return 'N/A'
    return f'{v:,.2f}'


def share_text(shares, ticker):
    try:
        n = int(shares or 0)
    except Exception:
        n = 0
    if n <= 0:
        return '0 股'
    if is_tw_ticker(ticker):
        lots = n / 1000
        if n < 1000:
            return f'{n:,} 股（零股，約 {lots:.3f} 張）'
        odd = n % 1000
        lot_text = f'{n // 1000:,} 張' if odd == 0 else f'{n // 1000:,} 張 + {odd:,} 股零股'
        return f'{n:,} 股（{lot_text}）'
    return f'{n:,} 股'


def calc_price_zones(ticker, price, a, ext):
    px = safe_num(price)
    ma20 = safe_num(ext.get('ma20'))
    ma60 = safe_num(ext.get('ma60'))
    ma240 = safe_num(ext.get('ma240'))
    if px is None or ma20 is None or ma60 is None:
        return None

    profile = get_profile(ticker)
    role = profile.get('role', '')
    trend = ext.get('ma_align') or '盤整中'
    w_pct = safe_num(ext.get('w_pct'))
    if w_pct is None:
        w_pct = 50
    risk_score = a.get('factor_scores', {}).get('risk', 50)
    is_core = role == '長期核心'

    if trend == '空頭排列':
        stop_raw = ma60 * 0.94
        lower_anchor = ma240 if ma240 is not None else safe_num(ext.get('w_low')) or ma60
        buy_low_raw = lower_anchor * 0.98
        buy_high_raw = ma60 * 0.98
        chase_raw = max(ma20 * 1.02, buy_high_raw)
        trend_note = '空頭排列時，便宜不等於安全，先等站回區間再動作。'
    else:
        stop_raw = ma60 * 0.94
        buy_low_raw = ma60 * 0.98
        buy_high_raw = ma20 * (1.04 if is_core else 1.03)
        chase_raw = max(buy_high_raw, ma20 * (1.09 if is_core else 1.07))
        trend_note = '用60日線抓中期支撐，用20日線避免短線追太高。'

    buy_low_raw, buy_high_raw = sorted([buy_low_raw, buy_high_raw])
    if ma240 is not None and trend != '多頭排列':
        buy_low_raw = min(buy_low_raw, ma240 * 1.02)

    stop_line = zone_price(ticker, stop_raw)
    buy_low = zone_price(ticker, buy_low_raw)
    buy_high = zone_price(ticker, buy_high_raw)
    chase_limit = zone_price(ticker, chase_raw)

    if not all(v is not None for v in [stop_line, buy_low, buy_high, chase_limit]):
        return None
    if buy_low > buy_high:
        buy_low, buy_high = buy_high, buy_low
    if stop_line >= buy_low:
        stop_line = zone_price(ticker, buy_low * 0.96)
    if chase_limit <= buy_high:
        chase_limit = zone_price(ticker, buy_high * 1.03)

    if px > chase_limit:
        status = '高於追高上限'
        summary = '定期定額可照計畫，但臨時單筆先不要追；等回到可分批區再加碼。'
        action = '正常扣款可以，額外資金先留著。'
        tone = 'hot'
    elif px < stop_line:
        status = '跌破保護線'
        summary = '不要因為看起來便宜就硬接，先確認不是基本面或大盤轉弱。'
        action = '等站回保護線或基本面沒有惡化，再小額分批。'
        tone = 'danger'
    elif px < buy_low:
        status = '加碼觀察區'
        summary = '價格低於主要分批區，長期ETF可慢慢加；個股要先看營收與獲利有沒有壞掉。'
        action = '核心ETF可小幅加碼，個股只小比例試單。'
        tone = 'cool'
    elif px <= buy_high:
        status = '可分批區'
        summary = '不是保證會漲，而是相對不像在高檔一次追價。'
        action = '想買可以拆 3-6 批；新手不要一次買完。'
        tone = 'ok'
    else:
        status = '偏高等待區'
        summary = '價格已離可分批區較遠，不代表永遠不能買，而是不要臨時加碼追高。'
        action = '定期定額照扣；單筆等回落或只買很小一部分。'
        tone = 'warm'

    if is_core and px > buy_high and px <= chase_limit:
        action = '核心ETF可照常扣款；額外加碼等回到區間內。'
    if risk_score < 40:
        summary += ' 風險分數偏低時，所有買進區間都要再保守。'

    range_text = f'{fmt_zone_price(buy_low)} ~ {fmt_zone_price(buy_high)}'
    guard_text = f'大於 {fmt_zone_price(chase_limit)} 不追；小於 {fmt_zone_price(stop_line)} 先停'
    reduce_watch = zone_price(ticker, chase_limit)
    trim_line = zone_price(ticker, max(chase_limit * (1.06 if is_core else 1.05), buy_high * 1.10))
    failure_line = stop_line
    if is_core:
        sell_status = '核心部位不因漲多就賣'
        sell_text = f'高於 {fmt_zone_price(reduce_watch)} 先停加碼；高於 {fmt_zone_price(trim_line)} 且超過目標配置時，只減碼額外加碼部位。'
        fail_text = '先檢查大盤與ETF是否異常，核心定期定額不急著整筆賣。'
    elif is_etf_like(ticker):
        sell_status = '過熱或ETF異常才減碼'
        sell_text = f'高於 {fmt_zone_price(reduce_watch)} 不追；高於 {fmt_zone_price(trim_line)} 且折溢價/集中度異常或超過配置時，可分批減碼。'
        fail_text = '先確認是否只是市場回檔，或ETF追蹤/流動性出問題。'
    else:
        sell_status = '不是現在賣，過熱且轉弱才減碼'
        sell_text = f'高於 {fmt_zone_price(reduce_watch)} 先停買；高於 {fmt_zone_price(trim_line)} 且動能轉弱，可減碼25%~33%。'
        fail_text = '若同時營收、EPS或毛利率轉弱，就是投資理由失效警告。'

    if status == '高於追高上限':
        buy_steps = [
            ('第一批', '0%~10%', '只適合試單或照定期定額，不做單筆加碼。'),
            ('第二批', '回到可分批區', f'等價格回到 {range_text} 再買。'),
            ('停止條件', '跌破保護線', f'低於 {fmt_zone_price(stop_line)} 先查原因。'),
        ]
    elif status == '偏高等待區':
        buy_steps = [
            ('第一批', '10%~20%', '想買只能小買，不能一次用完。'),
            ('第二批', '回到20日線附近', f'接近可分批區 {range_text} 再補。'),
            ('停止條件', '跌破保護線', f'低於 {fmt_zone_price(stop_line)} 先停。'),
        ]
    elif status == '可分批區':
        buy_steps = [
            ('第一批', '30%~40%', '價格在合理區，可先建立一部分。'),
            ('第二批', '再跌到區間下緣', f'接近 {fmt_zone_price(buy_low)} 再補。'),
            ('第三批', '站穩或回升', '沒有跌破保護線且量能回穩再買。'),
        ]
    elif status == '加碼觀察區':
        buy_steps = [
            ('第一批', 'ETF 40%~50%', '核心ETF可慢慢加，個股仍要小比例。'),
            ('第二批', '確認沒變壞', '基本面沒惡化、價格沒跌破保護線再補。'),
            ('停止條件', '跌破保護線', f'低於 {fmt_zone_price(stop_line)} 不硬接。'),
        ]
    else:
        buy_steps = [
            ('第一批', 'ETF照扣/個股0%', '不要把跌破保護線當成特價。'),
            ('第二批', '站回保護線', f'站回 {fmt_zone_price(stop_line)} 且風險降下來再看。'),
            ('停止條件', '基本面轉壞', '個股營收、EPS、毛利率轉弱就降風險。'),
        ]

    watch_items = [
        f'價格是否回到可分批區 {range_text}',
        f'是否守住保護線 {fmt_zone_price(stop_line)}',
        f'趨勢是否維持：{trend}，{fmt_zone_price(ma20)} / {fmt_zone_price(ma60)}',
        'ETF看費用率、折溢價、成分股；個股看營收、EPS、ROE、毛利率',
    ]
    return dict(
        status=status,
        summary=summary,
        action=action,
        tone=tone,
        buy_low=buy_low,
        buy_high=buy_high,
        stop_line=stop_line,
        chase_limit=chase_limit,
        range_text=range_text,
        guard_text=guard_text,
        reduce_watch=reduce_watch,
        trim_line=trim_line,
        failure_line=failure_line,
        sell_status=sell_status,
        sell_text=sell_text,
        fail_text=fail_text,
        buy_steps=buy_steps,
        watch_items=watch_items,
        basis=f'{trend_note} 這是價格紀律，不是預測最低或最高點。',
        ma_text=f'20日線 {fmt_zone_price(ma20)}、60日線 {fmt_zone_price(ma60)}'
                + (f'、240日線 {fmt_zone_price(ma240)}' if ma240 is not None else ''),
        role=role,
        w_pct=round(w_pct, 1),
    )


def price_zone_html(zone):
    if not zone:
        return ''
    tone_color = {
        'ok': '#1D9E75',
        'cool': '#185FA5',
        'warm': '#BA7517',
        'hot': '#D85A30',
        'danger': '#D85A30',
    }.get(zone.get('tone'), '#6c757d')
    steps = zone.get('buy_steps') or []
    step_html = ''.join(
        f'<div><span>{h(label)}</span><b>{h(amount)}</b><small>{h(note)}</small></div>'
        for label, amount, note in steps
    )
    watch_items = ''.join(f'<li>{h(item)}</li>' for item in zone.get('watch_items', []))
    return (
        f'<div class="zone-box">'
        f'<div class="zone-head"><span>買進 / 減碼價格地圖</span><b style="color:{tone_color}">{h(zone["status"])}</b></div>'
        f'<div class="zone-grid">'
        f'<div><span>可分批區</span><b>{h(zone["range_text"])}</b><small>臨時想買，優先等這個區間。</small></div>'
        f'<div><span>追高/保護線</span><b>{h(zone["guard_text"])}</b><small>上面不追，下面不硬接。</small></div>'
        f'<div><span>減碼觀察</span><b>{h(zone["sell_status"])}</b><small>{h(zone["sell_text"])}</small></div>'
        f'<div><span>失效提醒</span><b>低於 {h(fmt_zone_price(zone.get("failure_line")))}</b><small>{h(zone["fail_text"])}</small></div>'
        f'</div>'
        f'<p>{h(zone["summary"])}</p>'
        f'<small>{h(zone["action"])} {h(zone["basis"])} {h(zone["ma_text"])}</small>'
        f'<div class="zone-steps">{step_html}</div>'
        f'<div class="zone-watch"><b>觀察依據</b><ul>{watch_items}</ul></div>'
        f'</div>'
    )


def investment_plan_html(plan):
    p = plan['profile']
    reasons = '；'.join(plan['reasons'])
    if plan['amount'] <= 0:
        return ''
    else:
        cost_line = f'買進手續費約 {money(plan["fee"])} 元，{plan["fee_text"]}'
        buy_line = f'本月建議投入約 {money(plan["amount"])} 元，估可買 {share_text(plan["shares"], plan.get("ticker", ""))}'
    odd_lot_note = '<div class="plan-sub">台股一張是 1,000 股；未滿一張就是零股，小資也可以買高價股。</div>' if plan.get('is_tw') else ''

    bt = plan['backtest']
    if bt:
        pnl_col = '#1D9E75' if bt['pnl'] >= 0 else '#D85A30'
        bt_html = (
            f'<div class="plan-mini"><span>三年回測</span>'
            f'<b style="color:{pnl_col}">{money(bt["pnl"])} 元（{bt["roi"]:+.1f}%）</b>'
            f'<small>每月 {money(plan["amount"] if plan["amount"] > 0 else PUBLIC_EXAMPLE_PLAN["min_tw_dca_amount"])} 元，'
            f'投入 {money(bt["invested"])}，未含配息與稅務。</small></div>'
        )
    else:
        bt_html = '<div class="plan-mini"><span>三年回測</span><b>N/A</b><small>資料不足或海外費用未設定。</small></div>'

    return (
        f'<div class="plan-box">'
        f'<div class="plan-head"><span>{p["role"]}</span><b>{p["bucket"]}</b></div>'
        f'<div class="plan-main">{buy_line}</div>'
        f'<div class="plan-sub">{cost_line}</div>'
        f'<div class="plan-sub">賣出成本估算：手續費 {money(plan["sell_fee"])} 元、交易稅 {money(plan["sell_tax"])} 元</div>'
        f'{odd_lot_note}'
        f'<div class="plan-note">{reasons}</div>'
        f'{bt_html}'
        f'<div class="plan-note">{p["note"]}</div>'
        f'</div>'
    )


def decision_card_html(ticker, a, ext, rec, plan):
    d = decision_texts(ticker, a, ext, rec, plan)
    q = data_quality_note(ticker, a)
    return (
        f'<div class="decision-card">'
        f'<div class="decision-head"><span>小白決策卡</span><b>資料可信度：{q["confidence"]}</b></div>'
        f'<div class="decision-row"><span>結論</span><p>{d["conclusion"]}</p></div>'
        f'<div class="decision-row"><span>原因</span><p>{d["reason"]}</p></div>'
        f'<div class="decision-row"><span>反方</span><p>{d["counter"]}</p></div>'
        f'<div class="decision-row"><span>行動</span><p>{d["action"]}</p></div>'
        f'<div class="decision-note"><b>可信度原因：</b>{q["reason"]}<br><span>資料項目：{q["notes"]}</span></div>'
        f'</div>'
    )


def newbie_summary_html(market_ctx=None):
    monthly = PUBLIC_EXAMPLE_PLAN['monthly_budget']
    core = round_amount(monthly * 0.55)
    satellite = round_amount(monthly * 0.20)
    defensive = round_amount(monthly * 0.15)
    cash = max(0, monthly - core - satellite - defensive)
    themes = '、'.join(t['theme'] for t in NEWS_THEMES[:4])
    market_ctx = market_ctx or dict(regime='資料不足', temperature='中性', advice='先照計畫小額分批，不因單日漲跌改變策略。')
    return (
        f'<section class="sc intro-card">'
        f'<div class="st">新手定期定額設定</div>'
        f'<div class="intro-grid">'
        f'<div><span>每月預算</span><b>{money(monthly)} 元</b><small>可在試算工具自行調整，重點是先學會不要一次用完。</small></div>'
        f'<div><span>市場溫度</span><b>{market_ctx["temperature"]}</b><small>{market_ctx["regime"]}。{market_ctx["advice"]}</small></div>'
        f'<div><span>核心ETF</span><b>{money(core)} 元</b><small>0050 或 006208 擇一作主力。</small></div>'
        f'<div><span>衛星/高股息</span><b>{money(satellite)} 元</b><small>主題ETF或高股息ETF，小比例觀察。</small></div>'
        f'<div><span>防守/現金</span><b>{money(defensive + cash)} 元</b><small>保留彈藥，避免高點一次投入。</small></div>'
        f'</div>'
        f'<div class="intro-note">新聞雷達方向：{themes}。新聞只用來找「可關注產業」，仍要經過價格位置、基本面與風險檢查。</div>'
        f'</section>'
    )


def ticker_label(ticker):
    all_names = {}
    all_names.update(TW_STOCKS)
    all_names.update(TW_ETFS)
    all_names.update(US_STOCKS)
    all_names.update(US_ETFS)
    all_names.update(BONDS)
    name = all_names.get(ticker, '')
    code = ticker.replace('.TW', '')
    return f'{code} {name}'.strip()


def theme_radar_html():
    cards = []
    for item in NEWS_THEMES:
        if item['watch']:
            watch = ''.join(f'<span>{h(ticker_label(tk))}</span>' for tk in item['watch'][:8])
        else:
            watch = '<span>先觀察產業，不列買進名單</span>'
        words = '、'.join(item['words'][:5])
        cards.append(
            f'<div class="theme-card">'
            f'<div class="theme-title">{h(item["theme"])}</div>'
            f'<p>關鍵字：{h(words)}</p>'
            f'<div class="theme-watch">{watch}</div>'
            f'</div>'
        )
    return (
        f'<section class="sc theme-radar">'
        f'<div class="st">題材觀察雷達</div>'
        f'<div class="method-lead">這裡不是新聞買進訊號，只是把新聞可能提到的產業，轉成「可以研究的清單」。進場前仍要看估值、風險、趨勢與基本面。</div>'
        f'<div class="theme-grid">{"".join(cards)}</div>'
        f'</section>'
    )


def methodology_html():
    return (
        f'<section class="sc methodology">'
        f'<div class="st">計算方式說明</div>'
        f'<div class="method-lead">這個網站不是預測明天漲跌，而是把公開資料整理成「現在適不適合分批、要不要追高、風險在哪裡」。</div>'
        f'<div class="method-grid">'
        f'<div><b>小白版怎麼看</b><p>先看市場狀態與人話結論，再看每張卡片的健康分數。高分不代表保證賺錢，低分也不代表一定會跌。</p></div>'
        f'<div><b>ETF 分數</b><p>趨勢 25% + 動能 15% + 成交量 10% + 價格位置 10% + 風險 20% + ETF資料 20%。ETF 要看成本、折溢價、規模、配息與總報酬。</p></div>'
        f'<div><b>個股分數</b><p>趨勢 25% + 動能 15% + 成交量 10% + 價格位置 10% + 風險 20% + 基本面 20%。PE 只是入口，還要看營收、EPS、ROE與毛利率。</p></div>'
        f'<div><b>價格基準</b><p>價格卡以 Yahoo quote 的資料時間為主：報價時間先顯示台灣時間，括號補原市場時間；價格顯示該交易日的最新價/收盤、開盤、昨收、高低價。</p></div>'
        f'<div><b>買進/減碼區間</b><p>不是只回答買或不買，而是拆成可分批區、追高上限、減碼觀察與跌破保護線。高於上限不臨時追，小於保護線不硬接，減碼也只先動額外加碼或超過配置的部分。</p></div>'
        f'<div><b>零股怎麼算</b><p>個股是單一公司股票，不等於零股。台股一張是1,000股，未滿一張就是零股；試算會用股數與約幾張一起顯示。</p></div>'
        f'<div><b>RSI 怎麼解讀</b><p>RSI 高不一定危險。若趨勢強、量能正常，可能是強勢延續；若高檔轉弱、KD 偏空、波動放大，才提高風險。</p></div>'
        f'</div>'
        f'<div class="method-note">本頁只使用公開資料與固定規則，不放持倉、成本、券商帳戶、API key 或任何個人通知設定。</div>'
        f'</section>'
    )


def public_readiness_html(update_time, market_ctx=None):
    market_ctx = market_ctx or {}
    temp = market_ctx.get('temperature', '中性')
    regime = market_ctx.get('regime', '資料不足')
    return (
        f'<section class="sc public-check">'
        f'<div class="st">資料檢查</div>'
        f'<div class="method-lead">這一區是出貨前自查用：本頁是靜態頁，數字只代表這次產生報告時抓到的資料。</div>'
        f'<div class="check-grid">'
        f'<div><span>報告產生</span><b>{h(update_time)} 台灣時間</b><small>若過了交易日，請重新跑 generate.py 更新。</small></div>'
        f'<div><span>資料來源</span><b>TWSE / Yahoo</b><small>台股價格優先 TWSE 盤後公開資料；其他欄位用 Yahoo 免費資料補足。每張卡會列報價時間、來源與市場狀態。</small></div>'
        f'<div><span>市場狀態</span><b>{h(temp)}</b><small>{h(regime)}。這只調整分批與風險，不是保證漲跌。</small></div>'
        f'<div><span>手續費邏輯</span><b>分兩套</b><small>定期定額用台股1元優惠假設；臨時買入用一般電子下單低消估算。</small></div>'
        f'<div><span>資安檢查</span><b>不放個人資料</b><small>沒有持股、成本、券商、API key、AI key 或個人預算。</small></div>'
        f'<div><span>限制提醒</span><b>不是下單系統</b><small>買進/減碼區間是紀律提醒，不能取代券商成交價與正式投資建議。</small></div>'
        f'</div>'
        f'</section>'
    )


def dca_simulator_html(market_ctx=None):
    if not DCA_SERIES:
        return ''

    market_ctx = market_ctx or dict(regime='資料不足', temperature='中性', advice='先照計畫小額分批，不因單日漲跌改變策略。')
    temp = market_ctx.get('temperature', '中性')
    if temp == '熱':
        dca_status = ('適合持續', '照原本節奏扣款，不因為高檔就停扣。')
        batch_status = ('可，但要分批', '想單筆買可以切成 3-6 次，不要一次用完。')
        lump_status = ('不建議', '市場偏熱時，新手重押最容易買完就震盪。')
    elif temp == '冷':
        dca_status = ('適合持續，可小幅加碼', '市場偏冷時，長期資金可以分批買，但仍要留生活預備金。')
        batch_status = ('適合分批', '可以把預算拆開慢慢買，避免猜最低點。')
        lump_status = ('仍不建議', '就算看起來便宜，也不要把兩年內會用到的錢投進去。')
    else:
        dca_status = ('適合持續', '沒有極端訊號時，照計畫扣款通常比猜高低點穩。')
        batch_status = ('可以', '若有額外資金，分批比一次買更適合新手。')
        lump_status = ('少量即可', '除非很了解風險，否則不建議一次投入太大比例。')

    ordered = [tk for tk in DCA_SIM_TICKERS if tk in DCA_SERIES]
    options = ''.join(
        f'<option value="{tk}">{tk.replace(".TW", "")} {DCA_SERIES[tk]["name"]}</option>'
        for tk in ordered
    )
    data = {tk: DCA_SERIES[tk] for tk in ordered}
    data_json = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    monthly = PUBLIC_EXAMPLE_PLAN['monthly_budget']

    return (
        f'<section class="sc dca-tool">'
        f'<div class="tool-head"><div><div class="st">定期定額模擬器</div>'
        f'<p>用歷史價格練習「如果我每月固定投入，過程會多痛、最後可能變多少」。這不是預測，只是幫新手先看懂風險。</p></div>'
        f'<div class="temp-pill">{temp}</div></div>'
        f'<div class="dca-controls">'
        f'<label><span>標的</span><select id="dcaTicker">{options}</select></label>'
        f'<label><span>每月投入</span><input id="dcaAmount" type="number" min="1000" step="1000" value="{monthly}"></label>'
        f'<label><span>投資年限</span><input id="dcaYears" type="number" min="1" max="20" step="1" value="5"></label>'
        f'</div>'
        f'<div class="dca-result" id="dcaResult"></div>'
        f'<div class="tool-note">模擬基準：ETF 優先用 Yahoo Adj Close 做含息估算，較接近「配息再投入」；仍未完整計入匯率、稅務、券商差異與ETF內扣成本。</div>'
        f'<div class="strategy-grid">'
        f'<div><span>定期定額</span><b>{dca_status[0]}</b><small>{dca_status[1]}</small></div>'
        f'<div><span>分批單筆</span><b>{batch_status[0]}</b><small>{batch_status[1]}</small></div>'
        f'<div><span>重押單筆</span><b>{lump_status[0]}</b><small>{lump_status[1]}</small></div>'
        f'</div>'
        f'<div class="panic-box"><b>跌了怎麼辦</b>'
        f'<div><span>-10%</span><p>先不要慌，檢查是不是整體市場震盪；定期定額通常照扣。</p></div>'
        f'<div><span>-20%</span><p>這是新手最容易停扣的位置。若是核心 ETF 且資金是長期的，可以小額分批加碼。</p></div>'
        f'<div><span>-30%</span><p>代表市場進入很痛的區間。不要借錢加碼，只用兩年內不會用到的閒錢慢慢買。</p></div>'
        f'</div>'
        f'</section>'
        f'<script>'
        f'window.DCA_DATA={data_json};'
        f'''
function fmtMoney(n){{return Math.round(n).toLocaleString('zh-TW');}}
function monthKey(dateText){{return dateText.slice(0,7);}}
function runDcaSim(){{
  var ticker=document.getElementById('dcaTicker');
  var amountEl=document.getElementById('dcaAmount');
  var yearsEl=document.getElementById('dcaYears');
  var out=document.getElementById('dcaResult');
  if(!ticker||!amountEl||!yearsEl||!out||!window.DCA_DATA) return;
  var item=window.DCA_DATA[ticker.value];
  if(!item||!item.points||!item.points.length){{out.innerHTML='<div class="nd">資料不足，暫時無法模擬。</div>';return;}}
  var amount=Math.max(0, Number(amountEl.value||0));
  var years=Math.max(1, Number(yearsEl.value||1));
  var points=(item.total_points&&item.total_points.length)?item.total_points:item.points;
  var basisText=(item.total_points&&item.total_points.length)?item.total_basis:(item.basis||'市場收盤價');
  var lastDate=new Date(points[points.length-1][0]);
  var cutoff=new Date(lastDate);
  cutoff.setFullYear(cutoff.getFullYear()-years);
  var monthly=[];
  var seen={{}};
  for(var i=0;i<points.length;i++){{
    var d=new Date(points[i][0]);
    if(d<cutoff) continue;
    var key=monthKey(points[i][0]);
    if(!seen[key]){{monthly.push(points[i]);seen[key]=true;}}
  }}
  if(monthly.length<3){{out.innerHTML='<div class="nd">可用月份太少，請縮短年限。</div>';return;}}
  var units=0, invested=0, fees=0, peak=0, maxDrawdown=0, minRoi=0;
  var feePerBuy=ticker.value.endsWith('.TW') ? 1 : 0;
  for(var j=0;j<monthly.length;j++){{
    var price=Number(monthly[j][1]);
    var net=Math.max(0, amount-feePerBuy);
    var buyUnits=Math.floor(net/price);
    units+=buyUnits;
    fees+=feePerBuy;
    invested+=amount;
    var value=units*price;
    peak=Math.max(peak,value);
    var dd=peak>0 ? (value/peak-1)*100 : 0;
    maxDrawdown=Math.min(maxDrawdown,dd);
    var roi=invested>0 ? (value/invested-1)*100 : 0;
    minRoi=Math.min(minRoi,roi);
  }}
  var lastPrice=Number(points[points.length-1][1]);
  var finalValue=units*lastPrice;
  var pnl=finalValue-invested;
  var roiFinal=invested>0 ? pnl/invested*100 : 0;
  var pnlColor=pnl>=0?'#1D9E75':'#D85A30';
  out.innerHTML=
    '<div class="dca-stat"><span>累積投入</span><b>'+fmtMoney(invested)+' 元</b><small>'+monthly.length+' 個月，每月 '+fmtMoney(amount)+' 元</small></div>'+
    '<div class="dca-stat"><span>期末資產估算</span><b style="color:'+pnlColor+'">'+fmtMoney(finalValue)+' 元</b><small>損益 '+(pnl>=0?'+':'')+fmtMoney(pnl)+' 元（'+roiFinal.toFixed(1)+'%）</small></div>'+
    '<div class="dca-stat"><span>過程最大回撤</span><b style="color:#D85A30">'+maxDrawdown.toFixed(1)+'%</b><small>資產曾從高點往下跌的幅度</small></div>'+
    '<div class="dca-stat"><span>最差帳面損益</span><b style="color:#D85A30">'+minRoi.toFixed(1)+'%</b><small>歷史過程中最難熬的一刻</small></div>'+
    '<div class="dca-stat"><span>模擬口徑</span><b>'+basisText+'</b><small>含息估算不是正式保證報酬</small></div>';
}}
document.addEventListener('DOMContentLoaded',function(){{
  ['dcaTicker','dcaAmount','dcaYears'].forEach(function(id){{
    var el=document.getElementById(id);
    if(el) el.addEventListener('input',runDcaSim);
    if(el) el.addEventListener('change',runDcaSim);
  }});
  runDcaSim();
}});
'''
        f'</script>'
    )


def buy_now_tool_html(market_ctx=None):
    if not BUY_NOW_DATA:
        return ''
    market_ctx = market_ctx or dict(temperature='中性')
    temp = market_ctx.get('temperature', '中性')
    ordered = [tk for tk in list(TW_ETFS.keys()) + list(US_ETFS.keys()) + list(TW_STOCKS.keys()) + list(US_STOCKS.keys()) if tk in BUY_NOW_DATA]
    options = ''.join(
        f'<option value="{tk}">{tk.replace(".TW", "")} {BUY_NOW_DATA[tk]["name"]}</option>'
        for tk in ordered
    )
    data = {tk: BUY_NOW_DATA[tk] for tk in ordered}
    data_json = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    fee_rate = PUBLIC_EXAMPLE_PLAN['broker_fee_rate'] * PUBLIC_EXAMPLE_PLAN['broker_fee_discount']
    min_fee = PUBLIC_EXAMPLE_PLAN['regular_min_fee']
    return (
        f'<section class="sc buy-tool">'
        f'<div class="tool-head"><div><div class="st">今天想買試算</div>'
        f'<p>臨時想買時，不先問「會不會漲」，先問「現在能買幾成、剩下要留多少」。這裡用固定規則估算，不代表個人投資建議。</p></div>'
        f'<div class="temp-pill">{temp}</div></div>'
        f'<div class="dca-controls">'
        f'<label><span>標的</span><select id="buyTicker">{options}</select></label>'
        f'<label><span>今天預算</span><input id="buyBudget" type="number" min="1000" step="1000" value="10000"></label>'
        f'<label><span>分批次數</span><input id="buyBatches" type="number" min="1" max="6" step="1" value="3"></label>'
        f'</div>'
        f'<div class="buy-result" id="buyResult"></div>'
        f'<div class="tool-note">理由：臨時買入比定期定額更容易追高，所以預設只建議先投入一部分；剩下現金是保護，不是浪費。台股試算用零股股數，不要求買滿一張。</div>'
        f'</section>'
        f'<script>'
        f'window.BUY_NOW_DATA={data_json};window.MARKET_TEMP={json.dumps(temp, ensure_ascii=False)};'
        f'window.BUY_FEE_RATE={fee_rate};window.BUY_MIN_FEE={min_fee};'
        f'''
window.fmtMoney=window.fmtMoney||function(n){{return Math.round(n).toLocaleString('zh-TW');}};
function fmtShares(n,item){{
  n=Math.max(0, Math.floor(Number(n||0)));
  if(item&&item.is_tw){{
    var lots=n/1000;
    if(n===0) return '0 股';
    if(n<1000) return n.toLocaleString('zh-TW')+' 股零股（約 '+lots.toFixed(3)+' 張）';
    var whole=Math.floor(n/1000), odd=n%1000;
    return odd>0 ? n.toLocaleString('zh-TW')+' 股（'+whole+' 張 + '+odd+' 股零股）' : n.toLocaleString('zh-TW')+' 股（'+whole+' 張）';
  }}
  return n.toLocaleString('zh-TW')+' 股';
}}
function runBuyNow(){{
  var ticker=document.getElementById('buyTicker');
  var budgetEl=document.getElementById('buyBudget');
  var batchEl=document.getElementById('buyBatches');
  var out=document.getElementById('buyResult');
  if(!ticker||!budgetEl||!batchEl||!out||!window.BUY_NOW_DATA) return;
  var item=window.BUY_NOW_DATA[ticker.value];
  if(!item){{out.innerHTML='<div class="nd">資料不足，暫時無法試算。</div>';return;}}
  var budget=Math.max(0, Number(budgetEl.value||0));
  var batches=Math.max(1, Number(batchEl.value||1));
  var ratio=Number(item.ratio||0);
  var zone=item.price_zone||{{}};
  if(window.MARKET_TEMP==='熱') ratio=Math.min(ratio, item.role==='長期核心'?0.30:0.18);
  if(window.MARKET_TEMP==='冷') ratio=Math.min(ratio+0.08, item.role==='長期核心'?0.50:0.30);
  if(item.role==='個股觀察') ratio=Math.min(ratio,0.15);
  if(zone.status==='高於追高上限') ratio=Math.min(ratio, item.role==='長期核心'?0.12:0.05);
  if(zone.status==='偏高等待區') ratio=Math.min(ratio, item.role==='長期核心'?0.20:0.08);
  if(zone.status==='跌破保護線') ratio=Math.min(ratio, item.role==='長期核心'?0.10:0.00);
  if(zone.status==='可分批區') ratio=Math.max(ratio, item.role==='長期核心'?0.25:0.08);
  if(zone.status==='加碼觀察區') ratio=Math.max(ratio, item.role==='長期核心'?0.30:0.08);
  var firstAmount=Math.round((budget*ratio)/100)*100;
  if(ratio>0 && firstAmount<1000) firstAmount=Math.min(budget,1000);
  var reserve=Math.max(0,budget-firstAmount);
  var batchAmount=batches>1?Math.round((firstAmount/batches)/100)*100:firstAmount;
  var fee=0, feeText='海外標的費率另計';
  if(item.is_tw && firstAmount>0){{
    fee=Math.max(window.BUY_MIN_FEE, Math.round(firstAmount*window.BUY_FEE_RATE));
    feeText='買進手續費約 '+fmtMoney(fee)+' 元，未含日後賣出交易稅';
  }}else if(item.is_tw){{
    feeText='本次先不買，所以沒有買進手續費';
  }}
  var zoneHtml='';
  if(zone.status){{
    var sellText=zone.sell_status ? zone.sell_status+'：'+zone.sell_text : '看配置與風險，不用因為漲就亂賣';
    var stepText='';
    if(zone.buy_steps&&zone.buy_steps.length){{
      stepText=zone.buy_steps.map(function(s){{return s[0]+' '+s[1]+'：'+s[2];}}).join(' / ');
    }}
    zoneHtml=
      '<div class="dca-stat"><span>價格區間</span><b>'+zone.status+'</b><small>'+zone.summary+'</small></div>'+
      '<div class="dca-stat"><span>可分批區</span><b>'+zone.range_text+'</b><small>'+zone.guard_text+'</small></div>'+
      '<div class="dca-stat"><span>分批依據</span><b>按區間，不按感覺</b><small>'+stepText+'</small></div>'+
      '<div class="dca-stat"><span>減碼提醒</span><b>'+zone.sell_status+'</b><small>'+sellText+'</small></div>';
  }}
  var shares=firstAmount>fee && item.price>0 ? Math.floor((firstAmount-fee)/item.price) : 0;
  var amountColor=firstAmount>0?'#1D9E75':'#D85A30';
  var headline=firstAmount>0?'先買一部分，不要一次用完':'先不要買，保留現金';
  out.innerHTML=
    '<div class="buy-summary"><span>'+item.role+' / '+item.bucket+'</span><b>'+headline+'</b><small>資料可信度：'+item.confidence+'。'+item.data_note+'</small></div>'+
    '<div class="dca-stat"><span>建議先投入</span><b style="color:'+amountColor+'">'+fmtMoney(firstAmount)+' 元</b><small>約預算 '+(ratio*100).toFixed(0)+'%，估 '+fmtShares(shares,item)+'</small></div>'+
    '<div class="dca-stat"><span>保留現金</span><b>'+fmtMoney(reserve)+' 元</b><small>避免買完立刻遇到回檔沒子彈</small></div>'+
    '<div class="dca-stat"><span>分批買法</span><b>'+fmtMoney(batchAmount)+' 元/批</b><small>'+batches+' 批；高檔時分批比猜最低點實際</small></div>'+
    '<div class="dca-stat"><span>成本提醒</span><b>'+feeText+'</b><small>不同券商折扣與低消會不同</small></div>'+
    zoneHtml+
    '<div class="decision-card buy-decision"><div class="decision-row"><span>結論</span><p>'+item.conclusion+'</p></div>'+
    '<div class="decision-row"><span>原因</span><p>'+item.reason+'</p></div>'+
    '<div class="decision-row"><span>反方</span><p>'+item.counter+'</p></div>'+
    '<div class="decision-row"><span>行動</span><p>'+item.action+'</p></div></div>';
}}
document.addEventListener('DOMContentLoaded',function(){{
  ['buyTicker','buyBudget','buyBatches'].forEach(function(id){{
    var el=document.getElementById(id);
    if(el) el.addEventListener('input',runBuyNow);
    if(el) el.addEventListener('change',runBuyNow);
  }});
  runBuyNow();
}});
'''
        f'</script>'
    )


def daily_order_overview_html(market_ctx=None):
    if not BUY_NOW_DATA:
        return ''
    market_ctx = market_ctx or dict(temperature='中性')
    temp = market_ctx.get('temperature', '中性')
    ordered = [
        tk for tk in
        list(TW_ETFS.keys()) + list(TW_STOCKS.keys()) + list(US_ETFS.keys()) + list(US_STOCKS.keys()) + list(BONDS.keys())
        if tk in BUY_NOW_DATA
    ]
    keys_json = json.dumps(ordered, ensure_ascii=False, separators=(',', ':'))
    default_budget = PUBLIC_EXAMPLE_PLAN['monthly_budget'] * 2
    script = '''
function orderTickSize(price){
  if(price<10) return 0.01;
  if(price<50) return 0.05;
  if(price<100) return 0.10;
  if(price<500) return 0.50;
  if(price<1000) return 1.00;
  return 5.00;
}
function roundOrderPrice(item, price, side){
  price=Number(price||0);
  if(!price||price<=0) return 0;
  if(!item.is_tw) return Math.round(price*100)/100;
  var tick=orderTickSize(price);
  var scaled=price/tick;
  var rounded=side==='sell' ? Math.ceil(scaled)*tick : Math.floor(scaled)*tick;
  return Math.round(rounded*100)/100;
}
function fmtOrderPrice(v,item){
  v=Number(v||0);
  if(!v) return 'N/A';
  return v.toLocaleString('zh-TW',{minimumFractionDigits:item&&item.is_tw?2:2,maximumFractionDigits:item&&item.is_tw?2:2});
}
function orderConfidenceFactor(text){
  if(text==='高') return 1.00;
  if(text==='中') return 0.85;
  if(text==='偏低') return 0.55;
  return 0.35;
}
function orderCategory(item){
  if(item.is_tw&&item.is_etf) return 'tw-etf';
  if(item.is_tw) return 'tw-stock';
  if(item.is_etf) return 'us-etf';
  return 'us-stock';
}
function calcOrderRatio(item){
  var z=item.price_zone||{};
  var ratio=Number(item.ratio||0);
  if(window.MARKET_TEMP==='熱') ratio=Math.min(ratio, item.role==='長期核心'?0.30:0.18);
  if(window.MARKET_TEMP==='冷') ratio=Math.min(ratio+0.08, item.role==='長期核心'?0.50:0.30);
  if(item.role==='個股觀察') ratio=Math.min(ratio,0.10);
  if(z.status==='高於追高上限') ratio=Math.min(ratio, item.role==='長期核心'?0.10:0.04);
  if(z.status==='偏高等待區') ratio=Math.min(ratio, item.role==='長期核心'?0.18:0.07);
  if(z.status==='跌破保護線') ratio=0;
  if(z.status==='可分批區') ratio=Math.max(ratio, item.role==='長期核心'?0.25:0.06);
  if(z.status==='加碼觀察區') ratio=Math.max(ratio, item.role==='長期核心'?0.28:(item.is_etf?0.08:0.04));
  if(Number(item.risk_score||50)<40) ratio*=0.60;
  ratio*=orderConfidenceFactor(item.confidence);
  return Math.max(0, Math.min(0.50, ratio));
}
function calcOrderTarget(item){
  var z=item.price_zone||{};
  var price=Number(item.price||0);
  var low=Number(z.buy_low||0), high=Number(z.buy_high||0), stop=Number(z.stop_line||0);
  var status=z.status||'';
  var risk=Number(item.risk_score||50);
  var w=Number(item.w_pct||50);
  var pull=item.role==='長期核心'?0.003:0.006;
  if(risk<45) pull+=0.004;
  if(w>=85) pull+=0.004;
  var target=price, action='可掛單', cls='order-ok', note='在可分批區內低掛，沒成交不追價。';
  if(status==='高於追高上限'){
    target=high||price*0.97;
    action='等回檔';
    cls='order-wait';
    note='掛在可分批區上緣附近，今天沒碰到就不追。';
  }else if(status==='偏高等待區'){
    target=Math.min(high||price*0.99, price*0.99);
    action='低掛小單';
    cls='order-wait';
    note='價格偏高，只能低掛，成交不到也不要抬價。';
  }else if(status==='可分批區'){
    target=Math.max(low||0, Math.min(high||price, price*(1-pull)));
  }else if(status==='加碼觀察區'){
    target=price*(item.is_etf?0.998:0.994);
    if(stop) target=Math.max(target, stop*1.01);
    action=item.is_etf?'可小加碼':'小額試單';
    note=item.is_etf?'低位可小幅分批，但仍要保留現金。':'個股只小額試單，確認基本面沒變壞。';
  }else if(status==='跌破保護線'){
    target=stop||price;
    action='先不買';
    cls='order-stop';
    note='跌破保護線時不要硬接，等站回再重新算。';
  }
  if(high && target>high && status!=='加碼觀察區') target=high;
  if(low && target<low && status==='可分批區') target=low;
  return {price:roundOrderPrice(item,target,'buy'), action:action, cls:cls, note:note};
}
function runDailyOrders(){
  var out=document.getElementById('orderRows');
  var budgetEl=document.getElementById('orderBudget');
  var filterEl=document.getElementById('orderFilter');
  if(!out||!budgetEl||!window.BUY_NOW_DATA) return;
  var budget=Math.max(0, Number(budgetEl.value||0));
  var filter=filterEl?filterEl.value:'all';
  var keys=window.ORDER_KEYS||Object.keys(window.BUY_NOW_DATA);
  var html='';
  keys.forEach(function(tk){
    var item=window.BUY_NOW_DATA[tk];
    if(!item) return;
    var cat=orderCategory(item);
    if(filter!=='all'&&filter!==cat) return;
    var z=item.price_zone||{};
    var target=calcOrderTarget(item);
    var ratio=calcOrderRatio(item);
    if(target.action==='先不買') ratio=0;
    var amount=Math.round((budget*ratio)/100)*100;
    var fee=0;
    if(item.is_tw&&amount>0) fee=Math.max(window.BUY_MIN_FEE||20, Math.round(amount*(window.BUY_FEE_RATE||0.000855)));
    if(ratio>0&&item.is_tw&&target.price>0&&amount<target.price+fee&&budget>=target.price+fee){
      amount=Math.min(budget, Math.ceil((target.price+fee)/100)*100);
      fee=Math.max(window.BUY_MIN_FEE||20, Math.round(amount*(window.BUY_FEE_RATE||0.000855)));
    }
    if(ratio>0&&!item.is_tw&&target.price>0&&amount<target.price&&budget>=target.price){
      amount=Math.min(budget, Math.ceil(target.price));
    }
    var shares=target.price>0 ? Math.floor(Math.max(0, amount-fee)/target.price) : 0;
    if(shares<=0){ amount=0; fee=0; }
    var spend=shares>0 ? shares*target.price+fee : 0;
    var maxBuy=roundOrderPrice(item, z.buy_high||target.price, 'buy');
    var sellWatch=roundOrderPrice(item, z.trim_line||z.reduce_watch||0, 'sell');
    var failLine=roundOrderPrice(item, z.failure_line||z.stop_line||0, 'sell');
    var code=tk.replace('.TW','');
    var currency=item.is_tw?'TWD':'USD估';
    var feeText=item.is_tw?(fee>0?'含手續費約 '+fmtMoney(fee)+' 元':'無買進手續費估算'):'海外手續費/匯率另計';
    html+=
      '<tr>'+
      '<td><b>'+code+' '+item.name+'</b><small>'+item.role+' / '+item.bucket+' / '+currency+'</small></td>'+
      '<td><span class="order-action '+target.cls+'">'+target.action+'</span><small>'+target.note+'</small></td>'+
      '<td><b>'+fmtOrderPrice(target.price,item)+'</b><small>現價 '+fmtOrderPrice(item.price,item)+'</small></td>'+
      '<td><b>'+fmtMoney(spend)+' 元</b><small>'+fmtShares(shares,item)+'；'+feeText+'</small></td>'+
      '<td><b>'+fmtOrderPrice(maxBuy,item)+'</b><small>高於此價不抬價追</small></td>'+
      '<td><b>'+fmtOrderPrice(sellWatch,item)+'</b><small>'+(z.sell_status||'轉弱才減碼')+'</small></td>'+
      '<td><b>'+fmtOrderPrice(failLine,item)+'</b><small>跌破先停，不硬接</small></td>'+
      '<td><small>'+((z.status||'區間待補')+'；可信度 '+item.confidence+'；風險 '+(item.risk_score||'N/A'))+'</small></td>'+
      '</tr>';
  });
  out.innerHTML=html||'<tr><td colspan="8"><div class="nd">沒有符合篩選的標的。</div></td></tr>';
}
document.addEventListener('DOMContentLoaded',function(){
  ['orderBudget','orderFilter'].forEach(function(id){
    var el=document.getElementById(id);
    if(el) el.addEventListener('input',runDailyOrders);
    if(el) el.addEventListener('change',runDailyOrders);
  });
  runDailyOrders();
});
'''
    return (
        f'<section class="sc order-tool">'
        f'<div class="tool-head"><div><div class="st">今日掛單總覽</div>'
        f'<p>把每檔標的轉成今天能執行的掛單價、投入金額與風險線。掛單價是紀律價，不是預測價；沒成交就不追高。</p></div>'
        f'<div class="temp-pill">{h(temp)}</div></div>'
        f'<div class="order-filter">'
        f'<label><span>今日預算</span><input id="orderBudget" type="number" min="1000" step="1000" value="{default_budget}"></label>'
        f'<label><span>類別</span><select id="orderFilter">'
        f'<option value="all">全部</option><option value="tw-etf">台股 ETF</option><option value="tw-stock">台股個股</option>'
        f'<option value="us-etf">美股 ETF</option><option value="us-stock">美股個股</option></select></label>'
        f'<label><span>規則口徑</span><select disabled><option>價格區間 + 風險 + 資料可信度</option></select></label>'
        f'</div>'
        f'<div class="order-wrap"><table class="order-table">'
        f'<thead><tr><th>標的</th><th>今日動作</th><th>買入掛單價</th><th>建議投入</th><th>最高買價</th><th>賣出觀察價</th><th>失效線</th><th>理由</th></tr></thead>'
        f'<tbody id="orderRows"></tbody></table></div>'
        f'<div class="tool-note">買入掛單價會依台股跳動單位取合法價位；台股用零股估算，海外標的未含匯率與複委託成本。賣出觀察價不是到價就賣，還要搭配轉弱、超過配置或ETF異常。</div>'
        f'</section>'
        f'<script>window.ORDER_KEYS={keys_json};</script><script>{script}</script>'
    )


def calc_extended(close_series, close_val, ohlc, ticker, adj_close_series=None, price_basis_note='市場收盤價', price_basis_adjusted=False):
    c = close_series.dropna()

    period = min(252, len(c))
    w_high = float(c.tail(period).max())
    w_low  = float(c.tail(period).min())
    w_pct  = round((close_val - w_low) / (w_high - w_low) * 100, 1) if w_high > w_low else 50.0

    ma5  = float(c.rolling(5).mean().iloc[-1])  if len(c) >= 5  else None
    ma20 = float(c.rolling(20).mean().iloc[-1]) if len(c) >= 20 else None
    ma60 = float(c.rolling(60).mean().iloc[-1]) if len(c) >= 60 else None
    ma240 = float(c.rolling(240).mean().iloc[-1]) if len(c) >= 240 else None
    if ma5 and ma20 and ma60:
        if ma5 > ma20 > ma60:   ma_align = '多頭排列'
        elif ma5 < ma20 < ma60: ma_align = '空頭排列'
        else:                   ma_align = '盤整中'
    else:
        ma_align = '盤整中'

    year_str = str(datetime.now(ZoneInfo('Asia/Taipei')).year)
    ytd_data = c[c.index.astype(str) >= year_str]
    ytd = round((close_val - float(ytd_data.iloc[0])) / float(ytd_data.iloc[0]) * 100, 1) if len(ytd_data) >= 2 else None

    rets = c.pct_change().dropna()
    volatility = round(float(rets.tail(20).std()) * (252 ** 0.5) * 100, 1) if len(rets) >= 20 else None
    max_drawdown, current_drawdown = calc_drawdown(c)
    beta = calc_beta(c, ticker)
    return_source = adj_close_series.dropna() if adj_close_series is not None and len(adj_close_series.dropna()) else c
    price_return_3y = pct_return(c, 756)
    total_return_3y = pct_return(return_source, 756)
    tracking_error, tracking_benchmark, tracking_label, tracking_hint, tracking_mode = calc_tracking_error(return_source, ticker)

    is_tw = ticker in TW_LIMIT_MARKETS
    reference_close = (ohlc or {}).get('reference_close')
    limit_up   = round(round_to_tick(reference_close * 1.10, 'up'), 2) if is_tw and reference_close else None
    limit_down = round(round_to_tick(reference_close * 0.90, 'down'), 2) if is_tw and reference_close else None

    result = {
        'w_high': round(w_high, 2), 'w_low': round(w_low, 2), 'w_pct': w_pct,
        'ma_align': ma_align,
        'ma20': round(ma20, 2) if ma20 else None,
        'ma60': round(ma60, 2) if ma60 else None,
        'ma240': round(ma240, 2) if ma240 else None,
        'ytd': ytd,
        'volatility': volatility,
        'max_drawdown': max_drawdown,
        'current_drawdown': current_drawdown,
        'beta': beta,
        'price_return_3y': price_return_3y,
        'total_return_3y': total_return_3y,
        'tracking_error': tracking_error,
        'tracking_benchmark': tracking_benchmark,
        'tracking_label': tracking_label,
        'tracking_hint': tracking_hint,
        'tracking_mode': tracking_mode,
        'price_basis_note': price_basis_note,
        'price_basis_adjusted': price_basis_adjusted,
        'limit_up': limit_up,
        'limit_down': limit_down,
        'is_tw': is_tw,
    }
    if ohlc:
        result.update(ohlc)
    return result


def analyze(res, close_val):
    k    = gl(res['k'])
    d    = gl(res['d'])
    rsi  = gl(res['rsi'])
    macd = gl(res['macd'])
    msig = gl(res['macd_sig'])
    ma20 = gl(res['ma20'])
    ma60 = gl(res['ma60'])
    dev  = gl(res['dev20'])
    adx  = gl(res['adx'])
    dip  = gl(res['dip'])
    dim  = gl(res['dim'])
    bu   = gl(res['boll_u'])
    bl   = gl(res['boll_l'])
    vr   = gl(res['vol_ratio']) if len(res['vol_ratio'].dropna()) else float('nan')

    def ok(v): return not np.isnan(v)

    score = 50
    reasons = []
    kd_signal = None

    if ok(k) and ok(d):
        kd_df = pd.concat([res['k'], res['d']], axis=1).dropna()
        cross_up = cross_down = False
        if len(kd_df) >= 2:
            prev_k, prev_d = kd_df.iloc[-2, 0], kd_df.iloc[-2, 1]
            cross_up = prev_k <= prev_d and k > d
            cross_down = prev_k >= prev_d and k < d
        if k > d:
            score += 8
            if cross_up:
                score += 6
                kd_signal = '黃金交叉'
            else:
                kd_signal = 'KD偏多'
            if k < 25:
                score += 6
                reasons.append(f'KD 低檔{kd_signal}（較強買訊）')
            else:
                reasons.append(kd_signal)
        else:
            score -= 8
            if cross_down:
                score -= 6
                kd_signal = '死亡交叉'
            else:
                kd_signal = 'KD偏空'
            if k > 75:
                score -= 6
                reasons.append(f'KD 高檔{kd_signal}（注意風險）')
            else:
                reasons.append(kd_signal)

    if ok(rsi):
        if rsi < 30:   score += 14; reasons.append(f'RSI {rsi:.0f} 超賣區，有反彈機會')
        elif rsi > 70: score -= 14; reasons.append(f'RSI {rsi:.0f} 超買區，追高有風險')
        elif 40 <= rsi <= 60: score += 4; reasons.append(f'RSI {rsi:.0f} 健康區間')

    if ok(macd) and ok(msig):
        if macd > msig: score += 8; reasons.append('MACD 多頭排列')
        else:           score -= 8; reasons.append('MACD 空頭排列')

    if ok(close_val) and ok(ma20):
        if close_val > ma20:
            score += 7
            if ok(ma60) and close_val > ma60: score += 6; reasons.append('站上月線與季線（強勢格局）')
            else: reasons.append('站上月線')
        else:
            score -= 7
            if ok(ma60) and close_val < ma60: score -= 6; reasons.append('跌破月線與季線（弱勢格局）')
            else: reasons.append('跌破月線')

    if ok(dev):
        if   dev < -8:  score += 12; reasons.append(f'20日乖離 {dev:.1f}%，嚴重超賣')
        elif dev >  10: score -= 12; reasons.append(f'20日乖離 +{dev:.1f}%，嚴重過熱')
        elif dev < -5:  score += 6;  reasons.append(f'20日乖離 {dev:.1f}%，偏低有機會')
        elif dev >   5: score -= 6;  reasons.append(f'20日乖離 +{dev:.1f}%，偏高注意')

    if ok(adx) and ok(dip) and ok(dim) and adx > 25:
        if dip > dim: score += 7; reasons.append(f'ADX {adx:.0f} 多頭趨勢確立')
        else:         score -= 7; reasons.append(f'ADX {adx:.0f} 空頭趨勢確立')

    if ok(vr):
        if vr >= 1.5 and score > 50: score += 4
        elif vr < 0.5 and score != 50: score -= 3

    score = max(0, min(100, int(score)))

    if   score >= 70: sig, stxt = 'buy',  '可考慮買進'
    elif score >= 55: sig, stxt = 'hold', '持有/逢低布局'
    elif score >= 40: sig, stxt = 'wait', '觀望等待'
    else:             sig, stxt = 'sell', '注意風險'

    bpct = None
    if ok(bu) and ok(bl) and (bu - bl) > 0:
        bpct = max(0, min(100, int((close_val - bl) / (bu - bl) * 100)))

    return dict(
        score=score, sig=sig, stxt=stxt,
        reasons=reasons[:4],
        k=round(k, 1) if ok(k) else None,
        d=round(d, 1) if ok(d) else None,
        kd_signal=kd_signal,
        rsi=round(rsi, 1) if ok(rsi) else None,
        macd_bull=(macd > msig) if (ok(macd) and ok(msig)) else None,
        dev20=round(dev, 1) if ok(dev) else None,
        bpct=bpct,
        vol_ratio=round(vr, 2) if ok(vr) else None,
    )


def clamp_score(v):
    return int(max(0, min(100, round(v))))


def add_score_item(items, points, label):
    if points:
        items.append(dict(points=int(points), label=label))


def score_stock_fundamental(meta, items):
    score = 50
    notes = []
    used = 0

    pe = meta.get('trailing_pe')
    fpe = meta.get('forward_pe')
    revenue_growth = meta.get('finmind_ttm_revenue_yoy')
    if revenue_growth is None:
        revenue_growth = meta.get('revenue_growth')
    earnings_growth = meta.get('finmind_eps_yoy')
    if earnings_growth is None:
        earnings_growth = meta.get('earnings_growth')
    roe = meta.get('roe')
    gross = meta.get('finmind_gross_margin')
    if gross is None:
        gross = meta.get('gross_margin')
    debt = meta.get('debt_to_equity')
    growth_hint = max([v for v in [revenue_growth, earnings_growth] if v is not None], default=None)

    if pe is None:
        notes.append('PE待補')
    else:
        used += 1
        if pe <= 0:
            score -= 14
            add_score_item(items, -14, 'PE不適用或虧損')
        elif pe < 8:
            score -= 4
            add_score_item(items, -4, 'PE很低，需查是否景氣下滑')
        elif pe <= 25:
            score += 8
            add_score_item(items, 8, 'PE在合理區')
        elif pe <= 40:
            score += 2
            add_score_item(items, 2, 'PE偏高但尚可觀察')
        elif growth_hint is not None and growth_hint >= 0.25:
            score -= 3
            add_score_item(items, -3, 'PE高但成長仍快')
        else:
            score -= 12
            add_score_item(items, -12, 'PE偏高')

    if fpe is None:
        notes.append('Forward PE待補')
    else:
        used += 1
        if pe is not None and fpe < pe * 0.85:
            score += 6
            add_score_item(items, 6, 'Forward PE下降')
        elif pe is not None and fpe > pe * 1.20:
            score -= 5
            add_score_item(items, -5, 'Forward PE上升')

    if revenue_growth is None:
        notes.append('營收成長待補')
    else:
        used += 1
        if revenue_growth >= 0.20:
            score += 10
            add_score_item(items, 10, '營收成長強')
        elif revenue_growth >= 0.05:
            score += 5
            add_score_item(items, 5, '營收成長穩定')
        elif revenue_growth < 0:
            score -= 12
            add_score_item(items, -12, '營收成長轉弱')

    if earnings_growth is None:
        notes.append('EPS/獲利成長待補')
    else:
        used += 1
        if earnings_growth >= 0.20:
            score += 10
            add_score_item(items, 10, 'EPS/獲利成長強')
        elif earnings_growth >= 0.05:
            score += 5
            add_score_item(items, 5, 'EPS/獲利成長穩定')
        elif earnings_growth < 0:
            score -= 12
            add_score_item(items, -12, 'EPS/獲利成長轉弱')

    if roe is None:
        notes.append('ROE待補')
    else:
        used += 1
        if roe >= 0.20:
            score += 10
            add_score_item(items, 10, 'ROE高')
        elif roe >= 0.10:
            score += 4
            add_score_item(items, 4, 'ROE尚可')
        elif roe < 0.05:
            score -= 8
            add_score_item(items, -8, 'ROE偏低')

    if gross is None:
        notes.append('毛利率待補')
    else:
        used += 1
        if gross >= 0.40:
            score += 6
            add_score_item(items, 6, '毛利率高')
        elif gross < 0.15:
            score -= 6
            add_score_item(items, -6, '毛利率偏低')

    if debt is not None:
        used += 1
        if debt <= 50:
            score += 4
            add_score_item(items, 4, '負債比壓力較低')
        elif debt >= 150:
            score -= 8
            add_score_item(items, -8, '負債比偏高')

    if used < 4:
        notes.append('基本面欄位不足')
    return clamp_score(score), notes, used


def score_etf_quality(meta, items, ticker):
    score = 55
    notes = []
    used = 0
    expense = meta.get('expense_ratio')
    expense_label = 'ETF費用率'
    if (
        expense is None
        and meta.get('official_fee_annualized_estimate') is not None
        and not meta.get('official_fee_stale')
    ):
        expense = meta.get('official_fee_annualized_estimate')
        expense_label = '官方費用年化估算'
    elif expense is None and meta.get('official_fee_annualized_estimate') is not None:
        notes.append('官方費用資料偏舊，未納入ETF費用評分')
    premium = meta.get('premium_discount')
    assets = meta.get('total_assets')
    div_yield = meta.get('dividend_yield')
    top10_weight = meta.get('top10_weight')
    top1_weight = meta.get('top1_weight')

    if expense is None:
        notes.append('費用率待補')
    else:
        used += 1
        if expense <= 0.20:
            score += 10
            add_score_item(items, 10, f'{expense_label}低')
        elif expense <= 0.50:
            score += 5
            add_score_item(items, 5, f'{expense_label}尚可')
        elif expense >= 0.80:
            score -= 8
            add_score_item(items, -8, f'{expense_label}偏高')

    if premium is None:
        notes.append('折溢價待補')
    else:
        used += 1
        ap = abs(premium)
        if ap <= 0.5:
            score += 8
            add_score_item(items, 8, '折溢價貼近淨值')
        elif ap <= 1.5:
            score -= 3
            add_score_item(items, -3, '折溢價需觀察')
        else:
            score -= 12
            add_score_item(items, -12, '折溢價偏大')

    if assets is None:
        notes.append('基金規模待補')
    else:
        used += 1
        if assets >= 10_000_000_000:
            score += 6
            add_score_item(items, 6, 'ETF規模較大')
        elif assets < 100_000_000:
            score -= 8
            add_score_item(items, -8, 'ETF規模偏小')

    if div_yield is None:
        notes.append('配息殖利率待補')
    else:
        used += 1
        profile = get_profile(ticker)
        if profile['role'] == '現金流' and 3 <= div_yield <= 8:
            score += 4
            add_score_item(items, 4, '配息殖利率符合現金流用途')
        elif div_yield > 10:
            score -= 5
            add_score_item(items, -5, '殖利率過高需查配息來源')

    if top10_weight is None:
        notes.append('成分股集中度待補')
    else:
        used += 1
        if top1_weight is not None and top1_weight >= 40:
            score -= 8
            add_score_item(items, -8, '第一大持股占比很高')
        elif top10_weight >= 70:
            score -= 6
            add_score_item(items, -6, '前十大持股集中')
        elif top10_weight <= 35:
            score += 5
            add_score_item(items, 5, '成分股較分散')

    if used < 3:
        notes.append('ETF專用資料不足')
    return clamp_score(score), notes, used


def apply_factor_framework(ticker, a, ext, meta=None):
    """把技術指標轉成新手能理解的因子分數。

    這裡刻意避免「RSI 高就一定危險」的粗暴判斷：
    強勢趨勢中 RSI 70-80 可能是動能延續；只有搭配趨勢轉弱、KD偏空、
    或成交量/波動異常時，才提高風險。
    """
    items = []
    ma_align = ext['ma_align']
    rsi = a['rsi']
    vr = a['vol_ratio']
    w_pct = ext['w_pct']
    vol = ext['volatility']
    max_dd = ext.get('max_drawdown')
    beta = ext.get('beta')
    kd_bull = bool(a['k'] is not None and a['d'] is not None and a['k'] > a['d'])
    macd_bull = bool(a['macd_bull'])
    is_etf = is_etf_like(ticker)
    meta = meta or {}
    a['meta'] = meta

    trend = 50
    if ma_align == '多頭排列':
        trend += 24
        add_score_item(items, 24, '均線多頭排列')
    elif ma_align == '空頭排列':
        trend -= 24
        add_score_item(items, -24, '均線空頭排列')
    else:
        add_score_item(items, 0, '均線盤整')

    if a['macd_bull'] is not None:
        if macd_bull:
            trend += 18
            add_score_item(items, 18, 'MACD 多頭')
        else:
            trend -= 18
            add_score_item(items, -18, 'MACD 空頭')

    if ext['ytd'] is not None:
        if ext['ytd'] > 20:
            trend += 6
            add_score_item(items, 6, '今年報酬強勢')
        elif ext['ytd'] < -15:
            trend -= 8
            add_score_item(items, -8, '今年報酬偏弱')
    trend = clamp_score(trend)

    momentum = 50
    if kd_bull:
        momentum += 10
        add_score_item(items, 10, a.get('kd_signal') or 'KD偏多')
    elif a['k'] is not None and a['d'] is not None:
        momentum -= 10
        add_score_item(items, -10, a.get('kd_signal') or 'KD偏空')

    if rsi is not None:
        if 45 <= rsi <= 65:
            momentum += 10
            add_score_item(items, 10, 'RSI 健康區間')
        elif 65 < rsi <= 80:
            if trend >= 70:
                momentum += 12
                add_score_item(items, 12, 'RSI 偏高但趨勢強')
            else:
                momentum -= 10
                add_score_item(items, -10, 'RSI 偏高且趨勢不足')
        elif rsi > 80:
            if trend >= 75 and kd_bull:
                momentum += 5
                add_score_item(items, 5, 'RSI 鈍化，仍屬強勢')
            else:
                momentum -= 18
                add_score_item(items, -18, 'RSI 過熱')
        elif rsi < 30:
            momentum -= 6
            add_score_item(items, -6, 'RSI 弱勢超賣')
    momentum = clamp_score(momentum)

    volume = 50
    if vr is not None:
        if vr >= 1.5 and trend >= 65:
            volume += 20
            add_score_item(items, 20, '放量配合趨勢')
        elif vr >= 1.2:
            volume += 10
            add_score_item(items, 10, '成交量放大')
        elif vr < 0.5:
            volume -= 15
            add_score_item(items, -15, '成交量明顯不足')
        elif vr < 0.8:
            volume -= 6
            add_score_item(items, -6, '成交量偏低')
    volume = clamp_score(volume)

    position = 50
    if w_pct < 25:
        position += 25
        add_score_item(items, 25, '52週位置偏低')
    elif w_pct < 45:
        position += 12
        add_score_item(items, 12, '52週位置合理偏低')
    elif w_pct > 92:
        if trend >= 75 and momentum >= 65:
            position -= 6
            add_score_item(items, -6, '接近52週高點但趨勢延續')
        else:
            position -= 22
            add_score_item(items, -22, '接近52週高點')
    elif w_pct > 80:
        position -= 10
        add_score_item(items, -10, '52週位置偏高')
    position = clamp_score(position)

    risk = 75
    if vol is not None:
        if vol < 15:
            risk += 10
            add_score_item(items, 10, '波動率低')
        elif vol > 45:
            risk -= 28
            add_score_item(items, -28, '波動率很高')
        elif vol > 30:
            risk -= 15
            add_score_item(items, -15, '波動率偏高')
    if max_dd is not None:
        if max_dd <= -40:
            risk -= 20
            add_score_item(items, -20, '歷史最大回撤很深')
        elif max_dd <= -25:
            risk -= 12
            add_score_item(items, -12, '歷史回撤偏大')
        elif max_dd > -15:
            risk += 8
            add_score_item(items, 8, '歷史回撤較淺')
    if beta is not None:
        if beta > 1.35:
            risk -= 8
            add_score_item(items, -8, 'Beta 高於大盤')
        elif beta < 0.75:
            risk += 6
            add_score_item(items, 6, 'Beta 低於大盤')
    if w_pct > 90 and rsi is not None and rsi > 75 and not kd_bull:
        risk -= 18
        add_score_item(items, -18, '高檔動能轉弱')
    risk = clamp_score(risk)

    data_notes = ['價格、成交量與技術資料可用']
    if ext.get('market_status') == '開盤中':
        data_notes.append('盤中資料，技術指標會變動')
    if 'TWSE STOCK_DAY_ALL' in str(ext.get('quote_note', '')):
        data_notes.append('TWSE盤後日資料優先')
    if meta.get('twse_validation_note') and '盤中' in str(meta.get('twse_validation_note')):
        data_notes.append('盤中優先Yahoo報價，TWSE作正式收盤參考')
    elif meta.get('twse_validation_note'):
        data_notes.append('TWSE價格尺度驗證未通過，改用Yahoo報價')
    if meta.get('finmind_available'):
        data_notes.append('FinMind台股基本面/籌碼可用')
    if meta.get('finmind_errors'):
        data_notes.append('部分FinMind欄位暫時抓不到')
    if 'Delayed' in str(ext.get('quote_note', '')):
        data_notes.append('延遲報價')
    if max_dd is None:
        data_notes.append('最大回撤資料不足')
    if beta is None:
        data_notes.append('Beta資料不足')
    if ext.get('price_basis_adjusted'):
        data_notes.append('已用還原價格避免分割失真')

    if is_etf:
        fundamental, quality_notes, meta_used = score_etf_quality(meta, items, ticker)
        data_notes.extend(quality_notes)
        if meta.get('official_etf_available'):
            data_notes.append('ETF官方基本資料可用')
        if meta.get('official_nav_date') or meta.get('official_nav') is not None:
            data_notes.append('官方ETF每日淨值可用')
        if meta.get('official_fee_total_rate') is not None:
            data_notes.append('官方ETF費用資料可用')
        if meta.get('official_fee_stale'):
            data_notes.append('官方ETF費用資料偏舊，僅供參考')
        if meta_used >= 3:
            data_notes.append('費用率/NAV/規模等ETF資料可用')
        if meta.get('official_dividend_12m_amount') is not None:
            data_notes.append('官方ETF配息資料可用')
        if ext.get('total_return_3y') is not None:
            data_notes.append('Adj Close含息估算可用')
        else:
            data_notes.append('含息總報酬待補')
        if ext.get('tracking_error') is not None:
            if ext.get('tracking_mode') == 'tracking':
                data_notes.append('簡易追蹤差可用')
            else:
                data_notes.append('指數對照偏離可用，非正式追蹤誤差')
        else:
            data_notes.append('正式追蹤誤差待補')
        confidence = 54 + min(24, meta_used * 6)
        if meta.get('official_etf_available'):
            confidence += 8
        confidence -= 4
    else:
        fundamental, quality_notes, meta_used = score_stock_fundamental(meta, items)
        data_notes.extend(quality_notes)
        if meta_used >= 4:
            data_notes.append('PE/營收/EPS/ROE等基本面可用')
        confidence = 42 + min(30, meta_used * 5)
        if meta.get('finmind_available'):
            confidence += 8

    if not meta.get('info_available'):
        confidence -= 10
        data_notes.append('Yahoo基本資料暫時抓不到')
    if max_dd is None:
        confidence -= 8
    if beta is None:
        confidence -= 6
    confidence = clamp_score(confidence)

    if is_etf:
        weights = dict(trend=0.25, momentum=0.15, volume=0.10, position=0.10, risk=0.20, fundamental=0.20)
        total = (
            trend * weights['trend'] +
            momentum * weights['momentum'] +
            volume * weights['volume'] +
            position * weights['position'] +
            risk * weights['risk'] +
            fundamental * weights['fundamental']
        )
    else:
        weights = dict(trend=0.25, momentum=0.15, volume=0.10, position=0.10, risk=0.20, fundamental=0.20)
        total = (
            trend * weights['trend'] +
            momentum * weights['momentum'] +
            volume * weights['volume'] +
            position * weights['position'] +
            risk * weights['risk'] +
            fundamental * weights['fundamental']
        )

    a['factor_scores'] = dict(
        trend=trend,
        momentum=momentum,
        volume=volume,
        position=position,
        risk=risk,
        fundamental=fundamental,
        confidence=confidence,
    )
    a['data_notes'] = data_notes[:6]
    a['score_items'] = sorted(items, key=lambda x: abs(x['points']), reverse=True)[:6]
    a['score'] = clamp_score(total)

    if a['score'] >= 75:
        a['sig'], a['stxt'] = 'buy', '適合分批'
    elif a['score'] >= 60:
        a['sig'], a['stxt'] = 'hold', '持續觀察/正常扣款'
    elif a['score'] >= 45:
        a['sig'], a['stxt'] = 'wait', '偏中性，等更好位置'
    else:
        a['sig'], a['stxt'] = 'sell', '風險偏高'
    return a


def factor_score_html(a, ticker):
    fs = a.get('factor_scores')
    if not fs:
        return ''
    rows = [
        ('趨勢', fs['trend']),
        ('動能', fs['momentum']),
        ('成交量', fs['volume']),
        ('位置', fs['position']),
        ('風險', fs['risk']),
    ]
    rows.append(('ETF資料' if is_etf_like(ticker) else '基本面', fs['fundamental']))

    def color(v):
        return '#1D9E75' if v >= 70 else '#185FA5' if v >= 55 else '#BA7517' if v >= 40 else '#D85A30'

    factors = ''.join(
        f'<div class="factor"><span>{name}</span><b style="color:{color(val)}">{val}</b>'
        f'<i><em style="width:{val}%;background:{color(val)}"></em></i></div>'
        for name, val in rows
    )
    changes = ''.join(
        f'<li><span style="color:{("#1D9E75" if item["points"] > 0 else "#D85A30")}">'
        f'{item["points"]:+d}</span>{item["label"]}</li>'
        for item in a.get('score_items', [])
    )
    confidence = fs['confidence']
    conf_text = confidence_text(confidence)
    return (
        f'<div class="factor-box">'
        f'<div class="factor-title"><b>因子分數</b><span>信心度：{conf_text}</span></div>'
        f'<div class="factor-grid">{factors}</div>'
        f'<ul class="score-change">{changes}</ul>'
        f'</div>'
    )


def get_recommendations(a, ext):
    score    = a['score']
    rsi      = a['rsi']
    w_pct    = ext['w_pct']
    ma_align = ext['ma_align']
    vr       = a['vol_ratio']
    vr_text  = f'{vr:.1f}倍' if vr is not None else '資料不足'
    fs       = a.get('factor_scores', {})
    strong_trend = fs.get('trend', 0) >= 70 and ma_align == '多頭排列'
    hot_price = (rsi is not None and rsi > 72) or w_pct > 90
    risk_score = fs.get('risk', 50)

    if hot_price and strong_trend:
        rsi_text = f'RSI {rsi:.0f}' if rsi is not None else 'RSI 資料不足'
        trade = ('強勢但不追高', 'hold',
                 f'{rsi_text}，價格位置 {w_pct:.0f}%。這比較像強勢趨勢，不是單純危險；適合小額分批或等拉回，不建議一次重押。')
    elif hot_price:
        rsi_text = f'RSI {rsi:.0f}' if rsi is not None else 'RSI 資料不足'
        trade = ('短線偏熱，等回檔', 'wait',
                 f'{rsi_text} 或價格已接近52週高點（{w_pct:.0f}%）。追高風險大，建議定期小額即可，不要一次重壓。')
    elif score >= 70 and ma_align == '多頭排列' and vr is not None and vr >= 1.2:
        trade = ('現在可以買進', 'buy',
                 f'多項指標偏多，趨勢向上，成交量 {vr:.1f} 倍確認。技術面訊號明確，可考慮進場。')
    elif score >= 70 and ma_align != '空頭排列':
        trade = ('可考慮買進', 'buy',
                 f'技術指標偏多，趨勢正確。成交量為{vr_text}，建議小量試單，觀察量能是否跟進。')
    elif score >= 65:
        trade = ('可小量試單', 'hold',
                 '部分指標偏多但尚未完全確認。可先小量進場，等待訊號更明確後再加碼，設好停損點。')
    elif (rsi is not None and rsi > 68) or w_pct > 85:
        rsi_text = f'RSI {rsi:.0f}' if rsi is not None else 'RSI 資料不足'
        trade = ('短線偏熱，等回檔', 'wait',
                 f'{rsi_text} 偏高或已接近52週高點（{w_pct:.0f}%）。追高風險大，建議等拉回整理後再進場。')
    elif score >= 45:
        trade = ('等待更好時機', 'wait',
                 '指標偏中性，尚無明確買訊。建議等待KD黃金交叉、RSI回到健康區間後再考慮進場。')
    else:
        trade = ('目前不建議買進', 'sell',
                 f'多項技術指標偏空（健康分數{score}）。建議等待落底訊號，趨勢確認轉多後再考慮進場。')

    if risk_score < 45:
        dca = ('小額觀察，不加碼', 'wait',
               f'風險分數偏低（{risk_score}），可能是波動或歷史回撤太大。就算位置看起來不高，也先不要把它當便宜貨重押。')
    elif w_pct < 25 and ma_align in ['多頭排列', '盤整中']:
        dca = ('加碼好時機', 'buy',
               f'價格在52週相對低位（{w_pct:.0f}%），趨勢尚穩。定期定額加碼的好機會，可適度增加金額。')
    elif w_pct < 40 and ma_align == '多頭排列':
        dca = ('可略為加碼', 'hold',
               f'價格偏低（{w_pct:.0f}%）且趨勢向上，可適度增加本月扣款金額，有助降低長期持有成本。')
    elif w_pct > 85 and strong_trend:
        dca = ('持續扣款，不加碼', 'hold',
               f'價格偏高（{w_pct:.0f}%）但趨勢仍強。長期定期定額可照常，小白不需要因為高檔就停扣。')
    elif w_pct > 85:
        dca = ('正常扣款，勿加碼', 'wait',
               f'價格已接近52週高點（{w_pct:.0f}%）。按原計畫正常扣款即可，此時加碼成本偏高不划算。')
    elif ma_align == '空頭排列':
        dca = ('考慮暫停扣款', 'sell',
               '趨勢轉為空頭排列，建議暫停或減少定期定額金額，等待趨勢回穩後再繼續。')
    else:
        dca = ('繼續正常扣款', 'hold',
               f'趨勢穩定，價格在合理區間（{w_pct:.0f}%）。按原計畫繼續定期定額，無需特別調整。')

    return {'trade': trade, 'dca': dca}


# =============================================================
# SVG 迷你走勢圖
# =============================================================

def sparkline(prices, w=220, h=44):
    vals = prices.dropna().tail(60).values
    if len(vals) < 3:
        return ''
    mn, mx = float(vals.min()), float(vals.max())
    rng = mx - mn if mx != mn else 1
    pts = ' '.join(
        f'{i / (len(vals)-1) * w:.1f},{h - (v-mn)/rng*(h-4)-2:.1f}'
        for i, v in enumerate(vals)
    )
    fp  = f'0,{h} {pts} {w},{h}'
    up  = vals[-1] >= vals[0]
    lc  = '#1D9E75' if up else '#D85A30'
    fc  = 'rgba(29,158,117,0.1)' if up else 'rgba(216,90,48,0.1)'
    return (f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" style="display:block;width:100%">'
            f'<polygon points="{fp}" fill="{fc}"/>'
            f'<polyline points="{pts}" fill="none" stroke="{lc}" '
            f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
            f'</svg>')


# =============================================================
# 色彩設定
# =============================================================

SCORE_COL = {'buy': '#1D9E75', 'hold': '#185FA5', 'wait': '#BA7517', 'sell': '#D85A30'}
BADGE = {
    'buy':  ('#0F6E56', 'rgba(29,158,117,0.12)'),
    'hold': ('#185FA5', 'rgba(24,95,165,0.12)'),
    'wait': ('#854F0B', 'rgba(186,117,23,0.12)'),
    'sell': ('#993C1D', 'rgba(216,90,48,0.12)'),
}


# =============================================================
# HTML 元件
# =============================================================

def idx_card(ticker, name, price, chg, inverse=False, note=''):
    up   = chg >= 0
    good = (not up) if inverse else up
    col  = '#1D9E75' if good else '#D85A30'
    arr  = '▲' if up else '▼'
    sign = '+' if chg >= 0 else ''
    return (f'<div class="ic"><div class="ic-l">{name}</div>'
            f'<div class="ic-v">{price:,.2f}</div>'
            f'<div class="ic-c" style="color:{col}">{arr} {sign}{chg:.2f}%</div>'
            f'<div class="ic-note">{h(note)}</div></div>')


def metric_tile(label, value, hint=''):
    return (
        f'<div class="metric-tile">'
        f'<span>{h(label)}</span><b>{h(value)}</b>'
        f'<small>{h(hint)}</small></div>'
    )


def dividend_composition_text(meta):
    parts = []
    labels = [
        ('股利', meta.get('official_dividend_equity_income_pct')),
        ('利息', meta.get('official_dividend_interest_income_pct')),
        ('平準金', meta.get('official_dividend_equalization_pct')),
        ('資本利得', meta.get('official_dividend_capital_gain_pct')),
        ('其他', meta.get('official_dividend_other_income_pct')),
    ]
    for label, value in labels:
        v = safe_num(value)
        if v is not None and v > 0:
            parts.append(f'{label}{v:.0f}%')
    return ' / '.join(parts[:3]) if parts else 'N/A'


def metadata_detail_html(ticker, a, ext=None):
    meta = a.get('meta') or {}
    ext = ext or {}
    if not meta or (not meta.get('info_available') and not meta.get('finmind_available') and not meta.get('official_etf_available')):
        return (
            f'<div class="detail-box">'
            f'<div class="detail-title">基本資料詳情</div>'
            f'<div class="detail-note">Yahoo 基本資料暫時抓不到，所以這張卡只能先看價格、趨勢與風險。不要把它當完整買賣依據。</div>'
            f'</div>'
        )

    if is_etf_like(ticker):
        premium = meta.get('premium_discount')
        premium_text = 'N/A' if premium is None else f'{premium:+.2f}%'
        premium_hint = '接近 0% 代表成交價貼近淨值'
        if premium is not None and abs(premium) > 1.5:
            premium_hint = '偏離淨值較多，小白不要急著追'
        if meta.get('official_premium_discount') is not None:
            premium_hint = '用公開NAV與市價估算'

        fee_value = fmt_expense_pct(meta.get('expense_ratio'))
        fee_hint = '內扣成本，越低越好'
        if (
            meta.get('expense_ratio') is None
            and meta.get('official_fee_annualized_estimate') is not None
            and not meta.get('official_fee_stale')
        ):
            fee_value = fmt_optional_pct(meta.get('official_fee_annualized_estimate'))
            fee_hint = '官方費用年化估算，非保證全年'
        elif meta.get('expense_ratio') is None and meta.get('official_fee_annualized_estimate') is not None:
            fee_value = 'N/A'
            fee_hint = '官方費用資料偏舊，先不當正式費用率'

        price_return_label = '3年還原價報酬' if ext.get('price_basis_adjusted') else '3年價格報酬'
        price_return_hint = '使用還原價格，避免分割造成高低點失真' if ext.get('price_basis_adjusted') else '只看市價，不含完整配息'
        tracking_label = ext.get('tracking_label') or '正式追蹤差'
        tracking_hint = ext.get('tracking_hint') or f'對 {ext.get("tracking_benchmark") or "基準待補"}，非正式追蹤誤差'
        if ext.get('tracking_error') is not None and ext.get('tracking_benchmark'):
            tracking_hint = f'對 {ext.get("tracking_benchmark")}；{tracking_hint}'
        div_yield = meta.get('official_dividend_yield_12m')
        div_yield_hint = '用官方近12月配息/市價估算'
        if div_yield is None:
            div_yield = meta.get('dividend_yield')
            div_yield_hint = '配息是現金流，不等於保證賺錢'

        tile_items = [
            metric_tile('費用率', fee_value, fee_hint),
            metric_tile('折溢價', premium_text, premium_hint),
            metric_tile('配息殖利率', f'{div_yield:.1f}%' if div_yield is not None else 'N/A', div_yield_hint),
            metric_tile('基金規模', fmt_compact(meta.get('total_assets')), '太小要小心流動性'),
            metric_tile('NAV淨值', fmt_plain_num(meta.get('nav_price'), 2), 'ETF一單位的參考淨值'),
            metric_tile(price_return_label, f'{ext["price_return_3y"]:+.1f}%' if ext.get('price_return_3y') is not None else 'N/A', price_return_hint),
            metric_tile('3年含息估算', f'{ext["total_return_3y"]:+.1f}%' if ext.get('total_return_3y') is not None else 'N/A', '用 Yahoo Adj Close 估算'),
            metric_tile(tracking_label, f'{ext["tracking_error"]:.2f}%' if ext.get('tracking_error') is not None else 'N/A', tracking_hint),
            metric_tile('前十大占比', f'{meta["top10_weight"]:.1f}%' if meta.get('top10_weight') is not None else 'N/A', '越高代表越集中'),
            metric_tile('最大產業', f'{meta["top_sector"]["label"]} {meta["top_sector"]["weight"]:.1f}%' if meta.get('top_sector') else 'N/A', '看是否過度集中單一產業'),
        ]
        if ticker in TW_ETFS or meta.get('official_etf_available'):
            fee_period = f'{meta.get("official_fee_year") or ""}/{meta.get("official_fee_month") or ""}'.strip('/')
            fee_period_hint = f'{fee_period or "資料月份待補"} 合計費用率'
            if meta.get('official_fee_stale'):
                fee_period_hint += '，資料偏舊僅供參考'
            official_items = [
                metric_tile('官方費用月', fmt_optional_pct(meta.get('official_fee_total_rate')), fee_period_hint),
                metric_tile('官方NAV日期', meta.get('official_nav_date') or 'N/A', '每日淨值資料日期'),
                metric_tile('近12月配息', fmt_plain_num(meta.get('official_dividend_12m_amount'), 2), f'{int(meta.get("official_dividend_12m_count") or 0)} 次除息合計'),
                metric_tile('最近配息', fmt_plain_num(meta.get('official_latest_dividend_amount'), 3), f'除息日 {meta.get("official_latest_ex_dividend_date") or "N/A"}'),
                metric_tile('下次除息', meta.get('official_next_ex_dividend_date') or 'N/A', f'預告配息 {fmt_plain_num(meta.get("official_next_dividend_amount"), 3)}'),
                metric_tile('配息來源', dividend_composition_text(meta), '平準金或資本利得高時，不要只看殖利率'),
                metric_tile('追蹤指數', short_label(meta.get('official_etf_index_name') or meta.get('official_etf_benchmark_name'), 22), 'ETF真正跟著跑的標的'),
                metric_tile('基金類型', short_label(meta.get('official_etf_type'), 22), '看是台股、海外、債券或主題'),
                metric_tile('上市日期', meta.get('official_etf_listing_date') or 'N/A', '新ETF歷史資料較短'),
            ]
            tile_items = tile_items[:1] + official_items + tile_items[1:]
        tiles = ''.join(tile_items)
        top_holdings = meta.get('top_holdings') or []
        holdings_html = ''
        if top_holdings:
            chips = ''.join(
                f'<span>{h(item["symbol"] or item["name"])} {item["weight"]:.1f}%</span>'
                for item in top_holdings[:5]
            )
            holdings_html = f'<div class="holding-chips"><b>前五大持股</b><div>{chips}</div></div>'
        note = 'ETF 要分開看：重點是成本、折溢價、規模、總報酬與成分股集中度；價格線只是其中一部分。'
        if meta.get('official_etf_available'):
            note += ' 台股 ETF 已補公開 ETF 基本資料、可取得的每日淨值與基金費用資料。'
        if meta.get('official_dividend_12m_amount') is not None:
            note += ' 配息欄位使用 TWSE ETF e添富公開配息清單；組成占比屬公告預估，不能當成未來保證。'
        if (safe_num(meta.get('official_dividend_equalization_pct')) or 0) > 20 or (safe_num(meta.get('official_dividend_capital_gain_pct')) or 0) > 20:
            note += ' 這檔近期配息含較多收益平準金或資本利得，小白不要只因殖利率高就買。'
        if meta.get('premium_discount') is None:
            note += ' 目前折溢價資料不足，買進前要再查發行商或證交所資料。'
        if meta.get('official_etf_errors'):
            note += ' 仍有部分官方欄位待補：' + '、'.join(meta.get('official_etf_errors')[:3]) + '。'
        if ext.get('tracking_error') is None:
            note += ' ' + (ext.get('tracking_hint') or '正式追蹤誤差仍需發行商或指數資料確認。')
        elif ext.get('tracking_mode') != 'tracking':
            note += ' 指數對照偏離不是正式追蹤誤差，只能當大方向參考。'
        return (
            f'<div class="detail-box">'
            f'<div class="detail-title">ETF 專用資料</div>'
            f'<div class="detail-grid">{tiles}</div>'
            f'{holdings_html}'
            f'<div class="detail-note">{h(note)}</div>'
            f'</div>'
        )

    revenue_value = meta.get('finmind_ttm_revenue_yoy')
    revenue_hint = 'FinMind近12個月營收YoY'
    if revenue_value is None:
        revenue_value = meta.get('revenue_growth')
        revenue_hint = 'Yahoo或可用營收成長'
    eps_growth = meta.get('finmind_eps_yoy')
    eps_growth_hint = 'FinMind最新季EPS YoY'
    if eps_growth is None:
        eps_growth = meta.get('earnings_growth')
        eps_growth_hint = 'Yahoo或可用獲利成長'
    gross_margin = meta.get('finmind_gross_margin')
    gross_hint = 'FinMind最新季毛利率'
    if gross_margin is None:
        gross_margin = meta.get('gross_margin')
        gross_hint = 'Yahoo或可用毛利率'

    tiles = ''.join([
        metric_tile('PE本益比', fmt_plain_num(meta.get('trailing_pe'), 1), '價格相對獲利，不能單獨看'),
        metric_tile('Forward PE', fmt_plain_num(meta.get('forward_pe'), 1), '市場預估未來獲利'),
        metric_tile('PB股價淨值比', fmt_plain_num(meta.get('pb'), 1), '金融股更常用'),
        metric_tile('月營收YoY', fmt_ratio_pct(meta.get('finmind_month_revenue_yoy')), f'{meta.get("finmind_revenue_month_label") or "最新月"} 的營收年增'),
        metric_tile('近12月營收YoY', fmt_ratio_pct(revenue_value), revenue_hint),
        metric_tile('近四季EPS', fmt_plain_num(meta.get('finmind_eps_ttm'), 2), 'FinMind近四季EPS合計'),
        metric_tile('EPS/獲利成長', fmt_ratio_pct(eps_growth), eps_growth_hint),
        metric_tile('ROE', fmt_ratio_pct(meta.get('roe')), '股東權益報酬率'),
        metric_tile('毛利率', fmt_ratio_pct(gross_margin), gross_hint),
        metric_tile('殖利率', f'{meta["dividend_yield"]:.1f}%' if meta.get('dividend_yield') is not None else 'N/A', '配息參考，不是主要買點'),
    ])
    chip_html = ''
    if meta.get('finmind_chip_date'):
        chip_html = (
            f'<div class="holding-chips"><b>三大法人籌碼（{h(meta.get("finmind_chip_date"))}）</b>'
            f'<div>'
            f'<span>外資 {h(fmt_net_lots(meta.get("finmind_foreign_net_buy")))}</span>'
            f'<span>投信 {h(fmt_net_lots(meta.get("finmind_investment_trust_net_buy")))}</span>'
            f'<span>自營 {h(fmt_net_lots(meta.get("finmind_dealer_net_buy")))}</span>'
            f'<span>合計 {h(fmt_net_lots(meta.get("finmind_institutional_net_buy")))}</span>'
            f'</div></div>'
        )
    warnings_list = []
    if revenue_value is not None and revenue_value < 0:
        warnings_list.append('營收成長轉弱，過去 PE 可能會失效。')
    if eps_growth is not None and eps_growth < 0:
        warnings_list.append('EPS/獲利成長轉弱，不能只看過去便宜。')
    recent_yoys = meta.get('finmind_recent_revenue_yoys') or []
    if len(recent_yoys) >= 3 and all(v < 0 for v in recent_yoys):
        warnings_list.append('近三個月營收年增都下滑，估值模型要降權。')
    if meta.get('trailing_pe') is not None and meta['trailing_pe'] > 45 and (revenue_value or 0) < 0.15:
        warnings_list.append('PE 偏高但成長沒有同步跟上，要降低追價衝動。')
    note = '個股要看三層：經營有沒有變好、獲利品質好不好、財務壓力大不大。'
    if meta.get('finmind_available'):
        note += ' 台股個股已補 FinMind 的 PE/PBR、月營收、EPS 與三大法人資料。'
    if meta.get('finmind_errors'):
        note += ' 仍有部分免費資料暫時抓不到，缺欄位不會被當成正常。'
    if warnings_list:
        note += ' 失效警告：' + ''.join(warnings_list)
    return (
        f'<div class="detail-box">'
        f'<div class="detail-title">個股基本面詳情</div>'
        f'<div class="detail-grid">{tiles}</div>'
        f'{chip_html}'
        f'<div class="detail-note">{h(note)}</div>'
        f'</div>'
    )


def stock_card(ticker, name, price, chg, hist_close, a, ext, rec):
    up  = chg >= 0
    pc  = '#1D9E75' if up else '#D85A30'
    arr = '▲' if up else '▼'
    sgn = '+' if chg >= 0 else ''
    sc  = a['score']
    sig = a['sig']
    sc_col       = SCORE_COL[sig]
    badge_tc, badge_bg = BADGE[sig]
    sp   = sparkline(hist_close)
    disp = ticker.replace('.TW', '')
    qt = f' · {ext.get("quote_time_text")}' if ext.get('quote_time_text') else ''
    price_caption = f'{ext.get("price_label", "最新收盤")} {ext.get("latest_date", "")}{qt} · {ext.get("market_status", "")}'

    kd_v = f"K{a['k']} / D{a['d']}" if a['k'] is not None else 'N/A'
    kd_c = '#1D9E75' if (a['k'] and a['d'] and a['k'] > a['d']) else '#D85A30'
    kd_s = a.get('kd_signal') or (('KD偏多' if (a['k'] and a['d'] and a['k'] > a['d']) else 'KD偏空') if a['k'] else '')

    rsi_v = str(a['rsi']) if a['rsi'] is not None else 'N/A'
    rsi_c = ('#D85A30' if (a['rsi'] and a['rsi'] > 70)
             else '#1D9E75' if (a['rsi'] and a['rsi'] < 30) else '#6c757d')
    rsi_s = ('超買' if (a['rsi'] and a['rsi'] > 70)
             else '超賣' if (a['rsi'] and a['rsi'] < 30) else '正常')

    dv = a['dev20']
    if dv is not None:
        dv_v = f'+{dv:.1f}%' if dv >= 0 else f'{dv:.1f}%'
        dv_c = '#D85A30' if dv > 5 else '#1D9E75' if dv < -5 else '#6c757d'
        dv_s = '過熱' if dv > 5 else '偏低' if dv < -5 else '合理'
    else:
        dv_v, dv_c, dv_s = 'N/A', '#6c757d', ''

    if a['macd_bull'] is not None:
        macd_v = '多頭排列' if a['macd_bull'] else '空頭排列'
        macd_c = '#1D9E75' if a['macd_bull'] else '#D85A30'
    else:
        macd_v, macd_c = 'N/A', '#6c757d'

    boll_html = ''
    if a['bpct'] is not None:
        bp = a['bpct']
        bd = '#1D9E75' if bp < 30 else '#D85A30' if bp > 75 else '#185FA5'
        boll_html = (f'<div class="bw"><div class="bl-lbl">布林通道位置 '
                     f'<span style="color:{bd};font-weight:600">{bp}%</span></div>'
                     f'<div class="bb"><div class="bd" style="left:{bp}%;background:{bd}"></div></div>'
                     f'<div class="bt"><span style="color:#1D9E75">下軌</span>'
                     f'<span>中軌</span><span style="color:#D85A30">上軌</span></div></div>')

    w_pct = ext['w_pct']
    wd = '#1D9E75' if w_pct < 30 else '#D85A30' if w_pct > 75 else '#BA7517'
    basis_suffix = '（還原價格）' if ext.get('price_basis_adjusted') else ''
    week52_html = (
        f'<div class="bw" style="margin-bottom:10px">'
        f'<div class="bl-lbl">52週價格位置{basis_suffix} <span style="color:{wd};font-weight:600">{w_pct:.0f}%</span>'
        f'<span style="font-size:10px;color:var(--t2)"> （0%=52週最低，100%=52週最高）</span></div>'
        f'<div class="bb"><div class="bd" style="left:{w_pct:.0f}%;background:{wd}"></div></div>'
        f'<div class="bt"><span style="color:#1D9E75">低點 {ext["w_low"]:,.2f}</span>'
        f'<span style="color:#D85A30">高點 {ext["w_high"]:,.2f}</span></div></div>'
    )

    def pref_item(label, value, hint='', color='var(--t)'):
        val = f'{value:,.2f}' if isinstance(value, (int, float)) else str(value)
        return (
            f'<span class="pref-item"><span class="pref-l">{h(label)}</span>'
            f'<span class="pref-v" style="color:{color}">{h(val)}</span>'
            f'<span class="pref-hint">{h(hint)}</span></span>'
        )

    price_ref_html = ''
    if ext.get('reference_close'):
        pr  = '<div class="pref-row">'
        pr += pref_item(ext.get('reference_label', '資料日昨收'), ext['reference_close'], ext.get('reference_date') or ext.get('freshness_note', ''))
        if ext.get('session_open') is not None:
            pr += pref_item('資料日開盤', ext['session_open'], ext.get('latest_date', ''))
        else:
            pr += pref_item('資料日開盤', '尚無資料', ext.get('open_note', '尚未取得開盤價'))
        pr += pref_item(ext.get('price_label', '最新收盤/價'), ext.get('latest_close') or price, ext.get('quote_time_text') or ext.get('latest_date', ''))
        if ext.get('session_high') is not None:
            pr += pref_item('資料日最高', ext['session_high'], ext.get('latest_date', ''))
        if ext.get('session_low') is not None:
            pr += pref_item('資料日最低', ext['session_low'], ext.get('latest_date', ''))
        if ext['limit_up']:
            pr += pref_item('資料日漲停(+10%)', ext['limit_up'], '依資料日昨收與 tick size 估算', '#1D9E75')
            pr += pref_item('資料日跌停(-10%)', ext['limit_down'], '依資料日昨收與 tick size 估算', '#D85A30')
        pr += pref_item('資料來源/狀態', ext.get('quote_note') or 'Yahoo Finance', ext.get('current_market_status') or '')
        pr += '</div>'
        price_ref_html = pr

    ma_col = '#1D9E75' if ext['ma_align'] == '多頭排列' else '#D85A30' if ext['ma_align'] == '空頭排列' else '#BA7517'
    vr     = a['vol_ratio']
    vr_str = f'{vr:.1f}倍' if vr else 'N/A'
    vr_col = '#1D9E75' if (vr and vr >= 1.2) else '#D85A30' if (vr and vr < 0.5) else '#6c757d'
    vr_lbl = '放量' if (vr and vr >= 1.2) else '縮量' if (vr and vr < 0.8) else '正常'
    ytd_str = (f'+{ext["ytd"]:.1f}%' if ext['ytd'] and ext['ytd'] >= 0 else
               f'{ext["ytd"]:.1f}%'  if ext['ytd'] else 'N/A')
    ytd_col = '#1D9E75' if (ext['ytd'] and ext['ytd'] > 0) else '#D85A30' if (ext['ytd'] and ext['ytd'] < 0) else '#6c757d'
    vol_str = f'{ext["volatility"]:.0f}%' if ext['volatility'] else 'N/A'
    vol_lbl = ('低波動' if (ext['volatility'] and ext['volatility'] < 15) else
               '高波動' if (ext['volatility'] and ext['volatility'] > 35) else '中等')
    dd = ext.get('max_drawdown')
    dd_str = f'{dd:.0f}%' if dd is not None else 'N/A'
    dd_col = '#D85A30' if (dd is not None and dd <= -35) else '#BA7517' if (dd is not None and dd <= -20) else '#1D9E75'
    dd_lbl = ('跌幅很深' if (dd is not None and dd <= -35) else
              '需有心理準備' if (dd is not None and dd <= -20) else '相對溫和')
    beta = ext.get('beta')
    beta_str = f'{beta:.2f}' if beta is not None else 'N/A'
    beta_col = '#D85A30' if (beta is not None and beta > 1.3) else '#1D9E75' if (beta is not None and beta < 0.8) else '#6c757d'
    beta_lbl = ('比大盤更晃' if (beta is not None and beta > 1.3) else
                '比大盤穩' if (beta is not None and beta < 0.8) else '接近大盤')

    ext_html = (
        f'<div class="ig" style="margin-bottom:10px">'
        f'<div class="ic2"><div class="il">均線排列</div><div class="iv" style="color:{ma_col}">{ext["ma_align"]}</div></div>'
        f'<div class="ic2"><div class="il">成交量比</div><div class="iv" style="color:{vr_col}">{vr_str}</div>'
        f'<div class="is" style="color:{vr_col}">{vr_lbl}</div></div>'
        f'<div class="ic2"><div class="il">今年報酬</div><div class="iv" style="color:{ytd_col}">{ytd_str}</div></div>'
        f'<div class="ic2"><div class="il">波動率(年化)</div><div class="iv">{vol_str}</div>'
        f'<div class="is">{vol_lbl}</div></div>'
        f'<div class="ic2"><div class="il">最大回撤</div><div class="iv" style="color:{dd_col}">{dd_str}</div>'
        f'<div class="is" style="color:{dd_col}">{dd_lbl}</div></div>'
        f'<div class="ic2"><div class="il">Beta</div><div class="iv" style="color:{beta_col}">{beta_str}</div>'
        f'<div class="is" style="color:{beta_col}">{beta_lbl}</div></div>'
        f'</div>'
    )

    rl = ''.join(f'<li>{r}</li>' for r in a['reasons'])
    reasons_html = f'<ul class="rl">{rl}</ul>' if rl else ''

    trade_txt, trade_col, trade_reason = rec['trade']
    dca_txt,   dca_col,   dca_reason   = rec['dca']
    trade_tc, trade_bg = BADGE[trade_col]
    dca_tc,   dca_bg   = BADGE[dca_col]
    invest_plan = calc_investment_plan(ticker, price, a, ext, hist_close)
    price_zone = calc_price_zones(ticker, price, a, ext)
    store_buy_now_data(ticker, name, price, a, ext, rec, invest_plan, price_zone)
    invest_html = investment_plan_html(invest_plan)
    zone_html = price_zone_html(price_zone)
    decision_html = decision_card_html(ticker, a, ext, rec, invest_plan)
    detail_html = metadata_detail_html(ticker, a, ext)
    factor_html = factor_score_html(a, ticker)

    rec_html = (
        f'<div style="margin-bottom:8px">'
        f'<div style="background:{trade_bg};border:1px solid {trade_tc}44;border-radius:8px;padding:10px 12px;margin-bottom:8px">'
        f'<div style="font-size:10px;color:{trade_tc};font-weight:600;margin-bottom:3px">短線操作建議</div>'
        f'<div style="font-size:14px;font-weight:700;color:{trade_tc};margin-bottom:5px">{trade_txt}</div>'
        f'<div style="font-size:11px;color:var(--t);line-height:1.65">{trade_reason}</div>'
        f'</div>'
        f'<div style="background:{dca_bg};border:1px solid {dca_tc}44;border-radius:8px;padding:10px 12px">'
        f'<div style="font-size:10px;color:{dca_tc};font-weight:600;margin-bottom:3px">定期定額建議</div>'
        f'<div style="font-size:14px;font-weight:700;color:{dca_tc};margin-bottom:5px">{dca_txt}</div>'
        f'<div style="font-size:11px;color:var(--t);line-height:1.65">{dca_reason}</div>'
        f'</div></div>'
    )

    return (
        f'<div class="sc">'
        f'<div class="sh">'
        f'<div><div class="st">{disp}</div><div class="sn">{name}</div></div>'
        f'<div style="text-align:right">'
        f'<div class="sp" style="color:var(--t)">{price:,.2f}</div>'
        f'<div class="sc2" style="color:{pc}">{arr} {sgn}{chg:.2f}%</div>'
        f'<div class="price-caption">{h(price_caption)}</div>'
        f'</div></div>'
        f'<div class="spark">{sp}</div>'
        f'{price_ref_html}'
        f'{week52_html}'
        f'{zone_html}'
        f'<div class="sr"><span class="slbl">健康分數</span>'
        f'<div class="sbw"><div class="sbf" style="width:{sc}%;background:{sc_col}"></div></div>'
        f'<span class="sn2" style="color:{sc_col}">{sc}</span></div>'
        f'<div style="margin-bottom:10px">'
        f'<span class="sbadge" style="background:{badge_bg};color:{badge_tc}">{a["stxt"]}</span></div>'
        f'{decision_html}'
        f'{detail_html}'
        f'{factor_html}'
        f'<div class="ig">'
        f'<div class="ic2"><div class="il">KD 值</div><div class="iv" style="color:{kd_c}">{kd_v}</div>'
        f'<div class="is" style="color:{kd_c}">{kd_s}</div></div>'
        f'<div class="ic2"><div class="il">RSI</div><div class="iv" style="color:{rsi_c}">{rsi_v}</div>'
        f'<div class="is" style="color:{rsi_c}">{rsi_s}</div></div>'
        f'<div class="ic2"><div class="il">20日乖離率</div><div class="iv" style="color:{dv_c}">{dv_v}</div>'
        f'<div class="is" style="color:{dv_c}">{dv_s}</div></div>'
        f'<div class="ic2"><div class="il">MACD</div><div class="iv" style="color:{macd_c}">{macd_v}</div></div>'
        f'</div>'
        f'{boll_html}'
        f'{ext_html}'
        f'<hr class="cd">'
        f'{rec_html}'
        f'{invest_html}'
        f'<hr class="cd">'
        f'{reasons_html}'
        f'<div style="font-size:10px;color:var(--t2);margin-top:6px;line-height:1.5">'
        f'以上為規則化因子分析，僅供參考，不構成投資建議。投資有風險，請自行判斷。</div>'
        f'</div>'
    )


# =============================================================
# HTML 模板
# =============================================================

CSS = '''<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--t:#1a2035;--t2:#6c757d;--bg:#f5f7fa;--card:#fff;--card2:#f8f9fa;--bdr:rgba(0,0,0,0.07)}
body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--t);line-height:1.6}
.wrap{max-width:1200px;margin:0 auto;padding:0 16px}
.hdr{background:var(--card);border-bottom:1px solid var(--bdr);padding:13px 0;position:sticky;top:0;z-index:100}
.hi{display:flex;justify-content:space-between;align-items:center}
h1{font-size:19px;font-weight:700;color:var(--t);display:flex;align-items:center;gap:8px}
.dot{color:#1D9E75}
.ub{font-size:11px;color:var(--t2);background:var(--card2);padding:4px 10px;border-radius:20px;border:1px solid var(--bdr)}
.stl{font-size:11px;color:var(--t2);text-transform:uppercase;letter-spacing:0.08em;margin:18px 0 10px}
.igrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(155px,1fr));gap:10px;margin-bottom:4px}
.ic{background:var(--card);border:1px solid var(--bdr);border-radius:10px;padding:12px 14px}
.ic-l{font-size:11px;color:var(--t2);margin-bottom:4px}
.ic-v{font-size:18px;font-weight:700;color:var(--t);margin-bottom:3px;font-variant-numeric:tabular-nums}
.ic-c{font-size:12px;font-weight:600}
.ic-note{font-size:9px;color:var(--t2);line-height:1.25;margin-top:2px;min-height:12px}
.tnav{display:flex;gap:2px;border-bottom:1px solid rgba(0,0,0,0.1);margin:20px 0;flex-wrap:wrap}
.tb{background:transparent;border:none;padding:10px 16px 11px;font-size:13px;color:var(--t2);cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px;font-family:inherit;border-radius:8px 8px 0 0;transition:color .15s}
.tb:hover,.tb.on{color:var(--t)}
.tb.on{border-bottom-color:var(--t);font-weight:600}
.cgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px;padding-bottom:40px}
.tc{display:none}
.tc.on{display:block}
.sc{background:var(--card);border:1px solid var(--bdr);border-radius:12px;padding:16px}
.sh{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px}
.st{font-size:15px;font-weight:700;color:var(--t)}
.sn{font-size:11px;color:var(--t2);margin-top:2px}
.sp{font-size:19px;font-weight:700;font-variant-numeric:tabular-nums}
.sc2{font-size:12px;font-weight:600;margin-top:2px}
.price-caption{font-size:9px;color:var(--t2);line-height:1.35;margin-top:2px;max-width:120px}
.spark{margin-bottom:10px}
.pref-row{display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap}
.pref-item{background:var(--card2);border-radius:8px;padding:5px 10px;display:flex;flex-direction:column;gap:2px}
.pref-l{font-size:10px;color:var(--t2)}
.pref-v{font-size:12px;font-weight:600;color:var(--t);font-variant-numeric:tabular-nums}
.pref-hint{font-size:9px;color:var(--t2);line-height:1.25;max-width:120px}
.zone-box{background:var(--card2);border:1px solid var(--bdr);border-radius:8px;padding:10px 12px;margin:10px 0}
.zone-head{display:flex;justify-content:space-between;gap:8px;align-items:center;margin-bottom:8px}
.zone-head span{font-size:10px;color:var(--t2);font-weight:700}
.zone-head b{font-size:12px;color:var(--t)}
.zone-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px;margin-bottom:7px}
.zone-grid div{background:var(--card);border:1px solid var(--bdr);border-radius:7px;padding:7px 8px}
.zone-grid span{display:block;font-size:10px;color:var(--t2);margin-bottom:2px}
.zone-grid b{display:block;font-size:12px;color:var(--t);font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.zone-grid small{display:block;font-size:9px;color:var(--t2);line-height:1.35;margin-top:2px}
.zone-box p{font-size:11px;color:var(--t);line-height:1.55;margin-bottom:4px}
.zone-box>small{display:block;font-size:10px;color:var(--t2);line-height:1.5}
.zone-steps{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:7px;margin-top:8px}
.zone-steps div{background:var(--card);border:1px solid var(--bdr);border-radius:7px;padding:7px 8px}
.zone-steps span{display:block;font-size:10px;color:var(--t2);margin-bottom:2px}
.zone-steps b{display:block;font-size:12px;color:var(--t);font-variant-numeric:tabular-nums}
.zone-steps small{display:block;font-size:9px;color:var(--t2);line-height:1.35;margin-top:2px}
.zone-watch{border-top:1px solid var(--bdr);margin-top:8px;padding-top:7px}
.zone-watch b{display:block;font-size:10px;color:var(--t2);margin-bottom:4px}
.zone-watch ul{padding-left:16px}
.zone-watch li{font-size:10px;color:var(--t2);line-height:1.5;margin:2px 0}
.plan-box{background:var(--card2);border:1px solid var(--bdr);border-radius:8px;padding:10px 12px;margin:10px 0}
.plan-head{display:flex;justify-content:space-between;gap:8px;align-items:center;margin-bottom:6px}
.plan-head span{font-size:10px;color:var(--t2);font-weight:600}
.plan-head b{font-size:12px;color:var(--t)}
.plan-main{font-size:14px;font-weight:700;color:var(--t);margin-bottom:4px}
.plan-sub{font-size:11px;color:var(--t);line-height:1.55}
.plan-note{font-size:10px;color:var(--t2);line-height:1.55;margin-top:5px}
.plan-mini{display:flex;flex-direction:column;gap:2px;background:var(--card);border-radius:7px;padding:8px;margin-top:8px}
.plan-mini span{font-size:10px;color:var(--t2)}
.plan-mini b{font-size:13px;font-weight:700}
.plan-mini small{font-size:10px;color:var(--t2);line-height:1.45}
.decision-card{background:var(--card2);border:1px solid var(--bdr);border-radius:8px;padding:10px 12px;margin:10px 0}
.decision-head{display:flex;justify-content:space-between;gap:8px;align-items:center;margin-bottom:8px}
.decision-head span{font-size:10px;color:var(--t2);font-weight:700}
.decision-head b{font-size:11px;color:var(--t)}
.decision-row{display:grid;grid-template-columns:42px 1fr;gap:8px;margin-top:6px}
.decision-row span{font-size:10px;color:var(--t2);font-weight:700}
.decision-row p{font-size:11px;color:var(--t);line-height:1.6}
.decision-note{font-size:10px;color:var(--t2);line-height:1.5;margin-top:8px;border-top:1px solid var(--bdr);padding-top:7px}
.detail-box{background:var(--card2);border:1px solid var(--bdr);border-radius:8px;padding:10px 12px;margin:10px 0}
.detail-title{font-size:12px;font-weight:700;color:var(--t);margin-bottom:8px}
.detail-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:6px}
.metric-tile{background:var(--card);border:1px solid var(--bdr);border-radius:7px;padding:7px 8px;min-width:0}
.metric-tile span{display:block;font-size:10px;color:var(--t2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.metric-tile b{display:block;font-size:13px;color:var(--t);font-variant-numeric:tabular-nums;line-height:1.35;overflow-wrap:anywhere}
.metric-tile small{display:block;font-size:9px;color:var(--t2);line-height:1.35;margin-top:2px}
.detail-note{font-size:10px;color:var(--t2);line-height:1.55;margin-top:8px}
.holding-chips{margin-top:8px;border-top:1px solid var(--bdr);padding-top:8px}
.holding-chips b{display:block;font-size:10px;color:var(--t2);margin-bottom:5px}
.holding-chips div{display:flex;flex-wrap:wrap;gap:5px}
.holding-chips span{font-size:10px;color:var(--t);background:var(--card);border:1px solid var(--bdr);border-radius:999px;padding:3px 7px;line-height:1.35}
.intro-card{margin:14px 0}
.intro-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px;margin-top:10px}
.intro-grid div{background:var(--card2);border-radius:8px;padding:10px}
.intro-grid span{display:block;font-size:10px;color:var(--t2);margin-bottom:3px}
.intro-grid b{display:block;font-size:16px;color:var(--t);font-variant-numeric:tabular-nums}
.intro-grid small{display:block;font-size:10px;color:var(--t2);line-height:1.45;margin-top:3px}
.intro-note{font-size:11px;color:var(--t2);line-height:1.6;margin-top:9px}
.dca-tool{margin:14px 0}
.buy-tool{margin:14px 0}
.tool-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}
.tool-head p{font-size:11px;color:var(--t2);line-height:1.6;margin-top:4px}
.temp-pill{font-size:20px;font-weight:800;color:var(--t);background:var(--card2);border:1px solid var(--bdr);border-radius:8px;padding:8px 14px;min-width:58px;text-align:center}
.dca-controls{display:grid;grid-template-columns:1.4fr 1fr 1fr;gap:8px;margin-top:12px}
.dca-controls label{background:var(--card2);border:1px solid var(--bdr);border-radius:8px;padding:8px 10px;display:flex;flex-direction:column;gap:4px}
.dca-controls span{font-size:10px;color:var(--t2)}
.dca-controls input,.dca-controls select{width:100%;background:transparent;border:none;color:var(--t);font:inherit;font-size:14px;font-weight:700;outline:none}
.dca-result{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:8px;margin-top:10px}
.buy-result{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:8px;margin-top:10px}
.dca-stat{background:var(--card2);border-radius:8px;padding:10px}
.dca-stat span{display:block;font-size:10px;color:var(--t2);margin-bottom:3px}
.dca-stat b{display:block;font-size:16px;color:var(--t);font-variant-numeric:tabular-nums}
.dca-stat small{display:block;font-size:10px;color:var(--t2);line-height:1.45;margin-top:3px}
.buy-summary{grid-column:1/-1;background:var(--card2);border:1px solid var(--bdr);border-radius:8px;padding:10px}
.buy-summary span{display:block;font-size:10px;color:var(--t2);margin-bottom:3px}
.buy-summary b{display:block;font-size:16px;color:var(--t)}
.buy-summary small{display:block;font-size:10px;color:var(--t2);line-height:1.45;margin-top:3px}
.buy-decision{grid-column:1/-1;margin:0}
.tool-note{font-size:10px;color:var(--t2);line-height:1.55;margin-top:8px}
.strategy-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px;margin-top:10px}
.strategy-grid div{background:var(--card2);border-radius:8px;padding:10px}
.strategy-grid span{display:block;font-size:10px;color:var(--t2);margin-bottom:3px}
.strategy-grid b{display:block;font-size:14px;color:var(--t)}
.strategy-grid small{display:block;font-size:10px;color:var(--t2);line-height:1.45;margin-top:3px}
.panic-box{border-top:1px solid var(--bdr);margin-top:12px;padding-top:10px;display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:8px}
.panic-box>b{grid-column:1/-1;font-size:13px;color:var(--t)}
.panic-box div{display:grid;grid-template-columns:46px 1fr;gap:8px;align-items:flex-start;background:var(--card2);border-radius:8px;padding:9px 10px}
.panic-box span{font-size:13px;font-weight:800;color:#D85A30;font-variant-numeric:tabular-nums}
.panic-box p{font-size:10px;color:var(--t2);line-height:1.55}
.methodology{margin:14px 0}
.method-lead{font-size:12px;color:var(--t);line-height:1.65;margin-top:8px}
.method-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:8px;margin-top:10px}
.method-grid div{background:var(--card2);border-radius:8px;padding:10px}
.method-grid b{display:block;font-size:12px;color:var(--t);margin-bottom:4px}
.method-grid p{font-size:11px;color:var(--t2);line-height:1.65}
.method-note{font-size:10px;color:var(--t2);line-height:1.6;margin-top:9px}
.public-check{margin:14px 0}
.check-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px;margin-top:10px}
.check-grid div{background:var(--card2);border:1px solid var(--bdr);border-radius:8px;padding:10px}
.check-grid span{display:block;font-size:10px;color:var(--t2);margin-bottom:3px}
.check-grid b{display:block;font-size:14px;color:var(--t);font-variant-numeric:tabular-nums}
.check-grid small{display:block;font-size:10px;color:var(--t2);line-height:1.5;margin-top:3px}
.theme-radar{margin:14px 0}
.theme-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:8px;margin-top:10px}
.theme-card{background:var(--card2);border:1px solid var(--bdr);border-radius:8px;padding:10px}
.theme-title{font-size:12px;font-weight:700;color:var(--t);margin-bottom:4px}
.theme-card p{font-size:10px;color:var(--t2);line-height:1.5;margin-bottom:7px}
.theme-watch{display:flex;flex-wrap:wrap;gap:5px}
.theme-watch span{font-size:10px;color:var(--t);background:var(--card);border:1px solid var(--bdr);border-radius:999px;padding:3px 7px;line-height:1.3}
.order-wrap{overflow-x:auto;margin-top:10px;border:1px solid var(--bdr);border-radius:8px;background:var(--card2)}
.order-table{width:100%;border-collapse:collapse;min-width:960px}
.order-table th,.order-table td{padding:9px 10px;border-bottom:1px solid var(--bdr);font-size:11px;text-align:left;vertical-align:top}
.order-table th{font-size:10px;color:var(--t2);font-weight:700;background:rgba(0,0,0,0.03)}
.order-table td b{display:block;font-size:12px;color:var(--t);margin-bottom:2px}
.order-table td small{display:block;font-size:10px;color:var(--t2);line-height:1.45}
.order-action{display:inline-flex;padding:3px 8px;border-radius:999px;font-size:10px;font-weight:700;white-space:nowrap}
.order-ok{color:#0F6E56;background:rgba(29,158,117,0.12)}
.order-wait{color:#854F0B;background:rgba(186,117,23,0.12)}
.order-stop{color:#993C1D;background:rgba(216,90,48,0.12)}
.order-filter{display:grid;grid-template-columns:repeat(3,minmax(150px,1fr));gap:8px;margin-top:10px}
.order-filter label{display:grid;gap:4px;font-size:10px;color:var(--t2)}
.order-filter input,.order-filter select{border:1px solid var(--bdr);background:var(--card2);color:var(--t);border-radius:8px;padding:8px;font-size:13px}
.factor-box{background:var(--card2);border:1px solid var(--bdr);border-radius:8px;padding:10px;margin-bottom:10px}
.factor-title{display:flex;justify-content:space-between;gap:8px;align-items:center;margin-bottom:8px}
.factor-title b{font-size:12px;color:var(--t)}
.factor-title span{font-size:10px;color:var(--t2)}
.factor-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:7px}
.factor{display:grid;grid-template-columns:42px 28px 1fr;gap:6px;align-items:center}
.factor span{font-size:10px;color:var(--t2)}
.factor b{font-size:12px;text-align:right;font-variant-numeric:tabular-nums}
.factor i{height:5px;background:rgba(0,0,0,0.08);border-radius:3px;overflow:hidden}
.factor em{display:block;height:100%;border-radius:3px}
.score-change{list-style:none;margin-top:8px;display:grid;gap:3px}
.score-change li{font-size:10px;color:var(--t2);display:flex;gap:6px;align-items:center}
.score-change span{min-width:28px;font-weight:700;font-variant-numeric:tabular-nums}
.sr{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.slbl{font-size:11px;color:var(--t2);white-space:nowrap}
.sbw{flex:1;height:6px;background:rgba(0,0,0,0.08);border-radius:3px;overflow:hidden}
.sbf{height:100%;border-radius:3px}
.sn2{font-size:13px;font-weight:700;min-width:26px;text-align:right}
.sbadge{display:inline-block;padding:4px 12px;border-radius:20px;font-size:12px;font-weight:600;margin-bottom:10px}
.ig{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:8px}
.ic2{background:var(--card2);border-radius:8px;padding:8px 10px}
.il{font-size:10px;color:var(--t2);margin-bottom:2px}
.iv{font-size:13px;font-weight:600}
.is{font-size:10px;margin-top:1px}
.bw{margin-bottom:8px}
.bl-lbl{font-size:10px;color:var(--t2);margin-bottom:5px}
.bb{position:relative;height:8px;background:rgba(0,0,0,0.08);border-radius:4px}
.bd{position:absolute;width:14px;height:14px;border-radius:50%;top:-3px;transform:translateX(-50%);border:2.5px solid var(--card)}
.bt{display:flex;justify-content:space-between;font-size:10px;color:var(--t2);margin-top:4px}
.cd{border:none;border-top:1px solid var(--bdr);margin:8px 0}
.rl{list-style:none}
.rl li{font-size:11px;color:#495057;padding:2px 0 2px 12px;position:relative}
.rl li::before{content:"•";position:absolute;left:2px;color:var(--t2)}
.nd{text-align:center;padding:40px;color:var(--t2);font-size:14px}
footer{background:var(--card);border-top:1px solid var(--bdr);padding:20px 0;margin-top:10px}
footer p{font-size:12px;color:#adb5bd;margin-bottom:3px;text-align:center}
@media(prefers-color-scheme:dark){
  :root{--t:#e6edf3;--t2:#7d8590;--bg:#0d1117;--card:#161b22;--card2:#21262d;--bdr:rgba(255,255,255,0.07)}
  .rl li{color:#b0bec5}
  .tb.on{border-bottom-color:var(--t)}
  .tnav{border-bottom-color:rgba(255,255,255,0.1)}
  .sbw,.bb{background:rgba(255,255,255,0.1)}
}
@media(max-width:600px){
  .cgrid{grid-template-columns:1fr}
  h1{font-size:16px}
  .tb{padding:8px 10px;font-size:12px}
  .tool-head{flex-direction:column}
  .dca-controls{grid-template-columns:1fr}
  .order-filter{grid-template-columns:1fr}
  .panic-box div{grid-template-columns:42px 1fr}
}
</style>'''

JS_CODE = '''<script>
function showTab(id){
  document.querySelectorAll('.tc').forEach(function(s){s.classList.remove('on')});
  document.querySelectorAll('.tb').forEach(function(b){b.classList.remove('on')});
  var el=document.getElementById(id); if(el) el.classList.add('on');
  var btn=document.querySelector('[data-tab="'+id+'"]'); if(btn) btn.classList.add('on');
}
document.addEventListener('DOMContentLoaded',function(){showTab('tw-stocks')});
</script>'''


def build_html(idx_html, tw_s, tw_e, us_s, us_e, bonds, update_time, market_ctx=None):
    return (
        f'<!DOCTYPE html>\n<html lang="zh-Hant">\n<head>\n'
        f'<meta charset="UTF-8">\n'
        f'<meta name="viewport" content="width=device-width,initial-scale=1.0">\n'
        f'<title>智慧投資分析 | {update_time}</title>\n'
        f'{CSS}\n</head>\n<body>\n'
        f'<header class="hdr"><div class="wrap"><div class="hi">'
        f'<h1><span class="dot">◆</span> 智慧投資分析</h1>'
        f'<span class="ub">更新：{update_time} 台灣時間</span>'
        f'</div></div></header>\n'
        f'<main class="wrap">\n'
        f'<p class="stl">大盤指數 / 報價時間</p>\n'
        f'<div class="igrid">{idx_html}</div>\n'
        f'{newbie_summary_html(market_ctx)}\n'
        f'{dca_simulator_html(market_ctx)}\n'
        f'{buy_now_tool_html(market_ctx)}\n'
        f'{daily_order_overview_html(market_ctx)}\n'
        f'{theme_radar_html()}\n'
        f'{methodology_html()}\n'
        f'{public_readiness_html(update_time, market_ctx)}\n'
        f'<nav class="tnav">\n'
        f'<button class="tb" data-tab="tw-stocks" onclick="showTab(\'tw-stocks\')">台股個股</button>\n'
        f'<button class="tb" data-tab="tw-etfs"   onclick="showTab(\'tw-etfs\')">台股 ETF</button>\n'
        f'<button class="tb" data-tab="us-stocks" onclick="showTab(\'us-stocks\')">美股個股</button>\n'
        f'<button class="tb" data-tab="us-etfs"   onclick="showTab(\'us-etfs\')">美股 ETF</button>\n'
        f'<button class="tb" data-tab="bonds"     onclick="showTab(\'bonds\')">債券/商品</button>\n'
        f'</nav>\n'
        f'<div id="tw-stocks" class="tc"><div class="cgrid">{tw_s}</div></div>\n'
        f'<div id="tw-etfs"   class="tc"><div class="cgrid">{tw_e}</div></div>\n'
        f'<div id="us-stocks" class="tc"><div class="cgrid">{us_s}</div></div>\n'
        f'<div id="us-etfs"   class="tc"><div class="cgrid">{us_e}</div></div>\n'
        f'<div id="bonds"     class="tc"><div class="cgrid">{bonds}</div></div>\n'
        f'</main>\n'
        f'<footer><div class="wrap">\n'
        f'<p>資料來源：TWSE 盤後公開資料 / TWSE ETF e添富配息清單 / FinMind 免費公開資料 / 政府資料開放平臺基金資料 / Yahoo Finance 免費公開資料 | 分析方式：規則化因子評分（無任何 AI API）</p>\n'
        f'<p>以上分析僅供參考，不構成投資建議。投資有風險，請自行判斷。</p>\n'
        f'</div></footer>\n'
        f'{JS_CODE}\n</body>\n</html>'
    )


def report_is_healthy(html):
    card_count = html.count('<div class="sc"')
    failure_count = html.count('資料暫時無法取得') + html.count('處理失敗')
    if card_count < 30:
        return False, f'卡片數過少：{card_count}'
    if failure_count > 5:
        return False, f'資料失敗卡過多：{failure_count}'
    return True, f'卡片 {card_count}，失敗卡 {failure_count}'


# =============================================================
# 資料擷取
# =============================================================

def safe_download(tickers, period='1y'):
    results = {}
    if not tickers:
        return results
    try:
        raw = yf.download(
            tickers if len(tickers) > 1 else tickers[0],
            period=period, progress=False, auto_adjust=False
        )
        if raw.empty:
            raise ValueError('空資料')
        if len(tickers) == 1:
            close = raw['Close'].dropna() if 'Close' in raw.columns else pd.Series(dtype=float)
            if len(close) >= 20:
                results[tickers[0]] = raw
        elif isinstance(raw.columns, pd.MultiIndex):
            lvl = 0 if raw.columns.get_level_values(0)[0] in tickers else 1
            for tk in tickers:
                try:
                    df = raw.xs(tk, level=lvl, axis=1)
                    close = df['Close'].dropna() if 'Close' in df.columns else pd.Series(dtype=float)
                    if not df.empty and len(close) >= 20:
                        results[tk] = df
                except Exception:
                    pass
    except Exception as e:
        print(f'    批次失敗（{e}），改逐一擷取...')

    missing = [tk for tk in tickers if tk not in results]
    for tk in missing:
        try:
            df = yf.download(tk, period=period, progress=False, auto_adjust=False)
            close = df['Close'].dropna() if (not df.empty and 'Close' in df.columns) else pd.Series(dtype=float)
            if not df.empty and len(close) >= 20:
                results[tk] = df
                print(f'    {tk} ✓')
        except Exception as ex:
            print(f'    {tk} ✗ {ex}')
        time.sleep(0.4)
    return results


def process_group(tickers_dict, period='3y'):
    data = safe_download(list(tickers_dict.keys()), period)
    cards = []
    for ticker, name in tickers_dict.items():
        if ticker not in data:
            cards.append(
                f'<div class="sc"><div class="nd">'
                f'{ticker.replace(".TW","")} {name}<br>資料暫時無法取得'
                f'</div></div>'
            )
            continue
        df = data[ticker]
        try:
            df_close_for_meta = df['Close'].squeeze().dropna() if 'Close' in df.columns else pd.Series(dtype=float)
            ref_last_for_meta = safe_num(df_close_for_meta.iloc[-1]) if len(df_close_for_meta) else None
            meta   = fetch_public_metadata(ticker, ref_last_for_meta)
            ohlc   = ohlc_context(df, ticker, meta)
            if not ohlc or ohlc.get('latest_close') is None:
                raise ValueError('OHLC資料不足')
            df_calc = df.copy()
            session_date = pd.Timestamp(ohlc['latest_date']) if ohlc.get('latest_date') and ohlc.get('latest_date') != 'N/A' else None
            session_exists = session_date is not None and any(index_to_date(idx) == session_date.date() for idx in df_calc.index)
            if session_date is not None and not session_exists:
                for col, key in [('Open', 'session_open'), ('High', 'session_high'), ('Low', 'session_low'), ('Close', 'latest_close')]:
                    if col in df_calc.columns and ohlc.get(key) is not None:
                        df_calc.loc[session_date, col] = ohlc[key]
                if 'Volume' in df_calc.columns and ohlc.get('twse_volume') is not None:
                    df_calc.loc[session_date, 'Volume'] = ohlc['twse_volume']
                if 'Adj Close' in df_calc.columns:
                    df_calc.loc[session_date, 'Adj Close'] = ohlc['latest_close']
                df_calc = df_calc.sort_index()
            else:
                latest_idx = df_calc.index[-1]
                if 'Close' in df_calc.columns and pd.isna(df_calc.loc[latest_idx, 'Close']):
                    df_calc.loc[latest_idx, 'Close'] = ohlc['latest_close']
                if 'Adj Close' in df_calc.columns and pd.isna(df_calc.loc[latest_idx, 'Adj Close']):
                    df_calc.loc[latest_idx, 'Adj Close'] = ohlc['latest_close']
            close  = df_calc['Close'].squeeze()
            adj_close = df_calc['Adj Close'].squeeze() if 'Adj Close' in df_calc.columns else None
            last_c = float(ohlc['latest_close'])
            close_basis, price_basis_note, price_basis_adjusted = build_price_basis(close, last_c, adj_close)
            df_analysis = apply_price_basis_to_ohlc(df_calc, close_basis)
            ind, _ = calc_indicators(df_analysis)
            c_vals = close_basis.dropna()
            total_basis = scale_to_latest_price(adj_close, last_c)
            store_dca_series(ticker, name, c_vals, total_basis, price_basis_note)
            prev_c = ohlc.get('previous_close') or last_c
            chg    = (last_c - prev_c) / prev_c * 100 if prev_c else 0
            meta   = fetch_public_metadata(ticker, last_c)
            a      = analyze(ind, last_c)
            ext    = calc_extended(close_basis, last_c, ohlc, ticker, adj_close, price_basis_note, price_basis_adjusted)
            a      = apply_factor_framework(ticker, a, ext, meta)
            rec    = get_recommendations(a, ext)
            cards.append(stock_card(ticker, name, last_c, chg, close_basis, a, ext, rec))
        except Exception as ex:
            print(f'    {ticker} 處理失敗：{ex}')
            cards.append(
                f'<div class="sc"><div class="nd">'
                f'{ticker.replace(".TW","")} {name}<br>處理失敗'
                f'</div></div>'
            )
    return '\n'.join(cards)


def raw_close_series(raw, ticker):
    if isinstance(raw.columns, pd.MultiIndex):
        try:
            return raw['Close'][ticker].dropna()
        except Exception:
            lvl = 0 if raw.columns.get_level_values(0)[0] == ticker else 1
            return raw.xs(ticker, level=lvl, axis=1)['Close'].dropna()
    return raw['Close'].dropna()


def calc_market_context(raw, quotes=None):
    quotes = quotes or {}
    try:
        tw = raw_close_series(raw, '^TWII')
        vix = raw_close_series(raw, '^VIX')
    except Exception:
        return dict(regime='資料不足', temperature='中性', advice='資料不足時不要因單日訊號重押。')
    if len(tw) < 60:
        return dict(regime='資料不足', temperature='中性', advice='資料不足時先照計畫小額分批。')

    tw_quote = quotes.get('^TWII') or {}
    vix_quote = quotes.get('^VIX') or {}
    last = tw_quote.get('quote_price') or float(tw.iloc[-1])
    ma20 = float(tw.rolling(20).mean().iloc[-1])
    ma60 = float(tw.rolling(60).mean().iloc[-1])
    ma20_prev = float(tw.rolling(20).mean().iloc[-10]) if len(tw) >= 70 else ma20
    period = min(126, len(tw))
    high = max(float(tw.tail(period).max()), float(last))
    low = min(float(tw.tail(period).min()), float(last))
    pos = (last - low) / (high - low) * 100 if high > low else 50
    vix_last = vix_quote.get('quote_price') or (float(vix.iloc[-1]) if len(vix) else 20)

    score = 50
    if last > ma20:
        score += 12
    else:
        score -= 12
    if last > ma60:
        score += 18
    else:
        score -= 18
    if ma20 > ma60 and ma20 >= ma20_prev:
        score += 14
    elif ma20 < ma60:
        score -= 14
    if vix_last < 18:
        score += 10
    elif vix_last > 25:
        score -= 15

    if score >= 70:
        regime = '多頭趨勢'
    elif score <= 35:
        regime = '偏空防守'
    else:
        regime = '震盪整理'

    if pos > 85 and vix_last < 22:
        temperature = '熱'
        advice = '定期定額可持續，但不建議單筆重押。'
    elif pos < 35 and vix_last > 22:
        temperature = '冷'
        advice = '市場偏恐慌，長期資金可分批，保留現金。'
    else:
        temperature = '中性'
        advice = '按月扣款即可，等明顯回檔再加碼。'

    return dict(
        regime=regime,
        temperature=temperature,
        advice=advice,
        score=clamp_score(score),
        position=round(pos, 1),
        vix=round(vix_last, 2),
    )


def fetch_indices():
    tickers = list(INDICES.keys())
    parts   = []
    market_ctx = dict(regime='資料不足', temperature='中性', advice='資料不足時先照計畫小額分批。')
    try:
        raw = yf.download(tickers, period='3y', progress=False, auto_adjust=False)
        index_quotes = {tk: fetch_metadata(tk) for tk in tickers}
        try:
            BENCHMARK_SERIES['TW'] = raw_close_series(raw, '^TWII')
            BENCHMARK_SERIES['US'] = raw_close_series(raw, '^GSPC')
            for key in INDICES:
                BENCHMARK_SERIES[key] = raw_close_series(raw, key)
        except Exception:
            pass
        market_ctx = calc_market_context(raw, index_quotes)
        for tk, (name, inverse) in INDICES.items():
            try:
                s = raw_close_series(raw, tk)
                if len(s) < 2:
                    continue
                meta = index_quotes.get(tk) or fetch_metadata(tk)
                price = meta.get('quote_price') or float(s.iloc[-1])
                prev  = meta.get('quote_previous_close') or float(s.iloc[-2])
                chg   = (price - prev) / prev * 100
                qtime = format_quote_time(meta)
                source = meta.get('quote_source') or '日線資料'
                note = f'{qtime} {source}'.strip()
                parts.append(idx_card(tk, name, price, chg, inverse, note))
            except Exception:
                pass
    except Exception as e:
        print(f'指數擷取失敗：{e}')
    return '\n'.join(parts), market_ctx


def prepare_dca_sim_data():
    # 讓首頁模擬器有較長歷史；失敗時仍可使用 process_group 已保存的 3 年資料。
    for tk in ['0050.TW', '006208.TW', '0056.TW', '00878.TW']:
        try:
            current = DCA_SERIES.get(tk, {}).get('points', [])
            if len(current) >= 1200:
                continue
            df = yf.download(tk, period='max', progress=False, auto_adjust=False)
            if df.empty or 'Close' not in df.columns:
                continue
            close = df['Close'].squeeze().dropna()
            adj = df['Adj Close'].squeeze().dropna() if 'Adj Close' in df.columns else None
            latest = safe_num(close.iloc[-1]) if len(close) else None
            total_basis = scale_to_latest_price(adj, latest)
            close_basis, price_basis_note, _ = build_price_basis(close, latest, adj)
            store_dca_series(tk, DCA_SIM_TICKERS.get(tk, tk), close_basis, total_basis, price_basis_note)
            time.sleep(0.3)
        except Exception as ex:
            print(f'    {tk} 模擬資料略過：{ex}')


# =============================================================
# 主程式
# =============================================================

def main():
    print('=' * 45)
    print('  智慧投資分析 v2 — 開始產生報告')
    print('=' * 45)
    update_time = datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y-%m-%d %H:%M')

    print('\n[1/6] 擷取大盤指數...')
    idx_html, market_ctx = fetch_indices()

    print('\n[2/6] 分析台股個股...')
    tw_s = process_group(TW_STOCKS)

    print('\n[3/6] 分析台股 ETF...')
    tw_e = process_group(TW_ETFS)

    print('\n[4/6] 分析美股個股...')
    us_s = process_group(US_STOCKS)

    print('\n[5/6] 分析美股 ETF...')
    us_e = process_group(US_ETFS)

    print('\n[6/6] 分析債券/商品...')
    bonds = process_group(BONDS)

    print('\n[補充] 準備定期定額模擬資料...')
    prepare_dca_sim_data()

    print('\n產生 index.html...')
    html = build_html(idx_html, tw_s, tw_e, us_s, us_e, bonds, update_time, market_ctx)
    healthy, health_note = report_is_healthy(html)
    if not healthy:
        if Path('index.html').exists():
            print(f'報告健康檢查未通過（{health_note}），保留既有 index.html，不覆蓋。')
            return
        raise RuntimeError(f'報告健康檢查未通過：{health_note}')
    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html)

    print(f'\n完成！（{update_time}，{health_note}）')
    print('=' * 45)


if __name__ == '__main__':
    main()

