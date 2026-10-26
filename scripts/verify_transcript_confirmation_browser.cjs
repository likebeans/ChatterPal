#!/usr/bin/env node
// Real browser, HTTP and SQLite acceptance with controlled provider boundaries.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { spawn } = require('node:child_process');
const { setTimeout: delay } = require('node:timers/promises');
const root = path.resolve(__dirname, '..');
const { chromium } = require(path.join(root, 'frontend/node_modules/playwright'));
const python = process.env.TRANSCRIPT_PYTHON || path.join(root, '.venv/bin/python');
const apiPort = Number(process.env.TRANSCRIPT_API_PORT || 18240);
const uiPort = Number(process.env.TRANSCRIPT_UI_PORT || 13240);
const apiUrl = `http://127.0.0.1:${apiPort}`;
const uiUrl = `http://127.0.0.1:${uiPort}`;
const machineText = 'I go hiking with my friends last weekend.';
const bootstrap = `
import io, json, math, struct, sys, wave
from datetime import datetime
from pathlib import Path
root, output, port, origin = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / 'src'))
from chatterpal.config.settings import Settings
from chatterpal.web import api_server
from tests.int2_support import FixtureASR, FixtureLLM, FixtureTTS
buffer = io.BytesIO()
with wave.open(buffer, 'wb') as audio:
    audio.setnchannels(1)
    audio.setsampwidth(2)
    audio.setframerate(16000)
    audio.writeframes(b''.join(struct.pack('<h', int(1000 * math.sin(index * 2 * math.pi * 220 / 16000))) for index in range(16000)))
def event(kind):
    with (output / 'providers.jsonl').open('a') as stream:
        stream.write(json.dumps({'kind': kind}) + '\\n')
class ASR(FixtureASR):
    def recognize(self, content):
        event('asr')
        return super().recognize(content)
class LLM(FixtureLLM):
    def chat(self, messages):
        event('llm')
        return super().chat(messages)
class TTS(FixtureTTS):
    def synthesize(self, text):
        event('tts')
        return super().synthesize(text)
asr, llm, tts = ASR(), LLM(), TTS(buffer.getvalue())
asr.text = ${JSON.stringify(machineText)}
api_server.create_asr = lambda settings: asr
api_server.create_llm = lambda settings: llm
api_server.create_tts = lambda settings: tts
settings = Settings(_env_file=None, learning_db_path=str(output / 'learning.sqlite3'), learning_allowed_origins=origin, learning_shutdown_timeout=1, audio_temp_dir=str(output / 'audio'))
server = api_server.APIServer(settings)
def clock():
    return datetime.fromisoformat((output / 'clock.txt').read_text().strip())
server.app.state.learning_repository().clock = lambda: clock().timestamp()
server.app.state.learning_service().clock = clock
server.app.state.learning_review_service().clock = clock
server.run(host='127.0.0.1', port=port)
`;

async function ready(url, child) {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`Service exited ${child.exitCode}: ${url}`);
    try { if ((await fetch(url)).ok) return; } catch { /* Starting. */ }
    await delay(100);
  }
  throw new Error(`Service did not listen: ${url}`);
}
async function stop(child) {
  if (!child || child.exitCode !== null) return;
  const exited = new Promise(resolve => child.once('exit', resolve));
  child.kill('SIGTERM');
  const timer = setTimeout(() => child.kill('SIGKILL'), 15000);
  await exited;
  clearTimeout(timer);
}
const digest = value => createHash('sha256').update(value).digest('hex');

(async () => {
  const output = process.env.TRANSCRIPT_OUTPUT_DIR || await fs.mkdtemp(path.join(os.tmpdir(), 'chatterpal-transcript-browser-'));
  await fs.mkdir(output, { recursive: true });
  await fs.writeFile(path.join(output, 'clock.txt'), '2026-10-26T08:00:00+00:00');
  await fs.writeFile(path.join(output, 'providers.jsonl'), '');
  const errors = [], requests = [], operations = [], handles = [];
  let backend, frontend, browser, page;
  async function counts() {
    const lines = (await fs.readFile(path.join(output, 'providers.jsonl'), 'utf8')).trim();
    const values = lines ? lines.split('\n').map(line => JSON.parse(line)) : [];
    return Object.fromEntries(['asr', 'llm', 'tts'].map(kind => [kind, values.filter(value => value.kind === kind).length]));
  }
  async function startAPI(phase) {
    const log = await fs.open(path.join(output, `api-${phase}.log`), 'w'); handles.push(log);
    backend = spawn(python, ['-c', bootstrap, root, output, String(apiPort), uiUrl], {
      cwd: output, env: { ...process.env, GRADIO_ANALYTICS_ENABLED: 'False', HF_HUB_DISABLE_TELEMETRY: '1' }, stdio: ['ignore', log.fd, log.fd],
    });
    await ready(`${apiUrl}/health`, backend);
  }
  try {
    await startAPI('initial');
    const log = await fs.open(path.join(output, 'vite.log'), 'w'); handles.push(log);
    frontend = spawn(process.execPath, [path.join(root, 'frontend/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(uiPort), '--strictPort'], {
      cwd: path.join(root, 'frontend'), env: { ...process.env, CHATTERPAL_API_TARGET: apiUrl }, stdio: ['ignore', log.fd, log.fd],
    });
    await ready(uiUrl, frontend);
    browser = await chromium.launch({
      ...(process.env.CHROME_EXECUTABLE_PATH ? { executablePath: process.env.CHROME_EXECUTABLE_PATH } : {}),
      headless: true, args: ['--disable-gpu', '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'],
    });
    const context = await browser.newContext({ permissions: ['microphone'], viewport: { width: 1280, height: 900 }, reducedMotion: 'reduce' });
    page = await context.newPage();
    page.setDefaultTimeout(20000);
    page.on('pageerror', error => errors.push(error.message));
    page.on('response', response => {
      if (response.url().includes('/api/learning/')) requests.push({ method: response.request().method(), path: new URL(response.url()).pathname, status: response.status() });
    });
    const get = async suffix => {
      const response = await context.request.get(`${uiUrl}/api/learning${suffix}`);
      assert.equal(response.status(), 200, `GET ${suffix}: ${await response.text()}`);
      return response.json();
    };
    async function settled(value) {
      const deadline = Date.now() + 20000;
      while (value.status === 'pending' && Date.now() < deadline) {
        await delay(50); value = await get(`/operations/${value.operation_id}`);
      }
      assert.equal(value.status, 'succeeded', JSON.stringify(value.error));
      operations.push(value);
      return value;
    }
    async function submit(label, suffix) {
      const responsePromise = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname.endsWith(suffix));
      await page.getByRole('button', { name: label, exact: true }).click();
      const response = await responsePromise;
      assert.equal(response.status(), 202, `${suffix}: ${await response.text()}`);
      const operation = await settled(await response.json());
      await page.locator('.learning-pending').waitFor({ state: 'detached' });
      return { operation, request: response.request() };
    }
    async function record() {
      await page.getByRole('button', { name: '开始录音', exact: true }).click();
      await page.getByRole('button', { name: '停止录音', exact: true }).waitFor();
      await delay(650);
      await page.getByRole('button', { name: '停止录音', exact: true }).click();
      await page.getByRole('button', { name: '试听本次录音', exact: true }).waitFor();
    }
    async function recognize() {
      const before = await counts();
      const result = await submit('识别并核对录音', '/transcriptions');
      assert.equal(result.operation.result_type, 'transcription');
      const receipt = result.operation.result;
      assert.equal(receipt.machine_transcript, machineText);
      assert.equal(receipt.status, 'ready');
      await page.getByRole('textbox', { name: '核对录音文字', exact: true }).waitFor();
      assert.equal(await page.getByRole('textbox', { name: '核对录音文字', exact: true }).inputValue(), machineText);
      assert.deepEqual(await counts(), { ...before, asr: before.asr + 1 }, 'Preview may only call ASR');
      return receipt;
    }
    async function confirm(text, suffix) {
      await page.getByRole('textbox', { name: '核对录音文字', exact: true }).fill(text);
      const before = await counts();
      const saved = await submit('确认文字并提交', suffix);
      assert.equal((await counts()).asr, before.asr, 'Confirmation must reuse ASR');
      assert.match(saved.request.headers()['content-type'], /application\/json/);
      assert.equal(saved.request.postDataJSON().confirmed_text, text);
      assert.ok(saved.request.postDataJSON().transcription_id);
      assert.ok(!('audio' in saved.request.postDataJSON()));
      assert.ok(!('text' in saved.request.postDataJSON()));
      return saved;
    }
    async function reopenSession() {
      await page.getByRole('button', { name: '学习记录', exact: true }).click();
      await page.getByRole('button', { name: '打开练习记录', exact: true }).first().click();
    }
    async function play(label) {
      await page.getByRole('button', { name: label, exact: true }).click();
      await page.waitForFunction(() => {
        const audio = document.querySelector('.learning-workspace audio');
        return audio && audio.readyState >= 2 && audio.currentTime > 0.05 && !audio.error;
      });
      const button = page.getByRole('button', { name: '停止学习音频', exact: true });
      if (await button.count()) await button.click();
    }
    async function privateAudio(audio) {
      const response = await context.request.get(`${uiUrl}${audio.url}`);
      assert.equal(response.status(), 200);
      const bytes = await response.body();
      assert.equal(bytes.toString('ascii', 0, 4), 'RIFF');
      return bytes;
    }
    await page.goto(uiUrl);
    const { operation: opened } = await submit('开始描述周末', '/sessions');
    const sessionId = opened.session_id;
    const initial = await get(`/sessions/${sessionId}`);
    await record();
    assert.equal((await counts()).asr, 0, 'Recording alone must not send a response');
    await page.getByRole('button', { name: '放弃本次录音', exact: true }).click();
    assert.equal((await counts()).asr, 0);
    await record();
    const first = await recognize();
    assert.deepEqual(await get(`/sessions/${sessionId}`), initial, 'Preview must not append a turn or revision');
    const firstAudio = await privateAudio(first.audio);
    await page.getByRole('textbox', { name: '核对录音文字', exact: true }).fill('Last Saturday I go hiking with my friends.');
    await reopenSession();
    assert.equal(await page.getByRole('textbox', { name: '核对录音文字', exact: true }).inputValue(), 'Last Saturday I go hiking with my friends.');
    await page.screenshot({ path: path.join(output, 'transcript-check-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 375, height: 812 });
    assert.equal(await page.locator('.app-header').count(), 1);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.evaluate(async () => {
      if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
      window.scrollTo(0, 0);
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    });
    await page.screenshot({ path: path.join(output, 'transcript-check-mobile.png'), fullPage: true });
    await page.locator('.learning-composer').scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, 'transcript-check-mobile-viewport.png') });
    await page.locator('.learning-composer').screenshot({ path: path.join(output, 'transcript-check-mobile-composer.png') });
    await fs.writeFile(path.join(output, 'mobile-layout.json'), JSON.stringify(await page.evaluate(() => ({
      headers: document.querySelectorAll('.app-header').length,
      scrollHeight: document.documentElement.scrollHeight, scrollWidth: document.documentElement.scrollWidth,
      viewport: { width: innerWidth, height: innerHeight }, scrollY,
      composer: document.querySelector('.learning-composer').getBoundingClientRect().toJSON(),
    })), null, 2));
    await page.setViewportSize({ width: 812, height: 375 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.setViewportSize({ width: 1280, height: 900 });
    await stop(backend);
    await startAPI('ready-restart');
    await page.reload();
    await page.getByRole('button', { name: '开始描述周末', exact: true }).waitFor();
    await reopenSession();
    await page.getByRole('textbox', { name: '核对录音文字', exact: true }).waitFor();
    assert.equal((await counts()).asr, 1, 'Ready draft recovery must not recognize again');
    await play('试听本次录音');
    const staleTab = await context.newPage();
    staleTab.setDefaultTimeout(20000);
    staleTab.on('pageerror', error => errors.push(error.message));
    await staleTab.goto(uiUrl);
    await staleTab.getByRole('button', { name: '继续核对任务录音', exact: true }).click();
    const localCorrection = 'This local correction must survive another tab confirming the recording.';
    await staleTab.getByRole('textbox', { name: '核对录音文字', exact: true }).fill(localCorrection);
    const firstText = 'Last Saturday I go hiking with my friends.';
    const firstSaved = await confirm(firstText, '/turns');
    const spoken = firstSaved.operation.result.turns.find(turn => turn.role === 'user');
    assert.equal(spoken.machine_transcript, machineText);
    assert.equal(spoken.confirmed_text, firstText);
    assert.equal(spoken.text, firstText);
    assert.equal(spoken.transcription_id, first.transcription_id);
    assert.equal(spoken.audio.audio_asset_id, first.audio.audio_asset_id, 'Publish should reuse the original audio asset');
    assert.deepEqual(await privateAudio(spoken.audio), firstAudio);
    assert.equal((await get(`/transcriptions/${first.transcription_id}`)).status, 'consumed');
    const replay = await context.request.post(`${uiUrl}/api/learning/sessions/${sessionId}/turns`, { data: firstSaved.request.postDataJSON() });
    assert.equal(replay.status(), 202);
    assert.equal((await replay.json()).operation_id, firstSaved.operation.operation_id);
    const reuse = await context.request.post(`${uiUrl}/api/learning/sessions/${sessionId}/turns`, { data: { ...firstSaved.request.postDataJSON(), request_id: 'browser-reuse-consumed', expected_revision: firstSaved.operation.result.revision } });
    assert.equal(reuse.status(), 409);
    assert.equal((await counts()).asr, 1);
    const consumedResponse = staleTab.waitForResponse(response => response.request().method() === 'DELETE'
      && new URL(response.url()).pathname.endsWith(`/transcriptions/${first.transcription_id}`));
    await staleTab.getByRole('button', { name: '放弃本次录音', exact: true }).click();
    assert.equal((await consumedResponse).status(), 409, 'The saved recording must remain protected');
    await staleTab.getByRole('textbox', { name: '核对录音文字', exact: true }).waitFor({ state: 'detached' });
    assert.equal(await staleTab.getByRole('textbox', { name: '我的任务回答', exact: true }).inputValue(), localCorrection);
    assert.equal(await staleTab.getByRole('textbox', { name: '我的任务回答', exact: true }).isEnabled(), true);
    await staleTab.getByRole('button', { name: '刷新本次记录', exact: true }).click();
    await staleTab.getByText(firstText, { exact: true }).first().waitFor();
    await staleTab.getByRole('textbox', { name: '我的任务回答', exact: true }).fill('A separate text draft before rerecording.');
    await staleTab.getByRole('button', { name: '开始录音', exact: true }).click();
    await staleTab.getByRole('button', { name: '停止录音', exact: true }).waitFor();
    await delay(650);
    await staleTab.getByRole('button', { name: '停止录音', exact: true }).click();
    await staleTab.getByRole('button', { name: '放弃本次录音', exact: true }).click();
    await staleTab.getByRole('button', { name: '开始录音', exact: true }).waitFor();
    await staleTab.getByRole('textbox', { name: '我的任务回答', exact: true }).fill('I was with my friends.');
    const recoveredSubmission = staleTab.waitForResponse(response => response.request().method() === 'POST'
      && new URL(response.url()).pathname.endsWith(`/sessions/${sessionId}/turns`));
    await staleTab.getByRole('button', { name: '发送任务回答', exact: true }).click();
    const recoveredResponse = await recoveredSubmission;
    assert.equal(recoveredResponse.status(), 202);
    await settled(await recoveredResponse.json());
    await staleTab.locator('.learning-pending').waitFor({ state: 'detached' });
    assert.equal((await counts()).asr, 1, 'Consumed recovery and rerecord discard must not repeat ASR');
    await staleTab.screenshot({ path: path.join(output, 'consumed-recording-recovered.png'), fullPage: true });
    await staleTab.close();
    await reopenSession();
    for (const text of ['It was relaxing because we had time to talk.']) {
      await page.getByRole('textbox', { name: '我的任务回答', exact: true }).fill(text);
      await submit('发送任务回答', '/turns');
    }
    await submit('结束对话，查看反馈', '/finish');
    const { operation: reviewSaved } = await submit('保存这个表达到复习', '/reviews');
    const reviewId = reviewSaved.result.review_item_id;
    await page.getByRole('button', { name: '针对这一句重练', exact: true }).click();
    await record();
    const retryPreview = await recognize();
    const retryText = 'I spent Saturday hiking with my friends and felt relaxed outdoors.';
    const retrySaved = await confirm(retryText, '/attempts');
    assert.equal(retrySaved.operation.result.machine_transcript, machineText);
    assert.equal(retrySaved.operation.result.transcript, retryText);
    assert.equal(retrySaved.operation.result.audio.audio_asset_id, retryPreview.audio.audio_asset_id);
    assert.equal((await counts()).asr, 2);
    await fs.writeFile(path.join(output, 'clock.txt'), '2026-10-27T08:00:00+00:00');
    await page.reload();
    await page.getByRole('button', { name: /^到期复习/ }).click();
    await page.getByRole('button', { name: '开始这项复习', exact: true }).click();
    const beforeReview = await get(`/reviews/${reviewId}`);
    await record();
    const reviewPreview = await recognize();
    assert.deepEqual(await get(`/reviews/${reviewId}`), beforeReview, 'Review preview must preserve schedule and question');
    const reviewText = 'I cooked a meal at home alone last weekend and felt peaceful.';
    const reviewAnswer = await confirm(reviewText, '/attempts');
    assert.equal(reviewAnswer.operation.result.attempt.machine_transcript, machineText);
    assert.equal(reviewAnswer.operation.result.attempt.confirmed_text, reviewText);
    assert.equal(reviewAnswer.operation.result.attempt.audio.audio_asset_id, reviewPreview.audio.audio_asset_id);
    assert.equal(reviewAnswer.operation.result.attempt.schedule_advanced, true);
    assert.equal((await counts()).asr, 3);
    await play('回放本次复习录音');
    const stranger = await browser.newContext();
    await stranger.request.post(`${uiUrl}/api/learning/bootstrap`);
    assert.equal((await stranger.request.get(`${uiUrl}/api/learning/transcriptions/${reviewPreview.transcription_id}`)).status(), 404);
    assert.equal((await stranger.request.get(`${uiUrl}${reviewAnswer.operation.result.attempt.audio.url}`)).status(), 404);
    await stranger.close();
    await page.reload();
    await page.getByRole('button', { name: '练习进度', exact: true }).click();
    const progress = await get('/progress');
    const reviewIndex = progress.evidence.findIndex(item => item.review_item_id === reviewId);
    assert.ok(reviewIndex >= 0);
    await page.getByRole('button', { name: '查看对应尝试', exact: true }).nth(reviewIndex).click();
    const history = page.getByRole('region', { name: '已保存的复习回答', exact: true });
    await history.getByText(reviewText, { exact: true }).waitFor();
    await history.getByText(machineText, { exact: true }).waitFor({ state: 'attached' });
    await page.screenshot({ path: path.join(output, 'transcript-history-desktop.png'), fullPage: true });
    assert.deepEqual(errors, []);
    const result = { passed: true, output, controlled_providers: true, teaching_api_intercepted: false, asr_calls: 3,
      preview_only_asr: true, explicit_edited_confirmation: true, three_input_contexts: true,
      ready_draft_browser_reload: true, ready_draft_api_restart: true, preview_audio_played: true,
      original_audio_asset_reused: true, duplicate_request_recovered: true, consumed_draft_rejected: true,
      consumed_draft_other_tab_recovered: true, consumed_correction_preserved: true, continued_text_and_recording: true,
      historical_provenance_restored: true, owner_isolation: true, mobile_overflow: false,
      provider_counts: await counts(), errors, requests };
    await fs.writeFile(path.join(output, 'operations.json'), JSON.stringify(operations, null, 2));
    await fs.writeFile(path.join(output, 'results.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
  } catch (error) {
    if (page) {
      await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }).catch(() => {});
      await fs.writeFile(path.join(output, 'failure.html'), await page.content()).catch(() => {});
    }
    console.error(`Transcript browser evidence: ${output}`);
    throw error;
  } finally {
    if (browser) await browser.close();
    await stop(frontend); await stop(backend);
    for (const handle of handles) await handle.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
