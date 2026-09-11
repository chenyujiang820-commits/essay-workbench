/**
 * UI 适配性检查用 mock 后端：端口 8000，实现 /api 下所有前端用到的端点。
 * 数据为虚拟内容，仅用于页面渲染检查。
 */
const http = require("http");
const { URL } = require("url");

const students = Array.from({ length: 8 }, (_, i) => ({
  id: i + 1,
  student_no: String(2026001 + i),
  name: `学生${String(i + 1).padStart(2, "0")}`,
}));

const issues = [
  { id: 1, issue_no: 1, week_start_date: "2026-08-31", essay_count: 6 },
  { id: 2, issue_no: 2, week_start_date: "2026-09-07", essay_count: 3 },
];

const diffSegments = [
  { type: "equal", text_a: "星期天的早晨，阳光洒满了整个" },
  { type: "replace", text_a: "校园", text_b: "院子" },
  { type: "equal", text_a: "，小鸟在枝头唱着" },
  { type: "insert", text_a: "", text_b: "欢乐的" },
  { type: "equal", text_a: "歌。" },
];

function paragraphs(n) {
  return Array.from({ length: n }, (_, i) =>
    `第${i + 1}段。今天我去了公园，看到了许多美丽的花朵，有红的、黄的、紫的，五颜六色漂亮极了。我和小伙伴们在草地上奔跑，放飞了一只燕子形状的风筝，风筝越飞越高，我们的笑声也随风飘得很远很远。`,
  );
}

function essaySummaries(issueId) {
  const statuses = ["proofread", "review", "recognizing", "proofread", "failed", "review"];
  return students.slice(0, issueId === 1 ? 6 : 3).map((s, i) => ({
    id: (issueId - 1) * 10 + i + 1,
    issue_id: issueId,
    student_id: s.id,
    student_name: s.name,
    title: `难忘的一天（${issueId}）`,
    status: statuses[(i + issueId) % statuses.length],
    low_confidence: i === 1 ? 1 : 0,
    created_at: "2026-09-07 10:00:00",
  }));
}

function essayDetail(id) {
  const s = students[(id - 1) % students.length];
  return {
    id,
    issue_id: id > 10 ? 2 : 1,
    student_id: s.id,
    student_name: s.name,
    title: "难忘的一天",
    status: "review",
    low_confidence: 0,
    final_text: "",
    photos: [
      {
        id: id * 10 + 1,
        seq: 1,
        engine1_text: "星期天的早晨，阳光洒满了整个校园，小鸟在枝头唱着歌。",
        diff_json: diffSegments,
      },
      {
        id: id * 10 + 2,
        seq: 2,
        engine1_text: "下午我们回到家，妈妈夸我是个懂事的好孩子。",
        diff_json: [{ type: "equal", text_a: "下午我们回到家，妈妈夸我是个懂事的好孩子。" }],
      },
    ],
  };
}

const templates = [
  { key: "elegant", name: "素雅", description: "黑白灰简约排版" },
  { key: "warm", name: "暖阳", description: "暖色童趣排版" },
  { key: "classic", name: "经典", description: "传统作文本样式" },
];

function svgPhoto(id) {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="1600">
    <rect width="100%" height="100%" fill="#f5f0e6"/>
    <text x="60" y="120" font-size="48" fill="#333">原片 mock #${id}</text>
    <text x="60" y="240" font-size="36" fill="#555">星期天的早晨，阳光洒满了整个校园，</text>
    <text x="60" y="320" font-size="36" fill="#555">小鸟在枝头唱着歌。下午我们回到家，</text>
    <text x="60" y="400" font-size="36" fill="#555">妈妈夸我是个懂事的好孩子。</text>
  </svg>`;
}

function previewHtml() {
  return `<!doctype html><html><body style="font-family:sans-serif;padding:40px">
  <h1>第 1 期作文集（A4 预览，宽度 794px 模拟）</h1>
  <div style="width:794px;border:1px solid #ccc;padding:32px">
    <p>学生01 · 难忘的一天</p><p>正文……</p>
  </div></body></html>`;
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, "http://localhost");
  const path = url.pathname;
  const send = (data) => {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ code: 0, data, message: "ok" }));
  };

  if (path === "/api/auth/login" && req.method === "POST") {
    return send({ token: "mock-token" });
  }
  if (path === "/api/auth/logout") return send(null);
  if (path === "/api/me") return send({ role: "teacher" });
  if (path === "/api/issues" && req.method === "GET") return send(issues);
  if (path === "/api/issues" && req.method === "POST") {
    return send({ id: 3, issue_no: 3, week_start_date: "2026-09-14", essay_count: 0 });
  }
  const mIssue = path.match(/^\/api\/issues\/(\d+)$/);
  if (mIssue) return send(issues.find((i) => i.id === Number(mIssue[1])) ?? issues[0]);
  if (path === "/api/students") return send(students);
  const mIssueEssays = path.match(/^\/api\/issues\/(\d+)\/essays$/);
  if (mIssueEssays && req.method === "GET") return send(essaySummaries(Number(mIssueEssays[1])));
  if (mIssueEssays && req.method === "POST") return send({ essay_id: 11, photo_count: 2 });
  const mEssay = path.match(/^\/api\/essays\/(\d+)$/);
  if (mEssay && req.method === "GET") return send(essayDetail(Number(mEssay[1])));
  if (mEssay && req.method === "PATCH") return send({ ...essayDetail(Number(mEssay[1])), status: "proofread" });
  const mPhoto = path.match(/^\/api\/photos\/(\d+)\/file$/);
  if (mPhoto) {
    res.writeHead(200, { "Content-Type": "image/svg+xml" });
    return res.end(svgPhoto(Number(mPhoto[1])));
  }
  if (path === "/api/exports/templates") return send(templates);
  const mPreview = path.match(/^\/api\/exports\/(\d+)\/preview/);
  if (mPreview) {
    res.writeHead(200, { "Content-Type": "text/plain; charset=utf-8" });
    return res.end(previewHtml());
  }
  const mPresent = path.match(/^\/api\/exports\/(\d+)\/present/);
  if (mPresent) {
    return send({
      class_name: "三年二班",
      issue_no: 1,
      items: students.slice(0, 4).map((s, i) => ({
        student_no: s.student_no,
        name: s.name,
        title: i === 0 ? "难忘的一天" : "我的周末",
        is_selected: i === 0,
        paragraphs: paragraphs(i === 0 ? 8 : 3),
      })),
    });
  }
  const mExportBook = path.match(/^\/api\/exports\/(\d+)$/);
  if (mExportBook && req.method === "POST") {
    res.writeHead(200, { "Content-Type": "application/pdf" });
    return res.end("%PDF-1.4 mock");
  }
  res.writeHead(404, { "Content-Type": "application/json" });
  res.end(JSON.stringify({ code: 404, data: null, message: "not found: " + path }));
});

server.listen(8000, "127.0.0.1", () => console.log("mock api on 8000"));
