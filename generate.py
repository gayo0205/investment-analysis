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
from datetime import datetime
import warnings
import time
warnings.filterwarnings('ignore')

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
    '0056.TW':   '元大高股息',
    '00878.TW':  '國泰永續高股息',
    '00929.TW':  '復華台灣科技優息',
    '006208.TW': '富邦台50',
    '00919.TW':  '群益台灣精選高息',
    '00881.TW':  '國泰台灣5G+',
    '00940.TW':  '元大台灣價值高息',
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

TW_LIMIT_MARKETS = set(list(TW_STOCKS.keys()) + list(TW_ETFS.keys()))

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


def calc_extended(close_series, close_val, prev_close, ticker):
    c = close_series.dropna()

    period = min(252, len(c))
    w_high = float(c.tail(period).max())
    w_low  = float(c.tail(period).min())
    w_pct  = round((close_val - w_low) / (w_high - w_low) * 100, 1) if w_high > w_low else 50.0

    ma5  = float(c.rolling(5).mean().iloc[-1])  if len(c) >= 5  else None
    ma20 = float(c.rolling(20).mean().iloc[-1]) if len(c) >= 20 else None
    ma60 = float(c.rolling(60).mean().iloc[-1]) if len(c) >= 60 else None
    if ma5 and ma20 and ma60:
        if ma5 > ma20 > ma60:   ma_align = '多頭排列'
        elif ma5 < ma20 < ma60: ma_align = '空頭排列'
        else:                   ma_align = '盤整中'
    else:
        ma_align = '盤整中'

    year_str = str(datetime.now().year)
    ytd_data = c[c.index.astype(str) >= year_str]
    ytd = round((close_val - float(ytd_data.iloc[0])) / float(ytd_data.iloc[0]) * 100, 1) if len(ytd_data) >= 2 else None

    rets = c.pct_change().dropna()
    volatility = round(float(rets.tail(20).std()) * (252 ** 0.5) * 100, 1) if len(rets) >= 20 else None

    is_tw = ticker in TW_LIMIT_MARKETS
    limit_up   = round(prev_close * 1.10, 2) if is_tw and prev_close else None
    limit_down = round(prev_close * 0.90, 2) if is_tw and prev_close else None

    return {
        'w_high': round(w_high, 2), 'w_low': round(w_low, 2), 'w_pct': w_pct,
        'ma_align': ma_align,
        'ytd': ytd,
        'volatility': volatility,
        'limit_up': limit_up,
        'limit_down': limit_down,
        'prev_close': round(prev_close, 2) if prev_close else None,
        'is_tw': is_tw,
    }


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

    if ok(k) and ok(d):
        if k > d:
            score += 10
            if k < 25: score += 8; reasons.append('KD 低檔黃金交叉（強烈買訊）')
            else: reasons.append('KD 黃金交叉')
        else:
            score -= 10
            if k > 75: score -= 8; reasons.append('KD 高檔死亡交叉（注意風險）')
            else: reasons.append('KD 死亡交叉')

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
        rsi=round(rsi, 1) if ok(rsi) else None,
        macd_bull=(macd > msig) if (ok(macd) and ok(msig)) else None,
        dev20=round(dev, 1) if ok(dev) else None,
        bpct=bpct,
        vol_ratio=round(vr, 2) if ok(vr) else None,
    )


def get_recommendations(a, ext):
    score    = a['score']
    rsi      = a['rsi'] or 50
    w_pct    = ext['w_pct']
    ma_align = ext['ma_align']
    vr       = a['vol_ratio'] or 1.0

    if score >= 70 and ma_align == '多頭排列' and vr >= 1.2:
        trade = ('現在可以買進', 'buy',
                 f'多項指標偏多，趨勢向上，成交量 {vr:.1f} 倍確認。技術面訊號明確，可考慮進場。')
    elif score >= 70 and ma_align != '空頭排列':
        trade = ('可考慮買進', 'buy',
                 f'技術指標偏多，趨勢正確。成交量略不足（{vr:.1f}倍），建議小量試單，觀察量能是否跟進。')
    elif score >= 65:
        trade = ('可小量試單', 'hold',
                 '部分指標偏多但尚未完全確認。可先小量進場，等待訊號更明確後再加碼，設好停損點。')
    elif rsi > 72 or w_pct > 85:
        trade = ('短線偏熱，等回檔', 'wait',
                 f'RSI {rsi:.0f} 偏高或已接近52週高點（{w_pct:.0f}%）。追高風險大，建議等拉回整理後再進場。')
    elif score >= 45:
        trade = ('等待更好時機', 'wait',
                 '指標偏中性，尚無明確買訊。建議等待KD黃金交叉、RSI回到健康區間後再考慮進場。')
    else:
        trade = ('目前不建議買進', 'sell',
                 f'多項技術指標偏空（健康分數{score}）。建議等待落底訊號，趨勢確認轉多後再考慮進場。')

    if w_pct < 25 and ma_align in ['多頭排列', '盤整中']:
        dca = ('加碼好時機', 'buy',
               f'價格在52週相對低位（{w_pct:.0f}%），趨勢尚穩。定期定額加碼的好機會，可適度增加金額。')
    elif w_pct < 40 and ma_align == '多頭排列':
        dca = ('可略為加碼', 'hold',
               f'價格偏低（{w_pct:.0f}%）且趨勢向上，可適度增加本月扣款金額，有助降低長期持有成本。')
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

def idx_card(ticker, name, price, chg, inverse=False):
    up   = chg >= 0
    good = (not up) if inverse else up
    col  = '#1D9E75' if good else '#D85A30'
    arr  = '▲' if up else '▼'
    sign = '+' if chg >= 0 else ''
    return (f'<div class="ic"><div class="ic-l">{name}</div>'
            f'<div class="ic-v">{price:,.2f}</div>'
            f'<div class="ic-c" style="color:{col}">{arr} {sign}{chg:.2f}%</div></div>')


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

    kd_v = f"K{a['k']} / D{a['d']}" if a['k'] is not None else 'N/A'
    kd_c = '#1D9E75' if (a['k'] and a['d'] and a['k'] > a['d']) else '#D85A30'
    kd_s = ('黃金交叉' if (a['k'] and a['d'] and a['k'] > a['d']) else '死亡交叉') if a['k'] else ''

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
    week52_html = (
        f'<div class="bw" style="margin-bottom:10px">'
        f'<div class="bl-lbl">52週價格位置 <span style="color:{wd};font-weight:600">{w_pct:.0f}%</span>'
        f'<span style="font-size:10px;color:var(--t2)"> （0%=52週最低，100%=52週最高）</span></div>'
        f'<div class="bb"><div class="bd" style="left:{w_pct:.0f}%;background:{wd}"></div></div>'
        f'<div class="bt"><span style="color:#1D9E75">低點 {ext["w_low"]:,.2f}</span>'
        f'<span style="color:#D85A30">高點 {ext["w_high"]:,.2f}</span></div></div>'
    )

    price_ref_html = ''
    if ext['prev_close']:
        pr  = '<div class="pref-row">'
        pr += f'<span class="pref-item"><span class="pref-l">昨日收盤</span><span class="pref-v">{ext["prev_close"]:,.2f}</span></span>'
        if ext['limit_up']:
            pr += f'<span class="pref-item"><span class="pref-l">漲停價(+10%)</span><span class="pref-v" style="color:#1D9E75">{ext["limit_up"]:,.2f}</span></span>'
            pr += f'<span class="pref-item"><span class="pref-l">跌停價(-10%)</span><span class="pref-v" style="color:#D85A30">{ext["limit_down"]:,.2f}</span></span>'
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

    ext_html = (
        f'<div class="ig" style="margin-bottom:10px">'
        f'<div class="ic2"><div class="il">均線排列</div><div class="iv" style="color:{ma_col}">{ext["ma_align"]}</div></div>'
        f'<div class="ic2"><div class="il">成交量比</div><div class="iv" style="color:{vr_col}">{vr_str}</div>'
        f'<div class="is" style="color:{vr_col}">{vr_lbl}</div></div>'
        f'<div class="ic2"><div class="il">今年報酬</div><div class="iv" style="color:{ytd_col}">{ytd_str}</div></div>'
        f'<div class="ic2"><div class="il">波動率(年化)</div><div class="iv">{vol_str}</div>'
        f'<div class="is">{vol_lbl}</div></div>'
        f'</div>'
    )

    rl = ''.join(f'<li>{r}</li>' for r in a['reasons'])
    reasons_html = f'<ul class="rl">{rl}</ul>' if rl else ''

    trade_txt, trade_col, trade_reason = rec['trade']
    dca_txt,   dca_col,   dca_reason   = rec['dca']
    trade_tc, trade_bg = BADGE[trade_col]
    dca_tc,   dca_bg   = BADGE[dca_col]

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
        f'</div></div>'
        f'<div class="spark">{sp}</div>'
        f'{price_ref_html}'
        f'{week52_html}'
        f'<div class="sr"><span class="slbl">健康分數</span>'
        f'<div class="sbw"><div class="sbf" style="width:{sc}%;background:{sc_col}"></div></div>'
        f'<span class="sn2" style="color:{sc_col}">{sc}</span></div>'
        f'<div style="margin-bottom:10px">'
        f'<span class="sbadge" style="background:{badge_bg};color:{badge_tc}">{a["stxt"]}</span></div>'
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
        f'<hr class="cd">'
        f'{reasons_html}'
        f'<div style="font-size:10px;color:var(--t2);margin-top:6px;line-height:1.5">'
        f'以上為規則化技術指標分析，僅供參考，不構成投資建議。投資有風險，請自行判斷。</div>'
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
.spark{margin-bottom:10px}
.pref-row{display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap}
.pref-item{background:var(--card2);border-radius:8px;padding:5px 10px;display:flex;flex-direction:column;gap:2px}
.pref-l{font-size:10px;color:var(--t2)}
.pref-v{font-size:12px;font-weight:600;color:var(--t);font-variant-numeric:tabular-nums}
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


def build_html(idx_html, tw_s, tw_e, us_s, us_e, bonds, update_time):
    return (
        f'<!DOCTYPE html>\n<html lang="zh-Hant">\n<head>\n'
        f'<meta charset="UTF-8">\n'
        f'<meta name="viewport" content="width=device-width,initial-scale=1.0">\n'
        f'<title>智慧投資分析 | {update_time}</title>\n'
        f'{CSS}\n</head>\n<body>\n'
        f'<header class="hdr"><div class="wrap"><div class="hi">'
        f'<h1><span class="dot">◆</span> 智慧投資分析</h1>'
        f'<span class="ub">更新：{update_time}</span>'
        f'</div></div></header>\n'
        f'<main class="wrap">\n'
        f'<p class="stl">今日大盤指數</p>\n'
        f'<div class="igrid">{idx_html}</div>\n'
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
        f'<p>資料來源：Yahoo Finance（免費開放資料）| 分析方式：規則化技術指標（無任何 AI API）</p>\n'
        f'<p>以上分析僅供參考，不構成投資建議。投資有風險，請自行判斷。</p>\n'
        f'</div></footer>\n'
        f'{JS_CODE}\n</body>\n</html>'
    )


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
            period=period, progress=False, auto_adjust=True
        )
        if raw.empty:
            raise ValueError('空資料')
        if len(tickers) == 1:
            results[tickers[0]] = raw
        elif isinstance(raw.columns, pd.MultiIndex):
            lvl = 0 if raw.columns.get_level_values(0)[0] in tickers else 1
            for tk in tickers:
                try:
                    df = raw.xs(tk, level=lvl, axis=1)
                    if not df.empty and len(df) >= 20:
                        results[tk] = df
                except Exception:
                    pass
    except Exception as e:
        print(f'    批次失敗（{e}），改逐一擷取...')

    missing = [tk for tk in tickers if tk not in results]
    for tk in missing:
        try:
            df = yf.download(tk, period=period, progress=False, auto_adjust=True)
            if not df.empty and len(df) >= 20:
                results[tk] = df
                print(f'    {tk} ✓')
        except Exception as ex:
            print(f'    {tk} ✗ {ex}')
        time.sleep(0.4)
    return results


def process_group(tickers_dict, period='1y'):
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
            close  = df['Close'].squeeze()
            ind, _ = calc_indicators(df)
            c_vals = close.dropna()
            last_c = float(c_vals.iloc[-1])
            prev_c = float(c_vals.iloc[-2]) if len(c_vals) >= 2 else last_c
            chg    = (last_c - prev_c) / prev_c * 100
            a      = analyze(ind, last_c)
            ext    = calc_extended(close, last_c, prev_c, ticker)
            rec    = get_recommendations(a, ext)
            cards.append(stock_card(ticker, name, last_c, chg, close, a, ext, rec))
        except Exception as ex:
            print(f'    {ticker} 處理失敗：{ex}')
            cards.append(
                f'<div class="sc"><div class="nd">'
                f'{ticker.replace(".TW","")} {name}<br>處理失敗'
                f'</div></div>'
            )
    return '\n'.join(cards)


def fetch_indices():
    tickers = list(INDICES.keys())
    parts   = []
    try:
        raw = yf.download(tickers, period='5d', progress=False, auto_adjust=True)
        for tk, (name, inverse) in INDICES.items():
            try:
                if isinstance(raw.columns, pd.MultiIndex):
                    s = raw['Close'][tk].dropna()
                else:
                    s = raw['Close'].dropna()
                if len(s) < 2:
                    continue
                price = float(s.iloc[-1])
                prev  = float(s.iloc[-2])
                chg   = (price - prev) / prev * 100
                parts.append(idx_card(tk, name, price, chg, inverse))
            except Exception:
                pass
    except Exception as e:
        print(f'指數擷取失敗：{e}')
    return '\n'.join(parts)


# =============================================================
# 主程式
# =============================================================

def main():
    print('=' * 45)
    print('  智慧投資分析 v2 — 開始產生報告')
    print('=' * 45)
    update_time = datetime.now().strftime('%Y-%m-%d %H:%M')

    print('\n[1/6] 擷取大盤指數...')
    idx_html = fetch_indices()

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

    print('\n產生 index.html...')
    html = build_html(idx_html, tw_s, tw_e, us_s, us_e, bonds, update_time)
    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html)

    print(f'\n✅ 完成！（{update_time}）')
    print('=' * 45)


if __name__ == '__main__':
    main()
