---
name: creator-data-ops
description: Manage creator-platform account data for 小红书、抖音、微信视频号 through a dialogue-first workflow. Use when the user wants to install or use a social media operations skill, initialize data storage on the computer or Feishu, add platform accounts, confirm login, manually fetch all platform data, set scheduled fetch time, or sync account/content/live data into local CSV files or Feishu tables.
---

# Creator Data Ops

## Core Goal

Use a dialogue-first workflow to help non-technical users manage creator accounts on 小红书、抖音、微信视频号.

Keep the product direction aligned with:

1. Dialogue is the operation entry.
2. Local executor opens login pages, saves login state, fetches data, and runs schedules.
3. Data is displayed/stored in either local computer files or Feishu, depending on the user's choice.
4. Feishu is only a display/sync layer, not the trigger for login or fetch actions.

For the full product target, read `references/project-goal.txt` when decisions affect product direction.

## First-Run Flow

When the user says they installed this skill, asks to start using it, or needs setup:

1. Briefly explain that this skill manages 小红书、抖音、微信视频号 accounts and fetches account data, note/work data, and live data.
2. Tell the user:
   - Add account: `新增小红书` / `新增抖音` / `新增微信视频号`
   - Manual fetch: `抓取所有平台数据`
   - Default schedule: 每天24点（00:00）
   - Change schedule: `定时改成 09:00`, then choose the scope when asked
3. Ask: `你的数据想存在哪里？请回复：电脑 或 飞书。`

Preferred command in a prepared workspace:

```powershell
python "work\creator_ops_bridge.py" chat --text "开始使用"
```

Use the workspace Python runtime that has Playwright and the platform dependencies installed. In this development workspace, `C:\Users\surface\anaconda3\python.exe` is known to work. If using the skill's bundled scripts in a new workspace, copy the required scripts from `scripts/` into that workspace's `work/` folder first.

## Storage Choice

### If The User Chooses Computer

Run the storage configuration flow:

```powershell
python "work\creator_ops_bridge.py" chat --text "电脑"
```

Expected outcome:

1. Create a local folder named `自媒体运营数据`.
2. Create `账号数据.csv`.
3. Create `内容直播数据.csv`.
4. Tell the user the full file paths and what each file stores.
5. Guide the user to add an account.

Local file meanings:

1. `账号数据.csv`: platform, account name, account ID, fans, likes, note/work count, latest fetch time, data status.
2. `内容直播数据.csv`: content rows and live rows, separated by the `分类` field.

### If The User Chooses Feishu

Use `lark-shared`, `lark-base`, and related lark skills as needed.

Flow:

1. Check or install lark-cli.
2. Start Feishu user authorization with split-flow.
3. Show the authorization URL and QR code.
4. Tell the user the authorization is only used to sync account, content, and live data into Feishu.
5. Stop and wait for the user to say authorization is complete.
6. After completion, finalize auth and create/sync:
   - Knowledge base: `自媒体运营`
   - Base/table: `账号数据`
   - Base/table: `内容／直播数据`

In a prepared workspace after Feishu is authorized:

```powershell
python "work\creator_ops_bridge.py" chat --text "飞书授权完成"
```

## Dialogue Commands

Use the dialogue bridge for normal operations:

```powershell
python "work\creator_ops_bridge.py" chat --text "<用户原话>"
```

Supported examples:

1. `开始使用`
2. `电脑`
3. `飞书`
4. `新增小红书`
5. `新增抖音`
6. `新增微信视频号`
7. `小红书 登录完成`
8. `抖音 登录完成`
9. `微信视频号 登录完成`
10. `抓取所有平台数据`
11. `小红书 平台拉数`
12. `抖音 <账号标识> 拉数`
13. `查看状态`
14. `定时改成 24:00`

When a login window is opened, tell the user to finish login in the browser, then reply `登录完成`.

## Fetch And Sync Rules

After successful login confirmation:

1. Save login state.
2. Run first fetch for that account.
3. Extract real account name and account ID if possible.
4. Sync account data and content/live data to the chosen storage layer.
5. Tell the user the default scheduled fetch time.

After manual fetch or scheduled fetch:

1. Fetch all selected accounts.
2. Keep successful, failed, and skipped counts.
3. Sync latest rows to the chosen storage layer.
4. Tell the user where to view data.

## Scheduled Task Rules

Default schedule is 每天24点（00:00）.

When the user asks to change scheduled time, do not assume scope. Ask whether it applies to:

1. All platforms and all accounts.
2. One platform and all accounts.
3. One specific account under one platform.

Only update the schedule after both time and scope are clear.

## Bundled Resources

Use these files only when needed:

1. `scripts/creator_ops_bridge.py`: main dialogue and operation router.
2. `scripts/local_data_store.py`: local CSV storage initializer/syncer.
3. `scripts/feishu_bridge.py`: Feishu knowledge base and table syncer.
4. `scripts/xhs_monitor.py`, `scripts/douyin_monitor.py`, `scripts/wechat_video_monitor.py`: platform-specific login/fetch modules.
5. `references/workflow-spec.md`: detailed first-run and dialogue rules.
6. `references/dialogue-guide.txt`: short command guide.
7. `references/project-goal.txt`: full product target.

## Guardrails

1. Keep user-facing explanations non-technical.
2. Do not ask users to run scripts manually unless there is no better option.
3. Do not store user passwords.
4. Do not treat Feishu as the login trigger layer.
5. If a platform login page is already logged into the wrong account, guide the user to log out/switch account in that browser, then complete login.
6. If a command fails, explain the next user action in one short sentence and preserve the local state.
