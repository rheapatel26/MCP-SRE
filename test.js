/**
 * test.js — Playwright MCP sample test with log capture
 *
 * Runs a simple browser test: navigates to example.com, asserts the title,
 * and saves a full structured log to logs/run_<timestamp>.json
 */

const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

// ─── Log Collector ───────────────────────────────────────────────────────────
const logs = [];

function log(level, message, data = {}) {
  const entry = {
    timestamp: new Date().toISOString(),
    level,
    message,
    ...data,
  };
  logs.push(entry);
  const prefix = { info: '✅', warn: '⚠️', error: '❌', step: '🔵' }[level] ?? '  ';
  console.log(`${prefix} [${entry.timestamp}] ${message}`, Object.keys(data).length ? data : '');
}

function saveLogs() {
  const logsDir = path.join(__dirname, 'logs');
  fs.mkdirSync(logsDir, { recursive: true });
  const filename = `run_${new Date().toISOString().replace(/[:.]/g, '-')}.json`;
  const filepath = path.join(logsDir, filename);
  fs.writeFileSync(filepath, JSON.stringify(logs, null, 2));
  console.log(`\n📄 Logs saved → ${filepath}`);
  return filepath;
}

// ─── Test Runner ─────────────────────────────────────────────────────────────
async function runTest() {
  log('info', 'Test suite started');

  const browser = await chromium.launch({ headless: true });
  log('step', 'Browser launched', { browser: 'Chromium', headless: true });

  const context = await browser.newContext();
  const page = await context.newPage();

  // Capture browser console logs
  page.on('console', (msg) => {
    log('info', `[Browser console] ${msg.type()}: ${msg.text()}`);
  });

  // Capture page errors
  page.on('pageerror', (err) => {
    log('error', `[Page error] ${err.message}`);
  });

  // ── Test 1: Navigate to example.com ──────────────────────────────────────
  try {
    log('step', 'Test 1 — Navigating to example.com');
    await page.goto('https://example.com', { waitUntil: 'domcontentloaded' });

    const title = await page.title();
    log('info', 'Page title retrieved', { title });

    if (title === 'Example Domain') {
      log('info', 'Test 1 PASSED ✓ — Title matches "Example Domain"');
    } else {
      log('warn', 'Test 1 FAILED — Unexpected title', { expected: 'Example Domain', got: title });
    }

    const heading = await page.locator('h1').first().textContent();
    log('info', 'H1 heading text', { heading: heading?.trim() });

    // Screenshot
    const screenshotPath = path.join(__dirname, 'logs', 'screenshot.png');
    fs.mkdirSync(path.dirname(screenshotPath), { recursive: true });
    await page.screenshot({ path: screenshotPath, fullPage: true });
    log('info', 'Screenshot saved', { path: screenshotPath });
  } catch (err) {
    log('error', 'Test 1 encountered an error', { error: err.message });
  }

  // ── Test 2: Check for a link on the page ─────────────────────────────────
  try {
    log('step', 'Test 2 — Checking for "Learn more" link');
    const link = page.locator('a', { hasText: 'Learn more' });
    const count = await link.count();

    if (count > 0) {
      const href = await link.getAttribute('href');
      log('info', 'Test 2 PASSED ✓ — Link found', { href });
    } else {
      log('warn', 'Test 2 FAILED — Link not found');
    }
  } catch (err) {
    log('error', 'Test 2 encountered an error', { error: err.message });
  }

  await browser.close();
  log('info', 'Browser closed');
  log('info', 'Test suite completed');

  return saveLogs();
}

// ─── Entry Point ─────────────────────────────────────────────────────────────
runTest()
  .then((logPath) => {
    console.log('\n🎉 All tests finished. Log file:', logPath);
    process.exit(0);
  })
  .catch((err) => {
    console.error('\n💥 Fatal error:', err);
    process.exit(1);
  });
