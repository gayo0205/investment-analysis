import csv
import html as html_lib
import json
import re
import ssl
import time
from datetime import datetime, time as dt_time, timedelta
from io import StringIO
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


TW_TZ = ZoneInfo('Asia/Taipei')

TWSE_STOCK_DAY_ALL_URLS = [
    'https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL?response=open_data',
    'https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL',
]

FINMIND_API_V3_URL = 'https://api.finmindtrade.com/api/v3/data'
FINMIND_API_V4_URL = 'https://api.finmindtrade.com/api/v4/data'
TWSE_FUND_BASIC_URL = 'https://mopsfin.twse.com.tw/opendata/t187ap47_L.csv'
SITCA_FUND_NAV_URL = 'https://www.sitca.org.tw/MemberK0000/F/03/nav.csv'
FSC_FUND_FEE_URL = 'https://stat.fsc.gov.tw/FSC_OAS3_RESTORE/api/CSV_EXPORT?TableID=066&OUTPUT_FILE=Y'
TWSE_ETF_DIVIDEND_URL = 'https://wwwc.twse.com.tw/zh/ETFortune/dividendList'
FINMIND_PUBLIC_CACHE = {}
TW_ETF_PUBLIC_CACHE = {}
TWSE_FUND_BASIC_CACHE = None
SITCA_FUND_NAV_CACHE = None
FSC_FUND_FEE_CACHE = None
TWSE_ETF_DIVIDEND_CACHE = None
_LAST_FINMIND_REQUEST = 0.0


def _safe_num(value):
    try:
        if value is None:
            return None
        text = str(value).strip().replace(',', '')
        if text in ['', '--', '---', 'NaN', 'nan']:
            return None
        if text.startswith('X'):
            text = text[1:]
        return float(text)
    except Exception:
        return None


def _field(row, *names):
    for name in names:
        if name in row and str(row[name]).strip() != '':
            return row[name]
    return None


def _parse_twse_date(value):
    if value is None:
        return None
    text = str(value).strip()
    try:
        digits = ''.join(ch for ch in text if ch.isdigit())
        if len(digits) == 7:
            year = int(digits[:3]) + 1911
            return datetime(year, int(digits[3:5]), int(digits[5:7])).date()
        if len(digits) == 8 and int(digits[:4]) < 1911:
            year = int(digits[:3]) + 1911
            return datetime(year, int(digits[3:5]), int(digits[5:7])).date()
    except Exception:
        pass
    for fmt in ('%Y%m%d', '%Y-%m-%d', '%Y/%m/%d'):
        try:
            return datetime.strptime(text, fmt).date()
        except Exception:
            pass
    try:
        parts = text.split('/')
        if len(parts) == 3 and len(parts[0]) <= 3:
            year = int(parts[0]) + 1911
            return datetime(year, int(parts[1]), int(parts[2])).date()
    except Exception:
        pass
    return None


def _fetch_text(url, timeout=25, retries=3):
    req = Request(url, headers={'User-Agent': 'investment-analysis-public-dashboard/1.0'})
    context = ssl.create_default_context()
    if hasattr(ssl, 'VERIFY_X509_STRICT'):
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    last_error = None
    for attempt in range(retries):
        try:
            with urlopen(req, timeout=timeout, context=context) as resp:
                return resp.read().decode('utf-8-sig')
        except Exception as ex:
            last_error = ex
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
    raise last_error


def _fetch_json(url, timeout=25):
    return json.loads(_fetch_text(url, timeout=timeout))


def _finmind_wait():
    global _LAST_FINMIND_REQUEST
    elapsed = time.time() - _LAST_FINMIND_REQUEST
    if elapsed < 0.35:
        time.sleep(0.35 - elapsed)
    _LAST_FINMIND_REQUEST = time.time()


def _finmind_rows(url, params, timeout=25):
    _finmind_wait()
    payload = _fetch_json(url + '?' + urlencode(params), timeout=timeout)
    if payload.get('status') != 200:
        raise ValueError(payload.get('msg') or f'FinMind status {payload.get("status")}')
    rows = payload.get('data') or []
    return rows if isinstance(rows, list) else []


def _finmind_v3(dataset, stock_id, start_date, timeout=25):
    return _finmind_rows(
        FINMIND_API_V3_URL,
        dict(dataset=dataset, stock_id=stock_id, date=start_date),
        timeout=timeout,
    )


def _finmind_v4(dataset, data_id, start_date, timeout=25):
    return _finmind_rows(
        FINMIND_API_V4_URL,
        dict(dataset=dataset, data_id=data_id, start_date=start_date),
        timeout=timeout,
    )


def _date_key(row):
    return str(row.get('date') or '')


def _latest_row(rows):
    usable = [row for row in rows if _date_key(row)]
    if not usable:
        return None
    return sorted(usable, key=_date_key)[-1]


def _latest_rows(rows):
    latest = _latest_row(rows)
    if not latest:
        return []
    date = _date_key(latest)
    return [row for row in rows if _date_key(row) == date]


def _period_start(days):
    return (datetime.now(TW_TZ).date() - timedelta(days=days)).isoformat()


def _csv_rows(text):
    first_line = text.splitlines()[0] if text.splitlines() else ''
    delimiter = ';' if first_line.count(';') > first_line.count(',') else ','
    return list(csv.DictReader(StringIO(text), delimiter=delimiter))


def _clean_text(value):
    text = str(value or '').strip()
    return '' if text in {'--', '---', 'NaN', 'nan', 'null', 'None'} else text


def _safe_pct(value):
    val = _safe_num(value)
    if val is None:
        return None
    return val


def _yyyymm(row):
    year = int(_safe_num(row.get('年')) or 0)
    month = int(_safe_num(row.get('月')) or 0)
    return year * 100 + month if year and month else 0


def _dateish(value):
    text = _clean_text(value)
    if not text:
        return ''
    parsed = _parse_twse_date(text)
    return parsed.isoformat() if parsed else text


def _fund_code(value):
    return _clean_text(value).replace('.TW', '')


def _strip_html(value):
    text = str(value or '')
    text = re.sub(r'<script\b[^>]*>.*?</script>', ' ', text, flags=re.I | re.S)
    text = re.sub(r'<style\b[^>]*>.*?</style>', ' ', text, flags=re.I | re.S)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html_lib.unescape(text)
    return re.sub(r'\s+', ' ', text).strip()


def _parse_etf_dividend_date(value):
    text = _strip_html(value)
    if not text or text in {'-', '--', '尚未公告'}:
        return None
    parsed = _parse_twse_date(text)
    if parsed:
        return parsed
    match = re.search(r'(\d{2,4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', text)
    if not match:
        return None
    try:
        year = int(match.group(1))
        if year < 1911:
            year += 1911
        return datetime(year, int(match.group(2)), int(match.group(3))).date()
    except Exception:
        return None


def _pct_from_text(text, label):
    match = re.search(re.escape(label) + r'\D*([0-9]+(?:\.[0-9]+)?)\s*%', text)
    return _safe_num(match.group(1)) if match else None


def _dividend_date_text(value):
    return value.isoformat() if value else ''


def _summarize_etf_dividends(rows):
    today = datetime.now(TW_TZ).date()
    dated = sorted(
        [row for row in rows if row.get('ex_dividend_date')],
        key=lambda row: row['ex_dividend_date'],
    )
    paid_or_announced = [row for row in dated if row.get('amount') is not None]
    latest_candidates = [row for row in paid_or_announced if row['ex_dividend_date'] <= today]
    latest = latest_candidates[-1] if latest_candidates else (paid_or_announced[-1] if paid_or_announced else None)

    trailing_start = today - timedelta(days=365)
    trailing_rows = [
        row for row in paid_or_announced
        if trailing_start <= row['ex_dividend_date'] <= today
    ]
    trailing_amount = sum(float(row['amount']) for row in trailing_rows)
    future_rows = [row for row in dated if row['ex_dividend_date'] >= today]
    next_row = future_rows[0] if future_rows else None

    summary = {
        'official_dividend_source': 'TWSE ETF e添富：配息清單列表',
        'official_dividend_12m_amount': round(trailing_amount, 4) if trailing_rows else None,
        'official_dividend_12m_count': len(trailing_rows),
    }
    if latest:
        summary.update({
            'official_latest_dividend_amount': latest.get('amount'),
            'official_latest_ex_dividend_date': _dividend_date_text(latest.get('ex_dividend_date')),
            'official_latest_pay_date': _dividend_date_text(latest.get('pay_date')),
            'official_latest_base_date': _dividend_date_text(latest.get('base_date')),
            'official_dividend_equity_income_pct': latest.get('equity_income_pct'),
            'official_dividend_interest_income_pct': latest.get('interest_income_pct'),
            'official_dividend_equalization_pct': latest.get('income_equalization_pct'),
            'official_dividend_capital_gain_pct': latest.get('capital_gain_pct'),
            'official_dividend_other_income_pct': latest.get('other_income_pct'),
        })
    if next_row:
        summary.update({
            'official_next_dividend_amount': next_row.get('amount'),
            'official_next_ex_dividend_date': _dividend_date_text(next_row.get('ex_dividend_date')),
            'official_next_pay_date': _dividend_date_text(next_row.get('pay_date')),
        })
    return summary


def fetch_twse_etf_dividends():
    global TWSE_ETF_DIVIDEND_CACHE
    if TWSE_ETF_DIVIDEND_CACHE is not None:
        return dict(TWSE_ETF_DIVIDEND_CACHE)
    try:
        text = _fetch_text(TWSE_ETF_DIVIDEND_URL, timeout=35)
    except Exception as ex:
        print(f'    ETF配息清單略過：{ex}')
        return {}

    grouped = {}
    for row_html in re.findall(r'<tr\b[^>]*>(.*?)</tr>', text, flags=re.I | re.S):
        cells = re.findall(r'<td\b[^>]*>(.*?)</td>', row_html, flags=re.I | re.S)
        if len(cells) < 6:
            continue
        code = _fund_code(_strip_html(cells[0]))
        if not code or not re.match(r'^[0-9A-Z]{4,8}$', code):
            continue
        amount = _safe_num(_strip_html(cells[5]))
        blob = _strip_html(row_html)
        grouped.setdefault(code, []).append({
            'code': code,
            'name': _strip_html(cells[1]),
            'ex_dividend_date': _parse_etf_dividend_date(cells[2]),
            'base_date': _parse_etf_dividend_date(cells[3]),
            'pay_date': _parse_etf_dividend_date(cells[4]),
            'amount': amount,
            'equity_income_pct': _pct_from_text(blob, '股利所得占比'),
            'interest_income_pct': _pct_from_text(blob, '利息所得占比'),
            'income_equalization_pct': _pct_from_text(blob, '收益平準金占比'),
            'capital_gain_pct': _pct_from_text(blob, '已實現資本利得占比'),
            'other_income_pct': _pct_from_text(blob, '其他所得占比'),
        })

    data = {}
    for code, rows in grouped.items():
        data[code] = _summarize_etf_dividends(rows)
    TWSE_ETF_DIVIDEND_CACHE = dict(data)
    return data


def _calc_month_revenue(rows):
    rows = sorted([row for row in rows if _safe_num(row.get('revenue')) is not None], key=_date_key)
    if not rows:
        return {}

    latest = rows[-1]
    latest_rev = _safe_num(latest.get('revenue'))
    latest_year = int(_safe_num(latest.get('revenue_year')) or 0)
    latest_month = int(_safe_num(latest.get('revenue_month')) or 0)
    previous = rows[-2] if len(rows) >= 2 else None
    previous_rev = _safe_num(previous.get('revenue')) if previous else None

    year_ago = None
    for row in rows:
        row_year = int(_safe_num(row.get('revenue_year')) or 0)
        row_month = int(_safe_num(row.get('revenue_month')) or 0)
        if row_year == latest_year - 1 and row_month == latest_month:
            year_ago = row
            break
    year_ago_rev = _safe_num(year_ago.get('revenue')) if year_ago else None

    mom = (latest_rev / previous_rev - 1) if latest_rev and previous_rev and previous_rev > 0 else None
    yoy = (latest_rev / year_ago_rev - 1) if latest_rev and year_ago_rev and year_ago_rev > 0 else None

    ttm_yoy = None
    if len(rows) >= 24:
        last12 = rows[-12:]
        prev12 = rows[-24:-12]
        last_sum = sum(_safe_num(row.get('revenue')) or 0 for row in last12)
        prev_sum = sum(_safe_num(row.get('revenue')) or 0 for row in prev12)
        if last_sum > 0 and prev_sum > 0:
            ttm_yoy = last_sum / prev_sum - 1

    recent_yoys = []
    for row in rows[-3:]:
        row_rev = _safe_num(row.get('revenue'))
        row_year = int(_safe_num(row.get('revenue_year')) or 0)
        row_month = int(_safe_num(row.get('revenue_month')) or 0)
        same_month_last_year = next((
            old for old in rows
            if int(_safe_num(old.get('revenue_year')) or 0) == row_year - 1
            and int(_safe_num(old.get('revenue_month')) or 0) == row_month
        ), None)
        old_rev = _safe_num(same_month_last_year.get('revenue')) if same_month_last_year else None
        if row_rev and old_rev and old_rev > 0:
            recent_yoys.append(row_rev / old_rev - 1)

    month_label = f'{latest_year}/{latest_month:02d}' if latest_year and latest_month else _date_key(latest)
    return {
        'finmind_month_revenue': latest_rev,
        'finmind_month_revenue_mom': mom,
        'finmind_month_revenue_yoy': yoy,
        'finmind_ttm_revenue_yoy': ttm_yoy,
        'finmind_revenue_date': latest.get('date'),
        'finmind_revenue_month_label': month_label,
        'finmind_recent_revenue_yoys': recent_yoys,
        'finmind_revenue_source': 'FinMind TaiwanStockMonthRevenue',
    }


def _calc_financial_statement(rows):
    if not rows:
        return {}
    by_date = {}
    for row in rows:
        date = _date_key(row)
        typ = row.get('type')
        val = _safe_num(row.get('value'))
        if not date or not typ or val is None:
            continue
        by_date.setdefault(date, {})[typ] = val
    if not by_date:
        return {}

    dates = sorted(by_date)
    latest_date = dates[-1]
    latest = by_date[latest_date]
    eps_series = [(date, items.get('EPS')) for date, items in sorted(by_date.items()) if items.get('EPS') is not None]
    latest_eps = eps_series[-1][1] if eps_series else None
    eps_ttm = None
    eps_yoy = None
    if len(eps_series) >= 4:
        eps_ttm = sum(value for _, value in eps_series[-4:])
    if len(eps_series) >= 5:
        base = eps_series[-5][1]
        if base and base != 0 and latest_eps is not None:
            eps_yoy = latest_eps / base - 1

    gross_profit = latest.get('GrossProfit')
    revenue = (
        latest.get('OperatingRevenue')
        or latest.get('Revenue')
        or latest.get('OperatingIncome')
        or latest.get('Income')
    )
    gross_margin = gross_profit / revenue if gross_profit is not None and revenue and revenue > 0 else None

    return {
        'finmind_statement_date': latest_date,
        'finmind_quarter_eps': latest_eps,
        'finmind_eps_ttm': eps_ttm,
        'finmind_eps_yoy': eps_yoy,
        'finmind_gross_margin': gross_margin,
        'finmind_statement_source': 'FinMind TaiwanStockFinancialStatements',
    }


def _calc_institutional(rows):
    latest = _latest_rows(rows)
    if not latest:
        return {}
    foreign = trust = dealer = total = 0.0
    for row in latest:
        buy = _safe_num(row.get('buy')) or 0
        sell = _safe_num(row.get('sell')) or 0
        net = buy - sell
        name = str(row.get('name') or '')
        total += net
        if 'Foreign' in name:
            foreign += net
        elif name == 'Investment_Trust':
            trust += net
        elif name.startswith('Dealer'):
            dealer += net
    return {
        'finmind_chip_date': _date_key(latest[0]),
        'finmind_foreign_net_buy': foreign,
        'finmind_investment_trust_net_buy': trust,
        'finmind_dealer_net_buy': dealer,
        'finmind_institutional_net_buy': total,
        'finmind_chip_source': 'FinMind InstitutionalInvestorsBuySell',
    }


def fetch_finmind_public_stock_data(stock_id):
    """Fetch free, no-token FinMind data for Taiwan stocks.

    The public site treats FinMind as a helpful add-on. Any error returns a
    partial dict so the report never fabricates missing fundamentals.
    """
    stock_id = str(stock_id or '').strip()
    if not stock_id:
        return {}
    if stock_id in FINMIND_PUBLIC_CACHE:
        return dict(FINMIND_PUBLIC_CACHE[stock_id])

    result = {'finmind_available': False, 'finmind_errors': []}

    try:
        per_row = _latest_row(_finmind_v3('TaiwanStockPER', stock_id, _period_start(120)))
        if per_row:
            result.update({
                'finmind_per_date': per_row.get('date'),
                'finmind_per': _safe_num(per_row.get('PER')),
                'finmind_pbr': _safe_num(per_row.get('PBR')),
                'finmind_dividend_yield': _safe_num(per_row.get('dividend_yield')),
                'finmind_per_source': 'FinMind TaiwanStockPER',
            })
    except Exception as ex:
        result['finmind_errors'].append(f'PER/PBR略過：{ex}')

    try:
        result.update(_calc_month_revenue(_finmind_v3('TaiwanStockMonthRevenue', stock_id, _period_start(900))))
    except Exception as ex:
        result['finmind_errors'].append(f'月營收略過：{ex}')

    try:
        result.update(_calc_financial_statement(_finmind_v4('TaiwanStockFinancialStatements', stock_id, _period_start(900))))
    except Exception as ex:
        result['finmind_errors'].append(f'財報略過：{ex}')

    try:
        result.update(_calc_institutional(_finmind_v3('InstitutionalInvestorsBuySell', stock_id, _period_start(45))))
    except Exception as ex:
        result['finmind_errors'].append(f'三大法人略過：{ex}')

    result['finmind_available'] = any(
        key in result for key in (
            'finmind_per',
            'finmind_month_revenue_yoy',
            'finmind_eps_yoy',
            'finmind_institutional_net_buy',
        )
    )
    FINMIND_PUBLIC_CACHE[stock_id] = dict(result)
    return dict(result)


def fetch_twse_fund_basic():
    global TWSE_FUND_BASIC_CACHE
    if TWSE_FUND_BASIC_CACHE is not None:
        return dict(TWSE_FUND_BASIC_CACHE)
    try:
        rows = _csv_rows(_fetch_text(TWSE_FUND_BASIC_URL, timeout=30))
    except Exception as ex:
        print(f'    ETF基本資料略過：{ex}')
        return {}

    data = {}
    for row in rows:
        code = _fund_code(_field(row, '基金代號'))
        if not code:
            continue
        data[code] = {
            'official_etf_report_date': _dateish(_field(row, '出表日期')),
            'official_etf_code': code,
            'official_etf_short_name': _clean_text(_field(row, '基金簡稱')),
            'official_etf_type': _clean_text(_field(row, '基金類型')),
            'official_etf_full_name': _clean_text(_field(row, '基金中文名稱')),
            'official_etf_english_name': _clean_text(_field(row, '基金英文名稱')),
            'official_etf_index_name': _clean_text(_field(row, '標的指數/追蹤指數名稱')),
            'official_etf_custom_index': _clean_text(_field(row, '標的指數是否為客製化或需揭露相關資訊之指數')),
            'official_etf_allocation_note': _clean_text(_field(row, '股票及債券投資比例說明')),
            'official_etf_has_benchmark': _clean_text(_field(row, '是否設有績效指標')),
            'official_etf_benchmark_name': _clean_text(_field(row, '績效指標中文名稱')),
            'official_etf_foreign_components': _clean_text(_field(row, '是否包含國外成分股')),
            'official_etf_tax_id': _clean_text(_field(row, '基金統一編號')),
            'official_etf_inception_date': _dateish(_field(row, '成立日期')),
            'official_etf_listing_date': _dateish(_field(row, '上市日期')),
            'official_etf_manager': _clean_text(_field(row, '基金經理人')),
            'official_etf_agent': _clean_text(_field(row, '總代理人')),
            'official_etf_units': _safe_num(_field(row, '發行單位數/轉換數')),
            'official_etf_custodian': _clean_text(_field(row, '保管機構')),
            'official_etf_note': _clean_text(_field(row, '備註')),
            'official_etf_basic_source': '政府資料開放平臺：基金基本資料彙總表',
        }
    TWSE_FUND_BASIC_CACHE = dict(data)
    return data


def fetch_sitca_fund_nav():
    global SITCA_FUND_NAV_CACHE
    if SITCA_FUND_NAV_CACHE is not None:
        return dict(SITCA_FUND_NAV_CACHE)
    try:
        rows = _csv_rows(_fetch_text(SITCA_FUND_NAV_URL, timeout=30))
    except Exception as ex:
        print(f'    基金每日淨值略過：{ex}')
        return {}

    data = {}
    for row in rows:
        code_candidates = [
            _fund_code(_field(row, '受益憑證代號')),
            _fund_code(_field(row, '基金代號')),
        ]
        nav = _safe_num(_field(row, '基金淨值'))
        if nav is None:
            continue
        payload = {
            'official_nav_date': _dateish(_field(row, '日期')),
            'official_nav': nav,
            'official_nav_change': _safe_num(_field(row, '漲跌')),
            'official_nav_change_pct': _safe_pct(_field(row, '漲跌幅')),
            'official_nav_currency': _clean_text(_field(row, '幣別')),
            'official_nav_fund_name': _clean_text(_field(row, '基金名稱')),
            'official_nav_fund_id': _clean_text(_field(row, '基金統編')),
            'official_nav_source': '政府資料開放平臺：證券投資信託基金每日淨值',
        }
        for code in code_candidates:
            if code:
                data[code] = payload
    SITCA_FUND_NAV_CACHE = dict(data)
    return data


def fetch_fsc_fund_fee():
    global FSC_FUND_FEE_CACHE
    if FSC_FUND_FEE_CACHE is not None:
        return dict(FSC_FUND_FEE_CACHE)
    try:
        rows = _csv_rows(_fetch_text(FSC_FUND_FEE_URL, timeout=35))
    except Exception as ex:
        print(f'    境內基金費用略過：{ex}')
        return {}

    latest_by_code = {}
    latest_by_tax_id = {}
    for row in rows:
        key = _yyyymm(row)
        if key <= 0:
            continue
        payload = {
            'official_fee_year': int(_safe_num(row.get('年')) or 0),
            'official_fee_month': int(_safe_num(row.get('月')) or 0),
            'official_fee_type_code': _clean_text(_field(row, '類型代號')),
            'official_fee_tax_id': _clean_text(_field(row, '基金統編')),
            'official_fee_code': _fund_code(_field(row, '基金代號')),
            'official_fee_company': _clean_text(_field(row, '公司名稱')),
            'official_fee_fund_name': _clean_text(_field(row, '基金名稱')),
            'official_fee_management_rate': _safe_pct(_field(row, '月經理費_比率')),
            'official_fee_custody_rate': _safe_pct(_field(row, '月保管費_比率')),
            'official_fee_trading_tax_rate': _safe_pct(_field(row, '月交易稅_比率')),
            'official_fee_other_rate': _safe_pct(_field(row, '月其他費用_比率')),
            'official_fee_total_rate': _safe_pct(_field(row, '合計_比率')),
            'official_fee_announce_date': _dateish(_field(row, '公告日期')),
            'official_fee_source': '政府資料開放平臺：投信投顧公會境內基金各項費用資料',
        }
        if payload['official_fee_year'] and payload['official_fee_month']:
            today = datetime.now(TW_TZ).date()
            age_months = (today.year * 12 + today.month) - (
                payload['official_fee_year'] * 12 + payload['official_fee_month']
            )
            payload['official_fee_month_age'] = age_months
            payload['official_fee_stale'] = age_months > 4
        total_rate = payload['official_fee_total_rate']
        if total_rate is not None:
            payload['official_fee_annualized_estimate'] = total_rate * 12
        code = payload['official_fee_code']
        tax_id = payload['official_fee_tax_id']
        if code and key >= latest_by_code.get(code, (0, None))[0]:
            latest_by_code[code] = (key, payload)
        if tax_id and key >= latest_by_tax_id.get(tax_id, (0, None))[0]:
            latest_by_tax_id[tax_id] = (key, payload)

    data = {code: payload for code, (_, payload) in latest_by_code.items()}
    for tax_id, (_, payload) in latest_by_tax_id.items():
        data[tax_id] = payload
    FSC_FUND_FEE_CACHE = dict(data)
    return data


def fetch_tw_etf_public_data(stock_id):
    """Fetch free public ETF-specific data for Taiwan ETFs."""
    stock_id = _fund_code(stock_id)
    if not stock_id:
        return {}
    if stock_id in TW_ETF_PUBLIC_CACHE:
        return dict(TW_ETF_PUBLIC_CACHE[stock_id])

    result = {'official_etf_available': False, 'official_etf_errors': []}

    basic_map = fetch_twse_fund_basic()
    basic = basic_map.get(stock_id)
    if basic:
        result.update(basic)
    else:
        result['official_etf_errors'].append('ETF基本資料待補')

    nav_map = fetch_sitca_fund_nav()
    nav = nav_map.get(stock_id)
    if nav:
        result.update(nav)
    elif basic and basic.get('official_etf_tax_id') and nav_map.get(basic['official_etf_tax_id']):
        result.update(nav_map[basic['official_etf_tax_id']])
    else:
        result['official_etf_errors'].append('每日淨值待補')

    fee_map = fetch_fsc_fund_fee()
    fee = fee_map.get(stock_id)
    if not fee and basic and basic.get('official_etf_tax_id'):
        fee = fee_map.get(basic['official_etf_tax_id'])
    if fee:
        result.update(fee)
    else:
        result['official_etf_errors'].append('基金費用資料待補')

    dividend_map = fetch_twse_etf_dividends()
    dividend = dividend_map.get(stock_id)
    if dividend:
        result.update(dividend)
    else:
        result['official_etf_errors'].append('ETF配息資料待補')

    result['official_etf_available'] = any(
        key in result for key in (
            'official_etf_index_name',
            'official_nav',
            'official_fee_total_rate',
            'official_dividend_12m_amount',
        )
    )
    TW_ETF_PUBLIC_CACHE[stock_id] = dict(result)
    return dict(result)


def _normalize_twse_stock_row(row):
    code = _field(row, '證券代號', 'Code', 'code')
    if not code:
        return None
    code = str(code).strip()
    close = _safe_num(_field(row, '收盤價', 'ClosingPrice', 'closing_price'))
    open_ = _safe_num(_field(row, '開盤價', 'OpeningPrice', 'opening_price'))
    high = _safe_num(_field(row, '最高價', 'HighestPrice', 'highest_price'))
    low = _safe_num(_field(row, '最低價', 'LowestPrice', 'lowest_price'))
    change = _safe_num(_field(row, '漲跌價差', 'Change', 'change'))
    volume = _safe_num(_field(row, '成交股數', 'TradeVolume', 'trade_volume'))
    trade_value = _safe_num(_field(row, '成交金額', 'TradeValue', 'trade_value'))
    trade_count = _safe_num(_field(row, '成交筆數', 'Transaction', 'transaction'))
    date = _parse_twse_date(_field(row, '日期', 'Date', 'date'))

    if close is None:
        return None
    previous_close = close - change if change is not None else None
    quote_time = None
    if date is not None:
        quote_time = datetime.combine(date, dt_time(14, 30), TW_TZ).timestamp()

    return code, {
        'quote_price': close,
        'quote_previous_close': previous_close,
        'quote_open': open_,
        'quote_day_high': high,
        'quote_day_low': low,
        'quote_time': quote_time,
        'quote_timezone': 'Asia/Taipei',
        'market_state': 'CLOSED',
        'quote_source': 'TWSE STOCK_DAY_ALL 盤後公開資料',
        'twse_date': date.isoformat() if date else None,
        'twse_volume': volume,
        'twse_trade_value': trade_value,
        'twse_transaction': trade_count,
    }


def _parse_twse_payload(text):
    stripped = text.lstrip()
    if stripped.startswith('['):
        rows = json.loads(stripped)
    else:
        rows = list(csv.DictReader(StringIO(text)))

    quotes = {}
    for row in rows:
        normalized = _normalize_twse_stock_row(row)
        if normalized:
            code, item = normalized
            quotes[code] = item
    return quotes


def fetch_twse_stock_day_all():
    last_error = None
    for url in TWSE_STOCK_DAY_ALL_URLS:
        try:
            text = _fetch_text(url)
            data = _parse_twse_payload(text)
            if data:
                return data
        except Exception as ex:
            last_error = ex
    if last_error:
        print(f'    TWSE STOCK_DAY_ALL 略過：{last_error}')
    return {}
