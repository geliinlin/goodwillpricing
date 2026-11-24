// Run with Node's test runner and Playwright against the disposable MariaDB fixture.
// GW_E2E_DIR contains the fixture's .streamlit/secrets.toml and login-password.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const net = require('node:net');
const { spawn } = require('node:child_process');
const { chromium } = require('playwright');

const project = path.resolve(__dirname, '../..');

async function freePort() {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}

async function startApp(t) {
  if (process.env.GW_ACCEPTANCE_ENDPOINT) {
    const endpoint = new URL(process.env.GW_ACCEPTANCE_ENDPOINT);
    assert.equal(endpoint.protocol, 'http:');
    assert.equal(endpoint.hostname, '127.0.0.1');
    assert.ok(endpoint.port && process.env.GW_ACCEPTANCE_LOGIN);
    const browser = await chromium.launch({executablePath:process.env.GW_E2E_CHROME, headless:true});
    t.after(() => browser.close());
    return {base:endpoint.origin, browser, password:process.env.GW_ACCEPTANCE_LOGIN};
  }
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
  return { base, browser, password };
}

module.exports = { startApp };
