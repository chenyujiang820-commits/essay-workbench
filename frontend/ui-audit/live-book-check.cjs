// 补充验证：作文集页（/issues/1/book）真实后端实测
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 375, height: 812 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

  // 登录
  await page.goto('http://localhost:5173/', { waitUntil: 'networkidle' });
  await page.fill('input[type="password"]', 'admin123');
  await page.keyboard.press('Enter');
  await page.waitForURL('http://localhost:5173/', { timeout: 10000 });

  // 直接进入作文集页
  await page.goto('http://localhost:5173/issues/1/book', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: 'ui-audit/live-04-book.png' });

  const text = await page.evaluate(() => document.body.innerText);
  const hasBackHome = text.includes('首页') || text.includes('←');
  const hasNavEntry = text.includes('期数列表') || text.includes('看板');
  const bodyLen = text.length;
  console.log('=== 作文集页补充验证 ===');
  console.log('页面文本长度:', bodyLen);
  console.log('返回入口存在:', hasBackHome ? '是 ✓' : '否 ✗');
  console.log('看板/期数列表入口存在:', hasNavEntry ? '是 ✓' : '否 ✗');
  console.log('页面文本前200字:', JSON.stringify(text.slice(0, 200)));
  console.log('运行时错误数:', errors.length, errors.slice(0, 3));

  await browser.close();
})().catch((e) => { console.error('SCRIPT FAIL:', e.message); process.exit(1); });
