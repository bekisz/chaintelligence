const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    httpCredentials: { username: process.env.PORTAL_USERNAME, password: process.env.PORTAL_PASSWORD },
    viewport: { width: 1280, height: 900 }
  });
  const page = await context.newPage();
  await page.goto('http://localhost:8000/routing', { waitUntil: 'domcontentloaded' });

  await page.fill('#start-token', 'USDC');
  await page.fill('#end-token', 'USDT');
  await page.fill('#start-date', '2026-07-06');
  await page.fill('#end-date', '2026-07-07');
  await page.selectOption('#query-network-filter', 'BNB');
  await page.click('#analyze-btn');

  await page.waitForSelector('.route-card, .route-path-container, #no-data', { timeout: 60000 });
  // The V4 link is rendered in the DOM regardless of the protocol filter
  // (the filter only hides non-matching routes), so check the href directly.
  await page.waitForTimeout(800);

  const hrefs = await page.$$eval(
    'a[href*="pancakeswap.finance/info/v4/pools/"]',
    (as) => as.map((a) => a.getAttribute('href'))
  );
  console.log('V4 links found:', hrefs.length);
  hrefs.slice(0, 3).forEach((h) => console.log('  ', h));
  await browser.close();
  if (hrefs.length === 0) { console.error('FAIL: no PancakeSwap V4 link rendered'); process.exit(1); }
  console.log('PASS: PancakeSwap V4 link rendered in UI');
})().catch((e) => { console.error('ERR', e.message); process.exit(1); });
