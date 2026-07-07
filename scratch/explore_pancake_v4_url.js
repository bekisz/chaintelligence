const { chromium } = require('playwright');
const BASE = 'https://pancakeswap.finance';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
    viewport: { width: 1366, height: 900 }
  });
  const page = await context.newPage();

  // Capture API calls that might return the PoolKey / token addresses.
  const apiHits = [];
  page.on('response', async (resp) => {
    const url = resp.url();
    if (/graphql|api|subgraph|info/i.test(url)) {
      try {
        const body = await resp.text();
        if (/pool|currency|tickSpacing|hook|0x1e2faf/i.test(body)) {
          apiHits.push({ url: url.slice(0, 120), snippet: body.slice(0, 400) });
        }
      } catch (e) {}
    }
  });

  const poolUrl = `${BASE}/liquidity/pool/bsc/0x1e2faf20e424bda35e366d2bcdb01fd13f791b2e1e19d148e29573891bcebfb8`;
  await page.goto(poolUrl, { waitUntil: 'networkidle', timeout: 60000 }).catch(() => {});
  await page.waitForTimeout(4000);

  const text = await page.evaluate(() => document.body.innerText);
  console.log('--- page text snippet ---');
  console.log(text.replace(/\s+/g, ' ').slice(0, 500));

  // Look for token addresses (0x + 40 hex) in the page HTML
  const addrs = await page.evaluate(() => {
    const html = document.documentElement.innerHTML;
    const re = /0x[a-f0-9]{40}/gi;
    return [...new Set(html.match(re) || [])].slice(0, 20);
  });
  console.log('\n--- addresses in DOM ---');
  addrs.forEach((a) => console.log('  ', a));

  console.log('\n--- relevant API hits ---');
  apiHits.slice(0, 5).forEach((h) => console.log(`  ${h.url}\n    ${h.snippet.slice(0,300)}`));

  await browser.close();
})().catch((e) => { console.error('ERR', e.message); process.exit(1); });
