#!/usr/bin/env node
// Actual UI/API/SQLite acceptance with controlled providers and an injected UTC clock.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { setTimeout: delay } = require('node:timers/promises');
const root = path.resolve(__dirname, '..');
const { chromium } = require(path.join(root, 'frontend/node_modules/playwright'));
const python = process.env.INT2_PYTHON || path.join(root, '.venv/bin/python');
const apiPort = Number(process.env.REVIEW_API_PORT || 18220);
const uiPort = Number(process.env.REVIEW_UI_PORT || 13220);
const apiUrl = `http://127.0.0.1:${apiPort}`;
const uiUrl = `http://127.0.0.1:${uiPort}`;
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
    audio.writeframes(b''.join(struct.pack('<h', int(1000 * math.sin(index * 2 * math.pi * 220 / 16000))) for index in range(32000)))
class ASR(FixtureASR):
    def recognize(self, content):
        value = super().recognize(content)
        with (output / 'asr-calls.jsonl').open('a') as stream:
            stream.write(json.dumps({'bytes': len(content), 'transcript': value}) + '\\n')
        return value
class LLM(FixtureLLM):
    def chat(self, messages):
        instruction = messages[0]['content']
        if instruction.startswith('INT2_FEEDBACK'):
            self.calls.append(messages)
            task = json.loads(instruction.rsplit('\\n', 1)[-1])['task']
            turns = json.loads(messages[-1]['content'])['learner_turns']
            first, last = turns[0], turns[-1]
            suggested = {'weekend': 'I went hiking on Saturday.', 'restaurant-order': \"I'd like one chicken sandwich, please.\", 'hotel-check-in': \"I'd like to check in. I have a reservation.\"}[task['task_id']]
            return json.dumps({'strength': {'source_turn_id': last['source_turn_id'], 'quote': last['text'], 'explanation': '这句提供了当前任务所需的信息。'}, 'focuses': [{'source_turn_id': first['source_turn_id'], 'original': first['text'], 'suggested': suggested, 'explanation': '可以用这个表达说明同一件事。', 'category': 'clarity'}], 'criteria': [{'criterion_id': criterion['criterion_id'], 'result': 'met', 'evidence': turns[min(index, len(turns) - 1)]['text']} for index, criterion in enumerate(task['criteria'])]}, ensure_ascii=False)
        return super().chat(messages)
asr, llm, tts = ASR(), LLM(), FixtureTTS(buffer.getvalue())
asr.text = 'I cooked a meal at home by myself last weekend and felt peaceful.'
api_server.create_asr = lambda settings: asr
api_server.create_llm = lambda settings: llm
api_server.create_tts = lambda settings: tts
settings = Settings(_env_file=None, learning_db_path=str(output / 'learning.sqlite3'), learning_allowed_origins=origin, learning_shutdown_timeout=1, audio_temp_dir=str(output / 'audio'))
server = api_server.APIServer(settings)
def clock():
    return datetime.fromisoformat((output / 'clock.txt').read_text().strip())
server.app.state.learning_service().clock = clock
server.app.state.learning_review_service().clock = clock
server.run(host='127.0.0.1', port=port)
`;

async function ready(url, child) {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`Service exited ${child.exitCode}: ${url}`);
    try { if ((await fetch(url)).ok) return; } catch { /* starting */ }
    await delay(100);
  }
  throw new Error(`Service did not listen: ${url}`);
}
async function stop(child) {
  if (!child || child.exitCode !== null) return;
  const exited = new Promise(resolve => child.once('exit', resolve));
  child.kill('SIGTERM');
  const force = setTimeout(() => child.kill('SIGKILL'), 15000);
  await exited;
  clearTimeout(force);
}

(async () => {
  const output = process.env.REVIEW_OUTPUT_DIR || await fs.mkdtemp(path.join(os.tmpdir(), 'chatterpal-review-browser-'));
  await fs.mkdir(output, { recursive: true });
  const setClock = value => fs.writeFile(path.join(output, 'clock.txt'), value);
  await setClock('2026-10-25T08:00:00+00:00');
  const handles = [];
  const errors = [];
  const requests = [];
  const operations = [];
  let backend, frontend, browser, page;
  async function startAPI(phase) {
    const log = await fs.open(path.join(output, `api-${phase}.log`), 'w'); handles.push(log);
    const child = spawn(python, ['-c', bootstrap, root, output, String(apiPort), uiUrl], {
      cwd: output, env: { ...process.env, GRADIO_ANALYTICS_ENABLED: 'False', HF_HUB_DISABLE_TELEMETRY: '1' },
      stdio: ['ignore', log.fd, log.fd],
    });
    backend = child;
    await ready(`${apiUrl}/health`, child);
    return child;
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
    page.setDefaultTimeout(15000);
    page.on('pageerror', error => errors.push(error.message));
    page.on('response', response => {
      if (response.url().includes('/api/learning/')) requests.push({ method: response.request().method(), path: new URL(response.url()).pathname, status: response.status() });
    });
    const get = async suffix => {
      const response = await context.request.get(`${uiUrl}/api/learning${suffix}`);
      assert.equal(response.status(), 200, `GET ${suffix}: ${await response.text()}`);
      return response.json();
    };
    const settled = async operation => {
      const deadline = Date.now() + 15000;
      while (operation.status === 'pending' && Date.now() < deadline) {
        await delay(50);
        operation = await get(`/operations/${operation.operation_id}`);
      }
      assert.equal(operation.status, 'succeeded', JSON.stringify(operation.error));
      operations.push(operation);
      return operation;
    };
    async function submit(button, suffix) {
      const responsePromise = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname.endsWith(suffix));
      await button.click();
      const response = await responsePromise;
      assert.equal(response.status(), 202, `${suffix}: ${await response.text()}`);
      const result = await settled(await response.json());
      await page.locator('.learning-pending').waitFor({ state: 'detached' });
      return result;
    }
    async function reload() {
      await page.reload();
      await page.getByRole('button', { name: '开始描述周末', exact: true }).waitFor();
      await page.getByText('正在加载学习记录…', { exact: true }).waitFor({ state: 'detached' });
    }
    async function openReview(id) {
      await reload();
      const due = await get('/reviews/due');
      const index = due.items.findIndex(item => item.review_item_id === id);
      assert.ok(index >= 0, `Review ${id} must be due`);
      await page.getByRole('button', { name: /^到期复习/ }).click();
      await page.getByRole('button', { name: '开始这项复习', exact: true }).nth(index).click();
      await page.getByRole('textbox', { name: '我的复习回答', exact: true }).waitFor();
      return due.items[index];
    }
    const savedSession = async id => get(`/sessions/${id}`);
    await page.goto(uiUrl);
    const start = await submit(page.getByRole('button', { name: '开始描述周末', exact: true }), '/sessions');
    const sessionId = start.session_id;
    const composer = page.getByRole('textbox', { name: '我的任务回答', exact: true });
    for (const text of ['I go hiking last Saturday.', 'I was with my friends.', 'It was relaxing because we had time to talk.']) {
      await composer.fill(text);
      await submit(page.getByRole('button', { name: '发送任务回答', exact: true }), '/turns');
    }
    await submit(page.getByRole('button', { name: '结束对话，查看反馈', exact: true }), '/finish');
    const firstSave = await submit(page.getByRole('button', { name: '保存这个表达到复习', exact: true }), '/reviews');
    assert.equal(firstSave.result.prompt, null);
    assert.equal(firstSave.result.question_id, null);
    assert.equal(firstSave.result.prompt_available, false);
    await page.getByRole('button', { name: '针对这一句重练', exact: true }).click();
    const secondSave = await submit(page.getByRole('button', { name: '保存这次尝试到复习', exact: true }), '/reviews');
    assert.notEqual(firstSave.result.review_item_id, secondSave.result.review_item_id);
    const beforeDue = await get('/records?kind=review');
    for (const record of beforeDue.items) {
      assert.equal(record.data.prompt, null);
      for (const key of ['target_sha256', 'target_text', 'task_snapshot', 'source_text', 'source_attempt_text']) assert.ok(!(key in record.data));
    }
    assert.equal((await get('/reviews/due')).items.length, 0);
    const source = await savedSession(sessionId);
    assert.ok(!('transfer_questions' in source.domain.task));
    await setClock('2026-10-26T08:00:00+00:00');
    const due = await get('/reviews/due');
    assert.equal(due.items.length, 2);
    const independentId = due.items[0].review_item_id;
    const assistedId = due.items[1].review_item_id;
    await openReview(independentId);
    await page.getByRole('textbox', { name: '我的复习回答', exact: true }).fill('On Saturday I took a long walk with friends and enjoyed the quiet trail.');
    const firstAnswer = await submit(page.getByRole('button', { name: '提交复习回答', exact: true }), '/attempts');
    assert.equal(firstAnswer.result.attempt.schedule_advanced, true);
    assert.equal(firstAnswer.result.next_due_at, '2026-10-29T08:00:00+00:00');
    assert.equal(firstAnswer.result.review.prompt_available, false);
    assert.equal(firstAnswer.result.review.question_id, null);
    assert.equal((await get(`/reviews/${independentId}`)).cycle, 1);

    const assisted = await openReview(assistedId);
    const reviewComposer = page.getByRole('textbox', { name: '我的复习回答', exact: true });
    const draft = 'This draft must survive an external hint and a revision conflict.';
    await reviewComposer.fill(draft);
    const external = await context.request.post(`${uiUrl}/api/learning/reviews/${assistedId}/hints`, { data: { request_id: 'browser-external-hint', expected_revision: assisted.revision, kind: 'sentence_starter' } });
    assert.equal(external.status(), 202);
    await settled(await external.json());
    const conflictResponse = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname.endsWith(`/reviews/${assistedId}/attempts`));
    await page.getByRole('button', { name: '提交复习回答', exact: true }).click();
    assert.equal((await conflictResponse).status(), 409);
    await page.getByRole('button', { name: '刷新本次记录后继续', exact: true }).click();
    await page.getByText('本轮已经使用提示，之后的回答按有提示练习记录。', { exact: true }).waitFor();
    assert.equal(await reviewComposer.inputValue(), draft);
    const reveal = await submit(page.getByRole('button', { name: '显示参考表达并记录提示', exact: true }), '/reveal');
    assert.equal(reveal.result.answer, 'I went hiking on Saturday.');
    assert.equal(await reviewComposer.inputValue(), draft);
    const deferred = await submit(page.getByRole('button', { name: '明天再复习', exact: true }), '/defer');
    assert.equal(deferred.result.due_at, '2026-10-27T08:00:00+00:00');
    assert.equal(deferred.result.used_hint, true);
    assert.equal(deferred.result.prompt_available, false);
    await stop(backend);
    await startAPI('restart');
    const restored = await get(`/reviews/${assistedId}`);
    assert.equal(restored.revealed_answer, true);
    assert.equal(restored.used_hint, true);
    assert.equal(restored.revision, deferred.result.revision);
    await setClock('2026-10-27T08:00:00+00:00');
    await openReview(assistedId);
    await page.getByRole('button', { name: '开始录音', exact: true }).click();
    await page.getByRole('button', { name: '停止录音', exact: true }).waitFor();
    await delay(800);
    await page.getByRole('button', { name: '停止录音', exact: true }).click();
    await page.getByRole('button', { name: '试听本次录音', exact: true }).waitFor();
    const recognition = await submit(page.getByRole('button', { name: '识别并核对录音', exact: true }), '/transcriptions');
    assert.equal(recognition.result_type, 'transcription');
    await page.getByRole('textbox', { name: '核对录音文字', exact: true }).waitFor();
    const audioAnswer = await submit(page.getByRole('button', { name: '确认文字并提交', exact: true }), '/attempts');
    assert.equal(audioAnswer.result.attempt.machine_transcript, recognition.result.machine_transcript);
    assert.equal(audioAnswer.result.attempt.transcription_id, recognition.result.transcription_id);
    assert.equal(audioAnswer.result.attempt.used_hint, true);
    assert.equal(audioAnswer.result.attempt.schedule_advanced, false);
    assert.equal(audioAnswer.result.attempt.evidence_kind, 'shown_answer');
    assert.equal(audioAnswer.result.attempt.transcript, 'I cooked a meal at home by myself last weekend and felt peaceful.');
    assert.equal(audioAnswer.result.next_due_at, deferred.result.due_at);
    assert.equal(audioAnswer.result.attempt.audio.state, 'available');
    const audioResponse = await context.request.get(`${uiUrl}${audioAnswer.result.attempt.audio.url}`);
    assert.equal(audioResponse.status(), 200);
    const audioBytes = await audioResponse.body();
    assert.equal(audioBytes.toString('ascii', 0, 4), 'RIFF');
    await fs.writeFile(path.join(output, 'review-recording.wav'), audioBytes);
    await page.getByRole('button', { name: '回放本次复习录音', exact: true }).click();
    await page.waitForFunction(() => {
      const audio = document.querySelector('.learning-workspace audio');
      return audio && audio.readyState >= 2 && audio.currentTime > 0.05 && !audio.error;
    });
    const stopAudio = page.getByRole('button', { name: '停止学习音频', exact: true });
    if (await stopAudio.count()) await stopAudio.click();
    const asrCalls = (await fs.readFile(path.join(output, 'asr-calls.jsonl'), 'utf8')).trim().split('\n').map(line => JSON.parse(line));
    assert.equal(asrCalls.length, 1);
    assert.ok(asrCalls[0].bytes > 44);

    await setClock('2026-10-29T08:00:00+00:00');
    await openReview(independentId);
    await page.getByRole('textbox', { name: '我的复习回答', exact: true }).fill('On a quiet Sunday I read at home by myself and enjoyed the calm.');
    const secondAnswer = await submit(page.getByRole('button', { name: '提交复习回答', exact: true }), '/attempts');
    assert.equal(secondAnswer.result.attempt.schedule_advanced, true);
    assert.equal(secondAnswer.result.next_due_at, '2026-11-05T08:00:00+00:00');
    assert.notEqual(firstAnswer.result.attempt.question_id, secondAnswer.result.attempt.question_id);
    assert.equal(secondAnswer.result.review.independent_variant_available, false);
    await page.getByRole('textbox', { name: '我的复习回答', exact: true }).fill('I spent a calm weekend at home cooking and reading with my family.');
    const repeated = await submit(page.getByRole('button', { name: '提交复习回答', exact: true }), '/attempts');
    assert.equal(repeated.result.attempt.evidence_kind, 'repeated_prompt');
    assert.equal(repeated.result.attempt.schedule_advanced, false);
    assert.equal(repeated.result.next_due_at, secondAnswer.result.next_due_at);
    await page.screenshot({ path: path.join(output, 'review-exhausted-desktop.png'), fullPage: true });
    await page.getByRole('button', { name: '练习进度', exact: true }).click();
    await page.getByRole('heading', { name: '看得见依据的练习进度', exact: true }).waitFor();
    const progress = await get('/progress');
    assert.equal(progress.review_attempts_count, 4);
    assert.equal(progress.independent_transfer_count, 2);
    assert.equal(progress.hinted_review_count, 1);
    assert.equal(progress.repeated_prompt_count, 1);
    await page.screenshot({ path: path.join(output, 'review-progress-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 375, height: 812 });
    await page.evaluate(async () => { await document.fonts.ready; await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({ path: path.join(output, 'review-progress-mobile.png'), fullPage: true });
    await page.setViewportSize({ width: 1280, height: 900 });
    await reload();
    await page.getByRole('button', { name: '练习进度', exact: true }).click();
    await page.getByRole('heading', { name: '看得见依据的练习进度', exact: true }).waitFor();
    const restoredProgress = await get('/progress');
    const historyIndex = restoredProgress.evidence.findIndex(item => item.review_item_id === assistedId);
    assert.ok(historyIndex >= 0);
    await page.getByRole('button', { name: '查看对应尝试', exact: true }).nth(historyIndex).click();
    const history = page.getByRole('region', { name: '已保存的复习回答', exact: true });
    await history.getByText(audioAnswer.result.attempt.transcript, { exact: true }).waitFor();
    await history.getByText(audioAnswer.result.attempt.prompt, { exact: true }).waitFor();
    assert.ok((await history.textContent()).includes(audioAnswer.result.attempt.evaluation.reason));
    await history.getByRole('button', { name: '回放已保存的复习录音', exact: true }).click();
    await page.waitForFunction(() => {
      const audio = document.querySelector('.learning-workspace audio');
      return audio && audio.readyState >= 2 && audio.currentTime > 0.05 && !audio.error;
    });
    const restoredAudio = await context.request.get(`${uiUrl}${audioAnswer.result.attempt.audio.url}`);
    assert.equal(restoredAudio.status(), 200);
    assert.deepEqual(await restoredAudio.body(), audioBytes);
    await page.screenshot({ path: path.join(output, 'review-history-after-reload.png'), fullPage: true });
    const scenarioResults = [];
    for (const scenario of [
      { title: '在餐厅点餐', id: 'restaurant-order', turns: ['I want one chicken sandwich, please.', 'No drink for me, thank you.', 'Could I have it without onions?'], retry: 'Could I please order a chicken sandwich without onions and no drink?' },
      { title: '办理酒店入住', id: 'hotel-check-in', turns: ['I want check in. I have a reservation.', 'The booking is under Alex Morgan for two nights.', 'What time is breakfast served?'], retry: 'I have a booking for two nights under Alex Morgan. Could I check in and ask about breakfast?' },
    ]) {
      await page.getByRole('button', { name: '今日任务', exact: true }).click();
      const created = await submit(page.getByRole('button', { name: `开始${scenario.title}`, exact: true }), '/sessions');
      for (const text of scenario.turns) {
        await page.getByRole('textbox', { name: '我的任务回答', exact: true }).fill(text);
        await submit(page.getByRole('button', { name: '发送任务回答', exact: true }), '/turns');
      }
      await submit(page.getByRole('button', { name: '结束对话，查看反馈', exact: true }), '/finish');
      await page.getByRole('button', { name: '针对这一句重练', exact: true }).click();
      await page.getByRole('textbox', { name: '我的新表达', exact: true }).fill(scenario.retry);
      await submit(page.getByRole('button', { name: '保存这次重练', exact: true }), '/attempts');
      const saved = await savedSession(created.session_id);
      assert.equal(saved.task_id, scenario.id);
      assert.equal(saved.turns.filter(turn => turn.role === 'user').length, 3);
      assert.equal(saved.feedback.criteria.length, 3);
      assert.equal(saved.feedback.status, 'available');
      assert.equal(saved.attempts.length, 2);
      assert.equal(saved.attempts[1].parent_attempt_id, saved.attempts[0].attempt_id);
      scenarioResults.push({ task_id: scenario.id, user_turns: 3, feedback: saved.feedback.status, linked_retry: true });
      await page.screenshot({ path: path.join(output, `${scenario.id}-desktop.png`), fullPage: true });
    }
    const stranger = await browser.newContext();
    assert.equal((await stranger.request.post(`${uiUrl}/api/learning/bootstrap`)).status(), 200);
    const privateRead = await stranger.request.get(`${uiUrl}/api/learning/reviews/${independentId}`);
    assert.equal(privateRead.status(), 404);
    assert.equal((await (await stranger.request.get(`${uiUrl}/api/learning/reviews/due`)).json()).items.length, 0);
    await stranger.close();
    assert.deepEqual(errors, []);
    const result = { passed: true, output, teaching_api_intercepted: false, controlled_providers: true, injected_utc_clock: true,
      before_due_prompt_hidden: true, independent_intervals_days: [3, 7], assistance_survives_restart: true,
      revision_conflict_preserves_draft: true, exhausted_variant_does_not_advance: true, asr_calls: 1,
      review_audio_played: true, historical_attempt_restored_after_reload: true, historical_audio_replayed: true,
      owner_isolation: true, mobile_overflow: false, progress, scenarios: scenarioResults, errors, requests };
    await fs.writeFile(path.join(output, 'operations.json'), JSON.stringify(operations, null, 2));
    await fs.writeFile(path.join(output, 'results.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
  } catch (error) {
    if (page) {
      await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }).catch(() => {});
      await fs.writeFile(path.join(output, 'failure.html'), await page.content()).catch(() => {});
    }
    console.error(`Review browser evidence: ${output}`);
    throw error;
  } finally {
    if (browser) await browser.close();
    await stop(frontend);
    await stop(backend);
    for (const handle of handles) await handle.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
