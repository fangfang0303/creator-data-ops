import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import feishu_bridge  # Reuse the normalized account/content row builders.


ROOT = Path(__file__).resolve().parent.parent
OUTPUTS = ROOT / "outputs"
DEFAULT_STORE_DIR = ROOT / "自媒体运营数据"
CONFIG_FILE = OUTPUTS / "local_data_store_config.json"
SYNC_RESULT_FILE = OUTPUTS / "local_data_store_sync_result.json"

ACCOUNT_FILE_NAME = "账号数据.csv"
CONTENT_FILE_NAME = "内容直播数据.csv"


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return json.loads(path.read_text(encoding=encoding))
        except Exception:
            continue
    return default


def resolve_store_dir(path: str | None = None) -> Path:
    if path:
        return Path(path).expanduser().resolve()
    config = read_json(CONFIG_FILE, {})
    configured = str(config.get("store_dir") or "").strip() if isinstance(config, dict) else ""
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_STORE_DIR.resolve()


def ensure_csv(path: Path, fields: list[str]) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()


def write_rows(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: str(row.get(field, "")) for field in fields})


def init_store(path: str | None = None) -> dict[str, Any]:
    store_dir = resolve_store_dir(path)
    store_dir.mkdir(parents=True, exist_ok=True)
    account_file = store_dir / ACCOUNT_FILE_NAME
    content_file = store_dir / CONTENT_FILE_NAME
    ensure_csv(account_file, feishu_bridge.ACCOUNT_FIELDS)
    ensure_csv(content_file, feishu_bridge.CONTENT_FIELDS)

    result = {
        "ok": True,
        "action": "init_local_store",
        "store_dir": str(store_dir),
        "account_file": str(account_file),
        "content_file": str(content_file),
        "account_fields": feishu_bridge.ACCOUNT_FIELDS,
        "content_fields": feishu_bridge.CONTENT_FIELDS,
        "time": now_text(),
    }
    write_json(CONFIG_FILE, result)
    return result


def sync_store(path: str | None = None, accounts_only: bool = False) -> dict[str, Any]:
    config = init_store(path)
    account_rows = feishu_bridge.build_account_rows()
    content_rows = [] if accounts_only else feishu_bridge.build_content_rows()

    write_rows(Path(config["account_file"]), feishu_bridge.ACCOUNT_FIELDS, account_rows)
    if not accounts_only:
        write_rows(Path(config["content_file"]), feishu_bridge.CONTENT_FIELDS, content_rows)

    result = {
        "ok": True,
        "action": "sync_local_store",
        "accounts_only": accounts_only,
        "store_dir": config["store_dir"],
        "account_file": config["account_file"],
        "content_file": config["content_file"],
        "account_rows": len(account_rows),
        "content_rows": len(content_rows),
        "time": now_text(),
    }
    write_json(SYNC_RESULT_FILE, result)
    return result


def status() -> dict[str, Any]:
    config = read_json(CONFIG_FILE, {})
    return {
        "ok": True,
        "config_exists": CONFIG_FILE.exists(),
        "config": config,
        "last_sync": read_json(SYNC_RESULT_FILE, {}),
        "time": now_text(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    init_parser = sub.add_parser("init")
    init_parser.add_argument("--path", default="")

    sync_parser = sub.add_parser("sync")
    sync_parser.add_argument("--path", default="")
    sync_parser.add_argument("--accounts-only", action="store_true")

    sub.add_parser("status")

    args = parser.parse_args()
    if args.command == "init":
        print(json.dumps(init_store(args.path or None), ensure_ascii=False))
        return 0
    if args.command == "sync":
        print(json.dumps(sync_store(args.path or None, accounts_only=args.accounts_only), ensure_ascii=False))
        return 0
    if args.command == "status":
        print(json.dumps(status(), ensure_ascii=False))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
