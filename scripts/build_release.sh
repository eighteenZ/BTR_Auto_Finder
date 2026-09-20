#!/usr/bin/env bash
# Build the release branch: artifacts only, no source code.
#
# Produces an orphan `release` branch containing:
#   ai_hunter-<ver>-py3-none-any.whl   # the compiled project wheel
#   requirements.lock.txt              # exact versions of the tested env
#   openapi-hunter.json / openapi-marketing.json
#   docs/API.md  DEPLOY.md  deploy/systemd/*  .env.example
#
# Usage:  bash scripts/build_release.sh [--keep-work]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PY:-$ROOT/.venv/bin/python}"
RELEASE_BRANCH="${RELEASE_BRANCH:-release}"
WORK="$(mktemp -d /tmp/ai-hunter-release.XXXXXX)"
KEEP_WORK="${1:-}"

cd "$ROOT"

# NOTE: the working tree may legitimately contain parallel work-in-progress
# (other agents/humans). The wheel is built from `git archive HEAD`, so WIP
# stays out of the artifact; no clean-tree requirement any more.

echo "── 1/6 build wheel (from the committed tree)"
rm -rf "$WORK/src" "$WORK/dist"
mkdir -p "$WORK/src"
# Build from the COMMITTED snapshot: parallel work-in-progress in the working
# tree (other agents/humans) must never leak into a release artifact.
git archive HEAD | tar -x -C "$WORK/src"
"$PY" -m pip wheel "$WORK/src" --no-deps -w "$WORK/dist" -q
WHEEL="$(ls "$WORK/dist"/ai_hunter-*.whl | head -1)"
echo "  → $(basename "$WHEEL")"

echo "── 2/5 lock dependencies"
"$PY" -m pip freeze --exclude-editable > "$WORK/requirements.lock.txt"

echo "── 3/6 export OpenAPI specs and schema"
SCHEMA_DB="ai_hunter_schema_dump"
"$PY" - "$WORK" "$SCHEMA_DB" <<'EOF'
import json, sys, pathlib
sys.path.insert(0, ".")
from api.hunter_app import create_hunter_app
from api.marketing_app import create_marketing_app

out = pathlib.Path(sys.argv[1])
for name, app in (("openapi-hunter.json", create_hunter_app()),
                  ("openapi-marketing.json", create_marketing_app())):
    (out / name).write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  → {name}")

# Dump the schema against a throwaway database — never the real one.
import re
from urllib.parse import urlparse
from persistence.db import get_session_factory
from sqlalchemy import create_engine, text
from sqlalchemy.schema import CreateTable
from persistence.models import Base
from config.settings import get_settings

real = get_settings().database_url
parts = urlparse(real)
tmp_url = re.sub(r"/[^/?]+$", f"/{sys.argv[2]}", real)
admin = create_engine(real, isolation_level="AUTOCOMMIT")
with admin.connect() as c:
    c.execute(text(f'DROP DATABASE IF EXISTS "{sys.argv[2]}"'))
    c.execute(text(f'CREATE DATABASE "{sys.argv[2]}"'))
admin.dispose()

tmp_engine = create_engine(tmp_url)
Base.metadata.create_all(tmp_engine)
dump = tmp_engine.dialect
stmts = "\n\n".join(
    str(CreateTable(table).compile(dialect=dump)).strip() + ";"
    for table in Base.metadata.sorted_tables
)
(out / "schema.sql").write_text(
    "-- AI Hunter full schema — apply ONCE on an EMPTY database:\n"
    "--   psql \"$PSQL_URL\" -f schema.sql\n"
    "-- Generated from persistence.models at build time.\n\n" + stmts + "\n",
    encoding="utf-8",
)
tmp_engine.dispose()
admin = create_engine(real, isolation_level="AUTOCOMMIT")
with admin.connect() as c:
    c.execute(text(f'DROP DATABASE IF EXISTS "{sys.argv[2]}"'))
admin.dispose()
print("  → schema.sql (from throwaway db)")
EOF

echo "── 4/6 assemble release tree"
mkdir -p "$WORK/payload/docs" "$WORK/payload/deploy/systemd" "$WORK/payload/scripts"
# Ops scripts the server actually runs (account bootstrap + draft repair).
cp "$ROOT/scripts/create_user.py" "$ROOT/scripts/repair_draft_placeholders.py" "$WORK/payload/scripts/"
cp "$WHEEL" "$WORK/payload/"
cp "$WORK/requirements.lock.txt" "$WORK/payload/"
cp "$WORK/openapi-hunter.json" "$WORK/openapi-marketing.json" "$WORK/schema.sql" "$WORK/payload/"
cp "$ROOT/docs/API.md" "$WORK/payload/docs/"
cp "$ROOT/deploy/systemd/"*.service "$WORK/payload/deploy/systemd/"
cp "$ROOT/.env.example" "$WORK/payload/"
cat > "$WORK/payload/DEPLOY.md" <<'DEPLOY_DOC'
# AI Hunter 部署指南（release 分支）

本分支只含编译产物与部署文件，无源码。全部命令在服务器执行。

**前置要求**：Ubuntu 22.04+ / Debian 12+（或同等发行版）、Python >= 3.11、PostgreSQL >= 14、可访问 PyPI 与 LLM/搜索 API。

```bash
export REPO=https://github.com/eighteenZ/BTR_Auto_Finder.git
export APP_DIR=/opt/ai-hunter
```

## 1. 安装系统依赖

```bash
sudo apt update
sudo apt install -y git python3-venv python3-pip
python3 --version          # 需 >= 3.11（Ubuntu 24.04 自带 3.12；Debian 12 自带 3.11）
```

若版本 <= 3.10（Ubuntu 22.04 自带 3.10），默认源没有新版 Python，需加 PPA：

```bash
sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt update
sudo apt install -y python3.12 python3.12-venv
# 之后把所有命令里的 python3 换成 python3.12
```

> 报 `ensurepip is not available` = 缺 `python3-venv`（或对应版本的 `python3.X-venv`）包，装它即可。

## 2. 拉取产物并安装依赖

```bash
sudo mkdir -p "$APP_DIR" && sudo chown "$USER" "$APP_DIR"
git clone -b RELEASE_BRANCH "$REPO" "$APP_DIR"
cd "$APP_DIR"

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install --timeout 180 --retries 10 -r requirements.lock.txt ai_hunter-*.whl
```

- 中国大陆服务器可加镜像：`-i https://mirrors.aliyun.com/pypi/simple/`
- 若某个包报 `from versions: none`，通常是索引响应被中断（大包索引页可达 1MB+），已加 `--retries 10` 会自愈；仍失败则换镜像重跑

验证依赖完整：

```bash
.venv/bin/python -c "import fastapi, sqlalchemy, litellm, langgraph, psycopg, aiohttp; print('OK')"
```

## 3. 安装并初始化 PostgreSQL

已有 PostgreSQL 14+ 时跳过安装，只做建库建用户。

```bash
sudo apt install -y postgresql
sudo systemctl enable --now postgresql
pg_lsclusters        # 期望：16  main  5432  online
```

> `systemctl enable postgresql` 报 `Unit file postgresql.service does not exist` = 服务端没装。
> `postgresql-client-common` 只是客户端包装，不含服务端与集群。

建库建用户（密码换成你自己的，建议纯字母数字，避免 URL 转义问题）：

```bash
sudo -u postgres psql <<'SQL'
CREATE USER ai_hunter WITH PASSWORD 'YOUR_PASSWORD';
CREATE DATABASE ai_hunter OWNER ai_hunter;
SQL
```

验证 TCP + 密码认证（应用就是这么连的，管理用的 peer 认证不算）：

```bash
psql "postgresql://ai_hunter:YOUR_PASSWORD@localhost:5432/ai_hunter" -c "SELECT current_user, current_database();"
```

## 4. 配置 .env

```bash
cd "$APP_DIR"
cp .env.example .env
vi .env
```

必填：

| 变量 | 说明 |
| --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://ai_hunter:密码@localhost:5432/ai_hunter`（注意 `+psycopg`） |
| LLM key | 如 `DEEPSEEK_API_KEY`，需与 `LLM_MODEL` / `REASONING_MODEL` 匹配 |
| `SERPER_API_KEY` | Google Maps 搜索（搜索阶段必需） |
| `API_ACCESS_TOKEN` | 对外提供服务时必填，否则任何人可调用接口 |

邮件相关（可后置，但发送前必须配）：

| 变量 | 说明 |
| --- | --- |
| `EMAIL_FROM_ADDRESS` / `EMAIL_SMTP_USERNAME` | 发信地址，两者相同 |
| `EMAIL_SMTP_PASSWORD` | 服务商提供的 SMTP 密码（非邮箱登录密码） |
| `EMAIL_SMTP_HOST` / `EMAIL_SMTP_PORT` | 例：阿里云 DirectMail 美区 `smtpdm-us-east-1.aliyuncs.com`。端口与 TLS 的组合：`465` + `EMAIL_USE_TLS=true`（隐式 SSL）或 `80` + `EMAIL_USE_TLS=true`（STARTTLS——服务端 EHLO 广告 STARTTLS，实测升级 TLSv1.3 成功；程序两条发送路径均自动处理）。排错：`SSL: WRONG_VERSION_NUMBER` = 用**隐式 SSL** 连了明文/STARTTLS 端口（如以 SSL 方式连 80，或 openssl/邮件客户端按 SSL 模式测 80），不是端口不支持 TLS |
| `EMAIL_REPLY_TO` | 接收客户回复的邮箱（DirectMail 只发不收） |
| `EMAIL_AUTO_SEND_ENABLED` | **先保持 false**，SMTP 测试通过后再改为 true |

## 5. 建表

```bash
cd "$APP_DIR"
PSQL_URL=$(grep '^DATABASE_URL=' .env | sed 's/^DATABASE_URL=//; s/+psycopg//')
psql "$PSQL_URL" -f schema.sql
psql "$PSQL_URL" -c '\dt'        # 应列出 16 张表
```

> schema 只建表，不需要扩展或特殊权限；仅能在空库上执行一次。

## 6. 创建管理员账号（账号系统）

系统用企业邮箱登录（IMAP 验证凭据，不存密码），必须先建一个 admin 才能使用界面：

```bash
cd "$APP_DIR"
# .env 需已配置 EMAIL_IMAP_HOST（企业邮箱的 IMAP 服务器，如 imap.exmail.qq.com）
.venv/bin/python scripts/create_user.py --admin --email you@company.com
# 按提示输入邮箱密码做一次 IMAP 验证；成功即建号并打印个人 api_key
# 邮件服务器不可达时可加 --no-verify 跳过验证；其他子命令见 --help
```

- 登录入口 `:8100/login`；审批页 `:8100/review` 未登录会跳转登录页
- 程序化调用（openclaw）用打印出的个人 api_key（`X-API-Key` 头）
- 旧共享 `API_ACCESS_TOKEN` 仍有效（admin 级过渡通道），不配置即关闭
- ⚠️ 设置接口（SMTP 密码/LLM key）现在**仅 admin 可访问**

## 7. 启动服务

```bash
cd "$APP_DIR"
sudo cp deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ai-hunter-api ai-marketing-api
sleep 6
systemctl is-active ai-hunter-api ai-marketing-api
```

| 服务 | 端口 | 用途 |
| --- | --- | --- |
| ai-hunter-api | 8000 | 获客流水线 / 队列 / 线索库 / SSE（Swagger `/docs`） |
| ai-marketing-api | 8100 | 草稿审批 / campaign / 发送 / 回信检测（审批页 `/review`，Swagger `/docs`） |

排障：`sudo journalctl -u ai-hunter-api -n 50 --no-pager`

## 8. 验证

```bash
curl -s localhost:8000/api/v1/health; echo
curl -s localhost:8100/api/v1/health; echo
```

端到端（会消耗少量 LLM 额度）：

```bash
curl -s -X POST localhost:8000/api/v1/hunts -H 'Content-Type: application/json' \
  -d '{"website_url":"https://example.com/","description":"Find US importers buying from China","product_keywords":["importer of Chinese goods"],"target_regions":["United States"],"target_lead_count":1,"max_rounds":1,"enable_email_craft":false}'
# 轮询 /api/v1/hunts/{hunt_id}/status 至 completed，然后核对落库：
psql "$PSQL_URL" -c 'SELECT count(*) FROM hunts;'    # 应 > 0
```

> ⚠️ 这步落库验证必须做。存储层在写库失败时只打 warning、不会让任务失败，
> 所以 `DATABASE_URL` 填错时服务看起来完全正常（health 返回 ok），但数据一条都不落库。

## 9. 安全

```bash
# 1) 生成并填入 API_ACCESS_TOKEN，然后重启
openssl rand -hex 32

# 2) 只放行需要对外提供的端口
sudo ufw allow 8000/tcp
sudo ufw allow 8100/tcp

# 3) 确认数据库未对外暴露
sudo ss -tlnp | grep 5432        # 应只监听 127.0.0.1
```

## 10. 邮件启用顺序

```bash
curl -X POST localhost:8100/api/settings/email/test         # SMTP 连通（发送资格的前置条件）
curl -X POST localhost:8100/api/settings/email/imap-test    # 回信检测需要
# 通过后把 .env 的 EMAIL_AUTO_SEND_ENABLED 改为 true，重启 ai-marketing-api
```

# 另需开启自动回信检测（与人工审核互不冲突）：
#   .env 里 EMAIL_REPLY_DETECTION_ENABLED=true
#   前置条件：上面 imap-test 必须已成功执行过一次（否则检测线程每轮抛
#   "IMAP connection has not been verified yet"）。测试时间戳与 .env 同文件落盘，
#   若怀疑路径跑偏，用第 11 节的一致性验证脚本检查。

## 11. .env 读写路径一致性（wheel 安装）

**历史 bug（已在本 wheel 中修复）**：早期构建里 `config/settings.py`（读配置）优先看
`Path.cwd()/.env`，而 `config/settings_store.py`（写配置）按 `Path(__file__).parent.parent`
推导，直接落在 `.venv/lib/pythonX.Y/site-packages/.env`。

后果：**在设置页保存的任何配置（SMTP/IMAP/LLM/开关）都不会写入 `/opt/ai-hunter/.env`**，
只写进 venv 里一个孤立文件，服务重启后全部丢失；同时 IMAP 测试时间戳无法落盘，
导致「自动回复检测」即使开启也会每轮报
`IMAP connection has not been verified yet`。

**现状**：两个模块已统一为同一套 CWD 优先解析（上游修复已包含在本 wheel 中），
正常部署无需任何手工步骤。曾按旧文档做过 site-packages 符号链接的环境，
升级后该链接不再需要，建议删除以免混淆。

**验证读写指向同一文件**：

```bash
cd /opt/ai-hunter && sudo .venv/bin/python - <<'EOF'
from pathlib import Path
from config.settings_store import get_env_path
from config.settings import _resolve_env_file
print("write:", get_env_path())
print("read :", _resolve_env_file())
print("same :", get_env_path().resolve() == Path(_resolve_env_file()).resolve())   # 必须 True
EOF
```

`same : False` 时清理残留符号链接并重验：

```bash
SP=$(/opt/ai-hunter/.venv/bin/python -c "import sysconfig;print(sysconfig.get_paths()['purelib'])")
[ -L "$SP/.env" ] && rm -f "$SP/.env" && echo "stale symlink removed"
```

## 12. 已知限制

发送调度器只按 `scheduled_at` 判定，**不检查工作时间、时区、工作日、每日/每小时限流**
（配置项存在但未被消费）。邮件会在 campaign 启动后按 0/3/7 天偏移随时发出，
放量节奏需自行控制（如分小批建 campaign）。

## 13. 升级

release 分支是线性累积的，升级即快进拉取。先固定拉取策略，避免误产生 merge：

```bash
cd "$APP_DIR" && git config pull.ff only
```

**首次过渡（仅需一次）**：早期版本的 release 提交是各自独立重建的，与本地已有提交没有共同祖先，
直接 pull 会提示 divergent branches。执行一次：

```bash
git fetch origin release && git reset --hard origin/release
```

> `.env` 与 `.venv` 未被 git 跟踪（分支自带 .gitignore），`reset --hard` 不会删除它们。

**之后每次升级**：

```bash
cd "$APP_DIR"
git pull                                   # 快进拉取新产物
.venv/bin/pip install --force-reinstall -r requirements.lock.txt ai_hunter-*.whl
# 若升级说明提到新表： psql "$PSQL_URL" -f schema.sql
sudo systemctl restart ai-hunter-api ai-marketing-api
```

> ⚠️ **升级后建议复查第 11 节的路径一致性**：`--force-reinstall` 若清空过
> `site-packages`，旧部署时代创建的 `site-packages/.env` 符号链接会一并丢失。
> 跑一遍第 11 节的验证脚本，`same : False` 就按其指引清理残留链接再重验。
> 从未做过符号链接修复的环境不受影响。

⚠️ 拆分模式与合并模式（`api.app:app`）二选一部署；`EMAIL_AUTO_SEND_ENABLED` 只能由一个进程承载，
否则同一封邮件会被两个调度器重复发送。

完整 API 说明见 docs/API.md。
DEPLOY_DOC

sed -i "s|RELEASE_BRANCH|$RELEASE_BRANCH|g" "$WORK/payload/DEPLOY.md"
ls -la "$WORK/payload"

echo "── 5/6 commit branch '$RELEASE_BRANCH' (plumbing — never touches the working tree)"
START_BRANCH="$(git branch --show-current)"
START_SHA="$(git rev-parse --short "$START_BRANCH")"

# Parent the new release commit on the previous release tip so the branch is a
# linear chain: servers can then `git pull` (fast-forward) instead of hitting
# "divergent branches" against a rebuilt orphan. Prefer origin's tip — that is
# the chain servers actually hold.
RELEASE_PARENT="$(git rev-parse --verify --quiet "refs/remotes/origin/$RELEASE_BRANCH" \
                  || git rev-parse --verify --quiet "refs/heads/$RELEASE_BRANCH" || true)"

# Build the release tree in a side index so the main checkout, .venv and .env
# are never modified. Earlier versions cleared the working tree here and
# destroyed untracked-but-precious files.
cat > "$WORK/.gitignore" <<'EOF'
# Artifacts-only branch: every file here is generated by build_release.sh on
# main. Never commit secrets, source or the virtualenv.
.env
.venv/
__pycache__/

# Legacy sqlite shell (real data lives in PostgreSQL); never commit
email_automation.db
EOF
mv "$WORK/.gitignore" "$WORK/payload/.gitignore"

cd "$WORK/payload"
git init -q .
git add -A .
STAGED="$(git diff --cached --name-only)"
echo "$STAGED" | sed 's/^/  + /'

if echo "$STAGED" | grep -qE '(^\.env$|^\.venv/|\.pyc$|^tests/)'; then
  echo "✗ refusing to commit: staged files contain secrets/venv/tests" >&2
  echo "$STAGED" | grep -E '(^\.env$|^\.venv/|\.pyc$|^tests/)' | head -5 >&2
  exit 1
fi
for required in .env.example docs/API.md DEPLOY.md schema.sql \
                openapi-hunter.json openapi-marketing.json requirements.lock.txt; do
  echo "$STAGED" | grep -qx "$required" || { echo "✗ missing required release file: $required" >&2; exit 1; }
done
echo "$STAGED" | grep -qE '^ai_hunter-.*\.whl$' || { echo "✗ wheel missing from release" >&2; exit 1; }

TREE="$(git write-tree)"
# Objects live in the throwaway repo; merge the loose objects into the main
# store by copy. Existing files are skipped (-n): git writes them read-only
# and identical content already means the object is present.
cp -rn "$WORK/payload/.git/objects/." "$ROOT/.git/objects/" 2>/dev/null || true
cd "$ROOT"
rm -rf "$WORK/payload/.git"
MSG="release: $(basename "$WHEEL") build from $START_BRANCH @ $START_SHA"
if [ -n "$RELEASE_PARENT" ]; then
  COMMIT="$(git commit-tree "$TREE" -p "$RELEASE_PARENT" -m "$MSG")"
  echo "  parent: ${RELEASE_PARENT:0:8} (fast-forwardable)"
else
  COMMIT="$(git commit-tree "$TREE" -m "$MSG")"
  echo "  parent: none (first release build)"
fi
git update-ref "refs/heads/$RELEASE_BRANCH" "$COMMIT"
echo "  → $RELEASE_BRANCH = ${COMMIT:0:8}"
echo "── 6/6 cleanup"

[ "$KEEP_WORK" = "--keep-work" ] || rm -rf "$WORK"
echo "✓ done: branch '$RELEASE_BRANCH' (build artifacts only). Inspect: git ls-tree --name-only $RELEASE_BRANCH"
