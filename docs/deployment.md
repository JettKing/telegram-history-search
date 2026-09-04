# V0.5 部署

## 1. Cloudflare D1
创建 D1 数据库后，将 `worker/wrangler.toml` 中的 `database_id` 替换为实际 ID。
执行 schema：

```bash
cd worker
npx wrangler d1 execute tg-history-search --remote --file=schema.sql
npx wrangler deploy
```

## 2. Worker Secrets
在 Cloudflare Worker 中设置：
- `BOT_TOKEN`
- `ADMIN_TOKEN`
- `COLLECTOR_TOKEN`

## 3. Telegram Webhook
将 `https://你的Worker域名/bot/webhook` 设置为 Bot webhook。

## 4. GitHub Actions Secrets
仓库 Settings → Secrets and variables → Actions：
- `TG_API_ID`
- `TG_API_HASH`
- `TG_SESSION`
- `API_BASE_URL`
- `COLLECTOR_TOKEN`

## 5. 首次采集
打开 `/admin`，添加频道并保持启用，然后手动运行 GitHub Actions。之后每 30 分钟增量同步。


## 频道添加权限

### 管理员

打开 `/admin`，使用 `ADMIN_TOKEN` 登录。管理员在后台添加频道时**无需授权码**。

### 非管理员

打开 `/submit`，填写频道和管理员发放的授权码。Worker 会在服务端校验授权码，并检查：

- 是否存在
- 是否启用
- 是否过期
- 是否达到使用次数上限

验证成功后才允许新增频道，并记录授权码使用日志。

授权码由管理员在后台“授权码管理”中创建，数据库只保存 SHA-256 哈希，不保存明文授权码。
