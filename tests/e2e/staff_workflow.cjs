// Run with Node's test runner and Playwright against the disposable MariaDB fixture.
// GW_E2E_DIR contains the fixture's .streamlit/secrets.toml and login-password.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const net = require('node:net');
const { spawn } = require('node:child_process');
const { randomUUID } = require('node:crypto');
const { chromium } = require('playwright');

const project = path.resolve(__dirname, '../..');
const results = path.join(project, 'test-results/staff-workflow');

async function freePort() {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}

test('Store Staff prices two items, prints, refreshes, and reprints from History', { timeout: 180000 }, async t => {
  const fixture = await fs.realpath(process.env.GW_E2E_DIR);
  const password = (await fs.readFile(path.join(fixture, 'login-password'), 'utf8')).trim();
  const settings = path.join(fixture, '.streamlit/secrets.toml');
  const config = await fs.readFile(settings, 'utf8');
  if (!/^host\s*=\s*["']127\.0\.0\.1["']/m.test(config) ||
      !/^database\s*=\s*["'][\w]*test[\w]*["']/mi.test(config)) {
    throw Error('A loopback disposable test schema is required.');
  }
  const port = await freePort();
  const base = 'http://127.0.0.1:' + port;
  const env = { ...process.env };
  for (const key of Object.keys(env)) {
    if (key.startsWith('MYSQL_') || key.startsWith('STREAMLIT_')) delete env[key];
  }
  // Streamlit also discovers settings beside the entry script. Keep that lookup
  // inside the fixture, while loading all page and service modules from the project.
  const entry = path.join(fixture, 'app.py');
  await fs.copyFile(path.join(project, 'app.py'), entry);
  await fs.copyFile(path.join(project, '.streamlit/config.toml'), path.join(fixture, '.streamlit/config.toml'));
  env.PYTHONPATH = project;
  const server = spawn(process.env.GW_E2E_PYTHON || path.join(project, '.venv/bin/python'), [
    '-m', 'streamlit', 'run', entry, '--server.headless=true',
    '--server.address=127.0.0.1', '--server.port=' + port, '--server.fileWatcherType=none',
    '--browser.gatherUsageStats=false', '--secrets.files=' + settings,
  ], { cwd: fixture, env, stdio: ['ignore', 'ignore', 'pipe'] });
  // Keep application logs out of the test report (including connection exceptions).
  server.stderr.resume();
  t.after(() => { server.kill(); });
  let healthy = false;
  for (let attempt = 0; attempt < 100 && server.exitCode === null; attempt++) {
    try { healthy = (await fetch(base + '/_stcore/health')).ok; } catch {}
    if (healthy) break;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.ok(healthy, 'Isolated Streamlit server must become healthy');
  const browser = await chromium.launch({ executablePath: process.env.GW_E2E_CHROME, headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1500, height: 1080 }, acceptDownloads: true });
  page.setDefaultTimeout(12000);
  await fs.mkdir(results, { recursive: true });
  const run = 'QA-' + randomUUID().slice(0, 8);
  const button = name => page.getByRole('button', { name, exact: typeof name === 'string' });
  async function settled() {
    await page.locator('[data-testid="stApp"][data-test-script-state="notRunning"]').waitFor();
    await page.locator('[data-stale="true"]').first().waitFor({ state: 'detached' });
  }
  const screen = async name => {
    await settled();
    await page.getByRole('listbox').waitFor({ state: 'hidden' });
    await page.screenshot({ path: path.join(results, name + '.png'), animations: 'disabled' });
  };
  async function enter(label, value) {
    const input = page.getByRole('textbox', { name: label, exact: true });
    await input.fill(value);
    await input.press('Enter');
  }
  async function choose(label, name) {
    await settled();
    const input = page.getByRole('combobox', { name: label, exact: true });
    await input.click();
    await input.fill(name);
    const option = page.getByRole('option', { name, exact: true });
    await option.waitFor();
    await input.press('ArrowDown');
    await input.press('Enter');
    await page.getByRole('listbox').waitFor({ state: 'hidden' });
    assert.equal(await input.inputValue(), name);
  }
  async function login(identifier) {
    await page.getByLabel('Email or Username', { exact: true }).fill(identifier);
    await page.getByLabel('Password', { exact: true }).fill(password);
    await button('Login').click();
    await button('Logout').waitFor();
  }
  async function attributes(description) {
    await enter('Item Description (Optional)', description);
    await choose('Item Type', 'Clothing');
    await choose('Category', 'Shirts');
    await choose('Brand Tier', 'Generic');
    await choose('Condition', 'Good');
  }
  async function search() {
    await button('Search Guidelines').click();
    await page.locator('.gw-suggestion .price').filter({ hasText: '$12.50' }).waitFor();
    assert.equal(await page.getByLabel('Final Price', { exact: true }).inputValue(), '$12.50');
  }
  async function download(name, filename) {
    const pending = page.waitForEvent('download');
    await page.getByText(name, { exact: true }).click();
    const file = await pending;
    assert.equal(await file.failure(), null);
    await file.saveAs(path.join(results, filename));
    const bytes = await fs.readFile(path.join(results, filename));
    assert.equal(bytes.subarray(0, 5).toString(), '%PDF-');
    return file.suggestedFilename();
  }
  async function receipt(description, price) {
    await page.getByText('Tag generated successfully', { exact: true }).waitFor();
    const preview = page.locator('.gw-tag-preview');
    assert.ok((await preview.innerText()).includes(description));
    assert.equal(await preview.locator('.gw-tag-price').innerText(), price);
    const barcode = preview.getByAltText('Code 128 barcode');
    assert.ok(await barcode.evaluate(img => img.complete && img.naturalWidth > 0));
    return (await preview.locator('.gw-tag-id b').innerText()).trim();
  }
  await page.goto(base);
  await button('Login').waitFor();
  await screen('01-login');
  await page.getByLabel('Email or Username', { exact: true }).fill('inactive_e2e');
  await page.getByLabel('Password', { exact: true }).fill(password);
  await button('Login').click();
  await page.getByText(/Invalid email, username or password/).waitFor();
  await login('staff_e2e');
  await button(/Start Pricing/).waitFor();
  assert.equal(await button('Configuration').count(), 0);
  await screen('02-dashboard');
  await button('Guidelines').click();
  await button('View Attribute Descriptions').click();
  await page.getByRole('heading', { name: 'Attribute Descriptions' }).waitFor();
  await choose('Attribute group', 'Categories');
  await enter('Search descriptions', 'upper body');
  await screen('03a-descriptions');
  await button('Hide Attribute Descriptions').click();
  await button('View Attribute Descriptions').waitFor();
  await choose('Item Type', 'Clothing');
  await choose('Category', 'Shirts');
  await enter('Search guideline ID or attribute', 'Shirts');
  await page.getByText(/1 matching guidelines/).waitFor();
  await screen('03-guidelines');
  await page.locator('.st-key-gw_nav_pricing button').click();
  await button('Search Guidelines').click();
  await page.getByText('Select Item Type, Category, Brand Tier and Condition.', { exact: true }).waitFor();
  await attributes(run + '-accept');
  await choose('Item Type', 'Furniture');
  await choose('Category', 'Chairs');
  await button('Search Guidelines').click();
  await page.getByText(/No active guideline matches/).waitFor();
  await choose('Item Type', 'Clothing');
  await choose('Category', 'Shirts');
  await screen('04-attributes');
  await search();
  await screen('05-suggested');
  await button('Submit & Generate Tag').dblclick();
  const acceptedTag = await receipt(run + '-accept', '$12.50');
  assert.equal(await download('Print Tag', 'accepted.pdf'), 'goodwill_' + acceptedTag + '.pdf');
  await button('Start Next Item').click();
  await attributes(run + '-override');
  await search();
  await enter('Final Price', '9.00');
  await button('Submit & Generate Tag').click();
  await page.getByText(/Please select a Reason for Override/).waitFor();
  await choose('Reason for Override', 'Visible Wear');
  await screen('06-override');
  await button('Submit & Generate Tag').click();
  const overriddenTag = await receipt(run + '-override', '$9.00');
  assert.notEqual(overriddenTag, acceptedTag);
  await screen('07-tag');
  assert.equal(await download('Print Tag', 'override.pdf'), 'goodwill_' + overriddenTag + '.pdf');
  await page.reload();
  await login('staff_e2e@example.invalid');
  await button('History').click();
  await enter('Tag ID / item description', run);
  await page.getByText('2 matching decisions shown', { exact: false }).waitFor();
  const activity = page.locator('.gw-activity-row').filter({ hasText: overriddenTag });
  const detail = await activity.innerText();
  for (const value of ['Shirts', '$9.00', 'Suggested $12.50', 'Visible Wear']) assert.ok(detail.includes(value));
  await screen('08-history');
  await choose('Price Change', 'No Override');
  await page.getByText('1 matching decisions shown', { exact: false }).waitFor();
  assert.ok((await page.locator('.gw-activity').innerText()).includes(acceptedTag));
  await choose('Price Change', 'Override');
  await page.locator('.gw-activity-row').filter({ hasText: overriddenTag }).waitFor();
  await button('Reprint an Existing Tag').click();
  await enter('Existing Tag ID', overriddenTag);
  await button('Find Tag').click();
  await page.getByText('Saved tag found. No new record has been created.', { exact: true }).waitFor();
  assert.equal(await download('Download Printable Tag PDF', 'reprint.pdf'), 'goodwill_' + overriddenTag + '.pdf');
  await screen('09-reprint');
  await button('Logout').click();
  await login('admin_e2e');
  await button(/Open Pricing History/).click();
  await enter('Tag ID / item description', run);
  await page.getByText('2 matching decisions shown', { exact: false }).waitFor();
  await page.getByRole('combobox', { name: 'Staff', exact: true }).waitFor();
  await page.getByRole('combobox', { name: 'Branch', exact: true }).waitFor();
  await choose('Branch', 'Test North (#1)');
  await choose('Staff', 'Fixture staff_e2e (#2)');
  await choose('Category', 'Shirts (#1)');
  await choose('Date', 'Last 7 days');
  await page.getByText('2 matching decisions shown', { exact: false }).waitFor();
  await screen('10-admin-history');
  await choose('Branch', 'Test South (#2)');
  await page.getByText('0 matching decisions shown', { exact: false }).waitFor();
  await page.getByText('No pricing decisions match these filters.', { exact: true }).waitFor();
  await fs.writeFile(path.join(results, 'browser-tags.json'), JSON.stringify({ run, acceptedTag, overriddenTag }, null, 2) + '\n');
});
