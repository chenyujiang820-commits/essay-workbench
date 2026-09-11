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
worker_concurrency: 4          # 识别篇级并发，1~8（FR-10）
low_resolution_penalty: 0.05   # 每张低画质原片的置信扣分，0~0.2（整篇封顶 0.15）
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
* `worker_concurrency`（FR-10）决定同时处理几篇作文；**一篇内部的多张仍按 `seq` 串行**，
  因此不会打乱单篇正文顺序。实测收益**在 4 附近饱和**（45 篇 mock 冷跑：1→7.71s、2→4.31s、
  4→3.79s、8→3.92s）：瓶颈已从「等引擎」变成「每篇多次 commit 在 SQLite 单写锁上排队」，
  **再往上调不会更快**；真要提吞吐应当先减少每篇的提交次数。
  所以调大它只缩短「整班 45 篇」的总时长，不会打乱单篇正文顺序。越界钳制到 1~8，
  写错类型回退默认 4；填 `1` 即等价于一期的单消费者行为（一键回滚开关）。
* `low_resolution_penalty` 配合后端 `is_low_resolution`（长边 ≥800 且短边 ≥600 为合格）：
  每张「画质偏低」原片按该值扣置信，整篇累计封顶 0.15，让糊图更容易掉进低置信区间去触发
  复核引擎，而不是静默出一篇低质量稿。
* `engines.yaml` 只在**进程启动时读取一次**，改完必须重启后端才生效。

### 校对、标题与重跑（v1.2 rev.2 / rev.3）

* **存疑按句点看**：识别对照面板把每个存疑段落**按中文句末标点切成可点单元**，点一句即
  高亮并定位它所属的原片；「存疑 N 处 · 已查看 M 处」与定稿前的「还有 N 处未点看」提醒
  同源同口径（`frontend/src/components/DiffText.tsx`）。切分只发生在展示层，`diff_json`
  仍是引擎给出的段落序列。
* **画质门槛是识别质量的输入**：`photos` 表不存画质列，低画质由 `width`/`height` 现算
  （短边 <600 或长边 <800），上传页角标、看板汇总、置信度扣分三处共用同一判据。
* **失败稿就地重跑**：状态看板与校对页都对 `failed` 篇目提供「重新识别」，受理后该篇回到
  `recognizing` 并被看板的 3s 轮询接管；**已定稿（`proofread`）会被 409 拒绝**，不会覆盖
  老师成果。入口对应 `POST /api/essays/{id}/recognize`。
* **标题自动抽取（`app/pipeline/title.py`）取宁缺勿错**：只有首行「足够像标题」才写入，判正文的
  三条是句末标点落在**行主体内部**（OCR 断行的残句标点不在行尾）、清洗后 >30 字、整行是作文本
  表头/表单栏目（`月 日 星期` 这类：栏目词 ≥2 且实义字 ≤1）。判据看的是**位置**而不是个数。
  抽不到就留空，由老师在校对页填；成册 PDF、单篇文件名与投屏统一兜底显示「未命名」。

### 班级名与版本（`app.yaml` / `/api/meta`）

`<EWB_DATA_DIR>/config/app.yaml` 顶层可写 `class_name: 高一(1)班`（未配置回退「班级」）。
该值连同版本号由 `GET /api/meta` 一并返回，前端顶栏与上传/看板/成册页副标题共用这一来源；
版本号的唯一出处是 `backend/app/__init__.py` 的 `__version__`，`/api/health`、`/api/meta` 与
OpenAPI 元数据都读它，改版本只改这一处。

---

## 备份与恢复（FR-13）

一期数据只有两处：`essay.db`（WAL 模式）与 `photos/`。直接 `cp essay.db` 可能拷到半截状态——
未 checkpoint 的改动还在 `-wal` 边车里。`deploy/backup.py` 走 SQLite Online Backup API，在事务内
复制库，再连同 `config/`、`photos/` 一起打成一份一致性归档。

```bash
# 产出归档（缺省写到数据目录同级的 backups/，不会落在数据目录内）
python deploy/backup.py --data-dir /data/essay-workbench --out /var/backups/ewb

# 只校验某份归档是否真的可用：解到临时目录跑 PRAGMA integrity_check + 比对照片/配置数
python deploy/backup.py --verify /var/backups/ewb/ewb-backup-20260912-031000.tar.gz
```

* 保留策略：日 7 / 周 4 / 月 6，用 `--keep-daily/--keep-weekly/--keep-monthly` 覆盖，填 `0` 关闭该档。
* 对数据目录**只读**；裁剪只作用于 `--out` 里的 `ewb-backup-*.tar.gz`，`--out` 落在数据目录内时拒绝清理。
* 恢复：把归档解到目标数据目录，覆盖 `essay.db` / `config/` / `photos/`，删掉残留的 `essay.db-wal`
  边车，再启动服务。`config/` 含口令哈希与 API Key，**属敏感件**，异地备份请走加密通道。
* 定时：`deploy/backup.service` + `deploy/backup.timer`（`Persistent=true`，漏跑开机补），
  安装步骤见两个文件的头部注释。
* 登录保护（OPT-03）：同一来源连错 5 次进入指数退避锁（最长 15 分钟），正确口令解锁后仍可登录；
  公网暴露时另在 `deploy/nginx.conf` 开 `limit_req`。**上线第一件事仍是改掉默认口令 `admin123`。**

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

v1.2 另附三份文档：

* `docs/真机验收清单.md` —— 二期启动门禁 2/3 只能靠它（老师计时、真机与投影仪）；
* `docs/architecture.md` Part C —— v1.2 相对一期原设计的结构增量；
* `docs/UI交互升级评估.md` —— v1.2 收口后的 UI/交互/逻辑升级选型（结论：U1 校对动线，
  附带 U2 收齐对账；前提是 §8.1 门禁先由真人过一遍）。

> 截图已在 rev.3 用 v1.2 构建重拍（数据源是 T05 真实识别结果的**本机副本**，标题由 v1.2 抽取器
> 生成；`看板-失败重跑-1366x768.png` 是临时把一篇置为 `failed` 后拍的取证图，拍完已还原）。
> ⚠️ 三份样例 PDF 仍是 T05 旧产物：刷新它们要重跑真实引擎冒烟（外部调用、产生费用，需授权），
> 本轮未擅自跑。PDF 版式回归由 `backend/tests/test_render.py` 的快照用例守着。

真实冒烟脚本（**仅手动运行，会真实调用引擎**）：

```bash
cd backend && PYTHONPATH= .venv/Scripts/python.exe ../deploy/smoke_e2e.py
```

导出规模计时（**不联网、不调引擎**，约 15 秒；PRD「45 篇规模需复测」就是它）：

```bash
cd backend && PYTHONPATH= .venv/Scripts/python.exe ../deploy/bench_export.py --keep
```

它在临时数据目录里造 45 名学生的 45 篇已定稿作文（正文为合成句池），起一个关掉 Worker 的服务，
走真实 HTTP 端点给三套模板与单篇版式计时；任一套超过 `--limit-seconds`（默认 60）就以退出码 1 结束，
可直接当门禁。`--keep` 会把 PDF 留在临时目录里供目视（页数应为 49~50）。
