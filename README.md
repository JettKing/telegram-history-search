# Telegram 历史检索机器人 V1.3

一个基于 Telegram、GitHub Actions、Cloudflare Workers 与 Cloudflare D1 构建的频道历史内容检索系统。

## 项目简介

Telegram 历史检索机器人用于自动采集指定 Telegram 频道的历史消息，并将内容建立索引，提供 Web 搜索与 Telegram Bot 搜索入口。

系统采用增量采集机制。首次运行可以建立频道历史索引，后续运行只同步新增消息，减少重复采集和数据库写入。

V1.3 的权限模型进行了明确分离：

- 管理员通过 `ADMIN_TOKEN` 进入管理后台，可以直接添加频道，无需授权码。
- 普通用户通过公开的频道提交入口添加频道时，必须提供有效授权码。
- 授权码由管理员管理，并支持使用次数、有效期、启用状态和使用记录。

## 核心功能

### 历史内容检索

- 关键词搜索 Telegram 历史消息
- 支持频道条件
- 支持开始日期和结束日期
- 搜索结果分页
- 关键词高亮
- 跳转 Telegram 原消息

### 自动采集

- Telethon 连接 Telegram MTProto
- 首次运行建立历史索引
- 后续执行增量同步
- GitHub Actions 定时执行
- 支持手动触发采集
- 支持指定频道同步

### Telegram Bot

支持基础搜索和最新内容查询，例如：

```text
/start
/search AI工具
/search Midjourney
/latest
/channels
/help
```

搜索结果可以通过按钮进行分页，并跳转到对应 Telegram 原消息。

### 频道管理

管理员可以在管理后台：

- 添加频道
- 删除频道
- 启用或停用频道
- 手动同步频道
- 查看频道同步状态
- 查看采集日志
- 重试失败任务

### 授权码管理

管理员可以创建和管理普通用户使用的频道添加授权码：

- 多个授权码
- 自定义名称
- 使用次数限制
- 永久有效或设置过期时间
- 启用或停用
- 查看使用次数
- 查看最近使用时间
- 查看授权码使用记录
- 数据库保存授权码哈希，不保存明文授权码

## 技术架构

```text
Telegram Channel
       |
       v
Telethon / MTProto
       |
       v
GitHub Actions
       |
       v
Cloudflare Worker API
       |
       v
Cloudflare D1
       |
       +------------------+
       |                  |
       v                  v
 Telegram Bot          Web Search
       |
       v
 Telegram 原消息
```

### 技术组件

| 组件 | 用途 |
| --- | --- |
| Telegram Bot API | Telegram Bot 交互 |
| Telethon | Telegram 历史消息采集 |
| GitHub Actions | 定时与手动执行采集任务 |
| Cloudflare Workers | API、权限验证和业务逻辑 |
| Cloudflare D1 | 消息、频道、任务和授权码数据存储 |
| SQLite FTS5 | 全文检索 |
| HTML / CSS / JavaScript | Web 搜索与管理界面 |

## 项目结构

```text
 tg-history-search/
 ├── .github/
 │   └── workflows/
 │       └── collector.yml
 ├── collector/
 │   ├── collector.py
 │   └── requirements.txt
 ├── docs/
 │   └── deployment.md
 ├── web/
 │   ├── app.js
 │   ├── index.html
 │   ├── promo-banner.png
 │   └── style.css
 ├── worker/
 │   ├── schema.sql
 │   ├── src/
 │   │   └── index.js
 │   └── wrangler.toml
 ├── .gitignore
 └── README.md
```

## 部署方法

### 1. 创建 GitHub 仓库

将项目上传到 GitHub 仓库。

不要把 Telegram API 凭据、Bot Token、Cloudflare Token、管理员 Token 或其他 Secret 写入源码。

### 2. 创建 Cloudflare D1

在 Cloudflare 创建 D1 数据库，然后执行：

```bash
npx wrangler d1 execute <DATABASE_NAME> --remote --file=worker/schema.sql
```

将数据库绑定配置写入 `worker/wrangler.toml`。

### 3. 部署 Cloudflare Worker

进入 Worker 目录：

```bash
cd worker
npm install
npx wrangler deploy
```

如果项目环境没有安装 Wrangler，可以先安装：

```bash
npm install -g wrangler
```

### 4. 配置 GitHub Actions

在 GitHub 仓库的 Secrets 中配置 Telegram、Cloudflare 和采集器所需的敏感信息。

完成配置后，可以手动运行 GitHub Actions 测试首次采集。

### 5. 部署 Web 页面

`web/` 目录可以部署到 Cloudflare Pages，也可以通过 Cloudflare Workers 的静态资源能力托管。

部署后，将 Web 页面中的 API 地址指向实际的 Cloudflare Worker 地址。

## 环境变量

实际项目中的环境变量和 Secret 应通过 GitHub Secrets、Cloudflare Secrets 或对应平台的安全配置保存。

### Cloudflare Worker

常用配置包括：

| 变量 | 用途 |
| --- | --- |
| `ADMIN_TOKEN` | 管理后台管理员身份验证 |
| `GITHUB_TOKEN` | 管理后台触发 GitHub Actions |
| `GITHUB_REPO` | GitHub 仓库，格式为 `owner/repository` |
| `ADD_CHANNEL_CODE` | 兼容旧版固定授权码机制时使用 |

### GitHub Actions / Collector

根据采集器配置，需要提供 Telegram API 相关凭据以及 Cloudflare Worker 调用凭据，例如：

| 变量 | 用途 |
| --- | --- |
| `TELEGRAM_API_ID` | Telegram API ID |
| `TELEGRAM_API_HASH` | Telegram API Hash |
| `TELEGRAM_SESSION` | Telethon Session |
| `WORKER_URL` | Cloudflare Worker API 地址 |
| `COLLECTOR_TOKEN` | 采集器访问 Worker 的认证凭据 |

具体变量名称应以仓库当前 `.github/workflows/collector.yml` 和 `collector/collector.py` 的实际配置为准。

## 使用方法

### Web 搜索

打开部署后的 Web 页面。

填写关键词后，可以选择性填写频道、开始日期和结束日期，然后执行搜索。

搜索结果提供：

- 消息标题或正文摘要
- 频道信息
- 发布时间
- 媒体信息
- Telegram 原消息入口
- 分页导航

### Telegram Bot

向 Bot 发送：

```text
/search 关键词
```

例如：

```text
/search AI工具
```

也可以使用：

```text
/latest
```

查看最新索引内容。

### 管理后台

管理员使用 `ADMIN_TOKEN` 访问管理后台。

管理员添加频道时：

```text
管理员身份验证
        |
        v
添加频道
        |
        v
直接添加
```

不需要填写授权码。

### 普通用户提交频道

普通用户使用频道提交入口时，需要提供授权码：

```text
频道
授权码
```

服务端会验证：

- 授权码是否存在
- 是否启用
- 是否过期
- 是否达到使用次数

验证通过后才允许添加频道。

## 安全说明

### 管理员权限

`ADMIN_TOKEN` 具有管理权限，应使用高强度随机值，并且只通过 Cloudflare Secret 或其他安全的 Secret 管理方式保存。

不要将 `ADMIN_TOKEN` 写入：

- GitHub 仓库源码
- README
- 前端 JavaScript
- HTML
- 截图
- 日志

### Telegram 凭据

Telegram API ID、API Hash、Bot Token 和 Telethon Session 均属于敏感凭据，不应提交到公开仓库。

### 授权码

V1.3 的授权码由服务端验证。

数据库不需要保存授权码明文，可以保存用于验证的哈希值。授权码创建后，应由管理员安全保存。

### API 权限

管理员接口必须进行服务端身份验证。

普通用户提交频道的接口必须进行服务端授权码验证，不能只依赖前端 JavaScript 判断权限。

### GitHub Actions

GitHub Actions 使用的 Token 和 Secret 应通过 GitHub Secrets 管理，避免直接写入 workflow 文件。

### 数据安全

部署前应根据实际频道内容和运营需求设置 Cloudflare D1 的访问权限、GitHub 仓库权限以及管理员凭据。

---

Telegram 历史检索机器人 V1.3
