#!/usr/bin/env node
// Drive the actual UI/API/SQLite with controlled providers, including API restart.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { setTimeout: delay } = require('node:timers/promises');
const root = path.resolve(__dirname, '..');
const { chromium } = require(path.join(root, 'frontend/node_modules/playwright'));
const python = process.env.INT2_PYTHON || path.join(root, '.venv/bin/python');
const apiPort = Number(process.env.INT2_API_PORT || 18200);
const uiPort = Number(process.env.INT2_UI_PORT || 13200);
const apiUrl = `http://127.0.0.1:${apiPort}`;
const uiUrl = `http://127.0.0.1:${uiPort}`;
const providerBootstrap = `
import io, sys, wave
from pathlib import Path
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, str(Path(sys.argv[1]) / 'src'))
from chatterpal.config.settings import Settings
from chatterpal.web import api_server
from tests.int2_support import FixtureASR, FixtureLLM, FixtureTTS
buffer = io.BytesIO()
with wave.open(buffer, 'wb') as audio:
    audio.setnchannels(1)
    audio.setsampwidth(2)
    audio.setframerate(16000)
    audio.writeframes(b'\\0\\0' * 1600)
asr, llm, tts = FixtureASR(), FixtureLLM(), FixtureTTS(buffer.getvalue())
api_server.create_asr = lambda settings: asr
api_server.create_llm = lambda settings: llm
api_server.create_tts = lambda settings: tts
settings = Settings(_env_file=None, learning_db_path=sys.argv[2], learning_shutdown_timeout=1,
    learning_allowed_origins=sys.argv[4])
server = api_server.APIServer(settings)
server.temp_dir = Path(sys.argv[2]).parent
server.run(host='127.0.0.1', port=int(sys.argv[3]))
`;

async function ready(url, child) {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`Server exited: ${child.exitCode}; ${url}`);
    try { if ((await fetch(url)).ok) return; } catch { /* server still starting */ }
    await delay(100);
  }
  throw new Error(`Server did not listen: ${url}`);
}
async function stop(child) {
  if (!child || child.exitCode !== null) return;
  await new Promise(resolve => { child.once('exit', resolve); child.kill('SIGTERM'); });
}

(async () => {
  const output = process.env.INT2_OUTPUT_DIR || await fs.mkdtemp(path.join(os.tmpdir(), 'chatterpal-int2-browser-'));
  await fs.mkdir(output, { recursive: true });
  const handles = [];
  async function startAPI(restarted = false) {
    const log = await fs.open(path.join(output, restarted ? 'api-restart.log' : 'api.log'), 'w');
    handles.push(log);
    const child = spawn(python, ['-c', providerBootstrap, root, path.join(output, 'learning.sqlite3'), String(apiPort), uiUrl], {
      cwd: output, env: { ...process.env, GRADIO_ANALYTICS_ENABLED: 'False', HF_HUB_DISABLE_TELEMETRY: '1' },
      stdio: ['ignore', log.fd, log.fd],
    });
    await ready(`${apiUrl}/health`, child);
    return child;
  }
  let backend;
  let frontend;
  let browser;
  const requests = [];
  const errors = [];
  try {
    backend = await startAPI();
    const log = await fs.open(path.join(output, 'vite.log'), 'w');
    handles.push(log);
    frontend = spawn(process.execPath, [path.join(root, 'frontend/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(uiPort), '--strictPort'], {
      cwd: path.join(root, 'frontend'), env: { ...process.env, CHATTERPAL_API_TARGET: apiUrl },
      stdio: ['ignore', log.fd, log.fd],
    });
    await ready(uiUrl, frontend);
    browser = await chromium.launch({
      ...(process.env.CHROME_EXECUTABLE_PATH ? { executablePath: process.env.CHROME_EXECUTABLE_PATH } : {}),
      headless: true,
      args: ['--disable-gpu'],
    });
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, reducedMotion: 'reduce' });
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    page.on('pageerror', error => errors.push(error.message));
    page.on('response', response => {
      if (response.url().includes('/api/learning/')) requests.push({ method: response.request().method(), path: new URL(response.url()).pathname, status: response.status() });
    });
    await page.goto(uiUrl);
    await page.getByRole('button', { name: '开始描述周末', exact: true }).click();
    const composer = page.getByRole('textbox', { name: '我的任务回答', exact: true });
    await composer.fill('I go hiking last Saturday.');
    await page.getByRole('button', { name: '给我一个开头', exact: true }).click();
    await page.getByText('Last weekend, I ...', { exact: true }).waitFor();
    assert.equal(await composer.inputValue(), 'I go hiking last Saturday.');
    await page.getByRole('button', { name: '学习记录', exact: true }).click();
    await page.getByRole('button', { name: '打开练习记录', exact: true }).click();
    assert.equal(await composer.inputValue(), 'I go hiking last Saturday.');
    for (const text of ['I go hiking last Saturday.', 'I was with my friends.', 'It was relaxing because we had time to talk.']) {
      await composer.fill(text);
      await page.getByRole('button', { name: '发送任务回答', exact: true }).click();
      await page.getByRole('log', { name: '任务对话' }).getByText(text, { exact: true }).waitFor();
      await page.waitForFunction(element => element.value === '', await composer.elementHandle());
    }
    await page.getByRole('button', { name: '结束对话，查看反馈', exact: true }).click();
    await page.getByRole('region', { name: '练后重点反馈' }).waitFor();
    await page.getByRole('button', { name: '针对这一句重练', exact: true }).click();
    for (const [index, text] of [
      'On Saturday I hiked with friends and enjoyed the quiet trail.',
      'I spent Saturday hiking with my friends. I felt relaxed outdoors.',
    ].entries()) {
      await page.getByRole('textbox', { name: '我的新表达', exact: true }).fill(text);
      await page.getByRole('button', { name: '保存这次重练', exact: true }).click();
      await page.getByText(`第 ${index + 2} 次练习`, { exact: true }).waitFor();
    }
    await page.getByRole('button', { name: '获取目标范读', exact: true }).click();
    await page.getByRole('button', { name: '播放目标范读', exact: true }).waitFor();
    await page.screenshot({ path: path.join(output, 'weekend-retries-desktop.png'), fullPage: true });
    const before = await page.evaluate(async () => {
      const response = await fetch('/api/learning/sessions');
      if (!response.ok) throw new Error('Cannot read saved sessions');
      return (await response.json()).items[0];
    });
    assert.equal(before.turns.filter(turn => turn.role === 'user').length, 3);
    assert.equal(before.attempts.length, 3);
    assert.equal(before.attempts[1].parent_attempt_id, before.attempts[0].attempt_id);
    assert.equal(before.attempts[2].parent_attempt_id, before.attempts[1].attempt_id);
    const cookieBefore = (await context.cookies()).find(cookie => cookie.name === 'chatterpal_learner');
    assert.ok(cookieBefore?.httpOnly);
    await page.reload();
    await page.getByRole('button', { name: '学习记录', exact: true }).click();
    await page.getByRole('button', { name: '打开练习记录', exact: true }).click();
    await page.getByRole('button', { name: '针对这一句重练', exact: true }).click();
    await page.getByText('第 3 次练习', { exact: true }).waitFor();
    await stop(backend);
    backend = await startAPI(true);
    await page.reload();
    await page.getByRole('button', { name: '学习记录', exact: true }).click();
    await page.getByRole('button', { name: '打开练习记录', exact: true }).click();
    await page.getByRole('button', { name: '针对这一句重练', exact: true }).click();
    await page.getByText('第 3 次练习', { exact: true }).waitFor();
    const after = await page.evaluate(async sessionId => (await fetch(`/api/learning/sessions/${sessionId}`)).json(), before.session_id);
    assert.deepEqual(after, before);
    const cookieAfter = (await context.cookies()).find(cookie => cookie.name === 'chatterpal_learner');
    assert.equal(cookieAfter.value, cookieBefore.value);
    await page.setViewportSize({ width: 375, height: 812 });
    await page.evaluate(async () => { await document.fonts.ready; await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.equal(await page.locator('.app-header').count(), 1);
    const mobileLayout = await page.evaluate(() => ({
      height: document.documentElement.scrollHeight, width: document.documentElement.scrollWidth,
      feedbackTop: document.querySelector('.learning-feedback').getBoundingClientRect().top + scrollY,
      practiceTop: document.querySelector('.learning-practice').getBoundingClientRect().top + scrollY,
    }));
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({ path: path.join(output, 'weekend-mobile-top.png'), animations: 'disabled' });
    await page.locator('.learning-feedback').scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, 'weekend-mobile-feedback.png'), animations: 'disabled' });
    await page.getByRole('textbox', { name: '我的新表达', exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, 'weekend-mobile-practice.png'), animations: 'disabled' });
    assert.ok(await page.evaluate(() => scrollY > 0), 'Mobile page must scroll to the practice controls');
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({ path: path.join(output, 'weekend-retries-mobile.png'), fullPage: true, animations: 'disabled' });
    await fs.writeFile(path.join(output, 'mobile-layout.json'), JSON.stringify(mobileLayout, null, 2));
    await page.getByRole('button', { name: '到期复习', exact: true }).click();
    await page.getByText('当前没有到期复习。可以从练后反馈或已保存的尝试安排次日复习。', { exact: true }).waitFor();
    assert.equal((await context.request.get(`${uiUrl}/api/learning/reviews/due`)).status(), 200);
    assert.deepEqual(errors, []);
    const summary = { output, passed: true, session_id: before.session_id, user_turns: 3, attempts: 3, linked_retries: 2, browser_reload: true, api_restart: true, hint_preserved_draft: true, history_preserved_draft: true, mobile_overflow: false, errors, requests };
    await fs.writeFile(path.join(output, 'results.json'), JSON.stringify(summary, null, 2));
    console.log(JSON.stringify(summary, null, 2));
  } catch (error) {
    console.error(`INT2 browser evidence: ${output}`);
    throw error;
  } finally {
    if (browser) await browser.close();
    await stop(frontend);
    await stop(backend);
    for (const handle of handles) await handle.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
