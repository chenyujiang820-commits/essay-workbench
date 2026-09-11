/** 真实环境冒烟验证：真实后端(admin123) + 真实数据，覆盖核心链路与修复点。 */
const path = require("path");
const fs = require("fs");
const { chromium } = require(path.join("C:\\Users\\15050\\.workbuddy\\binaries\\node\\workspace", "node_modules", "playwright-core"));

const EXEC = "C:\\Users\\15050\\AppData\\Local\\ms-playwright\\chromium-1234\\chrome-win64\\chrome.exe";
const OUT = __dirname;

(async () => {
  const b = await chromium.launch({ executablePath: EXEC });
  const ctx = await b.newContext({ viewport: { width: 1366, height: 768 }, deviceScaleFactor: 2 });
  const p = await ctx.newPage();
  const pageErrors = [];
  p.on("pageerror", (e) => pageErrors.push(String(e).slice(0, 150)));
  const results = [];
  const log = (n, msg) => { results.push(`${n}. ${msg}`); console.log(`${n}. ${msg}`); };

  // 1. 真实登录
  await p.goto("http://localhost:5173/login");
  await p.fill("#password", "admin123");
  await p.click("button[type=submit]");
  await p.waitForURL("**/localhost:5173/", { timeout: 8000 }).catch(() => {});
  await p.waitForTimeout(1000);
  log(1, `登录 ${p.url().endsWith(":5173/") ? "成功" : "失败"}，当前页: ${p.url()}`);

  // 2. 首页（期数列表）
  const homeText = (await p.textContent("main")).slice(0, 100).replace(/\s+/g, " ");
  log(2, `首页内容: ${homeText}`);
  await p.screenshot({ path: path.join(OUT, "live-01-home.png") });

  // 3. 进入第一个看板（若存在），验证 B1 返回入口
  const boardBtn = await p.$('button:has-text("进入看板")');
  if (boardBtn) {
    await boardBtn.click();
    await p.waitForTimeout(1500);
    const backHome = await p.$$('[data-testid="back-home"]');
    log(3, `看板打开 (${p.url()})，「期数列表」返回入口: ${backHome.length === 1 ? "存在 ✓" : "缺失 ✗"}`);
    await p.screenshot({ path: path.join(OUT, "live-02-board.png") });

    // 4. 看板内的作文卡片 → 校对页，验证 A2 导航按钮 + 顶部结构
    const essayCard = await p.$('li button.w-full');
    if (essayCard) {
      await essayCard.click();
      await p.waitForTimeout(2000);
      const navHome = await p.$$('[data-testid="back-home"]');
      const navBoard = await p.$$('button:has-text("← 看板")');
      log(4, `校对页打开，导航按钮: 期数列表×${navHome.length} ←看板×${navBoard.length}`);
      await p.screenshot({ path: path.join(OUT, "live-03-proofread.png") });

      // 5. A2 验证：改动文字后编程式触发路由返回（模拟手势/浏览器返回），应弹确认
      const textarea = await p.$('[data-testid="final-text"]');
      if (textarea) {
        await textarea.fill("真实环境冒烟测试修改内容");
        await p.waitForTimeout(300);
        let dialogSeen = false;
        p.once("dialog", async (d) => { dialogSeen = true; await d.dismiss(); });
        await p.goBack();
        await p.waitForTimeout(1200);
        log(5, `A2 手势返回拦截: ${dialogSeen ? "弹出确认 ✓" : "未拦截 ✗"}，仍在校对页: ${(await p.$('[data-testid="final-text"]')) !== null ? "是 ✓" : "否 ✗"}`);
      }

      // 6. 回看板 → 成册页，验证 B6 PreviewFrame + C2 状态
      await p.goto(p.url().replace(/\/essays\/\d+\/proofread/, "").replace("/essays/" + (p.url().match(/\/essays\/(\d+)\//) || [])[1] + "/proofread", "/issues/1/essays"), { waitUntil: "load" }).catch(() => {});
    } else {
      log(4, "看板暂无作文卡片，跳过校对页步骤");
    }

    // 成册页
    const bookBtn = await p.$('button:has-text("成册导出")');
    if (bookBtn) {
      await bookBtn.click();
      await p.waitForTimeout(2500);
      const frame = await p.$$('[data-testid="preview-frame"], [data-testid="preview-loading"], [data-testid="preview-retry"]');
      log(6, `成册页预览组件: ${frame.length >= 1 ? "PreviewFrame 已挂载 ✓" : "未找到 ✗"}`);
      await p.screenshot({ path: path.join(OUT, "live-04-book.png") });
    }

    // 7. 投屏页（真实数据）
    await p.goBack().catch(() => {});
    const presentBtn = await p.$('button:has-text("讲评投屏")').catch(() => null);
    if (presentBtn) {
      await presentBtn.click().catch(() => {});
      await p.waitForTimeout(2000);
    } else {
      const m = p.url().match(/issues\/(\d+)/);
      if (m) await p.goto(`http://localhost:5173/present/${m[1]}`, { waitUntil: "load" });
      await p.waitForTimeout(2000);
    }
    const slide = await p.$('[data-testid="present-slide"]');
    log(7, `投屏页: ${slide ? "正常渲染 ✓" : (await p.textContent("main")).includes("暂无作文") ? "该期暂无作文（正常空态）" : "异常"}`);
    await p.screenshot({ path: path.join(OUT, "live-05-present.png") });
  } else {
    log(3, "首页暂无期数（空库），仅验证登录链路");
  }

  log(8, `页面运行时错误: ${pageErrors.length === 0 ? "无 ✓" : pageErrors.join(" | ")}`);
  fs.writeFileSync(path.join(OUT, "live-smoke-result.txt"), results.join("\n"));
  await b.close();
  console.log("SMOKE DONE");
})();
