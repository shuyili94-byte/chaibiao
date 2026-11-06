"""Deterministic year-to-date, consolidated, non-financial explanations.

All arithmetic uses Decimal. Nothing in this module fetches data or verifies
the submitted source. JSON output numbers are rounded to six decimal places.
"""
from decimal import Decimal, InvalidOperation, localcontext
import re
from urllib.parse import urlsplit


FIELD_LABELS = {
    "revenue": "营业收入", "cost": "营业成本", "net_profit": "合并净利润",
    "net_profit_parent": "归属于母公司股东的净利润", "assets_begin": "本年初资产总额（上年12月31日）",
    "assets_end": "期末资产总额", "liabilities_end": "期末负债总额",
    "equity_begin": "本年初所有者权益合计（上年12月31日）", "equity_end": "期末所有者权益合计",
    "parent_equity_begin": "本年初归属于母公司股东的权益（上年12月31日）",
    "parent_equity_end": "期末归属于母公司股东的权益",
    "cfo": "经营活动现金流量净额", "cfi": "投资活动现金流量净额",
    "cff": "筹资活动现金流量净额", "fx_effect": "汇率变动对现金及现金等价物的影响额",
    "cash_begin": "本年初现金及现金等价物余额", "cash_end": "期末现金及现金等价物余额",
    "capex_cash_outflow": "购建固定资产、无形资产和其他长期资产支付的现金（正数）",
    "revenue_previous": "上年同期累计营业收入", "receivables_begin": "本年初应收账款账面价值",
    "receivables_previous": "上年同一报告日应收账款账面价值",
    "receivables_end": "期末应收账款账面价值", "inventory_begin": "本年初存货账面价值",
    "inventory_end": "期末存货账面价值", "depreciation_amortization": "现金流附注折旧摊销调节额",
    "retained_begin": "本年初归母未分配利润（按同口径重述）",
    "retained_end": "期末归母未分配利润", "dividends_declared": "本期减少未分配利润的分红（非现金支付额）",
    "reserves_transfer": "本期提取盈余公积等减少未分配利润的金额",
    "retained_other_adjustments": "其他直接计入归母未分配利润的调节净额（增正减负）",
    "impairment": "现金流附注减值损失调节额",
    "finance_nonoperating_adjustments": "现金流附注财务及投资等非经营损益调节净额",
    "deferred_tax_adjustments": "现金流附注递延所得税调节净额",
    "working_capital_adjustments": "现金流附注存货及经营性应收应付等营运资金调节净额",
    "other_noncash_adjustments": "现金流附注其余非现金及其他调节净额（与上述项目不重复）",
}
NONNEGATIVE_FIELDS = {
    "revenue", "cost", "assets_begin", "assets_end", "liabilities_end", "cash_begin", "cash_end",
    "capex_cash_outflow", "revenue_previous", "receivables_begin", "receivables_end", "receivables_previous",
    "inventory_begin", "inventory_end", "dividends_declared", "reserves_transfer",
}
DISCLAIMER = "本报告用于财务数据整理与会计概念解释，不构成投资建议、估值、审计或数据真实性鉴证。"


def _text(value, label, maximum=300, allow_empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not allow_empty and not value.strip()):
        raise ValueError(f"{label}必须为{'可为空的' if allow_empty else '非空'}文本，长度不超过{maximum}字")
    return value.strip()


def validate(payload):
    """Return a new normalized payload; never mutate the caller's object."""
    if not isinstance(payload, dict):
        raise ValueError("请求主体必须是 JSON 对象")
    allowed = {"company", "code", "period", "scope", "industry", "unit", "source", "statements"}
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError("未知顶层字段：" + ", ".join(sorted(map(str, unknown))))
    company = _text(payload.get("company"), "company", 200)
    code = _text(payload.get("code", ""), "code", 50, True)
    period = payload.get("period")
    if not isinstance(period, str) or not re.fullmatch(r"(?:19|20)\d{2}(?:-(?:03-31|06-30|09-30|12-31))?", period):
        raise ValueError("period须为年度YYYY，或标准报告日YYYY-03-31/06-30/09-30/12-31；利润与现金流按本年累计")
    if payload.get("scope") != "consolidated":
        raise ValueError("scope暂只支持consolidated（合并报表）")
    if payload.get("unit") != "CNY_million":
        raise ValueError("unit暂只支持CNY_million（人民币百万元），请先统一单位")
    if payload.get("industry", "general") != "general":
        raise ValueError("industry暂只支持general（普通非金融企业）")
    source = payload.get("source")
    if not isinstance(source, dict) or source.get("kind") not in ("demo", "manual", "public_provider"):
        raise ValueError("source.kind必须为demo、manual或public_provider")
    if set(source) - {"kind", "document", "url", "page", "provider", "fetched_at"}:
        raise ValueError("source含未知字段")
    document = _text(source.get("document"), "source.document", 500)
    url = _text(source.get("url", ""), "source.url", 2000, True)
    page = _text(source.get("page", ""), "source.page", 300, True)
    normalized_source = {"kind": source["kind"], "document": document, "url": url, "page": page}
    for field, maximum in (("provider", 200), ("fetched_at", 80)):
        if field in source:
            normalized_source[field] = _text(source[field], "source." + field, maximum, True)
    if url:
        try:
            parsed = urlsplit(url)
            valid_url = parsed.scheme in ("http", "https") and bool(parsed.hostname)
        except ValueError:
            valid_url = False
        if not valid_url:
            raise ValueError("source.url必须为空或有效HTTP(S)来源链接")
    statements = payload.get("statements")
    if not isinstance(statements, dict):
        raise ValueError("statements必须为对象")
    if set(statements) - FIELD_LABELS.keys():
        raise ValueError("未知财务字段：" + ", ".join(sorted(set(statements) - FIELD_LABELS.keys())))
    normalized = {}
    for key in FIELD_LABELS:
        value = statements.get(key)
        if value is None:
            normalized[key] = None
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
            raise ValueError(f"{key}必须为有限数值或null，不能为布尔值")
        if len(str(value)) > 80:
            raise ValueError(f"{key}数值过长")
        try:
            number = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError(f"{key}不是有效数值") from exc
        if not number.is_finite() or number.copy_abs() > Decimal("1e18"):
            raise ValueError(f"{key}须为绝对值不超过1e18的有限数值")
        if number.as_tuple().exponent < -12:
            raise ValueError(f"{key}最多支持12位小数")
        if key in NONNEGATIVE_FIELDS and number < 0:
            raise ValueError(f"{key}必须为非负数；现金支出项目请填正数")
        normalized[key] = number
    return {"company": company, "code": code, "period": period, "scope": "consolidated",
            "industry": "general", "unit": "CNY_million", "statements": normalized,
            "source": normalized_source}


def _number(value):
    return None if value is None else float(value.quantize(Decimal("0.000001")))


def analyze(payload):
    data = validate(payload)
    with localcontext() as context:
        context.prec = 60
        return _analyze(data)


def _analyze(data):
    s = data["statements"]
    year = data["period"][:4]
    period_end = data["period"] if len(data["period"]) == 10 else year + "-12-31"
    endpoint = period_end[5:]
    is_annual = endpoint == "12-31"
    report_name, months = {"03-31": ("一季报", 3), "06-30": ("半年报", 6),
                           "09-30": ("三季报", 9), "12-31": ("年报", 12)}[endpoint]
    report_label = f"{year}年报" if is_annual else f"{year}年{report_name}"
    period_label = f"{report_label}（1—{months}月累计）"
    unannualized = "" if is_annual else "，本期未年化"
    warnings, metrics, checks = [], [], []
    values = {}

    def has(*keys):
        return all(s[k] is not None for k in keys)

    def average(begin, end):
        if not has(begin, end) or s[begin] <= 0 or s[end] <= 0:
            return None
        return (s[begin] + s[end]) / 2

    def ratio(numerator, denominator, percent=False):
        if numerator is None or denominator is None or denominator <= 0:
            return None
        return numerator / denominator * (100 if percent else 1)

    def add(key, label, value, unit, formula, fields, explanation):
        missing = [FIELD_LABELS[k] for k in fields if s[k] is None]
        if value is None:
            why = "缺少：" + "、".join(missing) if missing else "分母非正或期初/期末基数不适用"
            warnings.append(f"{label}未计算：{why}。")
        values[key] = value
        metrics.append({"key": key, "label": label, "value": _number(value), "unit": unit,
                        "formula": formula, "inputs": {k: _number(s[k]) for k in fields},
                        "explanation": explanation, "status": "computed" if value is not None else "insufficient"})

    def compare(key, label, expected, actual, explanation, unit="CNY_million"):
        difference = None if expected is None or actual is None else actual - expected
        status = "insufficient" if difference is None else ("pass" if abs(difference) <= Decimal("0.000001") else "review")
        checks.append({"key": key, "label": label, "status": status, "expected": _number(expected),
                       "actual": _number(actual), "difference": _number(difference), "unit": unit,
                       "explanation": explanation})

    avg_assets = average("assets_begin", "assets_end")
    avg_equity = average("equity_begin", "equity_end")
    avg_parent_equity = average("parent_equity_begin", "parent_equity_end")
    gross_profit = s["revenue"] - s["cost"] if has("revenue", "cost") else None
    add("gross_margin", "毛利率", ratio(gross_profit, s["revenue"], True), "%",
        "(revenue - cost) / revenue × 100", ["revenue", "cost"],
        "每100元营业收入扣除营业成本后形成的毛利；折旧可能已包含在成本中，不能再重复扣除。")
    add("net_margin", "净利率（合并）", ratio(s["net_profit"], s["revenue"], True), "%",
        "net_profit / revenue × 100", ["net_profit", "revenue"],
        "每100元营业收入对应的合并账面净利润。净利润按权责发生制确认，与现金净流入有区别。")
    add("parent_net_margin", "归母净利润占收入", ratio(s["net_profit_parent"], s["revenue"], True), "%",
        "net_profit_parent / revenue × 100", ["net_profit_parent", "revenue"],
        "归母净利润与合并营业收入的比值，须与全部净利润口径分别呈现。")
    add("roe_simple", f"ROE（归母，双期简单平均{unannualized}）", ratio(s["net_profit_parent"], avg_parent_equity, True), "%",
        "net_profit_parent / ((parent_equity_begin + parent_equity_end) / 2) × 100",
        ["net_profit_parent", "parent_equity_begin", "parent_equity_end"],
        "本年累计账面盈利相对本年初与本期末平均归母权益的比例，不机械年化；不等于股票收益或现金分红，也不等于监管披露的加权平均ROE。")
    add("roe_total_simple", f"ROE（总权益，双期简单平均{unannualized}）", ratio(s["net_profit"], avg_equity, True), "%",
        "net_profit / ((equity_begin + equity_end) / 2) × 100", ["net_profit", "equity_begin", "equity_end"],
        "合并净利润与平均所有者权益总额口径一致，供总口径杜邦分解使用。")
    add("asset_turnover", f"总资产周转率（双期平均{unannualized}）", ratio(s["revenue"], avg_assets), "times",
        "revenue / ((assets_begin + assets_end) / 2)", ["revenue", "assets_begin", "assets_end"],
        "本年累计收入相对本年初与本期末平均总资产的倍数，不机械年化；宜与同一行业及相同累计期间比较。")
    add("equity_multiplier", "权益乘数（总权益口径）", ratio(avg_assets, avg_equity), "times",
        "((assets_begin + assets_end) / 2) / ((equity_begin + equity_end) / 2)",
        ["assets_begin", "assets_end", "equity_begin", "equity_end"],
        "平均总资产与平均总权益之比，包含经营性负债的影响，不能直接解释为借款倍数。")
    add("cfo_to_profit", "经营现金流净额 / 净利润", ratio(s["cfo"], s["net_profit"]), "times",
        "cfo / net_profit", ["cfo", "net_profit"],
        "现金流与净利润均采用本年初至报告日累计数，仅在净利润为正时列示；大于1不能据此认定利润已全部收现。")
    fcf = s["cfo"] - s["capex_cash_outflow"] if has("cfo", "capex_cash_outflow") else None
    add("free_cash_flow", "简化自由现金流", fcf, "CNY_million", "cfo - capex_cash_outflow",
        ["cfo", "capex_cash_outflow"], "本页自定义为经营现金流净额减购建长期资产现金支出；不代表全部可分配现金。")
    add("debt_ratio", "资产负债率", ratio(s["liabilities_end"], s["assets_end"], True), "%",
        "liabilities_end / assets_end × 100", ["liabilities_end", "assets_end"],
        "总负债包括应付账款、应交税费等经营性负债，不应全部称为借来的钱。")
    ar_change = s["receivables_end"] - s["receivables_begin"] if has("receivables_end", "receivables_begin") else None
    ar_prior_field = "receivables_begin" if is_annual and s["receivables_previous"] is None else "receivables_previous"
    ar_yoy_change = s["receivables_end"] - s[ar_prior_field] if has("receivables_end", ar_prior_field) else None
    revenue_change = s["revenue"] - s["revenue_previous"] if has("revenue", "revenue_previous") else None
    add("receivables_growth", "应收账款同比变动", ratio(ar_yoy_change, s[ar_prior_field], True), "%",
        f"(receivables_end - {ar_prior_field}) / {ar_prior_field} × 100", [ar_prior_field, "receivables_end"],
        "本期报告日余额与上年同一报告日余额比较。年报可使用本年初即上年末余额；中期不能把较年初变化称为同比。本身不能证明信用政策发生改变。")
    add("receivables_change_ytd", "应收账款较年初变动", ratio(ar_change, s["receivables_begin"], True), "%",
        "(receivables_end - receivables_begin) / receivables_begin × 100", ["receivables_begin", "receivables_end"],
        "本期报告日应收账款相对本年初（上年12月31日）的变化，中期不称为同比。")
    add("revenue_growth", "营业收入同比变动", ratio(revenue_change, s["revenue_previous"], True), "%",
        "(revenue - revenue_previous) / revenue_previous × 100", ["revenue", "revenue_previous"],
        "本年初至报告日累计营业收入与上年相同起止期间比较，须注意重述或合并范围变化。")
    add("receivables_to_revenue", "期末应收 / 本期累计收入", ratio(s["receivables_end"], s["revenue"], True), "%",
        "receivables_end / revenue × 100", ["receivables_end", "revenue"],
        "时点存量除以本年初至报告日累计流量，不能解释为赊销比例或未收回收入比例；不宜跨不同期间长度直接比较。")
    add("capex_to_revenue", "购建长期资产现金支出 / 收入", ratio(s["capex_cash_outflow"], s["revenue"], True), "%",
        "capex_cash_outflow / revenue × 100", ["capex_cash_outflow", "revenue"],
        "当期购建长期资产支付的现金与营业收入之比；与折旧摊销的会计费用口径不同。")

    expected_balance = s["liabilities_end"] + s["equity_end"] if has("liabilities_end", "equity_end") else None
    compare("balance_equation", "期末资产 = 负债 + 所有者权益", expected_balance, s["assets_end"],
            "核对同一合并范围、同一时点的总额；通过只代表输入数字在此关系上相符。")
    cash_expected = sum(s[k] for k in ("cfo", "cfi", "cff", "fx_effect")) if has("cfo", "cfi", "cff", "fx_effect") else None
    cash_actual = s["cash_end"] - s["cash_begin"] if has("cash_end", "cash_begin") else None
    compare("cash_reconciliation", "现金及现金等价物净变动核对", cash_expected, cash_actual,
            "预期为经营、投资、筹资净额加汇率影响；实际为现金及现金等价物期末减期初，不能直接替换成货币资金。缺汇率影响时不假定为零。")
    factors = (values["net_margin"], values["asset_turnover"], values["equity_multiplier"])
    dupont = factors[0] * factors[1] * factors[2] if all(x is not None for x in factors) else None
    compare("dupont", "杜邦三因子计算自检（总权益口径）", values["roe_total_simple"], dupont,
            "净利率×总资产周转率×权益乘数，应复原总权益ROE。这是同一输入的代数自检，不能独立证明财报真实，也不能据此判断某因子主导。", "%")
    retained_fields = ("retained_begin", "net_profit_parent", "dividends_declared", "reserves_transfer", "retained_other_adjustments")
    retained_expected = (s["retained_begin"] + s["net_profit_parent"] - s["dividends_declared"] -
                         s["reserves_transfer"] + s["retained_other_adjustments"]) if has(*retained_fields) else None
    compare("retained_earnings", "归母未分配利润变动调节", retained_expected, s["retained_end"],
            "期初归母未分配利润加归母净利，减分红及盈余公积提取，再加其他直接调节。须与权益变动表一致；其他综合收益通常单列，不能把差额直接归因于它。")
    bridge_fields = ("net_profit", "depreciation_amortization", "impairment", "finance_nonoperating_adjustments",
                     "deferred_tax_adjustments", "working_capital_adjustments", "other_noncash_adjustments")
    bridge_expected = sum(s[k] for k in bridge_fields) if has(*bridge_fields) else None
    compare("profit_cash_bridge", "净利润到经营现金流调节", bridge_expected, s["cfo"],
            "须录入现金流附注完整、互不重复的调节项，并按其方向填数；仅比较净利与折旧不能完成核对。")

    summary = []
    for key in ("gross_margin", "net_margin"):
        if values[key] is not None:
            name = "毛利" if key == "gross_margin" else "合并账面净利润"
            summary.append(f"每100元营业收入对应{name}{_number(values[key]):.2f}元。")
    if not summary:
        summary.append("当前缺少计算基础数据；请从同一报告期、同一合并范围的报表补充字段。")
    summary.append("净利润与现金流分别反映权责发生制成果和现金收支，不能互相替代。")
    ar_paragraphs = ["应收账款与收入的增长差异只能提示后续核查，不能据此认定公司放宽信用政策。应继续查阅账龄、坏账准备、客户结算条款、季节性及合并范围变化。"]
    if values["receivables_growth"] is not None and values["revenue_growth"] is not None:
        ar_paragraphs.insert(0, f"应收账款账面价值同比变动{_number(values['receivables_growth']):.2f}%，营业收入同比变动{_number(values['revenue_growth']):.2f}%。")
    if not is_annual and values["receivables_change_ytd"] is not None:
        ar_paragraphs.insert(0, f"应收账款较本年初变动{_number(values['receivables_change_ytd']):.2f}%；此变化不等于上年同期同比变化。")
    if data["source"]["kind"] == "demo":
        warnings.insert(0, "全部数字为虚构演示数据，不对应真实上市公司，不可用作真实公司分析。")
    elif data["source"]["kind"] == "public_provider":
        provider_name = data["source"].get("provider") or "未注明服务商"
        warnings.insert(0, f"来源标记为公开数据服务商（{provider_name}）；计算内核未逐项核验原始披露及数据真实性，结果不属于鉴证结论。")
    else:
        warnings.insert(0, "数据由用户录入；程序未联网核验来源、页码、披露版本或数值真实性。")
    warnings.append("本计算内核仅支持普通非金融企业合并报表；利润和现金流均为本年初至报告日累计数，不按行业阈值作优劣判断。")
    warnings.append("期初资产、权益及现金余额须取上年12月31日；上年同期收入和应收比较值须对应相同累计期间或相同报告日。")
    if not is_annual:
        warnings.append("中期ROE和总资产周转率使用本期累计数，未年化；不能直接与全年指标比较。")
    review_count = sum(c["status"] == "review" for c in checks)
    insufficient_count = sum(c["status"] == "insufficient" for c in checks)
    return {
        "meta": {"company": data["company"], "code": data["code"], "period": data["period"],
                 "period_label": period_label, "income_basis": "year_to_date", "period_start": year + "-01-01",
                 "period_end": period_end, "is_annual": is_annual,
                 "scope": "consolidated", "unit": "CNY_million",
                 "industry": "general", "is_demo": data["source"]["kind"] == "demo", "version": "mvp-1.1",
                 "precision": "计算使用Decimal；展示保留最多6位小数；金额核对容差为0.000001百万元（1元）"},
        "metrics": metrics, "checks": checks,
        "sections": [
            {"title": "先看报表数字", "paragraphs": summary},
            {"title": "利润表｜一年收入与账面成果" if is_annual else "利润表｜本年累计收入与账面成果",
             "paragraphs": [f"期间为{year}年1月1日至{period_end}，利润表金额采用累计口径。以下展示本次已录入的部分利润表科目，以及按收入减营业成本计算的毛利；缺项显示空值。", "净利润是账面成果。当前未录入完整成本费用、其他损益和所得税等项目，不能搭成完整的收入到净利润金额桥，也不能把净利润当作收到的现金。"],
             "rows": [
                 {"label": FIELD_LABELS["revenue"], "value": _number(s["revenue"]), "unit": "CNY_million"},
                 {"label": FIELD_LABELS["cost"], "value": _number(s["cost"]), "unit": "CNY_million"},
                 {"label": "毛利（营业收入减营业成本）", "value": _number(gross_profit), "unit": "CNY_million"},
                 {"label": FIELD_LABELS["net_profit"], "value": _number(s["net_profit"]), "unit": "CNY_million"},
                 {"label": FIELD_LABELS["net_profit_parent"], "value": _number(s["net_profit_parent"]), "unit": "CNY_million"},
             ]},
            {"title": "资产负债表｜期末家底与义务",
             "paragraphs": [f"以下展示截至{period_end}的资产、负债和权益部分汇总项目；缺项显示空值。它们是同一时点的余额，不能当作期间现金流量。", "所有者权益合计包含归属于母公司股东的权益与少数股东权益，计算归母ROE时只使用归母权益。负债也包含应付货款、税费等经营性义务。"],
             "rows": [
                 {"label": FIELD_LABELS[key], "value": _number(s[key]), "unit": "CNY_million"}
                 for key in ("assets_end", "liabilities_end", "equity_end", "parent_equity_end")
             ]},
            {"title": "现金流量表｜现金如何变动",
             "paragraphs": [f"现金流量采用{year}年1月1日至{period_end}累计口径，与利润表同期。以下展示三类现金流量净额、汇率影响及现金及现金等价物余额等部分项目；缺项显示空值。净额同时反映流入和流出，不能当作收款总额。", "本年初及本期末余额必须采用现金及现金等价物口径。购建长期资产现金支出以正数单列，通常已计入投资活动现金流，核对现金净变动时不再扣一次。"],
             "rows": [
                 {"label": FIELD_LABELS[key], "value": _number(s[key]), "unit": "CNY_million"}
                 for key in ("cfo", "cfi", "cff", "fx_effect", "cash_begin", "cash_end", "capex_cash_outflow")
             ]},
            {"title": "利润与现金", "paragraphs": ["经营现金流净额既包含现金收入也包含现金支出，不能写成实际收到的全部现金。", "经营现金流与净利润之比可能受折旧、存货、应收应付、预收及税费时点等影响；大于1不等于当期利润全部变现。", "简化自由现金流是本页定义的辅助口径，仍需结合到期债务、受限资金、未来承诺等理解。"]},
            {"title": "应收变化线索", "paragraphs": ar_paragraphs},
            {"title": "ROE与杜邦口径", "paragraphs": ["归母ROE使用归母净利润及归母权益，总权益ROE使用合并净利润及所有者权益合计。两者不混用。", "本页ROE采用上年12月31日与本期末权益的简单平均，利润采用本年累计且不机械年化，区别于证监会规则下考虑增减资及分红时点等因素的加权平均ROE；不表示股票投资回报。", "杜邦分解在总口径下做代数自检。只凭某一期三个数字的大小，不能认定利润率、周转或杠杆是主导因子。"]},
            {"title": "核对结果及下一步", "paragraphs": [f"本次列示{len(checks)}项核对，其中{review_count}项需复核，{insufficient_count}项数据不足。通过不等于审计认可，不生成财报真实性评分。", "差额统一为实际减预期；资料不足时保留空值并提示补充，不把缺项默认为0。"]},
        ], "warnings": warnings, "source": dict(data["source"]), "disclaimer": DISCLAIMER,
    }
