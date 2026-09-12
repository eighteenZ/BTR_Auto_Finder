# AI Hunter

> 面向外贸与 B2B 场景的自动化客户挖掘引擎，基于 FastAPI、LangGraph 与多 Agent 流水线，可配置多模型。

AI Hunter 只需要提供公司官网、产品文档或产品关键词，再指定目标市场，即可自动完成公司理解、关键词生成、网页搜索、线索提取与联系方式发现，并可选择生成外联邮件、进入自动发送队列。

## 能力范围

- 多 Agent 流水线：`Insight -> KeywordGen -> Search -> LeadExtract -> Evaluate`
- 双模型协作：推理模型负责 ReAct 决策，普通模型负责抽取、生成与改写
- 输入灵活：官网 URL、PDF/Excel/CSV/Word/Markdown/TXT 文件、或纯关键词
- 多搜索通道：Google Search、Google Maps、B2B 平台站内搜索
- 智能抓取：针对官网、B2B 列表页、内容页自适应抓取策略
- 联系方式发现：邮箱、电话、地址、社媒链接等结构化信息
- AI 邮件生成：基于 ICP、官网洞察与历史样例生成 3 步开发信序列
- 邮件预览与审核：批准 / 拦截 / 手动发送 / 回信检测
- 邮件自动发送：已批准序列可创建 campaign，交由 scheduler 持久化发送
- 实时进度：FastAPI + SSE 推送任务进展
- 成本可观测：接入 Langfuse 记录 LLM 调用成本、Token 与延迟
- 可替换模型：统一通过 LiteLLM 接入 OpenAI、Anthropic、OpenRouter、Groq、GLM、Moonshot、MiniMax
- 队列化运行：PostgreSQL 持久化任务队列 + 内嵌 consumer / scheduler
- 持久化与去重：SQLAlchemy 2.0 + Alembic；全局线索仓库，跨 hunt 复用已有线索
- 服务门面：可直接 `import` 调用获客能力，便于对接自建 FastAPI

## 架构图

```mermaid
flowchart TB
    subgraph Callers[调用方]
        C1[HTTP Client / Swagger]
        C2["自建 FastAPI / 脚本<br/>services.hunter_service.run_hunt"]
        C3[Queue Producer]
    end

    subgraph App[FastAPI 服务层]
        A1["api/routes<br/>hunts / upload / SSE"]
        A2["api/automation_routes<br/>jobs / status / metrics"]
        A3["api/email_routes<br/>campaign / scheduler"]
        A4["api/leads_routes<br/>线索查询"]
        A5["api/settings_routes"]
    end

    subgraph Core[获客引擎 LangGraph]
        B0[ParseDescription]
        B1[InsightAgent]
        B2[KeywordGenAgent]
        B3[SearchAgent]
        B4["LeadExtractAgent<br/>+ 全局去重 / 复用"]
        B5[Evaluate]
        B6[EmailCraft]
        B0 --> B1 --> B2 --> B3 --> B4 --> B5
        B5 -->|继续| B2
        B5 -->|结束| B6
    end

    subgraph Repo[持久化层 persistence]
        R1["LeadRepository<br/>upsert / find_by_domains"]
        R2["HuntRepository"]
        R3["HuntJobQueue"]
        R4["EmailStore"]
    end

    subgraph PG[PostgreSQL]
        P1[(hunts)]
        P2[(leads / lead_sightings / hunt_leads)]
        P3[(hunt_jobs)]
        P4[(email_accounts / campaigns / sequences / messages / replies)]
        P5[(LangGraph checkpoints)]
    end

    subgraph External[外部服务]
        X1[LiteLLM<br/>MiniMax / OpenAI / Anthropic / OpenRouter / Groq / GLM / Moonshot]
        X2[Tavily / Serper]
        X3[Jina Reader]
        X4[SMTP / IMAP]
        X5[Feishu Webhook]
    end

    C1 --> App
    C2 --> B0
    C3 --> R3
    A1 --> B0
    A2 --> R3
    A3 --> R4
    A4 --> R1
    A1 --> R1
    A1 --> R2

    B1 --> X1
    B2 --> X1
    B4 --> X1
    B3 --> X2
    B4 --> X3
    B6 --> X1
    A3 --> X4
    A2 --> X5

    R1 --> P2
    R2 --> P1
    R3 --> P3
    R4 --> P4
    B0 -.LangGraph checkpoint.-> P5
```

### 分层说明

| 层 | 模块 | 职责 |
| --- | --- | --- |
| 服务层 | `api/` | HTTP/SSE 接口、鉴权、任务编排 |
| 能力层 | `services/` | 可直接 import 的获客门面，供自建 FastAPI 复用 |
| 引擎层 | `agents/` + `graph/` | LangGraph 流水线与各 Agent |
| 持久化层 | `persistence/` | SQLAlchemy 模型、仓储、去重身份、会话/事务 |
| 迁移 | `alembic/` | 版本化建表/变更 |
| 队列/调度 | `automation/` + `emailing/` | 任务队列、邮件发送与回信检测 |
| 工具 | `tools/` | 搜索、抓取、LLM、解析、联系方式抽取 |

### 数据模型

```mermaid
erDiagram
    HUNTS ||--o{ HUNT_LEADS : links
    LEADS ||--o{ HUNT_LEADS : "referenced by"
    LEADS ||--o{ LEAD_SIGHTINGS : "seen in"
    LEADS {
        string id PK
        string lead_key UK "d:domain | e:email | x:hash"
        string domain
        string company_name
        jsonb emails
        jsonb phone_numbers
        jsonb decision_makers
        int seen_count
        string first_seen_at
        string last_seen_at
    }
    HUNTS {
        string id PK
        string status
        int leads_count
        jsonb data
    }
    HUNT_LEADS {
        string hunt_id FK
        string lead_id FK
        int reused
    }
    LEAD_SIGHTINGS {
        bigint id PK
        string lead_id FK
        string hunt_id
        string source_keyword
    }
    HUNT_JOBS {
        string id PK
        string status
        string available_at
        string last_hunt_id
    }
    EMAIL_SEQUENCES ||--o{ EMAIL_MESSAGES : contains
    HUNTS ||--o{ EMAIL_CAMPAIGNS : spawns
    EMAIL_CAMPAIGNS ||--o{ EMAIL_SEQUENCES : contains
```

## 工作流

```mermaid
flowchart TD
    A[输入官网 / 上传文档 / 产品关键词] --> B[InsightAgent\n理解公司与产品]
    B --> C[KeywordGenAgent\n生成搜索词]
    C --> D[SearchAgent\n聚合搜索结果]
    D --> E{LeadExtractAgent}
    E -->|命中已有线索| E1[复用：合并联系方式\n标记 reused / 跳过深抓]
    E -->|新线索| E2[深抓 + 抽取结构化线索]
    E1 --> F
    E2 --> F
    F[Evaluate\n判断是否继续]
    F -->|继续| C
    F -->|结束| G[EmailCraft\n可选生成邮件序列]
    G --> H[写入 PostgreSQL\nhunts / leads / hunt_leads]
    H --> I[返回 leads / 邮件 / 成本 / 阶段结果]
```

停止逻辑由以下参数控制：

- `target_lead_count`：目标线索总数
- `max_rounds`：最多迭代轮数
- `min_new_leads_threshold`：单轮最少新增线索数

## 目录结构

```text
AI_Hunter/
├── agents/           # 各类 Agent
├── api/              # FastAPI 路由、SSE
├── automation/       # PostgreSQL 任务队列、metrics、通知
├── config/           # 配置读取与 .env 写入
├── persistence/      # SQLAlchemy 模型、仓储、去重身份
├── alembic/          # 数据库迁移
├── docs/             # 补充文档
├── emailing/         # 邮件生成、SMTP/IMAP、scheduler
├── graph/            # LangGraph StateGraph 与流程控制
├── observability/    # Langfuse / 成本追踪
├── prompts/          # 提示词
├── scripts/          # 队列 producer、headless worker、ETL、演示脚本
├── services/         # 可直接 import 的服务门面
├── tests/            # pytest 测试
├── main.py           # PyInstaller / uvicorn 入口
├── alembic.ini
├── requirements.txt
└── pyproject.toml
```

## 环境要求

- Python `3.11+`
- PostgreSQL `14+`
- 至少一个 LLM API Key
- 至少一个搜索 API Key（Tavily / Serper）

## 快速开始

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env，至少设置 DATABASE_URL 与模型/搜索 Key

alembic upgrade head            # 建表
uvicorn api.app:app --host 127.0.0.1 --port 8000
```

- API：`http://127.0.0.1:8000`
- Swagger：`http://127.0.0.1:8000/docs`

所有运行时密钥与参数都放在项目根目录的 `.env`。也可以通过 `Settings API` 写入，需在 `.env` 中设置 `SETTINGS_API_ENABLED=true`。

## 持久化与去重

所有状态存储于 PostgreSQL（SQLAlchemy 2.0 同步 ORM + Alembic 迁移），不再使用 SQLite：

- `hunts`：hunt 元数据与完整结果（JSONB），原子 upsert
- `leads`：**全局线索仓库**，唯一键 `lead_key`
- `lead_sightings` / `hunt_leads`：线索来源与 hunt 关联
- `hunt_jobs`：持久化任务队列
- `email_*`：campaign / sequence / message / reply
- LangGraph checkpoint 使用 `langgraph-checkpoint-postgres`

`lead_key` 规则：官网域名（规范化）→ `d:<domain>`；否则邮箱 → `e:<email>`；否则公司名+国家的哈希 → `x:<hash>`。

获客流程内分层去重：

1. 搜索 URL：`seen_urls`（单 hunt 内）
2. 线索抽取：按官网域名/hunt 内去重
3. **全局复用**：命中仓库已有线索时合并联系方式、标记 `reused=true`、跳过深抓；由 `LEAD_DEDUP_MODE` 控制（`reuse|skip|off`，可用请求字段 `dedup_mode` 覆盖）
4. 外联：创建 campaign 时按统一 `lead_key` 拦截已联系对象（旧键双读兼容）

迁移历史 SQLite 数据：

```bash
python scripts/migrate_sqlite_to_postgres.py --dry-run   # 先看数量
python scripts/migrate_sqlite_to_postgres.py             # 执行迁移
```

## 配置

```env
# 数据库
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/ai_hunter

# 线索去重 / 复用
LEAD_DEDUP_MODE=reuse
LEAD_REUSE_ENRICHMENT=true
LEAD_REUSE_MIN_CONTACTS=1

# LLM（litellm 命名）
LLM_MODEL=minimax/MiniMax-M2.1-highspeed
REASONING_MODEL=minimax/MiniMax-M2.5
MINIMAX_API_KEY=your-minimax-key
MINIMAX_API_BASE=https://api.minimax.io/v1

# 搜索
SERPER_API_KEY=        # Google Maps 搜索
TAVILY_API_KEY=        # 通用搜索，支持多 key 逗号分隔轮换
JINA_API_KEY=          # 网页抓取

# 邮件（可选）
EMAIL_FROM_NAME=Your Company
EMAIL_FROM_ADDRESS=sales@example.com
EMAIL_SMTP_HOST=smtp.exmail.qq.com
EMAIL_SMTP_PORT=465
EMAIL_SMTP_USERNAME=sales@example.com
EMAIL_SMTP_PASSWORD=your-app-password
EMAIL_IMAP_HOST=imap.exmail.qq.com
EMAIL_IMAP_PORT=993
EMAIL_IMAP_USERNAME=sales@example.com
EMAIL_IMAP_PASSWORD=your-app-password
EMAIL_AUTO_SEND_ENABLED=false
EMAIL_REPLY_DETECTION_ENABLED=false
EMAIL_REQUIRE_APPROVAL_BEFORE_SEND=true
```

说明：

- 邮件链路未单独配置 `EMAIL_LLM_MODEL` / `EMAIL_*_API_KEY` 时，会回退到主链路配置
- `EMAIL_REQUIRE_APPROVAL_BEFORE_SEND=true` 时，发送前必须人工批准
- 自动发送 / 回信检测开启前，需先通过 `POST /api/settings/email/test` 与 `/api/settings/email/imap-test` 验证连接
- 非 localhost 访问 API 时，若未配置 `API_ACCESS_TOKEN`，接口默认只允许本机访问

完整变量见 [`.env.example`](.env.example)。

## 支持的输入与上传限制

支持：官网 URL、产品关键词、目标客户画像、目标地区、上传文件作为补充语料。

允许上传：`.txt` `.md` `.pdf` `.docx` `.doc` `.xlsx` `.xls` `.csv` `.json`，默认单文件上限 `50 MB`。

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/health` | 健康检查 |
| POST | `/api/v1/upload` | 上传文件，返回 `uploaded_file_ids` |
| POST | `/api/v1/hunts` | 创建挖掘任务 |
| GET | `/api/v1/hunts` | 任务列表 |
| GET | `/api/v1/hunts/{id}/status` | 任务状态 |
| GET | `/api/v1/hunts/{id}/result` | 任务结果（线索 / 邮件 / 关键词 / 成本） |
| GET | `/api/v1/hunts/{id}/stream` | SSE 实时进度 |
| POST | `/api/v1/hunts/{id}/resume` | 基于已有线索继续挖掘 |
| GET | `/api/v1/hunts/{id}/cost` | 成本汇总 |
| POST | `/api/v1/hunts/{id}/email-sequences/{i}/decision` | 批准 / 拦截邮件序列 |
| POST | `/api/v1/hunts/{id}/email-sequences/{i}/send` | 手动发送单封草稿 |
| POST | `/api/v1/email-template-seeds/prepare` | 预生成邮件模板种子 |
| POST | `/api/v1/hunts/{id}/email-campaigns` | 创建 campaign |
| POST | `/api/v1/email-campaigns/{id}/start` | 启动 campaign |
| POST | `/api/v1/email-campaigns/{id}/pause` | 暂停 campaign |
| POST | `/api/v1/automation/jobs` | 入队一个自动化任务 |
| GET | `/api/v1/automation/jobs` | 队列任务列表 |
| GET | `/api/v1/automation/jobs/{id}` | 队列任务详情 |
| POST | `/api/v1/automation/jobs/{id}/cancel` | 取消任务 |
| POST | `/api/v1/automation/jobs/{id}/retry` | 重试任务 |
| GET | `/api/v1/automation/status` | 队列状态 |
| GET | `/api/v1/automation/metrics` | 指标统计 |
| GET | `/api/v1/leads` | 查询全局线索（分页 / domain / hunt_id） |
| GET | `/api/v1/leads/{lead_id}` | 线索详情（含来源 sighting） |
| GET | `/api/v1/hunts/{id}/leads` | 某 hunt 关联的规范线索 |
| GET | `/api/settings` | 读取设置（敏感字段打码） |
| POST | `/api/settings` | 写入设置到 `.env` |
| POST | `/api/settings/email/test` | 测试 SMTP |
| POST | `/api/settings/email/imap-test` | 测试 IMAP |
| POST | `/api/settings/automation/feishu-test` | 测试飞书 webhook |

## 服务门面（直接 import）

如果要在自建 FastAPI 中直接调用获客能力，而无需经过本项目的 HTTP 接口：

```python
from services.hunter_service import HuntRequest, run_hunt

outcome = await run_hunt(
    HuntRequest(
        website_url="https://example.com",
        description="我想找东南亚的旅行社",
        target_regions=["Thailand", "Vietnam"],
        target_lead_count=50,
        enable_email_craft=False,
    )
)

for lead in outcome.leads:
    print(lead["company_name"], lead.get("emails"))
```

`run_hunt` 在进程内执行完整流水线并返回 `HuntOutcome`（`leads` / `email_sequences` / `insight` / `cost_summary` 等）。
默认 `persist=True`：hunt 与线索会写入 PostgreSQL，并与全局仓库合并复用；传 `dedup_mode="reuse|skip|off"` 可覆盖默认策略。
如需并发执行，请使用队列 + consumer 模式；门面中的进度回调为进程级单例，同一进程内不支持并发 hunt。

## 队列与 Worker 模式

两层 PostgreSQL 持久化队列：

- `hunt_jobs`：待执行的挖掘任务（producer 入队，consumer 领取）
- `email_messages`：待发送的邮件（创建 campaign 入队，scheduler 发送）

常驻进程拆分为：

- API 服务：hunt、内嵌 consumer、template seed prewarm、campaign API、发送 scheduler、回信检测
- Producer 服务：持续向 `hunt_jobs` 写入新任务

```bash
# 1. 启动 API（含内嵌 consumer）
uvicorn api.app:app --host 0.0.0.0 --port 8000

# 2. 启动 producer（持续入队）
python scripts/hunt_queue.py producer \
  --payload-file ./automation_job.json \
  --continuous --enqueue-interval-seconds 60 --max-pending-jobs 3
```

串行兼容模式（非推荐）：

```bash
python scripts/headless_worker.py \
  --payload-file ./automation_job.json \
  --continuous --auto-start-campaign \
  --cycle-interval-seconds 60 --status-poll-seconds 15
```

## 部署（systemd）

`deploy/systemd/` 提供了三个 unit：

- `ai-hunter-api.service`
- `ai-hunter-producer.service`
- `ai-hunter-worker.service`（兼容的串行 worker）

部署前：

1. 把 unit 内的 `/opt/ai-hunter` 替换为实际路径
2. 准备好 PostgreSQL，并在 `.env` 中设置 `DATABASE_URL`
3. 首次启动前执行 `alembic upgrade head` 建表
4. 将 producer / worker 的 `--payload-file` 指向 `automation_job.json`

## 测试与 Lint

```bash
# 未提供 DATABASE_URL 时，测试会用 testcontainers 启动临时 PostgreSQL（需 Docker）
pytest -m "not live"
ruff check .
```

也可以直接指定已有数据库：

```bash
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/ai_hunter_test pytest -m "not live"
```

## License

见 [LICENSE](LICENSE)。
