import argparse
import csv
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


WORKDIR = Path(__file__).resolve().parent
OUTPUT_DIR = WORKDIR.parent / "outputs"
STATE_ROOT = WORKDIR / "wechat_video_state"
DEFAULT_ACCOUNT = "default"
ACCOUNTS_CONFIG_PATH = WORKDIR / "wechat_video_accounts.json"
HOME_URL = "https://channels.weixin.qq.com/"
LOGIN_TARGET_URL = "https://channels.weixin.qq.com/platform/post/list"
POST_LIST_URL = "https://channels.weixin.qq.com/platform/post/list"
LIVE_HOME_URL = "https://channels.weixin.qq.com/platform/live/home"
EDGE_PATH = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")

SCAN_TEXT = "扫码"
LOGIN_TEXT = "登录"
CHANNEL_ID_LABEL = "视频号ID:"
FOLLOW_LABEL = "关注者"
VIDEO_COUNT_LABEL = "视频"
DATE_PATTERN = re.compile(r"^\d{4}年\d{1,2}月\d{1,2}日 \d{2}:\d{2}$")


@dataclass
class WechatVideoMetrics:
    fetched_at: str
    item_id: str
    title: str
    publish_time: str
    views: int
    likes: int
    comments: int
    shares: int
    collects: int


def ensure_dirs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_ROOT.mkdir(parents=True, exist_ok=True)


def normalize_account_name(account: str | None) -> str:
    value = (account or DEFAULT_ACCOUNT).strip()
    if not value:
        return DEFAULT_ACCOUNT
    return "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in value)


def get_state_dir(account: str) -> Path:
    return STATE_ROOT / normalize_account_name(account)


def get_json_path(account: str) -> Path:
    return OUTPUT_DIR / f"wechat_video_metrics_latest_{normalize_account_name(account)}.json"


def get_csv_path(account: str) -> Path:
    return OUTPUT_DIR / f"wechat_video_metrics_history_{normalize_account_name(account)}.csv"


def get_screenshot_path(account: str) -> Path:
    return OUTPUT_DIR / f"wechat_video_dashboard_{normalize_account_name(account)}.png"


def get_storage_state_path(account: str) -> Path:
    return get_state_dir(account) / "storage_state.json"


def get_login_confirm_path(account: str) -> Path:
    return OUTPUT_DIR / f"wechat_video_login_confirm_{normalize_account_name(account)}.flag"


def append_login_trace(account: str, message: str) -> None:
    trace_path = OUTPUT_DIR / f"wechat_video_login_trace_{normalize_account_name(account)}.log"
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n"
    try:
        with trace_path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except Exception:
        pass


def get_summary_path() -> Path:
    return OUTPUT_DIR / "wechat_video_metrics_latest.json"


def ensure_accounts_config() -> None:
    if ACCOUNTS_CONFIG_PATH.exists():
        return
    template = {
        "accounts": [
            {
                "name": "default",
                "enabled": True,
                "login_mode": "manual",
                "notes": "默认视频号账号，首次手动登录一次，后续复用登录状态。",
            }
        ]
    }
    ACCOUNTS_CONFIG_PATH.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8-sig")


def load_accounts_config() -> list[dict]:
    ensure_accounts_config()
    data = json.loads(ACCOUNTS_CONFIG_PATH.read_text(encoding="utf-8-sig"))
    accounts = data.get("accounts") or []
    if not isinstance(accounts, list):
        raise RuntimeError("wechat_video_accounts.json format is invalid.")
    return accounts


def get_enabled_accounts() -> list[str]:
    names: list[str] = []
    for item in load_accounts_config():
        if not isinstance(item, dict):
            continue
        if item.get("enabled", True) is False:
            continue
        name = normalize_account_name(str(item.get("name") or "").strip())
        if name:
            names.append(name)
    if not names:
        raise RuntimeError("No enabled WeChat Channels accounts found.")
    return names


def is_login_complete(page) -> bool:
    current_url = page.url
    if "channels.weixin.qq.com" not in current_url:
        return False
    if "login.html" in current_url:
        return False
    try:
        body_text = page.locator("body").first.inner_text(timeout=3000)
    except Exception:
        return False
    if SCAN_TEXT in body_text or LOGIN_TEXT in body_text:
        return False
    blocked_markers = [
        "一站式服务，让创作更简单",
        "加热平台",
        "机构管理",
        "联盟带货机构",
    ]
    if any(marker in body_text for marker in blocked_markers):
        return False
    return True


def wait_for_manual_login(page, account: str) -> None:
    page.goto(LOGIN_TARGET_URL, wait_until="domcontentloaded", timeout=60000)
    print("Browser opened. Complete the WeChat Channels login manually.")
    print("After login finishes, reply login complete in Codex.")

    confirm_path = get_login_confirm_path(account)
    append_login_trace(account, f"session:wait_for_user_confirm path={confirm_path}")
    deadline = time.time() + 600
    while time.time() < deadline:
        if confirm_path.exists():
            append_login_trace(account, "session:user_confirmed")
            return
        time.sleep(1)

    raise RuntimeError("WeChat Channels login confirmation timed out.")


def parse_int(value: str) -> int:
    raw = (value or "").strip()
    if not raw or raw == "-":
        return 0
    try:
        if raw.lower().endswith("w"):
            return int(float(raw[:-1]) * 10000)
        return int(raw.replace(",", ""))
    except Exception:
        return 0


def format_publish_time(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y年%m月%d日 %H:%M").isoformat(timespec="minutes")
    except Exception:
        return value


def extract_profile(page) -> dict:
    body_text = page.locator("html").first.inner_text(timeout=15000)
    lines = [line.strip() for line in body_text.splitlines() if line.strip()]
    profile = {
        "name": "",
        "channel_id": "",
        "video_count": 0,
        "followers_count": 0,
    }
    for idx, line in enumerate(lines):
        if line == CHANNEL_ID_LABEL and idx + 1 < len(lines):
            profile["channel_id"] = lines[idx + 1]
            if idx >= 2:
                profile["name"] = lines[idx - 2]
            elif idx > 0:
                profile["name"] = lines[idx - 1]
        if line.startswith(VIDEO_COUNT_LABEL):
            suffix = line.replace(VIDEO_COUNT_LABEL, "", 1).strip()
            if suffix.isdigit():
                profile["video_count"] = parse_int(suffix)
        if line.startswith(FOLLOW_LABEL):
            suffix = line.replace(FOLLOW_LABEL, "", 1).strip()
            if suffix.isdigit():
                profile["followers_count"] = parse_int(suffix)
    if not profile["name"]:
        for idx, line in enumerate(lines):
            if line == "通知中心" and idx + 1 < len(lines):
                profile["name"] = lines[idx + 1]
                break
    return profile


def get_metric_spans(item) -> list[int]:
    try:
        spans = item.locator("span.count")
        values = []
        for i in range(spans.count()):
            values.append(parse_int(spans.nth(i).inner_text(timeout=3000)))
        return values
    except Exception:
        return []


def get_item_text(item) -> str:
    try:
        return item.inner_text(timeout=5000)
    except Exception:
        return ""


def fetch_post_list(page, page_num: int = 1, page_size: int = 20) -> dict[str, Any]:
    result = post_json(
        page,
        "https://channels.weixin.qq.com/cgi-bin/mmfinderassistant-bin/post/post_list",
        {
            "pageNum": page_num,
            "pageSize": page_size,
            "onlyUnread": False,
        },
    )
    body = result.get("body", {})
    if not isinstance(body, dict) or body.get("errCode") != 0:
        raise RuntimeError(f"WeChat Channels post list request failed: {body}")
    data = body.get("data", {})
    if not isinstance(data, dict):
        raise RuntimeError("WeChat Channels post list response is invalid.")
    return data


def build_video_metrics_from_api(page) -> tuple[list[WechatVideoMetrics], dict]:
    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)
    if not is_login_complete(page):
        raise RuntimeError("WeChat Channels session is not logged in.")

    fetched_at = datetime.now().isoformat(timespec="seconds")
    profile = extract_profile(page)
    post_data = fetch_post_list(page, page_num=1, page_size=50)
    raw_items = post_data.get("list") or []

    videos: list[WechatVideoMetrics] = []
    for idx, item in enumerate(raw_items):
        if not isinstance(item, dict):
            continue
        desc = item.get("desc") or {}
        title = ""
        if isinstance(desc, dict):
            title = (
                str(desc.get("description") or "").strip()
                or str(desc.get("shortTitle") or "").strip()
            )
        create_time = item.get("createTime")
        publish_time = ""
        if create_time:
            try:
                publish_time = datetime.fromtimestamp(int(create_time)).isoformat(timespec="minutes")
            except Exception:
                publish_time = str(create_time)
        videos.append(
            WechatVideoMetrics(
                fetched_at=fetched_at,
                item_id=str(item.get("objectId") or item.get("exportId") or f"video-{idx + 1}"),
                title=title or f"视频 {idx + 1}",
                publish_time=publish_time,
                views=parse_int(str(item.get("readCount", 0))),
                likes=parse_int(str(item.get("likeCount", 0))),
                comments=parse_int(str(item.get("commentCount", 0))),
                shares=parse_int(str(item.get("forwardCount", 0))),
                collects=parse_int(str(item.get("favCount", 0))),
            )
        )

    videos.sort(key=lambda row: row.publish_time or "", reverse=True)
    if not profile.get("video_count"):
        profile["video_count"] = parse_int(str(post_data.get("totalCount", len(videos))))
    return videos, profile


def get_video_items(page):
    selectors = [
        ".post-feed-item",
        ".post-feed-item-wrap .post-feed-item",
        "[class*='post-feed-item']",
        "[class*='feed-item']",
    ]
    for _ in range(3):
        for selector in selectors:
            items = page.locator(selector)
            if items.count() > 0:
                return items
        page.wait_for_timeout(3000)

    try:
        page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        page.get_by_text("内容管理", exact=True).click(timeout=5000)
        page.wait_for_timeout(5000)
    except Exception:
        pass

    for selector in selectors:
        items = page.locator(selector)
        if items.count() > 0:
            return items
    return page.locator(".post-feed-item")


def open_content_management(page) -> None:
    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)
    if not is_login_complete(page):
        raise RuntimeError("WeChat Channels session is not logged in.")
    click_scripts = [
        """
() => {
  const nodes = Array.from(document.querySelectorAll('*'));
  const target = nodes.find(node => (node.textContent || '').trim() === '内容管理');
  if (target) { target.click(); return true; }
  return false;
}
""",
        """
() => {
  const candidates = Array.from(document.querySelectorAll('li, a, div, span'));
  const target = candidates.find(node => (node.textContent || '').includes('内容管理'));
  if (target) { target.click(); return true; }
  return false;
}
""",
    ]
    for script in click_scripts:
        try:
            if page.evaluate(script):
                page.wait_for_timeout(5000)
                return
        except Exception:
            continue
    try:
        page.goto(POST_LIST_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)
    except Exception:
        pass


def build_video_metrics(page) -> tuple[list[WechatVideoMetrics], dict]:
    try:
        return build_video_metrics_from_api(page)
    except Exception:
        pass
    open_content_management(page)
    items = get_video_items(page)
    count = items.count()
    if count <= 0:
        raise RuntimeError("Could not find WeChat Channels video list items.")

    fetched_at = datetime.now().isoformat(timespec="seconds")
    videos: list[WechatVideoMetrics] = []
    for idx in range(count):
        item = items.nth(idx)
        text = get_item_text(item)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        publish_line = next((line for line in lines if DATE_PATTERN.match(line)), "")
        metrics = get_metric_spans(item)
        while len(metrics) < 5:
            metrics.append(0)
        videos.append(
            WechatVideoMetrics(
                fetched_at=fetched_at,
                item_id=f"video-{idx + 1}",
                title=f"视频 {idx + 1}",
                publish_time=format_publish_time(publish_line),
                views=metrics[0],
                likes=metrics[1],
                comments=metrics[2],
                shares=metrics[3],
                collects=metrics[4],
            )
        )

    profile = extract_profile(page)
    return videos, profile


def post_json(page, url: str, payload: dict[str, Any]) -> dict[str, Any]:
    script = """
async ({ url, payload }) => {
  const response = await fetch(url, {
    method: 'POST',
    credentials: 'include',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload || {})
  });
  const text = await response.text();
  return { status: response.status, text };
}
"""
    result = page.evaluate(script, {"url": url, "payload": payload})
    text = result.get("text") or "{}"
    try:
        body = json.loads(text)
    except Exception:
        body = {"raw_text": text}
    return {"status": result.get("status", 0), "body": body}


def build_live_metrics(page) -> dict:
    page.goto(LIVE_HOME_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)
    if "login.html" in page.url or not is_login_complete(page):
        raise RuntimeError("WeChat Channels session is not logged in.")

    base = "https://channels.weixin.qq.com/micro/live/cgi-bin/mmfinderassistant-bin/live"
    history = post_json(
        page,
        f"{base}/get_live_history",
        {"pageSize": 10, "currentPage": 1, "reqType": 2},
    )
    history_abstract = post_json(
        page,
        f"{base}/get_live_history",
        {"pageSize": 3, "currentPage": 1, "reqType": 1},
    )
    replay_list = post_json(
        page,
        f"{base}/get_live_replay_list_v2",
        {"pageSize": 20, "currentPage": 1},
    )
    notice_list = post_json(page, f"{base}/live_notice_list", {})
    current_notice = post_json(page, f"{base}/current_live_notice", {})
    live_status = post_json(page, f"{base}/check_live_status", {})

    history_data = history.get("body", {}).get("data", {}) if isinstance(history.get("body"), dict) else {}
    history_abstract_data = (
        history_abstract.get("body", {}).get("data", {})
        if isinstance(history_abstract.get("body"), dict)
        else {}
    )
    replay_data = replay_list.get("body", {}).get("data", {}) if isinstance(replay_list.get("body"), dict) else {}
    notice_data = notice_list.get("body", {}).get("data", {}) if isinstance(notice_list.get("body"), dict) else {}
    current_notice_data = (
        current_notice.get("body", {}).get("data", {})
        if isinstance(current_notice.get("body"), dict)
        else {}
    )
    live_status_data = (
        live_status.get("body", {}).get("data", {})
        if isinstance(live_status.get("body"), dict)
        else {}
    )

    return {
        "overview": {
            "has_live_history": bool(history_data.get("totalLiveCount", 0)),
            "live_history_count": history_data.get("totalLiveCount", 0),
            "live_total_duration_seconds": history_data.get("totalLiveDuration", 0),
            "replay_count": replay_data.get("totalCount", 0),
            "notice_count": notice_data.get("totalCount", 0),
            "is_currently_live": live_status_data.get("status", 0) not in (0, "0", None),
            "current_live_status": live_status_data.get("status", 0),
        },
        "current_live": live_status_data,
        "current_notice": current_notice_data,
        "live_notices": notice_data.get("noticeList", []),
        "live_history_summary": {
            "total_live_count": history_abstract_data.get("totalLiveCount", 0),
            "total_live_duration_seconds": history_abstract_data.get("totalLiveDuration", 0),
            "recent_live_list": history_abstract_data.get("liveObjectList", []),
        },
        "live_history_list": history_data.get("liveObjectList", []),
        "replay_list": replay_data.get("replayObjects", []),
        "replay_settings": replay_data.get("replaySetting", {}),
    }


def save_results(videos: list[WechatVideoMetrics], profile: dict, live: dict, account: str) -> dict:
    payload = {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "account_key": normalize_account_name(account),
        "account": profile,
        "live": live,
        "videos_count": len(videos),
        "latest_video": asdict(videos[0]) if videos else None,
        "all_videos": [asdict(item) for item in videos],
    }
    get_json_path(account).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8-sig")

    csv_path = get_csv_path(account)
    file_exists = csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "fetched_at",
                "item_id",
                "title",
                "publish_time",
                "views",
                "likes",
                "comments",
                "shares",
                "collects",
            ],
        )
        if not file_exists:
            writer.writeheader()
        for item in videos:
            writer.writerow(asdict(item))
    return payload


def update_summary_file() -> None:
    payloads = []
    for path in sorted(OUTPUT_DIR.glob("wechat_video_metrics_latest_*.json")):
        try:
            payloads.append(json.loads(path.read_text(encoding="utf-8-sig")))
        except Exception:
            continue
    summary = {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "accounts_count": len(payloads),
        "accounts": payloads,
    }
    get_summary_path().write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8-sig")


def run_login(account: str) -> None:
    ensure_dirs()
    ensure_accounts_config()
    append_login_trace(account, "run_login:start")
    if not EDGE_PATH.exists():
        raise RuntimeError(f"Edge browser not found: {EDGE_PATH}")

    state_dir = get_state_dir(account)
    state_dir.mkdir(parents=True, exist_ok=True)
    storage_state_path = get_storage_state_path(account)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(EDGE_PATH),
            headless=False,
        )
        context = browser.new_context()
        page = context.new_page()
        try:
            append_login_trace(account, f"page:open_login_target:{LOGIN_TARGET_URL}")
            wait_for_manual_login(page, account)
            page.goto(LOGIN_TARGET_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
            if not is_login_complete(page):
                append_login_trace(account, "session:confirm_received_but_page_not_ready")
                raise RuntimeError("Current page is not the WeChat Channels dashboard.")
            append_login_trace(account, f"session:validated url={page.url}")
            append_login_trace(account, f"storage:save_begin path={storage_state_path}")
            context.storage_state(path=str(storage_state_path))
            append_login_trace(account, f"storage:save_done exists={storage_state_path.exists()}")
            print(f"WeChat Channels login state saved for account: {normalize_account_name(account)}")
        finally:
            append_login_trace(account, "browser:close_begin")
            context.close()
            browser.close()
            append_login_trace(account, "browser:close_done")


def run_fetch(account: str) -> None:
    ensure_dirs()
    if not EDGE_PATH.exists():
        raise RuntimeError(f"Edge browser not found: {EDGE_PATH}")

    state_dir = get_state_dir(account)
    screenshot_path = get_screenshot_path(account)
    storage_state_path = get_storage_state_path(account)
    with sync_playwright() as playwright:
        browser = None
        use_storage_state = storage_state_path.exists()
        if use_storage_state:
            browser = playwright.chromium.launch(
                executable_path=str(EDGE_PATH),
                headless=True,
            )
            context = browser.new_context(storage_state=str(storage_state_path))
        else:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(state_dir),
                executable_path=str(EDGE_PATH),
                headless=True,
            )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            if use_storage_state:
                page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(5000)
                if not is_login_complete(page):
                    raise RuntimeError("WeChat Channels session is not logged in.")
            videos, profile = build_video_metrics(page)
            live: dict[str, Any]
            try:
                live = build_live_metrics(page)
            except Exception as exc:
                live = {
                    "overview": {
                        "has_live_history": False,
                        "live_history_count": 0,
                        "live_total_duration_seconds": 0,
                        "replay_count": 0,
                        "notice_count": 0,
                        "is_currently_live": False,
                        "current_live_status": 0,
                    },
                    "fetch_error": str(exc),
                }
            page.screenshot(path=str(screenshot_path), full_page=True)
            save_results(videos, profile, live, account)
            update_summary_file()
            print(
                f"WeChat Channels fetch complete for {normalize_account_name(account)}. "
                f"Total videos: {len(videos)}."
            )
        finally:
            context.close()
            if browser is not None:
                browser.close()


def run_fetch_all() -> None:
    errors: list[str] = []
    for account in get_enabled_accounts():
        try:
            run_fetch(account)
        except Exception as exc:
            errors.append(f"{account}: {exc}")
    if errors:
        raise RuntimeError(" | ".join(errors))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["login", "fetch", "fetch-all"])
    parser.add_argument("--account", default=DEFAULT_ACCOUNT)
    args = parser.parse_args()

    try:
        if args.mode == "login":
            run_login(args.account)
        elif args.mode == "fetch":
            run_fetch(args.account)
        else:
            run_fetch_all()
        return 0
    except PlaywrightTimeoutError as exc:
        print(f"Page wait timed out: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
