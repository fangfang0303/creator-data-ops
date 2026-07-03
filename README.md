# creator-data-ops

这是一个 Codex skill，用于通过对话管理小红书、抖音、微信视频号账号数据。

## 能做什么

1. 新增平台账号：小红书、抖音、微信视频号。
2. 登录完成后自动保存登录状态。
3. 抓取账号数据、笔记/作品数据、直播数据。
4. 支持手动抓取所有平台数据。
5. 支持每天定时抓取，默认每天 24 点（00:00）。
6. 支持把数据存到电脑本地 CSV 文件，或同步到飞书知识库。

## 给用户的安装方式

用户在 Codex 里说：

```text
安装这个 skill：https://github.com/fangfang0303/creator-data-ops/tree/main/creator-data-ops
```

安装完成后，用户需要重启 Codex，让新 skill 生效。

## 你怎么上传到 GitHub

你的 GitHub 用户名是 `fangfang0303`，仓库名是 `creator-data-ops`。

上传时，把当前这个文件夹里的内容上传到仓库根目录。也就是 GitHub 仓库里应该长这样：

```text
README.md
.gitignore
creator-data-ops/
```

不要再额外套一层 `creator-data-ops-github/` 文件夹。

## 安装后怎么开始

用户重启 Codex 后，可以说：

```text
使用 creator-data-ops 开始使用
```

Codex 会先说明这个 skill 是干嘛的，然后询问：

```text
你的数据想存在哪里？请回复：电脑 或 飞书。
```

## 常用话术

```text
新增小红书
新增抖音
新增微信视频号
登录完成
抓取所有平台数据
定时改成 09:00
查看状态
```

## 目录说明

```text
creator-data-ops/
  SKILL.md
  agents/openai.yaml
  scripts/
  references/
```

## 注意

这个 skill 不保存用户密码。平台登录通过浏览器完成，登录态保存在用户自己的电脑本地。
