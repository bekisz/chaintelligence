const { chromium } = require('playwright');
const BASE = 'https://pancakeswap.finance';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
    viewport: { width: 1366, height: 900 }
  });
  const page = await context.newPage();
  const hits = [];
  page.on('response', async (resp) => {
    const url = resp.url();
    if (!/pancakeswap\.com|thegraph|graphql/i.test(url)) return;
    try {
      const body = await resp.text();
      if (/1e2faf20|liquidity\/pool\/bsc\/0x[a-f0-9]{64}|0x2170ed0880ac9a755fd29b2688956bd959f933f8/i.test(body)) {
        hits.push({ url: url.slice(0, 160), snippet: body.slice(0, 600) });
      }
    } catch (e) {}
  });

  // ETH token page — its pair rows carry 32-byte pool ids
  await page.goto(`${BASE}/info/infinity/pairs/tokens/0x2170ed0880ac9a755fd29b2688956bd959f933f8?chain=bsc`,
    { waitUntil: 'networkidle', timeout: 60000 }).catch(() => {});
  await page.waitForTimeout(4000);

  console.log('--- API responses containing pool ids / ETH address ---');
  const seen = new Set();
  hits.forEach((h) => {
    const key = h.url.split('?')[0];
    if (seen.has(key)) return;
    seen.add(key);
    console.log(`\nURL: ${h.url}`);
    console.log(`  body: ${h.snippet}`);
  });
  await browser.close();
})().catch((e) => { console.error('ERR', e.message); process.exit(1); });
