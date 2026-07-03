import json
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent.parent
WORKDIR = ROOT / "work"
OUTPUTS = ROOT / "outputs"
TASK_NAME = "CodexMultiPlatformFetchDaily"
DEFAULT_SCHEDULE_TIME = "00:00"
DEFAULT_SCHEDULE_TEXT = "每天24点（00:00）"

sys.path.insert(0, str(WORKDIR))

import xhs_monitor  # type: ignore
import douyin_monitor  # type: ignore
import wechat_video_monitor  # type: ignore
import publish_automation  # type: ignore


PLATFORMS = {
    "xhs": {
        "label": "小红书",
        "accounts_file": WORKDIR / "xhs_accounts.json",
        "summary_file": OUTPUTS / "xhs_metrics_latest.json",
        "module": xhs_monitor,
        "content_count_keys": ["notes_count"],
    },
    "douyin": {
        "label": "抖音",
        "accounts_file": WORKDIR / "douyin_accounts.json",
        "summary_file": OUTPUTS / "douyin_metrics_latest.json",
        "module": douyin_monitor,
        "content_count_keys": ["videos_count"],
    },
    "wechat_video": {
        "label": "微信视频号",
        "accounts_file": WORKDIR / "wechat_video_accounts.json",
        "summary_file": OUTPUTS / "wechat_video_metrics_latest.json",
        "module": wechat_video_monitor,
        "content_count_keys": ["videos_count"],
    },
}

RUNTIME_LOCK = threading.Lock()
RUNTIME_EVENTS: list[dict[str, Any]] = []
RUNTIME_EVENTS_FILE = OUTPUTS / "desktop_runtime_events.json"
PUBLISH_TASKS_FILE = OUTPUTS / "publish_tasks.json"


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
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8-sig")


def publish_tasks() -> list[dict[str, Any]]:
    data = read_json(PUBLISH_TASKS_FILE, [])
    return [item for item in data if isinstance(item, dict)]


def save_publish_tasks(tasks: list[dict[str, Any]]) -> None:
    write_json(PUBLISH_TASKS_FILE, tasks)


def load_runtime_events_from_disk() -> list[dict[str, Any]]:
    data = read_json(RUNTIME_EVENTS_FILE, [])
    return [item for item in data if isinstance(item, dict)]


def save_runtime_events_to_disk(events: list[dict[str, Any]]) -> None:
    write_json(RUNTIME_EVENTS_FILE, events)


def load_accounts(platform_key: str) -> list[dict[str, Any]]:
    data = read_json(PLATFORMS[platform_key]["accounts_file"], {"accounts": []})
    return [item for item in data.get("accounts", []) if isinstance(item, dict)]


def save_accounts(platform_key: str, accounts: list[dict[str, Any]]) -> None:
    write_json(PLATFORMS[platform_key]["accounts_file"], {"accounts": accounts})


def enabled_account_names(platform_key: str) -> list[str]:
    return [
        str(item.get("name") or "").strip()
        for item in load_accounts(platform_key)
        if str(item.get("name") or "").strip() and item.get("enabled", True)
    ]


def upsert_account(platform_key: str, name: str, notes: str = "") -> bool:
    name = (name or "").strip()
    if not name:
        return False
    accounts = load_accounts(platform_key)
    if any((item.get("name") or "").strip() == name for item in accounts):
        return False
    accounts.append(
        {
            "name": name,
            "enabled": True,
            "login_mode": "manual",
            "notes": notes or "新增账号",
        }
    )
    save_accounts(platform_key, accounts)
    return True


def toggle_account(platform_key: str, account_name: str) -> bool:
    accounts = load_accounts(platform_key)
    changed = False
    for item in accounts:
        if (item.get("name") or "").strip() == account_name:
            item["enabled"] = not item.get("enabled", True)
            changed = True
            break
    if changed:
        save_accounts(platform_key, accounts)
    return changed


def latest_summary(platform_key: str) -> dict[str, Any]:
    return read_json(PLATFORMS[platform_key]["summary_file"], {})


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def push_event(platform_key: str, scope: str, account: str, action: str, status: str, message: str) -> None:
    with RUNTIME_LOCK:
        if not RUNTIME_EVENTS:
            RUNTIME_EVENTS.extend(load_runtime_events_from_disk())
        RUNTIME_EVENTS.insert(
            0,
            {
                "time": now_text(),
                "platform_key": platform_key,
                "platform_label": PLATFORMS.get(platform_key, {}).get("label", platform_key),
                "scope": scope,
                "account": account,
                "action": action,
                "status": status,
                "message": message,
            },
        )
        del RUNTIME_EVENTS[60:]
        save_runtime_events_to_disk(RUNTIME_EVENTS)


def runtime_events() -> list[dict[str, Any]]:
    with RUNTIME_LOCK:
        if not RUNTIME_EVENTS:
            RUNTIME_EVENTS.extend(load_runtime_events_from_disk())
        return list(RUNTIME_EVENTS)


def create_publish_task(
    title: str,
    body: str,
    asset_paths: list[str],
    targets: list[dict[str, str]],
    content_type: str,
) -> tuple[bool, str, str | None]:
    title = (title or "").strip()
    body = (body or "").strip()
    content_type = (content_type or "video").strip().lower()
    if content_type not in {"video", "image"}:
        return False, "内容类型只支持 video 或 image", None
    clean_assets = [str(item).strip() for item in asset_paths if str(item).strip()]
    if not clean_assets:
        return False, "请至少添加一个素材文件路径", None
    clean_targets: list[dict[str, str]] = []
    for target in targets:
        if not isinstance(target, dict):
            continue
        platform_key = str(target.get("platform_key") or "").strip()
        account_name = str(target.get("account_name") or "").strip()
        if not platform_key or not account_name:
            continue
        clean_targets.append({"platform_key": platform_key, "account_name": account_name})
    if not clean_targets:
        return False, "请至少选择一个发布目标账号", None

    task_id = f"pub_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    tasks = publish_tasks()
    task = {
        "task_id": task_id,
        "title": title,
        "body": body,
        "content_type": content_type,
        "asset_paths": clean_assets,
        "targets": clean_targets,
        "created_at": now_text(),
        "updated_at": now_text(),
        "status": "待执行",
        "runs": [],
    }
    tasks.insert(0, task)
    save_publish_tasks(tasks)
    return True, f"已创建发布任务：{task_id}", task_id


def update_publish_task_status(task_id: str, status: str, runs: list[dict[str, Any]] | None = None) -> None:
    tasks = publish_tasks()
    changed = False
    for task in tasks:
        if str(task.get("task_id")) != task_id:
            continue
        task["status"] = status
        task["updated_at"] = now_text()
        if runs is not None:
            task["runs"] = runs
        changed = True
        break
    if changed:
        save_publish_tasks(tasks)


def run_publish_task(task_id: str) -> tuple[bool, str]:
    tasks = publish_tasks()
    task = next((item for item in tasks if str(item.get("task_id")) == task_id), None)
    if not task:
        return False, "??????????"

    update_publish_task_status(task_id, "???")
    push_event("publish", "task", task_id, "????", "???", "????????????????")
    runs: list[dict[str, Any]] = []
    failed = 0
    for target in task.get("targets", []):
        platform_key = str(target.get("platform_key") or "")
        account_name = str(target.get("account_name") or "")
        try:
            command = [
                sys.executable,
                str(WORKDIR / "publish_automation.py"),
                "--platform",
                platform_key,
                "--account",
                account_name,
                "--content-type",
                str(task.get("content_type") or "video"),
                "--title",
                str(task.get("title") or ""),
                "--body",
                str(task.get("body") or ""),
                "--task-id",
                task_id,
            ]
            for asset_path in task.get("asset_paths", []):
                command.extend(["--asset", str(asset_path)])
            subprocess.Popen(
                command,
                cwd=str(WORKDIR),
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
            result = {
                "platform_key": platform_key,
                "account_key": account_name,
                "status": "opened",
                "message": "??????????????????????????",
                "run_time": now_text(),
            }
            runs.append(result)
            push_event(platform_key, "account", account_name, "????", "???", result["message"])
        except Exception as exc:
            failed += 1
            runs.append(
                {
                    "platform_key": platform_key,
                    "account_key": account_name,
                    "status": "failed",
                    "message": describe_exception(exc),
                    "run_time": now_text(),
                }
            )
            push_event(platform_key, "account", account_name, "????", "??", describe_exception(exc))
            print(f"Publish task failed for {platform_key}/{account_name}: {exc}", file=sys.stderr)

    final_status = "????" if failed else "??????"
    update_publish_task_status(task_id, final_status, runs)
    write_json(OUTPUTS / f"publish_task_debug_{task_id}.json", {"task": task, "runs": runs})
    push_event("publish", "task", task_id, "????", final_status, f"???????? {len(task.get('targets', []))} ??????")
    return True, f"??????????? {len(task.get('targets', []))} ??????"

def extract_content_total(platform_key: str) -> int:
    summary = latest_summary(platform_key)
    accounts = summary.get("accounts", [])
    total = 0
    if isinstance(accounts, list):
        for item in accounts:
            if not isinstance(item, dict):
                continue
            for key in PLATFORMS[platform_key]["content_count_keys"]:
                if item.get(key) is not None:
                    total += int(item.get(key) or 0)
                    break
    return total


def latest_account_fetch(platform_key: str, account: str | None) -> str:
    summary = latest_summary(platform_key)
    if not account:
        return str(summary.get("fetched_at") or "")
    for item in summary.get("accounts", []):
        if isinstance(item, dict) and str(item.get("account_key") or "") == account:
            return str(item.get("fetched_at") or "")
    return ""


def describe_exception(exc: Exception) -> str:
    raw = str(exc).strip() or exc.__class__.__name__
    lowered = raw.lower()
    if "edge browser not found" in lowered:
        return "未找到浏览器，请先确认 Edge 已安装。"
    if "valid login was not detected" in lowered:
        return "没有检测到有效登录，请重新登录后再试。"
    if "user doesn't login" in lowered or "doesn't login" in lowered:
        return "当前账号还没有真正登录成功，请重新登录。"
    if "douyin creator session is not logged in" in lowered:
        return "抖音当前读到的是登录页，不是创作者后台，请重新登录这个账号。"
    if "wechat channels session is not logged in" in lowered:
        return "视频号当前读到的是登录页，请重新登录这个账号。"
    if "no enabled" in lowered:
        return "当前平台没有启用中的账号。"
    if "could not find wechat channels video list items" in lowered:
        return "没有找到视频号内容列表，可能是页面结构变化、登录失效，或该页面暂时没有正确加载。"
    if "could not load the full note list" in lowered:
        return "没有完整读到小红书笔记列表，请重新抓取。"
    if "request failed" in lowered:
        return "平台接口请求失败，请稍后重试。"
    if "page.goto: timeout" in lowered or "timeout 30000ms exceeded" in lowered:
        return "页面打开超时，可能是网络慢、登录态失效，或平台页面响应异常。"
    if "timeout" in lowered:
        return "执行超时，请稍后重试。"
    if "format is invalid" in lowered:
        return "账号清单格式不正确，请检查配置。"
    return raw


def _run_single_action(
    platform_key: str,
    scope: str,
    account: str,
    action: str,
    func: Callable[..., Any],
    *args: Any,
) -> bool:
    started_at = datetime.now()
    before_total = extract_content_total(platform_key) if platform_key in PLATFORMS else 0
    before_fetch = latest_account_fetch(platform_key, args[0] if args else None) if platform_key in PLATFORMS else ""
    push_event(platform_key, scope, account, action, "进行中", "已经开始执行。这个动作可能需要 1 分钟左右。")
    try:
        func(*args)
        after_total = extract_content_total(platform_key) if platform_key in PLATFORMS else before_total
        after_fetch = latest_account_fetch(platform_key, args[0] if args else None) if platform_key in PLATFORMS else before_fetch
        delta = after_total - before_total
        elapsed = int((datetime.now() - started_at).total_seconds())
        parts = []
        if delta > 0:
            parts.append(f"内容新增 {delta} 条")
        elif delta == 0 and action != "登录":
            parts.append("内容总数没有变化")
        if after_fetch and after_fetch != before_fetch:
            parts.append(f"最近抓取更新到 {after_fetch}")
        parts.append(f"耗时 {elapsed} 秒")
        push_event(platform_key, scope, account, action, "完成", "；".join(parts))
        return True
    except Exception as exc:
        elapsed = int((datetime.now() - started_at).total_seconds())
        push_event(platform_key, scope, account, action, "失败", f"{describe_exception(exc)}（耗时 {elapsed} 秒）")
        print(f"Background task failed: {exc}", file=sys.stderr)
        return False


def _run_in_background(
    platform_key: str,
    scope: str,
    account: str,
    action: str,
    func: Callable[..., Any],
    *args: Any,
) -> None:
    def worker():
        _run_single_action(platform_key, scope, account, action, func, *args)

    threading.Thread(target=worker, daemon=True).start()


def _run_fetch_all_accounts(platform_key: str) -> None:
    names = enabled_account_names(platform_key)
    if not names:
        push_event(platform_key, "platform", "全部账号", "抓取全部账号", "失败", "当前平台没有启用中的账号。")
        return

    push_event(platform_key, "platform", "全部账号", "抓取全部账号", "进行中", f"准备依次抓取 {len(names)} 个账号。")
    success = 0
    failed = 0
    for name in names:
        ok = _run_single_action(platform_key, "account", name, "批量抓取", PLATFORMS[platform_key]["module"].run_fetch, name)
        if ok:
            success += 1
        else:
            failed += 1
    push_event(platform_key, "platform", "全部账号", "抓取全部账号", "完成", f"批量抓取完成：成功 {success} 个，失败 {failed} 个。")


def run_monitor(platform_key: str, mode: str, account: str | None = None) -> None:
    module = PLATFORMS[platform_key]["module"]
    account_label = account or "全部账号"
    if mode == "login":
        _run_in_background(platform_key, "account", account_label, "登录", module.run_login, account)
    elif mode == "fetch":
        _run_in_background(platform_key, "account", account_label, "手动抓取", module.run_fetch, account)
    else:
        threading.Thread(target=_run_fetch_all_accounts, args=(platform_key,), daemon=True).start()


def run_fetch_all_platforms() -> None:
    push_event("all", "all", "全部平台", "抓取全部平台", "进行中", "已经开始依次抓取全部平台。")
    for platform_key in PLATFORMS:
        run_monitor(platform_key, "fetch-all")


def query_schedule_time() -> str:
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"],
        capture_output=True,
        text=True,
        errors="ignore",
    )
    if result.returncode != 0:
        return "未设置"
    for line in result.stdout.splitlines():
        if line.strip().startswith("Start Time:"):
            return line.split(":", 1)[1].strip()
    return "已设置"


def set_schedule_time(value: str) -> tuple[bool, str]:
    value = (value or "").strip()
    if value == "24:00":
        value = DEFAULT_SCHEDULE_TIME
    if not value:
        return False, "时间不能为空"
    if len(value) != 5 or value[2] != ":":
        return False, "时间格式不正确，请使用 09:00 这种格式"
    try:
        hh = int(value[:2])
        mm = int(value[3:])
    except Exception:
        return False, "时间格式不正确，请使用 09:00 这种格式"
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return False, "时间超出范围"

    fetch_script = WORKDIR / "run_all_platforms_fetch.ps1"
    existing = subprocess.run(
        ["schtasks", "/Query", "/TN", TASK_NAME],
        capture_output=True,
        text=True,
        errors="ignore",
    )
    if existing.returncode == 0:
        changed = subprocess.run(
            ["schtasks", "/Change", "/TN", TASK_NAME, "/ST", value],
            capture_output=True,
            text=True,
            errors="ignore",
        )
        if changed.returncode != 0:
            return False, changed.stderr.strip() or changed.stdout.strip() or "修改失败"
        return True, f"已更新为{DEFAULT_SCHEDULE_TEXT}自动抓取" if value == DEFAULT_SCHEDULE_TIME else f"已更新为每天 {value} 自动抓取"

    created = subprocess.run(
        [
            "schtasks",
            "/Create",
            "/SC",
            "DAILY",
            "/TN",
            TASK_NAME,
            "/TR",
            f'powershell.exe -ExecutionPolicy Bypass -File "{fetch_script}"',
            "/ST",
            value,
            "/F",
        ],
        capture_output=True,
        text=True,
        errors="ignore",
    )
    if created.returncode != 0:
        return False, created.stderr.strip() or created.stdout.strip() or "创建失败"
    return True, f"已创建{DEFAULT_SCHEDULE_TEXT}自动抓取" if value == DEFAULT_SCHEDULE_TIME else f"已创建每天 {value} 自动抓取"


def launch_login_window(platform_key: str, account: str) -> tuple[bool, str]:
    account = (account or "").strip()
    if not account:
        return False, "请先选中账号。"
    script_map = {
        "xhs": WORKDIR / "run_xhs_login.ps1",
        "douyin": WORKDIR / "run_douyin_login.ps1",
        "wechat_video": WORKDIR / "run_wechat_video_login.ps1",
    }
    script_path = script_map.get(platform_key)
    if not script_path or not script_path.exists():
        return False, "没有找到登录入口脚本。"
    try:
        if platform_key in {"xhs", "douyin", "wechat_video"}:
            monitor_script_map = {
                "xhs": "xhs_monitor.py",
                "douyin": "douyin_monitor.py",
                "wechat_video": "wechat_video_monitor.py",
            }
            subprocess.Popen(
                [
                    r"C:\Users\surface\anaconda3\pythonw.exe",
                    str(WORKDIR / monitor_script_map[platform_key]),
                    "login",
                    "--account",
                    account,
                ],
                cwd=str(WORKDIR),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-WindowStyle",
                    "Hidden",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script_path),
                    account,
                ],
                cwd=str(WORKDIR),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        push_event(platform_key, "account", account, "登录", "已打开窗口", "登录窗口已经打开，请在浏览器里完成登录。")
        return True, "登录窗口已经打开，请在浏览器里完成登录。"
    except Exception as exc:
        return False, f"打开登录窗口失败：{exc}"


def account_rows(platform_key: str) -> list[dict[str, Any]]:
    summary = latest_summary(platform_key)
    accounts = load_accounts(platform_key)
    summary_accounts = {
        str(item.get("account_key") or ""): item
        for item in summary.get("accounts", [])
        if isinstance(item, dict)
    }
    rows: list[dict[str, Any]] = []
    for item in accounts:
        name = str(item.get("name") or "default")
        summary_item = summary_accounts.get(name, {})
        account_info = summary_item.get("account", {})
        content_count = 0
        for key in PLATFORMS[platform_key]["content_count_keys"]:
            if summary_item.get(key) is not None:
                content_count = summary_item.get(key) or 0
                break
        has_data = bool(summary_item)
        issue = ""
        if not item.get("enabled", True):
            issue = "账号已停用"
        elif not has_data:
            issue = "还没有抓到数据"
        elif content_count == 0:
            issue = "内容数为 0"
        rows.append(
            {
                "name": name,
                "enabled": bool(item.get("enabled", True)),
                "notes": str(item.get("notes") or ""),
                "display_name": account_info.get("name") or name,
                "content_count": content_count,
                "last_fetch": summary_item.get("fetched_at") or summary.get("fetched_at") or "",
                "issue": issue,
                "has_data": has_data,
            }
        )
    rows.sort(key=lambda x: (0 if x["issue"] else 1, 0 if x["enabled"] else 1, x["name"]))
    return rows


def dashboard_snapshot() -> dict[str, Any]:
    platforms = []
    for key, info in PLATFORMS.items():
        rows = account_rows(key)
        platforms.append(
            {
                "key": key,
                "label": info["label"],
                "accounts": rows,
                "accounts_count": len(rows),
                "enabled_count": sum(1 for row in rows if row["enabled"]),
                "issue_count": sum(1 for row in rows if row["issue"]),
                "content_total": sum(int(row["content_count"] or 0) for row in rows),
            }
        )
    return {
        "platforms": platforms,
        "schedule_time": query_schedule_time(),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "runtime_events": runtime_events(),
        "publish_tasks": publish_tasks(),
    }
