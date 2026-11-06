"""Exercise the real CLI, provider parsing and core; replace network transport only."""
import contextlib
import copy
import importlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import market_data

try:
    skill_cli = importlib.import_module("skill_cli")
except ModuleNotFoundError:
    skill_cli = None

APP = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "market"


class SkillCliTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(skill_cli, "股票查询 Skill 命令行尚未实现")
        market_data._CACHE.clear()
        self.replies = {name: json.loads((FIXTURES / (name + ".json")).read_text(encoding="utf-8"))
                        for name in ("sina_fzb", "sina_lrb", "sina_llb", "search_600519", "search_000001")}
        self.profiles = {
            "600519": {"rc": 0, "data": {"f57": "600519", "f58": "贵州茅台", "f127": "白酒Ⅱ", "f189": 20010827}},
            "000001": {"rc": 0, "data": {"f57": "000001", "f58": "平安银行", "f127": "银行Ⅱ", "f189": 19910403}},
        }
        self.network_error = None
        transport = patch.object(market_data, "_get_json", side_effect=self.transport_reply)
        transport.start()
        self.addCleanup(transport.stop)

    def transport_reply(self, url):
        if self.network_error:
            raise self.network_error
        query = parse_qs(urlsplit(url).query)
        if "suggest/get" in url:
            key = query["input"][0]
            return copy.deepcopy(self.replies["search_600519" if key == "贵州茅台" else "search_" + key])
        if "/stock/get" in url:
            return copy.deepcopy(self.profiles[query["secid"][0].split(".")[1]])
        return copy.deepcopy(self.replies["sina_" + query["source"][0]])

    def run_cli(self, *args):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            status = skill_cli.main(list(args))
        return status, output.getvalue(), errors.getvalue()

    def test_name_search_returns_real_filtered_candidates_as_json(self):
        status, output, errors = self.run_cli("search", "贵州茅台")
        self.assertEqual((status, errors), (0, ""))
        self.assertEqual([(r["code"], r["name"], r["exchange"]) for r in json.loads(output)],
                         [("600519", "贵州茅台", "SH")])
        self.assertIn("贵州茅台", output)

    def test_periods_forwards_exchange_code_and_keeps_actual_dates(self):
        status, output, errors = self.run_cli("periods", "sz000001")
        self.assertEqual((status, errors), (0, ""))
        result = json.loads(output)
        self.assertEqual(result["company"]["code"], "000001")
        dates = {p["date"]: p["type"] for p in result["periods"]}
        self.assertEqual(dates["2025-06-30"], "halfyear")
        self.assertEqual(dates["2024-12-31"], "annual")
        self.assertIn("coverage", result)

    def test_report_runs_real_core_and_preserves_raw_source_rows(self):
        status, output, errors = self.run_cli("report", "sh600519", "2025-06-30")
        self.assertEqual((status, errors), (0, ""))
        result = json.loads(output)
        self.assertEqual(result["payload"]["period"], "2025-06-30")
        self.assertEqual(result["payload"]["statements"]["revenue"], "89389.35441684")
        self.assertEqual(result["report"]["meta"]["period_end"], "2025-06-30")
        self.assertEqual(result["report"]["meta"]["income_basis"], "year_to_date")
        self.assertEqual([r["key"] for r in result["raw_statements"]], ["income", "balance", "cashflow"])
        self.assertEqual(result["provenance"]["comparators"]["previous_same_period"], "2024-06-30")
        self.assertNotIn("analysis_error", result)

    def test_financial_company_keeps_three_tables_when_core_declines(self):
        status, output, errors = self.run_cli("report", "000001", "2025-06-30")
        self.assertEqual((status, errors), (0, ""))
        result = json.loads(output)
        self.assertEqual(result["payload"]["industry"], "financial")
        self.assertIsNone(result["report"])
        self.assertEqual(result["analysis_error"]["code"], "ANALYSIS_UNAVAILABLE")
        self.assertTrue(all(r["rows"] for r in result["raw_statements"]))
        self.assertIn("statement_urls", result["provenance"])

    def test_invalid_date_returns_structured_market_error(self):
        status, output, errors = self.run_cli("report", "600519", "2025-06-31")
        self.assertEqual((status, output), (3, ""))
        self.assertEqual(json.loads(errors)["error"]["status"], 400)

    def test_network_failure_has_machine_readable_error_code(self):
        self.network_error = market_data.MarketDataError("暂时无法连接公开数据源", 503)
        status, output, errors = self.run_cli("search", "600519")
        self.assertEqual((status, output), (3, ""))
        self.assertEqual(json.loads(errors)["error"], {
            "code": "MARKET_DATA_ERROR", "message": "暂时无法连接公开数据源", "status": 503})

    def test_missing_arguments_return_json_and_nonzero_exit(self):
        status, output, errors = self.run_cli("report", "600519")
        self.assertEqual((status, output), (2, ""))
        self.assertEqual(json.loads(errors)["error"]["code"], "INVALID_ARGUMENT")

    def test_output_file_is_complete_utf8_json_matching_stdout(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "财报.json"
            status, output, errors = self.run_cli("report", "600519", "2025-06-30", "--output", str(target))
            self.assertEqual((status, errors), (0, ""))
            raw = target.read_bytes()
            self.assertIn("贵州茅台".encode("utf-8"), raw)
            self.assertEqual(json.loads(raw), json.loads(output))
            self.assertEqual([p.name for p in Path(folder).iterdir()], ["财报.json"])

    def test_existing_output_is_never_truncated_or_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "保留.json"
            original = "此前已核对的报告\n".encode("utf-8")
            target.write_bytes(original)
            status, output, errors = self.run_cli("report", "600519", "2025-06-30", "--output", str(target))
            self.assertEqual((status, output), (4, ""))
            self.assertEqual(json.loads(errors)["error"]["code"], "OUTPUT_ERROR")
            self.assertEqual(target.read_bytes(), original)
            self.assertEqual([p.name for p in Path(folder).iterdir()], ["保留.json"])

    def test_entrypoint_emits_utf8_json_errors_without_traceback(self):
        process = subprocess.run([sys.executable, str(APP / "skill_cli.py"), "report", "600519", "无效日期"],
                                 cwd=APP, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual((process.returncode, process.stdout), (3, b""))
        result = json.loads(process.stderr.decode("utf-8"))
        self.assertEqual(result["error"]["status"], 400)
        self.assertIn("日期", result["error"]["message"])


if __name__ == "__main__":
    unittest.main()
