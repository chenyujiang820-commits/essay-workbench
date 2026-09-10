# 班级作文工作台 · 一期

高中语文教师的作文收/改/成册工作台：手机拍照上传 → 双引擎 OCR 识别 → 字符级 diff 标疑 →
老师校对定稿 → 成册导出 PDF → 课堂投屏表彰。

> 本仓库当前为 **T01 + T02** 交付范围：项目基础设施 + 数据层 + 识别流水线。
> 校对环前端（T03）、成册导出/投屏（T04）、集成联调/部署（T05）另行交付。

---

## 目录结构

```
essay-workbench/
├── backend/                     # FastAPI + SQLAlchemy(async) + aiosqlite
│   ├── app/
│   │   ├── main.py              # 应用入口：生命周期 / 异常信封 / 静态托管
│   │   ├── config.py            # EWB_DATA_DIR + 数据目录 YAML 配置
│   │   ├── db.py                # async engine / session 工厂 / WAL PRAGMA
│   │   ├── models.py            # students/issues/essays/photos/recognition_tasks
│   │   ├── schemas.py           # Pydantic 契约（统一信封 {code,data,message}）
│   │   ├── auth.py              # 口令哈希 + Bearer token 签发/校验
│   │   ├── routers/             # auth / issues / essays / exports
│   │   └── pipeline/            # engine_adapter / confidence / diff / worker
│   ├── config/                  # *.example（真实配置放数据目录，不进 Git）
│   └── tests/                   # pytest（覆盖率门禁 >= 80%）
├── frontend/                    # Vite + React18 + TS + Tailwind + Zustand
├── deploy/                      # systemd / nginx / 学生名单脚本（T05）
└── verify.sh                    # 一键验证链
```

## 快速开始

### 1. 后端

```bash
cd backend
python -m venv .venv
# Windows (Git Bash)
.venv/Scripts/python.exe -m pip install -e ".[dev]"
# Linux / macOS
# .venv/bin/python -m pip install -e ".[dev]"

# 配置（首次启动会自动生成默认 app.yaml + 复制 engines.yaml）
# 数据目录默认 ./data（可用环境变量 EWB_DATA_DIR 覆盖）
cp config/engines.yaml.example data/config/engines.yaml   # 如未自动生成
# 编辑 data/config/engines.yaml，填入 base_url / api_key / model

.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
```

* 初始口令：环境变量 `EWB_PASSWORD`（未设置则默认 `admin123`），可用
  `python -m app.auth hash <新口令>` 生成哈希写入 `data/config/app.yaml`。
* OpenAPI 文档：<http://127.0.0.1:8000/docs>

### 2. 前端

```bash
cd frontend
npm install
npm run dev      # http://127.0.0.1:5173（/api 已代理到 8000）
```

### 3. 一键验证

```bash
bash verify.sh   # ruff -> mypy -> pytest(cov>=80) -> tsc -> eslint -> vitest
```

## 关键约定

| 项 | 约定 |
|---|---|
| 数据目录 | `EWB_DATA_DIR`（开发 `./data`，生产 `/data/essay-workbench/`）；DB/照片/配置全部在内 |
| 目录布局 | `photos/{issue_id}/{essay_id}/{uuid}.jpg`、`exports/`、`config/engines.yaml`、`config/app.yaml` |
| API 信封 | 成功 `{code:0,data,message}`；错误 `{code:非0,data:null,message}`；401 未授权、409 未校对不可成册 |
| Token | `Authorization: Bearer <token>` |
| 状态机 | `essay.status` ∈ uploaded/recognizing/review/proofread/failed；`task.step` ∈ queued/engine1/judge/engine2/diff/done/engine2_failed |
| 不可变审计 | `photos.engine1_text/engine2_text/diff_json` 落库只读，老师只写 `essays.final_text` |
| 置信度 | 整篇 = min(各张) + 启发式修正；阈值默认 0.85（`engines.yaml` 可调） |
| 复核降级 | 复核引擎超时/504 → `engine2_failed` + error，不阻塞，照常进入 review |
| 日期 | ISO 8601 UTC 存储，展示层转本地 |

## 安全

* API Key 只存数据目录 `config/engines.yaml`（已 gitignore），代码库仅存 `*.example`。
* 数据目录（照片、SQLite、导出）整体不入 Git。
