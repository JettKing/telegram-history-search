-- admin_audit 查询模板
-- 说明：时间字段使用 SQLite CURRENT_TIMESTAMP（UTC）。如需北京时间，可在展示层转换。

-- 1. 最近 100 条管理员操作
SELECT
  id, created_at, role, auth_code_id, action, resource, resource_id, details
FROM admin_audit
ORDER BY datetime(created_at) DESC, id DESC
LIMIT 100;

-- 2. 最近 24 小时的高危操作
SELECT
  id, created_at, role, auth_code_id, action, resource, resource_id, details
FROM admin_audit
WHERE datetime(created_at) >= datetime('now', '-24 hours')
  AND action IN (
    'delete_channel',
    'delete_auth_code',
    'disable_auth_code',
    'clear_runs'
  )
ORDER BY datetime(created_at) DESC, id DESC;

-- 3. 按管理员统计高危操作次数
SELECT
  role,
  COALESCE(CAST(auth_code_id AS TEXT), 'ADMIN_TOKEN') AS actor_key,
  COUNT(*) AS high_risk_count,
  MAX(created_at) AS last_high_risk_at
FROM admin_audit
WHERE datetime(created_at) >= datetime('now', '-7 days')
  AND action IN (
    'delete_channel',
    'delete_auth_code',
    'disable_auth_code',
    'clear_runs'
  )
GROUP BY role, auth_code_id
ORDER BY high_risk_count DESC, last_high_risk_at DESC;

-- 4. 10 分钟内同一主体连续执行 3 次以上高危操作
SELECT
  a.role,
  a.auth_code_id,
  a.created_at AS window_end,
  COUNT(*) AS operations_in_window
FROM admin_audit a
JOIN admin_audit b
  ON b.role = a.role
 AND COALESCE(b.auth_code_id, -1) = COALESCE(a.auth_code_id, -1)
 AND b.action IN ('delete_channel', 'delete_auth_code', 'disable_auth_code', 'clear_runs')
 AND datetime(b.created_at) BETWEEN datetime(a.created_at, '-10 minutes') AND datetime(a.created_at)
WHERE a.action IN ('delete_channel', 'delete_auth_code', 'disable_auth_code', 'clear_runs')
GROUP BY a.id, a.role, a.auth_code_id, a.created_at
HAVING COUNT(*) >= 3
ORDER BY datetime(a.created_at) DESC;

-- 5. 缺少授权码归属的普通管理员操作
SELECT
  id, created_at, role, action, resource, resource_id, details
FROM admin_audit
WHERE role = 'admin'
  AND auth_code_id IS NULL
ORDER BY datetime(created_at) DESC, id DESC;

-- 6. 每日操作趋势
SELECT
  date(created_at) AS utc_day,
  action,
  COUNT(*) AS operation_count
FROM admin_audit
WHERE datetime(created_at) >= datetime('now', '-30 days')
GROUP BY utc_day, action
ORDER BY utc_day DESC, operation_count DESC;

-- 7. 审计表完整性检查
SELECT
  COUNT(*) AS total_rows,
  MIN(created_at) AS first_event_at,
  MAX(created_at) AS last_event_at,
  SUM(CASE WHEN role IS NULL OR action IS NULL OR resource IS NULL THEN 1 ELSE 0 END) AS incomplete_rows
FROM admin_audit;

-- 8. 建议的索引（只需执行一次）
CREATE INDEX IF NOT EXISTS idx_admin_audit_action_time
  ON admin_audit(action, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_admin_audit_actor_time
  ON admin_audit(role, auth_code_id, created_at DESC);
