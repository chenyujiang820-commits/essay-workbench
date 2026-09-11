/** 修复后 UI 复测：针对 P0/P1/P2 修复点的定向截图验证。 */
const path = require("path");
const fs = require("fs");

const { chromium } = require(path.join("C:\\Users\\15050\\.workbuddy\\binaries\\node\\workspace", "node_modules", "playwright-core"));

const EXEC = "C:\\Users\\15050\\AppData\\Local\\ms-playwright\\chromium-1234\\chrome-win64\\chrome.exe";
const BASE = "http://localhost:5173";
const OUT = path.join(__dirname, "shots-fixed");

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ executablePath: EXEC });

  async function newPage(width, height, isMobile = false) {
    const ctx = await browser.newContext({
      viewport: { width, height },
      deviceScaleFactor: 2,
      isMobile,
      hasTouch: isMobile,
    });
    const page = await ctx.newPage();
    await page.goto(BASE + "/login", { waitUntil: "load" });
    await page.evaluate(() => localStorage.setItem("ewb_token", "mock-token"));
    return { ctx, page };
  }

  // A1：投屏 1366x768（原第 3 段截断）+ 375 手机
  {
    const { ctx, page } = await newPage(1366, 768);
    await page.goto(BASE + "/present/1", { waitUntil: "load" });
    await page.waitForTimeout(1500);
    await page.screenshot({ path: path.join(OUT, "A1-present-1366.png") });
    // 翻到第二篇（8 段长文），逐屏检查
    await page.keyboard.press("ArrowRight");
    await page.waitForTimeout(800);
    await page.screenshot({ path: path.join(OUT, "A1-present-essay2-screen1.png") });
    await page.keyboard.press("ArrowRight");
    await page.waitForTimeout(800);
    await page.screenshot({ path: path.join(OUT, "A1-present-essay2-screen2.png") });
    // 纯净模式退出按钮对比度
    await page.click("text=纯净模式");
    await page.waitForTimeout(500);
    await page.screenshot({ path: path.join(OUT, "C6-present-pure.png") });
    await ctx.close();
  }
  {
    const { ctx, page } = await newPage(375, 812, true);
    await page.goto(BASE + "/present/1", { waitUntil: "load" });
    await page.waitForTimeout(1500);
    await page.screenshot({ path: path.join(OUT, "A1-present-375.png") });
    await ctx.close();
  }

  // B1：看板返回首页入口（375）
  {
    const { ctx, page } = await newPage(375, 812, true);
    await page.goto(BASE + "/issues/1/essays", { waitUntil: "load" });
    await page.waitForTimeout(1200);
    await page.screenshot({ path: path.join(OUT, "B1-board-375.png") });
    await page.click('[data-testid="back-home"]');
    await page.waitForTimeout(800);
    const backHome = await page.textContent("h1");
    fs.writeFileSync(path.join(OUT, "B1-result.txt"), backHome);
    await ctx.close();
  }

  // B5/B8：上传页（触点 + 缩略图需要选图，仅验证 header 布局）
  {
    const { ctx, page } = await newPage(375, 812, true);
    await page.goto(BASE + "/issues/1/upload", { waitUntil: "load" });
    await page.waitForTimeout(1200);
    await page.screenshot({ path: path.join(OUT, "B5-upload-375.png") });
    await ctx.close();
  }

  // B6/C2：成册页预览缩放（375 + 1366）
  {
    const { ctx, page } = await newPage(375, 812, true);
    await page.goto(BASE + "/issues/1/book", { waitUntil: "load" });
    await page.waitForTimeout(1800);
    await page.screenshot({ path: path.join(OUT, "B6-book-375.png") });
    await ctx.close();
  }
  {
    const { ctx, page } = await newPage(1366, 768);
    await page.goto(BASE + "/issues/1/book", { waitUntil: "load" });
    await page.waitForTimeout(1800);
    await page.screenshot({ path: path.join(OUT, "B6-book-1366.png") });
    await ctx.close();
  }

  // A2：校对页手势返回拦截（手动验证困难，截图确认页面正常 + 导航按钮）
  {
    const { ctx, page } = await newPage(375, 812, true);
    await page.goto(BASE + "/essays/2/proofread", { waitUntil: "load" });
    await page.waitForTimeout(1500);
    await page.screenshot({ path: path.join(OUT, "A2-proofread-375.png") });
    await ctx.close();
  }

  // B4：校对/成册/上传页导航按钮齐全性检查（1366）
  {
    const { ctx, page } = await newPage(1366, 768);
    const results = {};
    for (const [name, url] of [
      ["proofread", "/essays/2/proofread"],
      ["book", "/issues/1/book"],
      ["upload", "/issues/1/upload"],
      ["board", "/issues/1/essays"],
    ]) {
      await page.goto(BASE + url, { waitUntil: "load" });
      await page.waitForTimeout(1000);
      results[name] = await page.$$eval('[data-testid="back-home"]', (els) => els.length);
    }
    fs.writeFileSync(path.join(OUT, "B4-backhome-counts.json"), JSON.stringify(results, null, 2));
    await ctx.close();
  }

  await browser.close();
  console.log("VERIFY DONE");
})();
