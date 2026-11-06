# 第一阶段性能重构方案

目标：在不改变搜索、采集和权限产品行为的前提下，减少 D1 写入/读取放大，降低采集网络往返，并为线上性能监控提供可观测性。

> 本文是实施方案和 SQL 示例。SQL 迁移必须先在备份或 staging 数据库执行，再应用到生产 D1；不要把生产 D1 的 DDL 放进普通 Worker 请求路径。

## 1. 移除请求路径中的运行时 DDL

当前 `worker/src/index.js` 的 `ensureOwnershipSchema()` 和 `ensureSecuritySchema()` 会在管理请求中反复尝试 `ALTER TABLE`、`CREATE TABLE IF NOT EXISTS`。这会增加每次请求的 D1 往返，并把“部署迁移”与“业务请求”耦合。

### 推荐迁移文件

新建 `worker/migrations/0001_schema_baseline.sql`，只记录部署前必须存在的结构。已经存在的生产表不需要重复执行；若是历史数据库，应先逐条确认列和表。

```sql
-- 仅在确认列不存在时执行；D1/SQLite 不支持安全的 IF NOT EXISTS 列语法。
ALTER TABLE channels ADD COLUMN owner_auth_code_id INTEGER;
ALTER TABLE channels ADD COLUMN deleted_at TEXT;
ALTER TABLE channels ADD COLUMN purge_status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE auth_sessions ADD COLUMN auth_code_id INTEGER;

CREATE TABLE IF NOT EXISTS admin_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_channels_owner_active
ON channels(owner_auth_code_id, deleted_at, enabled);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_token_expiry
ON auth_sessions(token_hash, expires_at);
```

实际项目中建议拆成多个有明确版本号的迁移，避免一个迁移重复执行失败后难以定位。生产执行示例：

```bash
npx wrangler d1 migrations apply tg-history-search --remote
```

### Worker 重构方向

迁移完成并验证后：

- `adminRole()` 只执行认证查询，不再调用 `ensureSecuritySchema()`；
- `publicAddChannel()` 不再调用 `ensureOwnershipSchema()`；
- `adminCodeLogin()`、`adminSessionDays()`、`adminAudit()` 只读写已经存在的表；
- 如果必须兼容旧实例，可保留一次性的部署前迁移脚本，不要在 `fetch()` 热路径中执行 DDL。

认证查询可以固定为：

```js
async function adminRole(req, env) {
  const token = authToken(req, "x-admin-token");
  if (env.ADMIN_TOKEN && token === env.ADMIN_TOKEN) return "super_admin";
  if (!token) return "";
  const tokenHash = await sha256Hex(token);
  const row = await env.DB.prepare(`
    SELECT s.role
    FROM auth_sessions s
    LEFT JOIN auth_codes c ON c.id = s.auth_code_id
    WHERE s.token_hash = ?
      AND s.expires_at > CURRENT_TIMESTAMP
      AND (
        s.role = 'super_admin'
        OR (s.role = 'admin'
            AND s.auth_code_id IS NOT NULL
            AND c.enabled = 1
            AND (c.expires_at IS NULL OR c.expires_at > CURRENT_TIMESTAMP))
      )
    LIMIT 1
  `).bind(tokenHash).first();
  return row?.role || "";
}
```

## 2. 将 `message_count` 从每批 COUNT 改为每频道结束时更新

当前 `/api/ingest` 每收到一个批次都执行整频道 `COUNT(*)`。应让 ingest 只负责写消息和推进游标，频道计数放到采集频道成功结束时执行一次。

### ingest 目标 SQL

```sql
INSERT INTO channels
  (telegram_id, username, title, last_message_id, last_synced_at, updated_at)
VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
ON CONFLICT(telegram_id) DO UPDATE SET
  username = CASE WHEN excluded.username <> '' THEN excluded.username ELSE channels.username END,
  title = CASE WHEN excluded.title <> '' THEN excluded.title ELSE channels.title END,
  last_message_id = MAX(channels.last_message_id, excluded.last_message_id),
  last_synced_at = CURRENT_TIMESTAMP,
  updated_at = CURRENT_TIMESTAMP;
```

注意：这条 SQL 不再在每个批次执行 `message_count=(SELECT COUNT(*))`。

### 频道结束 SQL

在 `/api/collector/run/finish` 成功路径，或新增受保护接口 `/api/collector/channel/finalize`，执行一次：

```sql
UPDATE channels
SET message_count = (
      SELECT COUNT(*) FROM messages m WHERE m.channel_id = channels.telegram_id
    ),
    last_synced_at = CURRENT_TIMESTAMP,
    updated_at = CURRENT_TIMESTAMP
WHERE telegram_id = ?;
```

对于增量采集，也可以暂时使用“批次新增计数 + 低频精确校准”的折中方案：

```sql
UPDATE channels
SET message_count = message_count + ?,
    last_synced_at = CURRENT_TIMESTAMP,
    updated_at = CURRENT_TIMESTAMP
WHERE telegram_id = ?;
```

但只有在能区分 inserted 和 updated 的情况下才能安全累加；全量校准结束后仍应执行一次精确 COUNT。

### Worker 伪代码

```js
async function finalizeChannel(env, channelId) {
  return env.DB.prepare(`
    UPDATE channels
    SET message_count = (
      SELECT COUNT(*) FROM messages WHERE channel_id = channels.telegram_id
    ),
    last_synced_at = CURRENT_TIMESTAMP,
    updated_at = CURRENT_TIMESTAMP
    WHERE telegram_id = ?
  `).bind(String(channelId)).run();
}
```

## 3. 合并 ingest 与 reconcile seen 网络往返

当前校准批次先调用 `/api/ingest`，再调用 `/api/collector/reconcile/seen`。可在 ingest 请求体中携带校准 ID 和消息 ID：

```json
{
  "reconcile_id": 123,
  "messages": [
    {"channel_id":"-100123", "message_id": 1001, "text":"..."}
  ],
  "seen_message_ids": [1001]
}
```

Worker 在同一个处理函数中完成：

```js
await env.DB.batch([
  ...messageStatements,
  ...new Set(seenIds).map(id =>
    env.DB.prepare(
      "INSERT OR IGNORE INTO collector_reconcile_seen(reconcile_id,message_id) VALUES(?,?)"
    ).bind(reconcileId, id)
  )
]);
```

这样每批减少一次 HTTPS 请求。为保持兼容，可先支持新字段，旧的 `/api/collector/reconcile/seen` 暂时保留，确认采集器全部升级后再移除。

采集器侧目标代码：

```python
async def push(http, messages, reconcile_id=None, seen_ids=None):
    payload = {"messages": messages}
    if reconcile_id:
        payload["reconcile_id"] = reconcile_id
        payload["seen_message_ids"] = list(dict.fromkeys(seen_ids or []))
    response = await http.post(
        f"{API_BASE_URL}/api/ingest",
        json=payload,
        headers=auth(),
    )
    response.raise_for_status()
```

## 4. 增加 HTTP 408/429/5xx 退避和耗时监控

不要遇到临时网络错误就立刻重跑整个频道。建议封装 HTTP 请求：

```python
import random

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}

async def post_with_retry(http, url, *, json=None, headers=None, attempts=4):
    for attempt in range(attempts):
        started = time.monotonic()
        try:
            response = await http.post(url, json=json, headers=headers)
            elapsed = round(time.monotonic() - started, 3)
            print(f"[HTTP] status={response.status_code} elapsed={elapsed}s url={url}")
            if response.status_code not in RETRYABLE_STATUS:
                response.raise_for_status()
                return response
            retry_after = response.headers.get("retry-after")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else min(60, 2 ** attempt + random.random())
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            elapsed = round(time.monotonic() - started, 3)
            print(f"[HTTP] transient_error elapsed={elapsed}s error={exc}")
            delay = min(60, 2 ** attempt + random.random())
        if attempt + 1 == attempts:
            raise RuntimeError(f"HTTP request failed after {attempts} attempts: {url}")
        await asyncio.sleep(delay)
```

`FloodWaitError` 仍按 Telegram 返回秒数处理；HTTP 退避和 Telegram FloodWait 不应共用同一个计数器。

## 5. 增加搜索和采集耗时日志

Worker 可在公开响应头中保留轻量耗时信息，具体日志应避免记录原始关键词或敏感内容：

```js
async function timedSearch(req, env) {
  const started = Date.now();
  const result = await search(env, /* parsed params */);
  console.log(JSON.stringify({
    route: "/api/search",
    ms: Date.now() - started,
    result_count: result.results.length,
    total: result.total,
    limit: result.limit,
    offset: result.offset
  }));
  return result;
}
```

建议记录：

- `route`、耗时、HTTP 状态；
- FTS 或 LIKE 分支；
- batch size、insert/update 数；
- reconcile scanned/seen/deleted；
- 429、5xx、超时和重试次数。

不要记录 Bot Token、授权码、ADMIN_TOKEN、原始搜索文本或消息正文。

## 6. 上线顺序

1. 备份 D1，核对现有表列和索引；
2. 执行版本化迁移；
3. 先部署“无运行时 DDL”兼容版本；
4. 部署 ingest 去 COUNT 版本；
5. 升级采集器，启用合并 seen 请求；
6. 观察至少一个增量周期和一个校准周期；
7. 再删除旧 seen 接口和旧运行时迁移代码。

## 7. 验收指标

- 增量采集每频道的 D1 COUNT 次数：从“每批一次”降为“每次运行最多一次”；
- 校准批次 HTTP 请求数：减少约一半的 ingest/seen 往返；
- 采集器超时和全频道重试次数下降；
- `/api/search` 和 `/api/latest` P95 延迟不升高；
- D1 写入量、读取量和 FTS 更新量下降；
- 任何失败重试仍能从最新游标继续，不能重复删除或误删消息。
