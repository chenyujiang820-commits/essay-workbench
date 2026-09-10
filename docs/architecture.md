# 「班级作文工作台」一期 系统设计 + 任务分解

架构师：高见远（Bob）｜依据：PRD v1.1（终审冻结版）｜2026-09-10
主理人裁定：5 项待明确事项已拍板（见文末附录）

---

## Part A: System Design

### 1. 实现方案 + 框架选型

**核心技术挑战**
1. 手机浏览器/微信内调用相机并多张上传（移动端硬要求）；
2. 双引擎异步识别流水线：慢通道 504 → 必须长超时 + 重试 + **降级不阻塞**；
3. 识别引擎可替换（适配器模式，配置驱动，零硬编码）；
4. 左右分栏逐句校对，照片缩放 + 可编辑文本 + 存疑高亮（移动端也要能操作）；
5. 3 套模板成册 + 中文 PDF 导出（Windows 开发、Linux 部署，跨平台是关键）；
6. 校对铁律的流程级强制（未校对不可成册）。

**选型决策**

| 层 | 选型 | 理由 |
|---|---|---|
| 后端 | **FastAPI + SQLAlchemy 2.0(async) + aiosqlite** | 团队偏好 Python；FastAPI 原生 async 适合长耗时 OCR IO 等待 + 自带 OpenAPI 文档；SQLite 起步零运维，4核4G 绰绰有余（单班级每周 ~45 篇量级） |
| 前端 | **Vite + React 18 + TypeScript + Tailwind CSS + Zustand** | 不用 MUI：校对页/投屏页是高度定制布局（分栏、翻页、大字号投屏），Tailwind 原子类比组件库更轻、移动端适配更快；Zustand 足够（无复杂全局状态，不上 Redux） |
| 异步任务 | **进程内 asyncio Worker + DB 状态机**（不上 Celery/Redis） | 单用户单机场景，引入 MQ 是过度设计；任务状态持久化在 SQLite，进程重启可断点续跑 |
| HTTP 客户端 | **httpx（AsyncClient）** | 异步、超时/重试控制精细，替代实测脚本的 urllib，API 形态保持一致 |
| OCR 引擎接入 | **自研适配器：`OcrEngineAdapter` Protocol + `OpenAICompatAdapter` 实现** | 引擎 API/Key/模型名全部从数据目录下 YAML 读取；换引擎只改配置 |
| diff | **Python 标准库 `difflib.SequenceMatcher`（字符级 opcodes）** | 中文无空格分词问题，字符级 diff 最稳；零依赖、确定性输出、天然输出 equal/replace/delete/insert 四类段落，直接映射"存疑"标注 |
| PDF 生成 | **Jinja2 模板 + Playwright(Chromium) headless print-to-PDF** | ① 中文排版完美；② **模板即 HTML/CSS，成册预览和 PDF 同一套模板**；③ Windows/Linux 均可运行（WeasyPrint 在 Windows 依赖 GTK，弃）；④ 每周一次导出，Chromium 冷启 2~3s 可接受 |
| 照片缩放 | **react-zoom-pan-pinch** | 成熟手势缩放库，手机双指/电脑滚轮都支持 |

**架构模式**：后端分层（routers → services/pipeline → models），前端页面组件 + 轻量全局 store。整体"单机单体 + 本地文件存储"，刻意保持简单。

### 2. 文件列表（项目根 `essay-workbench/`）

```
essay-workbench/
├── README.md                      # 启动/部署说明
├── backend/
│   ├── pyproject.toml             # Python 依赖 + ruff/mypy/pytest 配置
│   ├── app/
│   │   ├── main.py                # FastAPI 入口：路由注册、静态托管 frontend/dist、生命周期管理
│   │   ├── config.py              # 配置加载：env + 数据目录下 engines.yaml / app.yaml
│   │   ├── db.py                  # SQLAlchemy async engine / session 工厂 / WAL pragma
│   │   ├── models.py              # ORM：Student / Issue / Essay / Photo / RecognitionTask
│   │   ├── schemas.py             # Pydantic 请求/响应模型（API 契约）
│   │   ├── auth.py                # 口令登录、token 签发与校验依赖
│   │   ├── routers/
│   │   │   ├── auth_router.py     # POST /api/auth/login
│   │   │   ├── issues.py          # 期数 CRUD（一期一册）
│   │   │   ├── essays.py          # 上传(多图)/列表/详情/校对保存（含校对铁律校验）
│   │   │   ├── photos.py          # 原片文件服务（带鉴权）
│   │   │   └── exports.py         # 模板列表 / PDF 导出 / 投屏数据接口
│   │   ├── pipeline/
│   │   │   ├── worker.py          # asyncio 流水线：队列消费 + 任务状态机 + 降级逻辑
│   │   │   ├── engine_adapter.py  # OcrEngineAdapter Protocol + OpenAICompatAdapter + 工厂
│   │   │   ├── diff.py            # 字符级 diff（difflib 封装 → 段落 JSON）
│   │   │   └── confidence.py      # 置信度计算口径
│   │   └── render/
│   │       ├── templates.py       # Jinja2 环境与模板数据组装（3 套模板统一数据结构）
│   │       └── pdf.py             # Playwright 生成 PDF（A4 单页/整册）
│   ├── assets/templates/          # 3 套成册模板（部署时可被数据目录同名文件覆盖）
│   │   ├── elegant.html           # 素雅校刊风
│   │   ├── playful.html           # 活泼童趣风
│   │   └── formal.html            # 正式文集风
│   └── tests/
│       ├── conftest.py            # 临时 SQLite + 临时数据目录 fixture
│       ├── test_pipeline.py       # 流水线状态机、降级、diff、置信度单测
│       ├── test_engine_adapter.py # 适配器（mock httpx）
│       ├── test_routers.py        # API 契约 + 校对铁律强制
│       └── test_render.py         # 模板渲染 + PDF 冒烟
├── frontend/
│   ├── package.json / vite.config.ts / tsconfig.json / tailwind.config.ts
│   ├── index.html
│   └── src/
│       ├── main.tsx / App.tsx     # 入口 + 路由表
│       ├── styles.css             # Tailwind + 投屏大字号样式
│       ├── api/client.ts          # fetch 封装：token 注入、错误统一处理
│       ├── api/types.ts           # 与后端 schemas.ts 对齐的 TS 类型（含 DiffSegment）
│       ├── store.ts               # Zustand：登录态、当前期数、轮询状态
│       ├── pages/
│       │   ├── LoginPage.tsx      # 口令登录（适配手机）
│       │   ├── IssuePage.tsx      # 期数列表 + 新建一期
│       │   ├── UploadPage.tsx     # 拍照/选图多张上传（input capture）
│       │   ├── EssayListPage.tsx  # 任务状态看板（识别中/待校对/已定稿）
│       │   ├── ProofreadPage.tsx  # 校对环核心：左原片右文字、存疑高亮、整篇低置信横幅、铁律提示
│       │   ├── BookPage.tsx       # 成册预览 + 模板切换 + 排序 + 导出 PDF（未全定稿则禁用）
│       │   └── PresentPage.tsx    # 讲评投屏：1920×1080 横版、大字号、逐篇翻页
│       └── components/
│           ├── PhotoViewer.tsx    # react-zoom-pan-pinch 原片查看
│           ├── DiffText.tsx       # diff 段落渲染 + 存疑黄底 + 可编辑
│           ├── StatusBadge.tsx    # 任务状态徽标
│           ├── TemplatePicker.tsx # 3 套模板一键切换
│           └── Pager.tsx          # 投屏翻页（键盘 ←/→ + 触屏滑动）
└── deploy/
    ├── systemd.service            # uvicorn 服务单元
    ├── nginx.conf                 # 反代 + 静态（可选）
    └── seed_students.py           # 学生名单初始化脚本（读 CSV：学号,姓名）
```

### 3. 数据结构设计

**SQLite 表结构（含二/三期预留字段）**

```sql
CREATE TABLE students (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  student_no TEXT NOT NULL UNIQUE,      -- 学号，导出默认排序键
  name TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL              -- ISO 8601 UTC
);
CREATE TABLE issues (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  issue_no INTEGER NOT NULL UNIQUE,
  week_start_date TEXT NOT NULL,        -- 周一日期
  created_at TEXT NOT NULL
);
CREATE TABLE essays (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  issue_id INTEGER NOT NULL REFERENCES issues(id),
  student_id INTEGER NOT NULL REFERENCES students(id),
  title TEXT NOT NULL DEFAULT '',
  final_text TEXT NOT NULL DEFAULT '',  -- 老师校对后的定稿文字
  status TEXT NOT NULL DEFAULT 'uploaded',
    -- uploaded→recognizing→review→proofread / failed
  low_confidence INTEGER NOT NULL DEFAULT 0,
  -- ★ 二/三期预留位（一期不写入）：
  teacher_comment TEXT DEFAULT NULL,
  score REAL DEFAULT NULL,
  selected INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  proofread_at TEXT DEFAULT NULL        -- 校对完成时间（铁律审计）
);
CREATE TABLE photos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  essay_id INTEGER NOT NULL REFERENCES essays(id),
  seq INTEGER NOT NULL,
  file_path TEXT NOT NULL,
  width INTEGER, height INTEGER,
  engine1_text TEXT,
  engine2_text TEXT,                    -- 复核引擎结果（降级时为 NULL）
  diff_json TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(essay_id, seq)
);
CREATE TABLE recognition_tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  essay_id INTEGER NOT NULL REFERENCES essays(id),
  step TEXT NOT NULL DEFAULT 'queued',
    -- queued→engine1→judge→engine2→diff→done / engine2_failed→done(降级)
  retry_count INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  updated_at TEXT NOT NULL
);
```

**核心接口（类图要点）**
- `OcrEngineAdapter`（Protocol）：`name: str`、`async recognize(image_bytes, mime) -> EngineResult`
- `EngineResult(text: str, self_confidence: float, raw_response: dict)`
- `OpenAICompatAdapter`：base_url/api_key/model/timeout_s/max_retries 全配置驱动
- `ConfidenceScorer.score_essay(photos) -> float`、`is_low(overall) -> bool`
- `DiffService.char_diff(a, b) -> list[DiffSegment]`、`disagreement_rate(segments) -> float`
- `DiffSegment(type: equal|replace|delete|insert, text_a, text_b)`
- `RecognitionWorker`：enqueue / run_loop / _process / _stage_engine1 / _stage_engine2 / _stage_diff
- `PdfExporter.export_book(essays, template, order) -> bytes`、`TemplateRenderer.render_book_html(...)`

### 4. 程序调用流程（时序要点）

1. 老师（手机）拍照 2~3 张 → `POST /api/issues/{id}/essays`（multipart + student_id）→ 原片落盘 `data/photos/{issue}/{essay}/` → 建 essay(status=uploaded)+photos+task(queued) → enqueue → 返回 202
2. 前端 3s 轮询 `GET /api/essays/{id}`
3. Worker：step=engine1，逐张调主引擎（长超时+2 次重试）→ 存 engine1_text → step=judge 置信度评分 → 低置信则 essay.low_confidence=1 且调复核引擎（120s 超时）→ 成功则字符级 diff 存 diff_json；**失败（504 等）则降级：engine2_failed，无 diff 标注，不阻塞** → essay status=review
4. 校对页：GET 详情（含 diff_json）→ 左原片（zoom）右文字（编辑/存疑黄标/低置信横幅/铁律提示文案）→ `PATCH /api/essays/{id}`（final_text 非空 → status=proofread）
5. 成册页：`POST /api/exports/{issue_id}` → **校验该期全部 proofread（铁律，否则 409）** → Jinja2 渲染 → Playwright 打印 PDF（整册/单页）
6. 讲评课：`/present/{issue_id}` 投屏视图（横版大字号逐篇翻页，键盘+触屏）

### 5. 依赖包列表

**Python**：fastapi / uvicorn[standard] / sqlalchemy / aiosqlite / pydantic / pydantic-settings / httpx / python-multipart / jinja2 / playwright / pyyaml / pytest / pytest-asyncio / pytest-cov / ruff / mypy

**Node**：react / react-dom / react-router-dom / zustand / react-zoom-pan-pinch / typescript / vite / @vitejs/plugin-react / tailwindcss / postcss / autoprefixer / vitest / @testing-library/react / @testing-library/jest-dom / jsdom / eslint / @typescript-eslint/*

### 6. 任务列表（TDD，按依赖排序）

- **T01 项目基础设施**：后端骨架（FastAPI+配置+SQLite+口令登录）+ 前端骨架（Vite+React+Tailwind+路由+登录页）+ verify.sh 验证链。验收：test_auth 绿 + pnpm build 通过 + 登录契约测试绿。
- **T02 数据层 + 识别流水线**：全部 ORM（含预留字段）+ 上传 API + 适配器（配置驱动、httpx mock 可测）+ 置信度 + 字符级 diff + asyncio Worker（504 重试/超时/降级）。验收：mock 双引擎状态机全路径测试（含降级）绿，覆盖率 ≥80%。
- **T03 校对环 + 作文管理前端**：状态看板、拍照上传（手机 capture）、左右分栏校对、存疑黄标、低置信横幅、铁律提示、PATCH 定稿、移动端适配。验收：校对走查 ≤3 分钟；"未校对不可成册" 409 测试绿。
- **T04 成册导出 + 投屏视图**：3 套 Jinja2 模板、Playwright 导出、排序（学号/姓名/评分禁用）、投屏横版翻页。验收：45 篇全定稿→导出 ≤1 分钟；1920×1080 无缩放可读。
- **T05 集成联调 + 部署交付**：真实引擎冒烟、端到端用例、Linux 部署文档、覆盖率门禁收口。验收：验收清单全绿、覆盖率 ≥80%、服务器跑通完整周期。

依赖：T01→T02→T03→{T04, T05 并行收口}

### 7. 共享知识（跨文件约定）

- **API 信封**：成功 `{code:0, data, message}`；错误 `{code:非0, data:null, message}`；401 未授权、409 未校对成册。Token：`Authorization: Bearer`。
- **目录约定**：数据目录由 `EWB_DATA_DIR` 指定（开发 `./data`，生产 `/data/essay-workbench/`）：`photos/{issue_id}/{essay_id}/{uuid}.jpg`、`exports/`、`config/engines.yaml`、`config/app.yaml`。**DB 也在数据目录**，代码数据彻底分离。
- **适配器签名**：`async def recognize(image_bytes: bytes, mime: str) -> EngineResult`；`AdapterFactory.from_config(section)`；engines.yaml 两个 section：`[primary] timeout=30/retries=2`、`[review] timeout=120/retries=2`。
- **置信度口径**：整篇 = min(各张 self_confidence) 与启发式修正加权（生僻/不可打印字符占比 >2% 扣分、单图 <20 字扣分）；`is_low = overall < 0.85`（阈值 engines.yaml 可调）。
- **diff 格式**：`[{type, text_a, text_b}]`，字符级 SequenceMatcher；前端仅对 replace/delete/insert 的 text_a 段落打黄底"存疑"。
- **状态机**：essay.status ∈ {uploaded, recognizing, review, proofread, failed}；task.step ∈ {queued, engine1, judge, engine2, diff, done, engine2_failed}。
- **日期**：ISO 8601 UTC 存储，展示层转本地。
- **不可变数据**：engine1_text/engine2_text/diff_json 落库只读；老师编辑只写 final_text。照片永久留存，无删除端点。
- **工程纪律**：每任务收尾跑 verify.sh（ruff→mypy→pytest --cov-fail-under=80；tsc→eslint→vitest），任何一级失败即停。

---

## 附录：主理人对 5 项待明确事项的裁定（2026-09-10）

| # | 事项 | 裁定 |
|---|------|------|
| 1 | 学生名单初始化 | CSV 导入脚本（deploy/seed_students.py），一期不做界面管理 |
| 2 | 投屏视图鉴权 | **需登录**（token 持久化，登录一次即可）——公网部署下免登录会让学生作文裸奔 |
| 3 | 评分排序 | 预留禁用（score 字段已留），一期仅学号/姓名排序 |
| 4 | API Key 归属 | 老板自备，仅存数据目录 engines.yaml（gitignore），代码库只放 engines.yaml.example |
| 5 | 置信度阈值 | 默认 0.85，engines.yaml 可调，无需拍板 |
