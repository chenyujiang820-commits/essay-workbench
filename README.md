# 班级作文工作台 · 一期

高中语文教师的作文「收 / 改 / 成册」工作台：

> 手机拍照上传 → 双引擎 OCR 识别 → 字符级 diff 标疑 → 老师左右分栏校对定稿 →
> 三套模板成册导出 PDF → 课堂投屏表彰。

一期（T01–T05）已全部交付：项目基础设施、数据层与识别流水线、校对环前端、
成册导出与投屏、集成联调与部署。

---

## 目录结构

```
essay-workbench/
├── backend/                     # FastAPI + SQLAlchemy(async) + aiosqlite + Playwright
│   ├── app/
│   │   ├── main.py              # 入口：生命周期 / 异常信封 / SPA 静态托管
│   │   ├── config.py            # EWB_DATA_DIR + 数据目录 YAML 配置
│   │   ├── db.py                # async engine / session 工厂 / WAL PRAGMA
│   │   ├── models.py            # students/issues/essays/photos/recognition_tasks
│   │   ├── schemas.py           # Pydantic 契约（统一信封 {code,data,message}）
│   │   ├── images.py            # 上传白名单 + 单张 15MB 上限 + Pillow 尺寸解析
│   │   ├── auth.py              # 口令哈希 + Bearer token 签发/校验
│   │   ├── routers/             # auth / issues / students / essays / photos / exports
│   │   ├── pipeline/            # engine_adapter / confidence / diff / worker
│   │   └── render/              # Jinja2 模板 + Playwright PDF
│   ├── assets/templates/        # 三套成册模板 HTML（elegant/playful/formal）
│   ├── config/                  # *.example（真实配置放数据目录，不进 Git）
│   ├── data/                    # 本机数据目录（gitignore；含照片/SQLite/密钥）
│   └── tests/                   # pytest（覆盖率门禁 >= 80%）
├── frontend/                    # Vite + React18 + TS + Tailwind + Zustand
├── deploy/                      # 学生名单脚本 / 冒烟脚本 / systemd / nginx
├── artifacts/                   # 一期验收产物（PDF / 截图 / 验收记录）
└── verify.sh                    # 一键验证链
```

---

## 快速开始（本机开发）

### 1. 后端

```bash
cd backend
python -m venv .venv
# Windows (Git Bash)
.venv/Scripts/python.exe -m pip install -e ".[dev]"
# Linux / macOS
# .venv/bin/python -m pip install -e ".[dev]"

# PDF 导出依赖的无头 Chromium（首次需下载）
.venv/Scripts/python.exe -m playwright install chromium

# 首次启动会自动生成 data/config/app.yaml（默认口令 admin123）
# 并把 config/engines.yaml.example 复制为 data/config/engines.yaml —— 随后编辑它，
# 填入真实的 base_url / api_key / model。
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
```

* 初始口令：环境变量 `EWB_PASSWORD`（未设置则默认 `admin123`）。
* OpenAPI 文档：<http://127.0.0.1:8000/docs>

### 2. 前端

```bash
cd frontend
npm install
npm run dev       # http://127.0.0.1:5173（/api 已代理到 8000）
npm run build     # 产出 dist/，后端启动时会自动静态托管（单端口部署）
```

### 3. 导入学生名单（一期无管理界面）

```bash
# 名单为 CSV「学号,姓名」，见 deploy/students.sample.csv
cd backend && PYTHONPATH= .venv/Scripts/python.exe ../deploy/seed_students.py ../deploy/students.sample.csv
```

脚本**幂等**：按学号 upsert，只更新变化的姓名/启用状态，重复执行不产生重复行。

### 4. 一键验证

```bash
bash verify.sh    # 后端 ruff -> mypy -> pytest(cov>=80)；前端 tsc -> eslint -> vitest
```

---

## 配置说明

### 数据目录（代码与数据彻底分离）

由环境变量 `EWB_DATA_DIR` 指定，默认相对后端的 `./data`。生产建议 `/data/essay-workbench`
（见 `deploy/systemd.service`）。目录结构：

```
<EWB_DATA_DIR>/
├── essay.db                 # SQLite（WAL）
├── photos/{issue}/{essay}/  # 原片永久留存
├── exports/                 # PDF 导出产物（导出为流式下载，通常不落盘）
└── config/
    ├── app.yaml             # 口令哈希 / token 密钥 / 班级名
    └── engines.yaml         # 双引擎配置（含 API Key，gitignore，绝不入库）
```

### 口令（默认 admin123 的修改方法）

首次启动若 `config/app.yaml` 缺失，会用默认口令 `admin123` 生成哈希。**上线前务必改掉**：

```bash
# 方式 A（推荐）：生成口令哈希，手工写入 app.yaml 的 auth.password_hash
cd backend && .venv/Scripts/python.exe -m app.auth hash "你们的新口令"
# 方式 B：首次启动前设置环境变量 EWB_PASSWORD（仅在生成时生效一次）
EWB_PASSWORD="你们的新口令" .venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
```

### 双引擎（engines.yaml）

```yaml
low_confidence_threshold: 0.85
primary:                       # 主识别：快通道
  base_url: "https://<中转站>/v1"
  api_key: "sk-..."
  model: "qwen3.8-flash"
  timeout: 30
  retries: 2
review:                        # 复核：思考型慢通道，仅低置信稿件调用
  base_url: "https://<中转站>/v1"
  api_key: "sk-..."
  model: "qwen3.7-flash"
  timeout: 120
  retries: 2
```

* API Key **只**从数据目录 `engines.yaml` 读取，代码库仅存 `*.example`；日志/异常/报告均不外泄密钥。
* 复核引擎超时或 504 时**自动降级**（`engine2_failed` + error，不阻塞，照常进入待校对）。

---

## 生产托管（Linux）

```bash
# 1. 依赖
python3 -m venv /opt/essay-workbench/backend/.venv
/opt/essay-workbench/backend/.venv/bin/pip install -e /opt/essay-workbench/backend
/opt/essay-workbench/backend/.venv/bin/playwright install chromium   # PDF 导出必需
apt-get install -y fonts-noto-cjk                                   # 中文字体（防方框）

# 2. 数据目录
install -d -o essay -g essay /data/essay-workbench/config
cp /opt/essay-workbench/backend/config/engines.yaml.example /data/essay-workbench/config/engines.yaml
# 编辑 /data/essay-workbench/config/engines.yaml 填真实密钥

# 3. 前端构建（产出 dist/，由后端托管）
cd /opt/essay-workbench/frontend && npm ci && npm run build

# 4. 服务
cp /opt/essay-workbench/deploy/systemd.service /etc/systemd/system/essay-workbench.service
systemctl daemon-reload && systemctl enable --now essay-workbench

# 5. 反向代理
cp /opt/essay-workbench/deploy/nginx.conf /etc/nginx/conf.d/essay-workbench.conf
nginx -t && systemctl reload nginx
```

> **单进程**：识别 Worker 为进程内 asyncio 队列，uvicorn 必须 `--workers 1`，否则多进程会重复消费。
> 访问入口为 nginx（80/443），后端仅监听 `127.0.0.1:8000`。

---

## API 摘要

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth/login` | 口令登录，返回 Bearer token |
| GET/POST/PATCH/DELETE | `/api/issues[/{id}]` | 期数管理（删除仅限空期） |
| GET | `/api/students` | 启用学生列表 |
| POST | `/api/issues/{id}/essays` | 多图上传（202，异步识别） |
| GET | `/api/issues/{id}/essays` | 状态看板数据 |
| GET/PATCH | `/api/essays/{id}` | 详情 / 校对定稿 |
| GET | `/api/photos/{id}/file` | 原片二进制（带鉴权，防目录穿越） |
| GET | `/api/exports/templates` | 三套模板清单 |
| GET | `/api/exports/{id}/preview` | 整册 HTML（预览） |
| GET | `/api/exports/{id}/present` | 投屏数据 |
| POST | `/api/exports/{id}` | 整册 PDF（未全定稿 409） |
| POST | `/api/exports/{id}/single/{essay_id}` | 单篇版式 PDF |

统一信封：成功 `{code:0,data,message}`；失败 `{code:非0,data:null,message}`。
状态机：`essay.status` ∈ uploaded/recognizing/review/proofread/failed；
`task.step` ∈ queued/engine1/judge/engine2/diff/done/engine2_failed。

上传约束：格式白名单 `jpg/jpeg/png/webp/bmp`，单张 ≤ 15MB，超限返回 400 + 明确文案。

---

## 安全

* 数据目录（照片、SQLite、导出、密钥）整体不入 Git（`.gitignore`）。
* `engines.yaml` 含 API Key，只存在于数据目录；代码库仅提交 `*.example`。
* 口令以 PBKDF2-SHA256 哈希存 `app.yaml`，明文绝不落库。
* 原片文件服务强制路径落在数据目录内（防目录穿越 / 绝对路径注入）。

---

## 常见问题（FAQ）

**Q1. `pip install -e .` 卡死 / 装完后 venv 被掏空？**
某些受控沙箱或 IDE 会通过 `PYTHONPATH` 注入 `sitecustomize.py`（内含批量删除保护），
editable 安装在卸载旧版本时触发保护，导致 venv 被清空。规避：

```bash
# 清空 PYTHONPATH 使注入的 shim 不加载；必要时用 --ignore-installed 跳过卸载步骤
cd backend && PYTHONPATH= .venv/Scripts/python.exe -m pip install --ignore-installed -e ".[dev]"
```

普通 Linux 服务器 / 本地终端不存在该问题，可正常 `pip install -e ".[dev]"`。

**Q2. 导出 PDF 报 Chromium 相关错误？**
未安装无头浏览器：`python -m playwright install chromium`（Linux 首次还需 `--with-deps`）。

**Q3. PDF / 渲染里中文变成方框（□□）？**
缺中文字体。Linux 安装 `fonts-noto-cjk`；Windows 自带「微软雅黑」无需处理。

**Q4. 上传返回 400？**
两种可能：格式不在白名单（仅 jpg/jpeg/png/webp/bmp），或单张超过 15MB。错误信封的
`message` 会明确说明。

**Q5. 某篇识别后没有 diff（黄色存疑处）？**
复核引擎（engine2）**仅在主引擎结果低置信时**才调用；高置信稿件不会产生 diff，这是设计如此。
若复核引擎 504/超时，任务降级为 `engine2_failed`（记录 error，不阻塞，照常进入待校对），
可在作文详情的 `task.error` 看到。

**Q6. 换数据目录 / 迁移机器？**
设置 `EWB_DATA_DIR` 指向新目录即可；SQLite 与照片均为文件，整目录搬运。
换机器后需重跑 `playwright install chromium` 并按需安装中文字体。

**Q7. 端口被占用或想换端口？**
uvicorn `--port`；nginx 只反代到 `127.0.0.1:8000`，改端口时同步改 `upstream`。

---

## 一期验收产物

见 `artifacts/`：三套模板样例 PDF、投屏视图与校对页截图、`端到端验收记录.md`、
`smoke_report.json`（真实引擎冒烟的逐步耗时与识别质量观察）。

真实冒烟脚本（**仅手动运行，会真实调用引擎**）：

```bash
cd backend && PYTHONPATH= .venv/Scripts/python.exe ../deploy/smoke_e2e.py
```
