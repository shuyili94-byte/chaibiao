"""Offline command-line adapter for Skill callers. No network or payment calls."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from core import analyze


def main():
    parser = argparse.ArgumentParser(description="从已核对的年度财务JSON生成拆表报告")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.input.stat().st_size > 128 * 1024:
            raise ValueError("输入不能超过128KB")
        payload = json.loads(args.input.read_text(encoding="utf-8-sig"))
        report = analyze(payload)
        result = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
        encoded = (result + "\n").encode("utf-8")
        if args.output:
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=args.output.resolve().parent, prefix=".chaibiao-", delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(args.output)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        else:
            sys.stdout.reconfigure(encoding="utf-8")
            print(result)
        return 0
    except (ValueError, OSError) as exc:
        sys.stderr.reconfigure(encoding="utf-8")
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
