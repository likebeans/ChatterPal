#!/usr/bin/env node
// Exercise an already running trial over its actual HTTPS origin and providers.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { createHash, X509Certificate } = require('node:crypto');
const https = require('node:https');
const { setTimeout: delay } = require('node:timers/promises');
const root = path.resolve(__dirname, '..');
const { chromium } = require(path.join(root, 'frontend/node_modules/playwright'));
const origin = process.env.TRIAL_URL;
const output = process.env.TRIAL_EVIDENCE_DIR;
const certificate = process.env.TRIAL_CA_FILE;
const python = process.env.TRIAL_PYTHON || path.join(root, '.venv/bin/python');
const state = process.env.TRIAL_STATE_DIR;
const speech = 'Last weekend, I went hiking with two friends. I felt relaxed because the trail was quiet.';

(async () => {
  assert.ok(origin && new URL(origin).protocol === 'https:', 'Set TRIAL_URL to the running HTTPS origin');
  assert.ok(output && certificate && state, 'Set TRIAL_EVIDENCE_DIR, TRIAL_CA_FILE and TRIAL_STATE_DIR');
  await fs.mkdir(output, { recursive: true, mode: 0o700 });
  const input = path.join(output, 'synthetic-input.wav');
  execFileSync('say', ['-v', 'Samantha', '-r', '145', '-o', path.join(output, 'input.aiff'), speech]);
  execFileSync('ffmpeg', ['-nostdin', '-v', 'error', '-i', path.join(output, 'input.aiff'),
    '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', '-af', 'adelay=500|500,apad=pad_dur=0.5', input]);
  const duration = Number(JSON.parse(execFileSync('ffprobe', ['-v', 'error', '-show_entries',
    'format=duration', '-of', 'json', input], { encoding: 'utf8' })).format.duration);
  // Verify the served chain and hostname against this CA without altering system trust.
  const trusted = await new Promise((resolve, reject) => {
    const request = https.get(origin, { ca: require('node:fs').readFileSync(certificate) }, response => {
      const leaf = response.socket.getPeerCertificate(true);
      const authorized = response.socket.authorized;
      response.resume();
      response.on('end', () => resolve({ status: response.statusCode, authorized, raw: leaf.raw }));
    });
    request.setTimeout(15000, () => request.destroy(new Error('Trial HTTPS verification timed out')));
    request.on('error', reject);
  });
  assert.equal(trusted.status, 200);
  assert.equal(trusted.authorized, true);
  const der = new X509Certificate(trusted.raw).publicKey.export({ type: 'spki', format: 'der' });
  // Scope Chrome's test-only trust exception to this verified leaf public key.
  const pin = createHash('sha256').update(der).digest('base64');
  const browser = await chromium.launch({
    executablePath: process.env.CHROME_EXECUTABLE_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    headless: true,
    args: [`--ignore-certificate-errors-spki-list=${pin}`, '--use-fake-device-for-media-stream',
      '--use-fake-ui-for-media-stream', `--use-file-for-fake-audio-capture=${input}`],
  });
  const result = { passed: false, origin, synthetic_input: true, physical_microphone: false,
    physical_phone: false, teaching_api_intercepted: false, providers_replaced: false,
    injected_clock: false, timings: [], errors: [], checks: { verified_tls_chain_and_hostname: true }, screenshots: [] };
  let context;
  let page;
  let staleTab;
  async function persist() {
    await fs.writeFile(path.join(output, 'results.json'), JSON.stringify(result, null, 2), { mode: 0o600 });
  }
  try {
    context = await browser.newContext({ permissions: ['microphone'], viewport: { width: 1280, height: 900 }, reducedMotion: 'reduce' });
    await context.addInitScript(() => {
      window.__trialCapturedSeconds = 0;
      const NativeContext = window.AudioContext;
      window.AudioContext = new Proxy(NativeContext, { construct(target, args) {
        const audio = new target(...args);
        const create = audio.createScriptProcessor.bind(audio);
        audio.createScriptProcessor = (...params) => {
          const processor = create(...params);
          window.__trialCapturedSeconds = 0;
          processor.addEventListener('audioprocess', event => { window.__trialCapturedSeconds += event.inputBuffer.duration; });
          return processor;
        };
        return audio;
      } });
    });
    page = await context.newPage();
    page.setDefaultTimeout(45000);
    page.on('pageerror', error => result.errors.push(error.message));
    const api = async suffix => {
      const response = await page.evaluate(async url => {
        const response = await fetch(url, { credentials: 'same-origin' });
        return { status: response.status, data: await response.json() };
      }, `/api/learning${suffix}`);
      assert.equal(response.status, 200, `GET ${suffix}`);
      return response.data;
    };
    const waitIdle = () => page.locator('.learning-pending').waitFor({ state: 'detached', timeout: 150000 });
    async function submit(label, suffix, tag) {
      const started = Date.now();
      const [response] = await Promise.all([
        page.waitForResponse(response => response.request().method() === 'POST'
          && new URL(response.url()).pathname.endsWith(suffix)),
        page.getByRole('button', { name: label, exact: true }).click(),
      ]);
      assert.equal(response.status(), 202, tag);
      let operation = await response.json();
      const deadline = Date.now() + 150000;
      while (operation.status === 'pending' && Date.now() < deadline) {
        await delay(500);
        operation = await api(`/operations/${operation.operation_id}`);
      }
      result.timings.push({ action: tag, status: operation.status, seconds: (Date.now() - started) / 1000,
        error_code: operation.error?.code || null });
      await persist();
      assert.equal(operation.status, 'succeeded', `${tag}: ${operation.error?.code || operation.status}`);
      await waitIdle();
      return operation.result;
    }
    async function play(label, index = 0) {
      await page.getByRole('button', { name: label, exact: true }).nth(index).click();
      await page.waitForFunction(() => {
        const audio = document.querySelector('.learning-workspace audio');
        return audio && audio.currentTime > 0.1 && audio.readyState >= 2 && !audio.error;
      });
      await page.getByRole('button', { name: '停止学习音频', exact: true }).click();
    }
    await page.goto(origin, { waitUntil: 'networkidle' });
    result.checks.secure_microphone_context = await page.evaluate(() => window.isSecureContext && Boolean(navigator.mediaDevices?.getUserMedia));
    assert.equal(result.checks.secure_microphone_context, true);
    await page.getByRole('button', { name: '开始描述周末', exact: true }).waitFor();
    if (await page.getByRole('button', { name: '跳过引导', exact: true }).isVisible()) {
      await page.getByRole('button', { name: '跳过引导', exact: true }).click();
      await page.getByRole('button', { name: '跳过引导', exact: true }).waitFor({ state: 'detached' });
    }
    const cookies = await context.cookies();
    const ownerCookie = cookies.find(cookie => cookie.name === 'chatterpal_learner');
    assert.ok(ownerCookie?.secure && ownerCookie.httpOnly);
    result.checks.secure_httponly_identity = true;
    const session = await submit('开始描述周末', '/sessions', 'start-task');
    const sessionId = session.session_id;
    await page.getByRole('button', { name: '开始录音', exact: true }).click();
    await page.getByRole('button', { name: '停止录音', exact: true }).waitFor();
    await page.waitForFunction(seconds => window.__trialCapturedSeconds >= seconds, duration + 0.25);
    await page.getByRole('button', { name: '停止录音', exact: true }).click();
    await page.getByRole('button', { name: '识别并核对录音', exact: true }).waitFor();
    await play('试听本次录音');
    const preview = await submit('识别并核对录音', '/transcriptions', 'preview-asr');
    assert.equal(preview.status, 'ready');
    assert.ok(preview.machine_transcript.trim());
    result.checks.live_asr = true;
    await play('试听本次录音');
    // Restart the actual launchd API with its original database and real wall clock.
    execFileSync(python, [path.join(root, 'scripts/trial_environment.py'), '--state-dir', state, 'restart-api'], { timeout: 90000 });
    await page.reload({ waitUntil: 'networkidle' });
    await page.getByRole('button', { name: '继续核对任务录音', exact: true }).click();
    await page.getByRole('textbox', { name: '核对录音文字', exact: true }).waitFor();
    assert.equal(await page.getByRole('textbox', { name: '核对录音文字', exact: true }).inputValue(), preview.machine_transcript);
    result.checks.ready_draft_after_api_restart = true;
    await page.getByRole('textbox', { name: '核对录音文字', exact: true }).fill(speech);
    staleTab = await context.newPage();
    staleTab.setDefaultTimeout(45000);
    staleTab.on('pageerror', error => result.errors.push(error.message));
    await staleTab.goto(origin, { waitUntil: 'networkidle' });
    await staleTab.getByRole('button', { name: '继续核对任务录音', exact: true }).click();
    const localCorrection = 'I want to keep this local correction after the other tab saves the recording.';
    await staleTab.getByRole('textbox', { name: '核对录音文字', exact: true }).fill(localCorrection);
    const confirmed = await submit('确认文字并提交', '/turns', 'confirm-live-reply');
    const user = confirmed.turns.find(turn => turn.role === 'user');
    assert.equal(user.text, speech);
    assert.equal(user.transcription_id, preview.transcription_id);
    assert.equal(user.audio.audio_asset_id, preview.audio.audio_asset_id);
    const coach = confirmed.turns.filter(turn => turn.role === 'assistant').at(-1);
    assert.ok(coach?.text);
    await play('播放教练回复');
    result.checks.live_llm_tts_and_playback = true;
    result.checks.confirmed_text_and_original_audio = true;
    const [protectedRecording] = await Promise.all([
      staleTab.waitForResponse(response => response.request().method() === 'DELETE'
        && new URL(response.url()).pathname.endsWith(`/transcriptions/${preview.transcription_id}`)),
      staleTab.getByRole('button', { name: '放弃本次录音', exact: true }).click(),
    ]);
    assert.equal(protectedRecording.status(), 409, 'Saved recording must remain protected');
    await staleTab.getByRole('textbox', { name: '核对录音文字', exact: true }).waitFor({ state: 'detached' });
    assert.equal(await staleTab.getByRole('textbox', { name: '我的任务回答', exact: true }).inputValue(), localCorrection);
    assert.equal(await staleTab.getByRole('textbox', { name: '我的任务回答', exact: true }).isEnabled(), true);
    await staleTab.getByRole('button', { name: '刷新本次记录', exact: true }).click();
    await staleTab.getByText(speech, { exact: true }).first().waitFor();
    await staleTab.getByRole('button', { name: '开始录音', exact: true }).click();
    await staleTab.getByRole('button', { name: '停止录音', exact: true }).waitFor();
    await delay(650);
    await staleTab.getByRole('button', { name: '停止录音', exact: true }).click();
    await staleTab.getByRole('button', { name: '放弃本次录音', exact: true }).click();
    await staleTab.getByRole('button', { name: '开始录音', exact: true }).waitFor();
    assert.equal(await staleTab.getByRole('textbox', { name: '我的任务回答', exact: true }).inputValue(), localCorrection);
    await staleTab.screenshot({ path: path.join(output, 'trial-consumed-recording-recovered.png'), fullPage: true });
    result.screenshots.push('trial-consumed-recording-recovered.png');
    result.checks.consumed_recording_cross_tab_recovery = true;
    result.checks.local_correction_and_rerecording_preserved = true;
    await staleTab.close();
    await page.setViewportSize({ width: 375, height: 812 });
    await page.evaluate(async () => {
      if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
      window.scrollTo(0, 0);
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.equal(await page.locator('.app-header').count(), 1);
    await page.screenshot({ path: path.join(output, 'trial-mobile-recording.png'), fullPage: true });
    await page.locator('.learning-composer').scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, 'trial-mobile-composer-viewport.png') });
    result.screenshots.push('trial-mobile-recording.png', 'trial-mobile-composer-viewport.png');
    result.checks.mobile_viewport = true;
    await page.setViewportSize({ width: 1280, height: 900 });
    let completed = await submit('结束对话，查看反馈', '/finish', 'finish-feedback');
    assert.equal(completed.status, 'completed');
    if (completed.feedback?.status === 'unavailable') {
      result.initial_feedback_status = 'unavailable';
      completed = await submit('重新生成本次反馈', '/feedback', 'explicit-feedback-retry');
    }
    assert.ok(completed.feedback && ['available', 'partial'].includes(completed.feedback.status));
    assert.ok(completed.feedback.criteria.some(criterion => criterion.evidence));
    result.checks.live_feedback = true;
    let savedReview;
    if (completed.feedback.focuses.length) {
      const reviewLabel = '保存这个表达到复习';
      const buttons = page.getByRole('button', { name: reviewLabel, exact: true });
      // Each focus has its own save button; choose the first actual focus.
      const pending = page.waitForResponse(response => response.request().method() === 'POST'
        && new URL(response.url()).pathname.endsWith('/reviews'));
      await buttons.first().click();
      const response = await pending;
      assert.equal(response.status(), 202);
      let operation = await response.json();
      const deadline = Date.now() + 30000;
      while (operation.status === 'pending' && Date.now() < deadline) {
        await delay(250); operation = await api(`/operations/${operation.operation_id}`);
      }
      assert.equal(operation.status, 'succeeded');
      await waitIdle();
      savedReview = operation.result;
    } else {
      // A correct first response may have no useful correction; save an actual linked attempt.
      await page.getByRole('button', { name: '练这一句', exact: true }).first().click();
      await page.getByRole('textbox', { name: '我的新表达', exact: true }).fill('I went hiking with friends last weekend and felt relaxed.');
      await submit('提交这次练习', '/attempts', 'linked-attempt');
      savedReview = await submit('保存这次尝试到复习', '/reviews', 'save-review');
    }
    assert.ok(savedReview.review_item_id && new Date(savedReview.due_at).getTime() > Date.now() + 23 * 3600 * 1000);
    result.review_item_id = savedReview.review_item_id;
    result.review_due_at = savedReview.due_at;
    result.checks.review_saved_for_real_next_day = true;
    const beforeRestart = await api(`/sessions/${sessionId}`);
    execFileSync(python, [path.join(root, 'scripts/trial_environment.py'), '--state-dir', state, 'backup'], { timeout: 30000 });
    execFileSync(python, [path.join(root, 'scripts/trial_environment.py'), '--state-dir', state, 'restart-api'], { timeout: 90000 });
    await page.reload({ waitUntil: 'networkidle' });
    const restored = await api(`/sessions/${sessionId}`);
    assert.deepEqual(restored, beforeRestart);
    result.checks.completed_history_after_api_restart = true;
    await page.getByRole('button', { name: '学习记录', exact: true }).click();
    await page.getByRole('button', { name: '打开练习记录', exact: true }).first().click();
    await page.getByText(speech, { exact: true }).first().waitFor();
    await page.screenshot({ path: path.join(output, 'trial-desktop-history.png'), fullPage: true });
    result.screenshots.push('trial-desktop-history.png');
    await context.storageState({ path: path.join(output, 'browser-state.json') });
    await fs.chmod(path.join(output, 'browser-state.json'), 0o600);
    result.session_id = sessionId;
    result.checks.no_page_errors = result.errors.length === 0;
    assert.deepEqual(result.errors, []);
    result.passed = true;
    await persist();
    console.log(JSON.stringify(result, null, 2));
  } catch (error) {
    result.failure = error.message;
    if (page && !page.isClosed()) {
      await page.screenshot({ path: path.join(output, 'trial-failure.png'), fullPage: true }).catch(() => {});
      await fs.writeFile(path.join(output, 'failure-page.txt'), await page.locator('body').innerText().catch(() => ''), { mode: 0o600 });
    }
    await persist();
    throw error;
  } finally {
    await context?.close();
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
