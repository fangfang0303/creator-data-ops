import argparse
import csv
import hashlib
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


WORKDIR = Path(__file__).resolve().parent
OUTPUT_DIR = WORKDIR.parent / "outputs"
DEFAULT_ACCOUNT = "default"
ACCOUNTS_CONFIG_PATH = WORKDIR / "xhs_accounts.json"
HOME_URL = "https://creator.xiaohongshu.com/new/home?roleType=creator"
NOTE_MANAGER_URL = "https://creator.xiaohongshu.com/new/note-manager?roleType=creator"
EDGE_PATH = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


@dataclass
class NoteMetrics:
    fetched_at: str
    note_id: str
    title: str
    publish_time: str
    views: int
    likes: int
    collects: int
    comments: int
    shares: int
    rise_fans: int


def extract_home_dashboard_metrics(page) -> dict:
    try:
        body_text = page.locator("body").inner_text(timeout=15000)
    except Exception:
        return {}

    lines = [line.strip() for line in body_text.splitlines() if line.strip()]
    labels = [
        "曝光数",
        "观看数",
        "封面点击率",
        "视频完播率",
        "点赞数",
        "评论数",
        "收藏数",
        "分享数",
        "净涨粉",
        "新增关注",
        "取消关注",
        "主页访客",
    ]
    live_title = "直播数据总览"

    values = {}
    for idx, line in enumerate(lines):
        if line in labels and idx + 1 < len(lines):
            values[line] = lines[idx + 1]

    return {
        "content_overview": values,
        "live_overview": {
            "available": live_title in body_text,
            "raw_labels": values,
        },
    }


def decode_text(value: str) -> str:
    if not value:
        return ""
    try:
        return value.encode("latin1").decode("utf-8")
    except Exception:
        return value


def ensure_dirs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def normalize_account_name(account: str | None) -> str:
    value = (account or DEFAULT_ACCOUNT).strip()
    if not value:
        return DEFAULT_ACCOUNT
    clean = "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in value)
    if clean and clean != "_" * len(clean):
        return clean
    digest = hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"acct_{digest}"


def get_legacy_storage_state_path(account: str) -> Path:
    value = (account or DEFAULT_ACCOUNT).strip() or DEFAULT_ACCOUNT
    return OUTPUT_DIR / f"xhs_storage_state_{value}.json"


def get_storage_state_path(account: str) -> Path:
    expected_path = OUTPUT_DIR / f"xhs_storage_state_{normalize_account_name(account)}.json"
    legacy_path = get_legacy_storage_state_path(account)
    if expected_path.exists():
        return expected_path
    if legacy_path.exists() and legacy_path != expected_path:
        try:
            legacy_path.replace(expected_path)
        except Exception:
            return legacy_path
    return expected_path


def get_login_confirm_path(account: str) -> Path:
    return OUTPUT_DIR / f"xhs_login_confirm_{normalize_account_name(account)}.flag"


def get_json_path(account: str) -> Path:
    return OUTPUT_DIR / f"xhs_metrics_latest_{normalize_account_name(account)}.json"


def get_csv_path(account: str) -> Path:
    return OUTPUT_DIR / f"xhs_metrics_history_{normalize_account_name(account)}.csv"


def get_screenshot_path(account: str) -> Path:
    return OUTPUT_DIR / f"xhs_creator_dashboard_{normalize_account_name(account)}.png"


def get_login_debug_screenshot_path(account: str) -> Path:
    return OUTPUT_DIR / f"xhs_login_debug_{normalize_account_name(account)}.png"


def get_login_debug_text_path(account: str) -> Path:
    return OUTPUT_DIR / f"xhs_login_debug_{normalize_account_name(account)}.txt"


def get_summary_path() -> Path:
    return OUTPUT_DIR / "xhs_metrics_latest.json"


def ensure_accounts_config() -> None:
    if ACCOUNTS_CONFIG_PATH.exists():
        return

    template = {
        "accounts": [
            {
                "name": "default",
                "enabled": True,
                "login_mode": "manual",
                "notes": "默认账号。首次运行 login 时完成一次手动登录，后续复用登录态。",
            }
        ]
    }
    ACCOUNTS_CONFIG_PATH.write_text(
        json.dumps(template, ensure_ascii=False, indent=2),
        encoding="utf-8-sig",
    )


def load_accounts_config() -> list[dict]:
    ensure_accounts_config()
    raw = json.loads(ACCOUNTS_CONFIG_PATH.read_text(encoding="utf-8-sig"))
    accounts = raw.get("accounts") or []
    if not isinstance(accounts, list):
        raise RuntimeError("xhs_accounts.json 格式不正确。")
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
        raise RuntimeError("xhs_accounts.json 里没有可用账号。")
    return names


def upsert_account_config(account: str) -> None:
    ensure_accounts_config()
    account_key = normalize_account_name(account)
    raw = json.loads(ACCOUNTS_CONFIG_PATH.read_text(encoding="utf-8-sig"))
    accounts = raw.get("accounts") or []
    if not isinstance(accounts, list):
        accounts = []

    for item in accounts:
        if not isinstance(item, dict):
            continue
        name = normalize_account_name(str(item.get("name") or ""))
        if name == account_key:
            item["enabled"] = True
            if not item.get("login_mode"):
                item["login_mode"] = "manual"
            raw["accounts"] = accounts
            ACCOUNTS_CONFIG_PATH.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2),
                encoding="utf-8-sig",
            )
            return

    accounts.append(
        {
            "name": account_key,
            "enabled": True,
            "login_mode": "manual",
            "notes": "新增账号。首次运行 login 时完成一次手动登录，后续复用登录态。",
        }
    )
    raw["accounts"] = accounts
    ACCOUNTS_CONFIG_PATH.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8-sig",
    )


def is_login_complete(page) -> bool:
    current_url = page.url
    if (
        "creator.xiaohongshu.com" not in current_url
        or "creator.xiaohongshu.com/login" in current_url
        or "passport" in current_url
    ):
        return False

    try:
        page.locator("body").wait_for(timeout=3000)
        body_text = page.locator("body").inner_text()
    except Exception:
        return False

    if not body_text.strip():
        return False

    lowered = body_text.lower()
    markers = [
        "创作服务平台",
        "数据概览",
        "笔记管理",
        "数据看板",
        "创作中心",
        "发布笔记",
        "笔记数据总览",
        "直播数据总览",
        "粉丝数",
        "获赞与收藏",
        "鍒涗綔鏈嶅姟骞冲彴",
        "绗旇绠＄悊",
        "鏁版嵁鐪嬫澘",
        "鍙戝竷绗旇",
        "绗旇鏁版嵁鎬昏",
        "绮変笣鏁",
        "鑾疯禐涓庢敹钘",
    ]
    if any(marker in body_text for marker in markers):
        return True

    invalid_markers = [
        "登录后继续",
        "手机号登录",
        "验证码登录",
        "请先登录",
        "扫码登录",
        "立即登录",
    ]
    if any(marker in body_text for marker in invalid_markers):
        return False
    if "login" in lowered:
        return False
    return "/new/home" in current_url and bool(body_text.strip())


def open_creator_page(page, url: str) -> None:
    try:
        page.goto(url, wait_until="networkidle", timeout=30000)
        return
    except PlaywrightTimeoutError:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)


def fetch_json(page, endpoint: str, method: str = "GET", body: dict | None = None) -> dict:
    payload = json.dumps(body, ensure_ascii=False) if body is not None else None
    script = """
async ({ endpoint, method, payload }) => {
  const options = {
    method,
    credentials: 'include',
    headers: {},
  };
  if (payload !== null) {
    options.headers['content-type'] = 'application/json';
    options.body = payload;
  }
  const response = await fetch(endpoint, options);
  const text = await response.text();
  return { status: response.status, text };
}
"""
    result = page.evaluate(script, {"endpoint": endpoint, "method": method, "payload": payload})
    if result["status"] != 200:
        raise RuntimeError(f"Request failed: {endpoint} status={result['status']}")
    return json.loads(result["text"])


def validate_creator_session(page) -> dict:
    open_creator_page(page, HOME_URL)
    creator_home_loaded = (
        "creator.xiaohongshu.com/new/home" in page.url
        and "login" not in page.url.lower()
        and "passport" not in page.url.lower()
    )
    if not is_login_complete(page) and not creator_home_loaded:
        raise RuntimeError("Valid login was not detected. Finish login and try again.")
    try:
        return fetch_json(page, "/api/galaxy/creator/home/personal_info").get("data", {})
    except Exception:
        return {}


def validate_current_creator_session(page) -> dict:
    creator_home_loaded = (
        "creator.xiaohongshu.com/new/home" in page.url
        and "login" not in page.url.lower()
        and "passport" not in page.url.lower()
    )
    if not is_login_complete(page) and not creator_home_loaded:
        raise RuntimeError("Valid login was not detected. Finish login and try again.")
    try:
        return fetch_json(page, "/api/galaxy/creator/home/personal_info").get("data", {})
    except Exception:
        return {}


def capture_login_debug(page, account: str) -> None:
    try:
        page.screenshot(path=str(get_login_debug_screenshot_path(account)), full_page=True)
    except Exception:
        pass
    try:
        body_text = page.locator("body").inner_text(timeout=5000)
    except Exception:
        body_text = ""
    debug_text = [
        f"url: {page.url}",
        f"title: {page.title()}",
        "",
        body_text,
    ]
    try:
        get_login_debug_text_path(account).write_text("\n".join(debug_text), encoding="utf-8")
    except Exception:
        pass


def append_login_trace(account: str, message: str) -> None:
    trace_path = OUTPUT_DIR / f"xhs_login_trace_{normalize_account_name(account)}.log"
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n"
    try:
        with trace_path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except Exception:
        pass


def validate_creator_session_with_retry(
    page,
    account: str,
    timeout_seconds: int = 90,
    navigate_each_time: bool = True,
) -> dict:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            if navigate_each_time:
                return validate_creator_session(page)
            return validate_current_creator_session(page)
        except Exception as exc:
            last_error = exc
            time.sleep(3)
    capture_login_debug(page, account)
    if last_error is not None:
        raise RuntimeError(f"{last_error} | login_debug={get_login_debug_text_path(account).name}")
    raise RuntimeError("Valid login was not detected. Finish login and try again.")


def extract_note_page_payloads(page) -> list[dict]:
    payloads: dict[int, dict] = {}

    def on_response(response) -> None:
        url = response.url
        if "/api/galaxy/v2/creator/note/user/posted" not in url:
            return

        try:
            body = response.json()
        except Exception:
            return

        if not isinstance(body, dict):
            return

        data = body.get("data") or {}
        notes = data.get("notes") or []
        if not notes:
            return

        try:
            page_no = int(url.split("page=")[1].split("&")[0])
        except Exception:
            page_no = len(payloads)
        payloads[page_no] = body

    page.on("response", on_response)
    open_creator_page(page, NOTE_MANAGER_URL)
    page.wait_for_timeout(5000)

    no_change_rounds = 0
    previous_count = len(payloads)
    while no_change_rounds < 3:
        page.evaluate(
            """
() => {
  const els = Array.from(document.querySelectorAll('*'));
  let best = null;
  let bestScore = 0;
  for (const el of els) {
    const style = getComputedStyle(el);
    const sh = el.scrollHeight || 0;
    const ch = el.clientHeight || 0;
    if (sh <= ch + 20) continue;
    if (!/(auto|scroll)/.test(style.overflowY)) continue;
    const score = sh - ch;
    if (score > bestScore) {
      best = el;
      bestScore = score;
    }
  }
  if (best) {
    best.scrollTop = best.scrollHeight;
  }
  window.scrollTo(0, document.body.scrollHeight);
}
"""
        )
        page.wait_for_timeout(2000)
        current_count = len(payloads)
        if current_count == previous_count:
            no_change_rounds += 1
        else:
            no_change_rounds = 0
            previous_count = current_count

    page.remove_listener("response", on_response)
    return [payloads[key] for key in sorted(payloads.keys())]


def build_all_note_metrics(page) -> tuple[list[NoteMetrics], dict]:
    fetched_at = datetime.now().isoformat(timespec="seconds")
    profile = fetch_json(page, "/api/galaxy/creator/home/personal_info").get("data", {})
    payloads = extract_note_page_payloads(page)
    if not payloads:
        raise RuntimeError("Could not load the full note list from the note manager page.")

    notes_by_id: dict[str, NoteMetrics] = {}
    for payload in payloads:
        notes = payload.get("data", {}).get("notes") or []
        for item in notes:
            note_id = item.get("id")
            if not note_id:
                continue

            rise_fans = 0
            try:
                detail = fetch_json(page, f"/api/galaxy/creator/datacenter/note/base?note_id={note_id}")
                detail_data = detail.get("data") or {}
                rise_fans = int(detail_data.get("rise_fans_count", 0) or 0)
            except Exception:
                rise_fans = 0

            notes_by_id[note_id] = NoteMetrics(
                fetched_at=fetched_at,
                note_id=note_id,
                title=decode_text(item.get("display_title", "")),
                publish_time=item.get("time", ""),
                views=int(item.get("view_count", 0) or 0),
                likes=int(item.get("likes", 0) or 0),
                collects=int(item.get("collected_count", 0) or 0),
                comments=int(item.get("comments_count", 0) or 0),
                shares=int(item.get("shared_count", 0) or 0),
                rise_fans=rise_fans,
            )

    notes = sorted(notes_by_id.values(), key=lambda item: item.publish_time, reverse=True)
    return notes, profile


def save_results(notes: list[NoteMetrics], profile: dict, dashboard: dict, account: str) -> dict:
    if not notes:
        raise RuntimeError("No notes were collected.")

    payload = {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "account_key": normalize_account_name(account),
        "account": {
            "name": decode_text(profile.get("name", "")),
            "fans_count": profile.get("fans_count"),
            "follow_count": profile.get("follow_count"),
            "faved_count": profile.get("faved_count"),
            "red_num": profile.get("red_num"),
        },
        "dashboard_metrics": dashboard,
        "notes_count": len(notes),
        "latest_note": asdict(notes[0]),
        "all_notes": [asdict(note) for note in notes],
    }
    json_path = get_json_path(account)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8-sig")

    csv_path = get_csv_path(account)
    file_exists = csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "fetched_at",
                "note_id",
                "title",
                "publish_time",
                "views",
                "likes",
                "collects",
                "comments",
                "shares",
                "rise_fans",
            ],
        )
        if not file_exists:
            writer.writeheader()
        for note in notes:
            writer.writerow(asdict(note))
    return payload


def update_summary_file() -> None:
    payloads = []
    for path in sorted(OUTPUT_DIR.glob("xhs_metrics_latest_*.json")):
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

    with sync_playwright() as playwright:
        append_login_trace(account, "playwright:launch_browser")
        browser = playwright.chromium.launch(
            executable_path=str(EDGE_PATH),
            headless=False,
        )
        context = browser.new_context()
        page = context.new_page()
        append_login_trace(account, f"page:open_home:{HOME_URL}")
        open_creator_page(page, HOME_URL)
        print("Browser opened. Complete the Xiaohongshu login manually.")
        print("After you can see the creator dashboard, reply login complete in Codex.")
        try:
            confirm_path = get_login_confirm_path(account)
            append_login_trace(account, f"session:wait_for_user_confirm path={confirm_path}")
            deadline = time.time() + 600
            while time.time() < deadline:
                if confirm_path.exists():
                    append_login_trace(account, "session:user_confirmed")
                    break
                time.sleep(1)
            else:
                raise RuntimeError("Login confirmation timed out.")

            if not is_login_complete(page):
                append_login_trace(account, "session:confirm_received_but_page_not_ready")
                capture_login_debug(page, account)
                raise RuntimeError("Current page is not the creator dashboard.")
            append_login_trace(account, f"session:validated url={page.url}")
        except Exception:
            append_login_trace(account, "session:validation_failed")
            browser.close()
            raise RuntimeError("Valid login was not detected. Finish login and try again.")
        storage_state_path = get_storage_state_path(account)
        append_login_trace(account, f"storage:save_begin path={storage_state_path}")
        context.storage_state(path=str(storage_state_path))
        append_login_trace(account, f"storage:save_done exists={storage_state_path.exists()}")
        upsert_account_config(account)
        append_login_trace(account, "account_config:upsert_done")
        print(f"Login state saved for account: {normalize_account_name(account)}")
        append_login_trace(account, "browser:close_begin")
        browser.close()
        append_login_trace(account, "browser:close_done")


def run_fetch(account: str) -> None:
    ensure_dirs()
    if not EDGE_PATH.exists():
        raise RuntimeError(f"Edge browser not found: {EDGE_PATH}")

    screenshot_path = get_screenshot_path(account)
    storage_state_path = get_storage_state_path(account)
    if not storage_state_path.exists():
        raise RuntimeError("Valid login was not detected. Finish login and try again.")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(EDGE_PATH),
            headless=True,
        )
        context = browser.new_context(storage_state=str(storage_state_path))
        page = context.new_page()
        validate_creator_session_with_retry(page, account, timeout_seconds=20)
        try:
            page.screenshot(path=str(screenshot_path), full_page=True, timeout=15000)
        except Exception:
            pass
        dashboard = extract_home_dashboard_metrics(page)
        notes, profile = build_all_note_metrics(page)
        payload = save_results(notes, profile, dashboard, account)
        update_summary_file()
        print(
            f"Fetch complete for {normalize_account_name(account)}. "
            f"Total notes: {len(notes)}. Latest note: {payload['latest_note']['title']}"
        )
        context.storage_state(path=str(storage_state_path))
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
