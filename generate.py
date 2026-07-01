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
import re
import warnings
import time
from data_sources import fetch_finmind_public_stock_data, fetch_finmind_institutional_flow, fetch_tw_etf_public_data, fetch_twse_stock_day_all
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
    # AI 伺服器 / 資料中心供應鏈
    '2382.TW': '廣達',
    '3231.TW': '緯創',
    '6669.TW': '緯穎',
    '2356.TW': '英業達',
    '3017.TW': '奇鋐',
    # 電力、重電、電網與能源基建
    '2308.TW': '台達電',
    '1519.TW': '華城',
    '1513.TW': '中興電',
    '1504.TW': '東元',
    '1605.TW': '華新',
    '1609.TW': '大亞',
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
    'SMCI': 'Super Micro Computer',
    'DELL': 'Dell Technologies',
    'VRT':  'Vertiv',
    'ETN':  'Eaton',
    'GEV':  'GE Vernova',
    'CEG':  'Constellation Energy',
    'VST':  'Vistra',
}

US_ETFS = {
    'SPY':  '標普500 ETF',
    'QQQ':  '那斯達克100 ETF',
    'SOXX': '費城半導體 ETF',
    'IWM':  '羅素2000 ETF',
    'XLK':  '科技類股 ETF',
    'XLU':  '公用事業 ETF',
    'XLE':  '能源類股 ETF',
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
    '^N225': ('日經225',    False),
    '^HSI':  ('恒生指數',   False),
    'HSTECH.HK': ('恒生科技', False),
    '^KS11': ('KOSPI',      False),
    '^KQ11': ('KOSDAQ',     False),
    '^GSPC': ('標普500',    False),
    '^SOX':  ('費城半導體', False),
    '^VIX':  ('VIX恐慌',   True),
    '^IXIC': ('那斯達克',   False),
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


STOCK_PROFILES = {
    '2382.TW': dict(role='個股觀察', bucket='AI伺服器', base_ratio=0.0, note='AI伺服器ODM代表，景氣與大客戶訂單變化會放大波動。'),
    '3231.TW': dict(role='個股觀察', bucket='AI伺服器', base_ratio=0.0, note='伺服器與資料中心供應鏈，需觀察營收、毛利率與庫存循環。'),
    '6669.TW': dict(role='個股觀察', bucket='AI伺服器', base_ratio=0.0, note='AI伺服器高成長標的，波動與估值敏感度都高。'),
    '2356.TW': dict(role='個股觀察', bucket='AI伺服器', base_ratio=0.0, note='伺服器ODM與PC循環並存，不能只看單一題材。'),
    '3017.TW': dict(role='個股觀察', bucket='散熱/液冷', base_ratio=0.0, note='資料中心散熱與液冷題材，需留意訂單能見度與估值。'),
    '2308.TW': dict(role='個股觀察', bucket='電源/電力', base_ratio=0.0, note='電源管理、資料中心電源與電動車電源相關，適合觀察長期需求。'),
    '1519.TW': dict(role='個股觀察', bucket='重電/電網', base_ratio=0.0, note='重電與變壓器題材，需留意接單、交期與景氣循環。'),
    '1513.TW': dict(role='個股觀察', bucket='重電/電網', base_ratio=0.0, note='電網設備與重電題材，波動可能受政策與訂單消息影響。'),
    '1504.TW': dict(role='個股觀察', bucket='馬達/電氣設備', base_ratio=0.0, note='馬達與電氣設備代表，需同時看工業景氣與電力題材。'),
    '1605.TW': dict(role='個股觀察', bucket='電纜/能源基建', base_ratio=0.0, note='電線電纜與能源基建相關，需注意原物料價格與訂單。'),
    '1609.TW': dict(role='個股觀察', bucket='電纜/能源基建', base_ratio=0.0, note='電纜與綠電基建題材，需觀察獲利是否跟上題材熱度。'),
    'SMCI': dict(role='個股觀察', bucket='AI伺服器', base_ratio=0.0, note='AI伺服器高波動代表，財報、毛利率與供應鏈消息影響很大。'),
    'DELL': dict(role='個股觀察', bucket='AI伺服器', base_ratio=0.0, note='企業伺服器與AI伺服器需求觀察標的，但仍受PC與企業IT循環影響。'),
    'VRT': dict(role='個股觀察', bucket='資料中心電力/散熱', base_ratio=0.0, note='資料中心電力與散熱基建代表，需注意估值與訂單增速。'),
    'ETN': dict(role='個股觀察', bucket='電力設備', base_ratio=0.0, note='電力管理與基礎設施代表，可觀察資料中心用電與電網升級。'),
    'GEV': dict(role='個股觀察', bucket='能源設備', base_ratio=0.0, note='發電、電網與能源設備代表，需留意政策、訂單與估值。'),
    'CEG': dict(role='個股觀察', bucket='電力/核能', base_ratio=0.0, note='美國電力與核能題材，資料中心用電需求是觀察重點之一。'),
    'VST': dict(role='個股觀察', bucket='電力/發電', base_ratio=0.0, note='發電與電力需求題材，高波動時不適合新手重押。'),
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
    'XLU': dict(role='衛星主題', bucket='美股公用事業', base_ratio=0.08, note='美股公用事業ETF，可觀察電力需求與防禦型資產輪動。'),
    'XLE': dict(role='衛星主題', bucket='美股能源', base_ratio=0.08, note='美股能源ETF，受油氣價格、通膨與景氣循環影響。'),
}


NEWS_THEMES = [
    dict(theme='AI/半導體', words=['AI', '半導體', 'HPC', 'HBM', 'ASIC'], watch=['0052.TW', '00904.TW', '00881.TW', '2330.TW', '2317.TW', '2454.TW', '2449.TW']),
    dict(theme='AI伺服器/資料中心', words=['AI伺服器', '資料中心', 'ODM', '液冷', '散熱'], watch=['2382.TW', '3231.TW', '6669.TW', '2356.TW', '3017.TW', 'SMCI', 'DELL', 'VRT']),
    dict(theme='電力/重電/電網', words=['電力', '重電', '電網', '變壓器', '電纜'], watch=['2308.TW', '1519.TW', '1513.TW', '1504.TW', '1605.TW', '1609.TW', 'ETN', 'GEV']),
    dict(theme='能源/公用事業', words=['能源', '核電', '天然氣', '公用事業', '電力需求'], watch=['CEG', 'VST', 'XLU', 'XLE']),
    dict(theme='疫情/醫療防疫', words=['疫情', '口罩', '疫苗', '檢測', '醫療'], watch=[]),
    dict(theme='通膨/升息', words=['通膨', '升息', '殖利率', 'Fed'], watch=['2882.TW', '00679B.TW', '00720B.TW', 'XLE']),
    dict(theme='降息/景氣放緩', words=['降息', '景氣放緩', '衰退'], watch=['00679B.TW', '00720B.TW', '00751B.TW', 'XLU']),
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
    res['atr'] = atr
    res['atr_pct'] = atr / c.replace(0, np.nan) * 100
    res['adx'] = dx.rolling(14).mean()
    res['dip'] = dip
    res['dim'] = dim
    res['boll_width'] = (res['boll_u'] - res['boll_l']) / m20.replace(0, np.nan) * 100

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


def fmt_flow_amount(value, price):
    shares = safe_num(value)
    px = safe_num(price)
    if shares is None or px is None or px <= 0:
        return 'N/A'
    amount = shares * px
    if abs(amount) < 1:
        return '持平'
    side = '流入' if amount > 0 else '流出'
    amount_abs = abs(amount)
    if amount_abs >= 100_000_000:
        amount_text = f'{amount_abs / 100_000_000:.1f} 億元'
    else:
        amount_text = f'{amount_abs / 10_000:.0f} 萬元'
    return f'{side}約 {amount_text}（估）'


def fmt_flow_streak(value):
    v = safe_num(value)
    if v is None or abs(v) < 1:
        return '未連續'
    days = int(abs(v))
    return f'連買 {days} 日' if v > 0 else f'連賣 {days} 日'


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
    if ticker in STOCK_PROFILES:
        return STOCK_PROFILES[ticker]
    if ticker in TW_ETFS or ticker in US_ETFS or ticker in BONDS:
        return dict(role='觀察ETF', bucket='ETF', base_ratio=0.05, note='尚未設定細分類，先當觀察標的。')
    return dict(role='個股觀察', bucket='股票', base_ratio=0.0, note='新手不建議直接用定期定額重壓個股，先從ETF建立核心部位。')


def fetch_metadata(ticker, last_price=None):
    """抓取免費基本資料。失敗就回空 dict，避免硬湊假資料。"""
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


def apply_institutional_flow_metadata(ticker, meta):
    """補台股真實三大法人買賣超；美股不混用台股法人資料。"""
    if not is_tw_ticker(ticker):
        return meta
    code = ticker.replace('.TW', '')
    flow = fetch_finmind_institutional_flow(code)
    if not flow:
        return meta
    merged = dict(meta)
    merged.update(flow)
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
                return apply_institutional_flow_metadata(ticker, skipped)
        should_merge, note = should_merge_twse_quote(ticker, twse_quote)
        if not should_merge:
            skipped = dict(meta)
            skipped['twse_reference_date'] = twse_quote.get('twse_date')
            skipped['twse_reference_close'] = twse_quote.get('quote_price')
            skipped['twse_validation_note'] = note
            skipped['data_source_priority'] = '盤中Yahoo報價優先，TWSE盤後日資料保留為正式收盤參考'
            return apply_institutional_flow_metadata(ticker, apply_tw_etf_metadata(ticker, skipped, last_price))
        merged = dict(meta)
        merged.update(twse_quote)
        merged['data_source_priority'] = 'TWSE盤後資料優先，Yahoo補基本面/歷史資料'
        return apply_institutional_flow_metadata(ticker, apply_tw_etf_metadata(ticker, merged, merged.get('quote_price') or last_price))
    return apply_institutional_flow_metadata(ticker, apply_tw_etf_metadata(ticker, meta, last_price))


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
        eg = meta["earnings_growth"]
        if abs(eg) > 2.0:
            parts.append('EPS基期異常，已降權')
        else:
            parts.append(f'EPS/獲利成長 {eg * 100:.1f}%')
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


def chip_flow_judgment(meta, price=None):
    if not meta.get('finmind_chip_date'):
        return dict(
            available=False,
            state='法人資料待補',
            tone='neutral',
            score=50,
            summary='三大法人買賣超暫時抓不到，不能用資金流判斷。',
            action='回到價格區間、趨勢與基本面判斷。',
        )

    total_1d = safe_num(meta.get('finmind_institutional_net_buy')) or 0
    total_5d = safe_num(meta.get('finmind_institutional_net_buy_5d')) or 0
    total_20d = safe_num(meta.get('finmind_institutional_net_buy_20d')) or 0
    foreign_5d = safe_num(meta.get('finmind_foreign_net_buy_5d')) or 0
    trust_5d = safe_num(meta.get('finmind_investment_trust_net_buy_5d')) or 0
    dealer_5d = safe_num(meta.get('finmind_dealer_net_buy_5d')) or 0
    streak = safe_num(meta.get('finmind_institutional_streak')) or 0

    score = 50
    if total_1d > 0:
        score += 4
    elif total_1d < 0:
        score -= 4
    if total_5d > 0:
        score += 12
    else:
        score -= 12
    if total_20d > 0:
        score += 12
    else:
        score -= 12
    if foreign_5d > 0:
        score += 8
    else:
        score -= 6
    if trust_5d > 0:
        score += 8
    else:
        score -= 4
    if dealer_5d > 0:
        score += 3
    elif dealer_5d < 0:
        score -= 2
    if streak >= 3:
        score += 6
    elif streak <= -3:
        score -= 6
    score = clamp_score(score)

    if total_5d > 0 and total_20d > 0 and foreign_5d > 0 and trust_5d > 0:
        state, tone = '法人同步流入', 'positive'
        action = '資金流支持，但仍要看價格是否過熱。'
    elif total_5d > 0 and total_20d > 0:
        state, tone = '法人偏多', 'positive'
        action = '可列入分批觀察，不能單靠法人買超追價。'
    elif total_5d > 0 and total_20d <= 0:
        state, tone = '短線回補', 'mixed'
        action = '短線有資金回補，但中期還沒確認，適合觀察不重押。'
    elif total_5d <= 0 and total_20d > 0:
        state, tone = '短線降溫', 'mixed'
        action = '中期仍有資金，但短線轉弱，先等價格穩定。'
    elif total_5d < 0 and total_20d < 0:
        state, tone = '法人退潮', 'negative'
        action = '法人資金偏流出，不急著買；跌破支撐要降低風險。'
    else:
        state, tone = '資金分歧', 'neutral'
        action = '法人方向不明，回到基本面與價格區間。'

    summary = (
        f'近 5 日合計 {fmt_net_lots(total_5d)}、近 20 日 {fmt_net_lots(total_20d)}；'
        f'外資 5 日 {fmt_net_lots(foreign_5d)}，投信 5 日 {fmt_net_lots(trust_5d)}。'
    )
    if price is not None:
        summary += f' 近 5 日合計約當 {fmt_flow_amount(total_5d, price)}。'
    return dict(
        available=True,
        state=state,
        tone=tone,
        score=score,
        summary=summary,
        action=action,
        total_1d=total_1d,
        total_5d=total_5d,
        total_20d=total_20d,
        foreign_5d=foreign_5d,
        trust_5d=trust_5d,
        dealer_5d=dealer_5d,
        streak=streak,
    )


def institutional_flow_html(meta, price=None):
    chip = chip_flow_judgment(meta, price)
    if not chip['available']:
        return (
            f'<div class="chip-flow-box chip-neutral">'
            f'<div class="chip-flow-head"><div><span>法人資金流</span><b>{h(chip["state"])}</b></div>'
            f'<strong>待補</strong></div><p>{h(chip["summary"])}</p></div>'
        )
    tone = chip.get('tone', 'neutral')
    tiles = ''.join([
        metric_tile('1日合計', fmt_net_lots(chip.get('total_1d')), f'資料日 {meta.get("finmind_chip_date")}'),
        metric_tile('5日合計', fmt_net_lots(chip.get('total_5d')), fmt_flow_amount(chip.get('total_5d'), price)),
        metric_tile('20日合計', fmt_net_lots(chip.get('total_20d')), '中期資金方向'),
        metric_tile('外資5日', fmt_net_lots(chip.get('foreign_5d')), '外資偏中大型股'),
        metric_tile('投信5日', fmt_net_lots(chip.get('trust_5d')), '投信常影響台股主流題材'),
        metric_tile('自營5日', fmt_net_lots(chip.get('dealer_5d')), '自營商較偏短線'),
        metric_tile('連續狀態', fmt_flow_streak(chip.get('streak')), '連買/連賣越久越值得注意'),
        metric_tile('資金流分', f'{chip.get("score", 50):.0f}', '只是一個因子，不單獨決定買賣'),
    ])
    return (
        f'<div class="chip-flow-box chip-{h(tone)}">'
        f'<div class="chip-flow-head"><div><span>法人資金流</span><b>{h(chip["state"])}</b></div>'
        f'<strong>{chip.get("score", 50):.0f}</strong></div>'
        f'<p>{h(chip["summary"])}</p>'
        f'<div class="detail-grid chip-flow-grid">{tiles}</div>'
        f'<div class="detail-note">{h(chip["action"])} 資料來源：{h(meta.get("finmind_chip_source", "FinMind InstitutionalInvestorsBuySell"))}；買賣超為股數換算張數，金額為用目前價格粗估。</div>'
        f'</div>'
    )


def chip_flow_judgment(meta, price=None):
    if not meta.get('finmind_chip_date'):
        return dict(
            available=False,
            state='法人資料待補',
            tone='neutral',
            score=50,
            summary='三大法人買賣超暫時抓不到，不能用資金流判斷。',
            action='回到價格區間、趨勢與基本面判斷。',
        )

    total_1d = safe_num(meta.get('finmind_institutional_net_buy')) or 0
    total_5d = safe_num(meta.get('finmind_institutional_net_buy_5d')) or 0
    total_20d = safe_num(meta.get('finmind_institutional_net_buy_20d')) or 0
    foreign_5d = safe_num(meta.get('finmind_foreign_net_buy_5d')) or 0
    trust_5d = safe_num(meta.get('finmind_investment_trust_net_buy_5d')) or 0
    dealer_5d = safe_num(meta.get('finmind_dealer_net_buy_5d')) or 0
    streak = safe_num(meta.get('finmind_institutional_streak')) or 0

    score = 50
    score += 4 if total_1d > 0 else -4 if total_1d < 0 else 0
    score += 12 if total_5d > 0 else -12
    score += 12 if total_20d > 0 else -12
    score += 8 if foreign_5d > 0 else -6
    score += 8 if trust_5d > 0 else -4
    score += 3 if dealer_5d > 0 else -2 if dealer_5d < 0 else 0
    score += 6 if streak >= 3 else -6 if streak <= -3 else 0
    score = clamp_score(score)

    if total_5d > 0 and total_20d > 0 and foreign_5d > 0 and trust_5d > 0:
        state, tone = '法人同步流入', 'positive'
        action = '資金流是加分，但仍要確認價格沒有過熱。'
    elif total_5d > 0 and total_20d > 0:
        state, tone = '法人偏多', 'positive'
        action = '法人方向偏多，可以加分，但不能單靠買超進場。'
    elif total_5d > 0 and total_20d <= 0:
        state, tone = '短線回補', 'mixed'
        action = '短線買盤回來，中期尚未確認，不適合直接追高。'
    elif total_5d <= 0 and total_20d > 0:
        state, tone = '短線降溫', 'mixed'
        action = '中期仍有資金，但短線轉弱，要看是否守住支撐。'
    elif total_5d < 0 and total_20d < 0:
        state, tone = '法人退潮', 'negative'
        action = '法人資金流出時，不要因為價格看似便宜就急著攤平。'
    else:
        state, tone = '資金分歧', 'neutral'
        action = '法人方向不明，先看價格區間與基本面。'

    summary = (
        f'近 5 日合計{fmt_net_lots(total_5d)}，近 20 日{fmt_net_lots(total_20d)}；'
        f'外資 5 日{fmt_net_lots(foreign_5d)}，投信 5 日{fmt_net_lots(trust_5d)}。'
    )
    return dict(
        available=True,
        state=state,
        tone=tone,
        score=score,
        summary=summary,
        action=action,
        total_1d=total_1d,
        total_5d=total_5d,
        total_20d=total_20d,
        foreign_5d=foreign_5d,
        trust_5d=trust_5d,
        dealer_5d=dealer_5d,
        streak=streak,
    )


def flow_bar_html(label, value, max_abs):
    v = safe_num(value) or 0
    pct = 0 if not max_abs or max_abs <= 0 else min(100, abs(v) / max_abs * 100)
    cls = 'flow-in' if v > 0 else 'flow-out' if v < 0 else 'flow-flat'
    return (
        f'<div class="flow-bar-row {cls}">'
        f'<span>{h(label)}</span><div class="flow-track"><i style="width:{pct:.0f}%"></i></div>'
        f'<b>{h(fmt_net_lots(v))}</b></div>'
    )


def institutional_flow_html(meta, price=None):
    chip = chip_flow_judgment(meta, price)
    if not chip['available']:
        return (
            f'<div class="chip-flow-box chip-neutral">'
            f'<div class="chip-flow-head"><div><span>法人資金流</span><b>{h(chip["state"])}</b></div>'
            f'<strong>待補</strong></div><p>{h(chip["summary"])}</p></div>'
        )
    tone = chip.get('tone', 'neutral')
    max_abs = max(
        abs(safe_num(chip.get('total_5d')) or 0),
        abs(safe_num(chip.get('foreign_5d')) or 0),
        abs(safe_num(chip.get('trust_5d')) or 0),
        abs(safe_num(chip.get('dealer_5d')) or 0),
        1,
    )
    bars = ''.join([
        flow_bar_html('合計 5 日', chip.get('total_5d'), max_abs),
        flow_bar_html('外資 5 日', chip.get('foreign_5d'), max_abs),
        flow_bar_html('投信 5 日', chip.get('trust_5d'), max_abs),
        flow_bar_html('自營 5 日', chip.get('dealer_5d'), max_abs),
    ])
    tiles = ''.join([
        metric_tile('資料日', meta.get('finmind_chip_date') or 'N/A', 'FinMind 公開資料'),
        metric_tile('1 日合計', fmt_net_lots(chip.get('total_1d')), '最新一個交易日'),
        metric_tile('5 日合計', fmt_net_lots(chip.get('total_5d')), '短線資金方向'),
        metric_tile('20 日合計', fmt_net_lots(chip.get('total_20d')), '中期資金方向'),
        metric_tile('約當金額', fmt_flow_amount(chip.get('total_5d'), price), '5 日買賣超股數 × 最新價格'),
        metric_tile('連續狀態', fmt_flow_streak(chip.get('streak')), '連買/連賣越久越值得注意'),
        metric_tile('資金流分', f'{chip.get("score", 50):.0f}', '只是一個因子，不單獨決定買賣'),
    ])
    return (
        f'<div class="chip-flow-box chip-{h(tone)}">'
        f'<div class="chip-flow-head"><div><span>法人資金流</span><b>{h(chip["state"])}</b></div>'
        f'<strong>{chip.get("score", 50):.0f}</strong></div>'
        f'<p>{h(chip["summary"])}</p>'
        f'<div class="chip-flow-visual">{bars}</div>'
        f'<div class="detail-grid chip-flow-grid">{tiles}</div>'
        f'<details class="chip-flow-formula"><summary>約當金額怎麼算？</summary>'
        f'<p>估算方式：近 5 日法人買賣超股數 × 最新價格。它不是交易所提供的精準成交金額，所以只能看方向與量級，不要拿來當精準成本。</p></details>'
        f'<div class="detail-note">{h(chip["action"])} 資料來源：{h(meta.get("finmind_chip_source", "FinMind InstitutionalInvestorsBuySell"))}；買賣超為股數換算張數。</div>'
        f'</div>'
    )


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


def price_zone_html(zone):
    if not zone:
        return ''
    tone = zone.get('tone', 'neutral')
    px = safe_num(zone.get('price'))
    chase = safe_num(zone.get('chase_limit'))
    stop = safe_num(zone.get('stop_line'))
    marker = 50
    if px is not None and stop is not None and chase is not None and chase > stop:
        marker = max(3, min(97, (px - stop) / (chase - stop) * 100))

    buy_low = fmt_zone_price(zone.get('buy_low'))
    buy_high = fmt_zone_price(zone.get('buy_high'))
    chase_text = fmt_zone_price(zone.get('chase_limit'))
    stop_text = fmt_zone_price(zone.get('failure_line') or zone.get('stop_line'))
    early_text = fmt_zone_price(zone.get('early_warning_line') or zone.get('reduce_watch'))
    strong_text = f'{buy_high} ~ {chase_text}'
    risk_text = f'>{chase_text}'

    step_html = ''.join(
        f'<div><span>{h(title)}</span><b>{h(value)}</b><small>{h(note)}</small></div>'
        for title, value, note in zone.get('buy_steps', [])
    )
    sell_step_html = ''.join(
        f'<div><span>{h(title)}</span><b>{h(value)}</b><small>{h(note)}</small></div>'
        for title, value, note in zone.get('sell_steps', [])
    )
    watch_html = ''.join(f'<li>{h(x)}</li>' for x in zone.get('watch_items', [])[:5])

    return (
        f'<div class="zone-box zone-{h(tone)} zone-v2 price-position-card">'
        f'<div class="zone-head price-position-head"><span>價格位置圖</span><b>{h(zone["status"])}</b></div>'
        f'<div class="price-position-lead">'
        f'<div><span>目前解讀</span><strong>{h(zone["summary"])}</strong><p>{h(zone["action"])}</p></div>'
        f'<em>{h(zone.get("entry_mode", "先觀察"))}</em>'
        f'</div>'
        f'<div class="price-map" aria-label="價格位置圖">'
        f'<div class="price-map-rail">'
        f'<div class="price-map-segments">'
        f'<span class="seg-reset"></span><span class="seg-entry"></span><span class="seg-strong"></span><span class="seg-risk"></span>'
        f'</div>'
        f'<i class="price-map-marker" style="left:{marker:.0f}%"><span>現價</span></i>'
        f'</div>'
        f'<div class="price-map-labels">'
        f'<div><span>重看線</span><b>{h(stop_text)}</b><small>跌破先停看</small></div>'
        f'<div><span>承接區</span><b>{h(zone["range_text"])}</b><small>比較舒服的分批區</small></div>'
        f'<div><span>強勢區</span><b>{h(strong_text)}</b><small>趨勢還在但不重押</small></div>'
        f'<div><span>追價風險</span><b>{h(risk_text)}</b><small>上方只追蹤不衝動</small></div>'
        f'</div>'
        f'</div>'
        f'<div class="price-decision-grid">'
        f'<div><span>入場判斷</span><b>{h(zone.get("entry_mode", "先觀察"))}</b><small>{h(zone.get("entry_reason", ""))}</small></div>'
        f'<div><span>賣出預警</span><b>{h(zone.get("sell_status", "看預警"))}</b><small>{h(zone.get("sell_text", ""))}</small></div>'
        f'<div><span>改變看法</span><b>低於 {h(stop_text)}</b><small>{h(zone.get("fail_text", ""))}</small></div>'
        f'</div>'
        f'<details class="zone-more price-position-more"><summary>展開看分批、賣出劇本與依據</summary>'
        f'<div class="zone-subtitle">買進拆批</div><div class="zone-steps">{step_html}</div>'
        f'<div class="zone-subtitle">賣出劇本</div><div class="zone-steps">{sell_step_html}</div>'
        f'<div class="zone-subtitle">觀察依據</div><ul>{watch_html}</ul>'
        f'<p>{h(zone.get("basis", ""))}</p></details>'
        f'</div>'
    )


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
    chip = chip_flow_judgment(meta, safe_num(ext.get('latest_close')) or meta.get('quote_price'))
    chip_text = f'法人資金：{chip["state"]}，{chip["summary"]}' if chip.get('available') else '法人資金：資料待補。'

    if is_etf:
        dca_txt, _, dca_reason = rec['dca']
        if risk < 45:
            conclusion = '風險偏高，只能小額觀察'
        elif chip.get('tone') == 'negative' and w_pct >= 75:
            conclusion = '法人退潮且價格偏高，暫停加碼'
        elif chip.get('tone') == 'positive' and w_pct < 80 and score >= 60:
            conclusion = '法人流入支持，可紀律分批'
        elif w_pct >= 85:
            conclusion = '長期可持續扣款，但不追高加碼'
        elif score >= 70:
            conclusion = '可分批投入，仍保留現金'
        elif score < 45:
            conclusion = '先降低加碼衝動，等趨勢回穩'
        else:
            conclusion = dca_txt
        reason = f'價格位階在 52 週的 {w_pct:.0f}%，趨勢為「{ma}」，風險分數 {risk}{dd_text}。ETF資料：{etf_basic_phrase(meta)}。{chip_text}。{dca_reason}'
        counter = '如果大盤跌破季線、ETF折溢價異常、費用率偏高、配息來源不穩或成分股過度集中，這個判斷要降級。'
        if plan and plan.get('amount', 0) > 0:
            action = f'定期定額照計畫，本月示範投入約 {money(plan["amount"])} 元；臨時想買請看本卡買進區間，並用上方「今天想買」分批估算。'
        else:
            action = '本月不加碼，先保留現金；臨時想買要看本卡買進區間，只用小比例試單。'
        if chip.get('tone') == 'negative':
            action += ' 法人資金偏退潮時，不因便宜感加碼。'
    else:
        trade_txt, _, trade_reason = rec['trade']
        if chip.get('tone') == 'negative':
            conclusion = '法人資金退潮，新手先不要追'
        elif chip.get('tone') == 'positive' and score >= 65 and w_pct < 85:
            conclusion = '法人流入支持，可小比例研究'
        elif score >= 70:
            conclusion = '可小比例觀察，不建議重壓個股'
        elif score < 45:
            conclusion = '新手先不要追，等風險降低'
        else:
            conclusion = trade_txt
        reason = f'技術分數 {score}，價格位階 {w_pct:.0f}%，趨勢為「{ma}」{dd_text}。基本面：{stock_basic_phrase(meta)}。{chip_text}。{trade_reason}'
        counter = '如果營收連續轉弱、EPS下修、毛利率下降、Forward PE轉差或跌破季線，這個觀察要立刻降級。'
        action = '新手先把個股當觀察清單；真的想買，先看本卡買進區間，用小比例分批，不要影響核心 ETF 部位。'
        if chip.get('tone') == 'positive' and w_pct >= 85:
            action += ' 法人買超但價格已高，只能強勢追蹤，不是重押買點。'
        elif chip.get('tone') == 'negative':
            action += ' 法人資金流出時，先等止跌或基本面確認。'

    return dict(conclusion=conclusion, reason=reason, counter=counter, action=action)


def store_buy_now_data(ticker, name, price, a, ext, rec, plan, zone=None):
    decision = decision_texts(ticker, a, ext, rec, plan)
    quality = data_quality_note(ticker, a)
    meta = a.get('meta') or {}
    chip = chip_flow_judgment(meta, price)
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
        chip_available=bool(chip.get('available')),
        chip_state=chip.get('state'),
        chip_tone=chip.get('tone'),
        chip_score=chip.get('score'),
        chip_summary=chip.get('summary'),
        chip_action=chip.get('action'),
        chip_date=meta.get('finmind_chip_date'),
        chip_total_1d=chip.get('total_1d'),
        chip_total_5d=chip.get('total_5d'),
        chip_total_20d=chip.get('total_20d'),
        chip_foreign_5d=chip.get('foreign_5d'),
        chip_trust_5d=chip.get('trust_5d'),
        chip_dealer_5d=chip.get('dealer_5d'),
        chip_streak=chip.get('streak'),
        price_zone=zone or {},
    )


def target_dom_id(ticker):
    return 'target-' + re.sub(r'[^A-Za-z0-9]+', '-', ticker).strip('-')


def target_tab_id(ticker):
    if ticker in TW_ETFS:
        return 'tw-etfs'
    if ticker in TW_STOCKS:
        return 'tw-stocks'
    if ticker in US_ETFS:
        return 'us-etfs'
    if ticker in US_STOCKS:
        return 'us-stocks'
    return 'bonds'


def target_link(ticker, label='看完整卡', cls='mini-link'):
    return (
        f'<a class="{cls}" href="#{target_dom_id(ticker)}" '
        f'onclick="openTargetDetail(\'{target_dom_id(ticker)}\',\'{target_tab_id(ticker)}\');return false">{h(label)}</a>'
    )


def zone_range_text(item):
    zone = item.get('price_zone') or {}
    return zone.get('range_text') or 'N/A'


def zone_status(item):
    zone = item.get('price_zone') or {}
    return zone.get('status') or item.get('conclusion') or '資料不足'


def action_tone(status):
    if status in ['可分批區', '健康回檔', '加碼觀察區']:
        return 'ok'
    if status in ['健康創高', '強勢高位', '偏高等待區']:
        return 'wait'
    if status in ['過熱追高', '轉弱下跌']:
        return 'stop'
    return 'wait'


def home_action_text(item):
    status = zone_status(item)
    role = item.get('role', '')
    if status in ['可分批區', '健康回檔', '加碼觀察區']:
        return '照計畫扣款，可小額分批' if role == '長期核心' else '可小額分批參與'
    if status in ['健康創高', '強勢高位']:
        return '維持扣款，額外單筆縮小'
    if status == '偏高等待區':
        return '照常扣款，額外單筆先等'
    if status == '過熱追高':
        return '暫停額外追價'
    if status == '轉弱下跌':
        return '先觀察，不攤平'
    return '先看完整卡再決定'


def all_targets_order():
    return [
        tk for tk in
        list(TW_ETFS.keys()) + list(TW_STOCKS.keys()) +
        list(US_ETFS.keys()) + list(US_STOCKS.keys()) + list(BONDS.keys())
        if tk in BUY_NOW_DATA
    ]


def compact_target_tile(ticker, item, mode='normal'):
    code = ticker.replace('.TW', '')
    status = zone_status(item)
    tone = action_tone(status)
    price = item.get('price')
    price_text = f'{float(price):,.2f}' if isinstance(price, (int, float)) else 'N/A'
    score = item.get('score', 'N/A')
    score_num = safe_num(score) or 0
    score_tone = 'good' if score_num >= 70 else 'mid' if score_num >= 50 else 'low'
    range_text = zone_range_text(item)
    action = item.get('action') or item.get('conclusion') or '先看完整卡'
    extra = ''
    if mode == 'core':
        extra = (
            f'<div class="mini-meta">'
            f'<span>{h(item.get("role", ""))}</span>'
            f'<span>{h(item.get("bucket", ""))}</span>'
            f'<span>可信度 {h(item.get("confidence", "N/A"))}</span>'
            f'</div>'
        )
    return (
        f'<article class="mini-target mini-{tone} mini-{mode}">'
        f'<div class="mini-head"><div><b>{h(code)} {h(item.get("name", ""))}</b>'
        f'<small>{h(item.get("role", ""))} / {h(item.get("bucket", ""))}</small></div>'
        f'<span>{h(status)}</span></div>'
        f'<div class="mini-body">'
        f'<div><span>現價</span><b>{price_text}</b></div>'
        f'<div class="mini-score-cell score-{score_tone}"><span>體質分</span><b>{h(score)}</b><i><em style="width:{score_num:.0f}%"></em></i></div>'
        f'<div><span>可看區間</span><b>{h(range_text)}</b></div>'
        f'</div>'
        f'<p>{h(action)}</p>'
        f'{extra}'
        f'{target_link(ticker)}'
        f'</article>'
    )


def core_etf_spotlight_html():
    core = [tk for tk in ['0050.TW', '006208.TW', '006204.TW', '00662.TW'] if tk in BUY_NOW_DATA]
    cashflow = [tk for tk in ['0056.TW', '00878.TW', '00919.TW'] if tk in BUY_NOW_DATA]
    if not core and not cashflow:
        return ''
    core_html = ''.join(compact_target_tile(tk, BUY_NOW_DATA[tk], 'core') for tk in core)
    cash_html = ''.join(
        f'<div class="core-alt-row"><b>{h(tk.replace(".TW", ""))} {h(BUY_NOW_DATA[tk]["name"])}</b>'
        f'<span>{h(zone_status(BUY_NOW_DATA[tk]))}</span>'
        f'<small>{h(zone_range_text(BUY_NOW_DATA[tk]))}</small>{target_link(tk, "打開")}</div>'
        for tk in cashflow
    )
    return (
        f'<section class="sc core-etfs" id="core-etfs">'
        f'<div class="tool-head"><div><div class="st">核心 ETF</div>'
        f'<p>先把每月最可能照做的標的放上來。0050 / 006208 偏台股核心，00662 偏美股科技成長；都不是要猜最低點，而是看現在適不適合照常扣、少量加碼或先別重押。</p></div>'
        f'<span class="section-meta">每月扣款優先看</span></div>'
        f'<div class="core-grid">{core_html}</div>'
        f'<details class="core-alt"><summary>看高股息與現金流 ETF</summary>{cash_html}</details>'
        f'</section>'
    )


def today_focus_html():
    if not BUY_NOW_DATA:
        return ''
    ordered = all_targets_order()
    confidence_rank = {'高': 3, '中': 2, '偏低': 1}

    def priority(tk):
        item = BUY_NOW_DATA[tk]
        role_rank = {
            '長期核心': 80, '海外分散': 58, '現金流': 54,
            '衛星主題': 48, '防守配置': 44, '個股觀察': 30,
        }.get(item.get('role'), 30)
        tone_rank = {'ok': 38, 'wait': 26, 'stop': 18}.get(action_tone(zone_status(item)), 18)
        score = safe_num(item.get('score')) or 0
        conf = confidence_rank.get(item.get('confidence'), 0)
        return role_rank + tone_rank + score * 0.25 + conf * 4

    def pick(pool, limit, used):
        picked = []
        for tk in sorted(pool, key=priority, reverse=True):
            if tk in BUY_NOW_DATA and tk not in used:
                picked.append(tk)
                used.add(tk)
            if len(picked) >= limit:
                break
        return picked

    used = set()
    core_pool = [tk for tk in ['0050.TW', '006208.TW', '006204.TW', '00662.TW'] if tk in BUY_NOW_DATA]
    ok_pool = [tk for tk in ordered if action_tone(zone_status(BUY_NOW_DATA[tk])) == 'ok']
    risk_pool = [tk for tk in ordered if action_tone(zone_status(BUY_NOW_DATA[tk])) == 'stop']
    groups = [
        ('核心先確認', pick(core_pool, 2, used), '先看你最可能長期執行的標的：照扣可以，額外單筆要看價格區間。'),
        ('可分批機會', pick(ok_pool, 2, used), '這些比較接近紀律買點，但仍要分批，不是重押訊號。'),
        ('高位風險提醒', pick(risk_pool, 2, used), '市場熱時最容易手癢，這裡提醒哪些標的先別追。'),
    ]
    cards = []
    for title, picks, note in groups:
        if not picks:
            continue
        tiles = ''.join(compact_target_tile(tk, BUY_NOW_DATA[tk], 'focus') for tk in picks)
        cards.append(
            f'<div class="focus-group"><div class="focus-head"><b>{h(title)}</b><span>{h(note)}</span></div>'
            f'<div class="focus-grid">{tiles}</div></div>'
        )
    if not cards:
        return ''
    return (
        f'<section class="sc today-focus" id="today-focus">'
        f'<div class="tool-head"><div><div class="st">今日值得先看的標的</div>'
        f'<p>這裡不是全部分類，也不是買進排行榜；只挑少數今天最需要先確認的卡。其他標的請用下方搜尋與篩選。</p></div>'
        f'<span class="section-meta">情境掃描</span></div>'
        f'{"".join(cards)}'
        f'</section>'
    )


def home_core_preview_html():
    tickers = [tk for tk in ['0050.TW', '006208.TW', '00662.TW'] if tk in BUY_NOW_DATA]
    if not tickers:
        return ''
    rows = []
    for tk in tickers:
        item = BUY_NOW_DATA[tk]
        code = tk.replace('.TW', '')
        price = safe_num(item.get('price'))
        price_text = f'{price:,.2f}' if price is not None else 'N/A'
        score = safe_num(item.get('score'))
        score_text = f'{score:.0f}' if score is not None else 'N/A'
        tone = action_tone(zone_status(item))
        action = home_action_text(item)
        rows.append(
            f'<div class="home-table-row home-row-{tone}">'
            f'<div><b>{h(code)} {h(item.get("name", ""))}</b><small>{h(item.get("role", ""))}</small></div>'
            f'<div><span>現價</span><b>{price_text}</b></div>'
            f'<div><span>體質</span><b>{score_text}</b></div>'
            f'<div class="home-row-status"><span>{h(zone_status(item))}</span><small>{h(zone_range_text(item))}</small></div>'
            f'<div class="home-row-action"><span>今日做法</span><b>{h(action)}</b></div>'
            f'{target_link(tk, "查看", "home-row-link")}'
            f'</div>'
        )
    return (
        f'<section class="sc home-panel home-core-preview">'
        f'<div class="home-panel-head"><div><div class="st">核心 ETF</div>'
        f'<p>先看長期配置標的現在適合怎麼做。</p></div>'
        f'<button type="button" onclick="showMode(\'core-mode\')">查看全部</button></div>'
        f'<div class="home-table">'
        f'<div class="home-table-head"><span>標的</span><span>現價</span><span>體質</span><span>價格狀態</span><span>今日做法</span><span></span></div>'
        f'{"".join(rows)}</div>'
        f'</section>'
    )


def home_research_preview_html():
    core = {'0050.TW', '006208.TW', '006204.TW', '00662.TW'}
    confidence_rank = {'高': 3, '中': 2, '偏低': 1}
    stock_order = list(TW_STOCKS.keys()) + list(US_STOCKS.keys())
    candidates = [tk for tk in stock_order if tk in BUY_NOW_DATA and tk not in core]
    candidates.sort(
        key=lambda tk: (
            {'ok': 3, 'wait': 2, 'stop': 1}.get(action_tone(zone_status(BUY_NOW_DATA[tk])), 0),
            confidence_rank.get(BUY_NOW_DATA[tk].get('confidence'), 0),
            safe_num(BUY_NOW_DATA[tk].get('score')) or 0,
        ),
        reverse=True,
    )
    picks = candidates[:3]
    if not picks:
        return ''
    rows = []
    for rank, tk in enumerate(picks, 1):
        item = BUY_NOW_DATA[tk]
        code = tk.replace('.TW', '')
        action = item.get('conclusion') or home_action_text(item)
        risk = safe_num(item.get('risk_score'))
        risk_text = f'風險 {risk:.0f}' if risk is not None else '風險待補'
        rows.append(
            f'<div class="research-row">'
            f'<span class="research-rank">{rank}</span>'
            f'<div><b>{h(code)} {h(item.get("name", ""))}</b></div>'
            f'<div class="research-topic">{h(item.get("bucket", "個股研究"))}</div>'
            f'<p>{h(action)}</p>'
            f'<small>{h(risk_text)} / 可信度 {h(item.get("confidence", "N/A"))}</small>'
            f'{target_link(tk, "查看", "home-row-link")}'
            f'</div>'
        )
    return (
        f'<section class="sc home-panel home-research-preview">'
        f'<div class="home-panel-head"><div><div class="st">今天值得研究</div>'
        f'<p>研究順位，不等於買進推薦。</p></div>'
        f'<button type="button" onclick="showMode(\'research-mode\')">查看全部</button></div>'
        f'<div class="research-list">'
        f'<div class="research-list-head"><span></span><span>標的</span><span>題材</span><span>為什麼看</span><span>主要風險</span><span></span></div>'
        f'{"".join(rows)}</div>'
        f'</section>'
    )


def target_overview_html(market_ctx=None):
    ordered = all_targets_order()
    if not ordered:
        return ''
    target_count = len(ordered)
    order_html = daily_order_overview_html(market_ctx, embedded=True)
    rows = []
    cat_labels = {
        'tw-etfs': '台股 ETF',
        'tw-stocks': '台股個股',
        'us-etfs': '美股 ETF',
        'us-stocks': '美股個股',
        'bonds': '債券/商品',
    }
    confidence_rank = {'高': 3, '中': 2, '偏低': 1}
    for tk in ordered:
        item = BUY_NOW_DATA[tk]
        status = zone_status(item)
        tone = action_tone(status)
        cat = target_tab_id(tk)
        code = tk.replace('.TW', '')
        price = item.get('price')
        price_text = f'{float(price):,.2f}' if isinstance(price, (int, float)) else 'N/A'
        score = safe_num(item.get('score')) or 0
        score_tone = 'good' if score >= 70 else 'mid' if score >= 50 else 'low'
        risk = safe_num(item.get('risk_score')) or 0
        w_pct = safe_num(item.get('w_pct')) or 50
        conf = item.get('confidence', 'N/A')
        watch_score = {'ok': 300, 'wait': 200, 'stop': 100}.get(tone, 0) + score + confidence_rank.get(conf, 0) * 3
        action = item.get('action') or item.get('conclusion') or '看完整卡'
        query_text = ' '.join([
            code, tk, item.get('name', ''), item.get('role', ''), item.get('bucket', ''),
            cat_labels.get(cat, cat), status, action, zone_range_text(item), item.get('confidence', '')
        ]).lower()
        rows.append(
            f'<div class="overview-row overview-{tone}" data-cat="{h(cat)}" data-tone="{h(tone)}" '
            f'data-score="{score:.2f}" data-risk="{risk:.2f}" data-wpct="{w_pct:.2f}" '
            f'data-watch="{watch_score:.2f}" data-conf="{confidence_rank.get(conf, 0)}" '
            f'data-query="{h(query_text)}" data-order="{len(rows)}">'
            f'<div class="overview-name"><b>{h(code)} {h(item.get("name", ""))}</b>'
            f'<small>{h(cat_labels.get(cat, cat))} / {h(item.get("role", ""))} / {h(item.get("bucket", ""))}</small></div>'
            f'<div class="overview-score score-{score_tone}"><span>體質</span><b>{h(item.get("score", "N/A"))}</b><i><em style="width:{score:.0f}%"></em></i></div>'
            f'<div class="overview-price"><span>現價</span><b>{price_text}</b></div>'
            f'<div class="overview-status"><span>{h(status)}</span><small>{h(zone_range_text(item))}</small></div>'
            f'<div class="overview-confidence"><span>可信度</span><b>{h(conf)}</b></div>'
            f'{target_link(tk, "完整卡", "mini-link overview-link")}'
            f'</div>'
        )
    script = '''
function filterOverview(){
  var list=document.getElementById('overviewList');
  var empty=document.getElementById('overviewEmpty');
  if(!list) return;
  var cat=(document.getElementById('overviewCat')||{}).value||'all';
  var tone=(document.getElementById('overviewTone')||{}).value||'all';
  var sort=(document.getElementById('overviewSort')||{}).value||'watch';
  var q=((document.getElementById('overviewSearch')||{}).value||'').trim().toLowerCase();
  var rows=Array.prototype.slice.call(list.querySelectorAll('.overview-row'));
  rows.sort(function(a,b){
    function n(el,key){return Number(el.dataset[key]||0);}
    if(sort==='score') return n(b,'score')-n(a,'score');
    if(sort==='risk') return n(a,'risk')-n(b,'risk');
    if(sort==='wpct') return n(a,'wpct')-n(b,'wpct');
    if(sort==='conf') return n(b,'conf')-n(a,'conf');
    return n(b,'watch')-n(a,'watch') || n(a,'order')-n(b,'order');
  });
  var shown=0;
  rows.forEach(function(row){
    var query=row.dataset.query||'';
    var ok=(cat==='all'||row.dataset.cat===cat)&&(tone==='all'||row.dataset.tone===tone)&&(!q||query.indexOf(q)>=0);
    row.style.display=ok?'grid':'none';
    list.appendChild(row);
    if(ok) shown++;
  });
  if(empty) empty.style.display=shown?'none':'block';
}
function showTargetHub(mode){
  document.querySelectorAll('.target-hub-btn').forEach(function(btn){
    btn.classList.toggle('on', btn.dataset.hub===mode);
  });
  document.querySelectorAll('.target-hub-pane').forEach(function(pane){
    pane.classList.toggle('on', pane.dataset.hubPane===mode);
  });
  if(mode==='orders' && typeof runDailyOrders==='function') runDailyOrders();
}
document.addEventListener('DOMContentLoaded',function(){
  ['overviewSearch','overviewCat','overviewTone','overviewSort'].forEach(function(id){
    var el=document.getElementById(id);
    if(el){ el.addEventListener('change',filterOverview); el.addEventListener('input',filterOverview); }
  });
  filterOverview();
});
'''
    return (
        f'<section class="sc target-overview target-hub" id="target-overview">'
        f'<div class="tool-head"><div><div class="st">標的總覽</div>'
        f'<p>先用「看體質」掃描 {target_count} 檔是否健康，再切到「看掛單」看今天可用價格、投入金額與風險線；兩者分工不同，不會互相取代。</p></div>'
        f'<span class="section-meta">體質 / 掛單</span></div>'
        f'<div class="target-hub-tabs">'
        f'<button type="button" class="target-hub-btn on" data-hub="health" onclick="showTargetHub(\'health\')">看體質</button>'
        f'<button type="button" class="target-hub-btn" data-hub="orders" onclick="showTargetHub(\'orders\')">看掛單</button>'
        f'</div>'
        f'<div class="target-hub-pane on" data-hub-pane="health">'
        f'<div class="overview-controls">'
        f'<label class="overview-search"><span>搜尋代碼或名稱</span><input id="overviewSearch" type="search" placeholder="輸入 2330、台積電、NVDA、電力..." autocomplete="off"></label>'
        f'<label><span>類別</span><select id="overviewCat"><option value="all">全部</option>'
        f'<option value="tw-etfs">台股 ETF</option><option value="tw-stocks">台股個股</option>'
        f'<option value="us-etfs">美股 ETF</option><option value="us-stocks">美股個股</option><option value="bonds">債券/商品</option></select></label>'
        f'<label><span>狀態</span><select id="overviewTone"><option value="all">全部狀態</option>'
        f'<option value="ok">可分批/回檔</option><option value="wait">強勢或等待</option><option value="stop">過熱或轉弱</option></select></label>'
        f'<label><span>排序</span><select id="overviewSort"><option value="watch">值得先看</option>'
        f'<option value="score">體質分高到低</option><option value="risk">風險低到高</option>'
        f'<option value="wpct">52週位置低到高</option><option value="conf">資料可信度高到低</option></select></label>'
        f'</div>'
        f'<div class="overview-list" id="overviewList">{"".join(rows)}</div>'
        f'<div class="nd" id="overviewEmpty" style="display:none">沒有符合篩選的標的。</div>'
        f'</div>'
        f'<div class="target-hub-pane" data-hub-pane="orders">{order_html}</div>'
        f'<script>{script}</script>'
        f'</section>'
    )


def visual_action_board_html(market_ctx=None):
    if not BUY_NOW_DATA:
        return ''
    market_ctx = market_ctx or {}
    ordered = all_targets_order()
    counts = dict(ok=0, wait=0, stop=0)
    for tk in ordered:
        counts[action_tone(zone_status(BUY_NOW_DATA[tk]))] += 1
    total = max(1, sum(counts.values()))
    core_candidates = [tk for tk in ['0050.TW', '006208.TW'] if tk in BUY_NOW_DATA]
    core_text = '核心 ETF 資料不足'
    core_range = 'N/A'
    if core_candidates:
        core = BUY_NOW_DATA[core_candidates[0]]
        core_text = f'{core_candidates[0].replace(".TW", "")}：{zone_status(core)}'
        core_range = zone_range_text(core)
    temp = market_ctx.get('temperature', '中性')
    temp_key = market_ctx.get('temperature_key') or {'熱': 'hot', '偏熱': 'warm', '中性': 'neutral', '偏冷': 'cool', '冷': 'cold'}.get(temp, 'neutral')
    advice = market_ctx.get('advice', '先照計畫小額分批，不因單日訊號改變策略。')

    def bar(label, key, text):
        pct = counts[key] / total * 100
        return (
            f'<div class="visual-bar visual-{key}">'
            f'<div><b>{h(label)}</b><span>{counts[key]} / {total} 檔</span></div>'
            f'<i><em style="width:{pct:.1f}%"></em></i>'
            f'<small>{h(text)}</small></div>'
        )

    return (
        f'<section class="sc visual-board" id="visual-board">'
        f'<div class="tool-head"><div><div class="st">今日行動摘要</div>'
        f'<p>把全部 {total} 檔依目前價格與風險分成互斥狀態；這是市場掃描，不是推薦清單。</p></div>'
        f'<span class="section-meta">視覺化總覽</span></div>'
        f'<div class="visual-grid">'
        f'<div class="visual-main temp-{h(temp_key)}"><span>市場溫度</span><b>{h(temp)}</b><small>{h(advice)}</small></div>'
        f'<div class="visual-main visual-core"><span>核心 ETF</span><b>{h(core_text)}</b><small>可看區間：{h(core_range)}</small></div>'
        f'<div class="visual-main visual-chase temp-{h(temp_key)}"><span>單筆追價</span><b>{h(market_ctx.get("chase_state", "保守"))}</b><small>先看雷達，再看價格區間，不用追熱門。</small></div>'
        f'</div>'
        f'<div class="visual-bars">'
        f'{bar("可分批/回檔", "ok", "值得打開完整卡，但仍要按區間分批。")}'
        f'{bar("強勢或等待", "wait", "不是不能買，是不要用情緒追高。")}'
        f'{bar("過熱或轉弱", "stop", "先等風險降低，或只保留原本定期定額。")}'
        f'</div>'
        f'</section>'
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


def clamp_float(value, low, high):
    v = safe_num(value)
    if v is None:
        return low
    return max(low, min(high, float(v)))


def volatility_profile(ticker, price, a, ext):
    px = safe_num(price) or 0
    atr = safe_num(a.get('atr'))
    atr_pct = safe_num(a.get('atr_pct'))
    if (atr is None or atr <= 0) and px > 0:
        annual_vol = safe_num(ext.get('volatility'))
        if annual_vol is not None and annual_vol > 0:
            atr_pct = max(0.4, annual_vol / (252 ** 0.5))
            atr = px * atr_pct / 100
    if atr_pct is None and atr is not None and px > 0:
        atr_pct = atr / px * 100
    if atr is None and atr_pct is not None and px > 0:
        atr = px * atr_pct / 100
    if atr is None or atr <= 0:
        atr_pct = 1.5 if is_etf_like(ticker) else 2.5
        atr = px * atr_pct / 100 if px > 0 else None

    if atr_pct < 1:
        level = '低波動'
    elif atr_pct < 2:
        level = '中波動'
    elif atr_pct < 4:
        level = '高波動'
    else:
        level = '極高波動'

    role = get_profile(ticker).get('role', '')
    if role == '長期核心':
        min_band, max_band, max_chase = 0.018, 0.065, 0.10
    elif is_etf_like(ticker):
        min_band, max_band, max_chase = 0.024, 0.095, 0.14
    else:
        min_band, max_band, max_chase = 0.035, 0.14, 0.20

    band_pct = clamp_float((atr_pct or 1.5) * 1.35 / 100, min_band, max_band)
    chase_pct = clamp_float((atr_pct or 1.5) * 2.10 / 100, min_band * 1.35, max_chase)
    return dict(
        atr=atr,
        atr_pct=atr_pct,
        level=level,
        band_pct=band_pct,
        chase_pct=chase_pct,
        band_abs=max(px * band_pct, atr or 0) if px > 0 else atr,
    )


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
    vol = volatility_profile(ticker, px, a, ext)
    atr = safe_num(vol.get('atr')) or (px * 0.02)
    band_abs = safe_num(vol.get('band_abs')) or atr
    atr_pct = safe_num(vol.get('atr_pct')) or 2.0
    boll_u = safe_num(a.get('boll_u'))
    boll_l = safe_num(a.get('boll_l'))
    rsi = safe_num(a.get('rsi'))
    dev = safe_num(a.get('dev20'))
    bpct = safe_num(a.get('bpct'))
    vol_ratio = safe_num(a.get('vol_ratio'))
    macd_bull = a.get('macd_bull')
    kd_signal = a.get('kd_signal') or ''

    if trend == '空頭排列':
        stop_raw = ma60 - 1.5 * band_abs
        lower_anchor = ma240 if ma240 is not None else safe_num(ext.get('w_low')) or ma60
        buy_low_raw = lower_anchor - 0.25 * band_abs
        buy_high_raw = ma60 - 0.35 * band_abs
        dynamic_chase = ma20 + 0.8 * band_abs
        chase_raw = max(dynamic_chase, buy_high_raw)
        trend_note = '空頭排列時，便宜不等於安全；價格地圖會把區間往下移，但加碼比例要降。'
    else:
        stop_raw = ma60 - 1.15 * band_abs
        buy_low_raw = min(ma60 + 0.15 * band_abs, ma20 - 0.85 * band_abs)
        buy_high_raw = max(ma20 + 0.35 * band_abs, ma60 + 0.75 * band_abs)
        dynamic_chase = ma20 + 2.0 * atr
        if boll_u is not None:
            dynamic_chase = min(dynamic_chase, boll_u * 1.01)
        chase_raw = max(buy_high_raw, dynamic_chase, ma20 * (1 + vol['chase_pct']))
        trend_note = '用 ATR/布林通道調整區間：波動大的標的區間放寬，但投入比例要降低。'

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

    trend_healthy = (
        trend == '多頭排列'
        and px >= ma20
        and risk_score >= 45
        and macd_bull is not False
    )
    overheated = (
        (rsi is not None and rsi >= 82)
        or (dev is not None and dev >= max(8, atr_pct * 2.3))
        or (bpct is not None and bpct >= 92 and (vol_ratio or 1) >= 1.25)
    )
    turning_weak = (
        px < ma20
        or macd_bull is False
        or '死亡' in kd_signal
        or (bpct is not None and bpct < 45 and px < buy_high)
    )

    if px > chase_limit:
        if trend_healthy and not overheated:
            status = '健康創高'
            summary = '價格在高位但趨勢仍健康；今天的高不一定是未來高點，但不適合一次重押。'
            action = '可用強勢小額追蹤參與，保留資金等回測月線或可分批區。'
            tone = 'momentum'
        else:
            status = '過熱追高'
            summary = '價格已超過動態追高上限，短線報酬/風險不漂亮。'
            action = '定期定額可照計畫；臨時單筆先不要追，等回到可分批區或強勢回測。'
            tone = 'hot'
    elif px < stop_line:
        status = '轉弱下跌'
        summary = '跌破保護線時不要把下跌直接當特價，先確認不是基本面或大盤變壞。'
        action = '等站回保護線、趨勢回穩或基本面沒有惡化，再小額分批。'
        tone = 'danger'
    elif px < buy_low:
        status = '健康回檔' if trend != '空頭排列' else '加碼觀察區'
        summary = '價格拉回到分批區下緣附近；長期ETF可慢慢加，個股要先看營收與獲利有沒有壞掉。'
        action = '核心ETF可分批加碼，個股只小比例試單，跌破保護線就停止。'
        tone = 'cool'
    elif px <= buy_high:
        status = '可分批區'
        summary = '不是保證會漲，而是相對不像在高檔一次追價。'
        action = '想買可以拆 3-6 批；新手不要一次買完。'
        tone = 'ok'
    elif trend_healthy:
        status = '強勢高位'
        summary = '價格偏高但趨勢仍健康，不是不能買，而是不能重押追價。'
        action = '可用強勢小額追蹤；真正加碼等回測月線、ATR區間或可分批區。'
        tone = 'momentum'
    else:
        status = '偏高等待區'
        summary = '價格已離可分批區較遠，不代表永遠不能買，而是不要臨時加碼追高。'
        action = '定期定額照扣；單筆等回落或只買很小一部分。'
        tone = 'warm'

    if is_core and px > buy_high and px <= chase_limit:
        action = '核心ETF可照常扣款；額外加碼等回到區間內。'
    if risk_score < 40:
        summary += ' 風險分數偏低時，所有買進區間都要再保守。'
    if status in ['健康創高', '強勢高位', '過熱追高', '偏高等待區']:
        trend_note += ' 強勢行情中區間可能偏保守；它是紀律買點，不是預測價格一定會回來。'

    range_text = f'{fmt_zone_price(buy_low)} ~ {fmt_zone_price(buy_high)}'
    guard_text = f'大於 {fmt_zone_price(chase_limit)} 不追；小於 {fmt_zone_price(stop_line)} 先停'
    momentum_allowed = status in ['健康創高', '強勢高位'] and trend_healthy and not turning_weak
    momentum_pull = max(0.25 * atr, px * (0.003 if is_core else 0.006))
    momentum_entry = zone_price(ticker, max(buy_high, px - momentum_pull)) if momentum_allowed else None
    reduce_watch = zone_price(ticker, chase_limit)
    trim_line = zone_price(ticker, max(chase_limit + 1.5 * atr, buy_high * 1.10))
    failure_line = stop_line
    if is_core:
        sell_status = '核心部位不因漲多就賣'
        sell_text = f'高於 {fmt_zone_price(reduce_watch)} 先停額外加碼；高於 {fmt_zone_price(trim_line)} 且超過目標配置時，只減碼額外加碼部位。'
        fail_text = '先檢查大盤與ETF是否異常，核心定期定額不急著整筆賣。'
        sell_steps = [
            ('不用賣', '核心續抱', '只是創高或偏熱，不是賣出理由。'),
            ('先停買', f'>{fmt_zone_price(reduce_watch)}', '停止額外加碼，定期定額仍可照計畫。'),
            ('小減碼', '超過配置', '只處理額外加碼部位，通常不動核心。'),
        ]
    elif is_etf_like(ticker):
        sell_status = '過熱或ETF異常才減碼'
        sell_text = f'高於 {fmt_zone_price(reduce_watch)} 不追；高於 {fmt_zone_price(trim_line)} 且折溢價/集中度異常或超過配置時，可分批減碼。'
        fail_text = '先確認是否只是市場回檔，或ETF追蹤/流動性出問題。'
        sell_steps = [
            ('不用賣', '正常波動', 'ETF 沒有折溢價、流動性或追蹤異常時不急著賣。'),
            ('減碼觀察', f'>{fmt_zone_price(trim_line)}', '過熱又超過配置，才分批降一點。'),
            ('風險出場', f'<{fmt_zone_price(failure_line)}', '跌破保護線且ETF資料異常，先降風險。'),
        ]
    else:
        sell_status = '不是現在賣，過熱且轉弱才減碼'
        sell_text = f'高於 {fmt_zone_price(reduce_watch)} 先停買；高於 {fmt_zone_price(trim_line)} 且動能轉弱，可減碼25%~33%。'
        fail_text = '若同時營收、EPS或毛利率轉弱，就是投資理由失效警告。'
        sell_steps = [
            ('不用賣', '強勢健康', '高位但趨勢健康時，不因漲多就急著賣。'),
            ('減碼25%', '高位轉弱', '跌破月線、KD死亡交叉或MACD轉弱，再處理一部分。'),
            ('降風險', '基本面壞', '營收/EPS/毛利率轉弱，才是投資理由失效。'),
        ]

    if turning_weak:
        sell_status = '有持股才看減碼，沒持股先不接'
        sell_text = f'今天已有轉弱訊號；有持股者先看是否跌破 {fmt_zone_price(failure_line)} 或基本面變壞，再分批降風險。'
    elif overheated:
        sell_status = '高位但未轉弱，先停買不急賣'
        sell_text = f'價格偏熱時先停止追價；除非高於 {fmt_zone_price(trim_line)} 又出現轉弱或超過配置，否則不因漲多就賣。'

    if status == '健康創高':
        buy_steps = [
            ('第一批', '10%~20%', '只用小額參與，不把高位當低點重押。'),
            ('第二批', '回測月線', f'回到 {fmt_zone_price(ma20)} 附近且沒轉弱再補。'),
            ('停止條件', '轉弱', f'跌破 {fmt_zone_price(stop_line)} 或基本面轉弱就停。'),
        ]
    elif status == '強勢高位':
        buy_steps = [
            ('第一批', '15%~25%', '趨勢健康可小額分批，但不追滿。'),
            ('第二批', '回到可分批區', f'接近 {range_text} 再補。'),
            ('停止條件', '跌破月線', '強勢股失去月線支撐就先降低節奏。'),
        ]
    elif status == '過熱追高':
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
    elif status in ['加碼觀察區', '健康回檔']:
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
        f'波動度：{vol["level"]}，ATR 約 {atr_pct:.2f}%/日，區間會跟著標的震幅調整',
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
        momentum_entry=momentum_entry,
        momentum_allowed=momentum_allowed,
        range_text=range_text,
        guard_text=guard_text,
        reduce_watch=reduce_watch,
        trim_line=trim_line,
        failure_line=failure_line,
        sell_status=sell_status,
        sell_text=sell_text,
        fail_text=fail_text,
        buy_steps=buy_steps,
        sell_steps=sell_steps,
        watch_items=watch_items,
        price=px,
        atr_pct=round(atr_pct, 2),
        volatility_label=vol['level'],
        turning_weak=turning_weak,
        overheated=overheated,
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
        'momentum': '#185FA5',
        'hot': '#D85A30',
        'danger': '#D85A30',
    }.get(zone.get('tone'), '#6c757d')
    steps = zone.get('buy_steps') or []
    step_html = ''.join(
        f'<div><span>{h(label)}</span><b>{h(amount)}</b><small>{h(note)}</small></div>'
        for label, amount, note in steps
    )
    sell_steps = zone.get('sell_steps') or []
    sell_step_html = ''.join(
        f'<div><span>{h(label)}</span><b>{h(amount)}</b><small>{h(note)}</small></div>'
        for label, amount, note in sell_steps
    )
    watch_items = ''.join(f'<li>{h(item)}</li>' for item in zone.get('watch_items', []))
    low = safe_num(zone.get('failure_line')) or safe_num(zone.get('stop_line'))
    high = safe_num(zone.get('trim_line')) or safe_num(zone.get('chase_limit'))
    cur = safe_num(zone.get('price'))
    buy_low = safe_num(zone.get('buy_low'))
    buy_high = safe_num(zone.get('buy_high'))
    chase = safe_num(zone.get('chase_limit'))
    def pct(v):
        if v is None or low is None or high is None or high <= low:
            return 50
        return max(0, min(100, round((v - low) / (high - low) * 100, 1)))
    marker = pct(cur)
    buy_start = pct(buy_low)
    buy_end = pct(buy_high)
    chase_pos = pct(chase)
    zone_bar = (
        f'<div class="zone-bar">'
        f'<div class="zone-track">'
        f'<span class="seg danger" style="left:0;width:{buy_start}%"></span>'
        f'<span class="seg ok" style="left:{buy_start}%;width:{max(0, buy_end-buy_start)}%"></span>'
        f'<span class="seg warm" style="left:{buy_end}%;width:{max(0, chase_pos-buy_end)}%"></span>'
        f'<span class="seg hot" style="left:{chase_pos}%;width:{max(0, 100-chase_pos)}%"></span>'
        f'<i style="left:{marker}%"></i>'
        f'</div>'
        f'<div class="zone-labels"><span>失效</span><span>可分批</span><span>強勢</span><span>過熱</span></div>'
        f'</div>'
    )
    return (
        f'<div class="zone-box">'
        f'<div class="zone-head"><span>買進 / 減碼價格地圖</span><b style="color:{tone_color}">{h(zone["status"])}</b></div>'
        f'{zone_bar}'
        f'<div class="zone-grid">'
        f'<div><span>可分批區</span><b>{h(zone["range_text"])}</b><small>臨時想買，優先等這個區間。</small></div>'
        f'<div><span>追高/保護線</span><b>{h(zone["guard_text"])}</b><small>上面不追，下面不硬接。</small></div>'
        f'<div><span>減碼觀察</span><b>{h(zone["sell_status"])}</b><small>{h(zone["sell_text"])}</small></div>'
        f'<div><span>失效提醒</span><b>低於 {h(fmt_zone_price(zone.get("failure_line")))}</b><small>{h(zone["fail_text"])}</small></div>'
        f'</div>'
        f'<p>{h(zone["summary"])}</p>'
        f'<small>{h(zone["action"])} {h(zone["basis"])} {h(zone["ma_text"])}</small>'
        f'<details class="zone-more"><summary>分批、賣出與觀察依據</summary>'
        f'<div class="zone-subtitle">買進拆批</div><div class="zone-steps">{step_html}</div>'
        f'<div class="zone-subtitle">賣出劇本</div><div class="zone-steps">{sell_step_html}</div>'
        f'<div class="zone-watch"><b>觀察依據</b><ul>{watch_items}</ul></div>'
        f'</details>'
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


def decision_meter_html(label, value, hint=''):
    v = safe_num(value)
    if v is None:
        text, pct, color = 'N/A', 0, '#657282'
    else:
        pct = max(0, min(100, v))
        text = f'{v:.0f}'
        color = '#1D9E75' if pct >= 70 else '#185FA5' if pct >= 55 else '#BA7517' if pct >= 40 else '#D85A30'
    return (
        f'<div class="decision-meter"><div><span>{h(label)}</span><b style="color:{color}">{h(text)}</b></div>'
        f'<i><em style="width:{pct:.0f}%;background:{color}"></em></i><small>{h(hint)}</small></div>'
    )


def decision_evidence_cards_html(ticker, a, ext):
    meta = a.get('meta') or {}
    is_etf = is_etf_like(ticker)
    fs = a.get('factor_scores') or {}
    price = safe_num(ext.get('latest_close')) or meta.get('quote_price')
    chip = chip_flow_judgment(meta, price)
    w_pct = safe_num(ext.get('w_pct'))
    risk = safe_num(fs.get('risk')) or 50
    trend = safe_num(fs.get('trend')) or 50
    fundamental = safe_num(fs.get('fundamental')) or 50
    w_text = f'{w_pct:.0f}%' if w_pct is not None else 'N/A'
    tech_title = '趨勢偏強' if trend >= 65 else '趨勢普通' if trend >= 45 else '趨勢偏弱'
    if w_pct is not None and w_pct >= 85:
        tech_body = f'52 週位階 {w_text}，價格偏高，適合等回檔或縮小單筆。'
    elif w_pct is not None and w_pct <= 35:
        tech_body = f'52 週位階 {w_text}，接近相對低位，但仍要確認趨勢沒有轉壞。'
    else:
        tech_body = f'52 週位階 {w_text}，趨勢為「{ext.get("ma_align", "N/A")}」。'

    if chip.get('available'):
        flow_title = chip.get('state', '資金分歧')
        flow_body = (
            f'5 日{fmt_net_lots(chip.get("total_5d"))}，20 日{fmt_net_lots(chip.get("total_20d"))}；'
            f'約當金額為{fmt_flow_amount(chip.get("total_5d"), price)}。'
        )
    else:
        flow_title = '法人資料待補'
        flow_body = '這檔暫時不能用外資、投信、自營商判斷，只看價格與基本面。'

    basic_title = 'ETF 資料' if is_etf else '基本面'
    basic_body = etf_basic_phrase(meta) if is_etf else stock_basic_phrase(meta)
    risk_title = '風險可控' if risk >= 65 else '風險中等' if risk >= 45 else '風險偏高'
    risk_body = f'風險分數 {risk:.0f}；這裡只提供公開資料提醒，部位大小仍要自己控制。'

    cards = [
        ('技術面', tech_title, tech_body, 'tech'),
        ('法人資金', flow_title, flow_body, chip.get('tone', 'neutral')),
        (basic_title, '資料重點', basic_body, 'basic'),
        ('風險', risk_title, risk_body, 'risk'),
    ]
    return ''.join(
        f'<div class="decision-mini-card decision-mini-{h(tone)}"><span>{h(label)}</span>'
        f'<b>{h(title)}</b><p>{h(body)}</p></div>'
        for label, title, body, tone in cards
    )


def decision_card_html(ticker, a, ext, rec, plan):
    d = decision_texts(ticker, a, ext, rec, plan)
    q = data_quality_note(ticker, a)
    meta = a.get('meta') or {}
    chip = chip_flow_judgment(meta, safe_num(ext.get('latest_close')) or meta.get('quote_price'))
    fs = a.get('factor_scores') or {}
    meters = ''.join([
        decision_meter_html('體質', a.get('score'), a.get('stxt', '')),
        decision_meter_html('風險', fs.get('risk'), '越高代表越穩'),
        decision_meter_html('法人', chip.get('score') if chip.get('available') else None, chip.get('state', '待補')),
    ])
    evidence = decision_evidence_cards_html(ticker, a, ext)
    return (
        f'<div class="decision-card decision-card-v2">'
        f'<div class="decision-head"><span>小白決策卡</span><b class="confidence-pill">資料可信度：{h(q["confidence"])}</b></div>'
        f'<div class="decision-hero-row"><span>結論</span><strong>{h(d["conclusion"])}</strong></div>'
        f'<div class="decision-action-grid">'
        f'<div><span>今天怎麼做</span><p>{h(d["action"])}</p></div>'
        f'<div><span>什麼情況要改變</span><p>{h(d["counter"])}</p></div>'
        f'</div>'
        f'<div class="decision-meter-grid">{meters}</div>'
        f'<div class="decision-evidence-grid">{evidence}</div>'
        f'<details class="decision-more"><summary>查看原始推論與資料可信度</summary>'
        f'<div class="decision-row"><span>原因</span><p>{h(d["reason"])}</p></div>'
        f'<div class="decision-note"><b>可信度原因：</b>{h(q["reason"])}<br><span>資料項目：{h(q["notes"])}</span></div>'
        f'</details>'
        f'</div>'
    )


def newbie_summary_html(market_ctx=None):
    monthly = PUBLIC_EXAMPLE_PLAN['monthly_budget']
    core = round_amount(monthly * 0.55)
    satellite = round_amount(monthly * 0.20)
    defensive = round_amount(monthly * 0.15)
    cash = max(0, monthly - core - satellite - defensive)
    themes = '、'.join(t['theme'] for t in NEWS_THEMES[:4])
    market_ctx = market_ctx or dict(
        regime='資料不足', temperature='中性', advice='先照計畫小額分批，不因單日漲跌改變策略。',
        headline='資料不足，先照原計畫，不因單日訊號改變策略。',
        temperature_key='neutral',
        trend_state='資料不足', emotion_state='中性', risk_state='中', chase_state='保守',
        asia_state='資料不足', asia_note='亞洲指數資料不足',
        reasons=['資料不足。'], counters=['等資料更新。'], actions=['定期定額可照計畫，單筆先小額。'],
    )
    reasons = ''.join(f'<li>{h(x)}</li>' for x in market_ctx.get('reasons', [])[:4])
    counters = ''.join(f'<li>{h(x)}</li>' for x in market_ctx.get('counters', [])[:3])
    actions = ''.join(f'<li>{h(x)}</li>' for x in market_ctx.get('actions', [])[:3])
    core_statuses = [
        zone_status(BUY_NOW_DATA[tk])
        for tk in ['0050.TW', '006208.TW', '006204.TW', '00662.TW']
        if tk in BUY_NOW_DATA
    ]
    hot_core = any(s in ['過熱追高', '健康創高', '強勢高位', '偏高等待區'] for s in core_statuses)
    if market_ctx.get('regime') == '多頭延續' and hot_core:
        plain_take = '白話結論：市場方向偏多，但核心 ETF 多數不便宜；定期定額照扣，臨時單筆不要重押。'
    else:
        plain_take = f'白話結論：{market_ctx.get("advice", "先照計畫小額分批，不因單日訊號改變策略。")}'
    time_note = '時間提醒：不同市場交易時段不同；台股多為最近台股交易日收盤/延遲資料，美股可能是盤中資料，請看每張卡的台灣時間與原市場時間。'
    temperature = market_ctx.get('temperature', '中性')
    status_cards = [
        ('大盤趨勢', market_ctx.get('trend_state', market_ctx.get('regime', '資料不足')), market_ctx.get('regime', ''), 'ok'),
        ('市場溫度', temperature, '評估價格位置與追價風險', 'warn' if temperature in ['熱', '偏熱'] else 'info'),
        ('市場情緒', f'VIX {market_ctx.get("vix", "N/A")}', market_ctx.get('emotion_state', '中性'), 'info'),
        ('台股位階', f'{market_ctx.get("position", 50)}%', '52 週價格位置', 'ok' if safe_num(market_ctx.get('position')) is not None else 'info'),
    ]
    status_html = ''.join(
        f'<div class="status-card status-{tone}"><span>{h(label)}</span><b>{h(value)}</b><small>{h(note)}</small></div>'
        for label, value, note, tone in status_cards
    )
    return (
        f'<section class="sc intro-card decision-hero" id="market-summary">'
        f'<div class="market-brief">'
        f'<div><div class="hero-heading"><div class="st">今天市場適合怎麼做？</div>'
        f'<span class="hero-temperature hero-temperature-{h(market_ctx.get("temperature_key", "neutral"))}">{h(temperature)}</span></div>'
        f'<h2>{h(market_ctx.get("headline", ""))}</h2>'
        f'<p>{h(market_ctx.get("advice", ""))}</p></div>'
        f'</div>'
        f'<div class="status-strip">{status_html}</div>'
        f'<details class="decision-evidence">'
        f'<summary>查看依據與改變條件</summary>'
        f'<div class="market-reason-grid">'
        f'<div><b>為什麼</b><ul>{reasons}</ul></div>'
        f'<div><b>什麼情況要改變想法</b><ul>{counters}</ul></div>'
        f'<div><b>今天怎麼做</b><ul>{actions}</ul></div>'
        f'</div>'
        f'<div class="decision-time-note">{h(time_note)}</div>'
        f'<div class="plain-take"><b>{h(plain_take)}</b></div>'
        f'<div class="intro-note">題材觀察方向：{themes}。這不是買進訊號，只是把可能值得研究的產業列出來。</div>'
        f'</details>'
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
        f'<section class="sc theme-radar" id="theme-radar">'
        f'<div class="st">題材觀察雷達</div>'
        f'<div class="method-lead">這裡不是新聞買進訊號，只是把新聞可能提到的產業，轉成「可以研究的清單」。進場前仍要看估值、風險、趨勢與基本面。</div>'
        f'<div class="theme-grid">{"".join(cards)}</div>'
        f'</section>'
    )


def sector_flow_radar_html(compact=False):
    """Real Taiwan institutional flow by tracked sector.

    Only Taiwan tickers with FinMind institutional data are included. US names
    remain in theme radar, but not in this true fund-flow radar.
    """
    if not BUY_NOW_DATA:
        return ''

    def metrics_for(theme):
        rows = []
        for tk in theme.get('watch', []):
            item = BUY_NOW_DATA.get(tk)
            if not item or not item.get('chip_available') or not item.get('is_tw'):
                continue
            rows.append((tk, item))
        if not rows:
            return None

        total_1d = sum(safe_num(item.get('chip_total_1d')) or 0 for _, item in rows)
        total_5d = sum(safe_num(item.get('chip_total_5d')) or 0 for _, item in rows)
        total_20d = sum(safe_num(item.get('chip_total_20d')) or 0 for _, item in rows)
        foreign_5d = sum(safe_num(item.get('chip_foreign_5d')) or 0 for _, item in rows)
        trust_5d = sum(safe_num(item.get('chip_trust_5d')) or 0 for _, item in rows)
        dealer_5d = sum(safe_num(item.get('chip_dealer_5d')) or 0 for _, item in rows)
        amount_5d = sum((safe_num(item.get('chip_total_5d')) or 0) * (safe_num(item.get('price')) or 0) for _, item in rows)
        avg_wpct = sum(safe_num(item.get('w_pct')) or 50 for _, item in rows) / len(rows)
        avg_score = sum(safe_num(item.get('score')) or 0 for _, item in rows) / len(rows)

        if total_5d > 0 and total_20d > 0 and foreign_5d > 0 and trust_5d > 0:
            state, cls = '漲潮', 'tide'
            meaning = '外資與投信近 5 日同步偏買，且 20 日合計仍流入。'
        elif total_5d > 0 and total_20d > 0:
            state, cls = '輪動', 'rotate'
            meaning = '法人近 5 日與 20 日偏買，但不同法人之間仍有分歧。'
        elif total_5d > 0 and total_20d <= 0:
            state, cls = '短線回補', 'rotate'
            meaning = '短線買盤回來，但中期還沒確認，不適合直接追高。'
        elif total_5d < 0 and total_20d < 0:
            state, cls = '退潮', 'ebb'
            meaning = '近 5 日與 20 日都偏賣，法人資金正在流出。'
        else:
            state, cls = '觀望', 'watch'
            meaning = '法人方向不夠明確，先看下一次更新是否延續。'

        if avg_wpct >= 85 and cls in ['tide', 'rotate']:
            meaning += ' 但追蹤標的平均位階偏高，只能小額或等回測。'
        elif avg_wpct <= 45 and cls in ['tide', 'rotate']:
            meaning += ' 價格沒有明顯過熱，可優先打開完整卡檢查買進區間。'

        ranked = sorted(
            rows,
            key=lambda pair: (
                safe_num(pair[1].get('chip_score')) or 50,
                safe_num(pair[1].get('score')) or 0,
            ),
            reverse=True,
        )
        return dict(
            state=state,
            cls=cls,
            meaning=meaning,
            total_1d=total_1d,
            total_5d=total_5d,
            total_20d=total_20d,
            foreign_5d=foreign_5d,
            trust_5d=trust_5d,
            dealer_5d=dealer_5d,
            amount_5d=amount_5d,
            avg_wpct=avg_wpct,
            avg_score=avg_score,
            rows=ranked,
        )

    ranked_themes = []
    for theme in NEWS_THEMES:
        m = metrics_for(theme)
        if not m:
            continue
        priority = (
            {'tide': 80, 'rotate': 60, 'watch': 35, 'ebb': 20}.get(m['cls'], 30)
            + min(20, abs(m['amount_5d']) / 100_000_000 * 0.6)
            + (m['avg_score'] - 50) * 0.15
        )
        ranked_themes.append((priority, theme, m))

    ranked_themes.sort(key=lambda x: x[0], reverse=True)
    if compact:
        ranked_themes = ranked_themes[:4]

    cards = []
    for _, theme, m in ranked_themes:
        amount_text = fmt_flow_amount(m['total_5d'], m['amount_5d'] / m['total_5d'] if m['total_5d'] else None)
        targets = ''.join(
            f'<span>{h(tk.replace(".TW", ""))}<small>{h(item.get("chip_state") or "待補")}</small></span>'
            for tk, item in m['rows'][:5]
        )
        cards.append(
            f'<article class="sector-card sector-{h(m["cls"])}">'
            f'<div class="sector-card-head"><div><b>{h(theme["theme"])}</b>'
            f'<small>{h("、".join(theme["words"][:4]))}</small></div>'
            f'<strong>{h(m["state"])}</strong></div>'
            f'<div class="sector-metrics">'
            f'<div><span>5日合計</span><b>{h(fmt_net_lots(m["total_5d"]))}</b></div>'
            f'<div><span>約當金額</span><b>{h(amount_text)}</b></div>'
            f'<div><span>20日合計</span><b>{h(fmt_net_lots(m["total_20d"]))}</b></div>'
            f'</div>'
            f'<p>{h(m["meaning"])}</p>'
            f'<div class="sector-targets">{targets}</div>'
            f'</article>'
        )

    if not cards:
        return ''
    meta = '首頁摘要' if compact else '三大法人'
    section_id = 'sector-flow-radar-compact' if compact else 'sector-flow-radar'
    return (
        f'<section class="sc sector-flow-radar" id="{section_id}">'
        f'<div class="tool-head"><div><div class="st">法人資金流雷達</div>'
        f'<p>用台股三大法人買賣超加總板塊：外資、投信、自營商。美股與沒有法人資料的標的不放進這個雷達。</p></div>'
        f'<span class="section-meta">{h(meta)}</span></div>'
        f'<div class="sector-flow-legend"><span class="legend-tide">漲潮</span><span class="legend-rotate">輪動/回補</span><span class="legend-watch">觀望</span><span class="legend-ebb">退潮</span></div>'
        f'<div class="sector-grid">{"".join(cards)}</div>'
        f'<div class="tool-note">資料來源：FinMind InstitutionalInvestorsBuySell。買賣超以股數換算張數；板塊約當金額為買賣超股數乘以目前價格粗估，只用來判斷方向與量級，不是交易所原始成交金額。</div>'
        f'</section>'
    )


def methodology_html():
    return (
        f'<section class="sc methodology" id="methodology">'
        f'<div class="st">計算方式說明</div>'
        f'<div class="method-lead">這個網站不是預測明天漲跌，而是把公開資料整理成「現在適不適合分批、要不要追高、風險在哪裡」。</div>'
        f'<div class="method-grid">'
        f'<div><b>小白版怎麼看</b><p>先看今日市場重點，再看每張卡片的現在狀態與價格地圖。體質分高代表標的狀態較好，但不代表今天價格可以追。</p></div>'
        f'<div><b>ETF 分數</b><p>趨勢 25% + 動能 15% + 成交量 10% + 價格位置 10% + 風險 20% + ETF資料 20%。ETF 要看成本、折溢價、規模、配息與總報酬。</p></div>'
        f'<div><b>個股分數</b><p>趨勢 25% + 動能 15% + 成交量 10% + 價格位置 10% + 風險 20% + 基本面 20%。PE 只是入口，還要看營收、EPS、ROE與毛利率。</p></div>'
        f'<div><b>價格基準</b><p>價格卡以 Yahoo quote 的資料時間為主：報價時間先顯示台灣時間，括號補原市場時間；價格顯示該交易日的最新價/收盤、開盤、昨收、高低價。</p></div>'
        f'<div><b>買進/減碼區間</b><p>不是只回答買或不買，而是拆成健康創高、過熱追高、健康回檔、轉弱下跌等狀態。強勢高位不是不能買，而是只能小額分批。</p></div>'
        f'<div><b>ATR 動態區間</b><p>價格地圖會參考 ATR 與布林通道。高波動股區間較寬但投入比例較低；低波動 ETF 區間較窄但可照紀律分批。</p></div>'
        f'<div><b>零股怎麼算</b><p>個股是單一公司股票，不等於零股。台股一張是1,000股，未滿一張就是零股；試算會用股數與約幾張一起顯示。</p></div>'
        f'<div><b>RSI 怎麼解讀</b><p>RSI 高不一定危險。若趨勢強、量能正常，可能是強勢延續；若高檔轉弱、KD 偏空、波動放大，才提高風險。</p></div>'
        f'</div>'
        f'<div class="method-note">資料口徑以公開市場資料與固定規則為主；請把它當成市場理解工具，不要當成保證獲利或個人化下單指令。</div>'
        f'</section>'
    )


def report_phase_info(now_tw):
    if now_tw.weekday() >= 5:
        return {
            'label': '週末觀察版',
            'note': '台股休市，台股資料多為前一交易日；重點看美股收盤、全球市場與下週觀察。'
        }
    t = now_tw.time()
    if t < dt_time(8, 30):
        return {
            'label': '盤前版',
            'note': '台股尚未開盤，台股多為上一交易日；重點看美股收盤與開盤前風險。'
        }
    if t < dt_time(9, 45):
        return {
            'label': '開盤後快照',
            'note': '開盤後初步更新，早盤波動較大；先看方向，不把它當成全天結論。'
        }
    if t < dt_time(13, 30):
        return {
            'label': '早盤追蹤',
            'note': '早盤資料較穩，但午盤與收盤仍可能改變價格位置與資金流判斷。'
        }
    if t < dt_time(16, 30):
        return {
            'label': '收盤快照',
            'note': '台股已收盤，價格較完整；部分三大法人、ETF與官方資料可能稍晚補齊。'
        }
    return {
        'label': '收盤完整版',
        'note': '收盤後整理版；若三大法人與官方資料來源已更新，會一併納入判斷。'
    }


def public_readiness_html(update_time, market_ctx=None, report_phase=None):
    market_ctx = market_ctx or {}
    report_phase = report_phase or {'label': '資料更新', 'note': '請以頁面顯示的資料時間為準。'}
    temp = market_ctx.get('temperature', '中性')
    regime = market_ctx.get('regime', '資料不足')
    return (
        f'<section class="sc public-check" id="data-check">'
        f'<div class="st">資料檢查</div>'
        f'<div class="method-lead">這一區是出貨前自查用：本頁是靜態頁，數字只代表這次產生報告時抓到的資料。</div>'
        f'<div class="check-grid">'
        f'<div><span>報告產生</span><b>{h(update_time)} 台灣時間</b><small>若過了交易日，請重新跑 generate.py 更新。</small></div>'
        f'<div><span>資料時段</span><b>{h(report_phase["label"])}</b><small>{h(report_phase["note"])}</small></div>'
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
        f'<section class="sc dca-tool" id="dca-sim">'
        f'<div class="tool-head"><div><div class="st">定期定額模擬器</div>'
        f'<p>用歷史價格練習「如果我每月固定投入，過程會多痛、最後可能變多少」。這不是預測，只是幫新手先看懂風險。</p></div>'
        f'<span class="section-meta">歷史模擬</span></div>'
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
  var allMonthly=[];
  var seen={{}};
  for(var i=0;i<points.length;i++){{
    var key=monthKey(points[i][0]);
    if(!seen[key]){{allMonthly.push(points[i]);seen[key]=true;}}
  }}
  var targetMonths=Math.max(1, Math.round(years*12));
  var monthly=allMonthly.slice(-targetMonths);
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
    '<div class="dca-stat"><span>累積投入</span><b>'+fmtMoney(invested)+' 元</b><small>'+monthly.length+' 次扣款，每月 '+fmtMoney(amount)+' 元；'+years+' 年以 '+targetMonths+' 次為基準</small></div>'+
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
        f'<section class="sc buy-tool" id="buy-tool">'
        f'<div class="tool-head"><div><div class="st">今天想買試算</div>'
        f'<p>臨時想買時，不先問「會不會漲」，先問「現在能買幾成、剩下要留多少」。這裡用固定規則估算，不代表個人投資建議。</p></div>'
        f'<span class="section-meta">臨時買入工具</span></div>'
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
  if(zone.status==='過熱追高') ratio=Math.min(ratio, item.role==='長期核心'?0.12:0.05);
  if(zone.status==='健康創高') ratio=Math.min(ratio, item.role==='長期核心'?0.20:0.10);
  if(zone.status==='強勢高位') ratio=Math.min(ratio, item.role==='長期核心'?0.24:0.12);
  if(zone.status==='偏高等待區') ratio=Math.min(ratio, item.role==='長期核心'?0.20:0.08);
  if(zone.status==='轉弱下跌') ratio=Math.min(ratio, item.role==='長期核心'?0.10:0.00);
  if(zone.status==='可分批區') ratio=Math.max(ratio, item.role==='長期核心'?0.25:0.08);
  if(zone.status==='加碼觀察區'||zone.status==='健康回檔') ratio=Math.max(ratio, item.role==='長期核心'?0.30:0.08);
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


def daily_order_overview_html(market_ctx=None, embedded=False):
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
    section_tag = 'div' if embedded else 'section'
    section_class = 'order-tool embedded-order' if embedded else 'sc order-tool'
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
  if(z.status==='過熱追高') ratio=Math.min(ratio, item.role==='長期核心'?0.10:0.04);
  if(z.status==='健康創高') ratio=Math.min(ratio, item.role==='長期核心'?0.16:0.06);
  if(z.status==='強勢高位') ratio=Math.min(ratio, item.role==='長期核心'?0.18:0.08);
  if(z.momentum_allowed) ratio=Math.min(ratio, item.role==='長期核心'?0.18:(item.is_etf?0.10:0.06));
  if(z.status==='偏高等待區') ratio=Math.min(ratio, item.role==='長期核心'?0.18:0.07);
  if(z.status==='轉弱下跌') ratio=0;
  if(z.status==='可分批區') ratio=Math.max(ratio, item.role==='長期核心'?0.25:0.06);
  if(z.status==='加碼觀察區'||z.status==='健康回檔') ratio=Math.max(ratio, item.role==='長期核心'?0.28:(item.is_etf?0.08:0.04));
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
  if(status==='過熱追高'){
    target=high||price*0.97;
    action='等回檔';
    cls='order-wait';
    note='掛在可分批區上緣附近，今天沒碰到就不追。';
  }else if(status==='健康創高'){
    target=Number(z.momentum_entry||0)||Math.max(high||0, price*0.992);
    action='強勢小額追蹤';
    cls='order-momentum';
    note='趨勢仍健康，允許小額低掛參與；這不是加碼區，沒成交不抬價。';
  }else if(status==='強勢高位'){
    target=Number(z.momentum_entry||0)||Math.max(high||0, price*0.994);
    action='強勢小額追蹤';
    cls='order-momentum';
    note='強勢不是不能買，但只能用小比例追蹤，真正加碼等回測。';
  }else if(status==='偏高等待區'){
    target=Math.min(high||price*0.99, price*0.99);
    action='低掛小單';
    cls='order-wait';
    note='價格偏高，只能低掛，成交不到也不要抬價。';
  }else if(status==='可分批區'){
    target=Math.max(low||0, Math.min(high||price, price*(1-pull)));
  }else if(status==='加碼觀察區'||status==='健康回檔'){
    target=price*(item.is_etf?0.998:0.994);
    if(stop) target=Math.max(target, stop*1.01);
    action=item.is_etf?'可小加碼':'小額試單';
    note=item.is_etf?'低位可小幅分批，但仍要保留現金。':'個股只小額試單，確認基本面沒變壞。';
  }else if(status==='轉弱下跌'){
    target=stop||price;
    action='先不買';
    cls='order-stop';
    note='跌破保護線時不要硬接，等站回再重新算。';
  }
  if(high && target>high && status!=='加碼觀察區'&&status!=='健康回檔') target=high;
  if(low && target<low && status==='可分批區') target=low;
  return {price:roundOrderPrice(item,target,'buy'), action:action, cls:cls, note:note};
}
function runDailyOrders(){
  var out=document.getElementById('orderRows');
  var cards=document.getElementById('orderCards');
  var budgetEl=document.getElementById('orderBudget');
  var filterEl=document.getElementById('orderFilter');
  var scopeEl=document.getElementById('orderScope');
  var searchEl=document.getElementById('orderSearch');
  if(!out||!budgetEl||!window.BUY_NOW_DATA) return;
  var budget=Math.max(0, Number(budgetEl.value||0));
  var filter=filterEl?filterEl.value:'all';
  var scope=scopeEl?scopeEl.value:'actionable';
  var q=searchEl?String(searchEl.value||'').trim().toLowerCase():'';
  var keys=window.ORDER_KEYS||Object.keys(window.BUY_NOW_DATA);
  var html='';
  var cardHtml='';
  keys.forEach(function(tk){
    var item=window.BUY_NOW_DATA[tk];
    if(!item) return;
    var cat=orderCategory(item);
    if(filter!=='all'&&filter!==cat) return;
    var z=item.price_zone||{};
    var code=tk.replace('.TW','');
    var query=[tk, code, item.name||'', item.role||'', item.bucket||'', cat, z.status||'', item.conclusion||'', item.action||''].join(' ').toLowerCase();
    if(q&&query.indexOf(q)<0) return;
    var target=calcOrderTarget(item);
    var actionable=(target.cls==='order-ok'||target.cls==='order-momentum');
    var waiting=(target.cls==='order-wait');
    var riskLine=(target.cls==='order-stop'||target.action==='先不買'||target.action==='先等');
    if(scope==='actionable'&&!actionable) return;
    if(scope==='wait'&&!waiting) return;
    if(scope==='risk'&&!riskLine) return;
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
    cardHtml+=
      '<article class="order-card">'+
      '<div class="order-card-head"><div><b>'+code+' '+item.name+'</b><span>'+item.role+' / '+item.bucket+'</span></div><span class="order-action '+target.cls+'">'+target.action+'</span></div>'+
      '<div class="order-card-grid">'+
      '<div><span>掛單價</span><b>'+fmtOrderPrice(target.price,item)+'</b><small>現價 '+fmtOrderPrice(item.price,item)+'</small></div>'+
      '<div><span>建議投入</span><b>'+fmtMoney(spend)+' 元</b><small>'+fmtShares(shares,item)+'</small></div>'+
      '<div><span>賣出觀察</span><b>'+fmtOrderPrice(sellWatch,item)+'</b><small>'+(z.sell_status||'轉弱才減碼')+'</small></div>'+
      '<div><span>失效線</span><b>'+fmtOrderPrice(failLine,item)+'</b><small>跌破先停，不硬接</small></div>'+
      '</div>'+
      '<p>'+target.note+'</p><small>'+((z.status||'區間待補')+'；可信度 '+item.confidence+'；風險 '+(item.risk_score||'N/A'))+'</small>'+
      '</article>';
  });
  out.innerHTML=html||'<tr><td colspan="8"><div class="nd">沒有符合篩選的標的。</div></td></tr>';
  if(cards) cards.innerHTML=cardHtml||'<div class="nd">沒有符合篩選的標的。</div>';
}
document.addEventListener('DOMContentLoaded',function(){
  ['orderSearch','orderBudget','orderFilter','orderScope'].forEach(function(id){
    var el=document.getElementById(id);
    if(el) el.addEventListener('input',runDailyOrders);
    if(el) el.addEventListener('change',runDailyOrders);
  });
  runDailyOrders();
});
'''
    return (
        f'<{section_tag} class="{section_class}" id="daily-orders">'
        f'<div class="tool-head"><div><div class="st">今日掛單總覽</div>'
        f'<p>預設只顯示比較能執行的標的；等回檔與高風險標的可用篩選打開。每一列是單一標的獨立試算，不是把預算分配給全部標的。</p></div>'
        f'<span class="section-meta">執行工具</span></div>'
        f'<div class="order-filter">'
        f'<label><span>搜尋代碼或名稱</span><input id="orderSearch" type="search" placeholder="輸入 2330、台積電、00662、電力..." autocomplete="off"></label>'
        f'<label><span>單一標的預算</span><input id="orderBudget" type="number" min="1000" step="1000" value="{default_budget}"></label>'
        f'<label><span>顯示</span><select id="orderScope"><option value="actionable">可執行</option><option value="wait">等回檔</option><option value="risk">風險提醒</option><option value="all">全部</option></select></label>'
        f'<label><span>類別</span><select id="orderFilter">'
        f'<option value="all">全部</option><option value="tw-etf">台股 ETF</option><option value="tw-stock">台股個股</option>'
        f'<option value="us-etf">美股 ETF</option><option value="us-stock">美股個股</option></select></label>'
        f'<label><span>規則口徑</span><select disabled><option>價格區間 + 風險 + 資料可信度</option></select></label>'
        f'</div>'
        f'<div class="order-wrap"><table class="order-table">'
        f'<thead><tr><th>標的</th><th>今日動作</th><th>參與/掛單價</th><th>建議投入</th><th>最高買價</th><th>賣出觀察價</th><th>失效線</th><th>理由</th></tr></thead>'
        f'<tbody id="orderRows"></tbody></table></div>'
        f'<div id="orderCards" class="order-cards"></div>'
        f'<div class="tool-note">建議投入是用「單一標的預算 × 該標的比例」獨立估算，不是全部標的加總配置。買入掛單價會依台股跳動單位取合法價位；台股用零股估算，海外標的未含匯率與複委託成本。賣出觀察價不是到價就賣，還要搭配轉弱、超過配置或ETF異常。</div>'
        f'</{section_tag}>'
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
    atr  = gl(res.get('atr', pd.Series(dtype=float)))
    atr_pct = gl(res.get('atr_pct', pd.Series(dtype=float)))
    boll_width = gl(res.get('boll_width', pd.Series(dtype=float)))
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

    macd_bull_now = ok(macd) and ok(msig) and macd > msig
    ma_strong_now = ok(close_val) and ok(ma20) and close_val > ma20 and (not ok(ma60) or close_val > ma60)
    kd_weak_now = isinstance(kd_signal, str) and ('死亡' in kd_signal or '偏空' in kd_signal)
    trend_strong_now = ma_strong_now and macd_bull_now and not kd_weak_now

    if ok(rsi):
        if rsi < 30:
            score += 14
            reasons.append(f'RSI {rsi:.0f} 超賣區，有反彈機會')
        elif rsi > 82:
            if trend_strong_now:
                score += 2
                reasons.append(f'RSI {rsi:.0f} 鈍化但趨勢仍強，只適合小額追蹤')
            else:
                score -= 16
                reasons.append(f'RSI {rsi:.0f} 極熱且趨勢未確認，先停追')
        elif rsi > 70:
            if trend_strong_now:
                score += 4
                reasons.append(f'RSI {rsi:.0f} 偏高但趨勢強，屬於強勢動能')
            else:
                score -= 10
                reasons.append(f'RSI {rsi:.0f} 偏高且趨勢不足，追高有風險')
        elif 40 <= rsi <= 60:
            score += 4
            reasons.append(f'RSI {rsi:.0f} 健康區間')

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
        atr=round(atr, 4) if ok(atr) else None,
        atr_pct=round(atr_pct, 2) if ok(atr_pct) else None,
        boll_width=round(boll_width, 2) if ok(boll_width) else None,
        boll_u=round(bu, 4) if ok(bu) else None,
        boll_l=round(bl, 4) if ok(bl) else None,
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
        if abs(earnings_growth) > 2.0:
            score -= 2
            notes.append('EPS/獲利成長極端，可能是虧轉盈或基期過低，已降權')
            add_score_item(items, -2, 'EPS YoY極端，分數降權')
        elif earnings_growth >= 0.20:
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
    if meta.get('finmind_chip_available'):
        data_notes.append('FinMind三大法人買賣超可用')
    if meta.get('finmind_chip_errors'):
        data_notes.append('三大法人買賣超暫時抓不到')
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
                 f'多項技術指標偏空（體質分數{score}）。建議等待落底訊號，趨勢確認轉多後再考慮進場。')

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
    'buy':  ('#0F6E56', 'rgba(29,158,117,0.22)'),
    'hold': ('#185FA5', 'rgba(24,95,165,0.20)'),
    'wait': ('#854F0B', 'rgba(186,117,23,0.22)'),
    'sell': ('#993C1D', 'rgba(216,90,48,0.22)'),
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
    short_match = re.search(r'(\d{2}/\d{2}\s+\d{2}:\d{2})', str(note or ''))
    short_note = short_match.group(1) if short_match else note
    return (f'<div class="ic"><div class="ic-l">{name}</div>'
            f'<div class="ic-v">{price:,.2f}</div>'
            f'<div class="ic-c" style="color:{col}">{arr} {sign}{chg:.2f}%</div>'
            f'<div class="ic-note"><span class="ic-note-full">{h(note)}</span>'
            f'<span class="ic-note-short">{h(short_note)}</span></div></div>')


def idx_missing_card(name, note='資料不足，暫不納入市場判斷'):
    return (
        f'<div class="ic"><div class="ic-l">{h(name)}</div>'
        f'<div class="ic-v">N/A</div>'
        f'<div class="ic-c" style="color:#BA7517">資料不足</div>'
        f'<div class="ic-note">{h(note)}</div></div>'
    )


def index_group_html(title, note, tickers, cards, collapsed=False):
    body = ''.join(
        cards.get(tk) or idx_missing_card(INDICES.get(tk, (tk, False))[0])
        for tk in tickers
    )
    if collapsed:
        return (
            f'<details class="radar-group radar-more">'
            f'<summary><span>{h(title)}</span><small>{h(note)}</small></summary>'
            f'<div class="igrid radar-grid">{body}</div>'
            f'</details>'
        )
    return (
        f'<div class="radar-group">'
        f'<div class="radar-head"><b>{h(title)}</b><small>{h(note)}</small></div>'
        f'<div class="igrid radar-grid">{body}</div>'
        f'</div>'
    )


def indices_radar_html(cards):
    return (
        f'<section class="sc market-radar" id="market-radar">'
        f'<div class="market-radar-title"><div><div class="st">市場雷達</div>'
        f'<p>比較主要市場方向，不直接當成買賣訊號。</p></div>'
        f'<button type="button" onclick="showMode(\'market-mode\')">查看全部</button></div>'
        f'<div class="radar-layout">'
        f'{index_group_html("台股", "本地市場本體", ["^TWII"], cards)}'
        f'{index_group_html("美股科技 / 風險", "那斯達克、費半與 VIX 影響台股科技鏈", ["^GSPC", "^IXIC", "^SOX", "^VIX"], cards)}'
        f'{index_group_html("亞洲核心", "看日本、韓國、香港是否同步", ["^N225", "^KS11", "^HSI"], cards)}'
        f'{index_group_html("更多亞洲觀察", "恒生科技與 KOSDAQ 只作次要風險情緒參考", ["HSTECH.HK", "^KQ11"], cards, collapsed=True)}'
        f'</div></section>'
    )


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
    if eps_growth is not None and abs(eps_growth) > 2.0:
        eps_growth_text = 'EPS基期異常'
        eps_growth_hint = '虧轉盈/低基期造成極端值，已降權'
    else:
        eps_growth_text = fmt_ratio_pct(eps_growth)
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
        metric_tile('EPS/獲利成長', eps_growth_text, eps_growth_hint),
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
    if eps_growth is not None and abs(eps_growth) > 2.0:
        warnings_list.append('EPS 年增幅度極端，可能是低基期或虧轉盈/盈轉虧，不能直接解讀成景氣大好或大壞。')
    elif eps_growth is not None and eps_growth < 0:
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
    sp   = sparkline(hist_close, w=520, h=140)
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
    chip_detail_html = institutional_flow_html(a.get('meta') or {}, price)
    factor_html = factor_score_html(a, ticker)
    quick_status = price_zone.get('status') if price_zone else a['stxt']
    quick_action = price_zone.get('action') if price_zone else rec['dca'][0]
    quick_summary = price_zone.get('summary') if price_zone else rec['dca'][2]
    quick_html = (
        f'<div class="quick-take">'
        f'<div><span>現在狀態</span><b>{h(quick_status)}</b><small>{h(quick_summary)}</small></div>'
        f'<div><span>今天怎麼做</span><b>{h(quick_action)}</b><small>先看價格區間，再看資料可信度與風險。</small></div>'
        f'</div>'
    )
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
    strategy_more_html = (
        f'<details class="card-more strategy-more"><summary>看短線與定期定額建議</summary>'
        f'{rec_html}'
        f'{invest_html}'
        f'</details>'
    )
    quality = data_quality_note(ticker, a)
    asset_type = 'ETF' if is_etf_like(ticker) else '個股'
    profile = invest_plan.get('profile') or {}
    role = profile.get('role') or ('ETF 觀察' if is_etf_like(ticker) else '個股研究')
    target_id = target_dom_id(ticker)
    tab_id = target_tab_id(ticker)
    technical_html = (
        f'<div class="detail-section-head"><div><span>進階技術</span><h3>走勢與短線訊號</h3></div>'
        f'<p>技術指標只協助辨識節奏，不單獨決定長期價值。</p></div>'
        f'<div class="trend-chart-card"><div class="spark">{sp}</div>{price_ref_html}</div>'
        f'<div class="ig technical-metrics">'
        f'<div class="ic2"><div class="il">KD 值</div><div class="iv" style="color:{kd_c}">{kd_v}</div>'
        f'<div class="is" style="color:{kd_c}">{kd_s}</div></div>'
        f'<div class="ic2"><div class="il">RSI</div><div class="iv" style="color:{rsi_c}">{rsi_v}</div>'
        f'<div class="is" style="color:{rsi_c}">{rsi_s}</div></div>'
        f'<div class="ic2"><div class="il">20日乖離率</div><div class="iv" style="color:{dv_c}">{dv_v}</div>'
        f'<div class="is" style="color:{dv_c}">{dv_s}</div></div>'
        f'<div class="ic2"><div class="il">MACD</div><div class="iv" style="color:{macd_c}">{macd_v}</div></div>'
        f'</div>'
        f'{boll_html}'
        f'{reasons_html}'
    )
    risk_html = (
        f'<div class="detail-section-head"><div><span>風險檢查</span><h3>可能承受多大的波動</h3></div>'
        f'<p>高分不代表低風險；價格位置、回撤與 Beta 要分開看。</p></div>'
        f'{week52_html}'
        f'{ext_html}'
        f'{factor_html}'
    )
    fundamental_title = 'ETF 結構與成本' if is_etf_like(ticker) else '公司經營與獲利'
    fundamental_note = (
        'ETF 不看單一公司營收，重點是費用、折溢價、配息來源、集中度與總報酬。'
        if is_etf_like(ticker)
        else '個股先確認營收與獲利是否支持股價，再判斷估值是否合理。'
    )
    fundamental_html = (
        f'<div class="detail-section-head"><div><span>查證資料</span><h3>{h(fundamental_title)}</h3></div>'
        f'<p>{h(fundamental_note)}</p></div>'
        f'{detail_html}'
    )
    decision_panel_html = (
        f'<div class="detail-section-head"><div><span>價格計畫</span><h3>今天怎麼做</h3></div>'
        f'<p>先決定是否行動，再看要分幾批、什麼情況停止。</p></div>'
        f'{zone_html}'
        f'{chip_detail_html}'
        f'{strategy_more_html}'
    )
    summary_tone = action_tone(quick_status)
    summary_tone_class = {
        'ok': 'target-summary-ok',
        'wait': 'target-summary-wait',
        'stop': 'target-summary-stop',
    }.get(summary_tone, 'target-summary-wait')

    return (
        f'<article class="sc target-card {summary_tone_class}" id="{target_id}" data-tab="{tab_id}" data-label="{h(disp)} {h(name)}">'
        f'<div class="target-card-summary">'
        f'<div class="target-summary-name"><span>{h(asset_type)} · {h(role)}</span><b>{h(disp)} {h(name)}</b>'
        f'<small>{h(price_caption)}</small></div>'
        f'<div class="target-summary-price"><span>現價</span><b>{price:,.2f}</b><small style="color:{pc}">{arr} {sgn}{chg:.2f}%</small></div>'
        f'<div class="target-summary-score"><span>體質</span><b style="color:{sc_col}">{sc}</b><small>{h(a["stxt"])}</small></div>'
        f'<div class="target-summary-status"><span>價格狀態</span><b>{h(quick_status)}</b><small>{h(quick_action)}</small></div>'
        f'<button class="target-open-btn" type="button" onclick="openTargetDetail(\'{target_id}\',\'{tab_id}\')">查看分析</button>'
        f'</div>'
        f'<div class="target-detail-view">'
        f'<header class="target-detail-header">'
        f'<button class="target-back-btn" onclick="returnToFocusSpot()" type="button">返回剛剛位置</button>'
        f'<div class="target-detail-heading"><div class="target-kicker"><span>{h(asset_type)}</span><span>{h(role)}</span>'
        f'<span>可信度 {h(quality["confidence"])}</span></div>'
        f'<h2>{h(disp)} <small>{h(name)}</small></h2><p>{h(price_caption)}</p></div>'
        f'<div class="target-live-price"><span>目前價格</span><b>{price:,.2f}</b>'
        f'<small style="color:{pc}">{arr} {sgn}{chg:.2f}%</small></div>'
        f'</header>'
        f'<div class="target-snapshot">'
        f'<div><span>價格狀態</span><b>{h(quick_status)}</b><small>{h(quick_summary)}</small></div>'
        f'<div><span>今日做法</span><b>{h(quick_action)}</b><small>依價格區間執行，不把單一訊號當預測。</small></div>'
        f'<div><span>體質分數</span><b style="color:{sc_col}">{sc}</b><small>{h(a["stxt"])}</small></div>'
        f'<div><span>資料可信度</span><b>{h(quality["confidence"])}</b><small>{h(quality["reason"])}</small></div>'
        f'</div>'
        f'<div class="target-detail-layout">'
        f'<div class="target-detail-main">'
        f'<nav class="target-detail-tabs" aria-label="{h(name)}分析分頁">'
        f'<button class="on" data-target-section="decision" onclick="showTargetSection(\'{target_id}\',\'decision\')" type="button">決策</button>'
        f'<button data-target-section="fundamental" onclick="showTargetSection(\'{target_id}\',\'fundamental\')" type="button">{h("ETF資料" if is_etf_like(ticker) else "基本面")}</button>'
        f'<button data-target-section="risk" onclick="showTargetSection(\'{target_id}\',\'risk\')" type="button">風險</button>'
        f'<button data-target-section="technical" onclick="showTargetSection(\'{target_id}\',\'technical\')" type="button">技術</button>'
        f'</nav>'
        f'<section class="target-detail-panel on" data-target-panel="decision">{decision_panel_html}</section>'
        f'<section class="target-detail-panel" data-target-panel="fundamental">{fundamental_html}</section>'
        f'<section class="target-detail-panel" data-target-panel="risk">{risk_html}</section>'
        f'<section class="target-detail-panel" data-target-panel="technical">{technical_html}</section>'
        f'</div>'
        f'<aside class="target-decision-rail">'
        f'{quick_html}'
        f'{decision_html}'
        f'<div class="target-score-card"><div><span>體質分數</span><b style="color:{sc_col}">{sc}</b></div>'
        f'<div class="sr"><div class="sbw"><div class="sbf" style="width:{sc}%;background:{sc_col}"></div></div></div>'
        f'<span class="sbadge" style="background:{badge_bg};color:{badge_tc}">{a["stxt"]}</span></div>'
        f'<div class="target-source-note"><b>資料項目</b><p>{h(quality["notes"])}</p></div>'
        f'</aside>'
        f'</div>'
        f'<div class="target-disclaimer">以上為規則化因子分析，僅供理解資料與建立紀律，不構成投資建議。</div>'
        f'</div>'
        f'</article>'
    )


# =============================================================
# HTML 模板
# =============================================================

CSS = '''<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--t:#172033;--t2:#657282;--bg:#eef2f6;--card:#fff;--card2:#f7f9fc;--surface:#edf3f8;--bdr:rgba(35,48,70,0.10);--ok:#16825f;--ok-bg:rgba(29,158,117,0.20);--warn:#9A5B0D;--warn-bg:rgba(186,117,23,0.22);--risk:#B44724;--risk-bg:rgba(216,90,48,0.22);--info:#1E5F92;--info-bg:rgba(24,95,165,0.18);--cold:#2F6C8F;--cold-bg:rgba(47,108,143,0.20)}
body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--t);line-height:1.6}
html{scroll-behavior:smooth}
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
.ic-note-short{display:none}
.mobile-jump{display:none}
.mode-switch{position:sticky;top:55px;z-index:80;display:flex;gap:6px;background:var(--bg);padding:10px 0 6px;border-bottom:1px solid transparent;margin-bottom:4px}
.mode-btn{border:1px solid var(--bdr);background:var(--card);color:var(--t2);border-radius:999px;padding:8px 14px;font-size:13px;font-weight:800;cursor:pointer;font-family:inherit}
.mode-btn.on{background:var(--t);color:var(--bg);border-color:var(--t)}
.mode-pane{display:none}
.mode-pane.on{display:block}
.market-radar{margin:14px 0}
.radar-layout{display:grid;gap:10px;margin-top:10px}
.radar-group{background:var(--card2);border:1px solid var(--bdr);border-radius:10px;padding:10px}
.radar-head{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;margin-bottom:8px}
.radar-head b,.radar-more summary span{font-size:13px;color:var(--t);font-weight:800}
.radar-head small,.radar-more summary small{font-size:10px;color:var(--t2);line-height:1.45}
.radar-grid{margin:0;grid-template-columns:repeat(auto-fit,minmax(145px,1fr))}
.radar-more summary{cursor:pointer;display:flex;justify-content:space-between;gap:10px;align-items:flex-start;list-style:none}
.radar-more summary::-webkit-details-marker{display:none}
.radar-more summary::after{content:"＋";color:var(--t2);font-weight:800}
.radar-more[open] summary::after{content:"－"}
.radar-more .radar-grid{margin-top:8px}
.target-section{margin-top:14px}
.desktop-top-grid,.desktop-mid-grid{display:grid;gap:14px;margin:14px 0;align-items:start}
.desktop-top-grid{grid-template-columns:minmax(0,1.05fr) minmax(360px,.95fr)}
.desktop-mid-grid{grid-template-columns:minmax(360px,.9fr) minmax(0,1.1fr)}
.desktop-top-grid .intro-card,.desktop-top-grid .market-radar,.desktop-mid-grid .dca-tool,.desktop-mid-grid .buy-tool{margin:0}
.core-etfs,.today-focus,.target-overview,.visual-board{margin:14px 0}
.target-hub-tabs{display:inline-flex;gap:6px;background:var(--surface);border:1px solid var(--bdr);border-radius:999px;padding:4px;margin-top:10px}
.target-hub-btn{border:0;background:transparent;color:var(--t2);border-radius:999px;padding:7px 12px;font-size:12px;font-weight:850;font-family:inherit;cursor:pointer}
.target-hub-btn.on{background:var(--t);color:var(--bg)}
.target-hub-pane{display:none;margin-top:10px}
.target-hub-pane.on{display:block}
.target-hub .embedded-order{margin-top:0}
.visual-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-top:10px}
.visual-main{background:var(--card2);border:1px solid var(--bdr);border-radius:10px;padding:12px;box-shadow:0 1px 0 rgba(20,30,45,0.03)}
.temp-hot{background:linear-gradient(135deg,var(--risk-bg),var(--card));border-color:rgba(216,90,48,0.34)}
.temp-warm{background:linear-gradient(135deg,var(--warn-bg),var(--card));border-color:rgba(186,117,23,0.34)}
.temp-neutral{background:linear-gradient(135deg,var(--info-bg),var(--card));border-color:rgba(24,95,165,0.28)}
.temp-cool{background:linear-gradient(135deg,var(--cold-bg),var(--card));border-color:rgba(47,108,143,0.32)}
.temp-cold{background:linear-gradient(135deg,rgba(80,103,125,0.24),var(--card));border-color:rgba(80,103,125,0.36)}
.visual-main span{display:block;font-size:10px;color:var(--t2);font-weight:800;margin-bottom:4px}
.visual-main b{display:block;font-size:18px;color:var(--t);line-height:1.35}
.visual-main small{display:block;font-size:10px;color:var(--t2);line-height:1.5;margin-top:4px}
.visual-bars{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-top:8px}
.visual-bar{background:var(--card2);border:1px solid var(--bdr);border-radius:10px;padding:10px}
.visual-bar div{display:flex;justify-content:space-between;gap:8px;align-items:center}
.visual-bar b{font-size:12px;color:var(--t)}
.visual-bar span{font-size:10px;color:var(--t2);font-weight:800}
.visual-bar i{display:block;height:7px;background:rgba(0,0,0,0.08);border-radius:999px;overflow:hidden;margin:8px 0 5px}
.visual-bar em{display:block;height:100%;border-radius:999px}
.visual-ok em{background:#1D9E75}
.visual-wait em{background:#BA7517}
.visual-stop em{background:#D85A30}
.visual-bar small{font-size:10px;color:var(--t2);line-height:1.45}
.core-grid,.focus-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:8px;margin-top:10px}
.mini-target{background:var(--card2);border:1px solid var(--bdr);border-radius:8px;padding:11px;display:flex;flex-direction:column;gap:8px;min-width:0}
.mini-head{display:flex;justify-content:space-between;gap:8px;align-items:flex-start}
.mini-head b{display:block;font-size:13px;color:var(--t);line-height:1.35}
.mini-head small{display:block;font-size:10px;color:var(--t2);margin-top:2px;line-height:1.35}
.mini-head>span{font-size:10px;font-weight:800;border-radius:999px;padding:3px 7px;white-space:nowrap}
.mini-ok .mini-head>span,.overview-ok .overview-status span{color:var(--ok);background:var(--ok-bg)}
.mini-wait .mini-head>span,.overview-wait .overview-status span{color:var(--warn);background:var(--warn-bg)}
.mini-stop .mini-head>span,.overview-stop .overview-status span{color:var(--risk);background:var(--risk-bg)}
.mini-body{display:grid;grid-template-columns:.8fr .75fr 1.45fr;gap:6px}
.mini-body div{background:var(--card);border:1px solid var(--bdr);border-radius:7px;padding:7px;min-width:0}
.mini-body span,.overview-row span{display:block;font-size:10px;color:var(--t2);line-height:1.3}
.mini-body b{display:block;font-size:12px;color:var(--t);font-variant-numeric:tabular-nums;overflow-wrap:anywhere;line-height:1.35}
.mini-score-cell i,.overview-score i{display:block;height:5px;background:rgba(0,0,0,0.08);border-radius:999px;overflow:hidden;margin-top:5px}
.mini-score-cell em,.overview-score em{display:block;height:100%;border-radius:999px}
.score-good em{background:var(--ok)}
.score-mid em{background:var(--warn)}
.score-low em{background:var(--risk)}
.mini-target p{font-size:11px;color:var(--t2);line-height:1.55}
.mini-link{display:inline-flex;align-self:flex-start;text-decoration:none;color:#0F6E56;background:rgba(29,158,117,0.1);border:1px solid rgba(29,158,117,0.22);border-radius:999px;padding:5px 10px;font-size:11px;font-weight:800}
.mini-meta{display:flex;flex-wrap:wrap;gap:5px}
.mini-meta span{font-size:10px;color:var(--t2);background:var(--card);border:1px solid var(--bdr);border-radius:999px;padding:3px 7px}
.core-alt{border-top:1px solid var(--bdr);margin-top:10px;padding-top:8px}
.core-alt summary{cursor:pointer;font-size:11px;font-weight:800;list-style:none;color:var(--t)}
.core-alt summary::-webkit-details-marker{display:none}
.core-alt summary::after{content:"＋";float:right;color:var(--t2)}
.core-alt[open] summary::after{content:"－"}
.core-alt-row{display:grid;grid-template-columns:1.2fr .8fr 1fr auto;gap:8px;align-items:center;background:var(--card2);border:1px solid var(--bdr);border-radius:8px;padding:8px;margin-top:7px}
.core-alt-row b{font-size:12px;color:var(--t)}
.core-alt-row span{font-size:11px;font-weight:800;color:var(--t)}
.core-alt-row small{font-size:10px;color:var(--t2);line-height:1.35}
.focus-group{border-top:1px solid var(--bdr);padding-top:10px;margin-top:10px}
.focus-head{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;margin-bottom:8px}
.focus-head b{font-size:13px;color:var(--t)}
.focus-head span,.focus-group>small{font-size:10px;color:var(--t2);line-height:1.45}
.overview-controls{display:grid;grid-template-columns:minmax(220px,1.35fr) repeat(3,minmax(150px,1fr));gap:8px;margin-top:10px;background:var(--surface);border:1px solid var(--bdr);border-radius:10px;padding:10px}
.overview-controls label{display:grid;gap:4px;font-size:10px;color:var(--t2)}
.overview-controls input,.overview-controls select{border:1px solid var(--bdr);background:var(--card);color:var(--t);border-radius:8px;padding:8px;font-size:13px;font-family:inherit;outline:none}.overview-controls input:focus,.overview-controls select:focus{border-color:rgba(29,158,117,0.55);box-shadow:0 0 0 3px rgba(29,158,117,0.12)}
.overview-list{display:grid;gap:6px;margin-top:10px}
.overview-row{display:grid;grid-template-columns:minmax(190px,1.35fr) .48fr .55fr minmax(150px,1fr) .42fr auto;gap:8px;align-items:center;background:var(--card2);border:1px solid var(--bdr);border-radius:8px;padding:9px 10px}
.overview-name b{display:block;font-size:13px;color:var(--t);line-height:1.35}
.overview-name small{display:block;font-size:10px;color:var(--t2);line-height:1.35}
.overview-score b,.overview-price b,.overview-confidence b{display:block;font-size:13px;color:var(--t);font-variant-numeric:tabular-nums}
.overview-score b{display:inline-flex;min-width:34px;justify-content:center;border-radius:999px;padding:2px 7px;font-weight:900}
.score-good b{color:var(--ok);background:var(--ok-bg)}
.score-mid b{color:var(--warn);background:var(--warn-bg)}
.score-low b{color:var(--risk);background:var(--risk-bg)}
.overview-status span{display:inline-flex;border-radius:999px;padding:3px 7px;font-size:10px;font-weight:800;margin-bottom:2px}
.overview-status small{display:block;font-size:10px;color:var(--t2);line-height:1.35;overflow-wrap:anywhere}
.overview-link{justify-self:end}
.section-head{display:flex;justify-content:space-between;gap:14px;align-items:flex-end;background:var(--card);border:1px solid var(--bdr);border-radius:12px;padding:14px 16px;margin:14px 0 10px}
.section-head p{font-size:11px;color:var(--t2);line-height:1.6;margin-top:3px}
.section-head span{font-size:11px;color:var(--t2);background:var(--card2);border:1px solid var(--bdr);border-radius:999px;padding:5px 10px;white-space:nowrap}
.tnav{display:flex;gap:2px;border-bottom:1px solid rgba(0,0,0,0.1);margin:20px 0;flex-wrap:wrap}
.tb{background:transparent;border:none;padding:10px 16px 11px;font-size:13px;color:var(--t2);cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px;font-family:inherit;border-radius:8px 8px 0 0;transition:color .15s}
.tb:hover,.tb.on{color:var(--t)}
.tb.on{border-bottom-color:var(--t);font-weight:600}
.cgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px;padding-bottom:40px}
.tc{display:none}
.tc.on{display:block}
.sc{background:var(--card);border:1px solid var(--bdr);border-radius:12px;padding:16px}
.sh{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px}
.st{font-size:17px;font-weight:850;color:var(--t);letter-spacing:0}
.sn{font-size:11px;color:var(--t2);margin-top:2px}
.sp{font-size:19px;font-weight:700;font-variant-numeric:tabular-nums}
.sc2{font-size:12px;font-weight:600;margin-top:2px}
.price-caption{font-size:9px;color:var(--t2);line-height:1.35;margin-top:2px;max-width:120px}
.back-to-overview{display:none;margin-top:6px;border:1px solid rgba(29,158,117,0.22);background:rgba(29,158,117,0.10);color:#0F6E56;border-radius:999px;padding:5px 9px;font-size:11px;font-weight:800;font-family:inherit;cursor:pointer}
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
.zone-bar{margin:7px 0 10px}
.zone-track{position:relative;height:9px;border-radius:999px;background:rgba(0,0,0,0.08);overflow:hidden}
.zone-track .seg{position:absolute;top:0;bottom:0}
.zone-track .danger{background:rgba(216,90,48,0.22)}
.zone-track .ok{background:rgba(29,158,117,0.28)}
.zone-track .warm{background:rgba(186,117,23,0.25)}
.zone-track .hot{background:rgba(216,90,48,0.32)}
.zone-track i{position:absolute;top:-4px;width:17px;height:17px;border-radius:50%;background:var(--t);border:3px solid var(--card);transform:translateX(-50%);box-shadow:0 1px 4px rgba(0,0,0,0.18)}
.zone-labels{display:grid;grid-template-columns:repeat(4,1fr);font-size:9px;color:var(--t2);margin-top:4px;text-align:center}
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
.zone-subtitle{font-size:10px;color:var(--t2);font-weight:800;margin:9px 0 5px}
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
.decision-card-v2{padding:14px;background:linear-gradient(180deg,rgba(24,95,165,0.08),var(--card));border-color:rgba(24,95,165,0.20)}
.confidence-pill{border:1px solid var(--bdr);background:var(--card2);border-radius:999px;padding:4px 8px;white-space:nowrap}
.decision-hero-row{display:grid;grid-template-columns:54px 1fr;gap:10px;align-items:start;background:#101720;border:1px solid var(--bdr);border-radius:9px;padding:12px;margin:8px 0 10px}
.decision-hero-row span,.decision-action-grid span,.decision-mini-card span,.decision-meter span{font-size:10px;color:var(--t2);font-weight:850}
.decision-hero-row strong{font-size:20px;line-height:1.35;color:var(--t);letter-spacing:0;font-weight:950}
.decision-action-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px}
.decision-action-grid>div{background:var(--card2);border:1px solid var(--bdr);border-radius:9px;padding:10px}
.decision-action-grid p{font-size:11px;color:var(--t);line-height:1.6;margin-top:4px}
.decision-meter-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:8px 0 10px}
.decision-meter{background:var(--card2);border:1px solid var(--bdr);border-radius:9px;padding:9px;min-width:0}
.decision-meter>div{display:flex;justify-content:space-between;gap:8px;align-items:center}
.decision-meter b{font-size:16px;line-height:1;font-variant-numeric:tabular-nums}
.decision-meter i{display:block;height:7px;background:rgba(101,114,130,0.18);border-radius:999px;margin:7px 0 5px;overflow:hidden}
.decision-meter em{display:block;height:100%;border-radius:999px}
.decision-meter small{display:block;font-size:9px;color:var(--t2);line-height:1.35;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.decision-evidence-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:8px}
.decision-mini-card{background:var(--card2);border:1px solid var(--bdr);border-radius:9px;padding:10px;min-width:0}
.decision-mini-card b{display:block;font-size:13px;color:var(--t);line-height:1.35;margin-top:3px}
.decision-mini-card p{font-size:10px;color:var(--t2);line-height:1.55;margin-top:5px}
.decision-mini-positive{border-color:rgba(29,158,117,0.30);background:var(--ok-bg)}
.decision-mini-mixed{border-color:rgba(186,117,23,0.32);background:var(--warn-bg)}
.decision-mini-negative,.decision-mini-risk{border-color:rgba(216,90,48,0.30);background:var(--risk-bg)}
.decision-more{margin-top:10px;border-top:1px solid var(--bdr);padding-top:8px}
.decision-more summary{cursor:pointer;list-style:none;font-size:11px;font-weight:900;color:var(--t)}
.decision-more summary::-webkit-details-marker{display:none}
.decision-more summary::after{content:"＋";float:right;color:var(--t2)}
.decision-more[open] summary::after{content:"－"}
.quick-take{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:10px 0}
.quick-take div{background:var(--card2);border:1px solid var(--bdr);border-radius:8px;padding:10px 11px}
.quick-take span{display:block;font-size:10px;color:var(--t2);margin-bottom:3px;font-weight:700}
.quick-take b{display:block;font-size:14px;color:var(--t)}
.quick-take small{display:block;font-size:10px;color:var(--t2);line-height:1.45;margin-top:3px}
.card-more,.zone-more,.intro-more{border-top:1px solid var(--bdr);margin-top:9px;padding-top:8px}
.card-more summary,.zone-more summary,.intro-more summary{cursor:pointer;font-size:11px;color:var(--t);font-weight:800;list-style:none}
.card-more summary::-webkit-details-marker,.zone-more summary::-webkit-details-marker,.intro-more summary::-webkit-details-marker{display:none}
.card-more summary::after,.zone-more summary::after,.intro-more summary::after{content:"＋";float:right;color:var(--t2)}
.card-more[open] summary::after,.zone-more[open] summary::after,.intro-more[open] summary::after{content:"－"}
.detail-box{background:var(--card2);border:1px solid var(--bdr);border-radius:8px;padding:10px 12px;margin:10px 0}
.detail-title{font-size:12px;font-weight:700;color:var(--t);margin-bottom:8px}
.detail-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:6px}
.metric-tile{background:var(--card);border:1px solid var(--bdr);border-radius:7px;padding:7px 8px;min-width:0}
.metric-tile span{display:block;font-size:10px;color:var(--t2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.metric-tile b{display:block;font-size:13px;color:var(--t);font-variant-numeric:tabular-nums;line-height:1.35;overflow-wrap:anywhere}
.metric-tile small{display:block;font-size:9px;color:var(--t2);line-height:1.35;margin-top:2px}
.detail-note{font-size:10px;color:var(--t2);line-height:1.55;margin-top:8px}
.chip-flow-box{background:var(--card2);border:1px solid var(--bdr);border-radius:8px;padding:12px;margin:10px 0}
.chip-positive{border-color:rgba(29,158,117,0.35);background:linear-gradient(180deg,rgba(29,158,117,0.10),var(--card2))}
.chip-mixed{border-color:rgba(186,117,23,0.34);background:linear-gradient(180deg,rgba(186,117,23,0.10),var(--card2))}
.chip-negative{border-color:rgba(216,90,48,0.34);background:linear-gradient(180deg,rgba(216,90,48,0.10),var(--card2))}
.chip-neutral{border-color:rgba(24,95,165,0.28);background:linear-gradient(180deg,rgba(24,95,165,0.08),var(--card2))}
.chip-flow-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:8px}
.chip-flow-head span{display:block;font-size:10px;color:var(--t2);font-weight:800;margin-bottom:2px}
.chip-flow-head b{display:block;font-size:16px;color:var(--t);line-height:1.35}
.chip-flow-head strong{display:grid;place-items:center;min-width:38px;height:38px;border-radius:10px;background:var(--card);border:1px solid var(--bdr);font-size:15px;color:var(--t);font-variant-numeric:tabular-nums}
.chip-positive .chip-flow-head strong{color:var(--ok);background:var(--ok-bg)}
.chip-mixed .chip-flow-head strong{color:var(--warn);background:var(--warn-bg)}
.chip-negative .chip-flow-head strong{color:var(--risk);background:var(--risk-bg)}
.chip-flow-box>p{font-size:11px;color:var(--t);line-height:1.6;margin-bottom:9px}
.chip-flow-grid{grid-template-columns:repeat(4,minmax(0,1fr))}
.chip-flow-visual{display:grid;gap:7px;margin:8px 0 10px}
.flow-bar-row{display:grid;grid-template-columns:68px minmax(70px,1fr) 104px;gap:8px;align-items:center}
.flow-bar-row span{font-size:10px;color:var(--t2);font-weight:850}
.flow-bar-row b{font-size:10px;color:var(--t);text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.flow-track{height:8px;background:rgba(101,114,130,0.18);border-radius:999px;overflow:hidden}
.flow-track i{display:block;height:100%;border-radius:999px;background:var(--t2)}
.flow-in .flow-track i{background:var(--ok)}
.flow-out .flow-track i{background:var(--risk)}
.flow-flat .flow-track i{background:var(--t2)}
.chip-flow-formula{border-top:1px solid var(--bdr);margin-top:9px;padding-top:8px}
.chip-flow-formula summary{cursor:pointer;list-style:none;font-size:10px;font-weight:900;color:var(--t)}
.chip-flow-formula summary::-webkit-details-marker{display:none}
.chip-flow-formula summary::after{content:"＋";float:right;color:var(--t2)}
.chip-flow-formula[open] summary::after{content:"－"}
.chip-flow-formula p{font-size:10px;color:var(--t2);line-height:1.55;margin-top:6px}
.holding-chips{margin-top:8px;border-top:1px solid var(--bdr);padding-top:8px}
.holding-chips b{display:block;font-size:10px;color:var(--t2);margin-bottom:5px}
.holding-chips div{display:flex;flex-wrap:wrap;gap:5px}
.holding-chips span{font-size:10px;color:var(--t);background:var(--card);border:1px solid var(--bdr);border-radius:999px;padding:3px 7px;line-height:1.35}
.intro-card{margin:14px 0}
.market-brief{display:block}
.market-brief h2{font-size:28px;line-height:1.18;margin-top:6px;color:var(--t);letter-spacing:0;font-weight:900}
.market-brief p{font-size:12px;color:var(--t2);line-height:1.65;margin-top:8px}
.plain-take{margin-top:10px;background:var(--info-bg);border:1px solid rgba(24,95,165,0.28);border-radius:9px;padding:10px 12px}
.plain-take b{display:block;font-size:13px;line-height:1.5;color:var(--t)}
.plain-take span{display:block;font-size:10px;line-height:1.55;color:var(--t2);margin-top:4px}
.status-strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(135px,1fr));gap:8px;margin-top:12px}
.status-strip div,.market-reason-grid div{background:var(--card2);border:1px solid var(--bdr);border-radius:8px;padding:10px}
.status-strip .status-ok{background:var(--ok-bg);border-color:rgba(29,158,117,0.30)}
.status-strip .status-warn{background:var(--warn-bg);border-color:rgba(186,117,23,0.34)}
.status-strip .status-risk{background:var(--risk-bg);border-color:rgba(216,90,48,0.34)}
.status-strip .status-info{background:var(--info-bg);border-color:rgba(24,95,165,0.28)}
.status-strip span{display:block;font-size:10px;color:var(--t2);margin-bottom:3px}
.status-strip b{display:block;font-size:15px;color:var(--t)}
.status-strip small{display:block;font-size:10px;color:var(--t2);line-height:1.45;margin-top:3px}
.market-reason-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-top:8px}
.market-reason-grid b{display:block;font-size:12px;color:var(--t);margin-bottom:5px}
.market-reason-grid ul{padding-left:16px}
.market-reason-grid li{font-size:11px;color:var(--t2);line-height:1.55;margin:2px 0}
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
.section-meta{display:none}
.core-etfs{border-color:rgba(29,158,117,0.28);background:linear-gradient(180deg,rgba(29,158,117,0.08),var(--card))}
.target-overview{border-color:rgba(24,95,165,0.22)}
.today-focus{border-color:rgba(186,117,23,0.24)}
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
.sector-flow-radar{margin:14px 0;border-color:rgba(24,95,165,0.22)}
.sector-flow-legend{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.sector-flow-legend span{font-size:10px;font-weight:900;border-radius:999px;padding:4px 8px;border:1px solid var(--bdr)}
.legend-tide{color:var(--ok);background:var(--ok-bg)}
.legend-rotate{color:#176c9c;background:rgba(24,95,165,0.16)}
.legend-watch{color:var(--warn);background:var(--warn-bg)}
.legend-ebb{color:var(--risk);background:var(--risk-bg)}
.sector-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(245px,1fr));gap:9px;margin-top:10px}
.sector-card{background:var(--card2);border:1px solid var(--bdr);border-radius:10px;padding:12px;min-width:0}
.sector-tide{border-color:rgba(29,158,117,0.36);background:linear-gradient(180deg,rgba(29,158,117,0.12),var(--card2))}
.sector-rotate{border-color:rgba(24,95,165,0.34);background:linear-gradient(180deg,rgba(24,95,165,0.11),var(--card2))}
.sector-watch{border-color:rgba(186,117,23,0.34);background:linear-gradient(180deg,rgba(186,117,23,0.12),var(--card2))}
.sector-ebb{border-color:rgba(216,90,48,0.34);background:linear-gradient(180deg,rgba(216,90,48,0.12),var(--card2))}
.sector-card-head{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;margin-bottom:9px}
.sector-card-head b{display:block;font-size:14px;color:var(--t);line-height:1.35}
.sector-card-head small{display:block;font-size:10px;color:var(--t2);line-height:1.35;margin-top:2px}
.sector-card-head strong{white-space:nowrap;border-radius:999px;padding:4px 8px;font-size:11px;font-weight:900;background:var(--card);border:1px solid var(--bdr)}
.sector-tide .sector-card-head strong{color:var(--ok);background:var(--ok-bg);border-color:rgba(29,158,117,0.34)}
.sector-rotate .sector-card-head strong{color:#1E5F92;background:rgba(24,95,165,0.16);border-color:rgba(24,95,165,0.30)}
.sector-watch .sector-card-head strong{color:var(--warn);background:var(--warn-bg);border-color:rgba(186,117,23,0.34)}
.sector-ebb .sector-card-head strong{color:var(--risk);background:var(--risk-bg);border-color:rgba(216,90,48,0.34)}
.sector-metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px;margin-bottom:8px}
.sector-metrics div{background:var(--card);border:1px solid var(--bdr);border-radius:7px;padding:7px}
.sector-metrics span{display:block;font-size:10px;color:var(--t2);line-height:1.25}
.sector-metrics b{display:block;font-size:14px;color:var(--t);font-variant-numeric:tabular-nums;margin-top:2px}
.sector-card p{font-size:11px;color:var(--t2);line-height:1.6;margin-bottom:9px}
.sector-targets{display:flex;flex-wrap:wrap;gap:5px}
.sector-targets span{font-size:10px;color:var(--t);background:var(--card);border:1px solid var(--bdr);border-radius:999px;padding:4px 7px;line-height:1.25}
.sector-targets small{display:inline;color:var(--t2);margin-left:4px}
.order-wrap{overflow-x:auto;margin-top:10px;border:1px solid var(--bdr);border-radius:8px;background:var(--card2)}
.order-table{width:100%;border-collapse:collapse;min-width:960px}
.order-table th,.order-table td{padding:9px 10px;border-bottom:1px solid var(--bdr);font-size:11px;text-align:left;vertical-align:top}
.order-table th{font-size:10px;color:var(--t2);font-weight:700;background:rgba(0,0,0,0.03)}
.order-table td b{display:block;font-size:12px;color:var(--t);margin-bottom:2px}
.order-table td small{display:block;font-size:10px;color:var(--t2);line-height:1.45}
.order-action{display:inline-flex;padding:3px 8px;border-radius:999px;font-size:10px;font-weight:700;white-space:nowrap}
.order-ok{color:#0F6E56;background:rgba(29,158,117,0.12)}
.order-momentum{color:#185FA5;background:rgba(24,95,165,0.14)}
.order-wait{color:#854F0B;background:rgba(186,117,23,0.12)}
.order-stop{color:#993C1D;background:rgba(216,90,48,0.12)}
.order-filter{display:grid;grid-template-columns:minmax(220px,1.3fr) repeat(4,minmax(130px,1fr));gap:8px;margin-top:10px}
.order-filter label{display:grid;gap:4px;font-size:10px;color:var(--t2)}
.order-filter input,.order-filter select{border:1px solid var(--bdr);background:var(--card2);color:var(--t);border-radius:8px;padding:8px;font-size:13px}
.order-cards{display:none;gap:8px;margin-top:10px}
.order-card{background:var(--card2);border:1px solid var(--bdr);border-radius:8px;padding:11px}
.order-card-head{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;margin-bottom:8px}
.order-card-head b{display:block;font-size:13px;color:var(--t)}
.order-card-head span:not(.order-action){display:block;font-size:10px;color:var(--t2);margin-top:2px}
.order-card-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px}
.order-card-grid div{background:var(--card);border:1px solid var(--bdr);border-radius:7px;padding:8px}
.order-card-grid span{display:block;font-size:10px;color:var(--t2);margin-bottom:2px}
.order-card-grid b{display:block;font-size:13px;color:var(--t);font-variant-numeric:tabular-nums}
.order-card-grid small{display:block;font-size:10px;color:var(--t2);line-height:1.4;margin-top:2px}
.order-card p{font-size:11px;color:var(--t);line-height:1.55;margin-top:8px}
.order-card>small{display:block;font-size:10px;color:var(--t2);line-height:1.45;margin-top:4px}
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
  :root{--t:#e6edf3;--t2:#9aa7b3;--bg:#0d1117;--card:#161b22;--card2:#21262d;--surface:#1b222c;--bdr:rgba(255,255,255,0.09);--ok:#56D6A5;--ok-bg:rgba(29,158,117,0.24);--warn:#E3A64C;--warn-bg:rgba(186,117,23,0.24);--risk:#FF8A65;--risk-bg:rgba(216,90,48,0.25);--info:#76B7E8;--info-bg:rgba(24,95,165,0.25);--cold:#8CC7E8;--cold-bg:rgba(47,108,143,0.24)}
  .rl li{color:#b0bec5}
  .tb.on{border-bottom-color:var(--t)}
  .tnav{border-bottom-color:rgba(255,255,255,0.1)}
  .sbw,.bb{background:rgba(255,255,255,0.1)}
}
@media(max-width:600px){
  .wrap{padding:0 12px}
  [id]{scroll-margin-top:92px}
  .desktop-top-grid,.desktop-mid-grid{grid-template-columns:1fr;margin:8px 0;gap:10px}
  .section-head{display:block;padding:12px 13px;margin:12px 0 8px}
  .section-head span{display:inline-block;margin-top:8px}
  .mode-switch{top:48px;margin:0 -12px 6px;padding:8px 12px;background:var(--bg);overflow-x:auto}
  .mode-btn{white-space:nowrap;padding:7px 12px;font-size:12px}
  .tool-head{gap:8px}
  .section-meta{display:none}
  .mobile-jump{position:static;display:flex;gap:6px;overflow-x:auto;padding:8px 0;background:var(--bg);border-bottom:1px solid var(--bdr);margin:0 -12px 8px;padding-left:12px;padding-right:12px}
  .mobile-jump a{white-space:nowrap;text-decoration:none;color:var(--t);background:var(--card);border:1px solid var(--bdr);border-radius:999px;padding:7px 12px;font-size:12px;font-weight:700}
  .visual-grid,.visual-bars{grid-template-columns:1fr}
  .visual-main b{font-size:16px}
  .core-grid,.focus-grid{grid-template-columns:1fr}
  .mini-body{grid-template-columns:1fr 1fr}
  .mini-body div:last-child{grid-column:1/-1}
  .core-alt-row{grid-template-columns:1fr;gap:4px}
  .focus-head{display:block}
  .focus-head span{display:block;margin-top:3px}
  .overview-controls{grid-template-columns:1fr}
  .overview-row{grid-template-columns:1fr auto;gap:6px}
  .overview-name{grid-column:1/-1}
  .overview-status{grid-column:1/-1}
  .overview-link{justify-self:start}
  .market-radar{padding:13px}
  .radar-group{padding:9px}
  .radar-grid{grid-template-columns:1fr 1fr;gap:7px}
  .radar-grid .ic{padding:9px}
  .cgrid{grid-template-columns:1fr}
  .sc{padding:13px}
  h1{font-size:16px}
  .ub{display:none}
  .tb{padding:8px 10px;font-size:12px}
  .sh{gap:8px}
  .sp{font-size:18px}
  .price-caption{max-width:150px}
  .back-to-overview{display:inline-flex;align-items:center;gap:4px}
  .tool-head{flex-direction:column}
  .market-brief{grid-template-columns:1fr}
  .market-brief h2{font-size:20px}
  .status-strip,.market-reason-grid,.quick-take,.zone-grid,.zone-steps{grid-template-columns:1fr}
  .dca-controls{grid-template-columns:1fr}
  .order-filter{grid-template-columns:1fr}
  .order-wrap{display:none}
  .order-cards{display:grid}
  .panic-box div{grid-template-columns:42px 1fr}
  .factor-grid,.detail-grid,.ig{grid-template-columns:1fr}
}
</style>'''

APP_SHELL_CSS = '''<style>
:root{
  --t:#eef5f7;--t2:#91a0aa;--bg:#071019;--card:#0d1822;--card2:#13202a;
  --surface:#101d27;--bdr:rgba(196,220,226,0.14);--ok:#5bd3b2;
  --ok-bg:rgba(45,184,145,0.16);--warn:#f2b94b;--warn-bg:rgba(242,185,75,0.15);
  --risk:#ff7b72;--risk-bg:rgba(255,123,114,0.16);--info:#72b7e8;
  --info-bg:rgba(68,148,201,0.16);--cold:#8bc8df;--cold-bg:rgba(75,155,184,0.16)
}
body{background:var(--bg);color:var(--t);min-height:100vh}
html,body{max-width:100%;overflow-x:hidden}
.hdr{display:none}
.wrap{max-width:none;padding:0}
.app-shell{display:grid;grid-template-columns:208px minmax(0,1fr);min-height:100vh}
.app-sidebar{position:sticky;top:0;height:100vh;border-right:1px solid var(--bdr);background:#0a141d;padding:20px 12px;display:flex;flex-direction:column;z-index:120}
.app-brand{display:flex;align-items:center;gap:10px;padding:2px 8px 22px;font-size:17px;font-weight:900;color:var(--t)}
.brand-bars{display:grid;grid-template-columns:repeat(4,4px);gap:3px;align-items:end;height:22px}
.brand-bars i{display:block;width:4px;background:var(--ok);border-radius:2px}
.brand-bars i:nth-child(1){height:8px}.brand-bars i:nth-child(2){height:13px}.brand-bars i:nth-child(3){height:18px}.brand-bars i:nth-child(4){height:22px}
.app-nav{display:grid;gap:5px}
.app-nav-btn{width:100%;border:0;background:transparent;color:#bcc7ce;border-radius:7px;padding:11px 12px;display:flex;align-items:center;gap:10px;text-align:left;font:inherit;font-size:14px;font-weight:750;cursor:pointer}
.app-nav-btn:hover{background:rgba(255,255,255,0.05);color:var(--t)}
.app-nav-btn.on{background:#172530;color:var(--ok);box-shadow:inset 3px 0 0 var(--ok)}
.nav-mark{display:grid;place-items:center;width:25px;height:25px;color:inherit;font-size:12px;font-weight:900}
.app-sidebar-note{margin-top:auto;padding:14px 10px 0;border-top:1px solid var(--bdr);font-size:10px;color:var(--t2);line-height:1.55}
.app-sidebar-note small{display:block;color:var(--accent);font-weight:900;margin-top:5px}
.app-main{min-width:0;padding:0 24px 42px}
.app-topbar{position:sticky;top:0;z-index:100;background:rgba(7,16,25,0.96);border-bottom:1px solid var(--bdr);min-height:68px;display:flex;align-items:center;justify-content:space-between;gap:18px;padding:10px 4px}
.app-topbar h1{font-size:24px;color:var(--t);min-width:0}
.topbar-meta{display:flex;align-items:center;gap:12px;font-size:11px;color:var(--t2)}
.topbar-phase{display:inline-flex;align-items:center;border-radius:999px;padding:6px 10px;background:rgba(110,231,199,.10);border:1px solid rgba(110,231,199,.22);color:var(--accent);font-weight:900;white-space:nowrap}
.reload-btn{border:1px solid var(--bdr);background:var(--card2);color:var(--t);border-radius:7px;padding:8px 11px;font:inherit;font-size:12px;font-weight:800;cursor:pointer}
.data-status{color:var(--ok);font-weight:800;white-space:nowrap}
.data-status::before{content:"";display:inline-block;width:8px;height:8px;background:var(--ok);border-radius:50%;margin-right:6px}
.app-content{max-width:1420px;margin:0 auto}
.app-page{padding-top:16px}
.page-heading{display:flex;justify-content:space-between;gap:16px;align-items:flex-end;margin-bottom:12px}
.page-heading h2{font-size:21px;line-height:1.25}
.page-heading p{font-size:11px;color:var(--t2);line-height:1.55;margin-top:3px}
.page-heading span{font-size:10px;color:var(--t2);border:1px solid var(--bdr);border-radius:999px;padding:5px 9px}
.sc,.ic,.section-head{background:var(--card);border-color:var(--bdr);box-shadow:none}
.section-head{background:var(--card)}
.decision-hero{padding:22px;margin:0;min-width:0}
.market-brief h2{font-size:31px;max-width:920px}
.market-brief p{font-size:13px;max-width:900px}
.plain-take{background:transparent;border:0;border-left:3px solid var(--ok);border-radius:0;padding:3px 0 3px 12px}
.plain-take b{font-size:14px}
.decision-evidence{border-top:1px solid var(--bdr);margin-top:14px;padding-top:11px}
.decision-evidence>summary{cursor:pointer;list-style:none;color:var(--ok);font-size:12px;font-weight:850}
.decision-evidence>summary::-webkit-details-marker{display:none}
.decision-evidence>summary::after{content:"⌄";float:right;font-size:16px}
.decision-evidence[open]>summary::after{content:"⌃"}
.decision-time-note{font-size:10px;color:var(--t2);line-height:1.55;margin-top:8px}
.status-strip{grid-template-columns:repeat(5,minmax(0,1fr))}
.status-strip div,.market-reason-grid div,.visual-main,.visual-bar,.mini-target,.theme-card,.overview-row,.dca-stat,.strategy-grid div,.panic-box div,.check-grid div,.method-grid div,.order-card{background:var(--card2)}
.home-dashboard-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:14px;margin:14px 0}
.home-panel{margin:0;min-width:0}
.home-panel-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:10px}
.home-panel-head p{font-size:10px;color:var(--t2);line-height:1.5;margin-top:3px}
.home-panel-head button{border:0;background:transparent;color:var(--ok);font:inherit;font-size:11px;font-weight:850;cursor:pointer;white-space:nowrap}
.home-table,.research-list{display:grid}
.home-table-row{display:grid;grid-template-columns:minmax(150px,1.25fr) .45fr .38fr minmax(105px,.75fr) minmax(120px,1fr) auto;gap:9px;align-items:center;padding:10px 0;border-top:1px solid var(--bdr)}
.home-table-row>div:first-child b,.research-row>div b{display:block;font-size:12px;color:var(--t)}
.home-table-row small,.research-row small{display:block;font-size:9px;color:var(--t2);line-height:1.4}
.home-table-row span{display:block;font-size:9px;color:var(--t2)}
.home-table-row>div>b{font-size:12px}
.home-row-status>span{display:inline-flex;color:var(--warn);font-size:10px;font-weight:850}
.home-row-ok .home-row-status>span{color:var(--ok)}.home-row-stop .home-row-status>span{color:var(--risk)}
.home-row-link{color:var(--ok);text-decoration:none;font-size:11px;font-weight:850}
.research-row{display:grid;grid-template-columns:28px minmax(110px,.8fr) minmax(90px,.7fr) minmax(140px,1.3fr) minmax(90px,.65fr) auto;gap:9px;align-items:center;padding:10px 0;border-top:1px solid var(--bdr)}
.research-rank{display:grid;place-items:center;width:22px;height:22px;border-radius:50%;background:var(--ok);color:#062119;font-size:11px;font-weight:900}
.research-row p{font-size:10px;color:var(--t);line-height:1.45}
.market-radar{margin:14px 0}
.core-etfs{background:var(--card);border-color:var(--bdr)}
.temp-hot,.temp-warm,.temp-neutral,.temp-cool,.temp-cold{background:var(--card2)}
.desktop-mid-grid{grid-template-columns:1fr 1fr}
.mode-switch,.mobile-jump{display:none!important}
.mobile-bottom-nav,.mobile-more-sheet,.mobile-topbar{display:none}
footer{margin-left:208px;background:#0a141d;border-color:var(--bdr)}
@media(max-width:980px){
  .app-shell{grid-template-columns:174px minmax(0,1fr)}
  .app-main{padding-left:16px;padding-right:16px}
  footer{margin-left:174px}
  .status-strip{grid-template-columns:repeat(3,minmax(0,1fr))}
  .home-dashboard-grid{grid-template-columns:1fr}
}
@media(max-width:760px){
  body{padding-bottom:66px}
  .app-shell{display:block}
  .app-sidebar{display:none}
  .app-main{padding:0 11px 18px}
  .app-topbar{min-height:58px;padding:8px 2px}
  .app-topbar h1{font-size:18px}
  .topbar-meta{gap:7px;margin-left:auto;flex:0 0 auto}
  .topbar-time{display:none}
  .data-status{display:none}
  .topbar-phase{padding:5px 8px;font-size:10px}
  .reload-btn{width:36px;height:36px;padding:0;font-size:0}
  .reload-btn::after{content:"↻";font-size:22px}
  .data-status{font-size:10px}
  .app-page{padding-top:10px}
  .page-heading{display:none}
  .decision-hero{padding:15px}
  .market-brief h2{font-size:25px;overflow-wrap:anywhere;word-break:break-word}
  .market-brief p{font-size:11px}
  .plain-take b{font-size:12px}
  .status-strip{grid-template-columns:repeat(2,minmax(0,1fr))}
  .status-strip div:last-child{grid-column:1/-1}
  .market-reason-grid{grid-template-columns:1fr}
  .home-dashboard-grid{gap:10px;margin:10px 0}
  .home-table-row{grid-template-columns:1fr auto auto}
  .home-table-row>div:first-child{grid-column:1/3}
  .home-row-status{grid-column:1/3}
  .research-row{grid-template-columns:26px 1fr auto}
  .research-row p,.research-row>small{grid-column:2/4}
  .desktop-mid-grid{grid-template-columns:1fr}
  .mobile-bottom-nav{position:fixed;display:grid;grid-template-columns:repeat(5,1fr);left:0;right:0;bottom:0;z-index:300;background:rgba(10,20,29,0.98);border-top:1px solid var(--bdr);padding:6px max(8px,env(safe-area-inset-right)) calc(6px + env(safe-area-inset-bottom)) max(8px,env(safe-area-inset-left))}
  .mobile-nav-btn{border:0;background:transparent;color:var(--t2);font:inherit;font-size:10px;font-weight:800;padding:4px 2px;display:grid;place-items:center;gap:1px;cursor:pointer}
  .mobile-nav-btn b{font-size:15px;color:inherit}
  .mobile-nav-btn.on{color:var(--ok)}
  .mobile-more-sheet{position:fixed;display:none;inset:0;z-index:400;background:rgba(0,0,0,0.55);align-items:flex-end}
  .mobile-more-sheet.open{display:flex}
  .mobile-more-panel{width:100%;background:#111e28;border:1px solid var(--bdr);border-radius:12px 12px 0 0;padding:12px 12px calc(16px + env(safe-area-inset-bottom))}
  .mobile-more-panel header{display:flex;justify-content:space-between;align-items:center;padding:3px 3px 10px}
  .mobile-more-panel header b{font-size:15px}
  .mobile-more-panel header button{border:0;background:transparent;color:var(--t2);font-size:22px;cursor:pointer}
  .mobile-more-panel nav{display:grid;gap:7px}
  .mobile-more-panel nav button{border:1px solid var(--bdr);background:var(--card2);color:var(--t);border-radius:8px;padding:12px;text-align:left;font:inherit;font-size:13px;font-weight:800}
  footer{margin-left:0;padding-bottom:76px}
}

/* 2026-06 workspace redesign: decision-first, readable, and quieter. */
:root{
  --t:#f1f6f9;--t2:#9aaabb;--bg:#0b1016;--card:#151c25;--card2:#111822;
  --surface:#1b2430;--bdr:rgba(136,153,170,.18);--ok:#35c99a;
  --warn:#e0a23a;--risk:#ec6a5c;--info:#77aaff
}
body{font-family:"Noto Sans TC","Microsoft JhengHei",system-ui,sans-serif;line-height:1.55}
.app-shell{grid-template-columns:190px minmax(0,1fr)}
.app-sidebar{background:#0e151d;padding:24px 12px}
.app-brand{display:block;padding:0 16px 28px;font-size:18px;letter-spacing:.01em}
.app-nav{gap:4px}
.app-nav-btn{padding:13px 16px;border-radius:6px;font-size:15px;font-weight:800;color:#c9d4dc}
.app-nav-btn.on{background:#1b2530;color:var(--ok);box-shadow:inset 3px 0 0 var(--ok)}
.app-sidebar-note{padding:16px 16px 0;font-size:11px;line-height:1.7}
.app-sidebar-note b{display:block;color:#cbd6df;font-size:12px;margin-top:3px}
.app-main{padding:0 28px 48px}
.app-content{max-width:1240px}
.app-topbar{min-height:72px;background:rgba(11,16,22,.97);padding:12px 2px}
.app-topbar h1{font-size:25px}
.topbar-meta{font-size:12px}
.reload-btn{background:#111822;padding:9px 13px;font-size:12px}
.sc{border-radius:8px;padding:18px}
.st{font-size:20px;line-height:1.3}
.decision-hero{padding:22px 24px;border-left:3px solid var(--ok)}
.hero-heading{display:flex;justify-content:space-between;align-items:center;gap:14px}
.hero-temperature{display:inline-flex;border:1px solid rgba(224,162,58,.35);background:rgba(224,162,58,.10);color:var(--warn);border-radius:999px;padding:5px 10px;font-size:12px;font-weight:900}
.hero-temperature-hot{border-color:rgba(255,138,101,.46);background:rgba(216,90,48,.18);color:var(--risk)}
.hero-temperature-warm{border-color:rgba(227,166,76,.48);background:rgba(186,117,23,.18);color:var(--warn)}
.hero-temperature-neutral{border-color:rgba(118,183,232,.40);background:rgba(24,95,165,.16);color:var(--info)}
.hero-temperature-cool,.hero-temperature-cold{border-color:rgba(140,199,232,.42);background:rgba(47,108,143,.18);color:var(--cold)}
.market-brief h2{font-size:34px;line-height:1.18;margin-top:16px;max-width:none}
.market-brief p{font-size:15px;line-height:1.7;color:#cbd6df;margin-top:8px}
.status-strip{grid-template-columns:repeat(4,minmax(0,1fr));gap:0;margin-top:20px;border-top:1px solid var(--bdr);padding-top:14px}
.status-strip div,.status-strip .status-ok,.status-strip .status-warn,.status-strip .status-risk,.status-strip .status-info{background:transparent;border:0;border-left:1px solid var(--bdr);border-radius:0;padding:3px 18px}
.status-strip div:first-child{border-left:0;padding-left:4px}
.status-strip span{font-size:12px}
.status-strip b{font-size:19px;margin-top:4px}
.status-strip small{font-size:11px;line-height:1.45}
.status-strip .status-ok b{color:var(--ok)}
.status-strip .status-warn b{color:var(--warn)}
.decision-evidence{margin-top:15px;padding-top:12px}
.decision-evidence>summary{font-size:13px}
.plain-take{margin-top:12px}
.home-market-radar{padding:14px 16px;margin:14px 0}
.market-radar-title{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;margin-bottom:10px}
.market-radar-title p,.home-panel-head p{font-size:12px;color:var(--t2);line-height:1.55;margin-top:3px}
.market-radar-title button,.home-panel-head button{border:0;background:transparent;color:var(--ok);font:inherit;font-size:12px;font-weight:900;cursor:pointer;white-space:nowrap}
.home-market-radar .radar-layout{display:flex;gap:0;overflow-x:auto;border:1px solid var(--bdr);border-radius:7px;scrollbar-width:thin}
.home-market-radar .radar-layout{scrollbar-width:none}
.home-market-radar .radar-layout::-webkit-scrollbar{display:none}
.home-market-radar .radar-group{display:contents}
.home-market-radar .radar-head{display:none}
.home-market-radar .radar-grid{display:contents}
.home-market-radar .radar-more{display:none}
.home-market-radar .ic{flex:1 0 135px;min-width:135px;background:#111822;border:0;border-right:1px solid var(--bdr);border-radius:0;padding:11px 12px}
.home-market-radar .ic:last-child{border-right:0}
.home-market-radar .ic-l{font-size:12px;color:#cbd6df}
.home-market-radar .ic-v{font-size:16px}
.home-market-radar .ic-c{font-size:12px}
.home-market-radar .ic-note{font-size:11px;min-height:0}
.home-market-radar .ic-note-full{display:none}
.home-market-radar .ic-note-short{display:inline}
.home-preview-tabs{display:none}
.home-dashboard-grid{gap:14px;margin:14px 0}
.home-panel{padding:16px 18px}
.home-panel-head{margin-bottom:10px}
.home-table-head,.research-list-head{display:grid;gap:10px;align-items:center;padding:8px 10px;background:#1b2430;color:var(--t2);font-size:11px}
.home-table-head{grid-template-columns:minmax(150px,1.25fr) .45fr .38fr minmax(105px,.75fr) minmax(120px,1fr) auto}
.research-list-head{grid-template-columns:28px minmax(110px,.8fr) minmax(90px,.7fr) minmax(140px,1.3fr) minmax(90px,.65fr) auto}
.home-table-row{padding:12px 10px;gap:10px}
.home-table-row>div:first-child b,.research-row>div b{font-size:13px}
.home-table-row small,.research-row small{font-size:11px}
.home-table-row span{font-size:11px}
.home-table-row>div>b{font-size:13px}
.home-row-status>span{font-size:12px}
.home-row-action span{display:none}
.home-row-action b{font-size:12px;color:#d7e1e8;line-height:1.45}
.home-row-link{font-size:12px}
.research-row{padding:12px 10px;gap:10px}
.research-rank{width:24px;height:24px;border-radius:5px;background:#243241;color:#d9e4eb;font-size:12px}
.research-row p{font-size:12px;line-height:1.55}
.research-topic{font-size:12px;color:#cbd6df}
.visual-board{margin-top:14px}
.visual-grid{grid-template-columns:repeat(3,minmax(0,1fr));gap:0;border:1px solid var(--bdr);border-radius:7px;overflow:hidden}
.visual-main{border:0;border-right:1px solid var(--bdr);border-radius:0;padding:14px}
.visual-main:last-child{border-right:0}
.visual-main span{font-size:11px}.visual-main b{font-size:17px}.visual-main small{font-size:11px}
.visual-bars{gap:8px}
.visual-bar{padding:12px}.visual-bar b{font-size:13px}.visual-bar small{font-size:11px}
footer{margin-left:190px}

@media(max-width:980px) and (min-width:761px){
  .app-shell{grid-template-columns:170px minmax(0,1fr)}
  footer{margin-left:170px}
  .home-dashboard-grid{grid-template-columns:1fr}
}
@media(max-width:760px){
  .app-main{padding:0 10px 18px}
  .app-topbar{min-height:60px}
  .app-topbar h1{font-size:20px}
  .reload-btn{width:38px;height:38px;padding:0;font-size:0}
  .reload-btn::after{content:"↻";font-size:22px}
  .decision-hero{padding:17px 15px}
  .hero-heading{align-items:flex-start}
  .hero-heading .st{font-size:15px}
  .hero-temperature{font-size:11px;padding:4px 8px}
  .market-brief h2{font-size:27px;line-height:1.28;margin-top:13px}
  .market-brief p{font-size:13px;line-height:1.65}
  .status-strip{grid-template-columns:repeat(2,minmax(0,1fr));gap:0;padding-top:8px}
  .status-strip div,.status-strip .status-ok,.status-strip .status-warn,.status-strip .status-risk,.status-strip .status-info{border-left:0;border-top:1px solid var(--bdr);padding:11px 8px}
  .status-strip div:nth-child(odd){border-right:1px solid var(--bdr)}
  .status-strip div:first-child,.status-strip div:nth-child(2){border-top:0}
  .status-strip div:last-child{grid-column:auto}
  .status-strip span{font-size:11px}.status-strip b{font-size:17px}.status-strip small{font-size:10px}
  .home-market-radar{padding:14px 12px}
  .market-radar-title p{font-size:11px}
  .home-market-radar .radar-layout{margin:0 -1px}
  .home-market-radar .ic{flex-basis:128px;min-width:128px;padding:10px}
  .home-market-radar .ic-l{font-size:11px}.home-market-radar .ic-v{font-size:15px}.home-market-radar .ic-note{font-size:10px}
  .home-preview-tabs{display:grid;grid-template-columns:1fr 1fr;border:1px solid var(--bdr);border-radius:7px;overflow:hidden;margin:12px 0 8px}
  .home-preview-tabs button{border:0;background:#111822;color:var(--t2);padding:10px;font:inherit;font-size:13px;font-weight:850}
  .home-preview-tabs button.on{background:#1b453a;color:#8be5c7;box-shadow:inset 0 -2px 0 var(--ok)}
  .home-dashboard-grid{display:block;margin:0}
  .home-panel{display:none;padding:14px 13px}
  .home-panel.mobile-panel-on{display:block}
  .home-panel-head p{font-size:11px}
  .home-table-head,.research-list-head{display:none}
  .home-table-row{grid-template-columns:minmax(0,1fr) auto;padding:13px 2px}
  .home-table-row>div:first-child{grid-column:auto}
  .home-table-row>div:nth-child(2){text-align:right}
  .home-table-row>div:nth-child(3){display:none}
  .home-row-status{grid-column:1/2}
  .home-row-status small{display:none}
  .home-row-link{grid-column:2;grid-row:2;text-align:right}
  .home-row-action{grid-column:1/3}
  .home-row-action span{display:block;font-size:10px;color:var(--t2)}
  .home-row-action b{font-size:12px}
  .research-row{grid-template-columns:28px minmax(0,1fr) auto;padding:13px 2px}
  .research-topic{grid-column:2/4;color:var(--info);font-size:11px}
  .research-row p{grid-column:2/4;font-size:12px}
  .research-row>small{grid-column:2/4}
  .visual-grid{grid-template-columns:1fr}
  .visual-main{border-right:0;border-bottom:1px solid var(--bdr)}
  .visual-main:last-child{border-bottom:0}
  .mobile-bottom-nav{background:#0e151d;padding-top:9px;padding-bottom:calc(9px + env(safe-area-inset-bottom))}
  .mobile-nav-btn{font-size:12px;padding:5px 2px}
}

/* Complete target workspace: browse compactly, inspect one target with a clear workflow. */
#core-mode .core-etfs,
#research-mode .today-focus,
#research-mode .target-overview,
#research-mode .theme-radar,
#market-mode .market-radar,
#tools-mode .dca-tool,
#tools-mode .buy-tool,
#data-mode .methodology,
#data-mode .public-check{border-radius:8px}
.page-heading{padding:4px 2px 13px;border-bottom:1px solid var(--bdr);align-items:center}
.page-heading h2{font-size:24px}
.page-heading p{font-size:12px;max-width:780px}
.page-heading span{background:var(--card2);font-size:11px}
#market-mode .market-radar{padding:18px;margin-top:0}
#market-mode .radar-layout{grid-template-columns:repeat(2,minmax(0,1fr))}
#market-mode .radar-group{background:#111822;border-radius:7px;padding:14px}
#tools-mode .desktop-mid-grid{gap:14px}
#tools-mode .dca-tool,#tools-mode .buy-tool{padding:18px}

#data-mode .target-section{margin-top:0}
#data-mode .cgrid{grid-template-columns:1fr;gap:8px;padding-bottom:28px}
.target-card{padding:0;background:transparent;border:0;border-radius:0;box-shadow:none}
.target-card-summary{display:grid;grid-template-columns:minmax(260px,1.55fr) minmax(100px,.45fr) minmax(90px,.38fr) minmax(180px,.95fr) auto;gap:16px;align-items:center;background:var(--card);border:1px solid var(--bdr);border-left:3px solid var(--warn);border-radius:8px;padding:14px 15px}
.target-summary-ok .target-card-summary{border-left-color:var(--ok)}
.target-summary-stop .target-card-summary{border-left-color:var(--risk)}
.target-card-summary>div{min-width:0}
.target-card-summary span{display:block;font-size:10px;color:var(--t2);margin-bottom:3px}
.target-card-summary b{display:block;color:var(--t);font-size:14px;line-height:1.35;font-variant-numeric:tabular-nums}
.target-card-summary small{display:block;font-size:10px;color:var(--t2);line-height:1.4;margin-top:2px}
.target-summary-name>b{font-size:15px}
.target-summary-name>span{color:var(--info);font-weight:800}
.target-summary-status>b{color:var(--warn)}
.target-summary-ok .target-summary-status>b{color:var(--ok)}
.target-summary-stop .target-summary-status>b{color:var(--risk)}
.target-open-btn,.target-back-btn{border:1px solid rgba(53,201,154,.34);background:rgba(53,201,154,.10);color:#77dfbf;border-radius:7px;padding:9px 12px;font:inherit;font-size:12px;font-weight:900;cursor:pointer;white-space:nowrap}
.target-open-btn:hover,.target-back-btn:hover{background:rgba(53,201,154,.18)}
.target-detail-view{display:none}
body.target-detail-open #data-mode>.page-heading,
body.target-detail-open #target-list>.section-head,
body.target-detail-open #target-list>.tnav,
body.target-detail-open #data-mode>.methodology,
body.target-detail-open #data-mode>.public-check{display:none}
body.target-detail-open #data-mode .target-card{display:none}
body.target-detail-open #data-mode .target-card.target-detail-active{display:block}
body.target-detail-open #data-mode .target-card.target-detail-active .target-card-summary{display:none}
body.target-detail-open #data-mode .target-card.target-detail-active .target-detail-view{display:block}
body.target-detail-open #data-mode .cgrid{padding-bottom:10px}

.target-detail-view{background:transparent}
.target-detail-header{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:16px;align-items:center;background:var(--card);border:1px solid var(--bdr);border-radius:8px 8px 0 0;padding:18px 20px}
.target-back-btn{align-self:start;margin-top:2px}
.target-kicker{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:6px}
.target-kicker span{font-size:10px;color:var(--t2);background:var(--card2);border:1px solid var(--bdr);border-radius:999px;padding:3px 7px}
.target-detail-heading h2{font-size:25px;line-height:1.25;color:var(--t)}
.target-detail-heading h2 small{font-size:16px;color:#cbd6df;font-weight:750}
.target-detail-heading p{font-size:11px;color:var(--t2);margin-top:4px}
.target-live-price{text-align:right;min-width:120px}
.target-live-price span{display:block;font-size:10px;color:var(--t2)}
.target-live-price b{display:block;font-size:25px;color:var(--t);font-variant-numeric:tabular-nums}
.target-live-price small{display:block;font-size:12px;font-weight:850}
.target-snapshot{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));background:#101720;border:1px solid var(--bdr);border-top:0;border-radius:0 0 8px 8px;margin-bottom:14px}
.target-snapshot>div{padding:13px 15px;border-left:1px solid var(--bdr);min-width:0}
.target-snapshot>div:first-child{border-left:0}
.target-snapshot span{display:block;font-size:10px;color:var(--t2)}
.target-snapshot b{display:block;font-size:15px;color:var(--t);margin-top:3px;line-height:1.35}
.target-snapshot small{display:block;font-size:10px;color:var(--t2);line-height:1.45;margin-top:3px}
.target-detail-layout{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:14px;align-items:start}
.target-detail-main,.target-decision-rail{min-width:0}
.target-detail-main{background:var(--card);border:1px solid var(--bdr);border-radius:8px;overflow:hidden}
.target-detail-tabs{display:grid;grid-template-columns:repeat(4,1fr);background:#101720;border-bottom:1px solid var(--bdr);padding:0 14px}
.target-detail-tabs button{position:relative;border:0;background:transparent;color:var(--t2);padding:14px 8px;font:inherit;font-size:13px;font-weight:850;cursor:pointer}
.target-detail-tabs button.on{color:var(--ok)}
.target-detail-tabs button.on::after{content:"";position:absolute;left:18%;right:18%;bottom:0;height:2px;background:var(--ok)}
.target-detail-panel{display:none;padding:18px}
.target-detail-panel.on{display:block}
.detail-section-head{display:flex;justify-content:space-between;align-items:flex-end;gap:20px;margin-bottom:13px;padding-bottom:12px;border-bottom:1px solid var(--bdr)}
.detail-section-head span{display:block;font-size:10px;color:var(--ok);font-weight:900;margin-bottom:3px}
.detail-section-head h3{font-size:20px;color:var(--t);line-height:1.3}
.detail-section-head p{font-size:11px;color:var(--t2);max-width:360px;text-align:right;line-height:1.5}
.target-detail-panel .zone-box,.target-detail-panel .detail-box,.target-detail-panel .factor-box{background:#111822;border-radius:7px;margin:0}
.target-detail-panel .detail-grid{grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
.target-detail-panel .metric-tile,.target-detail-panel .ic2,.target-detail-panel .zone-grid div,.target-detail-panel .zone-steps div{background:#17212c;border-color:var(--bdr)}
.target-detail-panel .metric-tile{padding:10px}
.target-detail-panel .metric-tile span{font-size:11px}
.target-detail-panel .metric-tile b{font-size:15px;margin-top:3px}
.target-detail-panel .metric-tile small{font-size:10px}
.target-detail-panel .zone-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
.target-detail-panel .zone-grid div{padding:10px}
.target-detail-panel .zone-grid b{font-size:14px}
.target-detail-panel .zone-steps{gap:8px}
.target-detail-panel .zone-steps div{padding:10px}
.target-detail-panel .zone-more,.target-detail-panel .card-more{margin-top:12px;padding-top:10px}
.target-detail-panel .zone-more summary,.target-detail-panel .card-more summary{font-size:12px;color:var(--ok)}
.target-detail-panel .factor-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.target-detail-panel .factor{grid-template-columns:58px 32px 1fr}
.target-detail-panel .ig{grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
.target-detail-panel .technical-metrics{grid-template-columns:repeat(4,minmax(0,1fr))}
.trend-chart-card{background:#111822;border:1px solid var(--bdr);border-radius:7px;padding:14px;margin-bottom:10px}
.trend-chart-card .spark{margin-bottom:12px}
.target-detail-panel .pref-item{background:#17212c;border:1px solid var(--bdr);padding:7px 9px}
.target-decision-rail{position:sticky;top:86px;display:grid;gap:10px}
.target-decision-rail .quick-take{margin:0;grid-template-columns:1fr}
.target-decision-rail .quick-take div{background:var(--card);padding:13px;border-radius:8px}
.target-decision-rail .quick-take b{font-size:16px}
.target-decision-rail .decision-card{background:var(--card);border-radius:8px;margin:0;padding:14px}
.target-decision-rail .decision-head span{color:var(--ok)}
.target-decision-rail .decision-row{grid-template-columns:48px 1fr;gap:9px;margin-top:9px}
.target-decision-rail .decision-row p{font-size:12px}
.target-score-card,.target-source-note{background:var(--card);border:1px solid var(--bdr);border-radius:8px;padding:13px}
.target-score-card>div:first-child{display:flex;justify-content:space-between;align-items:center}
.target-score-card span{font-size:11px;color:var(--t2)}
.target-score-card b{font-size:20px}
.target-score-card .sr{margin:8px 0}
.target-score-card .sbadge{margin:0}
.target-source-note b{font-size:11px;color:var(--t2)}
.target-source-note p{font-size:10px;color:var(--t2);line-height:1.55;margin-top:4px}
.target-disclaimer{font-size:10px;color:var(--t2);line-height:1.5;padding:12px 2px 0}

@media(max-width:980px){
  .target-card-summary{grid-template-columns:minmax(220px,1.4fr) .45fr .4fr minmax(150px,.8fr) auto;gap:10px}
  .target-detail-layout{grid-template-columns:minmax(0,1fr) 300px}
  .target-detail-panel .detail-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
  .target-snapshot{grid-template-columns:repeat(2,minmax(0,1fr))}
  .target-snapshot>div:nth-child(odd){border-left:0}
  .target-snapshot>div:nth-child(n+3){border-top:1px solid var(--bdr)}
}
@media(max-width:760px){
  .page-heading{display:none}
  #market-mode .radar-layout{grid-template-columns:1fr}
  #market-mode .market-radar{padding:13px}
  .target-card-summary{grid-template-columns:minmax(0,1fr) auto;padding:13px;gap:8px 12px}
  .target-summary-name{grid-column:1/-1}
  .target-summary-price,.target-summary-score{display:inline-grid}
  .target-summary-status{grid-column:1/-1;padding-top:8px;border-top:1px solid var(--bdr)}
  .target-open-btn{grid-column:1/-1;width:100%}
  .target-detail-header{grid-template-columns:minmax(0,1fr) auto;padding:14px 13px;gap:10px}
  .target-back-btn{grid-column:1/-1;justify-self:start;padding:7px 10px}
  .target-detail-heading h2{font-size:22px}
  .target-detail-heading h2 small{display:block;font-size:14px;margin-top:2px}
  .target-live-price{min-width:92px}
  .target-live-price b{font-size:21px}
  .target-snapshot{grid-template-columns:repeat(2,minmax(0,1fr));margin-bottom:10px}
  .target-snapshot>div{padding:11px 10px}
  .target-snapshot b{font-size:13px}
  .target-snapshot small{font-size:9px}
  .target-detail-layout{display:flex;flex-direction:column;gap:10px}
  .target-decision-rail{position:static;order:-1;width:100%;gap:8px}
  .target-decision-rail .quick-take{grid-template-columns:1fr 1fr}
  .target-decision-rail .decision-card{padding:12px}
  .target-decision-rail .decision-row{grid-template-columns:42px 1fr}
  .decision-hero-row{grid-template-columns:1fr;padding:11px}
  .decision-hero-row strong{font-size:18px}
  .decision-action-grid,.decision-evidence-grid{grid-template-columns:1fr}
  .decision-meter-grid{grid-template-columns:repeat(3,minmax(0,1fr));gap:6px}
  .decision-meter{padding:8px 7px}
  .decision-meter small{white-space:normal}
  .chip-flow-grid{grid-template-columns:1fr}
  .flow-bar-row{grid-template-columns:58px minmax(60px,1fr);gap:6px}
  .flow-bar-row b{grid-column:2;text-align:left}
  .target-score-card,.target-source-note{display:none}
  .target-detail-main{width:100%}
  .target-detail-tabs{position:sticky;top:60px;z-index:80;padding:0 5px}
  .target-detail-tabs button{padding:12px 4px;font-size:12px}
  .target-detail-panel{padding:13px}
  .detail-section-head{display:block;margin-bottom:10px;padding-bottom:9px}
  .detail-section-head h3{font-size:18px}
  .detail-section-head p{text-align:left;margin-top:5px;font-size:10px}
  .target-detail-panel .detail-grid,
  .target-detail-panel .ig,
  .target-detail-panel .technical-metrics,
  .target-detail-panel .factor-grid{grid-template-columns:1fr}
  .target-detail-panel .zone-grid{grid-template-columns:1fr}
  .target-detail-panel .zone-steps{grid-template-columns:1fr}
  .target-detail-panel .metric-tile{padding:9px}
  .target-detail-panel .metric-tile b{font-size:14px}
  .target-disclaimer{padding:10px 2px 2px}
}
</style>'''

UI_OVERRIDE_CSS = '''<style>
.decision-card-v3{background:linear-gradient(180deg,rgba(13,25,38,.96),rgba(14,22,32,.96));border-color:rgba(110,231,199,.24)}
.decision-card-v3 .decision-head span{font-size:13px;letter-spacing:.02em;color:#dce7f2}
.decision-stage-line{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:8px 0 12px}
.decision-stage-line span{display:inline-flex;align-items:center;border:1px solid rgba(110,231,199,.26);background:rgba(110,231,199,.10);color:#7ee0c3;border-radius:999px;padding:6px 10px;font-size:12px;font-weight:900}
.decision-stage-line b{font-size:12px;color:#f2bd58;background:rgba(242,189,88,.12);border:1px solid rgba(242,189,88,.24);border-radius:999px;padding:6px 10px}
.decision-card-v3 .decision-hero-row strong{font-size:22px;line-height:1.28;letter-spacing:.01em}
.decision-mini-card p{font-size:12px;line-height:1.65}
.zone-v2{background:linear-gradient(180deg,rgba(16,29,42,.98),rgba(13,22,33,.98));border-color:rgba(112,128,144,.30)}
.zone-v2 .zone-head b{display:inline-flex;align-items:center;border-radius:999px;padding:5px 10px;background:rgba(110,231,199,.10);border:1px solid rgba(110,231,199,.22);color:#7ee0c3}
.zone-hot .zone-head b,.zone-danger .zone-head b{background:rgba(255,111,96,.12);border-color:rgba(255,111,96,.25);color:#ff8a78}
.zone-warm .zone-head b{background:rgba(242,189,88,.12);border-color:rgba(242,189,88,.25);color:#f2bd58}
.zone-v2 .zone-track{height:10px;background:rgba(139,158,177,.20);overflow:visible}
.zone-v2 .zone-track .zone-range{position:absolute;top:0;bottom:0;border-radius:999px;background:linear-gradient(90deg,rgba(110,231,199,.28),rgba(110,231,199,.62))}
.zone-v2 .zone-track .zone-marker{position:absolute;top:50%;width:14px;height:14px;border-radius:50%;background:#f4f7fb;border:3px solid #112033;transform:translate(-50%,-50%);box-shadow:0 2px 8px rgba(0,0,0,.35)}
.zone-v2 .zone-labels{margin-top:8px;color:#91a9bd;font-size:10px}
.trend-chart-card .spark svg{height:140px;width:100%;max-width:100%}
.desktop-mid-grid:has(> .tool-card:only-child){grid-template-columns:minmax(0,1fr)}
.price-position-card{padding:14px;border-radius:10px}
.price-position-head{align-items:center;margin-bottom:10px}
.price-position-lead{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:start;background:rgba(255,255,255,.035);border:1px solid rgba(255,255,255,.08);border-radius:9px;padding:12px;margin:10px 0 14px}
.price-position-lead span{display:block;color:#8aa2b7;font-size:11px;font-weight:850;margin-bottom:4px}
.price-position-lead strong{display:block;color:#f4f7fb;font-size:15px;line-height:1.45}
.price-position-lead p{margin:5px 0 0;color:#a9bacb;font-size:12px;line-height:1.65}
.price-position-lead em{font-style:normal;white-space:nowrap;color:#7ee0c3;background:rgba(110,231,199,.11);border:1px solid rgba(110,231,199,.24);border-radius:999px;padding:7px 10px;font-size:12px;font-weight:900}
.price-map{margin:12px 0 13px}
.price-map-rail{position:relative;height:34px;display:flex;align-items:center}
.price-map-segments{display:grid;grid-template-columns:24fr 34fr 26fr 16fr;width:100%;height:11px;border-radius:999px;overflow:hidden;background:rgba(139,158,177,.18);box-shadow:inset 0 0 0 1px rgba(255,255,255,.06)}
.price-map-segments span{display:block}
.seg-reset{background:linear-gradient(90deg,rgba(255,111,96,.35),rgba(242,189,88,.24))}
.seg-entry{background:linear-gradient(90deg,rgba(110,231,199,.30),rgba(110,231,199,.72))}
.seg-strong{background:linear-gradient(90deg,rgba(88,166,255,.34),rgba(88,166,255,.60))}
.seg-risk{background:linear-gradient(90deg,rgba(242,189,88,.40),rgba(255,111,96,.56))}
.price-map-marker{position:absolute;top:50%;width:16px;height:16px;border-radius:50%;background:#f8fbff;border:3px solid #101b27;transform:translate(-50%,-50%);box-shadow:0 2px 10px rgba(0,0,0,.45);z-index:2}
.price-map-marker span{position:absolute;left:50%;top:-24px;transform:translateX(-50%);font-size:10px;line-height:1;color:#e8f1f8;background:#142231;border:1px solid rgba(255,255,255,.10);border-radius:999px;padding:4px 7px;white-space:nowrap;font-style:normal}
.price-map-labels{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-top:4px}
.price-map-labels div,.price-decision-grid div{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:8px;padding:9px 10px;min-width:0}
.price-map-labels span,.price-decision-grid span{display:block;color:#8aa2b7;font-size:10px;font-weight:850;margin-bottom:4px}
.price-map-labels b,.price-decision-grid b{display:block;color:#edf5fb;font-size:13px;line-height:1.35;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.price-map-labels small,.price-decision-grid small{display:block;color:#93a8b9;font-size:10px;line-height:1.45;margin-top:3px}
.price-decision-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-top:10px}
.price-position-more{margin-top:12px}
.stage-summary{border:1px solid rgba(255,255,255,.08);background:rgba(255,255,255,.035);border-radius:8px;padding:11px 12px;margin:10px 0}
.stage-summary strong{display:block;color:#f4f7fb;font-size:14px;margin-bottom:4px}
.stage-summary p{margin:0;color:#a9bacb;font-size:12px;line-height:1.65}
.zone-grid-v2>div{background:rgba(255,255,255,.04);border-color:rgba(255,255,255,.08)}
.zone-grid-v2 span{color:#88a1b8}
.zone-grid-v2 b{font-size:14px}
.zone-more li{margin:5px 0;color:#c4d2df;line-height:1.55}
.chip-flow-head strong{min-width:96px;height:auto;padding:8px 10px;border-radius:999px;font-size:12px;line-height:1.2;white-space:nowrap}
.chip-flow-box p{line-height:1.65;color:#b9cad9}
.chip-focus-note{margin:8px 0 10px;padding:9px 10px;border-radius:8px;background:rgba(110,231,199,.07);border:1px solid rgba(110,231,199,.16);color:#bdd2df;font-size:12px;line-height:1.65}
.flow-in .flow-track i{background:linear-gradient(90deg,#3fd69f,#7ee0c3)}
.flow-out .flow-track i{background:linear-gradient(90deg,#ff8a78,#d85a30)}
.flow-flat .flow-track i{background:#8c98a6}
.order-action.order-momentum{color:#7ee0c3;background:rgba(110,231,199,.12);border-color:rgba(110,231,199,.28)}
.order-action.order-stop{color:#ff8a78;background:rgba(255,111,96,.12);border-color:rgba(255,111,96,.28)}
.order-action.order-wait{color:#f2bd58;background:rgba(242,189,88,.12);border-color:rgba(242,189,88,.28)}
.order-action.order-ok{color:#7ee0c3;background:rgba(110,231,199,.12);border-color:rgba(110,231,199,.28)}
@media(max-width:760px){
  .decision-card-v3 .decision-hero-row strong{font-size:19px}
  .decision-stage-line{align-items:flex-start;flex-direction:column}
  .decision-stage-line b{white-space:normal}
  .chip-flow-head{align-items:flex-start}
  .chip-flow-head strong{min-width:auto}
  .trend-chart-card{padding:11px}
  .trend-chart-card .spark svg{height:118px}
  .zone-v2 .zone-labels{grid-template-columns:repeat(2,1fr);gap:4px 8px;text-align:left;font-size:10px}
  .zone-v2 .zone-grid-v2>div{padding:10px}
  .price-position-card{padding:12px}
  .price-position-lead{grid-template-columns:1fr;gap:8px;padding:11px}
  .price-position-lead em{justify-self:start;white-space:normal}
  .price-map-rail{height:36px;margin-top:8px}
  .price-map-marker span{top:-22px;font-size:9px}
  .price-map-labels{grid-template-columns:1fr 1fr;gap:7px}
  .price-map-labels div{padding:8px}
  .price-map-labels b{font-size:12px}
  .price-decision-grid{grid-template-columns:1fr;gap:7px}
  .price-decision-grid div{padding:9px}
}
</style>'''

JS_CODE = '''<script>
window.LAST_FOCUS_SCROLL = 0;
window.RETURN_APP_PAGE = 'research-mode';
window.ACTIVE_TARGET_ID = '';
var APP_PAGE_TITLES = {
  'focus-mode':'今日市場',
  'core-mode':'核心 ETF',
  'research-mode':'研究標的',
  'market-mode':'市場雷達',
  'tools-mode':'試算工具',
  'data-mode':'完整資料'
};
function rememberFocusSpot(){
  var active=document.querySelector('.mode-pane.on');
  if(active){
    window.RETURN_APP_PAGE=active.id||'research-mode';
    window.LAST_FOCUS_SCROLL=window.scrollY||0;
  }
}
function restoreFocusSpot(){
  var y=Number(window.LAST_FOCUS_SCROLL||0);
  window.scrollTo({top:y,behavior:'smooth'});
}
function returnToFocusSpot(){
  clearTargetDetail();
  showMode(window.RETURN_APP_PAGE||'research-mode',true);
}
function clearTargetDetail(){
  document.body.classList.remove('target-detail-open');
  document.querySelectorAll('.target-card.target-detail-active').forEach(function(card){
    card.classList.remove('target-detail-active');
  });
  window.ACTIVE_TARGET_ID='';
}
function showTargetSection(cardId,name){
  var card=document.getElementById(cardId);
  if(!card) return;
  card.querySelectorAll('[data-target-section]').forEach(function(btn){
    btn.classList.toggle('on',btn.dataset.targetSection===name);
  });
  card.querySelectorAll('[data-target-panel]').forEach(function(panel){
    panel.classList.toggle('on',panel.dataset.targetPanel===name);
  });
  if(window.innerWidth<=760){
    var tabs=card.querySelector('.target-detail-tabs');
    if(tabs) tabs.scrollIntoView({block:'start',behavior:'smooth'});
  }
}
function scrollTargetDetailTop(){
  var root=document.documentElement;
  var previousBehavior=root.style.scrollBehavior;
  root.style.scrollBehavior='auto';
  window.scrollTo(0,0);
  root.style.scrollBehavior=previousBehavior;
}
function openTargetDetail(cardId,tabId,remember){
  if(remember!==false) rememberFocusSpot();
  clearTargetDetail();
  showMode('data-mode',false,true);
  showTab(tabId,true);
  var card=document.getElementById(cardId);
  if(!card) return;
  card.classList.add('target-detail-active');
  document.body.classList.add('target-detail-open');
  window.ACTIVE_TARGET_ID=cardId;
  showTargetSection(cardId,'decision');
  var title=document.getElementById('appPageTitle');
  if(title) title.textContent=card.dataset.label||'標的分析';
  if(history.replaceState) history.replaceState(null,'','#'+cardId);
  scrollTargetDetailTop();
  if(remember===false) setTimeout(scrollTargetDetailTop,120);
}
function closeMobileMore(){
  var sheet=document.getElementById('mobileMoreSheet');
  if(sheet) sheet.classList.remove('open');
}
function toggleMobileMore(){
  var sheet=document.getElementById('mobileMoreSheet');
  if(sheet) sheet.classList.toggle('open');
}
function showHomePanel(name){
  document.querySelectorAll('[data-home-panel]').forEach(function(btn){
    btn.classList.toggle('on',btn.dataset.homePanel===name);
  });
  var core=document.querySelector('.home-core-preview');
  var research=document.querySelector('.home-research-preview');
  if(core) core.classList.toggle('mobile-panel-on',name==='core');
  if(research) research.classList.toggle('mobile-panel-on',name==='research');
}
function showMode(id,restore,keepTarget){
  var active=document.querySelector('.mode-pane.on');
  if(id==='data-mode' && active && active.id!=='data-mode') rememberFocusSpot();
  if(!keepTarget) clearTargetDetail();
  document.querySelectorAll('.mode-pane').forEach(function(s){s.classList.remove('on')});
  document.querySelectorAll('[data-mode]').forEach(function(b){b.classList.remove('on')});
  var el=document.getElementById(id); if(el) el.classList.add('on');
  document.querySelectorAll('[data-mode="'+id+'"]').forEach(function(btn){btn.classList.add('on')});
  var title=document.getElementById('appPageTitle');
  if(title) title.textContent=APP_PAGE_TITLES[id]||'市場報告';
  closeMobileMore();
  if(id==='data-mode' && typeof filterOverview==='function') filterOverview();
  if(restore){setTimeout(restoreFocusSpot,40);}
  else{window.scrollTo({top:0,behavior:'smooth'});}
}
function showTab(id,keepTarget){
  if(!keepTarget) clearTargetDetail();
  document.querySelectorAll('.tc').forEach(function(s){s.classList.remove('on')});
  document.querySelectorAll('.tb').forEach(function(b){b.classList.remove('on')});
  var el=document.getElementById(id); if(el) el.classList.add('on');
  var btn=document.querySelector('[data-tab="'+id+'"]'); if(btn) btn.classList.add('on');
}
document.addEventListener('DOMContentLoaded',function(){
  showTab('tw-stocks');
  showHomePanel('core');
  var requested=(location.hash||'').replace('#','');
  if(APP_PAGE_TITLES[requested]) showMode(requested);
  else if(requested.indexOf('target-')===0){
    var requestedCard=document.getElementById(requested);
    if(requestedCard) openTargetDetail(requested,requestedCard.dataset.tab||'tw-stocks',false);
  }
  var sheet=document.getElementById('mobileMoreSheet');
  if(sheet) sheet.addEventListener('click',function(e){if(e.target===sheet) closeMobileMore();});
});
</script>'''


def mobile_jump_nav_html():
    links = [
        ('#market-summary', '重點'),
        ('#market-radar', '雷達'),
        ('#visual-board', '儀表'),
        ('#core-etfs', '核心ETF'),
        ('#today-focus', '先看'),
        ('#target-overview', '一覽'),
        ('#tools', '工具'),
    ]
    return '<nav class="mobile-jump">' + ''.join(
        f'<a href="{href}">{label}</a>' for href, label in links
    ) + '</nav>'


def mode_switch_html():
    return (
        '<div class="mode-switch" aria-label="切換檢視模式">'
        '<button class="mode-btn on" data-mode="focus-mode" onclick="showMode(\'focus-mode\')" type="button">看重點</button>'
        '<button class="mode-btn" data-mode="data-mode" onclick="showMode(\'data-mode\')" type="button">完整數據</button>'
        '</div>'
    )


def app_sidebar_html(update_time, report_phase=None):
    report_phase = report_phase or {'label': '資料更新'}
    items = [
        ('focus-mode', '今日'),
        ('core-mode', '核心 ETF'),
        ('research-mode', '研究'),
        ('market-mode', '市場'),
        ('tools-mode', '試算'),
        ('data-mode', '完整資料'),
    ]
    buttons = ''.join(
        f'<button class="app-nav-btn{" on" if mode == "focus-mode" else ""}" '
        f'data-mode="{mode}" onclick="showMode(\'{mode}\')" type="button">'
        f'<span>{label}</span></button>'
        for mode, label in items
    )
    return (
        f'<aside class="app-sidebar">'
        f'<div class="app-brand">每日市場報告</div>'
        f'<nav class="app-nav" aria-label="主要導覽">{buttons}</nav>'
        f'<div class="app-sidebar-note">資料更新時間<br><b>{h(update_time)}</b><small>{h(report_phase["label"])}</small></div>'
        f'</aside>'
    )


def mobile_bottom_nav_html():
    return (
        f'<nav class="mobile-bottom-nav" aria-label="手機主要導覽">'
        f'<button class="mobile-nav-btn on" data-mode="focus-mode" onclick="showMode(\'focus-mode\')" type="button">今日</button>'
        f'<button class="mobile-nav-btn" data-mode="core-mode" onclick="showMode(\'core-mode\')" type="button">核心</button>'
        f'<button class="mobile-nav-btn" data-mode="research-mode" onclick="showMode(\'research-mode\')" type="button">研究</button>'
        f'<button class="mobile-nav-btn" data-mode="market-mode" onclick="showMode(\'market-mode\')" type="button">市場</button>'
        f'<button class="mobile-nav-btn" onclick="toggleMobileMore()" type="button">更多</button>'
        f'</nav>'
        f'<div class="mobile-more-sheet" id="mobileMoreSheet">'
        f'<div class="mobile-more-panel"><header><b>更多功能</b><button type="button" onclick="closeMobileMore()">×</button></header>'
        f'<nav>'
        f'<button type="button" onclick="showMode(\'tools-mode\')">試算工具</button>'
        f'<button type="button" onclick="showMode(\'data-mode\')">完整資料與分析卡</button>'
        f'</nav></div></div>'
    )


def home_preview_tabs_html():
    return (
        f'<div class="home-preview-tabs" role="tablist" aria-label="首頁標的摘要">'
        f'<button class="on" data-home-panel="core" onclick="showHomePanel(\'core\')" type="button">核心 ETF</button>'
        f'<button data-home-panel="research" onclick="showHomePanel(\'research\')" type="button">研究</button>'
        f'</div>'
    )


def page_heading_html(title, description, meta=''):
    return (
        f'<div class="page-heading"><div><h2>{h(title)}</h2><p>{h(description)}</p></div>'
        f'<span>{h(meta)}</span></div>'
    )


def build_html(idx_html, tw_s, tw_e, us_s, us_e, bonds, update_time, market_ctx=None, report_phase=None):
    report_phase = report_phase or {'label': '資料更新', 'note': '請以頁面顯示的資料時間為準。'}
    market_full_html = idx_html.replace('id="market-radar"', 'id="market-radar-full"', 1)
    market_home_html = (
        idx_html
        .replace('class="sc market-radar"', 'class="sc market-radar home-market-radar"', 1)
        .replace('id="market-radar"', 'id="market-radar-home"', 1)
    )
    return (
        f'<!DOCTYPE html>\n<html lang="zh-Hant">\n<head>\n'
        f'<meta charset="UTF-8">\n'
        f'<meta name="viewport" content="width=device-width,initial-scale=1.0">\n'
        f'<title>智慧投資分析 | {update_time}</title>\n'
        f'{CSS}\n{APP_SHELL_CSS}\n{UI_OVERRIDE_CSS}\n</head>\n<body>\n'
        f'<div class="app-shell">\n'
        f'{app_sidebar_html(update_time, report_phase)}\n'
        f'<main class="app-main">\n'
        f'<header class="app-topbar"><h1 id="appPageTitle">今日市場</h1>'
        f'<div class="topbar-meta"><span class="topbar-time">資料截至 {update_time} 台灣時間</span>'
        f'<span class="topbar-phase" title="{h(report_phase["note"])}">{h(report_phase["label"])}</span>'
        f'<button class="reload-btn" type="button" onclick="location.reload()">重新載入</button>'
        f'<span class="data-status">資料已載入</span></div></header>\n'
        f'<div class="app-content">\n'
        f'<section class="mode-pane app-page on" id="focus-mode">\n'
        f'{newbie_summary_html(market_ctx)}\n'
        f'{market_home_html}\n'
        f'{sector_flow_radar_html(compact=True)}\n'
        f'{home_preview_tabs_html()}\n'
        f'<div class="home-dashboard-grid">{home_core_preview_html()}{home_research_preview_html()}</div>\n'
        f'{visual_action_board_html(market_ctx)}\n'
        f'</section>\n'
        f'<section class="mode-pane app-page" id="core-mode">\n'
        f'{page_heading_html("核心 ETF", "把長期配置標的集中在這裡，先看體質、價格狀態與可看區間。", "長期配置")}\n'
        f'{core_etf_spotlight_html()}\n'
        f'</section>\n'
        f'<section class="mode-pane app-page" id="research-mode">\n'
        f'{page_heading_html("研究標的", "先看今日需要確認的標的，再用搜尋、體質與價格狀態縮小範圍。", "公開資料研究")}\n'
        f'{today_focus_html()}\n'
        f'{target_overview_html(market_ctx)}\n'
        f'{theme_radar_html()}\n'
        f'</section>\n'
        f'<section class="mode-pane app-page" id="market-mode">\n'
        f'{page_heading_html("市場雷達", "比較台股、美股科技、波動與亞洲市場，理解今天的環境，不把指數直接當買進訊號。", "全球市場")}\n'
        f'{market_full_html}\n'
        f'{sector_flow_radar_html(compact=False)}\n'
        f'</section>\n'
        f'<section class="mode-pane app-page" id="tools-mode">\n'
        f'{page_heading_html("試算工具", "模擬定期投入與臨時買入，把金額、批次、手續費與歷史風險放在同一處。", "不代表個人投資建議")}\n'
        f'<div class="desktop-mid-grid" id="tools">\n'
        f'{dca_simulator_html(market_ctx)}\n'
        f'</div>\n'
        f'</section>\n'
        f'<section class="mode-pane app-page" id="data-mode">\n'
        f'{page_heading_html("完整資料", "保留所有標的完整卡、基本面、ETF 資料、因子分數、技術細節與資料來源。", "進階檢視")}\n'
        f'<section class="target-section" id="target-list">\n'
        f'<div class="section-head"><div><div class="st">標的分析</div>'
        f'<p>一覽看完後，再到這裡看完整卡。每張卡先放結論、原因、反方與行動；技術細節和資料來源可展開。</p></div>'
        f'<span>完整細節保留</span></div>\n'
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
        f'</section>\n'
        f'{methodology_html()}\n'
        f'{public_readiness_html(update_time, market_ctx, report_phase)}\n'
        f'</section>\n'
        f'</div></main>\n'
        f'</div>\n'
        f'{mobile_bottom_nav_html()}\n'
        f'<footer><div class="wrap">\n'
        f'<p>資料來源：TWSE 盤後公開資料 / TWSE ETF e添富配息清單 / FinMind 免費公開資料 / 政府資料開放平臺基金資料 / Yahoo Finance 免費公開資料 | 分析方式：規則化因子評分（無任何 AI API）</p>\n'
        f'<p>以上分析僅供參考，不構成投資建議。投資有風險，請自行判斷。</p>\n'
        f'</div></footer>\n'
        f'{JS_CODE}\n</body>\n</html>'
    )


def report_is_healthy(html):
    card_count = len(re.findall(r'class="sc target-card(?:\s|")', html))
    failure_count = html.count('資料暫時無法取得') + html.count('處理失敗')
    if card_count < 30:
        return False, f'卡片數過少：{card_count}'
    if failure_count > 5:
        return False, f'資料失敗卡過多：{failure_count}'
    return True, f'卡片 {card_count}，失敗卡 {failure_count}'


# =============================================================
# 資料擷取
# =============================================================

def flow_strength_label(score):
    v = safe_num(score)
    if v is None:
        return '待補'
    if v >= 70:
        return '偏強'
    if v >= 55:
        return '略強'
    if v >= 45:
        return '中性'
    if v >= 30:
        return '偏弱'
    return '明顯偏弱'


def is_core_etf_ticker(ticker):
    return ticker in ['0050.TW', '006208.TW', '006204.TW', '00662.TW']


def flow_side_text(value):
    v = safe_num(value) or 0
    if v > 0:
        return '買超'
    if v < 0:
        return '賣超'
    return '持平'


def flow_brief(label, value):
    return f'{label}{flow_side_text(value)} {fmt_net_lots(value)}'


def institutional_focus_text(meta):
    bucket = str((meta or {}).get('bucket') or '')
    industry = str((meta or {}).get('industry') or '')
    name = str((meta or {}).get('long_name') or (meta or {}).get('short_name') or '')
    text = '外資 / 投信 / 自營商都看，但重點不同。'
    if 'ETF' in bucket or 'ETF' in name:
        text += ' ETF 與權值型標的先看外資，其次投信；自營商多作短線參考。'
    elif any(word in industry + name for word in ['半導體', '電子', 'AI', '電力', '散熱', '伺服器']):
        text += ' 題材與科技股要同時看投信和外資：投信代表內資主流，外資代表國際資金是否認同。'
    else:
        text += ' 權值股偏重外資，中小型與題材股提高投信權重，自營商只當短線溫度計。'
    return text


def chip_flow_judgment(meta, price=None):
    if not meta or not meta.get('finmind_chip_available'):
        return dict(
        available=False,
        state='三大法人資料待補',
        tone='neutral',
        score=50,
        strength_label='待補',
        focus=(
            '三大法人指外資、投信、自營商。外資偏國際資金，投信偏台灣基金資金，'
            '自營商偏短線與避險交易。'
        ),
        summary='這檔暫時抓不到外資、投信、自營商買賣超，不能用資金流判斷。',
        action='先回到價格、趨勢與基本面檢查。',
        )

    total_1d = safe_num(meta.get('finmind_institutional_net_buy')) or 0
    total_5d = safe_num(meta.get('finmind_institutional_net_buy_5d')) or 0
    total_20d = safe_num(meta.get('finmind_institutional_net_buy_20d')) or 0
    foreign_5d = safe_num(meta.get('finmind_foreign_net_buy_5d')) or 0
    trust_5d = safe_num(meta.get('finmind_investment_trust_net_buy_5d')) or 0
    dealer_5d = safe_num(meta.get('finmind_dealer_net_buy_5d')) or 0
    streak = safe_num(meta.get('finmind_institutional_streak')) or 0

    score = 50
    score += 12 if total_5d > 0 else -12 if total_5d < 0 else 0
    score += 8 if total_20d > 0 else -8 if total_20d < 0 else 0
    score += 8 if foreign_5d > 0 else -7 if foreign_5d < 0 else 0
    score += 6 if trust_5d > 0 else -5 if trust_5d < 0 else 0
    score += 3 if dealer_5d > 0 else -2 if dealer_5d < 0 else 0
    if streak >= 3:
        score += 5
    elif streak <= -3:
        score -= 5
    score = clamp_score(score)
    strength = flow_strength_label(score)

    if total_5d > 0 and total_20d > 0 and foreign_5d > 0 and trust_5d > 0:
        state, tone = '外資與投信同步偏買', 'positive'
        action = '資金面是加分，但仍要確認價格不是失控追高。'
    elif total_5d > 0 and (foreign_5d > 0 or trust_5d > 0):
        state, tone = '三大法人短線偏買', 'positive'
        action = '可以提高觀察順位，但不能只因買超就進場。'
    elif total_5d > 0 and total_20d <= 0:
        state, tone = '短線回補，中期未確認', 'mixed'
        action = '短線買盤回來，但還要看後續是否延續。'
    elif total_5d < 0 and total_20d < 0:
        state, tone = '三大法人偏賣', 'negative'
        action = '資金正在撤，價格看似便宜時也要防便宜陷阱。'
    elif total_5d < 0:
        state, tone = '短線偏賣', 'mixed'
        action = '短線資金退潮，先看支撐有沒有守住。'
    else:
        state, tone = '資金方向不明', 'neutral'
        action = '三大法人沒有明顯方向，回到趨勢與基本面。'

    summary = (
        f'近 5 日合計{fmt_net_lots(total_5d)}，近 20 日合計{fmt_net_lots(total_20d)}；'
        f'{flow_brief("外資 5 日", foreign_5d)}，{flow_brief("投信 5 日", trust_5d)}，'
        f'{flow_brief("自營商 5 日", dealer_5d)}。'
    )
    return dict(
        available=True,
        state=state,
        tone=tone,
        score=score,
        strength_label=strength,
        focus=institutional_focus_text(meta),
        summary=summary,
        action=action,
        total_1d=total_1d,
        total_5d=total_5d,
        total_20d=total_20d,
        foreign_5d=foreign_5d,
        trust_5d=trust_5d,
        dealer_5d=dealer_5d,
        streak=streak,
    )


def flow_bar_html(label, value, max_abs):
    v = safe_num(value) or 0
    base = max(safe_num(max_abs) or 1, 1)
    pct = min(100, abs(v) / base * 100)
    cls = 'flow-in' if v > 0 else 'flow-out' if v < 0 else 'flow-flat'
    return (
        f'<div class="flow-bar-row {cls}">'
        f'<span>{h(label)}</span><div class="flow-track"><i style="width:{pct:.0f}%"></i></div>'
        f'<b>{h(fmt_net_lots(v))}</b></div>'
    )


def institutional_flow_html(meta, price=None):
    chip = chip_flow_judgment(meta, price)
    tone = chip.get('tone', 'neutral')
    if not chip.get('available'):
        return (
            f'<div class="chip-flow-box chip-neutral">'
            f'<div class="chip-flow-head"><div><span>三大法人資金流</span><b>{h(chip["state"])}</b></div>'
            f'<strong>待補</strong></div><p>{h(chip["summary"])}</p><div class="chip-focus-note">{h(chip.get("focus", ""))}</div></div>'
        )

    max_abs = max(
        abs(safe_num(chip.get('total_5d')) or 0),
        abs(safe_num(chip.get('foreign_5d')) or 0),
        abs(safe_num(chip.get('trust_5d')) or 0),
        abs(safe_num(chip.get('dealer_5d')) or 0),
        1,
    )
    bars = ''.join([
        flow_bar_html('合計 5 日', chip.get('total_5d'), max_abs),
        flow_bar_html('外資 5 日', chip.get('foreign_5d'), max_abs),
        flow_bar_html('投信 5 日', chip.get('trust_5d'), max_abs),
        flow_bar_html('自營商 5 日', chip.get('dealer_5d'), max_abs),
    ])
    tiles = ''.join([
        metric_tile('資料日', meta.get('finmind_chip_date') or 'N/A', meta.get('finmind_chip_source', 'FinMind')),
        metric_tile('1 日合計', fmt_net_lots(chip.get('total_1d')), '最新一日三大法人合計'),
        metric_tile('5 日合計', fmt_net_lots(chip.get('total_5d')), '短線資金方向'),
        metric_tile('20 日合計', fmt_net_lots(chip.get('total_20d')), '中期資金方向'),
        metric_tile('約當金額', fmt_flow_amount(chip.get('total_5d'), price), '5 日買賣超股數 × 最新價格'),
        metric_tile('連續狀態', fmt_flow_streak(chip.get('streak')), '連買/連賣越久越值得注意'),
    ])
    return (
        f'<div class="chip-flow-box chip-{h(tone)}">'
        f'<div class="chip-flow-head"><div><span>三大法人資金流</span><b>{h(chip["state"])}</b></div>'
        f'<strong>資金流：{h(chip["strength_label"])} {chip.get("score", 50):.0f}/100</strong></div>'
        f'<p>{h(chip["summary"])}</p>'
        f'<div class="chip-focus-note">{h(chip.get("focus", ""))}</div>'
        f'<div class="chip-flow-visual">{bars}</div>'
        f'<div class="detail-grid chip-flow-grid">{tiles}</div>'
        f'<details class="chip-flow-formula"><summary>約當金額怎麼算？</summary>'
        f'<p>近 5 日三大法人買賣超股數 × 最新價格，只能看方向與量級，不是交易所提供的精準成交金額。</p></details>'
        f'<div class="detail-note">{h(chip["action"])} 資料來源：{h(meta.get("finmind_chip_source", "FinMind InstitutionalInvestorsBuySell"))}；買賣超為股數換算張數。</div>'
        f'</div>'
    )


def price_stage_inputs(ticker, price, a, ext):
    px = safe_num(price)
    ma20 = safe_num(ext.get('ma20'))
    ma60 = safe_num(ext.get('ma60'))
    ma240 = safe_num(ext.get('ma240'))
    vol = volatility_profile(ticker, px or 0, a, ext)
    atr = safe_num(vol.get('atr')) or ((px or 0) * 0.02)
    atr_pct = safe_num(vol.get('atr_pct')) or 2.0
    band_abs = safe_num(vol.get('band_abs')) or atr
    w_pct = safe_num(ext.get('w_pct'))
    if w_pct is None:
        w_pct = 50
    fs = a.get('factor_scores') or {}
    meta = a.get('meta') or {}
    chip = chip_flow_judgment(meta, px)
    return dict(
        px=px, ma20=ma20, ma60=ma60, ma240=ma240, vol=vol, atr=atr,
        atr_pct=atr_pct, band_abs=band_abs, w_pct=w_pct,
        rsi=safe_num(a.get('rsi')), dev=safe_num(a.get('dev20')),
        bpct=safe_num(a.get('bpct')), vol_ratio=safe_num(a.get('vol_ratio')),
        macd_bull=a.get('macd_bull'), risk=safe_num(fs.get('risk')) or 50,
        trend=safe_num(fs.get('trend')) or 50,
        fundamental=safe_num(fs.get('fundamental')) or 50,
        chip=chip,
    )


def classify_price_stage(ticker, data):
    px, ma20, ma60, ma240 = data['px'], data['ma20'], data['ma60'], data['ma240']
    if px is None or ma20 is None or ma60 is None:
        return dict(stage='資料不足', key='unknown', tone='neutral', warning_score=0)
    atr_pct = data['atr_pct']
    w_pct = data['w_pct']
    rsi = data['rsi']
    dev = data['dev']
    bpct = data['bpct']
    vol_ratio = data['vol_ratio'] or 1
    chip = data['chip']
    risk = data['risk']
    fundamental = data['fundamental']

    up_structure = px >= ma20 and ma20 >= ma60 and (ma240 is None or ma60 >= ma240 * 0.98)
    above_support = px >= ma60 and (ma240 is None or px >= ma240 * 0.98)
    below_ma20 = px < ma20
    below_ma60 = px < ma60
    high_position = (
        w_pct >= 85
        or (dev is not None and dev >= max(6, atr_pct * 1.8))
        or (bpct is not None and bpct >= 88)
    )
    cheap_position = (
        w_pct <= 35
        or px <= ma60
        or (dev is not None and dev <= -max(4, atr_pct * 1.2))
    )
    momentum_hot = (
        (rsi is not None and rsi >= 78)
        or (dev is not None and dev >= max(8, atr_pct * 2.3))
        or (bpct is not None and bpct >= 92 and vol_ratio >= 1.2)
    )
    momentum_weak = (
        below_ma20
        or data['macd_bull'] is False
        or (rsi is not None and rsi < 45)
        or (bpct is not None and bpct < 45)
    )
    chip_weak = chip.get('available') and (
        (chip.get('total_5d', 0) < 0 and chip.get('total_20d', 0) < 0)
        or (chip.get('foreign_5d', 0) < 0 and chip.get('total_5d', 0) < 0)
    )
    chip_strong = chip.get('available') and chip.get('total_5d', 0) > 0 and (
        chip.get('foreign_5d', 0) > 0 or chip.get('trust_5d', 0) > 0
    )
    basic_weak = fundamental < 42
    risk_weak = risk < 40
    bargain_trap = cheap_position and (below_ma60 or chip_weak or basic_weak or risk_weak)

    warning_score = 0
    warning_score += 25 if high_position else 0
    warning_score += 18 if momentum_hot else 0
    warning_score += 18 if momentum_weak else 0
    warning_score += 18 if chip_weak else 0
    warning_score += 12 if basic_weak else 0
    warning_score += 9 if risk_weak else 0
    warning_score = min(100, warning_score)

    if bargain_trap:
        return dict(stage='便宜陷阱', key='trap', tone='danger', warning_score=warning_score)
    if below_ma60 and (momentum_weak or chip_weak or basic_weak):
        return dict(stage='轉弱下跌', key='weak', tone='danger', warning_score=max(warning_score, 70))
    if high_position and warning_score >= 62:
        return dict(stage='高檔轉弱預警', key='sell_warning', tone='hot', warning_score=warning_score)
    if up_structure and high_position and not momentum_weak and not chip_weak:
        return dict(stage='多頭續強', key='bull_run', tone='momentum', warning_score=warning_score)
    if up_structure and below_ma20 and above_support and not chip_weak and not basic_weak:
        return dict(stage='疑似震盪洗盤', key='shakeout', tone='ok', warning_score=warning_score)
    if above_support and not chip_weak and not basic_weak and px <= ma20 + data['band_abs'] * 0.6:
        return dict(stage='多頭正常回檔', key='pullback', tone='ok', warning_score=warning_score)
    if high_position:
        return dict(stage='高檔震盪', key='high_range', tone='warm', warning_score=warning_score)
    if px >= ma60 and not basic_weak:
        return dict(stage='合理分批區', key='normal', tone='ok', warning_score=warning_score)
    return dict(stage='等待確認', key='wait', tone='warm', warning_score=warning_score)


def stage_action_copy(stage_key, ticker, is_core):
    if stage_key == 'bull_run':
        return (
            '趨勢還在，今天看起來高，未來不一定仍是高。',
            '怕錯過可以小額試單；想買得舒服，等回踩承接區。',
            '不要因為創高就賣，但也不要一次重押。'
        )
    if stage_key == 'pullback':
        return (
            '比較像多頭中的正常回檔，尚未破壞趨勢。',
            '可分批觀察，優先等回踩承接區。',
            '若跌破支撐且三大法人轉賣，再改成防守。'
        )
    if stage_key == 'shakeout':
        return (
            '像震盪洗盤，但還不能直接認定主力會拉回去。',
            '先看是否站回短均；小額分批可以，重押不適合。',
            '若 3 日內站不回支撐，視為轉弱。'
        )
    if stage_key == 'sell_warning':
        return (
            '價格在高位，且動能或資金開始變弱。',
            '新買先停；已有持股者準備保護獲利。',
            '若跌破短線支撐且三大法人續賣，可先減碼。'
        )
    if stage_key == 'weak':
        return (
            '不是便宜，是趨勢正在變弱。',
            '暫不入場，不要因為跌了就攤平。',
            '等止跌、站回支撐、資金回流後再重新看。'
        )
    if stage_key == 'trap':
        return (
            '價格看似便宜，但下跌可能有原因。',
            '暫不入場；先確認基本面、三大法人與趨勢是否修復。',
            '這種低價不當成打折，先當成風險警訊。'
        )
    if stage_key == 'high_range':
        return (
            '高檔整理中，方向還沒明確。',
            '不急著追；可用小額追蹤或等回踩。',
            '若重新放量突破可追蹤，跌破支撐就防守。'
        )
    if stage_key == 'normal':
        return (
            '價格與趨勢相對正常。',
            '可按區間分批，不需要一次買滿。',
            '若基本面或資金轉弱，分批計畫要暫停。'
        )
    return ('資料還不夠明確。', '先觀察，不急著下單。', '等資料與趨勢更清楚再決定。')


def calc_price_zones(ticker, price, a, ext):
    data = price_stage_inputs(ticker, price, a, ext)
    px, ma20, ma60 = data['px'], data['ma20'], data['ma60']
    if px is None or ma20 is None or ma60 is None:
        return None

    ma240 = data['ma240']
    atr = data['atr'] or px * 0.02
    band = data['band_abs'] or atr
    vol = data['vol']
    boll_u = safe_num(a.get('boll_u'))
    is_core = is_core_etf_ticker(ticker)
    is_etf = is_etf_like(ticker)
    stage = classify_price_stage(ticker, data)
    stage_key = stage['key']
    summary, action, counter = stage_action_copy(stage_key, ticker, is_core)

    support = ma60 if ma240 is None else max(ma60, ma240 * 0.98)
    if stage_key in ['bull_run', 'high_range', 'sell_warning']:
        buy_low_raw = max(ma20 - 0.9 * band, support - 0.25 * band)
        buy_high_raw = ma20 + 0.35 * band
    elif stage_key in ['pullback', 'shakeout', 'normal']:
        buy_low_raw = max(support - 0.45 * band, ma20 - 1.2 * band)
        buy_high_raw = ma20 + 0.45 * band
    elif stage_key in ['trap', 'weak']:
        buy_low_raw = support - 1.4 * band
        buy_high_raw = support - 0.35 * band
    else:
        buy_low_raw = ma60 - 0.6 * band
        buy_high_raw = ma20 + 0.3 * band

    buy_low_raw, buy_high_raw = sorted([buy_low_raw, buy_high_raw])
    dynamic_chase = ma20 + 2.0 * atr
    if boll_u is not None:
        dynamic_chase = min(dynamic_chase, boll_u * 1.01)
    chase_raw = max(buy_high_raw * 1.02, dynamic_chase)
    stop_raw = min(ma60 - 0.8 * band, support - 0.7 * band)
    early_warning_raw = max(ma20 - 0.35 * band, ma60 + 0.15 * band)

    buy_low = zone_price(ticker, buy_low_raw)
    buy_high = zone_price(ticker, buy_high_raw)
    chase_limit = zone_price(ticker, chase_raw)
    stop_line = zone_price(ticker, stop_raw)
    early_warning_line = zone_price(ticker, early_warning_raw)
    if not all(v is not None for v in [buy_low, buy_high, chase_limit, stop_line]):
        return None
    if stop_line >= buy_low:
        stop_line = zone_price(ticker, buy_low * 0.96)
    if chase_limit <= buy_high:
        chase_limit = zone_price(ticker, buy_high * 1.03)

    momentum_allowed = stage_key in ['bull_run', 'high_range'] and stage['warning_score'] < 60
    momentum_pull = max(0.25 * atr, px * (0.003 if is_core else 0.006))
    momentum_entry = zone_price(ticker, max(buy_high, px - momentum_pull)) if momentum_allowed else None
    reduce_watch = zone_price(ticker, chase_limit)
    trim_line = zone_price(ticker, max(chase_limit + 1.1 * atr, buy_high * (1.08 if is_etf else 1.12)))
    failure_line = stop_line

    if stage_key in ['trap', 'weak']:
        entry_mode = '暫不進場'
        entry_reason = '低價來自轉弱或資料惡化，先確認修復，不把它當便宜。'
    elif stage_key in ['bull_run', 'high_range']:
        entry_mode = '小額試單 / 回踩承接'
        entry_reason = '強勢趨勢可以怕錯過，但只能小額；主要買點仍等回踩。'
    elif stage_key in ['pullback', 'shakeout', 'normal']:
        entry_mode = '分批承接'
        entry_reason = '趨勢尚未破壞，可把買點拆成 3 批，不一次買滿。'
    else:
        entry_mode = '等待確認'
        entry_reason = '訊號不夠明確，先等價格或資金方向更清楚。'

    if is_core:
        sell_status = '核心續抱，先看預警'
        sell_text = '核心 ETF 通常不是因為偏高就賣；高位轉弱時先停止額外加碼。'
    elif is_etf:
        sell_status = '高位轉弱才減碼'
        sell_text = 'ETF 先看折溢價、流動性與趨勢；不是到價就賣。'
    else:
        sell_status = '高位轉弱可先減碼'
        sell_text = '個股若高位轉弱、三大法人續賣或基本面跟不上，可先減碼一部分。'

    if stage_key == 'sell_warning':
        sell_status = '提前預警，可準備保護獲利'
        sell_text = f'若跌破 {fmt_zone_price(early_warning_line)} 且三大法人續賣，可先減碼 10%~20%。'
    elif stage_key in ['weak', 'trap']:
        sell_status = '不接刀，有持股先降風險'
        sell_text = f'若已持有且跌破 {fmt_zone_price(failure_line)} 後站不回，要重新檢查持有理由。'

    buy_steps = [
        ('第 1 批', entry_mode, entry_reason),
        ('回踩區', f'{fmt_zone_price(buy_low)} ~ {fmt_zone_price(buy_high)}', '價格回到這裡且沒有轉壞，才是比較舒服的承接區。'),
        ('停止買', f'跌破 {fmt_zone_price(stop_line)}', '跌破支撐不是更便宜，而是先確認是否變成便宜陷阱。'),
    ]
    if momentum_allowed:
        buy_steps[0] = ('小額試單', f'{fmt_zone_price(momentum_entry)} 附近', '只適合怕錯過的少量追蹤，主力資金仍等回踩。')

    sell_steps = [
        ('高位提醒', f'高於 {fmt_zone_price(reduce_watch)}', '不代表立刻賣，只代表不要再追太大。'),
        ('提前預警', f'跌破 {fmt_zone_price(early_warning_line)}', '若同時三大法人轉賣，可先減碼 10%~20%。'),
        ('正式重看', f'跌破 {fmt_zone_price(failure_line)}', '若 3 日內站不回，重新檢查趨勢與基本面。'),
    ]

    watch_items = [
        f'現在階段：{stage["stage"]}，不是只看高低價。',
        f'回踩承接區：{fmt_zone_price(buy_low)} ~ {fmt_zone_price(buy_high)}。',
        f'提前預警線：{fmt_zone_price(early_warning_line)}；跌破後要看是否站回。',
        f'三大法人：{data["chip"].get("summary", "資料待補")}',
        '便宜要分兩種：健康回檔可以觀察，轉弱下跌先不要接。',
    ]

    return dict(
        status=stage['stage'],
        stage_key=stage_key,
        summary=summary,
        action=action,
        counter=counter,
        tone=stage['tone'],
        warning_score=stage['warning_score'],
        buy_low=buy_low,
        buy_high=buy_high,
        stop_line=stop_line,
        chase_limit=chase_limit,
        momentum_entry=momentum_entry,
        momentum_allowed=momentum_allowed,
        range_text=f'{fmt_zone_price(buy_low)} ~ {fmt_zone_price(buy_high)}',
        guard_text=f'高於 {fmt_zone_price(chase_limit)} 不重押；低於 {fmt_zone_price(stop_line)} 先重看',
        reduce_watch=reduce_watch,
        trim_line=trim_line,
        failure_line=failure_line,
        early_warning_line=early_warning_line,
        sell_status=sell_status,
        sell_text=sell_text,
        fail_text=f'若跌破 {fmt_zone_price(failure_line)} 且 3 日內站不回，代表這波趨勢要重新檢查。',
        buy_steps=buy_steps,
        sell_steps=sell_steps,
        watch_items=watch_items,
        entry_mode=entry_mode,
        entry_reason=entry_reason,
        price=px,
        atr_pct=round(data['atr_pct'], 2),
        volatility_label=vol.get('level'),
        turning_weak=stage_key in ['sell_warning', 'weak', 'trap'],
        overheated=stage_key in ['bull_run', 'high_range', 'sell_warning'],
        bargain_trap=stage_key == 'trap',
        basis='用市場環境、均線結構、ATR、三大法人與基本面一起判斷，不把高低價當唯一訊號。',
        ma_text=f'20日線 {fmt_zone_price(ma20)}、60日線 {fmt_zone_price(ma60)}'
                + (f'、240日線 {fmt_zone_price(ma240)}' if ma240 is not None else ''),
        role=get_profile(ticker).get('role', ''),
        w_pct=round(data['w_pct'], 1),
    )


def action_tone(status):
    if status in ['多頭正常回檔', '疑似震盪洗盤', '合理分批區']:
        return 'ok'
    if status in ['多頭續強', '高檔震盪', '等待確認', '資料不足']:
        return 'wait'
    if status in ['高檔轉弱預警', '轉弱下跌', '便宜陷阱']:
        return 'stop'
    return 'wait'


def home_action_text(item):
    status = zone_status(item)
    if status in ['多頭正常回檔', '疑似震盪洗盤', '合理分批區']:
        return '可分批觀察'
    if status == '多頭續強':
        return '小額試單或等回踩'
    if status == '高檔震盪':
        return '不重押，等方向'
    if status == '高檔轉弱預警':
        return '停買，持有者看預警'
    if status in ['轉弱下跌', '便宜陷阱']:
        return '暫不入場'
    return '先觀察'


def price_zone_html(zone):
    if not zone:
        return ''
    tone = zone.get('tone', 'neutral')
    px = safe_num(zone.get('price'))
    chase = safe_num(zone.get('chase_limit'))
    stop = safe_num(zone.get('stop_line'))
    marker = 50
    if px is not None and stop is not None and chase is not None and chase > stop:
        marker = max(3, min(97, (px - stop) / (chase - stop) * 100))

    buy_high = fmt_zone_price(zone.get('buy_high'))
    chase_text = fmt_zone_price(zone.get('chase_limit'))
    stop_text = fmt_zone_price(zone.get('failure_line') or zone.get('stop_line'))
    strong_text = f'{buy_high} ~ {chase_text}'
    risk_text = f'>{chase_text}'

    step_html = ''.join(
        f'<div><span>{h(title)}</span><b>{h(value)}</b><small>{h(note)}</small></div>'
        for title, value, note in zone.get('buy_steps', [])
    )
    sell_step_html = ''.join(
        f'<div><span>{h(title)}</span><b>{h(value)}</b><small>{h(note)}</small></div>'
        for title, value, note in zone.get('sell_steps', [])
    )
    watch_html = ''.join(f'<li>{h(x)}</li>' for x in zone.get('watch_items', [])[:5])
    return (
        f'<div class="zone-box zone-{h(tone)} zone-v2 price-position-card">'
        f'<div class="zone-head price-position-head"><span>價格位置圖</span><b>{h(zone["status"])}</b></div>'
        f'<div class="price-position-lead">'
        f'<div><span>目前解讀</span><strong>{h(zone["summary"])}</strong><p>{h(zone["action"])}</p></div>'
        f'<em>{h(zone.get("entry_mode", "先觀察"))}</em>'
        f'</div>'
        f'<div class="price-map" aria-label="價格位置圖">'
        f'<div class="price-map-rail">'
        f'<div class="price-map-segments">'
        f'<span class="seg-reset"></span><span class="seg-entry"></span><span class="seg-strong"></span><span class="seg-risk"></span>'
        f'</div>'
        f'<i class="price-map-marker" style="left:{marker:.0f}%"><span>現價</span></i>'
        f'</div>'
        f'<div class="price-map-labels">'
        f'<div><span>重看線</span><b>{h(stop_text)}</b><small>跌破先停看</small></div>'
        f'<div><span>承接區</span><b>{h(zone["range_text"])}</b><small>比較舒服的分批區</small></div>'
        f'<div><span>強勢區</span><b>{h(strong_text)}</b><small>趨勢還在但不重押</small></div>'
        f'<div><span>追價風險</span><b>{h(risk_text)}</b><small>上方只追蹤不衝動</small></div>'
        f'</div>'
        f'</div>'
        f'<div class="price-decision-grid">'
        f'<div><span>入場判斷</span><b>{h(zone.get("entry_mode", "先觀察"))}</b><small>{h(zone.get("entry_reason", ""))}</small></div>'
        f'<div><span>賣出預警</span><b>{h(zone.get("sell_status", "看預警"))}</b><small>{h(zone.get("sell_text", ""))}</small></div>'
        f'<div><span>改變看法</span><b>低於 {h(stop_text)}</b><small>{h(zone.get("fail_text", ""))}</small></div>'
        f'</div>'
        f'<details class="zone-more price-position-more"><summary>展開看分批、賣出劇本與依據</summary>'
        f'<div class="zone-subtitle">買進拆批</div><div class="zone-steps">{step_html}</div>'
        f'<div class="zone-subtitle">賣出劇本</div><div class="zone-steps">{sell_step_html}</div>'
        f'<div class="zone-subtitle">觀察依據</div><ul>{watch_html}</ul>'
        f'<p>{h(zone.get("basis", ""))}</p></details>'
        f'</div>'
    )


def decision_texts(ticker, a, ext, rec, plan=None):
    zone = calc_price_zones(ticker, safe_num(ext.get('latest_close')) or safe_num(a.get('close')) or safe_num(a.get('price')), a, ext)
    meta = a.get('meta') or {}
    chip = chip_flow_judgment(meta, safe_num(ext.get('latest_close')) or meta.get('quote_price'))
    profile = get_profile(ticker)
    is_core = is_core_etf_ticker(ticker)
    is_etf = is_etf_like(ticker)
    if zone:
        conclusion = f'{zone["status"]}：{zone["entry_mode"]}'
        reason = (
            f'{zone["summary"]} {zone["basis"]} '
            f'三大法人：{chip.get("summary", "資料待補")} '
            f'價格區間：{zone.get("range_text", "N/A")}。'
        )
        counter = zone.get('fail_text') or '如果價格、資金與基本面同時轉弱，就要降低信心。'
        action = zone.get('action') or '先按區間分批，不要情緒追價。'
        if is_core and zone.get('stage_key') not in ['trap', 'weak']:
            action = '核心 ETF 可維持紀律扣款；臨時加碼看回踩區，不要因為市場熱就一次買滿。'
        if zone.get('stage_key') in ['trap', 'weak']:
            action = '先不要入場；這不是單純便宜，而是可能已經轉弱。'
    else:
        conclusion = '資料不足：先觀察'
        reason = '價格或均線資料不足，不能給入場區間。'
        counter = '等資料補齊後再判斷。'
        action = '先不要用這張卡做買賣決策。'
    return dict(conclusion=conclusion, reason=reason, counter=counter, action=action)


def decision_meter_html(label, value, hint=''):
    v = safe_num(value)
    if v is None:
        text, pct, color = 'N/A', 0, '#657282'
    else:
        pct = max(0, min(100, v))
        text = f'{v:.0f}'
        color = '#1D9E75' if pct >= 70 else '#185FA5' if pct >= 55 else '#BA7517' if pct >= 40 else '#D85A30'
    return (
        f'<div class="decision-meter"><div><span>{h(label)}</span><b style="color:{color}">{h(text)}</b></div>'
        f'<i><em style="width:{pct:.0f}%;background:{color}"></em></i><small>{h(hint)}</small></div>'
    )


def decision_evidence_cards_html(ticker, a, ext, zone=None):
    meta = a.get('meta') or {}
    fs = a.get('factor_scores') or {}
    price = safe_num(ext.get('latest_close')) or meta.get('quote_price')
    chip = chip_flow_judgment(meta, price)
    zone = zone or calc_price_zones(ticker, price, a, ext)
    w_pct = safe_num(ext.get('w_pct'))
    risk = safe_num(fs.get('risk')) or 50
    fundamental = safe_num(fs.get('fundamental')) or 50
    w_text = f'{w_pct:.0f}%' if w_pct is not None else 'N/A'
    tech_title = zone.get('status') if zone else '趨勢待確認'
    tech_body = zone.get('summary') if zone else f'52 週位置 {w_text}，先等趨勢資料補齊。'
    if chip.get('available'):
        flow_title = f'{chip.get("strength_label")} {chip.get("score", 50):.0f}/100'
        flow_body = chip.get('summary')
    else:
        flow_title = '資料待補'
        flow_body = '這檔不能用外資、投信、自營商判斷。'
    basic_title = 'ETF 資料' if is_etf_like(ticker) else '基本面'
    basic_body = etf_basic_phrase(meta) if is_etf_like(ticker) else stock_basic_phrase(meta)
    risk_title = '風險可控' if risk >= 65 else '風險中等' if risk >= 45 else '風險偏高'
    risk_body = f'風險分數 {risk:.0f}；基本面/ETF 支撐分 {fundamental:.0f}。低價若伴隨風險升高，不視為買點。'
    cards = [
        ('趨勢階段', tech_title, tech_body, zone.get('tone', 'neutral') if zone else 'neutral'),
        ('三大法人', flow_title, flow_body, chip.get('tone', 'neutral')),
        (basic_title, '支撐條件', basic_body, 'basic'),
        ('風險', risk_title, risk_body, 'risk'),
    ]
    return ''.join(
        f'<div class="decision-mini-card decision-mini-{h(tone)}"><span>{h(label)}</span>'
        f'<b>{h(title)}</b><p>{h(body)}</p></div>'
        for label, title, body, tone in cards
    )


def decision_card_html(ticker, a, ext, rec, plan):
    price = safe_num(ext.get('latest_close')) or safe_num(a.get('close')) or safe_num((a.get('meta') or {}).get('quote_price'))
    zone = calc_price_zones(ticker, price, a, ext)
    d = decision_texts(ticker, a, ext, rec, plan)
    q = data_quality_note(ticker, a)
    meta = a.get('meta') or {}
    chip = chip_flow_judgment(meta, price)
    fs = a.get('factor_scores') or {}
    meters = ''.join([
        decision_meter_html('標的體質', a.get('score'), a.get('stxt', '')),
        decision_meter_html('風險穩定', fs.get('risk'), '越高代表波動壓力較低'),
        decision_meter_html('三大法人', chip.get('score') if chip.get('available') else None, chip.get('state', '待補')),
    ])
    evidence = decision_evidence_cards_html(ticker, a, ext, zone)
    stage = zone.get('status') if zone else '資料不足'
    warning = zone.get('warning_score') if zone else None
    warning_text = f'賣出預警 {warning:.0f}/100' if warning is not None else '預警待補'
    return (
        f'<div class="decision-card decision-card-v2 decision-card-v3">'
        f'<div class="decision-head"><span>今日判斷</span><b class="confidence-pill">資料可信度：{h(q["confidence"])}</b></div>'
        f'<div class="decision-stage-line"><span>{h(stage)}</span><b>{h(warning_text)}</b></div>'
        f'<div class="decision-hero-row"><span>結論</span><strong>{h(d["conclusion"])}</strong></div>'
        f'<div class="decision-action-grid">'
        f'<div><span>現在怎麼做</span><p>{h(d["action"])}</p></div>'
        f'<div><span>什麼情況改變看法</span><p>{h(d["counter"])}</p></div>'
        f'</div>'
        f'<div class="decision-meter-grid">{meters}</div>'
        f'<div class="decision-evidence-grid">{evidence}</div>'
        f'<details class="decision-more"><summary>看完整理由與資料限制</summary>'
        f'<div class="decision-row"><span>理由</span><p>{h(d["reason"])}</p></div>'
        f'<div class="decision-note"><b>資料可信度怎麼看：</b>{h(q["reason"])}<br><span>資料限制：{h(q["notes"])}</span></div>'
        f'</details>'
        f'</div>'
    )


def daily_order_overview_html(market_ctx=None, embedded=False):
    if not BUY_NOW_DATA:
        return ''
    keys = [
        tk for tk in
        list(TW_ETFS.keys()) + list(TW_STOCKS.keys()) +
        list(US_ETFS.keys()) + list(US_STOCKS.keys()) + list(BONDS.keys())
        if tk in BUY_NOW_DATA
    ]
    keys_json = json.dumps(keys, ensure_ascii=False)
    default_budget = PUBLIC_EXAMPLE_PLAN.get('lump_budget', 10000)
    section_tag = 'div' if embedded else 'section'
    section_class = 'tool-card daily-orders embedded-orders' if embedded else 'tool-card daily-orders'
    script = """
function orderCategory(item){
  if(item.is_tw&&item.is_etf) return 'tw-etf';
  if(item.is_tw&&!item.is_etf) return 'tw-stock';
  if(!item.is_tw&&item.is_etf) return 'us-etf';
  return 'us-stock';
}
function orderConfidenceFactor(conf){
  if(conf==='高') return 1;
  if(conf==='中') return 0.75;
  return 0.45;
}
function orderTargetInfo(item){
  var z=item.price_zone||{};
  var status=z.status||'';
  var price=Number(item.price||0);
  var low=Number(z.buy_low||0), high=Number(z.buy_high||0);
  var momentum=Number(z.momentum_entry||0);
  var cls='order-wait', action='先觀察', note='等趨勢或資料更清楚。', target=price;
  if(status==='便宜陷阱'||status==='轉弱下跌'){
    cls='order-stop'; action='暫不入場'; target=0;
    note='這不是單純便宜，可能是趨勢或基本面轉壞。';
  }else if(status==='高檔轉弱預警'){
    cls='order-stop'; action='停買看預警'; target=0;
    note='高位加轉弱，不急著接，持有者看賣出預警。';
  }else if(status==='多頭續強'){
    cls='order-momentum'; action='小額試單'; target=momentum||Math.max(high||0, price*0.995);
    note='牛市可能續強，但只能小額，不重押。';
  }else if(status==='多頭正常回檔'||status==='疑似震盪洗盤'||status==='合理分批區'){
    cls='order-ok'; action='分批觀察'; target=Math.max(low||0, Math.min(high||price, price*0.995));
    note='趨勢未壞，可按區間分批，不一次買滿。';
  }else if(status==='高檔震盪'){
    cls='order-wait'; action='等方向'; target=high||price*0.98;
    note='高檔整理，等回踩或突破確認。';
  }
  return {cls:cls, action:action, note:note, price:target};
}
function orderRatio(item, target){
  var z=item.price_zone||{};
  var status=z.status||'';
  var ratio=0;
  if(status==='多頭正常回檔'||status==='疑似震盪洗盤'||status==='合理分批區') ratio=item.role==='長期核心'?0.28:(item.is_etf?0.10:0.05);
  if(status==='多頭續強') ratio=item.role==='長期核心'?0.12:(item.is_etf?0.06:0.03);
  if(status==='高檔震盪') ratio=item.role==='長期核心'?0.08:0.02;
  if(status==='高檔轉弱預警'||status==='轉弱下跌'||status==='便宜陷阱') ratio=0;
  if(Number(item.risk_score||50)<40) ratio*=0.55;
  ratio*=orderConfidenceFactor(item.confidence);
  if(target.cls==='order-stop') ratio=0;
  return Math.max(0, Math.min(0.45, ratio));
}
function runDailyOrders(){
  var out=document.getElementById('orderRows');
  var cards=document.getElementById('orderCards');
  var budgetEl=document.getElementById('orderBudget');
  if(!out||!budgetEl||!window.BUY_NOW_DATA) return;
  var filterEl=document.getElementById('orderFilter');
  var scopeEl=document.getElementById('orderScope');
  var searchEl=document.getElementById('orderSearch');
  var budget=Math.max(0, Number(budgetEl.value||0));
  var filter=filterEl?filterEl.value:'all';
  var scope=scopeEl?scopeEl.value:'actionable';
  var q=searchEl?String(searchEl.value||'').trim().toLowerCase():'';
  var keys=window.ORDER_KEYS||Object.keys(window.BUY_NOW_DATA);
  var html='', cardHtml='';
  keys.forEach(function(tk){
    var item=window.BUY_NOW_DATA[tk]; if(!item) return;
    var cat=orderCategory(item);
    if(filter!=='all'&&filter!==cat) return;
    var z=item.price_zone||{};
    var code=tk.replace('.TW','');
    var query=[tk, code, item.name||'', item.role||'', item.bucket||'', cat, z.status||'', item.conclusion||'', item.action||''].join(' ').toLowerCase();
    if(q&&query.indexOf(q)<0) return;
    var target=orderTargetInfo(item);
    var actionable=(target.cls==='order-ok'||target.cls==='order-momentum');
    var waiting=(target.cls==='order-wait');
    var riskLine=(target.cls==='order-stop');
    if(scope==='actionable'&&!actionable) return;
    if(scope==='wait'&&!waiting) return;
    if(scope==='risk'&&!riskLine) return;
    var ratio=orderRatio(item,target);
    var amount=Math.round((budget*ratio)/100)*100;
    var fee=0;
    if(item.is_tw&&amount>0) fee=Math.max(window.BUY_MIN_FEE||20, Math.round(amount*(window.BUY_FEE_RATE||0.000855)));
    if(ratio>0&&item.is_tw&&target.price>0&&amount<target.price+fee&&budget>=target.price+fee){
      amount=Math.min(budget, Math.ceil((target.price+fee)/100)*100);
      fee=Math.max(window.BUY_MIN_FEE||20, Math.round(amount*(window.BUY_FEE_RATE||0.000855)));
    }
    var shares=target.price>0 ? Math.floor(Math.max(0, amount-fee)/target.price) : 0;
    if(shares<=0){ amount=0; fee=0; }
    var spend=shares>0 ? shares*target.price+fee : 0;
    var maxBuy=roundOrderPrice(item, z.buy_high||target.price||0, 'buy');
    var sellWatch=roundOrderPrice(item, z.early_warning_line||z.trim_line||z.reduce_watch||0, 'sell');
    var failLine=roundOrderPrice(item, z.failure_line||z.stop_line||0, 'sell');
    var currency=item.is_tw?'TWD':'USD';
    var feeText=item.is_tw?(fee>0?'含手續費約 '+fmtMoney(fee)+' 元':'本次不買，無手續費估算'):'海外手續費/匯率另計';
    html+='<tr>'+
      '<td><b>'+code+' '+item.name+'</b><small>'+item.role+' / '+item.bucket+' / '+currency+'</small></td>'+
      '<td><span class="order-action '+target.cls+'">'+target.action+'</span><small>'+target.note+'</small></td>'+
      '<td><b>'+(target.price?fmtOrderPrice(target.price,item):'不掛買單')+'</b><small>現價 '+fmtOrderPrice(item.price,item)+'</small></td>'+
      '<td><b>'+fmtMoney(spend)+' 元</b><small>'+fmtShares(shares,item)+'，'+feeText+'</small></td>'+
      '<td><b>'+fmtOrderPrice(maxBuy,item)+'</b><small>高於這裡不重押</small></td>'+
      '<td><b>'+fmtOrderPrice(sellWatch,item)+'</b><small>'+(z.sell_status||'看預警')+'</small></td>'+
      '<td><b>'+fmtOrderPrice(failLine,item)+'</b><small>跌破先重看</small></td>'+
      '<td><small>'+((z.status||'區間待補')+'；可信度 '+item.confidence+'；風險 '+(item.risk_score||'N/A'))+'</small></td>'+
      '</tr>';
    cardHtml+='<article class="order-card">'+
      '<div class="order-card-head"><div><b>'+code+' '+item.name+'</b><span>'+item.role+' / '+item.bucket+'</span></div><span class="order-action '+target.cls+'">'+target.action+'</span></div>'+
      '<div class="order-card-grid">'+
      '<div><span>掛單價</span><b>'+(target.price?fmtOrderPrice(target.price,item):'不掛買單')+'</b><small>現價 '+fmtOrderPrice(item.price,item)+'</small></div>'+
      '<div><span>建議投入</span><b>'+fmtMoney(spend)+' 元</b><small>'+fmtShares(shares,item)+'</small></div>'+
      '<div><span>賣出預警</span><b>'+fmtOrderPrice(sellWatch,item)+'</b><small>'+(z.sell_status||'看預警')+'</small></div>'+
      '<div><span>重看線</span><b>'+fmtOrderPrice(failLine,item)+'</b><small>跌破先重看</small></div>'+
      '</div><p>'+target.note+'</p><small>'+((z.status||'區間待補')+'；可信度 '+item.confidence+'；風險 '+(item.risk_score||'N/A'))+'</small></article>';
  });
  out.innerHTML=html||'<tr><td colspan="8"><div class="nd">沒有符合條件的標的。</div></td></tr>';
  if(cards) cards.innerHTML=cardHtml||'<div class="nd">沒有符合條件的標的。</div>';
}
document.addEventListener('DOMContentLoaded',function(){
  ['orderSearch','orderBudget','orderFilter','orderScope'].forEach(function(id){
    var el=document.getElementById(id);
    if(el) el.addEventListener('input',runDailyOrders);
    if(el) el.addEventListener('change',runDailyOrders);
  });
  runDailyOrders();
});
"""
    return (
        f'<{section_tag} class="{section_class}" id="daily-orders">'
        f'<div class="tool-head"><div><div class="st">今日掛單總覽</div>'
        f'<p>每一列都是單一標的獨立試算；重點是看「能不能入場、掛多少、哪裡要停看」，不是把預算平均分給全部標的。</p></div>'
        f'<span class="section-meta">執行試算</span></div>'
        f'<div class="order-filter">'
        f'<label><span>搜尋代碼或名稱</span><input id="orderSearch" type="search" placeholder="輸入 2330、台積電、00662、電力..." autocomplete="off"></label>'
        f'<label><span>單一標的預算</span><input id="orderBudget" type="number" min="1000" step="1000" value="{default_budget}"></label>'
        f'<label><span>顯示</span><select id="orderScope"><option value="actionable">可執行</option><option value="wait">等待觀察</option><option value="risk">風險提醒</option><option value="all">全部</option></select></label>'
        f'<label><span>類別</span><select id="orderFilter">'
        f'<option value="all">全部</option><option value="tw-etf">台股 ETF</option><option value="tw-stock">台股股票</option>'
        f'<option value="us-etf">美股 ETF</option><option value="us-stock">美股股票</option></select></label>'
        f'<label><span>規則口徑</span><select disabled><option>趨勢階段 + 價格區間 + 風險</option></select></label>'
        f'</div>'
        f'<div class="order-wrap"><table class="order-table">'
        f'<thead><tr><th>標的</th><th>今日動作</th><th>參與/掛單價</th><th>建議投入</th><th>最高買價</th><th>賣出預警</th><th>重看線</th><th>理由</th></tr></thead>'
        f'<tbody id="orderRows"></tbody></table></div>'
        f'<div id="orderCards" class="order-cards"></div>'
        f'<div class="tool-note">「不推薦入場」可能有兩種原因：價格太高，或價格雖低但趨勢/資金/基本面轉壞。後者會標成便宜陷阱或轉弱下跌，不能只因為便宜就買。</div>'
        f'<script>window.ORDER_KEYS={keys_json};</script><script>{script}</script>'
        f'</{section_tag}>'
    )


def target_overview_html(market_ctx=None):
    ordered = all_targets_order()
    if not ordered:
        return ''
    target_count = len(ordered)
    rows = []
    cat_labels = {
        'tw-etfs': '台股 ETF',
        'tw-stocks': '台股股票',
        'us-etfs': '美股 ETF',
        'us-stocks': '美股股票',
        'bonds': '債券/商品',
    }
    confidence_rank = {'高': 3, '中': 2, '低': 1}
    for tk in ordered:
        item = BUY_NOW_DATA[tk]
        status = zone_status(item)
        tone = action_tone(status)
        cat = target_tab_id(tk)
        code = tk.replace('.TW', '')
        price = item.get('price')
        price_text = f'{float(price):,.2f}' if isinstance(price, (int, float)) else 'N/A'
        score = safe_num(item.get('score')) or 0
        score_tone = 'good' if score >= 70 else 'mid' if score >= 50 else 'low'
        risk = safe_num(item.get('risk_score')) or 0
        w_pct = safe_num(item.get('w_pct')) or 50
        conf = item.get('confidence', 'N/A')
        watch_score = {'ok': 300, 'wait': 200, 'stop': 100}.get(tone, 0) + score + confidence_rank.get(conf, 0) * 3
        action = item.get('action') or item.get('conclusion') or '先看完整卡'
        query_text = ' '.join([
            code, tk, item.get('name', ''), item.get('role', ''), item.get('bucket', ''),
            cat_labels.get(cat, cat), status, action, zone_range_text(item), item.get('confidence', '')
        ]).lower()
        rows.append(
            f'<div class="overview-row overview-{tone}" data-cat="{h(cat)}" data-tone="{h(tone)}" '
            f'data-score="{score:.2f}" data-risk="{risk:.2f}" data-wpct="{w_pct:.2f}" '
            f'data-watch="{watch_score:.2f}" data-conf="{confidence_rank.get(conf, 0)}" '
            f'data-query="{h(query_text)}" data-order="{len(rows)}">'
            f'<div class="overview-name"><b>{h(code)} {h(item.get("name", ""))}</b>'
            f'<small>{h(cat_labels.get(cat, cat))} / {h(item.get("role", ""))} / {h(item.get("bucket", ""))}</small></div>'
            f'<div class="overview-score score-{score_tone}"><span>體質</span><b>{h(item.get("score", "N/A"))}</b><i><em style="width:{score:.0f}%"></em></i></div>'
            f'<div class="overview-price"><span>現價</span><b>{price_text}</b></div>'
            f'<div class="overview-status"><span>{h(status)}</span><small>{h(zone_range_text(item))}</small></div>'
            f'<div class="overview-confidence"><span>可信度</span><b>{h(conf)}</b></div>'
            f'{target_link(tk, "完整卡", "mini-link overview-link")}'
            f'</div>'
        )
    script = '''
function filterOverview(){
  var list=document.getElementById('overviewList');
  var empty=document.getElementById('overviewEmpty');
  if(!list) return;
  var cat=(document.getElementById('overviewCat')||{}).value||'all';
  var tone=(document.getElementById('overviewTone')||{}).value||'all';
  var sort=(document.getElementById('overviewSort')||{}).value||'watch';
  var q=((document.getElementById('overviewSearch')||{}).value||'').trim().toLowerCase();
  var rows=Array.prototype.slice.call(list.querySelectorAll('.overview-row'));
  rows.sort(function(a,b){
    function n(el,key){return Number(el.dataset[key]||0);}
    if(sort==='score') return n(b,'score')-n(a,'score');
    if(sort==='risk') return n(a,'risk')-n(b,'risk');
    if(sort==='wpct') return n(a,'wpct')-n(b,'wpct');
    if(sort==='conf') return n(b,'conf')-n(a,'conf');
    return n(b,'watch')-n(a,'watch') || n(a,'order')-n(b,'order');
  });
  var shown=0;
  rows.forEach(function(row){
    var query=row.dataset.query||'';
    var ok=(cat==='all'||row.dataset.cat===cat)&&(tone==='all'||row.dataset.tone===tone)&&(!q||query.indexOf(q)>=0);
    row.style.display=ok?'grid':'none';
    list.appendChild(row);
    if(ok) shown++;
  });
  if(empty) empty.style.display=shown?'none':'block';
}
document.addEventListener('DOMContentLoaded',function(){
  ['overviewSearch','overviewCat','overviewTone','overviewSort'].forEach(function(id){
    var el=document.getElementById(id);
    if(el){ el.addEventListener('change',filterOverview); el.addEventListener('input',filterOverview); }
  });
  filterOverview();
});
'''
    return (
        f'<section class="sc target-overview" id="target-overview">'
        f'<div class="tool-head"><div><div class="st">標的總覽</div>'
        f'<p>用體質、價格階段、可信度快速掃描 {target_count} 檔標的。這裡只做市場理解與研究排序，不提供個人化掛單建議。</p></div>'
        f'<span class="section-meta">體質 / 階段</span></div>'
        f'<div class="overview-controls">'
        f'<label class="overview-search"><span>搜尋代碼或名稱</span><input id="overviewSearch" type="search" placeholder="輸入 2330、台積電、00662、電力..." autocomplete="off"></label>'
        f'<label><span>類別</span><select id="overviewCat"><option value="all">全部</option>'
        f'<option value="tw-etfs">台股 ETF</option><option value="tw-stocks">台股股票</option>'
        f'<option value="us-etfs">美股 ETF</option><option value="us-stocks">美股股票</option><option value="bonds">債券/商品</option></select></label>'
        f'<label><span>狀態</span><select id="overviewTone"><option value="all">全部狀態</option>'
        f'<option value="ok">可分批/回檔</option><option value="wait">強勢或等待</option><option value="stop">過熱或轉弱</option></select></label>'
        f'<label><span>排序</span><select id="overviewSort"><option value="watch">優先看</option>'
        f'<option value="score">體質高到低</option><option value="risk">風險低到高</option>'
        f'<option value="wpct">52週位置低到高</option><option value="conf">可信度高到低</option></select></label>'
        f'</div>'
        f'<div class="overview-list" id="overviewList">{"".join(rows)}</div>'
        f'<div class="nd" id="overviewEmpty" style="display:none">沒有符合條件的標的。</div>'
        f'<script>{script}</script>'
        f'</section>'
    )


# =============================================================
# 鞈??瑕?
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


def prioritize_market_reasons(reasons):
    risk_words = ['跌破', '風險', '保守', '偏弱', 'VIX', '恐慌', '降級', '不同步']
    positive_words = ['站上', '偏強', '支撐', '強於']
    def priority(text):
        if any(word in text for word in risk_words):
            return 0
        if any(word in text for word in positive_words):
            return 1
        return 2
    return sorted(reasons, key=priority)


def calc_market_context(raw, quotes=None):
    quotes = quotes or {}
    def idx(ticker):
        try:
            s = raw_close_series(raw, ticker).dropna()
        except Exception:
            s = pd.Series(dtype=float)
        q = quotes.get(ticker) or {}
        last = safe_num(q.get('quote_price')) or (float(s.iloc[-1]) if len(s) else None)
        if last is None or len(s) < 60:
            return dict(ok=False, ticker=ticker, last=last)
        ma20 = float(s.rolling(20).mean().iloc[-1])
        ma60 = float(s.rolling(60).mean().iloc[-1])
        ma240 = float(s.rolling(240).mean().iloc[-1]) if len(s) >= 240 else None
        period = min(126, len(s))
        high = max(float(s.tail(period).max()), float(last))
        low = min(float(s.tail(period).min()), float(last))
        pos = (last - low) / (high - low) * 100 if high > low else 50
        return dict(
            ok=True, ticker=ticker, series=s, last=last, ma20=ma20, ma60=ma60, ma240=ma240,
            pos=pos, r20=pct_return(s, 20), r60=pct_return(s, 60)
        )

    tw = idx('^TWII')
    spx = idx('^GSPC')
    ixic = idx('^IXIC')
    sox = idx('^SOX')
    n225 = idx('^N225')
    hsi = idx('^HSI')
    hstech = idx('HSTECH.HK')
    kospi = idx('^KS11')
    kosdaq = idx('^KQ11')
    vix_series = idx('^VIX')
    if not tw.get('ok'):
        return dict(
            regime='資料不足', temperature='中性', advice='資料不足時先照計畫小額分批。',
            headline='資料不足，今天先不要因單一訊號改變策略。',
            temperature_key='neutral',
            trend_state='資料不足', emotion_state='中性', risk_state='中', chase_state='保守',
            asia_state='資料不足', asia_note='亞洲指數資料不足',
            reasons=['台股大盤資料不足。'], counters=['等資料更新後再判斷。'], actions=['定期定額可照計畫，單筆先小額。'],
            score=50, position=50, vix=None,
        )

    vix_quote = quotes.get('^VIX') or {}
    vix_last = safe_num(vix_quote.get('quote_price')) or vix_series.get('last') or 20
    score = 50
    reasons = []
    counters = []
    actions = []

    if tw['last'] > tw['ma20']:
        score += 10
        reasons.append('台股仍站上月線，短線趨勢沒有明顯破壞。')
    else:
        score -= 12
        reasons.append('台股跌破月線，短線要保守。')
    if tw['last'] > tw['ma60']:
        score += 16
        reasons.append('台股站上季線，中期趨勢仍有支撐。')
    else:
        score -= 18
        reasons.append('台股跌破季線，中期風險升高。')
    if tw.get('ma240') and tw['last'] > tw['ma240']:
        score += 8
    elif tw.get('ma240'):
        score -= 12
        reasons.append('台股跌破年線，單筆加碼要降級。')

    us_support = 0
    for item, label in [(spx, '標普500'), (ixic, '那斯達克'), (sox, '費城半導體')]:
        if item.get('ok') and item['last'] > item['ma20']:
            us_support += 1
    if us_support >= 2:
        score += 8
        reasons.append('美股主要指數多數站上月線，外部風險暫時沒有全面轉弱。')
    elif us_support == 0:
        score -= 10
        reasons.append('美股主要指數同步轉弱，台股科技鏈要提高警覺。')

    if vix_last < 18:
        score += 7
        emotion_state = '偏樂觀'
    elif vix_last > 30:
        score -= 18
        emotion_state = '恐慌'
        reasons.append('VIX 高於 30，市場進入系統性風險區。')
    elif vix_last > 23:
        score -= 8
        emotion_state = '緊張'
    else:
        emotion_state = '中性'

    semis_hot = sox.get('ok') and (sox.get('r20') or 0) > max((spx.get('r20') or 0) + 2, 4)
    if semis_hot:
        reasons.append('費半短期表現強於大盤，AI/半導體仍是主要題材。')

    asia_markets = [
        (n225, '日本'),
        (hsi, '香港'),
        (hstech, '香港科技'),
        (kospi, '韓國大型股'),
        (kosdaq, '韓國成長股'),
    ]
    asia_ok = [(item, label) for item, label in asia_markets if item.get('ok')]
    asia_above_ma20 = [(item, label) for item, label in asia_ok if item['last'] > item['ma20']]
    asia_below_ma20 = [(item, label) for item, label in asia_ok if item['last'] <= item['ma20']]
    asia_r20 = [item.get('r20') for item, _ in asia_ok if item.get('r20') is not None]
    korea_strong = (
        kospi.get('ok') and kospi['last'] > kospi['ma20'] and
        kosdaq.get('ok') and kosdaq['last'] > kosdaq['ma20']
    )
    hk_weak = (
        hsi.get('ok') and hsi['last'] <= hsi['ma20'] and
        hstech.get('ok') and hstech['last'] <= hstech['ma20']
    )
    if len(asia_ok) >= 3:
        asia_ratio = len(asia_above_ma20) / len(asia_ok)
        avg_asia_r20 = sum(asia_r20) / len(asia_r20) if asia_r20 else None
        if asia_ratio >= 0.7:
            asia_state = '同步偏強'
            asia_note = '多數亞洲市場站上月線'
            score += 5
            reasons.append('亞洲主要市場多數站上月線，區域資金情緒偏正向。')
        elif asia_ratio <= 0.3:
            asia_state = '同步偏弱'
            asia_note = '多數亞洲市場跌破月線'
            score -= 7
            reasons.append('亞洲主要市場多數跌破月線，外資風險偏好要保守看。')
        else:
            asia_state = '區域分歧'
            asia_note = '亞洲市場不同步'
            if korea_strong and hk_weak:
                reasons.append('韓國科技鏈仍有支撐，但香港偏弱，代表亞洲市場不是全面樂觀。')
            elif korea_strong:
                reasons.append('韓國市場偏強，半導體與科技供應鏈仍有支撐。')
            elif hk_weak:
                reasons.append('香港市場偏弱，代表中國相關風險情緒仍需觀察。')
        if avg_asia_r20 is not None:
            asia_note += f'，20日均值 {avg_asia_r20:+.1f}%'
    else:
        asia_state = '資料不足'
        asia_note = '香港/韓國資料不足'

    if score >= 72:
        regime = '多頭延續'
        trend_state = '偏多'
    elif score <= 35:
        regime = '防守模式'
        trend_state = '防守'
    else:
        regime = '震盪整理'
        trend_state = '震盪'

    if vix_last > 30 or (tw['last'] < tw['ma60'] and us_support == 0):
        risk_state = '高'
    elif tw['pos'] > 88 and vix_last < 20:
        risk_state = '中高'
    else:
        risk_state = '中' if vix_last >= 20 else '低'

    market_score = clamp_score(score)
    uptrend = tw['last'] > tw['ma20'] and tw['last'] > tw['ma60']
    downtrend = tw['last'] < tw['ma20'] and tw['last'] < tw['ma60']
    very_high = tw['pos'] >= 93
    high = tw['pos'] >= 82
    low = tw['pos'] <= 42
    very_low = tw['pos'] <= 28
    risk_off = vix_last >= 30 or (downtrend and us_support == 0)

    if risk_off and (downtrend or tw['pos'] <= 50):
        temperature = '冷'
        temperature_key = 'cold'
        chase_state = '恐慌防守'
        advice = '市場風險升高，先保留現金；核心長期資金可慢慢分批，個股與題材股暫時保守。'
    elif very_low or ((low or market_score <= 38 or vix_last >= 23) and not uptrend):
        temperature = '偏冷'
        temperature_key = 'cool'
        chase_state = '回檔觀察'
        advice = '市場轉保守，核心 ETF 可小額分批觀察，個股先降低比例，等趨勢回穩。'
    elif very_high and vix_last < 20 and uptrend:
        temperature = '熱'
        temperature_key = 'hot'
        chase_state = '追價偏高'
        advice = '定期定額可持續，但單筆資金要分批，不要因市場樂觀就重押。'
    elif high or market_score >= 68 or (uptrend and vix_last < 18):
        temperature = '偏熱'
        temperature_key = 'warm'
        chase_state = '強勢但別追'
        advice = '趨勢仍偏多，但價格不便宜；定期定額照走，臨時單筆只適合小額分批。'
    else:
        temperature = '中性'
        temperature_key = 'neutral'
        chase_state = '可分批'
        advice = '按月扣款即可，單筆資金等價格回到可分批區再動作。'

    if regime == '多頭延續' and temperature == '熱':
        headline = '市場偏多但追價風險升高。'
    elif regime == '多頭延續' and temperature == '偏熱':
        headline = '市場偏多但價格不便宜，適合分批不適合重押。'
    elif regime == '多頭延續':
        headline = '市場仍偏多，適合紀律分批，不適合亂追。'
    elif regime == '防守模式':
        headline = '市場進入防守，先保留現金與降低題材股衝動。'
    elif temperature in ['偏冷', '冷']:
        headline = '市場轉保守，先看風險再看便宜。'
    else:
        headline = '市場震盪整理，重點是分批與等待好價格。'

    counters.append('若台股跌破季線、美股主要指數同步轉弱或 VIX 升高，偏多判斷要降級。')
    if asia_state == '區域分歧':
        counters.append('若韓國與香港同步轉弱，代表亞洲資金風險偏好下降，追價要降級。')
    elif asia_state == '同步偏弱':
        counters.append('若亞洲市場沒有回到月線上方，台股單獨強勢也要避免重押。')
    if semis_hot:
        counters.append('半導體強勢若伴隨估值過熱與放量轉弱，不能把題材熱度當成買進保證。')
    else:
        counters.append('若費半與那斯達克轉強，科技股高位可能仍是健康延續，不宜只因高就排除。')

    actions.append(advice)
    if risk_state in ['高', '中高']:
        actions.append('衛星題材與個股先降投入比例，核心 ETF 才保留定期定額。')
    else:
        actions.append('想買單筆時，優先找健康回檔或可分批區，不用猜最低點。')

    return dict(
        regime=regime,
        temperature=temperature,
        temperature_key=temperature_key,
        advice=advice,
        headline=headline,
        trend_state=trend_state,
        emotion_state=emotion_state,
        risk_state=risk_state,
        chase_state=chase_state,
        asia_state=asia_state,
        asia_note=asia_note,
        reasons=prioritize_market_reasons(reasons)[:4],
        counters=counters[:3],
        actions=actions[:3],
        score=market_score,
        position=round(tw['pos'], 1),
        vix=round(vix_last, 2),
        sox_20d=round(sox.get('r20'), 1) if sox.get('r20') is not None else None,
        nasdaq_20d=round(ixic.get('r20'), 1) if ixic.get('r20') is not None else None,
        spx_20d=round(spx.get('r20'), 1) if spx.get('r20') is not None else None,
    )


def fetch_indices():
    tickers = list(INDICES.keys())
    cards = {}
    market_ctx = dict(regime='資料不足', temperature='中性', temperature_key='neutral', advice='資料不足時先照計畫小額分批。')
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
                meta = index_quotes.get(tk) or fetch_metadata(tk)
                if len(s) < 2:
                    price = meta.get('quote_price')
                    prev = meta.get('quote_previous_close')
                    if price is not None and prev:
                        chg = (price - prev) / prev * 100
                        qtime = format_quote_time(meta)
                        source = meta.get('quote_source') or 'Quote'
                        note = f'{qtime} {source} · 無足夠日線，不納入市場判斷'.strip()
                        cards[tk] = idx_card(tk, name, price, chg, inverse, note)
                    else:
                        cards[tk] = idx_missing_card(name, 'Yahoo 暫無可用日線，先不納入市場判斷')
                    continue
                price = meta.get('quote_price') or float(s.iloc[-1])
                prev  = meta.get('quote_previous_close') or float(s.iloc[-2])
                chg   = (price - prev) / prev * 100
                qtime = format_quote_time(meta)
                source = meta.get('quote_source') or '日線資料'
                note = f'{qtime} {source}'.strip()
                cards[tk] = idx_card(tk, name, price, chg, inverse, note)
            except Exception:
                cards[tk] = idx_missing_card(name, '資料擷取失敗，先不納入市場判斷')
    except Exception as e:
        print(f'指數擷取失敗：{e}')
    return indices_radar_html(cards), market_ctx


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
    now_tw = datetime.now(ZoneInfo('Asia/Taipei'))
    update_time = now_tw.strftime('%Y-%m-%d %H:%M')
    report_phase = report_phase_info(now_tw)

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
    html = build_html(idx_html, tw_s, tw_e, us_s, us_e, bonds, update_time, market_ctx, report_phase)
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

