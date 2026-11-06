"""Public A-share lookup for Skill callers. No credentials or payment calls."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

from core import analyze
from market_data import MarketDataError, fetch_report, list_periods, search_companies


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def _write_new_report(path, encoded):
    """Publish a complete sibling file atomically, refusing an existing target."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".chaibiao-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        # Creating a hard link cannot replace an existing path, including a symlink.
        # The temporary file and destination share a filesystem by construction.
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = _Parser(description="拆表：搜索A股、列出实际报告期、读取三表与解读")
    commands = parser.add_subparsers(dest="command", required=True)
    search = commands.add_parser("search", help="按股票名称、拼音或代码搜索候选")
    search.add_argument("query")
    periods = commands.add_parser("periods", help="列出所选公司的实际可用报告期")
    periods.add_argument("code")
    report = commands.add_parser("report", help="读取选定报告日三表并运行确定性解读")
    report.add_argument("code")
    report.add_argument("date", help="从periods结果选择YYYY-MM-DD")
    report.add_argument("--output", type=Path, help="同时保存UTF-8 JSON；目标必须尚不存在")
    try:
        args = parser.parse_args(argv)
        if args.command == "search":
            result = search_companies(args.query)
        elif args.command == "periods":
            result = list_periods(args.code)
        else:
            result = fetch_report(args.code, args.date)
            try:
                result["report"] = analyze(result["payload"])
            except ValueError as exc:
                result["report"] = None
                result["analysis_error"] = {"code": "ANALYSIS_UNAVAILABLE", "message": str(exc)}
        serialized = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.command == "report" and args.output:
            _write_new_report(args.output, serialized.encode("utf-8"))
        sys.stdout.write(serialized)
        return 0
    except MarketDataError as exc:
        error, status = {"code": "MARKET_DATA_ERROR", "message": str(exc), "status": exc.status}, 3
    except ValueError as exc:
        error, status = {"code": "INVALID_ARGUMENT", "message": str(exc)}, 2
    except OSError as exc:
        error, status = {"code": "OUTPUT_ERROR", "message": str(exc)}, 4
    sys.stderr.write(json.dumps({"error": error}, ensure_ascii=False) + "\n")
    return status


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
