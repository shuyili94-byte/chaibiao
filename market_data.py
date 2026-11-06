"""Read public A-share disclosures. Standard library; no cookies or credentials.

Endpoint/parameter reference (not an AKShare runtime dependency):
https://github.com/akfamily/akshare/blob/main/akshare/stock_fundamental/stock_finance_sina.py
https://github.com/akfamily/akshare/blob/main/akshare/stock/stock_info_em.py
Provider snapshots can contain later restatements; they are not point-in-time data.
"""
from collections import OrderedDict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import http.client
from html.parser import HTMLParser
import json
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

SEARCH_URL = "https://searchapi.eastmoney.com/api/suggest/get"
PROFILE_URL = "https://push2.eastmoney.com/api/qt/stock/get"
REPORT_URL = "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
ALLOWED_HOSTS = {"searchapi.eastmoney.com", "push2.eastmoney.com", "quotes.sina.cn", "vip.stock.finance.sina.com.cn"}
MAX_RESPONSE = 8 * 1024 * 1024
_CACHE = OrderedDict()
_CACHE_LOCK = threading.RLock()
TABLES = (("income", "利润表", "lrb"), ("balance", "资产负债表", "fzb"), ("cashflow", "现金流量表", "llb"))


class MarketDataError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status
        self.message = message


def _allowed_url(url):
    parsed = urlsplit(url)
    return parsed.scheme == "https" and parsed.hostname in ALLOWED_HOSTS and not parsed.username and not parsed.password and parsed.port in (None, 443)


class _SafeRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _allowed_url(newurl):
            raise MarketDataError("公开数据源重定向到未支持的地址。")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read_bytes(url):
    if not _allowed_url(url):
        raise MarketDataError("不支持的数据源地址。", 400)
    try:
        # No authentication, proxy rotation, browser impersonation, or anti-bot bypass.
        with build_opener(_SafeRedirects()).open(Request(url), timeout=10) as response:
            charset = response.headers.get_content_charset()
            size = response.headers.get("Content-Length")
            if size and int(size) > MAX_RESPONSE:
                raise MarketDataError("数据源响应过大，请选择其他报告期或稍后再试。")
            raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise MarketDataError("数据源响应超过 8 MB 上限。")
        return raw, charset
    except MarketDataError:
        raise
    except HTTPError as exc:
        raise MarketDataError("公开数据源暂时拒绝访问或限流，请稍后再试；未绕过访问限制。", 503) from exc
    except (URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
        raise MarketDataError("暂时无法连接公开数据源，请检查网络或稍后再试。", 503) from exc
    except (ValueError, UnicodeError) as exc:
        raise MarketDataError("公开数据源返回格式已变化，暂时无法读取。") from exc


def _get_json(url):
    raw, _ = _read_bytes(url)
    try:
        value = json.loads(raw.decode("utf-8-sig"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeError) as exc:
        raise MarketDataError("公开数据源返回格式已变化，暂时无法读取。") from exc


def _get_text(url):
    raw, charset = _read_bytes(url)
    encoding = charset.lower() if charset else "gb18030"
    if encoding not in {"gbk", "gb2312", "gb18030", "utf-8", "utf8"}:
        raise MarketDataError("公司资料页面编码不受支持。")
    try:
        return raw.decode("gb18030" if encoding in {"gbk", "gb2312"} else encoding)
    except UnicodeError as exc:
        raise MarketDataError("公司资料页面无法解码。") from exc


def _cached_value(url, loader, ttl):
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(url)
        if cached and cached[0] > now:
            _CACHE.move_to_end(url)
            return cached[1]
    value = loader(url)
    with _CACHE_LOCK:
        _CACHE[url] = (now + ttl, value, datetime.now(timezone.utc).isoformat())
        _CACHE.move_to_end(url)
        while len(_CACHE) > 24:
            _CACHE.popitem(last=False)
    return value


def _cached_json(url, ttl=1800):
    return _cached_value(url, _get_json, ttl)


class _Rows(HTMLParser):
    """Read text cells only; scripts are never executed or included."""
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell, self.title = [], None, None, ""
        self.in_title = False
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.skip += 1
        if tag == "title":
            self.in_title = True
        if tag == "tr":
            self.row = []
        if tag in {"td", "th"}:
            self.cell = []

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.skip = max(0, self.skip - 1)
        if tag == "title":
            self.in_title = False
        if tag in {"td", "th"} and self.cell is not None:
            if self.row is not None:
                self.row.append("".join(self.cell).strip())
            self.cell = None
        if tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.skip:
            return
        if self.in_title:
            self.title += data
        if self.cell is not None:
            self.cell.append(data)


def _sina_profile(company):
    code = company["code"]
    base = "https://vip.stock.finance.sina.com.cn/corp/go.php/"
    urls = [base + "vCI_CorpInfo/stockid/" + code + ".phtml",
            base + "vCI_CorpOtherInfo/stockid/" + code + "/menu_num/2.phtml"]
    parsers = []
    for url in urls:
        parser = _Rows()
        parser.feed(_cached_value(url, _get_text, 3600))
        if code not in parser.title:
            raise MarketDataError("公开公司资料页与选择的股票代码不符。")
        parsers.append(parser)
    listing, industry = None, None
    for row in parsers[0].rows:
        for index, cell in enumerate(row[:-1]):
            if cell.strip().rstrip("：:") == "上市日期":
                listing = _iso_date(row[index + 1].strip())
    for index, row in enumerate(parsers[1].rows[:-1]):
        if row and row[0].strip() == "所属行业板块" and "同行业个股" in row:
            candidate = parsers[1].rows[index + 1]
            if candidate:
                industry = candidate[0].strip()
                break
    if not listing or not industry or industry in {"--", "-", "所属概念板块"}:
        raise MarketDataError("公开基础资料缺少上市日期或行业，暂不套用普通企业解读。")
    return listing, industry, urls


def _normal_code(value):
    if not isinstance(value, str):
        raise MarketDataError("请输入沪深北 A 股的六位代码。", 400)
    text = value.strip().lower()
    match = re.fullmatch(r"(?:(sh|sz|bj))?(\d{6})(?:\.(sh|sz|bj))?", text)
    if not match:
        raise MarketDataError("只支持沪深北 A 股六位代码，例如 600519、sz000001。", 400)
    prefix, code, suffix = match.groups()
    exchange = "SH" if code.startswith("6") else "SZ" if code.startswith(("0", "3")) else "BJ" if code.startswith(("4", "8", "92")) else None
    if not exchange or (prefix and prefix.upper() != exchange) or (suffix and suffix.upper() != exchange):
        raise MarketDataError("代码与交易所不匹配，或不属于支持的 A 股范围。", 400)
    return code, exchange


def search_companies(query):
    if not isinstance(query, str) or not re.fullmatch(r"[A-Za-z0-9\u3400-\u9fff* ._-]{1,40}", query.strip()):
        raise MarketDataError("请输入 1–40 个字的 A 股公司名、拼音或股票代码。", 400)
    query = query.strip()
    if re.fullmatch(r"(?:[sS][hHzZ]|[bB][jJ])?\d{6}(?:\.[sSbB][hHzZjJ])?", query):
        query = _normal_code(query)[0]
    response = _cached_json(SEARCH_URL + "?" + urlencode({"input": query, "type": 14, "count": 10}), 300)
    table = response.get("QuotationCodeTable")
    if not isinstance(table, dict) or str(table.get("Status", "0")) != "0":
        raise MarketDataError("公司搜索数据源返回异常。")
    rows = table.get("Data") or []
    if not isinstance(rows, list):
        raise MarketDataError("公司搜索数据源格式已变化。")
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) or row.get("SecurityTypeName") not in {"沪A", "深A", "京A"}:
            continue
        try:
            code, exchange = _normal_code(str(row.get("Code", "")))
        except MarketDataError:
            continue
        name = row.get("Name")
        if not isinstance(name, str) or not name.strip() or code in seen:
            continue
        seen.add(code)
        result.append({"code": code, "name": name.strip(), "exchange": exchange, "symbol": exchange.lower() + code})
    return result


def _iso_date(value):
    text = str(value)
    if re.fullmatch(r"\d{8}", text):
        text = text[:4] + "-" + text[4:6] + "-" + text[6:]
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise MarketDataError("报告日期格式无效。", 400) from exc
    if parsed.isoformat() != text:
        raise MarketDataError("请使用 YYYY-MM-DD 日期。", 400)
    return text


def _company(code):
    code, exchange = _normal_code(code)
    cache_key = "company:" + code
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached and cached[0] > time.monotonic():
            _CACHE.move_to_end(cache_key)
            return dict(cached[1])
    candidates = [x for x in search_companies(code) if x["code"] == code and x["exchange"] == exchange]
    if len(candidates) != 1:
        raise MarketDataError("未找到唯一匹配的沪深北上市公司，请先搜索并选择候选。", 404)
    company = dict(candidates[0])
    url = PROFILE_URL + "?" + urlencode({"secid": ("1" if exchange == "SH" else "0") + "." + code,
                                         "fields": "f57,f58,f127,f189"})
    try:
        data = _cached_json(url, 3600).get("data")
        if not isinstance(data, dict) or str(data.get("f57")) != code:
            raise MarketDataError("公司基础资料暂时不可用。", 503)
        industry = data.get("f127")
        if not isinstance(industry, str) or industry.strip() in {"", "-", "--"}:
            raise MarketDataError("数据源未提供行业资料。")
        listing = _iso_date(data.get("f189", ""))
        company["profile_urls"] = [url]
    except MarketDataError:
        listing, industry, urls = _sina_profile(company)
        company["profile_urls"] = urls
    company["listing_date"] = listing
    company["industry_name"] = industry
    company["industry"] = "financial" if any(word in industry for word in ("银行", "证券", "保险", "金融", "信托")) else "general"
    with _CACHE_LOCK:
        _CACHE[cache_key] = (time.monotonic() + 3600, dict(company), datetime.now(timezone.utc).isoformat())
        _CACHE.move_to_end(cache_key)
        while len(_CACHE) > 24:
            _CACHE.popitem(last=False)
    return company


def _statement(symbol, source):
    reports, dates, urls, total = {}, set(), [], 0
    truncated = False
    for page in range(1, 5):
        url = REPORT_URL + "?" + urlencode({"paperCode": symbol, "source": source, "type": "0", "page": page, "num": "1000"})
        response = _cached_json(url)
        result = response.get("result")
        if not isinstance(result, dict) or str(result.get("status", {}).get("code", -1)) != "0":
            raise MarketDataError("新浪财报源暂时返回异常，未将其当作没有披露。")
        data = result.get("data")
        if data in (None, []):
            return {"reports": {}, "dates": set(), "urls": [url], "truncated": False}
        if not isinstance(data, dict) or not isinstance(data.get("report_list", {}), dict) or not isinstance(data.get("report_date", []), list):
            raise MarketDataError("新浪财报响应结构已变化。")
        before = len(dates)
        for item in data.get("report_date", []):
            if not isinstance(item, dict):
                continue
            try:
                value = _iso_date(item.get("date_value", ""))
            except MarketDataError:
                continue
            if value[5:] in {"03-31", "06-30", "09-30", "12-31"}:
                dates.add(value)
        for key, value in data.get("report_list", {}).items():
            try:
                normalized = _iso_date(key)
            except MarketDataError:
                continue
            if isinstance(value, dict) and isinstance(value.get("data"), list):
                reports.setdefault(normalized, value)
        urls.append(url)
        try:
            total = max(total, int(data.get("report_count", len(dates))))
        except (ValueError, TypeError):
            raise MarketDataError("新浪财报数量字段无效。")
        if len(dates) >= total:
            break
        if len(dates) == before or page == 4:
            truncated = True
            break
    return {"reports": reports, "dates": dates, "urls": urls, "truncated": truncated}


def _load(code):
    company = _company(code)
    tables = {key: _statement(company["symbol"], source) for key, _, source in TABLES}
    union = set().union(*(table["dates"] for table in tables.values()))
    periods = []
    names = {"03-31": ("一季报", "q1"), "06-30": ("半年报", "halfyear"),
             "09-30": ("三季报", "q3"), "12-31": ("年报", "annual")}
    today = datetime.now(timezone.utc).date().isoformat()
    for value in sorted(union, reverse=True):
        if value < company["listing_date"] or value > today:
            continue
        label, kind = names[value[5:]]
        available = [key for key, _, _ in TABLES if value in tables[key]["reports"] and tables[key]["reports"][value]["data"]]
        if available:
            periods.append({"date": value, "label": value[:4] + " " + label, "type": kind, "year": int(value[:4]),
                            "available_statements": available, "complete": len(available) == 3})
    truncated = any(x["truncated"] for x in tables.values())
    note = "仅列公开提供商实际返回且不早于上市日的报告期；可能有缺表、历史缺口与后续重述，不保证覆盖每一披露版本。"
    if company["exchange"] == "BJ":
        note += "北交所历史可能包含旧代码，源未提供的期间不会补造。"
    if truncated:
        note += "源分页未完整返回全部记录，历史列表已明确截断。"
    coverage = {"earliest": periods[-1]["date"] if periods else None,
                "latest": periods[0]["date"] if periods else None, "note": note, "truncated": truncated}
    return company, tables, periods, coverage


def list_periods(code):
    company, _, periods, coverage = _load(code)
    return {"company": company, "periods": periods, "coverage": coverage}


def _record(table, value):
    record = table["reports"].get(value)
    if not record:
        return None
    if record.get("rCurrency") != "CNY":
        raise MarketDataError("报告币种并非人民币 CNY，不能按人民币百万元转换。", 422)
    if not str(record.get("rType", "")).startswith("合并"):
        raise MarketDataError("本期并非已确认的合并报表，暂不套用合并口径。", 422)
    return record


def _fields(record):
    return {row.get("item_field"): row.get("item_value") for row in record.get("data", []) if isinstance(row, dict) and row.get("item_field")} if record else {}


def _million(fields, key):
    raw = fields.get(key)
    if raw in (None, "", "--", "-", "不适用"):
        return None
    try:
        number = Decimal(str(raw).replace(",", ""))
        if not number.is_finite() or number.copy_abs() > Decimal("1e24"):
            raise InvalidOperation()
        with localcontext() as context:
            context.prec = 40
            text = format(number / Decimal(1000000), "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    except InvalidOperation as exc:
        raise MarketDataError("财报中有无法安全转换的金额字段。") from exc


def fetch_report(code, report_date):
    selected = _iso_date(report_date)
    company, tables, periods, coverage = _load(code)
    if selected not in {p["date"] for p in periods}:
        raise MarketDataError("该公司没有返回所选报告期，请从实际报告期列表选择。", 404)
    year = int(selected[:4])
    opening, previous = str(year - 1) + "-12-31", str(year - 1) + selected[4:]
    records = {key: _record(tables[key], selected) for key, _, _ in TABLES}
    income, balance, cash = (_fields(records[key]) for key in ("income", "balance", "cashflow"))
    begin_balance = _fields(_record(tables["balance"], opening))
    previous_income = _fields(_record(tables["income"], previous))
    previous_balance = _fields(_record(tables["balance"], previous))
    statement_values = {}
    mappings = (
        (income, {"revenue": "BIZINCO", "cost": "BIZCOST", "net_profit": "NETPROFIT", "net_profit_parent": "PARENETP"}),
        (balance, {"assets_end": "TOTASSET", "liabilities_end": "TOTLIAB", "equity_end": "RIGHAGGR",
                   "parent_equity_end": "PARESHARRIGH", "receivables_end": "ACCORECE", "inventory_end": "INVE", "retained_end": "UNDIPROF"}),
        (begin_balance, {"assets_begin": "TOTASSET", "equity_begin": "RIGHAGGR", "parent_equity_begin": "PARESHARRIGH",
                         "receivables_begin": "ACCORECE", "inventory_begin": "INVE", "retained_begin": "UNDIPROF"}),
        (previous_income, {"revenue_previous": "BIZINCO"}),
        (previous_balance, {"receivables_previous": "ACCORECE"}),
        (cash, {"cfo": "MANANETR", "cfi": "INVNETCASHFLOW", "cff": "FINNETCFLOW", "fx_effect": "CHGEXCHGCHGS",
                "cash_begin": "INICASHBALA", "cash_end": "FINALCASHBALA", "capex_cash_outflow": "ACQUASSETCASH"}),
    )
    for fields, mapping in mappings:
        statement_values.update({target: _million(fields, source) for target, source in mapping.items()})
    for key in ("depreciation_amortization", "dividends_declared", "reserves_transfer", "retained_other_adjustments",
                "impairment", "finance_nonoperating_adjustments", "deferred_tax_adjustments", "working_capital_adjustments", "other_noncash_adjustments"):
        statement_values[key] = None
    raw_tables, metadata = [], {}
    for key, title, _ in TABLES:
        record = records[key]
        rows = []
        for item in record.get("data", []) if record else []:
            if not isinstance(item, dict) or not isinstance(item.get("item_title"), str):
                continue
            field = item.get("item_field", "")
            unit = "元/股" if field in {"BASICEPS", "DILUTEDEPS"} else "元" if field else ""
            rows.append({"label": item["item_title"], "value": item.get("item_value"), "unit": unit, "provider_field": field})
        raw_tables.append({"key": key, "title": title, "date": selected, "available": bool(rows), "rows": rows,
                           "note": "公开提供商原始行，金额单位元；每股指标另标元/股。" if rows else "本期该表无数据，未用相邻期替代。"})
        metadata[key] = {k: v for k, v in record.items() if k != "data"} if record else None
    with _CACHE_LOCK:
        snapshot_times = [entry[2] for table in tables.values() for url in table["urls"] if (entry := _CACHE.get(url))]
    fetched_at = min(snapshot_times) if snapshot_times else datetime.now(timezone.utc).isoformat()
    source = {"kind": "public_provider", "provider": "新浪财经（报表）/东方财富（公司资料）",
              "document": company["name"] + " " + selected + " 合并财务报表（公开提供商当前版本）",
              "url": tables["income"]["urls"][0], "page": "按原科目行定位，非原始PDF页码", "fetched_at": fetched_at}
    return {"payload": {"company": company["name"], "code": company["code"], "period": selected,
                        "scope": "consolidated", "industry": company["industry"], "unit": "CNY_million",
                        "source": source, "statements": statement_values},
            "raw_statements": raw_tables,
            "provenance": {"provider": source["provider"], "fetched_at": fetched_at,
                           "statement_urls": {k: v["urls"] for k, v in tables.items()}, "statement_metadata": metadata,
                           "comparators": {"opening_balance": opening, "previous_same_period": previous},
                           "income_basis": "本年年初至报告期末累计；不是单季度，也未年化。", "coverage": coverage,
                           "profile_urls": company.get("profile_urls", []),
                           "note": "提供商当前快照，历史可能重述；比较基数取独立历史期记录，未固定为所选公告中的重述比较列。元数据publish_date不保证是首次披露日期。来源真实性与原始公告仍需复核。"}}
