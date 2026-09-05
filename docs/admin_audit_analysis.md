# `admin_audit` 审计日志查询与分析

本目录提供两种方式查看管理员高危操作。`admin_audit_queries.sql` 适合直接在 Cloudflare D1 中执行；`tools/analyze_admin_audit.py` 适合将 D1 导出的 JSON 或 NDJSON 文件下载后进行本地分析。脚本只使用 Python 标准库，不需要安装第三方依赖。

## 一、导出 D1 审计日志

可以使用 Wrangler 导出最近 30 天的数据：

```bash
npx wrangler d1 execute <数据库名称> --remote --command "SELECT id,created_at,role,auth_code_id,action,resource,resource_id,details FROM admin_audit WHERE datetime(created_at) >= datetime('now','-30 days') ORDER BY datetime(created_at) DESC, id DESC" --json > audit.json
```

请将 `<数据库名称>` 替换为项目实际的 D1 数据库名称。也可以在 D1 控制台执行同样的查询并保存结果。

## 二、运行 Python 分析器

分析最近 7 天的记录：

```bash
python3 tools/analyze_admin_audit.py audit.json --since-hours 168
```

输出 JSON，便于继续接入告警系统或日志平台：

```bash
python3 tools/analyze_admin_audit.py audit.json --since-hours 168 --json > audit-report.json
```

脚本会统计操作类型、风险等级和高危操作主体，并识别两类异常：同一主体在短时间内连续执行多次高危操作，以及普通管理员记录缺少 `auth_code_id`。

默认风险分级如下：

| 风险级别 | 操作 |
| --- | --- |
| 高危 | `delete_channel`、`delete_auth_code`、`disable_auth_code`、`clear_runs` |
| 中危 | `disable_channel`、`enable_channel`、`retry_run` |
| 低危 | `sync_channel`、`create_auth_code` |

默认情况下，10 分钟内同一主体出现 3 次或以上高危操作会被标记为异常。可以调整参数：

```bash
python3 tools/analyze_admin_audit.py audit.json \
  --since-hours 24 \
  --burst-count 2 \
  --burst-minutes 5
```

## 三、推荐的 D1 查询

`admin_audit_queries.sql` 包含以下查询：

1. 最近 100 条管理员操作；
2. 最近 24 小时的高危操作；
3. 按管理员统计高危操作次数；
4. 短时间高危操作聚集检测；
5. 普通管理员缺少授权码归属的记录；
6. 每日操作趋势；
7. 审计表完整性检查；
8. 审计查询索引。

审计时间使用 SQLite 的 UTC 时间。展示给用户时，建议在前端或报告层转换为目标时区，不要修改数据库中的原始时间。

## 四、建议的告警规则

生产环境中可以优先针对以下情况告警：

- `delete_channel` 或 `delete_auth_code`；
- `clear_runs`；
- 普通管理员出现没有 `auth_code_id` 的记录；
- 同一授权码归属在短时间内连续执行多次高危操作；
- 非工作时段的大量删除、停用或清空操作；
- 审计表出现字段缺失或时间倒退。

审计日志中不应保存明文授权码、`ADMIN_TOKEN` 或其他登录凭据。`details` 字段只应保存必要的资源标识和操作范围。
