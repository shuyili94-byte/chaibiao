"""Behavior tests for local API boundaries and durable order delivery."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit
from server import create_app


class ServerClient:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "orders.sqlite3"
        self.app = create_app(self.db, token="test-token")

    def tearDown(self):
        self.tmp.cleanup()

    def request(self, path, body=None, headers=None, method=None, raw_override=None):
        raw = json.dumps(body, ensure_ascii=False).encode() if body is not None else b""
        if raw_override is not None:
            raw = raw_override
        env = {"REQUEST_METHOD": method or ("POST" if body is not None else "GET"),
               "PATH_INFO": urlsplit(path).path, "QUERY_STRING": urlsplit(path).query,
               "CONTENT_LENGTH": str(len(raw)),
               "CONTENT_TYPE": "application/json", "wsgi.input": io.BytesIO(raw),
               "HTTP_HOST": "127.0.0.1:8765", "REMOTE_ADDR": "127.0.0.1",
               "HTTP_X_CHAIBIAO_TOKEN": "test-token"}
        env.update(headers or {})
        captured = {}
        def start(status, response_headers):
            captured.update(status=int(status.split()[0]), headers=dict(response_headers))
        result = b"".join(self.app(env, start))
        try:
            result = json.loads(result)
        except (ValueError, UnicodeDecodeError):
            result = result.decode()
        return captured["status"], result, captured["headers"]

    def demo(self):
        status, data, _ = self.request("/api/v1/demo")
        self.assertEqual(status, 200)
        return data

    def order(self, key="one"):
        status, order, _ = self.request("/api/v1/orders", {"data": self.demo()},
                                       {"HTTP_IDEMPOTENCY_KEY": key})
        self.assertEqual(status, 201)
        return order

class ServerTests(ServerClient, unittest.TestCase):
    def test_missing_token_cannot_read_financial_api(self):
        status, _, _ = self.request("/api/v1/demo", headers={"HTTP_X_CHAIBIAO_TOKEN": ""})
        self.assertEqual(status, 401)

    def test_foreign_origin_is_rejected_even_with_token(self):
        status, _, _ = self.request("/api/v1/analyze", {}, {"HTTP_ORIGIN": "https://evil.invalid"})
        self.assertEqual(status, 403)

    def test_host_rebinding_is_rejected(self):
        status, _, _ = self.request("/", headers={"HTTP_HOST": "evil.invalid"})
        self.assertEqual(status, 403)

    def test_body_limit_and_invalid_json_fail_closed(self):
        status, _, _ = self.request("/api/v1/analyze", {}, {"CONTENT_LENGTH": "140000"})
        self.assertEqual(status, 413)
        status, _, _ = self.request("/api/v1/analyze", [], {})
        self.assertEqual(status, 422)

    def test_demo_generates_structured_report_without_order(self):
        status, report, _ = self.request("/api/v1/analyze", self.demo())
        self.assertEqual(status, 200)
        self.assertIn("metrics", report)
        self.assertIn("checks", report)
        self.assertEqual(report["source"]["kind"], "demo")

    def test_unpaid_order_cannot_deliver(self):
        order = self.order()
        self.assertEqual(order["status"], "created")
        self.assertEqual(order["mode"], "local_simulation")
        self.assertEqual(order["price_fen"], 300)
        status, _, _ = self.request(f"/api/v1/orders/{order['id']}/deliver", {})
        self.assertEqual(status, 409)

    def test_idempotency_survives_restart_and_rejects_changed_payload(self):
        first = self.order()
        self.app = create_app(self.db, token="test-token")
        again = self.order()
        self.assertEqual(first["id"], again["id"])
        changed = self.demo()
        changed["company"] = "另一家示例公司"
        status, _, _ = self.request("/api/v1/orders", {"data": changed}, {"HTTP_IDEMPOTENCY_KEY": "one"})
        self.assertEqual(status, 409)

    def test_simulated_payment_and_repeat_delivery_are_durable(self):
        order = self.order()
        status, paid, _ = self.request(f"/api/v1/orders/{order['id']}/simulate-pay", {})
        self.assertEqual((status, paid["status"]), (200, "paid"))
        status, delivered, _ = self.request(f"/api/v1/orders/{order['id']}/deliver", {})
        self.assertEqual((status, delivered["status"]), (200, "delivered"))
        self.app = create_app(self.db, token="test-token")
        _, again, _ = self.request(f"/api/v1/orders/{order['id']}/deliver", {})
        self.assertEqual(again["report"], delivered["report"])
        status, _, _ = self.request(f"/api/v1/orders/{order['id']}/simulate-pay", {})
        self.assertEqual(status, 409)

    def test_invalid_data_does_not_create_order(self):
        status, _, _ = self.request("/api/v1/orders", {"data": {}}, {"HTTP_IDEMPOTENCY_KEY": "bad"})
        self.assertEqual(status, 422)

    def test_no_computable_metric_cannot_be_sold_even_in_simulation(self):
        payload = self.demo()
        payload["statements"] = {}
        status, _, _ = self.request("/api/v1/orders", {"data": payload}, {"HTTP_IDEMPOTENCY_KEY": "empty"})
        self.assertEqual(status, 422)

    def test_invalid_unicode_cannot_crash_or_leak_response(self):
        payload = self.demo()
        payload["company"] = "\ud800"
        raw = json.dumps(payload, ensure_ascii=True).encode()
        status, result, _ = self.request("/api/v1/analyze", method="POST", raw_override=raw)
        self.assertIn(status, (400, 422))
        self.assertIn("error", result)

    def test_pages_are_confined_and_use_security_headers(self):
        status, _, headers = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        status, _, _ = self.request("/static/../../server.py")
        self.assertEqual(status, 404)


class FixtureMarket:
    """Replace only the slow upstream network at the API integration boundary."""
    class MarketDataError(Exception):
        status = 503

    def search_companies(self, query):
        if query == "停用":
            raise self.MarketDataError("公开数据源暂不可用，请稍后重试。")
        return [{"code": "600519", "name": "贵州茅台", "exchange": "SH", "symbol": "sh600519", "matched_query": query}]

    def list_periods(self, code):
        return {"company": {"code": code, "name": "贵州茅台"},
                "periods": [{"date": "2025-06-30", "year": 2025, "type": "halfyear", "label": "2025 半年报"}],
                "coverage": {"earliest": "2025-06-30", "latest": "2025-06-30", "note": "固定公开样例测试响应"}}

    def fetch_report(self, code, date):
        return {"payload": {"code": code, "period": date},
                "raw_statements": [{"key": "income", "title": "利润表", "rows": [{"label": "营业收入", "value": "89400", "unit": "CNY_million"}]}],
                "provenance": {"period": date}}


class MarketAPITests(ServerClient, unittest.TestCase):
    def setUp(self):
        super().setUp()
        # Existing behavior remains on real core/SQLite; only internet calls vary.
        self.app.market = FixtureMarket()

    def test_company_search_decodes_chinese_and_requires_one_query(self):
        status, data, _ = self.request("/api/v1/companies/search?q=%E8%8C%85%E5%8F%B0")
        self.assertEqual(status, 200)
        self.assertEqual(data["companies"][0]["matched_query"], "茅台")
        self.assertEqual(self.request("/api/v1/companies/search?q=a&q=b")[0], 422)
        self.assertEqual(self.request("/api/v1/companies/search")[0], 422)

    def test_star_st_stock_names_are_searchable(self):
        status, result, _ = self.request("/api/v1/companies/search?q=%2AST%E4%BB%A3%E7%A0%81")
        self.assertEqual(status, 200)
        self.assertEqual(result["companies"][0]["matched_query"], "*ST代码")

    def test_period_endpoint_preserves_available_history_without_inventing_dates(self):
        status, data, _ = self.request("/api/v1/companies/600519/periods")
        self.assertEqual(status, 200)
        self.assertEqual([p["date"] for p in data["periods"]], ["2025-06-30"])
        self.assertEqual(data["company"]["code"], "600519")

    def test_report_endpoint_preserves_raw_statements_and_exact_period(self):
        status, data, _ = self.request("/api/v1/companies/600519/report?date=2025-06-30")
        self.assertEqual(status, 200)
        self.assertEqual(data["payload"]["period"], "2025-06-30")
        self.assertEqual(data["raw_statements"][0]["rows"][0]["label"], "营业收入")

    def test_market_endpoints_reject_extra_parameters_and_invalid_report_dates(self):
        self.assertEqual(self.request("/api/v1/companies/600519/report?date=2025-05-31")[0], 422)
        self.assertEqual(self.request("/api/v1/companies/600519/report?date=2025-06-30&url=http://evil.invalid")[0], 422)
        self.assertEqual(self.request("/api/v1/companies/600519/periods?date=2025-06-30")[0], 422)

    def test_upstream_failure_is_a_retriable_json_error_not_fake_data(self):
        status, data, _ = self.request("/api/v1/companies/search?q=%E5%81%9C%E7%94%A8")
        self.assertEqual(status, 503)
        self.assertIn("error", data)
        self.assertNotIn("companies", data)


if __name__ == "__main__":
    unittest.main()
