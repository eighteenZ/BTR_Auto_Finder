# AI Hunter API 集成文档（面向 openclaw）

> 版本：v1.0（hunter/marketing 拆分架构）｜ 机器可读规格：`openapi-hunter.json` / `openapi-marketing.json`（与本文档同目录发布）

## 1. 架构概览

系统由两个独立服务组成，仅通过 PostgreSQL 交互，可分开部署、分开扩容：

| 服务 | 默认端口 | 职责 |
|---|---|---|
| **hunter** | `:8000` | 获客：创建任务、LangGraph 流水线（解析→洞察→关键词→搜索→线索提取→评估→邮件草稿生成）、自动化队列、全局线索库、SSE 进度流 |
| **marketing** | `:8100` | 邮件营销：草稿审批、campaign 生命周期、定时发送调度、IMAP 回信检测、邮件设置 |

```text
openclaw ──HTTP──▶ hunter(:8000)                     openclaw ──HTTP──▶ marketing(:8100)
                    │ 写 email_drafts（草稿契约表）                      │ 读 email_drafts（审批后建 campaign）
                    │ 写 campaign_jobs（建campaign请求）                 │ 认领 campaign_jobs（挂起等审批，5分钟回查）
                    ▼                                                  ▼
                          PostgreSQL（两服务唯一交互边界）
```

- 健康检查：`GET :8000/api/v1/health`、`GET :8100/api/v1/health`
- Swagger UI：两服务各自的 `/docs`

## 2. 认证与账号（企业邮箱登录）

### 2.1 交互式用户（审批页 / Swagger）

浏览器打开 `:8100/login`，用**企业邮箱地址 + 邮箱密码**登录（凭据由企业邮件服务器 IMAP 即时验证，本系统不存密码）。会话 cookie 有效期 7 天；`:8100/review` 未登录会自动跳转登录页。

- 角色：`admin`（可读写设置、管理用户）/ `member`（审批、campaign、线索、导出）
- 首个 admin 由部署者在服务器创建：`python scripts/create_user.py --admin --email you@company.com`
- 登出：`POST /api/auth/logout`；当前身份：`GET /api/auth/me`

### 2.2 程序化调用（openclaw 等）

每个用户有一个个人 API key（首次创建账号时生成），沿用旧的请求头方式：

```http
X-API-Key: ahk_xxxx
# 或
Authorization: Bearer ahk_xxxx
```

- member 的 key 只能调业务端点（草稿/campaign/线索/导出）；admin 的 key 额外可调设置与用户管理
- key 泄露时由 admin 重置：`POST :8100/api/auth/users/{email}/reset-api-key`
- 用户管理端点（admin）：`GET/POST /api/auth/users`、`DELETE /api/auth/users/{email}`、`POST /api/auth/users/{email}/role`

### 2.3 兼容与例外

- 旧共享 `API_ACCESS_TOKEN` **继续有效并视为 admin**（过渡期 break-glass；不再配置即关闭此通道）
- **localhost 来源的请求免鉴权**（本机调试与 systemd 脚本）
- `GET /api/v1/health` 免鉴权
- 语义注意：**显式提供了错误凭据一律 401**（即使来自 localhost）；未提供凭据时 localhost 放行、远程 401/403

## 3. 核心工作流（时序）

```text
① 创建获客任务 ──▶ ② 轮询进度/收线索 ──▶ ③ 审批邮件草稿 ──▶ ④ campaign 自动/手动建立 ──▶ ⑤ 调度器自动发送 ──▶ ⑥ 回信检测停止跟进
```

### ① 创建获客任务（hunter）

**方式 A：直连**（立即后台执行）

```bash
curl -X POST http://<host>:8000/api/v1/hunts -H 'Content-Type: application/json' -d '{
  "website_url": "https://example.com/",
  "description": "Find US importers who buy from China... (同行排除写清楚)",
  "product_keywords": ["importer of Chinese goods"],
  "target_customer_profile": "US importers; exclude freight forwarders",
  "target_regions": ["United States"],
  "target_lead_count": 5,
  "max_rounds": 1,
  "enable_email_craft": true,
  "email_template_notes": "签名规范与禁止编造的约束说明"
}'
# → {"hunt_id": "<uuid>", "status": "pending"}
```

**方式 B：队列**（持久化队列，进程重启不丢，支持全自动链路）

```bash
curl -X POST http://<host>:8000/api/v1/automation/jobs -d '{...同上字段...}'
# → {"job_id": "<uuid>", "status": "queued"}
```

队列任务完成后，若 payload 含 `enable_email_craft: true` 且服务端 `AUTOMATION_CONSUMER_AUTO_START_CAMPAIGN=true`，hunter 会**自动向 `campaign_jobs` 表入队一条建 campaign 请求**，由 marketing 消费——这是全自动链路的入口。

### ② 轮询进度与获取结果（hunter）

```bash
GET /api/v1/hunts/{hunt_id}/status     # status: pending→running→completed|failed|cancelled
GET /api/v1/automation/jobs/{job_id}   # 队列态 + last_hunt_id + leads_count
GET /api/v1/hunts/{hunt_id}/result     # 线索全量 + 摘要（草稿明细请到 marketing 查）
GET /api/v1/leads?limit=50             # 全局线索库（跨 hunt 去重累积）
```

实时进度（可选）：`GET /api/v1/hunts/{hunt_id}/stream`（SSE），事件见 §5。

### ③ 审批邮件草稿（marketing）

任务完成后草稿写入 `email_drafts` 表，状态 `draft`。审批前**任何邮件都不可能发出**。

```bash
# 列出待审草稿（含三封邮件全文/收件人/校验摘要）
GET :8100/api/v1/email-drafts?status=draft
GET :8100/api/v1/hunts/{hunt_id}/email-drafts

# 审批 / 拒绝（可附备注）
POST :8100/api/v1/email-drafts/{draft_id}/decision
{"decision": "approved", "notes": "ok"}
```

- 人工页面：`GET :8100/review`（列表 + 全文预览 + 批准/拒绝按钮）
- 兼容旧路径：`POST :8100/api/v1/hunts/{hunt_id}/email-sequences/{index}/decision`

**草稿内容保证**：草稿在**生成时**就完成签名净化，落库正文里不会出现 `[Your Name]`、`[Phone]`、`[Email]`、`[Title]`、`[Last Name]`、`[phone/email]` 等占位符——审批页看到的就是最终发出内容（发送时还会再净化一次兜底）。

- 发信人身份取自 `.env` 的 `EMAIL_SIGNATURE_NAME` / `EMAIL_SIGNATURE_TITLE` / `EMAIL_SIGNATURE_PHONE`，邮箱取 `EMAIL_FROM_ADDRESS`；对应值未配置时该行整行删除，而不是留占位符
- 称呼 `[Name]` 在问候语中优先用收件人姓名（`target_name`，兼容 `{"name": ...}` 嵌套结构），无姓名时用 `Dear Sir/Madam`
- **无法填充的占位符**（如 `[date]`）会连同前置介词一并移除（"my note of [date]" → "my note"），同时该草稿标记 `needs_review`，并在 `review_summary.issues` 与 `placeholder_issues` 中记录被移除的标记，提示人工复核措辞
- 存量脏草稿用 `python scripts/repair_draft_placeholders.py [--dry-run] [--include-hunts]` 批量清洗，幂等且保留审批状态

**campaign_jobs 挂起机制**（全自动链路的关键）：队列路径的建 campaign 请求在草稿未决时**不会结束**，marketing 每 5 分钟回查一次；任一草稿被批准后自动建 campaign 并启动发送；全部拒绝则任务关闭；**72 小时**无人审批任务超时关闭（草稿本身永久保留，可事后手动建 campaign）。

### ④ 结果交付：导出 xlsx（hunter）

业务方要表格时用这个端点（按单个获客任务导出）：

```bash
GET :8000/api/v1/hunts/{hunt_id}/export?format=xlsx&view=brief   # 默认 brief
GET :8000/api/v1/hunts/{hunt_id}/export?format=xlsx&view=full
curl -OJ "http://<host>:8000/api/v1/hunts/<hunt_id>/export?view=full"   # -OJ 按响应文件名保存
```

- 返回 xlsx 附件（`Content-Disposition: attachment`），并带 `X-Lead-Count` / `X-Export-View` 响应头
- `view=brief`（9 列）：公司名称、官网、国家/地区、行业、联系人、邮箱、电话、优先级、匹配度
- `view=full`（21 列）：brief 全部 + 联系人职务、全部决策人、地址、社交媒体、客户类型、可触达度、契合度、证据强度、来源关键词、首次发现、最近更新、复用线索
- `联系人` 优先取 `contact_person`，为空时回退到首位决策人；`全部决策人` 格式为 `姓名 (职务) <邮箱>`
- 无线索时仍返回带表头的空表（不会报错）
- 错误：`404` 任务不存在；`400` format 非 xlsx 或 view 取值非法

> **全局线索库不提供导出**：`/api/v1/leads` 跨任务累积、无上界，整体导出有内存与耗时风险。
> 需要时按 `limit`/`offset` 分页拉取自行汇总。

### ⑤ campaign 管理（marketing）
```bash
# 手动建 campaign（只收已批准草稿；全自动路径无需此步）
POST :8100/api/v1/hunts/{hunt_id}/email-campaigns   {"name": "Batch 1"}
# 启动 / 暂停（启动前需 SMTP 测试通过：POST :8100/api/settings/email/test）
POST :8100/api/v1/email-campaigns/{campaign_id}/start
POST :8100/api/v1/email-campaigns/{campaign_id}/pause
# 查询（实时聚合：序列数/已发/待发/失败/回信/模板表现）
GET  :8100/api/v1/hunts/{hunt_id}/email-campaigns
GET  :8100/api/v1/email-sequences/{sequence_id}
```

### ⑥ 定时发送（marketing，自动）

`EMAIL_AUTO_SEND_ENABLED=true` 时调度器每 60 秒扫描到期消息：三步序列按生成时标注的第 0/3/7 天发送；发送正文经过**确定性净化**（签名占位符替换、联系方式行重写、招聘邮箱降权）。手动触发：`POST :8100/api/v1/email-scheduler/run`。

> ⚠️ **发送窗口限制（当前实现）**：调度器仅按 `scheduled_at <= now` 判定，**不检查工作时间 / 时区 / 工作日 / 每日与每小时限流**（`EMAIL_TIMEZONE`、`EMAIL_BUSINESS_HOURS_*`、`EMAIL_WEEKDAYS_ONLY`、`EMAIL_DAILY_SEND_LIMIT`、`EMAIL_HOURLY_SEND_LIMIT` 目前只在设置接口可读写，未被调度器消费）。因此邮件会在 campaign 启动后按天偏移随时发出；如需控制送达时段或放量节奏，请在调用侧分批建 campaign。

单封手动直发（审批后）：`POST :8100/api/v1/email-drafts/{draft_id}/send` `{"sequence_number": 1}`

### ⑦ 回信检测（marketing，自动）

`EMAIL_REPLY_DETECTION_ENABLED=true` + IMAP 配置后，检测到客户回复即把序列标记 `replied` 并自动取消后续跟进信。手动触发：`POST :8100/api/v1/email-replies/check`。

## 4. 端点参考

### hunter（:8000）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/v1/health` | 健康检查 |
| POST | `/api/v1/upload` | 上传资料文件（≤50MB，txt/md/pdf/docx/xlsx/csv/json） |
| POST | `/api/v1/hunts` | 创建获客任务（直连后台执行） |
| GET | `/api/v1/hunts` | 任务列表 |
| GET | `/api/v1/hunts/{id}/status` | 状态/阶段/轮次/计数 |
| GET | `/api/v1/hunts/{id}/result` | 完整结果（线索/关键词/统计） |
| GET | `/api/v1/hunts/{id}/cost` | Token 成本摘要 |
| POST | `/api/v1/hunts/{id}/resume` | 续跑已结束任务 |
| GET | `/api/v1/hunts/{id}/stream` | SSE 实时进度 |
| GET | `/api/v1/leads` | 全局线索库（`status=`、`domain=`、`hunt_id=` 过滤；分页用 `limit`/`offset`） |
| GET | `/api/v1/leads/{id}` | 线索详情（含出现历史） |
| GET | `/api/v1/hunts/{id}/leads` | 任务的线索 |
| GET | `/api/v1/hunts/{id}/export` | **导出该任务的线索为 xlsx**（业务交付，见 §4.1） |
| POST | `/api/v1/automation/jobs` | 入队获客任务 |
| GET | `/api/v1/automation/jobs[/{id}]` | 队列查询（含 `last_hunt_id`/进度） |
| POST | `/api/v1/automation/jobs/{id}/cancel` · `/{id}/retry` | 取消/重试 |
| GET | `/api/v1/automation/status` · `/metrics` · `/health` | 队列状态/指标/积压健康检查 |
| POST | `/api/v1/email-template-seeds/prepare` | 预生成邮件模板种子 |

### marketing（:8100）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/v1/health` | 健康检查 |
| GET | `/review` | 人工审批页（浏览器） |
| GET | `/api/v1/email-drafts` | 草稿列表（`status=draft/approved/rejected`、`hunt_id=`） |
| GET | `/api/v1/hunts/{hunt_id}/email-drafts` | 某任务的草稿 |
| POST | `/api/v1/email-drafts/{id}/decision` | 审批/拒绝（批准即触发自动建 campaign） |
| POST | `/api/v1/email-drafts/{id}/send` | 手动单发某一步（需先批准） |
| POST | `/api/v1/hunts/{hunt_id}/email-campaigns` | 建 campaign（只收已批准草稿） |
| GET | `/api/v1/hunts/{hunt_id}/email-campaigns` | campaign 列表 + 实时摘要 |
| POST | `/api/v1/email-campaigns/{id}/start` · `/pause` | 启动/暂停 |
| GET | `/api/v1/email-sequences/{id}` | 序列详情（消息/回信事件） |
| POST | `/api/v1/email-scheduler/run` | 手动触发一次发送调度 |
| POST | `/api/v1/email-replies/check` | 手动触发一次回信检测 |
| GET/POST | `/api/settings` | 设置读写（脱敏返回） |
| POST | `/api/settings/email/test` · `/email/imap-test` | SMTP/IMAP 连通测试 |

## 5. SSE 事件（`/api/v1/hunts/{id}/stream`）

`stage_change`（阶段切换）、`stage_data`（阶段明细）、`round_change`（搜索轮次）、`progress`（计数心跳）、`lead_progress`（逐线索产出）、`completed`、`failed`、`heartbeat`。

队列任务另有 `GET /api/v1/automation/jobs/{job_id}/stream`。

## 6. 错误码语义

| 码 | 场景 |
|---|---|
| 401/403 | 缺失/错误 API token（非 localhost） |
| 404 | hunt/草稿/campaign/序列不存在 |
| 409 | 前置条件不满足：SMTP 未配置/未测试、草稿未批准即发送、campaign 重复启动 |
| 422 | 请求字段非法（如线索数为 0） |
| 400 | 上游执行失败（SMTP 拒绝等，detail 带原因） |

## 7. 部署与配置

见同目录 `DEPLOY.md`；全部配置项见 `.env.example`。关键项：`DATABASE_URL`（两服务同库）、`API_ACCESS_TOKEN`、LLM/搜索 Key、`EMAIL_AUTO_SEND_ENABLED`、`AUTOMATION_CONSUMER_AUTO_START_CAMPAIGN`。

> ⚠️ 调度器单例约束：hunter/marketing 拆分模式与合并模式（api.app）二选一部署，否则邮件会双发。
