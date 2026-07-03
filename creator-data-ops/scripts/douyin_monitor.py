import argparse
import csv
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


WORKDIR = Path(__file__).resolve().parent
OUTPUT_DIR = WORKDIR.parent / "outputs"
STATE_ROOT = WORKDIR / "douyin_state"
DEFAULT_ACCOUNT = "default"
ACCOUNTS_CONFIG_PATH = WORKDIR / "douyin_accounts.json"
HOME_URL = "https://creator.douyin.com/creator-micro/home"
CONTENT_URL = "https://creator.douyin.com/creator-micro/content/manage"
LIVE_DATA_URL = "https://creator.douyin.com/creator-micro/live/data"
EDGE_PATH = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
ACCOUNT_ALIASES = {
    "doyin2": "douyin2",
}

DATE_PATTERN = re.compile(r"^\d{4}[年/-]\d{1,2}[月/-]\d{1,2}日?\s+\d{2}:\d{2}$")

LOGIN_TEXT = "登录"
DOUYIN_ID_LABEL = "抖音号："
FOLLOW_LABEL = "关注"
FANS_LABEL = "粉丝"
LIKED_LABEL = "获赞"
WORKS_LABEL = "作品"
NO_MORE_WORKS = "没有更多作品"
PUBLISHED_LABEL = "已发布"
PRIVATE_LABEL = "私密"
EDIT_LABEL = "编辑作品"
PERMISSION_LABEL = "设置权限"
TOP_LABEL = "作品置顶"
DELETE_LABEL = "删除作品"
PLAY_LABEL = "播放"
LIKE_LABEL = "点赞"
COMMENT_LABEL = "评论"
SHARE_LABEL = "分享"
COLLECT_LABEL = "收藏"
EXPAND_RATE_LABEL = "完播率"
AVG_BROWSE_LABEL = "平均播放时长"
CHALLENGE_PREFIX = "#"

IGNORE_LINES = {
    EDIT_LABEL,
    PERMISSION_LABEL,
    TOP_LABEL,
    DELETE_LABEL,
    PUBLISHED_LABEL,
    PRIVATE_LABEL,
    PLAY_LABEL,
    LIKE_LABEL,
    COMMENT_LABEL,
    SHARE_LABEL,
    COLLECT_LABEL,
    EXPAND_RATE_LABEL,
    AVG_BROWSE_LABEL,
    "--",
}

NAV_LINES = {
    "首页",
    "活动管理",
    "内容管理",
    "作品管理",
    "合集管理",
    "共创中心",
    "原创保护中心",
    "互动管理",
    "数据中心",
    "变现中心",
    "创作中心",
    "通知",
    "网址",
    "抖音",
    "发布视频",
    "发布图文",
    "发布全景视频",
    "发布文章",
}


@dataclass
class VideoMetrics:
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
    sanitized = "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in value)
    return ACCOUNT_ALIASES.get(sanitized, sanitized)


def iter_state_dir_candidates(account: str) -> list[Path]:
    account_key = normalize_account_name(account)
    candidates: list[Path] = []
    seen: set[str] = set()
    for name in [account_key, *[alias for alias, target in ACCOUNT_ALIASES.items() if target == account_key]]:
        if name in seen:
            continue
        seen.add(name)
        candidates.append(STATE_ROOT / name)
    return candidates


def score_state_dir(path: Path) -> tuple[int, int, int]:
    storage_state = path / "storage_state.json"
    local_state = path / "Local State"
    preferences = path / "Default" / "Preferences"
    score = 0
    if storage_state.exists():
        score += 100
    if local_state.exists():
        score += 10
    if preferences.exists():
        score += 5
    latest_mtime = 0
    for item in (storage_state, local_state, preferences):
        if item.exists():
            latest_mtime = max(latest_mtime, int(item.stat().st_mtime))
    return score, latest_mtime, len(str(path))


def get_state_dir(account: str) -> Path:
    candidates = iter_state_dir_candidates(account)
    existing = [path for path in candidates if path.exists()]
    if existing:
        existing.sort(key=score_state_dir, reverse=True)
        return existing[0]
    return candidates[0]


def get_json_path(account: str) -> Path:
    return OUTPUT_DIR / f"douyin_metrics_latest_{normalize_account_name(account)}.json"


def get_csv_path(account: str) -> Path:
    return OUTPUT_DIR / f"douyin_metrics_history_{normalize_account_name(account)}.csv"


def get_screenshot_path(account: str) -> Path:
    return OUTPUT_DIR / f"douyin_creator_dashboard_{normalize_account_name(account)}.png"


def get_debug_text_path(account: str) -> Path:
    return OUTPUT_DIR / f"douyin_debug_{normalize_account_name(account)}.txt"


def get_storage_state_path(account: str) -> Path:
    return get_state_dir(account) / "storage_state.json"


def get_login_confirm_path(account: str) -> Path:
    return OUTPUT_DIR / f"douyin_login_confirm_{normalize_account_name(account)}.flag"


def append_login_trace(account: str, message: str) -> None:
    trace_path = OUTPUT_DIR / f"douyin_login_trace_{normalize_account_name(account)}.log"
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n"
    try:
        with trace_path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except Exception:
        pass


def repair_state_dir_aliases(account: str) -> Path:
    primary = get_state_dir(account)
    primary.mkdir(parents=True, exist_ok=True)
    primary_storage = primary / "storage_state.json"
    for candidate in iter_state_dir_candidates(account):
        if candidate == primary or not candidate.exists():
            continue
        candidate_storage = candidate / "storage_state.json"
        if not primary_storage.exists() and candidate_storage.exists():
            primary_storage.write_bytes(candidate_storage.read_bytes())
    return primary


def get_summary_path() -> Path:
    return OUTPUT_DIR / "douyin_metrics_latest.json"


def ensure_accounts_config() -> None:
    if ACCOUNTS_CONFIG_PATH.exists():
        return
    template = {
        "accounts": [
            {
                "name": "default",
                "enabled": True,
                "login_mode": "manual",
                "notes": "默认抖音账号，首次手动登录一次，后续复用登录状态。",
            }
        ]
    }
    ACCOUNTS_CONFIG_PATH.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8-sig")


def load_accounts_config() -> list[dict]:
    ensure_accounts_config()
    data = json.loads(ACCOUNTS_CONFIG_PATH.read_text(encoding="utf-8-sig"))
    accounts = data.get("accounts") or []
    if not isinstance(accounts, list):
        raise RuntimeError("douyin_accounts.json format is invalid.")
    normalized_accounts: list[dict] = []
    seen: set[str] = set()
    changed = False
    for item in accounts:
        if not isinstance(item, dict):
            changed = True
            continue
        normalized_name = normalize_account_name(str(item.get("name") or "").strip())
        if not normalized_name:
            changed = True
            continue
        if normalized_name in seen:
            changed = True
            continue
        seen.add(normalized_name)
        if item.get("name") != normalized_name:
            item["name"] = normalized_name
            changed = True
        normalized_accounts.append(item)
    if changed:
        data["accounts"] = normalized_accounts
        ACCOUNTS_CONFIG_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8-sig",
        )
    return normalized_accounts


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
        raise RuntimeError("No enabled Douyin accounts found.")
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
        name = normalize_account_name(str(item.get("name") or "").strip())
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
    if "creator.douyin.com" not in current_url:
        return False
    if "passport" in current_url or "login" in current_url:
        return False
    dashboard_markers = [
        "发布视频",
        "数据中心",
        "抖音号：",
        "粉丝",
        "获赞",
    ]
    login_markers = [
        "扫码登录",
        "验证码登录",
        "密码登录",
        "登录/注册",
        "创作者登录",
        "MCN机构登录",
    ]
    last_body_text = ""
    for _ in range(3):
        try:
            body_text = page.locator("body").inner_text(timeout=8000)
        except Exception:
            body_text = ""
        last_body_text = body_text
        if sum(1 for marker in dashboard_markers if marker in body_text) >= 3:
            return True
        if any(marker in body_text for marker in login_markers):
            return False
        page.wait_for_timeout(3000)
    return bool(last_body_text.strip())


def wait_for_manual_login(page, account: str) -> None:
    page.goto(HOME_URL, wait_until="domcontentloaded")
    print("Browser opened. Complete the Douyin Creator login manually.")
    print("After login finishes, reply login complete in Codex.")

    confirm_path = get_login_confirm_path(account)
    append_login_trace(account, f"session:wait_for_user_confirm path={confirm_path}")
    deadline = time.time() + 600
    while time.time() < deadline:
        if confirm_path.exists():
            append_login_trace(account, "session:user_confirmed")
            return
        time.sleep(1)

    raise RuntimeError("Douyin login confirmation timed out.")


def format_publish_time(value: str) -> str:
    try:
        cleaned = value.strip()
        for pattern in ("%Y年%m月%d日 %H:%M", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"):
            try:
                return datetime.strptime(cleaned, pattern).isoformat(timespec="minutes")
            except Exception:
                continue
        return value
    except Exception:
        return value


def parse_metric_value(raw: str) -> int:
    value = (raw or "").strip()
    if not value or value == "-":
        return 0
    try:
        lowered = value.lower()
        if lowered.endswith("w"):
            return int(float(lowered[:-1]) * 10000)
        return int(value.replace(",", ""))
    except Exception:
        return 0


def parse_works_count(raw: str) -> int:
    if WORKS_LABEL not in raw:
        return 0
    match = re.search(r"共\s*(\d+)\s*个作品", raw)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d+)", raw)
    return int(match.group(1)) if match else 0


def extract_profile(page) -> dict:
    page.wait_for_timeout(3000)
    body_text = page.locator("body").inner_text(timeout=15000)
    lines = [line.strip() for line in body_text.splitlines() if line.strip()]
    profile = {
        "name": "",
        "douyin_id": "",
        "follow_count": 0,
        "fans_count": 0,
        "liked_count": 0,
    }
    for idx, line in enumerate(lines):
        if line.startswith(DOUYIN_ID_LABEL):
            profile["douyin_id"] = line.replace(DOUYIN_ID_LABEL, "", 1).strip()
            if idx > 0:
                profile["name"] = lines[idx - 1]
        if line == FOLLOW_LABEL and idx + 1 < len(lines):
            profile["follow_count"] = parse_metric_value(lines[idx + 1])
        if line == FANS_LABEL and idx + 1 < len(lines):
            profile["fans_count"] = parse_metric_value(lines[idx + 1])
        if line == LIKED_LABEL and idx + 1 < len(lines):
            profile["liked_count"] = parse_metric_value(lines[idx + 1])
    return profile


def extract_profile_from_text(body_text: str) -> dict:
    lines = [line.strip() for line in body_text.splitlines() if line.strip()]
    profile = {
        "name": "",
        "douyin_id": "",
        "follow_count": 0,
        "fans_count": 0,
        "liked_count": 0,
    }
    for idx, line in enumerate(lines):
        if line.startswith("抖音号："):
            profile["douyin_id"] = line.replace("抖音号：", "", 1).strip()
            if idx > 0:
                profile["name"] = lines[idx - 1]
        if line == "关注" and idx + 1 < len(lines):
            profile["follow_count"] = parse_metric_value(lines[idx + 1])
        if line == "粉丝" and idx + 1 < len(lines):
            profile["fans_count"] = parse_metric_value(lines[idx + 1])
        if line == "获赞" and idx + 1 < len(lines):
            profile["liked_count"] = parse_metric_value(lines[idx + 1])
    return profile


def merge_profile(primary: dict, fallback: dict) -> dict:
    merged = dict(primary)
    for key, value in fallback.items():
        if merged.get(key) in ("", 0, None, []):
            merged[key] = value
    return merged


def load_all_content_text(page) -> str:
    page.goto(CONTENT_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(8000)
    previous_height = -1
    stable_rounds = 0
    while stable_rounds < 3:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(2500)
        new_height = page.evaluate("document.body.scrollHeight")
        if new_height == previous_height:
            stable_rounds += 1
        else:
            stable_rounds = 0
            previous_height = new_height
    return page.locator("body").inner_text(timeout=15000)


def should_start_title(line: str) -> bool:
    if not line or line in IGNORE_LINES or line in NAV_LINES:
        return False
    if line.startswith(CHALLENGE_PREFIX):
        return False
    if DATE_PATTERN.match(line):
        return False
    if re.fullmatch(r"\d{2}:\d{2}", line):
        return False
    if re.fullmatch(r"\d+万", line):
        return False
    if line.startswith("共") and WORKS_LABEL in line:
        return False
    return True


def parse_videos_from_text(body_text: str) -> tuple[list[VideoMetrics], int]:
    lines = [line.strip() for line in body_text.splitlines() if line.strip()]
    total_count = 0
    start_index = 0
    for idx, line in enumerate(lines):
        total = parse_works_count(line)
        if total:
            total_count = total
            start_index = idx + 1
            break

    records: list[VideoMetrics] = []
    current: dict[str, Any] | None = None
    fetched_at = datetime.now().isoformat(timespec="seconds")

    i = start_index
    while i < len(lines):
        line = lines[i]
        if line == NO_MORE_WORKS:
            break

        if current is None and should_start_title(line):
            current = {
                "fetched_at": fetched_at,
                "item_id": f"video-{len(records) + 1}",
                "title": line if line else "无作品描述",
                "publish_time": "",
                "views": 0,
                "likes": 0,
                "comments": 0,
                "shares": 0,
                "collects": 0,
            }
            i += 1
            continue

        if current is not None and DATE_PATTERN.match(line):
            current["publish_time"] = format_publish_time(line)
            i += 1
            while i < len(lines):
                marker = lines[i]
                if marker in (PUBLISHED_LABEL, PRIVATE_LABEL) or marker.startswith(CHALLENGE_PREFIX):
                    i += 1
                    continue
                if marker == PLAY_LABEL and i + 1 < len(lines):
                    current["views"] = parse_metric_value(lines[i + 1])
                    i += 2
                    continue
                if marker == LIKE_LABEL and i + 1 < len(lines):
                    current["likes"] = parse_metric_value(lines[i + 1])
                    i += 2
                    continue
                if marker == COMMENT_LABEL and i + 1 < len(lines):
                    current["comments"] = parse_metric_value(lines[i + 1])
                    i += 2
                    continue
                if marker == SHARE_LABEL and i + 1 < len(lines):
                    current["shares"] = parse_metric_value(lines[i + 1])
                    i += 2
                    continue
                if marker == COLLECT_LABEL and i + 1 < len(lines):
                    current["collects"] = parse_metric_value(lines[i + 1])
                    i += 2
                    continue
                if DATE_PATTERN.match(marker) or marker == NO_MORE_WORKS:
                    break
                if should_start_title(marker):
                    break
                i += 1
            records.append(VideoMetrics(**current))
            current = None
            continue

        i += 1

    records.sort(key=lambda item: item.publish_time, reverse=True)
    return records, total_count


def build_all_video_metrics(page) -> tuple[list[VideoMetrics], dict]:
    profile = extract_profile(page)
    body_text = load_all_content_text(page)
    videos, total_count = parse_videos_from_text(body_text)
    profile["works_count"] = total_count
    return videos, profile


def post_json(page, url: str, payload: dict[str, Any], method: str = "GET") -> dict[str, Any]:
    script = """
async ({ url, payload, method }) => {
  const response = await fetch(url, {
    method: method || 'GET',
    credentials: 'include',
    headers: { 'content-type': 'application/json' },
    body: method === 'POST' ? JSON.stringify(payload || {}) : undefined
  });
  const text = await response.text();
  return { status: response.status, text };
}
"""
    result = page.evaluate(script, {"url": url, "payload": payload, "method": method})
    text = result.get("text") or "{}"
    try:
        body = json.loads(text)
    except Exception:
        body = {"raw_text": text}
    return {"status": result.get("status", 0), "body": body}


def extract_home_dashboard_metrics(page) -> dict:
    page.wait_for_timeout(3000)
    body_text = page.locator("body").inner_text(timeout=15000)
    return {
        "live_overview_available": "直播数据" in body_text,
        "live_overview_has_data": "暂无数据" not in body_text and "直播数据" in body_text,
        "home_live_module_text": body_text[:4000],
    }


def looks_like_login_landing(body_text: str) -> bool:
    dashboard_markers = [
        "发布视频",
        "数据中心",
        "抖音号：",
        "粉丝",
        "获赞",
    ]
    if sum(1 for marker in dashboard_markers if marker in body_text) >= 3:
        return False
    markers = [
        "扫码登录",
        "验证码登录",
        "密码登录",
        "登录/注册",
        "创作者登录",
        "我是创作者",
        "我是MCN机构",
    ]
    return any(marker in body_text for marker in markers)


def build_live_metrics(page) -> dict:
    page.goto(LIVE_DATA_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(8000)

    today = datetime.now()
    start_7d = (today - timedelta(days=6)).strftime("%Y-%m-%d")
    start_30d = (today - timedelta(days=29)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")

    create_info = post_json(page, "https://creator.douyin.com/live/api/anchor/get_create_info/", {}, "GET")
    performance_all_7d = post_json(
        page,
        f"https://creator.douyin.com/live/api/data/get_live_performance_data/?start_time={start_7d}&end_time={end}&deliver=all",
        {},
        "GET",
    )
    performance_douyin_7d = post_json(
        page,
        f"https://creator.douyin.com/live/api/data/get_live_performance_data/?start_time={start_7d}&end_time={end}&deliver=douyin",
        {},
        "GET",
    )
    performance_all_30d = post_json(
        page,
        f"https://creator.douyin.com/live/api/data/get_live_performance_data/?start_time={start_30d}&end_time={end}&deliver=all",
        {},
        "GET",
    )
    performance_douyin_30d = post_json(
        page,
        f"https://creator.douyin.com/live/api/data/get_live_performance_data/?start_time={start_30d}&end_time={end}&deliver=douyin",
        {},
        "GET",
    )
    replay_list = post_json(
        page,
        "https://creator.douyin.com/live/api/replay/get_replay_list/?page_num=1&page_size=20",
        {},
        "GET",
    )

    create_info_data = (
        create_info.get("body", {}).get("data", {}) if isinstance(create_info.get("body"), dict) else {}
    )
    perf_all_7d_data = (
        performance_all_7d.get("body", {}).get("data", {})
        if isinstance(performance_all_7d.get("body"), dict)
        else {}
    )
    perf_douyin_7d_data = (
        performance_douyin_7d.get("body", {}).get("data", {})
        if isinstance(performance_douyin_7d.get("body"), dict)
        else {}
    )
    perf_all_30d_data = (
        performance_all_30d.get("body", {}).get("data", {})
        if isinstance(performance_all_30d.get("body"), dict)
        else {}
    )
    perf_douyin_30d_data = (
        performance_douyin_30d.get("body", {}).get("data", {})
        if isinstance(performance_douyin_30d.get("body"), dict)
        else {}
    )
    replay_data = (
        replay_list.get("body", {}).get("data", {}) if isinstance(replay_list.get("body"), dict) else {}
    )

    return {
        "date_windows": {
            "seven_days": {"start": start_7d, "end": end},
            "thirty_days": {"start": start_30d, "end": end},
        },
        "overview": {
            "has_live_data": bool(perf_all_7d_data.get("summary_data", {}).get("room_cnt", 0)),
            "room_count": perf_all_7d_data.get("summary_data", {}).get("room_cnt", 0),
            "live_day_count": perf_all_7d_data.get("summary_data", {}).get("live_day_cnt", 0),
            "live_duration": perf_all_7d_data.get("summary_data", {}).get("live_duration", 0),
            "watch_count": perf_all_7d_data.get("summary_data", {}).get("live_watch_cnt", 0),
            "watch_user_count": perf_all_7d_data.get("summary_data", {}).get("live_watch_ucnt", 0),
            "max_watch_count": perf_all_7d_data.get("summary_data", {}).get("live_watch_cnt_max", 0),
            "comment_count": perf_all_7d_data.get("summary_data", {}).get("live_comment_cnt", 0),
            "new_fans_count": perf_all_7d_data.get("summary_data", {}).get("live_new_fans_ucnt", 0),
            "replay_count": replay_data.get("total_count", 0),
        },
        "create_info": create_info_data,
        "performance_all": perf_all_7d_data,
        "performance_douyin": perf_douyin_7d_data,
        "performance_all_30d": perf_all_30d_data,
        "performance_douyin_30d": perf_douyin_30d_data,
        "replay_list": replay_data.get("replays", []),
        "replay_meta": {
            "total_count": replay_data.get("total_count", 0),
            "page_no": replay_data.get("page_no", 1),
            "size": replay_data.get("size", 0),
            "ugc_vs_replay_info": replay_data.get("ugc_vs_replay_info", {}),
        },
    }


def save_results(videos: list[VideoMetrics], profile: dict, dashboard: dict, live: dict, account: str) -> dict:
    payload = {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "account_key": normalize_account_name(account),
        "account": profile,
        "dashboard_metrics": dashboard,
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
    payloads_by_account: dict[str, dict[str, Any]] = {}
    for path in sorted(OUTPUT_DIR.glob("douyin_metrics_latest_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        account_key = normalize_account_name(str(payload.get("account_key") or path.stem.replace("douyin_metrics_latest_", "")))
        payload["account_key"] = account_key
        previous = payloads_by_account.get(account_key)
        if previous is None or str(payload.get("fetched_at") or "") >= str(previous.get("fetched_at") or ""):
            payloads_by_account[account_key] = payload
    payloads = list(payloads_by_account.values())
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
    account_key = normalize_account_name(account)
    state_dir = repair_state_dir_aliases(account_key)
    state_dir.mkdir(parents=True, exist_ok=True)
    storage_state_path = state_dir / "storage_state.json"
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(state_dir),
            executable_path=str(EDGE_PATH),
            headless=False,
        )
        page = context.pages[0] if context.pages else context.new_page()
        append_login_trace(account_key, f"page:open_home:{HOME_URL}")
        wait_for_manual_login(page, account_key)
        page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)
        if not is_login_complete(page):
            append_login_trace(account_key, "session:confirm_received_but_page_not_ready")
            raise RuntimeError("Current page is not the Douyin creator dashboard.")
        append_login_trace(account_key, f"session:validated url={page.url}")
        append_login_trace(account_key, f"storage:save_begin path={storage_state_path}")
        context.storage_state(path=str(storage_state_path))
        append_login_trace(account_key, f"storage:save_done exists={storage_state_path.exists()}")
        upsert_account_config(account_key)
        print(f"Douyin login state saved for account: {account_key}")
        append_login_trace(account_key, "browser:close_begin")
        context.close()
        append_login_trace(account_key, "browser:close_done")


def run_fetch(account: str) -> None:
    ensure_dirs()
    if not EDGE_PATH.exists():
        raise RuntimeError(f"Edge browser not found: {EDGE_PATH}")
    account_key = normalize_account_name(account)
    state_dir = repair_state_dir_aliases(account_key)
    screenshot_path = get_screenshot_path(account_key)
    with sync_playwright() as playwright:
        browser = None
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(state_dir),
            executable_path=str(EDGE_PATH),
            headless=True,
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
            if not is_login_complete(page):
                debug_text = page.locator("body").inner_text(timeout=8000)
                get_debug_text_path(account_key).write_text(debug_text, encoding="utf-8-sig")
                try:
                    page.screenshot(path=str(screenshot_path), full_page=True)
                except Exception:
                    pass
                raise RuntimeError("Douyin Creator session is not logged in.")
            dashboard = extract_home_dashboard_metrics(page)
            if looks_like_login_landing(dashboard.get("home_live_module_text", "")):
                get_debug_text_path(account_key).write_text(
                    dashboard.get("home_live_module_text", ""),
                    encoding="utf-8-sig",
                )
                try:
                    page.screenshot(path=str(screenshot_path), full_page=True)
                except Exception:
                    pass
                raise RuntimeError("Douyin Creator session is not logged in.")
            live = build_live_metrics(page)
            videos, profile = build_all_video_metrics(page)
            dashboard_profile = extract_profile_from_text(dashboard.get("home_live_module_text", ""))
            profile = merge_profile(profile, dashboard_profile)
            save_results(videos, profile, dashboard, live, account_key)
            update_summary_file()
            try:
                page.screenshot(path=str(screenshot_path), full_page=True, timeout=15000)
            except Exception:
                pass
            latest_title = videos[0].title if videos else "No published works found"
            print(
                f"Douyin fetch complete for {account_key}. "
                f"Total videos: {len(videos)}. Latest video: {latest_title}"
            )
        except Exception:
            try:
                page.screenshot(path=str(screenshot_path), full_page=True)
            except Exception:
                pass
            try:
                body_text = page.locator("body").inner_text(timeout=10000)
                get_debug_text_path(account_key).write_text(body_text, encoding="utf-8-sig")
            except Exception:
                pass
            raise
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
