import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
WORKDIR = ROOT / "work"
OUTPUTS = ROOT / "outputs"

WIKI_NAME = "自媒体运营"
ACCOUNT_BASE_NAME = "账号数据"
CONTENT_BASE_NAME = "内容／直播数据"

CONFIG_FILE = OUTPUTS / "feishu_display_config.json"
SYNC_RESULT_FILE = OUTPUTS / "feishu_display_sync_result.json"

ACCOUNT_FIELDS = [
    "唯一键",
    "平台",
    "账号标识",
    "账号名称",
    "账号ID",
    "粉丝数",
    "关注数",
    "获赞数",
    "笔记/作品数",
    "收藏数",
    "最近拉数时间",
    "数据状态",
    "账号原始数据",
]

CONTENT_FIELDS = [
    "唯一键",
    "分类",
    "平台",
    "账号标识",
    "账号名称",
    "账号ID",
    "内容ID",
    "标题",
    "发布时间",
    "播放/阅读量",
    "点赞数",
    "评论数",
    "收藏数",
    "分享数",
    "观看人数",
    "直播场次",
    "直播天数",
    "直播时长秒",
    "新增粉丝数",
    "回放数",
    "最近拉数时间",
    "原始数据",
]

PLATFORMS = {
    "xhs": {
        "label": "小红书",
        "accounts_file": WORKDIR / "xhs_accounts.json",
        "latest_prefix": "xhs_metrics_latest_",
        "aggregate_file": OUTPUTS / "xhs_metrics_latest.json",
        "content_array": "all_notes",
        "latest_content": "latest_note",
        "content_id": "note_id",
    },
    "douyin": {
        "label": "抖音",
        "accounts_file": WORKDIR / "douyin_accounts.json",
        "latest_prefix": "douyin_metrics_latest_",
        "aggregate_file": OUTPUTS / "douyin_metrics_latest.json",
        "content_array": "all_videos",
        "latest_content": "latest_video",
        "content_id": "item_id",
    },
    "wechat_video": {
        "label": "微信视频号",
        "accounts_file": WORKDIR / "wechat_video_accounts.json",
        "latest_prefix": "wechat_video_metrics_latest_",
        "aggregate_file": OUTPUTS / "wechat_video_metrics_latest.json",
        "content_array": "all_videos",
        "latest_content": "latest_video",
        "content_id": "item_id",
    },
}


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return json.loads(path.read_text(encoding=encoding))
        except Exception:
            continue
    return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def compact_json(value: Any, max_len: int = 6000) -> str:
    text = json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"))
    if len(text) > max_len:
        return text[: max_len - 20] + "...[已截断]"
    return text


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        if not value:
            return ""
        first = value[0]
        if isinstance(first, dict):
            return str(first.get("text") or first.get("name") or first.get("value") or "")
        return str(first)
    if isinstance(value, dict):
        return str(value.get("text") or value.get("name") or value.get("value") or "")
    return str(value)


def first_value(data: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return ""


def node_command() -> list[str]:
    node_dirs = [
        ROOT / "node",
        Path(r"C:\Users\surface\AppData\Local\OpenAI\Codex\bin\5b9024f90663758b"),
        Path(r"C:\Users\surface\AppData\Local\OpenAI\Codex\runtimes\cua_node\1b23c930bdf84ed6\bin"),
        Path(r"C:\Users\surface\anaconda3\Lib\site-packages\playwright\driver"),
    ]
    lark_cli_script = Path(r"C:\Users\surface\AppData\Roaming\npm\node_modules\@larksuite\cli\scripts\run.js")
    node_candidates = [
        *(path / "node.exe" for path in node_dirs),
        Path(r"C:\Program Files\nodejs\node.exe"),
    ]
    node_bin = next((path for path in node_candidates if path.exists()), None)
    if not node_bin:
        return ["node", str(lark_cli_script)]
    return [str(node_bin), str(lark_cli_script)]


def run_cli(args: list[str], cwd: Path | None = None) -> dict[str, Any]:
    env = dict(os.environ)
    prepend = [
        ROOT / "node",
        Path(r"C:\Users\surface\AppData\Local\OpenAI\Codex\bin\5b9024f90663758b"),
        Path(r"C:\Users\surface\AppData\Local\OpenAI\Codex\runtimes\cua_node\1b23c930bdf84ed6\bin"),
    ]
    existing = [str(path) for path in prepend if path.exists()]
    if existing:
        env["PATH"] = ";".join(existing + [env.get("PATH", "")])

    result = subprocess.run(
        [*node_command(), *args],
        cwd=str(cwd or ROOT),
        capture_output=True,
        env=env,
    )

    def decode_output(raw: bytes) -> str:
        candidates = []
        for encoding in ("utf-8-sig", "utf-8", "gbk", "cp936"):
            try:
                text = raw.decode(encoding, errors="replace")
            except Exception:
                continue
            candidates.append((text.count("\ufffd"), text))
        if not candidates:
            return raw.decode(errors="replace")
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    stdout = decode_output(result.stdout or b"").strip()
    stderr = decode_output(result.stderr or b"").strip()
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError(stderr or stdout or f"飞书 CLI 没有返回 JSON：{' '.join(args)}")
    payload = json.loads(stdout[start : end + 1])
    if not payload.get("ok", False):
        error = payload.get("error", {})
        raise RuntimeError(str(error.get("message") or payload))
    return payload


def field_json(fields: list[str]) -> str:
    return json.dumps([{"name": name, "type": "text"} for name in fields], ensure_ascii=False)


def extract_space_id(space: dict[str, Any]) -> str:
    return str(space.get("space_id") or space.get("id") or "").strip()


def extract_base_token(payload: dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    base = data.get("base") if isinstance(data.get("base"), dict) else {}
    candidates = [
        base.get("base_token"),
        base.get("app_token"),
        base.get("token"),
        base.get("obj_token"),
        base.get("url"),
        data.get("app_token"),
        data.get("base_token"),
        data.get("token"),
        data.get("obj_token"),
        data.get("url"),
    ]
    for value in candidates:
        text = str(value or "").strip()
        if text.startswith("http") and "/base/" in text:
            return text.split("/base/", 1)[1].split("?", 1)[0].split("/", 1)[0]
        if text:
            return text
    raise RuntimeError(f"无法从飞书返回中识别 base token：{payload}")


def extract_table_id(payload: dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    table = data.get("table") if isinstance(data.get("table"), dict) else {}
    candidates = [
        table.get("table_id"),
        table.get("id"),
        data.get("table_id"),
        data.get("default_table_id"),
        data.get("id"),
    ]
    tables = data.get("tables")
    if isinstance(tables, list) and tables:
        candidates.append((tables[0] or {}).get("table_id") or (tables[0] or {}).get("id"))
    for value in candidates:
        text = str(value or "").strip()
        if text:
            return text
    raise RuntimeError(f"无法从飞书返回中识别 table id：{payload}")


def list_wiki_spaces() -> list[dict[str, Any]]:
    payload = run_cli(["wiki", "+space-list", "--page-all", "--as", "user", "--format", "json"])
    return list(payload.get("data", {}).get("spaces", []))


def ensure_wiki_space(config: dict[str, Any]) -> dict[str, Any]:
    if config.get("space_id"):
        return config

    for space in list_wiki_spaces():
        if str(space.get("name") or "").strip() == WIKI_NAME:
            config["space_id"] = extract_space_id(space)
            config["space_name"] = WIKI_NAME
            return config

    payload = run_cli(
        [
            "wiki",
            "+space-create",
            "--name",
            WIKI_NAME,
            "--description",
            "本地自媒体运营工具自动同步的数据展示空间，仅用于个人或团队内部查看。",
            "--as",
            "user",
            "--format",
            "json",
        ]
    )
    data = payload.get("data", {})
    config["space_id"] = str(data.get("space_id") or data.get("id") or "").strip()
    config["space_name"] = WIKI_NAME
    if not config["space_id"]:
        raise RuntimeError(f"知识库创建成功但没有返回 space_id：{payload}")
    return config


def list_wiki_nodes(space_id: str) -> list[dict[str, Any]]:
    payload = run_cli(["wiki", "+node-list", "--space-id", space_id, "--page-all", "--as", "user", "--format", "json"])
    return list(payload.get("data", {}).get("items") or payload.get("data", {}).get("nodes") or [])


def find_bitable_node(space_id: str, title: str) -> dict[str, Any] | None:
    for node in list_wiki_nodes(space_id):
        node_title = str(node.get("title") or node.get("obj_title") or node.get("name") or "").strip()
        obj_type = str(node.get("obj_type") or node.get("node_type") or "").strip()
        if node_title == title and (not obj_type or obj_type == "bitable"):
            return node
    return None


def table_list(base_token: str) -> list[dict[str, Any]]:
    payload = run_cli(["base", "+table-list", "--base-token", base_token, "--as", "user", "--format", "json"])
    return list(payload.get("data", {}).get("tables", []))


def ensure_table(base_token: str, table_name: str, fields: list[str]) -> str:
    tables = table_list(base_token)
    for table in tables:
        name = str(table.get("name") or "").strip()
        table_id = str(table.get("table_id") or table.get("id") or "").strip()
        if name == table_name and table_id:
            ensure_fields(base_token, table_id, fields)
            return table_id

    if tables:
        table = tables[0]
        table_id = str(table.get("table_id") or table.get("id") or "").strip()
        if table_id:
            ensure_fields(base_token, table_id, fields)
            return table_id

    payload = run_cli(
        [
            "base",
            "+table-create",
            "--base-token",
            base_token,
            "--name",
            table_name,
            "--fields",
            field_json(fields),
            "--as",
            "user",
            "--format",
            "json",
        ]
    )
    return extract_table_id(payload)


def ensure_fields(base_token: str, table_id: str, fields: list[str]) -> None:
    payload = run_cli(
        [
            "base",
            "+field-list",
            "--base-token",
            base_token,
            "--table-id",
            table_id,
            "--as",
            "user",
            "--format",
            "json",
        ]
    )
    existing = {
        str(item.get("name") or item.get("field_name") or "").strip()
        for item in payload.get("data", {}).get("fields", [])
    }
    for field in fields:
        if field in existing:
            continue
        field_path = OUTPUTS / f"feishu_display_field_{safe_name(field)}.json"
        write_json(field_path, {"name": field, "type": "text"})
        run_cli(
            [
                "base",
                "+field-create",
                "--base-token",
                base_token,
                "--table-id",
                table_id,
                "--json",
                f"@{field_path.relative_to(ROOT)}",
                "--as",
                "user",
                "--format",
                "json",
            ]
        )


def safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value)[:80] or "field"


def ensure_base_in_wiki(config: dict[str, Any], key: str, title: str, fields: list[str]) -> dict[str, Any]:
    token_key = f"{key}_base_token"
    table_key = f"{key}_table_id"
    url_key = f"{key}_url"

    if config.get(token_key):
        base_token = str(config[token_key])
        config[table_key] = ensure_table(base_token, title, fields)
        config[url_key] = f"https://wcnyz09v8cbl.feishu.cn/base/{base_token}"
        if not config.get(f"{key}_wiki_moved"):
            try:
                run_cli(
                    [
                        "wiki",
                        "+move",
                        "--obj-type",
                        "bitable",
                        "--obj-token",
                        base_token,
                        "--target-space-id",
                        str(config["space_id"]),
                        "--as",
                        "user",
                        "--format",
                        "json",
                    ]
                )
                config[f"{key}_wiki_moved"] = True
            except Exception as exc:
                config[f"{key}_wiki_move_warning"] = str(exc)
        return config

    space_id = str(config["space_id"])
    node = find_bitable_node(space_id, title)
    if node:
        base_token = str(node.get("obj_token") or node.get("token") or "").strip()
        if base_token:
            config[token_key] = base_token
            config[table_key] = ensure_table(base_token, title, fields)
            config[url_key] = f"https://wcnyz09v8cbl.feishu.cn/base/{base_token}"
            return config

    created = run_cli(
        [
            "base",
            "+base-create",
            "--name",
            title,
            "--table-name",
            title,
            "--fields",
            field_json(fields),
            "--time-zone",
            "Asia/Shanghai",
            "--as",
            "user",
            "--format",
            "json",
        ]
    )
    base_token = extract_base_token(created)
    table_id = extract_table_id(created)
    config[token_key] = base_token
    config[table_key] = table_id
    config[url_key] = f"https://wcnyz09v8cbl.feishu.cn/base/{base_token}"

    try:
        run_cli(
            [
                "wiki",
                "+move",
                "--obj-type",
                "bitable",
                "--obj-token",
                base_token,
                "--target-space-id",
                space_id,
                "--as",
                "user",
                "--format",
                "json",
            ]
        )
    except Exception as exc:
        config[f"{key}_wiki_move_warning"] = str(exc)

    return config


def ensure_feishu_display() -> dict[str, Any]:
    config = read_json(CONFIG_FILE, {})
    config = ensure_wiki_space(config)
    config = ensure_base_in_wiki(config, "account", ACCOUNT_BASE_NAME, ACCOUNT_FIELDS)
    config = ensure_base_in_wiki(config, "content", CONTENT_BASE_NAME, CONTENT_FIELDS)
    config["updated_at"] = now_text()
    write_json(CONFIG_FILE, config)
    return config


def configured_accounts() -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for platform_key, meta in PLATFORMS.items():
        data = read_json(meta["accounts_file"], {"accounts": []})
        for item in data.get("accounts", []):
            if not isinstance(item, dict):
                continue
            account_key = str(item.get("name") or "").strip()
            if not account_key:
                continue
            result[(platform_key, account_key)] = {
                "platform_key": platform_key,
                "platform": meta["label"],
                "account_key": account_key,
                "account_name": account_key,
                "status": "未抓取",
                "raw": item,
            }
    return result


def metrics_files() -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for platform_key, meta in PLATFORMS.items():
        prefix = str(meta["latest_prefix"])
        for path in OUTPUTS.glob(f"{prefix}*.json"):
            if path.name == Path(str(meta["aggregate_file"])).name:
                continue
            files.append((platform_key, path))
    return files


def account_id_from(platform_key: str, account: dict[str, Any]) -> str:
    if platform_key == "xhs":
        return str(first_value(account, ["red_num", "account_id", "user_id"]))
    if platform_key == "douyin":
        return str(first_value(account, ["douyin_id", "account_id", "user_id"]))
    if platform_key == "wechat_video":
        return str(first_value(account, ["channel_id", "account_id", "user_id"]))
    return str(first_value(account, ["account_id", "id"]))


def account_name_from(account_key: str, account: dict[str, Any]) -> str:
    return str(first_value(account, ["name", "nickname", "account_name"]) or account_key)


def account_row(platform_key: str, account_key: str, data: dict[str, Any] | None, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    meta = PLATFORMS[platform_key]
    account = dict((data or {}).get("account") or {})
    fallback = fallback or {}
    latest_time = str((data or {}).get("fetched_at") or (data or {}).get("time") or "")
    content_count = first_value(data or {}, ["notes_count", "videos_count", "contents_count"])
    if not content_count:
        content_count = first_value(account, ["works_count", "video_count", "notes_count"])
    row = {
        "唯一键": f"{platform_key}|{account_key}",
        "平台": meta["label"],
        "账号标识": account_key,
        "账号名称": account_name_from(account_key, account) if account else str(fallback.get("account_name") or account_key),
        "账号ID": account_id_from(platform_key, account) if account else "",
        "粉丝数": str(first_value(account, ["fans_count", "followers_count", "follower_count"])),
        "关注数": str(first_value(account, ["follow_count", "following_count"])),
        "获赞数": str(first_value(account, ["liked_count", "faved_count", "likes_count"])),
        "笔记/作品数": str(content_count or ""),
        "收藏数": str(first_value(account, ["collect_count", "collected_count"])),
        "最近拉数时间": latest_time,
        "数据状态": "已抓取" if data else str(fallback.get("status") or "未抓取"),
        "账号原始数据": compact_json(account or fallback.get("raw") or {}),
    }
    return row


def build_account_rows() -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    fallback = configured_accounts()
    for (platform_key, account_key), item in fallback.items():
        row = account_row(platform_key, account_key, None, item)
        rows[row["唯一键"]] = row

    for platform_key, path in metrics_files():
        data = read_json(path, {})
        account_key = str(data.get("account_key") or path.stem.replace(str(PLATFORMS[platform_key]["latest_prefix"]), "")).strip()
        if not account_key:
            continue
        row = account_row(platform_key, account_key, data)
        rows[row["唯一键"]] = row
    return list(rows.values())


def content_identity(platform_key: str, account_key: str, item: dict[str, Any], content_id_key: str) -> str:
    content_id = str(item.get(content_id_key) or item.get("id") or item.get("item_id") or "").strip()
    if not content_id:
        content_id = str(item.get("title") or item.get("publish_time") or len(compact_json(item, 1000))).strip()
    return f"{platform_key}|{account_key}|内容|{content_id}"


def content_row(platform_key: str, account_key: str, account: dict[str, Any], item: dict[str, Any], content_id_key: str) -> dict[str, Any]:
    meta = PLATFORMS[platform_key]
    content_id = str(item.get(content_id_key) or item.get("id") or item.get("item_id") or "")
    return {
        "唯一键": content_identity(platform_key, account_key, item, content_id_key),
        "分类": "内容",
        "平台": meta["label"],
        "账号标识": account_key,
        "账号名称": account_name_from(account_key, account),
        "账号ID": account_id_from(platform_key, account),
        "内容ID": content_id,
        "标题": str(item.get("title") or ""),
        "发布时间": str(item.get("publish_time") or item.get("create_time") or ""),
        "播放/阅读量": str(first_value(item, ["views", "read_count", "play_count"])),
        "点赞数": str(first_value(item, ["likes", "like_count"])),
        "评论数": str(first_value(item, ["comments", "comment_count"])),
        "收藏数": str(first_value(item, ["collects", "collect_count"])),
        "分享数": str(first_value(item, ["shares", "share_count"])),
        "观看人数": "",
        "直播场次": "",
        "直播天数": "",
        "直播时长秒": "",
        "新增粉丝数": str(first_value(item, ["rise_fans", "new_fans_count"])),
        "回放数": "",
        "最近拉数时间": str(item.get("fetched_at") or ""),
        "原始数据": compact_json(item),
    }


def live_overview(platform_key: str, live: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(live, dict) or not live:
        return {}
    overview = live.get("overview") if isinstance(live.get("overview"), dict) else {}
    if platform_key == "douyin":
        return dict(overview)
    if platform_key == "wechat_video":
        return dict(overview)
    if platform_key == "xhs":
        return dict(overview or live)
    return dict(overview or live)


def live_row(platform_key: str, account_key: str, account: dict[str, Any], live: dict[str, Any], fetched_at: str) -> dict[str, Any] | None:
    overview = live_overview(platform_key, live)
    if not overview:
        return None
    meta = PLATFORMS[platform_key]
    watch_count = first_value(overview, ["watch_count", "watch_user_count", "观看人数", "live_watch_count"])
    live_count = first_value(overview, ["room_count", "live_history_count", "room_cnt"])
    live_day_count = first_value(overview, ["live_day_count", "live_day_cnt"])
    duration = first_value(overview, ["live_duration", "live_total_duration_seconds"])
    return {
        "唯一键": f"{platform_key}|{account_key}|直播|summary",
        "分类": "直播",
        "平台": meta["label"],
        "账号标识": account_key,
        "账号名称": account_name_from(account_key, account),
        "账号ID": account_id_from(platform_key, account),
        "内容ID": "live_summary",
        "标题": "直播汇总",
        "发布时间": fetched_at,
        "播放/阅读量": "",
        "点赞数": "",
        "评论数": str(first_value(overview, ["comment_count"])),
        "收藏数": "",
        "分享数": "",
        "观看人数": str(watch_count or ""),
        "直播场次": str(live_count or ""),
        "直播天数": str(live_day_count or ""),
        "直播时长秒": str(duration or ""),
        "新增粉丝数": str(first_value(overview, ["new_fans_count"])),
        "回放数": str(first_value(overview, ["replay_count"])),
        "最近拉数时间": fetched_at,
        "原始数据": compact_json(live),
    }


def build_content_rows() -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for platform_key, path in metrics_files():
        meta = PLATFORMS[platform_key]
        data = read_json(path, {})
        account_key = str(data.get("account_key") or path.stem.replace(str(meta["latest_prefix"]), "")).strip()
        account = dict(data.get("account") or {})
        fetched_at = str(data.get("fetched_at") or "")
        for item in data.get(str(meta["content_array"]), []) or []:
            if not isinstance(item, dict):
                continue
            row = content_row(platform_key, account_key, account, item, str(meta["content_id"]))
            rows[row["唯一键"]] = row
        live = data.get("live")
        if isinstance(live, dict):
            row = live_row(platform_key, account_key, account, live, fetched_at)
            if row:
                rows[row["唯一键"]] = row
    return list(rows.values())


def list_records(base_token: str, table_id: str) -> list[dict[str, Any]]:
    payload = run_cli(
        [
            "base",
            "+record-list",
            "--base-token",
            base_token,
            "--table-id",
            table_id,
            "--format",
            "json",
            "--as",
            "user",
        ]
    )
    data = payload.get("data", {})
    field_names = list(data.get("fields", []))
    record_ids = list(data.get("record_id_list", []))
    records: list[dict[str, Any]] = []
    for index, raw in enumerate(data.get("data", []) or []):
        fields: dict[str, Any] = {}
        if isinstance(raw, dict):
            fields = dict(raw)
        elif isinstance(raw, list):
            for pos, value in enumerate(raw):
                if pos < len(field_names):
                    fields[str(field_names[pos])] = value
        records.append(
            {
                "record_id": record_ids[index] if index < len(record_ids) else "",
                "fields": fields,
            }
        )
    return records


def record_index_by_key(base_token: str, table_id: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for record in list_records(base_token, table_id):
        record_id = str(record.get("record_id") or "").strip()
        key = as_text((record.get("fields") or {}).get("唯一键")).strip()
        if record_id and key:
            result[key] = record_id
    return result


def upsert_rows(base_token: str, table_id: str, rows: list[dict[str, Any]], fields: list[str], payload_name: str) -> dict[str, Any]:
    ensure_fields(base_token, table_id, fields)
    existing = record_index_by_key(base_token, table_id)
    created = 0
    updated = 0
    failed: list[str] = []

    for row in rows:
        key = str(row.get("唯一键") or "").strip()
        payload = {field: str(row.get(field, "")) for field in fields}
        payload_path = OUTPUTS / f"{payload_name}_{safe_name(key)}.json"
        write_json(payload_path, payload)
        args = [
            "base",
            "+record-upsert",
            "--base-token",
            base_token,
            "--table-id",
            table_id,
            "--json",
            f"@{payload_path.relative_to(ROOT)}",
            "--as",
            "user",
            "--format",
            "json",
        ]
        record_id = existing.get(key)
        if record_id:
            args.extend(["--record-id", record_id])
        try:
            run_cli(args)
            if record_id:
                updated += 1
            else:
                created += 1
        except Exception as exc:
            failed.append(f"{key}: {exc}")

    return {"created": created, "updated": updated, "failed": failed}


def sync_display(reason: str = "manual", accounts_only: bool = False) -> dict[str, Any]:
    config = ensure_feishu_display()
    account_rows = build_account_rows()
    content_rows = [] if accounts_only else build_content_rows()

    account_result = upsert_rows(
        str(config["account_base_token"]),
        str(config["account_table_id"]),
        account_rows,
        ACCOUNT_FIELDS,
        "feishu_account_row",
    )
    if accounts_only:
        content_result = {"created": 0, "updated": 0, "failed": [], "skipped": True}
    else:
        content_result = upsert_rows(
            str(config["content_base_token"]),
            str(config["content_table_id"]),
            content_rows,
            CONTENT_FIELDS,
            "feishu_content_row",
        )

    result = {
        "ok": not account_result["failed"] and not content_result["failed"],
        "reason": reason,
        "accounts_only": accounts_only,
        "wiki_name": WIKI_NAME,
        "space_id": config.get("space_id"),
        "account_base_url": config.get("account_url"),
        "content_base_url": config.get("content_url"),
        "account_rows": len(account_rows),
        "content_rows": len(content_rows),
        "account_result": account_result,
        "content_result": content_result,
        "time": now_text(),
    }
    write_json(SYNC_RESULT_FILE, result)
    return result


def status() -> dict[str, Any]:
    config = read_json(CONFIG_FILE, {})
    result = {
        "ok": True,
        "wiki_name": WIKI_NAME,
        "config_exists": CONFIG_FILE.exists(),
        "config": config,
        "last_sync": read_json(SYNC_RESULT_FILE, {}),
        "time": now_text(),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init")
    sync_parser = sub.add_parser("sync")
    sync_parser.add_argument("--reason", default="manual")
    sync_parser.add_argument("--accounts-only", action="store_true")
    sub.add_parser("status")

    args = parser.parse_args()
    if args.command == "init":
        print(json.dumps(ensure_feishu_display(), ensure_ascii=False))
        return 0
    if args.command == "sync":
        print(json.dumps(sync_display(reason=args.reason, accounts_only=args.accounts_only), ensure_ascii=False))
        return 0
    if args.command == "status":
        print(json.dumps(status(), ensure_ascii=False))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
