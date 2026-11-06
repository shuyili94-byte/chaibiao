"""Behavior tests: hand-derived expected values, no network or mocks."""
import copy
import json
from pathlib import Path
import unittest

from core import analyze


def fixture():
    return {
        "company": "虚构演示公司", "code": "DEMO", "period": "2025",
        "scope": "consolidated", "unit": "CNY_million",
        "source": {"kind": "demo", "document": "虚构测试数据", "url": "", "page": "不适用"},
        "statements": {
            "revenue": "100", "cost": "40", "net_profit": "20", "net_profit_parent": "18",
            "assets_begin": "180", "assets_end": "220", "liabilities_end": "100",
            "equity_begin": "80", "equity_end": "120",
            "parent_equity_begin": "60", "parent_equity_end": "90",
            "cfo": "30", "cfi": "-10", "cff": "-5", "fx_effect": "2",
            "cash_begin": "10", "cash_end": "27", "capex_cash_outflow": "8",
            "revenue_previous": "80", "receivables_begin": "10", "receivables_end": "15"
        }
    }


def metric(report, key):
    return next(x for x in report["metrics"] if x["key"] == key)


def check(report, key):
    return next(x for x in report["checks"] if x["key"] == key)


class FinancialBehaviorTests(unittest.TestCase):
    def test_standard_report_endpoints_are_cumulative_and_never_annualized(self):
        for endpoint, report_name in (("03-31", "一季报"), ("06-30", "半年报"),
                                      ("09-30", "三季报"), ("12-31", "年报")):
            with self.subTest(endpoint=endpoint):
                p = fixture()
                p["period"] = "2025-" + endpoint
                r = analyze(p)
                self.assertIn(report_name, r["meta"]["period_label"])
                self.assertEqual(r["meta"]["income_basis"], "year_to_date")
                self.assertEqual(r["meta"]["period_start"], "2025-01-01")
                self.assertEqual(r["meta"]["period_end"], "2025-" + endpoint)
                self.assertEqual(metric(r, "roe_simple")["value"], 24)
                self.assertEqual(metric(r, "asset_turnover")["value"], .5)
                if endpoint != "12-31":
                    self.assertIn("未年化", metric(r, "roe_simple")["label"])
                    self.assertIn("未年化", metric(r, "asset_turnover")["label"])
                    self.assertNotIn("一年", " ".join(s["title"] for s in r["sections"]))

    def test_interim_receivable_growth_needs_same_day_last_year(self):
        p = fixture()
        p["period"] = "2025-06-30"
        r = analyze(p)
        self.assertIsNone(metric(r, "receivables_growth")["value"])
        self.assertEqual(metric(r, "receivables_change_ytd")["value"], 50)
        p["statements"]["receivables_previous"] = "12"
        r = analyze(p)
        self.assertEqual(metric(r, "receivables_growth")["value"], 25)
        self.assertEqual(metric(r, "receivables_change_ytd")["value"], 50)
        self.assertEqual(metric(r, "revenue_growth")["value"], 25)

    def test_annual_receivable_growth_accepts_explicit_prior_year_end(self):
        p = fixture()
        p["statements"]["receivables_previous"] = "12"
        self.assertEqual(metric(analyze(p), "receivables_growth")["value"], 25)

    def test_public_provider_provenance_is_preserved_without_claiming_verification(self):
        p = fixture()
        p["source"].update(kind="public_provider", provider="公开测试服务", fetched_at="2026-10-22T12:00:00Z")
        r = analyze(p)
        self.assertFalse(r["meta"]["is_demo"])
        self.assertEqual(r["source"]["provider"], "公开测试服务")
        self.assertEqual(r["source"]["fetched_at"], "2026-10-22T12:00:00Z")
        self.assertNotIn("用户录入", r["warnings"][0])

    def test_nonstandard_reporting_dates_remain_rejected(self):
        for period in ("2025-02-28", "2025-06-31", "2025-09-01", "2025Q1", "2025-6-30"):
            p = fixture()
            p["period"] = period
            with self.subTest(period=period), self.assertRaises(ValueError):
                analyze(p)

    def test_income_statement_rows_show_book_profit_and_derive_gross_profit_once(self):
        r = analyze(fixture())
        sections = [s for s in r["sections"] if s["title"] == "利润表｜一年收入与账面成果"]
        self.assertEqual(len(sections), 1)
        self.assertEqual([row["value"] for row in sections[0]["rows"]], [100, 40, 60, 20, 18])
        self.assertTrue(all(row["unit"] == "CNY_million" for row in sections[0]["rows"]))

    def test_balance_statement_rows_do_not_substitute_total_for_parent_equity(self):
        r = analyze(fixture())
        sections = [s for s in r["sections"] if s["title"] == "资产负债表｜期末家底与义务"]
        self.assertEqual(len(sections), 1)
        self.assertEqual([row["value"] for row in sections[0]["rows"]], [220, 100, 120, 90])

    def test_cash_statement_rows_keep_signs_fx_and_positive_capex(self):
        r = analyze(fixture())
        sections = [s for s in r["sections"] if s["title"] == "现金流量表｜现金如何变动"]
        self.assertEqual(len(sections), 1)
        self.assertEqual([row["value"] for row in sections[0]["rows"]], [30, -10, -5, 2, 10, 27, 8])

    def test_statement_rows_preserve_missing_amounts_as_null(self):
        p = fixture()
        p["statements"] = {"revenue": "100"}
        tables = [s for s in analyze(p)["sections"] if "rows" in s]
        self.assertEqual(len(tables), 3)
        amounts = [row["value"] for s in tables for row in s["rows"]]
        self.assertEqual(amounts[0], 100)
        self.assertTrue(all(value is None for value in amounts[1:]))

    def test_margins_use_revenue_and_do_not_deduct_depreciation_twice(self):
        p = fixture()
        p["statements"]["depreciation_amortization"] = "9"
        r = analyze(p)
        self.assertEqual(metric(r, "gross_margin")["value"], 60)
        self.assertEqual(metric(r, "net_margin")["value"], 20)

    def test_parent_roe_never_uses_total_equity(self):
        r = analyze(fixture())
        self.assertEqual(metric(r, "roe_simple")["value"], 24)
        self.assertEqual(metric(r, "roe_total_simple")["value"], 20)

    def test_missing_parent_equity_does_not_fall_back_to_total_equity(self):
        p = fixture()
        del p["statements"]["parent_equity_begin"]
        r = analyze(p)
        self.assertIsNone(metric(r, "roe_simple")["value"])
        self.assertEqual(metric(r, "roe_simple")["status"], "insufficient")
        self.assertTrue(r["warnings"])

    def test_total_dupont_reconstructs_total_roe(self):
        r = analyze(fixture())
        self.assertEqual(metric(r, "asset_turnover")["value"], .5)
        self.assertEqual(metric(r, "equity_multiplier")["value"], 2)
        self.assertEqual(check(r, "dupont")["status"], "pass")

    def test_cash_reconciliation_includes_fx_and_cce_balances(self):
        r = analyze(fixture())
        self.assertEqual(check(r, "cash_reconciliation")["expected"], 17)
        self.assertEqual(check(r, "cash_reconciliation")["actual"], 17)
        self.assertEqual(check(r, "cash_reconciliation")["status"], "pass")

    def test_missing_fx_is_not_silently_zero(self):
        p = fixture()
        del p["statements"]["fx_effect"]
        self.assertEqual(check(analyze(p), "cash_reconciliation")["status"], "insufficient")

    def test_balance_mismatch_is_review_with_signed_difference(self):
        p = fixture()
        p["statements"]["liabilities_end"] = "99"
        c = check(analyze(p), "balance_equation")
        self.assertEqual(c["status"], "review")
        self.assertEqual(c["difference"], 1)

    def test_zero_denominator_returns_null(self):
        p = fixture()
        p["statements"]["revenue"] = "0"
        r = analyze(p)
        self.assertIsNone(metric(r, "gross_margin")["value"])
        self.assertIsNone(metric(r, "net_margin")["value"])

    def test_nonpositive_equity_has_no_roe(self):
        p = fixture()
        p["statements"]["parent_equity_begin"] = "-10"
        self.assertIsNone(metric(analyze(p), "roe_simple")["value"])

    def test_loss_does_not_make_cash_profit_ratio_look_healthy(self):
        p = fixture()
        p["statements"]["net_profit"] = "-20"
        r = analyze(p)
        self.assertEqual(metric(r, "net_margin")["value"], -20)
        self.assertIsNone(metric(r, "cfo_to_profit")["value"])

    def test_fcf_subtracts_positive_capex_once(self):
        r = analyze(fixture())
        self.assertEqual(metric(r, "free_cash_flow")["value"], 22)
        self.assertEqual(metric(r, "cfo_to_profit")["value"], 1.5)

    def test_ar_growth_is_a_flag_not_a_causal_claim(self):
        r = analyze(fixture())
        self.assertEqual(metric(r, "receivables_growth")["value"], 50)
        self.assertEqual(metric(r, "revenue_growth")["value"], 25)
        paragraphs = " ".join(p for s in r["sections"] for p in s["paragraphs"])
        self.assertIn("不能据此认定", paragraphs)

    def test_missing_adjustments_are_not_reported_as_passing(self):
        r = analyze(fixture())
        self.assertEqual(check(r, "retained_earnings")["status"], "insufficient")
        self.assertEqual(check(r, "profit_cash_bridge")["status"], "insufficient")

    def test_retained_earnings_uses_declared_dividends_and_reserve_transfers(self):
        p = fixture()
        p["statements"].update(retained_begin="50", retained_end="61", dividends_declared="5",
            reserves_transfer="2", retained_other_adjustments="0")
        self.assertEqual(check(analyze(p), "retained_earnings")["status"], "pass")

    def test_profit_cash_bridge_requires_complete_adjustments(self):
        p = fixture()
        p["statements"].update(depreciation_amortization="4", impairment="1",
            finance_nonoperating_adjustments="0", deferred_tax_adjustments="0",
            working_capital_adjustments="5", other_noncash_adjustments="0")
        self.assertEqual(check(analyze(p), "profit_cash_bridge")["status"], "pass")

    def test_rejects_boolean_nonfinite_and_nonnumeric_values(self):
        for value in (True, False, float("nan"), float("inf"), "NaN", "Infinity", "1e9999", "abc", []):
            with self.subTest(value=repr(value)):
                p = fixture()
                p["statements"]["revenue"] = value
                with self.assertRaises(ValueError):
                    analyze(p)

    def test_extreme_exponent_is_validation_error_not_decimal_overflow(self):
        p = fixture()
        p["statements"]["revenue"] = "1e999999999"
        with self.assertRaises(ValueError):
            analyze(p)

    def test_rejects_unsupported_period_scope_unit_and_finance(self):
        for key, value in (("period", "2025Q1"), ("period", "2025-01-01/2025-06-30"),
                ("scope", "parent_only"), ("unit", "CNY"), ("industry", "financial")):
            p = fixture()
            p[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                analyze(p)

    def test_rejects_negative_capex_and_unknown_fields(self):
        for key, value in (("capex_cash_outflow", "-8"), ("cfo_typo", "10")):
            p = fixture()
            p["statements"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                analyze(p)

    def test_preserves_input_and_serializes_deterministically(self):
        p = fixture()
        before = copy.deepcopy(p)
        a, b = analyze(p), analyze(p)
        self.assertEqual(p, before)
        self.assertEqual(json.dumps(a, ensure_ascii=False, allow_nan=False),
                         json.dumps(b, ensure_ascii=False, allow_nan=False))
        self.assertTrue(a["meta"]["is_demo"])

    def test_committed_demo_and_blank_template_are_usable(self):
        folder = Path(__file__).resolve().parents[1] / "data"
        for name in ("demo.json", "import-template.json"):
            with self.subTest(name=name):
                with (folder / name).open(encoding="utf-8") as f:
                    r = analyze(json.load(f))
                self.assertIn("metrics", r)


if __name__ == "__main__":
    unittest.main()
