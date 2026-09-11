/**
 * UI 适配性自动检查：多视口 × 多页面截图 + 控制台错误采集。
 * 产出：ui-audit/shots/<viewport>/<page>.png 与 console 报告。
 */
const path = require("path");
const fs = require("fs");

const { chromium } = require(path.join("C:\\Users\\15050\\.workbuddy\\binaries\\node\\workspace", "node_modules", "playwright-core"));

const EXEC = "C:\\Users\\15050\\AppData\\Local\\ms-playwright\\chromium-1234\\chrome-win64\\chrome.exe";
const BASE = "http://localhost:5173";
const OUT = path.join(__dirname, "shots");

const VIEWPORTS = [
  { name: "mobile-375", width: 375, height: 812 },
  { name: "mobile-390", width: 390, height: 844 },
  { name: "tablet-768", width: 768, height: 1024 },
  { name: "laptop-1366", width: 1366, height: 768 },
  { name: "desktop-1920", width: 1920, height: 1080 },
];

const PAGES = [
  { name: "01-login", url: "/login" },
  { name: "02-issues", url: "/" },
  { name: "03-essay-list", url: "/issues/1/essays" },
  { name: "04-upload", url: "/issues/1/upload" },
  { name: "05-proofread", url: "/essays/2/proofread" },
  { name: "06-book", url: "/issues/1/book" },
  { name: "07-present", url: "/present/1" },
];

// 横向溢出检测：document.scrollWidth > innerWidth 即视为溢出
async function auditPage(page) {
  return page.evaluate(() => {
    const overflowers = [];
    const vw = document.documentElement.clientWidth;
    document.querySelectorAll("*").forEach((el) => {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && (r.right > vw + 1 || r.left < -1)) {
        const cls = (typeof el.className === "string" ? el.className : "").slice(0, 60);
        overflowers.push(`${el.tagName.toLowerCase()}.${cls} right=${Math.round(r.right)} vw=${vw}`);
      }
    });
    return {
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: vw,
      overflowers: overflowers.slice(0, 8),
      tinyButtons: Array.from(document.querySelectorAll("button"))
        .filter((b) => b.getBoundingClientRect().height > 0 && b.getBoundingClientRect().height < 24)
        .map((b) => b.textContent?.trim().slice(0, 12)),
    };
  });
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ executablePath: EXEC });
  const report = [];

  for (const vp of VIEWPORTS) {
    const context = await browser.newContext({
      viewport: { width: vp.width, height: vp.height },
      deviceScaleFactor: 2,
      isMobile: vp.width < 500,
      hasTouch: vp.width < 500,
    });
    const page = await context.newPage();
    const consoleErrors = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text().slice(0, 150));
    });
    page.on("pageerror", (err) => consoleErrors.push("PAGEERROR: " + String(err).slice(0, 200)));

    const vDir = path.join(OUT, vp.name);
    fs.mkdirSync(vDir, { recursive: true });

    for (const p of PAGES) {
      // 先注入 token 再访问受保护页面
      await page.goto(BASE + "/login", { waitUntil: "load" });
      await page.evaluate(() => localStorage.setItem("ewb_token", "mock-token"));
      await page.goto(BASE + p.url, { waitUntil: "load" });
      await page.waitForTimeout(1200);
      const audit = await auditPage(page).catch(() => null);
      await page.screenshot({ path: path.join(vDir, p.name + ".png"), fullPage: vp.name === "desktop-1920" ? false : true });
      const overflow = audit && audit.scrollWidth > audit.clientWidth;
      report.push({
        viewport: vp.name,
        page: p.name,
        url: p.url,
        overflow,
        scrollWidth: audit?.scrollWidth,
        clientWidth: audit?.clientWidth,
        overflowers: audit?.overflowers ?? [],
        tinyButtons: audit?.tinyButtons ?? [],
        consoleErrors: consoleErrors.slice(0, 5),
      });
    }
    await context.close();
  }

  // 交互检查：投屏翻页 / 纯净模式 / 校对页移动端切换
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 768 } });
  const page = await ctx.newPage();
  await page.goto(BASE + "/login");
  await page.evaluate(() => localStorage.setItem("ewb_token", "mock-token"));
  await page.goto(BASE + "/present/1");
  await page.waitForTimeout(1000);
  await page.screenshot({ path: path.join(OUT, "interact-present.png") });
  // 纯净模式按钮
  const pureBtn = await page.$("text=纯净模式");
  if (pureBtn) {
    await pureBtn.click();
    await page.waitForTimeout(300);
    await page.screenshot({ path: path.join(OUT, "interact-present-pure.png") });
  }
  // 键盘翻页到最后
  for (let i = 0; i < 12; i++) await page.keyboard.press("ArrowRight");
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(OUT, "interact-present-last.png") });

  // 移动端校对页切换
  const mctx = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
  const mpage = await mctx.newPage();
  await mpage.goto(BASE + "/login");
  await mpage.evaluate(() => localStorage.setItem("ewb_token", "mock-token"));
  await mpage.goto(BASE + "/essays/2/proofread");
  await mpage.waitForTimeout(1500);
  await mpage.screenshot({ path: path.join(OUT, "interact-proofread-mobile.png") });
  const textTab = await mpage.$("text=文字");
  if (textTab) {
    await textTab.click();
    await mpage.waitForTimeout(300);
    await mpage.screenshot({ path: path.join(OUT, "interact-proofread-mobile-text.png") });
  }

  await browser.close();
  fs.writeFileSync(path.join(__dirname, "audit-report.json"), JSON.stringify(report, null, 2));
  console.log("DONE. pages audited:", report.length);
  const bad = report.filter((r) => r.overflow);
  console.log("overflow pages:", bad.map((r) => `${r.viewport}/${r.page}`).join(", ") || "none");
})();
