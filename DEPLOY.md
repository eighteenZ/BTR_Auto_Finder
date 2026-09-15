# AI Hunter 部署指南（release 分支）

本分支只含编译产物与部署文件，无源码。以下命令在服务器执行（需 Python >= 3.11 与 PostgreSQL 14+）。

## 1. 拉取与安装
```bash
git clone -b release <repo_url> /opt/ai-hunter && cd /opt/ai-hunter
python3.11 -m venv .venv   # Ubuntu 缺模块时: apt install python3.11-venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install ai_hunter-*.whl -r requirements.lock.txt
```

> 中国大陆服务器才需要镜像加速（官方源约 86KB/s）：
> `pip install -i https://mirrors.aliyun.com/pypi/simple/ ...`
> 其他区域（美国/欧洲/新加坡等）直接用官方 PyPI 即可。

⚠️ 已知限制：发送调度器只按 `scheduled_at` 判定，**不检查工作时间、时区、工作日或每日/每小时限流**
（这些配置项存在但未被消费）。邮件会在 campaign 启动后按 0/3/7 天偏移随时发出，
放量节奏需自行控制（如分小批建 campaign）。

## 2. 配置与建库
```bash
cp .env.example .env && vi .env
# 必填: DATABASE_URL / LLM 提供商 key / SERPER_API_KEY
# 邮件: EMAIL_SMTP_* + EMAIL_FROM_ADDRESS（发送前先跑 :8100/api/settings/email/test）

# 建表（在空库上执行一次，把 .env 里的 DATABASE_URL 从 postgresql+psycopg:// 改成 postgresql:// 后使用）:
psql "<postgresql://user:pass@host:port/dbname>" -f schema.sql
```

## 3. systemd 服务
```bash
cp deploy/systemd/*.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now ai-hunter-api ai-marketing-api
```
- hunter: `http://<host>:8000`（Swagger /docs）
- marketing: `http://<host>:8100`（人工审批页 /review，Swagger /docs）

## 4. 验证
```bash
curl :8000/api/v1/health && curl :8100/api/v1/health
```

⚠️ 拆分模式与合并模式二选一部署；EMAIL_AUTO_SEND_ENABLED 只能由一个进程承载，否则双发。
⚠️ 升级：重新拉取 release 分支后，pip install --force-reinstall 新 wheel 并重启两个服务。
完整 API 说明见 docs/API.md。
