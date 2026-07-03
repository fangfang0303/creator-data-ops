import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
WORKDIR = ROOT / "work"
OUTPUTS = ROOT / "outputs"

sys.path.insert(0, str(WORKDIR))

import desktop_backend as backend  # type: ignore
import douyin_monitor  # type: ignore
import wechat_video_monitor as wechat_monitor  # type: ignore
import xhs_monitor  # type: ignore
from playwright._impl._errors import TargetClosedError  # type: ignore


PLATFORMS = {
    "xhs": {"label": "小红书", "module": xhs_monitor},
    "douyin": {"label": "抖音", "module": douyin_monitor},
    "wechat_video": {"label": "微信视频号", "module": wechat_monitor},
}

PENDING_ACCOUNTS_FILE = OUTPUTS / "pending_accounts.json"
SCHEDULE_PENDING_FILE = OUTPUTS / "creator_ops_schedule_pending.json"
SCHEDULES_FILE = OUTPUTS / "creator_ops_schedules.json"
STORAGE_CONFIG_FILE = OUTPUTS / "creator_ops_storage_config.json"
DEFAULT_SCHEDULE_TIME = "00:00"
DEFAULT_SCHEDULE_TEXT = "每天24点（00:00）"
SCHEDULE_TASK_PREFIX = "CodexCreatorFetch"


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


def storage_mode() -> str:
    config = read_json(STORAGE_CONFIG_FILE, {})
    mode = str(config.get("mode") or "").strip() if isinstance(config, dict) else ""
    if mode in {"local", "computer", "电脑", "本地"}:
        return "local"
    if mode in {"feishu", "飞书"}:
        return "feishu"
    # Keep the current project behavior: this workspace already uses Feishu as display.
    return "feishu"


def sync_feishu_display(reason: str, accounts_only: bool = False) -> dict[str, Any]:
    mode = storage_mode()
    try:
        if mode == "local":
            import local_data_store  # type: ignore

            result = local_data_store.sync_store(accounts_only=accounts_only)
            result.update({"reason": reason, "mode": "local"})
            return result

        import feishu_bridge  # type: ignore

        result = feishu_bridge.sync_display(reason=reason, accounts_only=accounts_only)
        result["mode"] = "feishu"
        return result
    except Exception as exc:
        result = {
            "ok": False,
            "reason": reason,
            "mode": mode,
            "message": str(exc),
            "time": now_text(),
        }
        write_json(OUTPUTS / "data_display_sync_error.json", result)
        return result


def detect_platform(text: str) -> str:
    raw = (text or "").strip()
    compact = raw.replace("：", "").replace(":", "").replace("，", "").replace(",", "").replace(" ", "")
    if "小红书" in raw or "小红书" in compact:
        return "xhs"
    if "微信视频号" in raw or "微信视频号" in compact or "视频号" in raw or "视频号" in compact:
        return "wechat_video"
    if "抖音" in raw or "抖音" in compact:
        return "douyin"
    if raw in {"xhs", "douyin", "wechat_video"}:
        return raw
    return ""


def require_platform(platform: str) -> str:
    platform_key = detect_platform(platform)
    if platform_key not in PLATFORMS:
        raise RuntimeError(f"不支持的平台：{platform}")
    return platform_key


def require_account(account: str) -> str:
    value = (account or "").strip()
    if not value:
        raise RuntimeError("账号不能为空")
    return value


def result_path(prefix: str, platform_key: str | None = None, account: str | None = None) -> Path:
    if platform_key and account:
        safe_account = account.replace("\\", "_").replace("/", "_").replace(" ", "_")
        return OUTPUTS / f"{prefix}_{platform_key}_{safe_account}.json"
    if platform_key:
        return OUTPUTS / f"{prefix}_{platform_key}.json"
    return OUTPUTS / f"{prefix}.json"


def storage_state_exists(platform_key: str, account: str) -> bool:
    module = PLATFORMS[platform_key]["module"]
    path = module.get_storage_state_path(account)
    if path.exists() and path.stat().st_size > 0:
        return True
    if platform_key == "douyin":
        state_dir = douyin_monitor.get_state_dir(account)
        markers = [
            state_dir / "Local State",
            state_dir / "Default" / "Preferences",
            state_dir / "Default" / "Cookies",
        ]
        return any(marker.exists() for marker in markers)
    if platform_key == "wechat_video":
        state_dir = wechat_monitor.get_state_dir(account)
        markers = [
            state_dir / "Local State",
            state_dir / "Default" / "Preferences",
            state_dir / "Default" / "Cookies",
        ]
        return any(marker.exists() for marker in markers)
    return False


def wait_for_storage_state(platform_key: str, account: str, timeout_seconds: int = 90) -> bool:
    deadline = datetime.now().timestamp() + timeout_seconds
    while datetime.now().timestamp() < deadline:
        if storage_state_exists(platform_key, account):
            return True
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", "Start-Sleep -Seconds 2"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            errors="ignore",
        )
    return storage_state_exists(platform_key, account)


def signal_login_complete(platform_key: str, account: str) -> None:
    module = PLATFORMS[platform_key]["module"]
    if not hasattr(module, "get_login_confirm_path"):
        return
    confirm_path = module.get_login_confirm_path(account)
    write_json(
        confirm_path,
        {
            "account": account,
            "confirmed_at": now_text(),
        },
    )


def safe_task_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_") or "default"


def task_name_for_scope(scope: dict[str, str]) -> str:
    kind = scope.get("kind", "all")
    if kind == "all":
        return "CodexMultiPlatformFetchDaily"
    platform_key = safe_task_component(scope.get("platform_key", ""))
    if kind == "platform":
        return f"{SCHEDULE_TASK_PREFIX}_{platform_key}_all"
    account = safe_task_component(scope.get("account", ""))
    return f"{SCHEDULE_TASK_PREFIX}_{platform_key}_{account}"


def task_command_for_scope(scope: dict[str, str]) -> str:
    runner = WORKDIR / "run_creator_ops_schedule.ps1"
    kind = scope.get("kind", "all")
    if kind == "all":
        return f'powershell.exe -ExecutionPolicy Bypass -File "{runner}" -Mode all'
    platform_key = scope.get("platform_key", "")
    if kind == "platform":
        return f'powershell.exe -ExecutionPolicy Bypass -File "{runner}" -Mode platform -Platform "{platform_key}"'
    account = scope.get("account", "")
    return f'powershell.exe -ExecutionPolicy Bypass -File "{runner}" -Mode account -Platform "{platform_key}" -Account "{account}"'


def normalize_schedule_time(value: str) -> str:
    if value == "24:00":
        return DEFAULT_SCHEDULE_TIME
    return value


def display_schedule_time(value: str) -> str:
    normalized = normalize_schedule_time(value)
    if normalized == DEFAULT_SCHEDULE_TIME:
        return DEFAULT_SCHEDULE_TEXT
    return f"每天{normalized}"


def parse_schedule_time(text: str) -> str:
    raw = (text or "").strip()
    compact = raw.replace(" ", "")
    if any(keyword in compact for keyword in ["凌晨24点", "每天24点", "24点", "零点", "0点", "00点"]):
        return "24:00"

    match = re.search(r"(\d{1,2})[:：](\d{2})", raw)
    if match:
        hh = int(match.group(1))
        mm = int(match.group(2))
        if hh == 24 and mm == 0:
            return "24:00"
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return f"{hh:02d}:{mm:02d}"
        return ""

    match = re.search(r"(\d{1,2})点(?:(\d{1,2})分?)?", raw)
    if match:
        hh = int(match.group(1))
        mm = int(match.group(2) or 0)
        if hh == 24 and mm == 0:
            return "24:00"
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return f"{hh:02d}:{mm:02d}"
    return ""


def ensure_default_schedule() -> dict[str, Any]:
    ok, message = backend.set_schedule_time(DEFAULT_SCHEDULE_TIME)
    return {
        "ok": bool(ok),
        "schedule_time": DEFAULT_SCHEDULE_TIME,
        "schedule_text": DEFAULT_SCHEDULE_TEXT,
        "message": message,
    }


def pending_accounts() -> list[dict[str, Any]]:
    data = read_json(PENDING_ACCOUNTS_FILE, [])
    return [item for item in data if isinstance(item, dict)]


def save_pending_accounts(items: list[dict[str, Any]]) -> None:
    write_json(PENDING_ACCOUNTS_FILE, items)


def clear_platform_pending_accounts(platform_key: str) -> None:
    items = pending_accounts()
    items = [item for item in items if str(item.get("platform_key") or "") != platform_key]
    save_pending_accounts(items)


def kill_platform_login_processes(platform_key: str) -> None:
    script_map = {
        "xhs": "xhs_monitor.py",
        "douyin": "douyin_monitor.py",
        "wechat_video": "wechat_video_monitor.py",
    }
    script_name = script_map.get(platform_key)
    if not script_name:
        return
    cmd = (
        "Get-CimInstance Win32_Process | "
        f"Where-Object {{ $_.Name -like 'python*' -and $_.CommandLine -like '*{script_name}*login*' }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
    )
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", cmd],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        errors="ignore",
    )


def next_pending_key(platform_key: str) -> str:
    prefix = f"{platform_key}_pending_"
    numbers = []

    def collect(value: Any) -> None:
        key = str(value or "")
        if key.startswith(prefix):
            try:
                numbers.append(int(key.replace(prefix, "")))
            except Exception:
                pass

    for item in pending_accounts():
        collect(item.get("account_key"))

    accounts_file_map = {
        "xhs": WORKDIR / "xhs_accounts.json",
        "douyin": WORKDIR / "douyin_accounts.json",
        "wechat_video": WORKDIR / "wechat_video_accounts.json",
    }
    accounts_path = accounts_file_map.get(platform_key)
    if accounts_path:
        accounts_data = read_json(accounts_path, {"accounts": []})
        accounts = accounts_data.get("accounts", []) if isinstance(accounts_data, dict) else []
        for item in accounts:
            if isinstance(item, dict):
                collect(item.get("name"))

    state_dir_map = {
        "douyin": WORKDIR / "douyin_state",
        "wechat_video": WORKDIR / "wechat_video_state",
    }
    state_root = state_dir_map.get(platform_key)
    if state_root and state_root.exists():
        for child in state_root.iterdir():
            collect(child.name)

    for path in OUTPUTS.glob(f"*{prefix}*"):
        collect(path.name)

    return f"{prefix}{max(numbers, default=0) + 1}"


def create_pending_account(platform: str) -> dict[str, Any]:
    platform_key = require_platform(platform)
    platform_label = PLATFORMS[platform_key]["label"]
    kill_platform_login_processes(platform_key)
    clear_platform_pending_accounts(platform_key)
    account_key = next_pending_key(platform_key)

    backend.upsert_account(platform_key, account_key, "对话入口新增账号")
    ok, login_message = backend.launch_login_window(platform_key, account_key)

    items = pending_accounts()
    items.append(
        {
            "platform_key": platform_key,
            "platform_label": platform_label,
            "account_key": account_key,
            "created_at": now_text(),
            "status": "pending_login",
        }
    )
    save_pending_accounts(items)

    if platform_key == "douyin":
        close_hint = "登录完成后，请先关闭刚才那个抖音浏览器窗口，再回复“抖音 登录完成”。"
    elif platform_key == "wechat_video":
        close_hint = "登录完成后，请先关闭刚才那个视频号浏览器窗口，再回复“微信视频号 登录完成”。"
    else:
        close_hint = "登录完成后，直接回复“小红书 登录完成”就行。"

    result = {
        "ok": bool(ok),
        "action": "add_account",
        "platform": platform_label,
        "platform_key": platform_key,
        "account": account_key,
        "message": login_message,
        "reply_text": (
            f"已为你创建一个新的{platform_label}接入位，系统正在帮你打开{platform_label}登录页。"
            f"{close_hint}"
            "后面我会在首次拉数后，自动拿到这个账号的真实账号名和账号信息。"
        ),
        "time": now_text(),
    }
    write_json(result_path("creator_ops_add", platform_key, account_key), result)
    result["feishu_sync"] = sync_feishu_display(f"add-account:{platform_key}:{account_key}", accounts_only=True)
    write_json(result_path("creator_ops_add", platform_key, account_key), result)
    return result


def latest_pending_account(platform: str) -> dict[str, Any] | None:
    platform_key = require_platform(platform)
    items = pending_accounts()
    candidates = [item for item in items if str(item.get("platform_key")) == platform_key]
    if not candidates:
        return None
    candidates.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return candidates[0]


def try_extract_real_account_info(platform_key: str, account_key: str) -> dict[str, str]:
    info = {"account_name": "", "account_id": ""}
    if platform_key == "xhs":
        summary = read_json(OUTPUTS / f"xhs_metrics_latest_{account_key}.json", {})
        account = summary.get("account", {}) if isinstance(summary, dict) else {}
        info["account_name"] = str(account.get("name") or "")
        info["account_id"] = str(account.get("account_id") or "")
    elif platform_key == "douyin":
        summary = read_json(OUTPUTS / f"douyin_metrics_latest_{account_key}.json", {})
        account = summary.get("account", {}) if isinstance(summary, dict) else {}
        info["account_name"] = str(account.get("display_name") or account.get("name") or "")
        info["account_id"] = str(account.get("douyin_id") or "")
    elif platform_key == "wechat_video":
        summary = read_json(OUTPUTS / f"wechat_video_metrics_latest_{account_key}.json", {})
        account = summary.get("account", {}) if isinstance(summary, dict) else {}
        info["account_name"] = str(account.get("name") or "")
        info["account_id"] = str(account.get("channel_id") or "")
    return info


def confirm_latest_login_and_fetch(platform: str) -> dict[str, Any]:
    pending = latest_pending_account(platform)
    platform_key = require_platform(platform)
    platform_label = PLATFORMS[platform_key]["label"]
    if not pending:
        result = {
            "ok": False,
            "action": "confirm_login",
            "platform": platform_label,
            "message": f"当前没有待确认的{platform_label}新增账号。",
            "reply_text": f"当前没有待确认的{platform_label}新增账号。请先说“新增{platform_label}”。",
            "time": now_text(),
        }
        write_json(result_path("creator_ops_confirm_missing", platform_key), result)
        return result

    account_key = str(pending.get("account_key") or "")
    signal_login_complete(platform_key, account_key)
    if not wait_for_storage_state(platform_key, account_key, timeout_seconds=90):
        result = {
            "ok": False,
            "action": "confirm_login",
            "platform": platform_label,
            "platform_key": platform_key,
            "account": account_key,
            "message": "还没有检测到可用登录态，请先完成登录。",
            "reply_text": f"{platform_label} 还没检测到登录完成。请确认你已经进入{platform_label}后台，再回复“{platform_label} 登录完成”。",
            "time": now_text(),
        }
        write_json(result_path("creator_ops_confirm", platform_key, account_key), result)
        return result

    module = PLATFORMS[platform_key]["module"]
    try:
        module.run_fetch(account_key)
    except TargetClosedError:
        result = {
            "ok": False,
            "action": "confirm_login",
            "platform": platform_label,
            "platform_key": platform_key,
            "account": account_key,
            "message": "浏览器窗口仍在占用登录目录，暂时无法开始首次抓数。",
            "reply_text": f"{platform_label} 登录态已经保存，但浏览器窗口还没完全释放。请先关闭刚才的浏览器窗口，再回复“{platform_label} 登录完成”。",
            "time": now_text(),
        }
        write_json(result_path("creator_ops_confirm", platform_key, account_key), result)
        return result
    except Exception as exc:
        result = {
            "ok": False,
            "action": "confirm_login",
            "platform": platform_label,
            "platform_key": platform_key,
            "account": account_key,
            "message": str(exc),
            "reply_text": f"{platform_label} 首次拉数失败：{exc}",
            "time": now_text(),
        }
        write_json(result_path("creator_ops_confirm", platform_key, account_key), result)
        return result

    real_info = try_extract_real_account_info(platform_key, account_key)

    items = pending_accounts()
    for item in items:
        if str(item.get("platform_key")) == platform_key and str(item.get("account_key")) == account_key:
            item["status"] = "completed"
            item["completed_at"] = now_text()
            item["account_name"] = real_info["account_name"]
            item["account_id"] = real_info["account_id"]
            break
    save_pending_accounts(items)

    schedule = ensure_default_schedule()
    schedule_text = (
        f"后续会在{DEFAULT_SCHEDULE_TEXT}自动刷新所有平台所有账号数据。"
        if schedule["ok"]
        else f"定时任务暂时没有设置成功：{schedule['message']}"
    )

    result = {
        "ok": True,
        "action": "confirm_login_and_fetch",
        "platform": platform_label,
        "platform_key": platform_key,
        "account": account_key,
        "account_name": real_info["account_name"],
        "account_id": real_info["account_id"],
        "message": "已检测到登录态，并已完成首次真实抓数。",
        "reply_text": f"{platform_label} 新账号已登录成功，并已完成首次抓数。账号名称：{real_info['account_name'] or '暂未识别'}；账号ID：{real_info['account_id'] or '暂未识别'}。{schedule_text}",
        "schedule": schedule,
        "time": now_text(),
    }
    result["feishu_sync"] = sync_feishu_display(f"confirm-login:{platform_key}:{account_key}")
    write_json(result_path("creator_ops_confirm", platform_key, account_key), result)
    return result


def manual_fetch(platform: str, account: str) -> dict[str, Any]:
    platform_key = require_platform(platform)
    account_key = require_account(account)
    platform_label = PLATFORMS[platform_key]["label"]
    module = PLATFORMS[platform_key]["module"]

    if not storage_state_exists(platform_key, account_key):
        result = {
            "ok": False,
            "action": "manual_fetch",
            "platform": platform_label,
            "platform_key": platform_key,
            "account": account_key,
            "message": "该账号还没有可用登录态，不能手动抓数。",
            "reply_text": f"{platform_label} 账号 {account_key} 还没登录成功，暂时不能抓数。请先登录。",
            "time": now_text(),
        }
        write_json(result_path("creator_ops_manual_fetch", platform_key, account_key), result)
        return result

    module.run_fetch(account_key)
    result = {
        "ok": True,
        "action": "manual_fetch",
        "platform": platform_label,
        "platform_key": platform_key,
        "account": account_key,
        "message": "该账号已完成一次手动抓数。",
        "reply_text": f"{platform_label} 账号 {account_key} 已完成一次手动抓数。",
        "time": now_text(),
    }
    result["feishu_sync"] = sync_feishu_display(f"manual-fetch:{platform_key}:{account_key}")
    write_json(result_path("creator_ops_manual_fetch", platform_key, account_key), result)
    return result


def fetch_platform(platform: str) -> dict[str, Any]:
    platform_key = require_platform(platform)
    platform_label = PLATFORMS[platform_key]["label"]
    module = PLATFORMS[platform_key]["module"]
    module.run_fetch_all()
    result = {
        "ok": True,
        "action": "fetch_platform",
        "platform": platform_label,
        "platform_key": platform_key,
        "message": f"{platform_label} 平台已完成全部账号抓数。",
        "reply_text": f"{platform_label} 平台全部账号已经完成本轮抓数。",
        "time": now_text(),
    }
    result["feishu_sync"] = sync_feishu_display(f"fetch-platform:{platform_key}")
    write_json(result_path("creator_ops_fetch_platform", platform_key), result)
    return result


def enabled_accounts(platform_key: str) -> list[str]:
    accounts_file_map = {
        "xhs": WORKDIR / "xhs_accounts.json",
        "douyin": WORKDIR / "douyin_accounts.json",
        "wechat_video": WORKDIR / "wechat_video_accounts.json",
    }
    accounts_path = accounts_file_map.get(platform_key)
    if not accounts_path:
        return []
    data = read_json(accounts_path, {"accounts": []})
    accounts = data.get("accounts", []) if isinstance(data, dict) else []
    names: list[str] = []
    seen: set[str] = set()
    for item in accounts:
        if not isinstance(item, dict):
            continue
        if item.get("enabled", True) is False:
            continue
        name = str(item.get("name") or "").strip()
        if name and name not in seen:
            names.append(name)
            seen.add(name)
    return names


def known_account_options(platform_key: str) -> list[dict[str, Any]]:
    options: dict[str, dict[str, Any]] = {}
    for account in enabled_accounts(platform_key):
        options[account] = {
            "account": account,
            "account_name": "",
            "account_id": "",
            "aliases": {account},
        }

    for item in pending_accounts():
        if str(item.get("platform_key") or "") != platform_key:
            continue
        account = str(item.get("account_key") or "")
        if not account:
            continue
        option = options.setdefault(
            account,
            {
                "account": account,
                "account_name": "",
                "account_id": "",
                "aliases": {account},
            },
        )
        for key in ("account_name", "account_id"):
            value = str(item.get(key) or "").strip()
            if value:
                option[key] = value
                option["aliases"].add(value)

    for account, option in list(options.items()):
        info = try_extract_real_account_info(platform_key, account)
        for key in ("account_name", "account_id"):
            value = str(info.get(key) or "").strip()
            if value:
                option[key] = value
                option["aliases"].add(value)
    return list(options.values())


def describe_scope(scope: dict[str, str]) -> str:
    kind = scope.get("kind", "all")
    if kind == "all":
        return "所有平台的所有账号"
    platform_label = str(PLATFORMS.get(scope.get("platform_key", ""), {}).get("label", scope.get("platform_key", "")))
    if kind == "platform":
        return f"{platform_label}的所有账号"
    account = scope.get("account", "")
    return f"{platform_label}账号 {account}"


def schedule_scope_options_text() -> str:
    lines = ["请选择要修改的范围：", "1. 所有平台的所有账号"]
    index = 2
    for platform_key, meta in PLATFORMS.items():
        platform_label = str(meta["label"])
        lines.append(f"{index}. {platform_label}的所有账号")
        index += 1
        account_labels = []
        for option in known_account_options(platform_key):
            label = option.get("account_name") or option.get("account")
            account_id = option.get("account_id") or ""
            if account_id:
                label = f"{label} / {account_id}"
            account_labels.append(str(label))
        if account_labels:
            lines.append(f"{platform_label}单账号：{'; '.join(account_labels)}")
    return "\n".join(lines)


def parse_schedule_scope(text: str) -> dict[str, str] | None:
    raw = text or ""
    compact = raw.replace(" ", "").replace("，", "").replace(",", "").replace("：", "").replace(":", "")
    all_markers = ["所有平台所有账号", "全部平台全部账号", "所有平台", "全部平台", "全平台"]
    if any(marker in compact for marker in all_markers):
        return {"kind": "all"}

    platform_key = detect_platform(raw)
    if not platform_key:
        return None

    account_options = known_account_options(platform_key)
    for option in account_options:
        aliases = sorted(option.get("aliases", set()), key=len, reverse=True)
        for alias in aliases:
            if alias and alias in raw:
                return {"kind": "account", "platform_key": platform_key, "account": str(option["account"])}

    if any(keyword in compact for keyword in ["全部账号", "所有账号", "平台账号", "整个账号", "全账号"]):
        return {"kind": "platform", "platform_key": platform_key}

    return None


def fetch_all_platforms() -> dict[str, Any]:
    platform_results: list[dict[str, Any]] = []
    ok_count = 0
    failed_count = 0
    skipped_count = 0

    for platform_key, meta in PLATFORMS.items():
        platform_label = str(meta["label"])
        module = meta["module"]
        account_results: list[dict[str, Any]] = []
        for account in enabled_accounts(platform_key):
            if not storage_state_exists(platform_key, account):
                skipped_count += 1
                account_results.append(
                    {
                        "account": account,
                        "status": "skipped",
                        "message": "该账号还没有可用登录态，已跳过。",
                    }
                )
                continue
            try:
                module.run_fetch(account)
                ok_count += 1
                info = try_extract_real_account_info(platform_key, account)
                account_results.append(
                    {
                        "account": account,
                        "status": "success",
                        "account_name": info.get("account_name", ""),
                        "account_id": info.get("account_id", ""),
                        "message": "已完成抓取。",
                    }
                )
            except Exception as exc:
                failed_count += 1
                account_results.append(
                    {
                        "account": account,
                        "status": "failed",
                        "message": str(exc),
                    }
                )

        platform_status = "success"
        if any(item["status"] == "failed" for item in account_results):
            platform_status = "partial_failed"
        elif any(item["status"] == "success" for item in account_results):
            platform_status = "success"
        elif account_results:
            platform_status = "skipped"
        else:
            platform_status = "empty"

        platform_results.append(
            {
                "platform": platform_label,
                "platform_key": platform_key,
                "status": platform_status,
                "accounts": account_results,
            }
        )

    result = {
        "ok": failed_count == 0,
        "action": "fetch_all_platforms",
        "message": f"全平台抓取完成：成功 {ok_count} 个账号，失败 {failed_count} 个账号，跳过 {skipped_count} 个账号。",
        "reply_text": f"全平台数据抓取完成：成功 {ok_count} 个账号，失败 {failed_count} 个账号，跳过 {skipped_count} 个未登录账号。",
        "ok_count": ok_count,
        "failed_count": failed_count,
        "skipped_count": skipped_count,
        "platforms": platform_results,
        "time": now_text(),
    }
    result["feishu_sync"] = sync_feishu_display("fetch-all-platforms")
    write_json(OUTPUTS / "creator_ops_fetch_all_platforms.json", result)
    return result


def set_schedule(schedule_time: str) -> dict[str, Any]:
    value = (schedule_time or "").strip()
    ok, message = backend.set_schedule_time(value)
    result = {
        "ok": bool(ok),
        "action": "set_schedule",
        "schedule_time": value,
        "message": message,
        "reply_text": message,
        "time": now_text(),
    }
    write_json(OUTPUTS / "creator_ops_schedule.json", result)
    return result


def set_scoped_schedule(schedule_time: str, scope: dict[str, str]) -> dict[str, Any]:
    normalized_time = normalize_schedule_time(schedule_time)
    if not normalized_time:
        raise RuntimeError("时间不能为空")
    if len(normalized_time) != 5 or normalized_time[2] != ":":
        raise RuntimeError("时间格式不正确，请使用 09:00 或 24:00")
    hh = int(normalized_time[:2])
    mm = int(normalized_time[3:])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise RuntimeError("时间超出范围")

    task_name = task_name_for_scope(scope)
    task_command = task_command_for_scope(scope)
    existing = subprocess.run(
        ["schtasks", "/Query", "/TN", task_name],
        capture_output=True,
        text=True,
        errors="ignore",
    )
    if existing.returncode == 0:
        changed = subprocess.run(
            ["schtasks", "/Change", "/TN", task_name, "/ST", normalized_time],
            capture_output=True,
            text=True,
            errors="ignore",
        )
        ok = changed.returncode == 0
        message = "修改成功" if ok else (changed.stderr.strip() or changed.stdout.strip() or "修改失败")
    else:
        created = subprocess.run(
            [
                "schtasks",
                "/Create",
                "/SC",
                "DAILY",
                "/TN",
                task_name,
                "/TR",
                task_command,
                "/ST",
                normalized_time,
                "/F",
            ],
            capture_output=True,
            text=True,
            errors="ignore",
        )
        ok = created.returncode == 0
        message = "创建成功" if ok else (created.stderr.strip() or created.stdout.strip() or "创建失败")

    schedule_record = {
        "ok": bool(ok),
        "action": "set_scoped_schedule",
        "task_name": task_name,
        "scope": scope,
        "scope_text": describe_scope(scope),
        "schedule_time": schedule_time,
        "normalized_time": normalized_time,
        "schedule_text": display_schedule_time(schedule_time),
        "message": message,
        "time": now_text(),
    }
    schedules = read_json(SCHEDULES_FILE, [])
    schedules = [item for item in schedules if isinstance(item, dict) and item.get("task_name") != task_name]
    schedules.append(schedule_record)
    write_json(SCHEDULES_FILE, schedules)
    write_json(OUTPUTS / "creator_ops_schedule.json", schedule_record)

    if not ok:
        schedule_record["reply_text"] = f"定时任务设置失败：{message}"
    else:
        schedule_record["reply_text"] = f"已设置{describe_scope(scope)}在{display_schedule_time(schedule_time)}自动抓取数据。"
    return schedule_record


def ask_schedule_detail(schedule_time: str | None, scope: dict[str, str] | None) -> dict[str, Any]:
    pending = {
        "action": "schedule_change_pending",
        "schedule_time": schedule_time or "",
        "scope": scope or {},
        "created_at": now_text(),
    }
    write_json(SCHEDULE_PENDING_FILE, pending)

    missing = []
    if not schedule_time:
        missing.append("时间")
    if not scope:
        missing.append("范围")

    if not schedule_time and not scope:
        reply = "你想把定时任务改成几点？以及改哪个范围？\n" + schedule_scope_options_text()
    elif not schedule_time:
        reply = f"你要把{describe_scope(scope or {'kind': 'all'})}的定时任务改成几点？例如：24:00 或 09:30。"
    else:
        reply = f"已记住时间：{display_schedule_time(schedule_time)}。\n{schedule_scope_options_text()}"

    result = {
        "ok": False,
        "action": "ask_schedule_detail",
        "missing": missing,
        "schedule_time": schedule_time or "",
        "scope": scope or {},
        "message": "定时任务信息还不完整，需要继续确认。",
        "reply_text": reply,
        "time": now_text(),
    }
    write_json(OUTPUTS / "creator_ops_schedule_dialog.json", result)
    return result


def handle_schedule_dialog(raw: str) -> dict[str, Any]:
    pending = read_json(SCHEDULE_PENDING_FILE, {})
    schedule_time = parse_schedule_time(raw) or str(pending.get("schedule_time") or "")
    scope = parse_schedule_scope(raw) or (pending.get("scope") if isinstance(pending.get("scope"), dict) and pending.get("scope") else None)

    if not schedule_time or not scope:
        return ask_schedule_detail(schedule_time or None, scope)

    result = set_scoped_schedule(schedule_time, scope)
    if SCHEDULE_PENDING_FILE.exists():
        try:
            SCHEDULE_PENDING_FILE.unlink()
        except Exception:
            pass
    return result


def snapshot() -> dict[str, Any]:
    result = backend.dashboard_snapshot()
    result["generated_at"] = now_text()
    result["reply_text"] = "已生成当前多平台多账号状态快照。"
    write_json(OUTPUTS / "creator_ops_snapshot.json", result)
    return result


def skill_onboarding_intro() -> dict[str, Any]:
    result = {
        "ok": True,
        "action": "skill_onboarding_intro",
        "message": "已生成 skill 安装后的首次引导。",
        "reply_text": (
            "这个 skill 用来帮你统一管理小红书、抖音、微信视频号账号，自动抓账号数据、笔记/作品数据和直播数据。\n"
            "你可以直接说：新增小红书 / 新增抖音 / 新增微信视频号。\n"
            "想手动更新数据时，说：抓取所有平台数据。\n"
            f"默认会在{DEFAULT_SCHEDULE_TEXT}自动刷新数据；如果要改时间，说：定时改成 09:00，我会继续问你改全部账号还是某个平台/某个账号。\n\n"
            "现在先确认一件事：你的数据想存在哪里？请回复：电脑 或 飞书。"
        ),
        "time": now_text(),
    }
    write_json(OUTPUTS / "creator_ops_onboarding_intro.json", result)
    return result


def configure_storage(choice: str) -> dict[str, Any]:
    raw = (choice or "").strip()
    compact = raw.replace(" ", "")
    wants_local = any(keyword in compact for keyword in ["电脑", "本地", "本机", "csv", "CSV"])
    wants_feishu = "飞书" in compact or "授权完成" in compact
    if not wants_local and not wants_feishu:
        raise RuntimeError("请回复：电脑 或 飞书。")

    if wants_local:
        import local_data_store  # type: ignore

        config = local_data_store.sync_store()
        storage_config = {
            "mode": "local",
            "store_dir": config["store_dir"],
            "account_file": config["account_file"],
            "content_file": config["content_file"],
            "updated_at": now_text(),
        }
        write_json(STORAGE_CONFIG_FILE, storage_config)
        result = {
            "ok": True,
            "action": "configure_storage",
            "mode": "local",
            "storage": storage_config,
            "message": "已切换为电脑本地存储。",
            "reply_text": (
                "已设置为存到这台电脑，并创建好数据文件夹。\n"
                f"文件夹：{config['store_dir']}\n"
                f"账号数据：{config['account_file']}，用于记录平台、账号名称、粉丝数、获赞数、最近拉数时间等账号维度数据。\n"
                f"内容直播数据：{config['content_file']}，用于记录笔记/作品数据和直播数据，里面会用“分类”区分内容或直播。\n\n"
                "下一步可以直接说：新增小红书 / 新增抖音 / 新增微信视频号。"
            ),
            "time": now_text(),
        }
        write_json(OUTPUTS / "creator_ops_storage_choice.json", result)
        return result

    write_json(
        STORAGE_CONFIG_FILE,
        {
            "mode": "feishu",
            "updated_at": now_text(),
        },
    )
    try:
        import feishu_bridge  # type: ignore

        sync = feishu_bridge.sync_display(reason="storage-config-feishu")
        result = {
            "ok": True,
            "action": "configure_storage",
            "mode": "feishu",
            "storage": sync,
            "message": "已切换为飞书展示存储。",
            "reply_text": (
                "已设置为同步到飞书，并创建/更新好知识库和表格。\n"
                f"知识库：{sync.get('wiki_name', '自媒体运营')}\n"
                f"账号数据：{sync.get('account_base_url', '')}，用于记录平台账号、账号数据和状态。\n"
                f"内容／直播数据：{sync.get('content_base_url', '')}，用于记录笔记/作品数据和直播数据。\n\n"
                "下一步可以直接说：新增小红书 / 新增抖音 / 新增微信视频号。"
            ),
            "time": now_text(),
        }
    except Exception as exc:
        result = {
            "ok": False,
            "action": "configure_storage",
            "mode": "feishu",
            "message": str(exc),
            "reply_text": (
                "已选择飞书存储，但当前还需要先完成飞书授权。\n"
                "授权只用于后续把账号数据、内容数据和直播数据同步到你的飞书里。\n"
                "请先完成飞书授权；授权完成后回复：飞书授权完成。"
            ),
            "time": now_text(),
        }
    write_json(OUTPUTS / "creator_ops_storage_choice.json", result)
    return result


def help_text() -> dict[str, Any]:
    result = {
        "ok": True,
        "action": "help",
        "message": "可用对话指令已生成。",
        "reply_text": (
            "可直接这样说：\n"
            "0. 首次设置：开始使用，然后选择 电脑 或 飞书\n"
            "1. 新增账号：新增小红书 / 新增抖音 / 新增微信视频号\n"
            "2. 登录完成：小红书 登录完成\n"
            "3. 手动拉数：抖音 douyin2 拉数\n"
            "4. 平台全量拉数：微信视频号 平台拉数\n"
            "5. 全平台拉数：抓取所有平台数据\n"
            "6. 查看状态：查看状态\n"
            "7. 修改定时：定时改成 24:00，然后按提示选择所有平台、某个平台或某个账号"
        ),
        "time": now_text(),
    }
    write_json(OUTPUTS / "creator_ops_help.json", result)
    return result


def parse_chat_command(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise RuntimeError("请输入一句完整指令。")

    if raw in {"帮助", "help", "说明"}:
        return help_text()

    onboarding_keywords = ["开始使用", "初始化", "安装完成", "安装好了", "skill安装完成", "skill 安装完成"]
    if any(keyword in raw for keyword in onboarding_keywords):
        return skill_onboarding_intro()

    storage_choices = {"电脑", "本地", "本机", "飞书", "飞书授权完成", "授权完成"}
    storage_compact = raw.replace(" ", "")
    storage_intent = any(keyword in storage_compact for keyword in ["存电脑", "放电脑", "存在电脑", "存本地", "放本地", "存飞书", "放飞书", "存在飞书"])
    if raw in storage_choices or storage_compact in storage_choices or storage_intent:
        return configure_storage(raw)

    schedule_keywords = ["定时", "自动抓", "自动拉", "每天", "刷新时间", "抓取时间", "拉数时间"]
    if any(keyword in raw for keyword in schedule_keywords) or SCHEDULE_PENDING_FILE.exists():
        return handle_schedule_dialog(raw)

    if raw in {"查看状态", "状态", "看状态"}:
        return snapshot()

    compact_raw = raw.replace(" ", "").replace("，", "").replace(",", "").replace("：", "").replace(":", "")
    if any(
        keyword in compact_raw
        for keyword in [
            "抓取所有平台数据",
            "抓取全部平台数据",
            "抓取全平台数据",
            "所有平台拉数",
            "全部平台拉数",
            "全平台拉数",
            "全平台抓数",
            "全平台抓取",
        ]
    ):
        return fetch_all_platforms()

    action = ""
    if any(keyword in raw for keyword in ["新增小红书", "新增抖音", "新增微信视频号", "新增视频号", "新增账号", "添加账号", "增加账号"]):
        action = "add_account"
    elif any(keyword in raw for keyword in ["登录完成", "已登录完成", "确认登录"]):
        action = "confirm_login"
    elif any(keyword in raw for keyword in ["平台拉数", "平台抓数"]):
        action = "fetch_platform"
    elif any(keyword in raw for keyword in ["手动拉数", "拉数", "抓数", "抓取"]):
        action = "manual_fetch"

    platform_key = detect_platform(raw)
    if not platform_key:
        raise RuntimeError("没有识别到平台。请直接说：新增小红书 / 新增抖音 / 新增微信视频号。")

    if action == "add_account":
        return create_pending_account(platform_key)

    if action == "confirm_login":
        return confirm_latest_login_and_fetch(platform_key)

    if action == "fetch_platform":
        return fetch_platform(platform_key)

    if action == "manual_fetch":
        cleaned = raw.replace("：", " ").replace(":", " ").replace("，", " ").replace(",", " ")
        parts = [item for item in cleaned.split() if item]
        account = ""
        for part in parts:
            if detect_platform(part):
                continue
            if any(keyword in part for keyword in ["新增", "账号", "登录", "完成", "拉数", "抓数", "抓取", "平台"]):
                continue
            account = part
            break
        if not account:
            raise RuntimeError("手动拉数时，需要带上账号标识。")
        return manual_fetch(platform_key, account)

    raise RuntimeError("没有识别到动作。你可以先说“帮助”。")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    add_parser = sub.add_parser("add-account")
    add_parser.add_argument("--platform", required=True)

    confirm_parser = sub.add_parser("confirm-login")
    confirm_parser.add_argument("--platform", required=True)

    manual_parser = sub.add_parser("manual-fetch")
    manual_parser.add_argument("--platform", required=True)
    manual_parser.add_argument("--account", required=True)

    fetch_platform_parser = sub.add_parser("fetch-platform")
    fetch_platform_parser.add_argument("--platform", required=True)

    sub.add_parser("fetch-all-platforms")

    schedule_parser = sub.add_parser("set-schedule")
    schedule_parser.add_argument("--time", required=True)

    storage_parser = sub.add_parser("configure-storage")
    storage_parser.add_argument("--choice", required=True)

    chat_parser = sub.add_parser("chat")
    chat_parser.add_argument("--text", required=True)

    sub.add_parser("snapshot")
    sub.add_parser("help")
    sub.add_parser("onboarding")

    args = parser.parse_args()

    if args.command == "add-account":
        print(json.dumps(create_pending_account(args.platform), ensure_ascii=False))
        return 0
    if args.command == "confirm-login":
        print(json.dumps(confirm_latest_login_and_fetch(args.platform), ensure_ascii=False))
        return 0
    if args.command == "manual-fetch":
        print(json.dumps(manual_fetch(args.platform, args.account), ensure_ascii=False))
        return 0
    if args.command == "fetch-platform":
        print(json.dumps(fetch_platform(args.platform), ensure_ascii=False))
        return 0
    if args.command == "fetch-all-platforms":
        print(json.dumps(fetch_all_platforms(), ensure_ascii=False))
        return 0
    if args.command == "set-schedule":
        print(json.dumps(set_schedule(args.time), ensure_ascii=False))
        return 0
    if args.command == "configure-storage":
        print(json.dumps(configure_storage(args.choice), ensure_ascii=False))
        return 0
    if args.command == "snapshot":
        print(json.dumps(snapshot(), ensure_ascii=False))
        return 0
    if args.command == "help":
        print(json.dumps(help_text(), ensure_ascii=False))
        return 0
    if args.command == "onboarding":
        print(json.dumps(skill_onboarding_intro(), ensure_ascii=False))
        return 0
    if args.command == "chat":
        print(json.dumps(parse_chat_command(args.text), ensure_ascii=False))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

