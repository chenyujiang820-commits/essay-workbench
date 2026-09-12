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

# 门禁之外还有两条真分辨率取证探针（只读、不调引擎），见「版式与真机取证探针」
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
* **标题自动抽取（`app/pipeline/title.py`）取宁缺勿错**：**先找显式标签行** —— 前 5 个非空行里出现
  「题目：/标题：/作文题目：」就以它的内容为准（冒号后换行书写时向下取一行）；**无标签时只看首个非空行，
  不跨行猜**（跨行会把日期栏后面的正文残句抽成标题）。判「不是标题」的四类噪声：句末标点落在**行主体内部**
  （OCR 断行的残句标点不在行尾）、清洗后 >30 字、整行是作文本表头/表单栏目（`月 日 星期`：栏目词 ≥2 且
  实义字 ≤1）、**题号行与页眉署名**（`四、写作`、`23. …`、`第 23 题`，以及 ≤8 字命中署名后缀的水印如
  `逸云手写`）。抽不到就留空，由老师在校对页填；成册 PDF、单篇文件名与投屏统一兜底显示「未命名」。

### 手机端访问与投屏排版（v1.2 rev.4 / rev.5）

* **手机怎么连**：后端监听 `0.0.0.0:8000`，手机用同一局域网内的电脑 IP 访问，例如
  `http://192.168.31.167:8000`（Windows 首次需放行 8000 入站规则）。
* **改完代码手机看不到新版**：入口 `index.html` 已发 `Cache-Control: no-cache`，正常刷新即可；
  但**本次修复后的第一次仍需硬刷新**（旧入口被缓存过 → 请求已删除的旧 hash 脚本 → 白屏，即 GAP-12）。
  纯前端改动只需 `npm run build`，`StaticFiles` 每次读盘，**不必重启后端**；改了 `app/**` 才要重启。
* **校对页手机端**：默认落在「文字」页（定稿框 + 识别对照第一眼可见），页签显示「文字 · 存疑 N」，
  点存疑处会连页签一起切到原片；`<lg` 整页可滚，原片区 `52vh` 封顶不超屏高。桌面 `lg:` 仍是双栏一屏。
* **识别对照的两种来源**：有引擎 diff 时按 diff 标黄；真机最常见的**高置信稿（`diff_json` 为 NULL）**
  改渲染「识别原文对照」—— 橙色 = 未识别占位字（`? ？ □ ▯` 及 `【?】〔?〕`），红色 = 定稿中已不存在的句子。
  这类 fallback 存疑点**刻意不计入**定稿前的「还有 N 处未点看」弹框，否则每篇高置信稿都要弹一次。
* **iPhone 的 HEIC**：前端按与后端 `resolve_extension` 同源的白名单预检，点名哪几张不收、给出
  「设置 → 相机 → 格式 → 兼容性（JPEG）」路径，**其余照片照常加入**。不做自动转码（需新增依赖）。
  上传框 `accept=image/*` 且**故意不加 `capture`**，否则手机只能拍照、不能从相册选。
* **投屏（智慧黑板）**：抬头**标题在上、作者在下**，正文按 `paginateColumns` 排成**书式左右两栏**；
  `<1180px` 视口自动退单栏（两栏在窄屏会得到 11 字/行的窄栏）。翻页按**全局位置**判定，
  本篇最后一屏仍可翻进下一篇，只有全站最后一屏才置灰。
* **投屏列哪些稿子（v1.2 rev.5，GAP-14）**：判据是「**有没有文字**」，不是「是否定稿」。
  未定稿的照样进轮播，正文取按张序拼接的识别初稿并挂「**未定稿 · 识别初稿**」徽标；顶栏另写
  「N 篇未定稿… · 另有 M 篇暂无文字，未进轮播」。**rev.4 曾把未定稿过滤掉**，结果老师拍 3 篇只
  定稿 1 篇时就以为「另一篇看不到」—— 空白页难看但可归因，静默消失不可归因。
  **校对铁律只管成册/导出，不管投屏**：整册导出遇未定稿照旧 409。
* **识别对照的框粒度（v1.2 rev.5，GAP-13）**：比对单元**先按行切、行内再按句切**。只按句末标点切会
  把「没有句号的页眉」并进下一个长句（真机 essay1 三行页眉 + 第一个长句 = 一个 6 行大框），老师删掉
  页眉就整句标红，看着像「没触发」。判据是**每个框至多覆盖一行**，由探针在真实浏览器逐框量高把关。

### 版式与真机取证探针（只读，不调引擎、不产生费用）

```bash
# 校对页：3 篇 × 4 视口（390x844 / 844x390 / 1366x768 / 1920x1080）—— 识别对照是否可见、能否滚到底，
#   并逐框量「识别对照」标注框（含换行或高过 4 视觉行即 FAIL，GAP-13）
cd backend && .venv/Scripts/python.exe ../deploy/probe_proofread_layout.py --essay 1 2 3
# 加 --out ../artifacts/v1.2r5复测 可同时出取证截图

# 投屏轮播：把 /present 返回的篇目当期望集合，真点「下一页」验证每篇都翻得到（GAP-08/14）；
#   第二阶段用响应桩在未定稿状态下确认徽标与顶栏说明在真实 Chromium 里渲染
cd backend && .venv/Scripts/python.exe ../deploy/probe_present_paging.py

# 手机上传页（走局域网地址）：缓存头、拍摄引导、HEIC 提示、accept/capture 属性、页面 JS 错误
cd backend && .venv/Scripts/python.exe ../deploy/probe_mobile_upload.py
```

三条探针都**只读**：绝不点「上传并识别」，不触发真实引擎调用、不产生费用，也不改真机数据。

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
