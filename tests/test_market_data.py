"""Parse real public-provider fixtures; network transport only is replaced."""
import copy
from concurrent.futures import ThreadPoolExecutor
import importlib
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

try:
    market = importlib.import_module("market_data")
except ModuleNotFoundError:
    market = None

FIXTURES = Path(__file__).parent / "fixtures" / "market"


def fixture(name):
    return json.loads((FIXTURES / (name + ".json")).read_text(encoding="utf-8"))


class MarketDataTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(market, "真实市场数据适配器尚未实现")
        market._CACHE.clear()
        self.replies = {name: fixture(name) for name in (
            "sina_fzb", "sina_lrb", "sina_llb", "search_600519", "search_000001",
            "search_中国平安", "search_920001")}
        self.profiles = {
            "600519": {"rc": 0, "data": {"f57": "600519", "f58": "贵州茅台", "f127": "白酒Ⅱ", "f189": 20010827}},
            "000001": {"rc": 0, "data": {"f57": "000001", "f58": "平安银行", "f127": "银行Ⅱ", "f189": 19910403}},
            "920001": {"rc": 0, "data": {"f57": "920001", "f58": "纬达光电", "f127": "光学光电子", "f189": 20221227}},
        }
        self.calls = []
        self.transport = patch.object(market, "_get_json", side_effect=self.transport_reply)
        self.transport.start()
        self.addCleanup(self.transport.stop)

    def transport_reply(self, url):
        self.calls.append(url)
        query = parse_qs(urlsplit(url).query)
        if "suggest/get" in url:
            return copy.deepcopy(self.replies["search_" + query["input"][0]])
        if "/stock/get" in url:
            result = self.profiles[query["secid"][0].split(".")[1]]
            if isinstance(result, Exception):
                raise result
            return copy.deepcopy(result)
        return copy.deepcopy(self.replies["sina_" + query["source"][0]])

    def test_code_search_excludes_same_code_index_and_fund(self):
        result = market.search_companies("000001")
        self.assertEqual([(x["code"], x["name"], x["exchange"]) for x in result],
                         [("000001", "平安银行", "SZ")])

    def test_name_search_excludes_hong_kong_and_futures(self):
        self.assertEqual([x["symbol"] for x in market.search_companies("中国平安")], ["sh601318"])

    def test_beijing_a_share_classified_neeq_is_not_discarded(self):
        self.assertEqual(market.search_companies("920001")[0]["symbol"], "bj920001")

    def test_period_union_keeps_a_missing_cashflow_period_and_filters_pre_ipo(self):
        cash = self.replies["sina_llb"]["result"]["data"]
        cash["report_date"] = [x for x in cash["report_date"] if x["date_value"] != "20240630"]
        cash["report_list"].pop("20240630")
        result = market.list_periods("600519")
        periods = {p["date"]: p for p in result["periods"]}
        self.assertNotIn("2000-12-31", periods)
        self.assertEqual(periods["2024-06-30"]["available_statements"], ["income", "balance"])
        self.assertFalse(periods["2024-06-30"]["complete"])
        self.assertEqual(periods["2025-06-30"]["type"], "halfyear")
        self.assertEqual(result["company"]["listing_date"], "2001-08-27")

    def test_quarter_type_values_match_the_public_ui_contract(self):
        # Synthetic date-only cases exercise labels, not actual financial values.
        data = self.replies["sina_lrb"]["result"]["data"]
        for value in ("20240331", "20240930"):
            data["report_date"].append({"date_value": value})
            data["report_list"][value] = {"data": [{"item_title": "测试日期占位"}]}
        result = market.list_periods("600519")
        kinds = {p["date"]: p["type"] for p in result["periods"]}
        self.assertEqual(kinds["2024-03-31"], "q1")
        self.assertEqual(kinds["2024-09-30"], "q3")

    def test_report_maps_yuan_to_millions_and_uses_exact_year_comparators(self):
        result = market.fetch_report("600519", "2025-06-30")
        data = result["payload"]; s = data["statements"]
        self.assertEqual(data["source"]["kind"], "public_provider")
        self.assertEqual(data["period"], "2025-06-30")
        self.assertEqual(s["revenue"], "89389.35441684")
        self.assertEqual(s["cost"], "7777.49108393")
        self.assertEqual(s["assets_begin"], "298944.5799187")
        self.assertEqual(s["revenue_previous"], "81930.97766775")
        self.assertEqual(s["receivables_begin"], "18.97419275")
        self.assertEqual(s["receivables_previous"], "1.35859512")
        self.assertEqual(s["cash_begin"], "169970.08925783002")
        self.assertIsNone(s["dividends_declared"])
        self.assertEqual([x["key"] for x in result["raw_statements"]], ["income", "balance", "cashflow"])
        income = result["raw_statements"][0]
        row = next(x for x in income["rows"] if x["label"] == "营业收入")
        self.assertEqual((row["value"], row["unit"]), ("89389354416.840000", "元"))
        self.assertGreater(len(income["rows"]), 30)

    def test_missing_selected_table_is_visible_and_not_replaced_with_a_nearby_period(self):
        cash = self.replies["sina_llb"]["result"]["data"]
        cash["report_date"] = [x for x in cash["report_date"] if x["date_value"] != "20250630"]
        cash["report_list"].pop("20250630")
        result = market.fetch_report("600519", "2025-06-30")
        self.assertIsNone(result["payload"]["statements"]["cfo"])
        self.assertEqual(result["raw_statements"][2]["rows"], [])
        self.assertFalse(result["raw_statements"][2]["available"])

    def test_wrong_currency_or_nonconsolidated_data_fail_instead_of_relabelling(self):
        report = self.replies["sina_lrb"]["result"]["data"]["report_list"]["20250630"]
        report["rCurrency"] = "USD"
        with self.assertRaises(market.MarketDataError):
            market.fetch_report("600519", "2025-06-30")
        market._CACHE.clear(); report["rCurrency"] = "CNY"; report["rType"] = "母公司期末"
        with self.assertRaises(market.MarketDataError):
            market.fetch_report("600519", "2025-06-30")

    def test_invalid_or_unavailable_dates_and_url_codes_never_guess_a_report(self):
        with self.assertRaises(market.MarketDataError):
            market.fetch_report("600519", "2025-03-31")
        for value in ("https://example.com", "02318.HK", "../../600519", "600519&x=1"):
            before = len(self.calls)
            with self.assertRaises(market.MarketDataError):
                market.list_periods(value)
            self.assertEqual(len(self.calls), before)

    def test_company_and_statement_requests_are_cached_between_period_and_report_views(self):
        market.list_periods("600519")
        count = len(self.calls)
        market.fetch_report("600519", "2025-06-30")
        self.assertEqual(len(self.calls), count)

    def test_concurrent_cache_eviction_does_not_mix_companies_or_raise_key_errors(self):
        def read_sequence(offset):
            for index in range(1500):
                key = "concurrent:" + str((index + offset) % 40)
                result = market._cached_value(key, lambda url: {"company": url}, 300)
                if result["company"] != key:
                    return False
            return True
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertTrue(all(pool.map(read_sequence, range(8))))

    def test_provider_error_is_not_presented_as_no_disclosures(self):
        self.replies["sina_lrb"] = {"result": {"status": {"code": 1}, "data": None}}
        with self.assertRaises(market.MarketDataError) as error:
            market.list_periods("600519")
        self.assertGreaterEqual(error.exception.status, 500)

    def test_provider_pagination_without_progress_explicitly_marks_history_truncated(self):
        self.replies["sina_fzb"]["result"]["data"]["report_count"] = "5000"
        result = market.list_periods("600519")
        self.assertTrue(result["coverage"]["truncated"])
        self.assertIn("截断", result["coverage"]["note"])

    def test_public_sina_profile_fallback_keeps_listing_date_and_avoids_concept_as_industry(self):
        self.profiles["600519"] = market.MarketDataError("暂时断连", 503)
        def get_text(url):
            name = "profile_sina.html" if "vCI_CorpInfo/" in url else "industry_sina.html"
            return (FIXTURES / name).read_text(encoding="utf-8")
        with patch.object(market, "_get_text", side_effect=get_text, create=True):
            try:
                result = market.list_periods("600519")
            except market.MarketDataError as exc:
                self.fail("东财基础资料不可用时应读取已验证公开备选：" + str(exc))
            requests = len(self.calls)
            market.fetch_report("600519", "2025-06-30")
            self.assertEqual(len(self.calls), requests, "备选公司资料也应缓存，不能每次触发失败请求")
        self.assertEqual(result["company"]["listing_date"], "2001-08-27")
        self.assertEqual(result["company"]["industry_name"], "白酒")
        self.assertEqual(result["company"]["industry"], "general")

    def test_extreme_provider_exponent_returns_a_data_error(self):
        with self.assertRaises(market.MarketDataError):
            market._million({"amount": "1e999999999"}, "amount")


@unittest.skipUnless(os.environ.get("CHAIBIAO_LIVE_TEST") == "1", "显式启用才请求公开数据源")
class LiveMarketSmokeTests(unittest.TestCase):
    def test_real_shanghai_periods_and_report(self):
        self.assertIsNotNone(market)
        periods = market.list_periods("600519")
        self.assertGreater(len(periods["periods"]), 20)
        result = market.fetch_report("600519", "2025-06-30")
        self.assertEqual(result["payload"]["company"], "贵州茅台")
        self.assertEqual(result["payload"]["source"]["kind"], "public_provider")
        self.assertEqual(len(result["raw_statements"]), 3)


if __name__ == "__main__":
    unittest.main()
