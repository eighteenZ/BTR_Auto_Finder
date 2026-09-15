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

if [ -n "$(git status --porcelain | grep -v '^??')" ]; then
  echo "✗ working tree has uncommitted changes — commit first" >&2
  exit 1
fi

echo "── 1/5 build wheel"
rm -rf "$WORK/dist"
"$PY" -m pip wheel . --no-deps -w "$WORK/dist" -q
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
mkdir -p "$WORK/payload/docs" "$WORK/payload/deploy/systemd"
cp "$WHEEL" "$WORK/payload/"
cp "$WORK/requirements.lock.txt" "$WORK/payload/"
cp "$WORK/openapi-hunter.json" "$WORK/openapi-marketing.json" "$WORK/schema.sql" "$WORK/payload/"
cp "$ROOT/docs/API.md" "$WORK/payload/docs/"
cp "$ROOT/deploy/systemd/"*.service "$WORK/payload/deploy/systemd/"
cp "$ROOT/.env.example" "$WORK/payload/"
{
  echo "# AI Hunter 部署指南（release 分支）"
  echo
  echo "本分支只含编译产物与部署文件，无源码。以下命令在服务器执行（需 Python >= 3.11 与 PostgreSQL 14+）。"
  echo
  echo "## 1. 环境准备与安装"
  echo
  echo "需要 Python **>= 3.11**（项目与依赖均已验证 3.11/3.12）。先确认系统自带版本："
  echo '```bash'
  echo "python3 --version"
  echo '```'
  echo "- \`3.11\` / \`3.12\` / \`3.13\`（Ubuntu 24.04、Debian 12 自带）→ 直接用 \`python3\`"
  echo "- \`3.10\` 或更低（Ubuntu 22.04 / 20.04）→ 默认源里没有新版 Python（\`apt install python3.11\` 会报 Unable to locate package），需加 PPA："
  echo '```bash'
  echo "sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt update"
  echo "sudo apt install -y python3.12 python3.12-venv"
  echo "# 之后把下面命令里的 <PY> 换成 python3.12"
  echo '```'
  echo
  echo "安装（\`<PY>\` 为上面确定的可执行名，如 \`python3\`）："
  echo '```bash'
  echo "git clone -b $RELEASE_BRANCH <repo_url> /opt/ai-hunter && cd /opt/ai-hunter"
  echo "sudo apt install -y <PY>-venv        # 提供 venv 模块（缺它时报 ensurepip is not available）"
  echo "<PY> -m venv .venv"
  echo ".venv/bin/pip install --upgrade pip"
  echo ".venv/bin/pip install ai_hunter-*.whl -r requirements.lock.txt"
  echo '```'
  echo
  echo "> 中国大陆服务器才需要镜像加速（官方源约 86KB/s）："
  echo "> \`pip install -i https://mirrors.aliyun.com/pypi/simple/ ...\`"
  echo "> 其他区域（美国/欧洲/新加坡等）直接用官方 PyPI 即可。"
  echo
  echo "⚠️ 已知限制：发送调度器只按 \`scheduled_at\` 判定，**不检查工作时间、时区、工作日或每日/每小时限流**"
  echo "（这些配置项存在但未被消费）。邮件会在 campaign 启动后按 0/3/7 天偏移随时发出，"
  echo "放量节奏需自行控制（如分小批建 campaign）。"
  echo
  echo "## 2. 配置与建库"
  echo '```bash'
  echo "cp .env.example .env && vi .env"
  echo "# 必填: DATABASE_URL / LLM 提供商 key / SERPER_API_KEY"
  echo "# 邮件: EMAIL_SMTP_* + EMAIL_FROM_ADDRESS（发送前先跑 :8100/api/settings/email/test）"
  echo
  echo "# 建表（在空库上执行一次，把 .env 里的 DATABASE_URL 从 postgresql+psycopg:// 改成 postgresql:// 后使用）:"
  echo 'psql "<postgresql://user:pass@host:port/dbname>" -f schema.sql'
  echo '```'
  echo
  echo "## 3. systemd 服务"
  echo '```bash'
  echo "cp deploy/systemd/*.service /etc/systemd/system/"
  echo "systemctl daemon-reload && systemctl enable --now ai-hunter-api ai-marketing-api"
  echo '```'
  echo "- hunter: \`http://<host>:8000\`（Swagger /docs）"
  echo "- marketing: \`http://<host>:8100\`（人工审批页 /review，Swagger /docs）"
  echo
  echo "## 4. 验证"
  echo '```bash'
  echo "curl :8000/api/v1/health && curl :8100/api/v1/health"
  echo '```'
  echo
  echo "⚠️ 拆分模式与合并模式二选一部署；EMAIL_AUTO_SEND_ENABLED 只能由一个进程承载，否则双发。"
  echo
  echo "## 5. 升级"
  echo
  echo "release 分支现在是线性累积的，升级就是普通快进拉取。建议先固定拉取策略，避免误产生 merge："
  echo '```bash'
  echo "cd /opt/ai-hunter && git config pull.ff only"
  echo '```'
  echo
  echo "**首次过渡（仅需一次）**：早期版本的 release 提交是各自独立重建的，与你本地已有的提交没有共同祖先，"
  echo "直接 pull 会提示 divergent branches。执行一次："
  echo '```bash'
  echo "git fetch origin release && git reset --hard origin/release"
  echo '```'
  echo "> \`.env\` 与 \`.venv\` 未被 git 跟踪（分支自带 .gitignore），\`reset --hard\` 不会删除它们。"
  echo
  echo "**之后每次升级**："
  echo '```bash'
  echo "cd /opt/ai-hunter"
  echo "git pull                                   # 快进拉取新产物"
  echo ".venv/bin/pip install --force-reinstall -r requirements.lock.txt ai_hunter-*.whl"
  echo "# 若 schema.sql 有新表（升级说明会指出）: psql \"<psql url>\" -f schema.sql"
  echo "systemctl restart ai-hunter-api ai-marketing-api"
  echo "curl -s :8000/api/v1/health && curl -s :8100/api/v1/health"
  echo '```'
  echo
  echo "完整 API 说明见 docs/API.md。"
} > "$WORK/payload/DEPLOY.md"
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
