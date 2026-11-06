"""Single-user, loopback-only financial report prototype. Python standard library."""
import argparse
from collections import deque
from contextlib import contextmanager
import hashlib
import importlib
from http import HTTPStatus
import json
from pathlib import Path
import re
import secrets
import sqlite3
import time
from threading import Lock
from socketserver import ThreadingMixIn
from urllib.parse import urlsplit, parse_qs
from wsgiref.simple_server import make_server, WSGIRequestHandler, WSGIServer

ROOT = Path(__file__).resolve().parent
MAX_BODY = 128 * 1024
VERSION = "0.2.0"


class APIError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")


def create_app(db_path=None, token=None):
    return Application(db_path or ROOT / "var" / "orders.sqlite3", token or secrets.token_urlsafe(32))


class Application:
    def __init__(self, db_path, token):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.token = token
        self.calls = deque()
        self.rate_lock = Lock()
        self.market = None
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY, idem TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
                data TEXT NOT NULL, status TEXT NOT NULL, report TEXT,
                price_fen INTEGER NOT NULL, created_at INTEGER NOT NULL)""")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.db_path), timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def __call__(self, env, start_response):
        try:
            status, body, mime = self.dispatch(env)
        except APIError as exc:
            status, body, mime = exc.status, {"error": exc.message}, "application/json"
        except (ValueError, UnicodeDecodeError) as exc:
            status, body, mime = 422, {"error": str(exc)}, "application/json"
        except Exception:
            # Never return traceback, input records, tokens, or database paths.
            status, body, mime = 500, {"error": "服务处理失败，请重试。未发生真实扣款。"}, "application/json"
        raw = encode(body) if mime == "application/json" else body
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        headers = [("Content-Type", mime + "; charset=utf-8"), ("Content-Length", str(len(raw))),
                   ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
                   ("X-Frame-Options", "DENY"), ("Referrer-Policy", "no-referrer"),
                   ("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; "
                    "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
                    "frame-ancestors 'none'; form-action 'self'")]
        start_response(f"{status} {HTTPStatus(status).phrase}", headers)
        return [raw]

    def dispatch(self, env):
        host = env.get("HTTP_HOST", "")
        try:
            hostname = urlsplit("http://" + host).hostname
        except ValueError:
            hostname = None
        if hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise APIError(403, "此原型仅允许本机访问。")
        origin = env.get("HTTP_ORIGIN")
        if origin and origin not in {"http://" + host, "https://" + host}:
            raise APIError(403, "不允许跨站请求。")
        path, method = env.get("PATH_INFO", "/"), env.get("REQUEST_METHOD", "GET")
        if path.startswith("/api/"):
            if not secrets.compare_digest(env.get("HTTP_X_CHAIBIAO_TOKEN", ""), self.token):
                raise APIError(401, "本地访问凭证缺失，请重新打开工作台。")
            with self.rate_lock:
                now = time.monotonic()
                while self.calls and self.calls[0] < now - 60:
                    self.calls.popleft()
                if len(self.calls) >= 120:
                    raise APIError(429, "请求较多，请稍后重试。")
                self.calls.append(now)
        if method == "GET":
            if path == "/":
                page = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
                return 200, page.replace("__API_TOKEN__", self.token), "text/html"
            if path in {"/static/app.js", "/static/app.css"}:
                mime = "text/javascript" if path.endswith(".js") else "text/css"
                return 200, (ROOT / path.lstrip("/")).read_bytes(), mime
            if path == "/healthz":
                return 200, {"status": "ok", "version": VERSION, "mode": "local_simulation"}, "application/json"
            if path in {"/api/v1/demo", "/api/v1/template"}:
                filename = "demo.json" if path.endswith("/demo") else "import-template.json"
                return 200, json.loads((ROOT / "data" / filename).read_text(encoding="utf-8")), "application/json"
            if path == "/api/v1/schema":
                return 200, json.loads((ROOT / "data" / "schema.json").read_text(encoding="utf-8")), "application/json"
            if path == "/api/v1/companies/search":
                query = self.query(env, {"q"}, required={"q"})["q"].strip()
                if not re.fullmatch(r"[\w\u4e00-\u9fff*.\- ]{1,40}", query):
                    raise APIError(422, "请输入1–40字的股票代码或公司名。")
                return 200, {"companies": self.market_call("search_companies", query)}, "application/json"
            company_route = re.fullmatch(r"/api/v1/companies/(\d{6})/(periods|report)", path)
            if company_route:
                code, action = company_route.groups()
                if action == "periods":
                    self.query(env, set())
                    result = self.market_call("list_periods", code)
                else:
                    date = self.query(env, {"date"}, required={"date"})["date"]
                    if not re.fullmatch(r"(?:19|20)\d{2}-(?:03-31|06-30|09-30|12-31)", date):
                        raise APIError(422, "请选择一季报、半年报、三季报或年报的有效报告期。")
                    result = self.market_call("fetch_report", code, date)
                return 200, result, "application/json"
        if method == "POST" and path.startswith("/api/"):
            data = self.read_body(env)
            if path == "/api/v1/analyze":
                from core import analyze
                return 200, analyze(data), "application/json"
            if path == "/api/v1/orders":
                return 201, self.create_order(data, env.get("HTTP_IDEMPOTENCY_KEY", "")), "application/json"
            match = re.fullmatch(r"/api/v1/orders/([a-f0-9]{32})/(simulate-pay|deliver)", path)
            if match:
                if data:
                    raise APIError(422, "此操作的请求体应为空对象。")
                return 200, self.transition(match[1], match[2]), "application/json"
        raise APIError(404, "未找到该页面或接口。")

    @staticmethod
    def query(env, allowed, required=frozenset()):
        raw = env.get("QUERY_STRING", "")
        if len(raw) > 2048:
            raise APIError(422, "查询参数过长。")
        try:
            parsed = parse_qs(raw, keep_blank_values=True, errors="strict", max_num_fields=8)
        except (ValueError, UnicodeError):
            raise APIError(422, "查询参数无效。")
        if set(parsed) - allowed or required - set(parsed) or any(len(v) != 1 or not v[0] for v in parsed.values()):
            raise APIError(422, "查询参数缺失、重复或不受支持。")
        return {key: value[0] for key, value in parsed.items()}

    def market_call(self, operation, *args):
        if self.market is None:
            self.market = importlib.import_module("market_data")
        try:
            return getattr(self.market, operation)(*args)
        except self.market.MarketDataError as exc:
            status = getattr(exc, "status", 502)
            if status not in {400, 404, 422, 429, 502, 503, 504}:
                status = 502
            raise APIError(status, str(exc)) from None

    def read_body(self, env):
        if env.get("CONTENT_TYPE", "").split(";")[0].strip() != "application/json":
            raise APIError(415, "请使用 application/json。")
        try:
            length = int(env.get("CONTENT_LENGTH", "0"))
        except ValueError:
            raise APIError(400, "请求长度无效。")
        if length > MAX_BODY:
            raise APIError(413, "JSON 文件不能超过 128 KB。")
        if length <= 0:
            raise APIError(400, "请求体不能为空。")
        raw = env["wsgi.input"].read(length)
        if len(raw) != length:
            raise APIError(400, "请求体不完整。")
        try:
            data = json.loads(raw.decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            # JSON can contain escaped lone UTF-16 surrogates. Reject them before
            # a response encoder or the WSGI error handler ever sees user records.
            json.dumps(data, ensure_ascii=False).encode("utf-8")
        except (ValueError, UnicodeDecodeError):
            raise APIError(400, "JSON 格式无效。")
        if not isinstance(data, dict):
            raise APIError(422, "JSON 顶层必须是对象。")
        return data

    def create_order(self, body, key):
        from core import analyze
        if set(body) != {"data"}:
            raise APIError(422, "订单请求仅接受 data 字段。")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key):
            raise APIError(422, "请提供 1–128 位 Idempotency-Key。")
        preview = analyze(body["data"])  # Validate BEFORE any order is created.
        if not any(metric["status"] == "computed" for metric in preview["metrics"]):
            raise APIError(422, "没有可计算的指标，请补充财务数据后再创建订单。")
        raw = encode(body["data"]).decode("utf-8")
        fingerprint = hashlib.sha256(raw.encode()).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id,fingerprint,status,price_fen FROM orders WHERE idem=?", (key,)).fetchone()
            if row:
                if row[1] != fingerprint:
                    raise APIError(409, "同一幂等键已用于不同的数据，请创建新请求。")
                return self.public_order(row[0], row[2], row[3])
            order_id = secrets.token_hex(16)
            db.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)",
                       (order_id, key, fingerprint, raw, "created", None, 300, int(time.time())))
            return self.public_order(order_id, "created", 300)

    @staticmethod
    def public_order(order_id, status, price):
        return {"id": order_id, "status": status, "price_fen": price,
                "mode": "local_simulation", "notice": "本地流程模拟，不连接支付宝，不发生真实扣款。"}

    def transition(self, order_id, action):
        from core import analyze
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data,status,report,price_fen FROM orders WHERE id=?", (order_id,)).fetchone()
            if not row:
                raise APIError(404, "订单不存在。")
            payload, state, stored, price = row
            if action == "simulate-pay":
                if state != "created":
                    raise APIError(409, "订单已处理，不能重复确认模拟付款。")
                db.execute("UPDATE orders SET status='paid' WHERE id=?", (order_id,))
                return self.public_order(order_id, "paid", price)
            if state == "delivered":
                report = json.loads(stored)
            elif state == "paid":
                report = analyze(json.loads(payload))
                db.execute("UPDATE orders SET status='delivered',report=? WHERE id=?",
                           (encode(report).decode("utf-8"), order_id))
            else:
                raise APIError(409, "请先完成本地模拟付款。")
            result = self.public_order(order_id, "delivered", price)
            result["report"] = report
            return result


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format_string, *args):
        # Order IDs and input records are deliberately excluded from console logs.
        pass


class LocalThreadedServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


def main():
    parser = argparse.ArgumentParser(description="拆表：本地财报工作台，不发生真实扣款")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="启动后在默认浏览器打开")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("端口需在 1024–65535 之间")
    app = create_app()
    with make_server("127.0.0.1", args.port, app, handler_class=QuietHandler, server_class=LocalThreadedServer) as server:
        url = f"http://127.0.0.1:{args.port}"
        print(f"拆表 v{VERSION} 已启动：{url}\n本地单用户程序；支持A股公开历史财报查询，不发生真实扣款。Ctrl+C 停止。", flush=True)
        if args.open:
            import webbrowser
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
