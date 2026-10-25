#!/usr/bin/env node
// Explicit live-provider acceptance. Uses configured services and synthetic speech.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { spawn, execFileSync } = require('node:child_process');
const { setTimeout: delay } = require('node:timers/promises');
const root = path.resolve(__dirname, '..');
const { chromium } = require(path.join(root, 'frontend/node_modules/playwright'));
const python = process.env.INT2_PYTHON || path.join(root, '.venv/bin/python');
const apiPort = Number(process.env.INT2_API_PORT || 18210);
const uiPort = Number(process.env.INT2_UI_PORT || 13210);
const apiUrl = `http://127.0.0.1:${apiPort}`;
const uiUrl = `http://127.0.0.1:${uiPort}`;
const speechText = 'Last weekend, I went hiking with two friends. I felt relaxed because the trail was quiet.';
const sha = bytes => createHash('sha256').update(bytes).digest('hex');

const bootstrap = `
import functools, json, logging, os, sys, threading, time
from pathlib import Path
root, output, port, origin, phase = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]), sys.argv[4], sys.argv[5]
sys.path.insert(0, str(root / 'src'))
os.environ['GRADIO_ANALYTICS_ENABLED']='False'
os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
from chatterpal.config.settings import Settings
settings=Settings(_env_file=root / '.env', learning_db_path=str(output / 'learning.sqlite3'),
    learning_allowed_origins=origin, learning_shutdown_timeout=2,
    audio_temp_dir=str(output / 'audio'), cache_dir=str(output / 'cache'), model_cache_dir=str(output / 'models'))
errors=settings.get_configuration_errors()
if errors:
    raise SystemExit('Required provider configuration is missing')
secrets=[value for value in (settings.openai_api_key, settings.get_dashscope_api_key()) if value]
class Redact(logging.Filter):
    def filter(self, record):
        message=record.getMessage()
        for secret in secrets:
            message=message.replace(secret, '[REDACTED]')
        record.msg, record.args=message, ()
        return True
handler=logging.StreamHandler()
handler.addFilter(Redact())
logging.basicConfig(level=logging.WARNING, handlers=[handler], force=True)
from chatterpal.web.api_server import APIServer
server=APIServer(settings)
server.temp_dir=output
lock=threading.Lock()
audit=output / ('provider-calls-' + phase + '.jsonl')
def observe(instance, name, kind):
    original=getattr(instance, name)
    @functools.wraps(original)
    def call(*args, **kwargs):
        started=time.monotonic()
        event={'provider':type(instance).__name__, 'method':name, 'kind':kind}
        if kind=='llm' and args and isinstance(args[0], list):
            first=args[0][0].get('content', '')
            event['action']='feedback' if first.startswith('INT2_FEEDBACK') else 'evaluation' if first.startswith('INT2_EVALUATE_FREE_RESPONSE') else 'coaching'
        try:
            value=original(*args, **kwargs)
            event.update(succeeded=True, bytes=len(value)) if isinstance(value, bytes) else event.update(succeeded=True, text=value)
            return value
        except Exception as error:
            event.update(succeeded=False, error_type=type(error).__name__)
            raise
        finally:
            event['seconds']=round(time.monotonic()-started, 3)
            serialized=json.dumps(event, ensure_ascii=False)
            for secret in secrets:
                serialized=serialized.replace(secret, '[REDACTED]')
            with lock:
                with audit.open('a') as stream:
                    stream.write(serialized+'\\n')
    setattr(instance, name, call)
# Observe timings and unchanged return values. All work still calls real providers.
observe(server.asr, 'recognize', 'asr')
observe(server.llm, 'chat', 'llm')
observe(server.tts, 'synthesize', 'tts')
metadata={'asr':type(server.asr).__name__, 'llm':type(server.llm).__name__, 'tts':type(server.tts).__name__,
    'model':settings.openai_model if settings.llm_provider=='openai' else settings.alibaba_model}
(output / 'providers.json').write_text(json.dumps(metadata, indent=2))
server.run(host='127.0.0.1', port=port)
`;

async function ready(url, child) {
  const deadline = Date.now() + 45000;
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
function decodeAudio(file) {
  const metadata = JSON.parse(execFileSync('ffprobe', ['-v', 'error', '-show_entries',
    'format=duration,format_name:stream=codec_name,sample_rate,channels', '-of', 'json', file], { encoding: 'utf8' }));
  execFileSync('ffmpeg', ['-nostdin', '-v', 'error', '-i', file, '-f', 'null', '-']);
  assert.ok(Number(metadata.format.duration) > 0);
  return metadata;
}

(async () => {
  const output = process.env.INT2_OUTPUT_DIR || await fs.mkdtemp(path.join(os.tmpdir(), 'chatterpal-int2-live-'));
  await fs.mkdir(output, { recursive: true });
  const inputFile = process.env.INT2_INPUT_WAV || path.join(output, 'synthetic-input.wav');
  if (!process.env.INT2_INPUT_WAV) {
    // A fixed synthetic learner utterance avoids recording a physical microphone.
    execFileSync('say', ['-v', 'Samantha', '-r', '145', '-o', path.join(output, 'synthetic-input.aiff'), speechText]);
    execFileSync('ffmpeg', ['-nostdin', '-v', 'error', '-i', path.join(output, 'synthetic-input.aiff'),
      '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', '-af', 'adelay=500|500,apad=pad_dur=0.5', inputFile]);
  }
  const inputMetadata = decodeAudio(inputFile);
  const duration = Number(inputMetadata.format.duration);
  const handles = [];
  let backend, frontend, browser, page;
  const errors = [];
  const operations = [];
  const timings = [];
  const summary = { passed: false, output, synthetic_input: !process.env.INT2_INPUT_WAV, input_text: speechText,
    teaching_api_intercepted: false, providers_replaced: false };
  async function startAPI(phase) {
    const log = await fs.open(path.join(output, `api-${phase}.log`), 'w'); handles.push(log);
    const child = spawn(python, ['-c', bootstrap, root, output, String(apiPort), uiUrl, phase], {
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
    frontend = spawn(process.execPath, [path.join(root, 'frontend/node_modules/vite/bin/vite.js'),
      '--host', '127.0.0.1', '--port', String(uiPort), '--strictPort'], {
      cwd: path.join(root, 'frontend'), env: { ...process.env, CHATTERPAL_API_TARGET: apiUrl },
      stdio: ['ignore', log.fd, log.fd],
    });
    await ready(uiUrl, frontend);
    browser = await chromium.launch({
      ...(process.env.CHROME_EXECUTABLE_PATH ? { executablePath: process.env.CHROME_EXECUTABLE_PATH } : {}),
      headless: true, args: ['--disable-gpu', '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream',
        `--use-file-for-fake-audio-capture=${inputFile}`],
    });
    const context = await browser.newContext({ permissions: ['microphone'], viewport: { width: 1280, height: 900 }, reducedMotion: 'reduce' });
    await context.addInitScript(() => {
      window.__liveCapture = { seconds: 0, streams: [] };
      const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      navigator.mediaDevices.getUserMedia = async (...args) => {
        const stream = await original(...args);
        window.__liveCapture.streams.push(stream);
        return stream;
      };
      const NativeContext = window.AudioContext;
      window.AudioContext = new Proxy(NativeContext, { construct(target, args) {
        const audio = new target(...args);
        const create = audio.createScriptProcessor.bind(audio);
        audio.createScriptProcessor = (...processorArgs) => {
          const processor = create(...processorArgs);
          window.__liveCapture.seconds = 0;
          processor.addEventListener('audioprocess', event => { window.__liveCapture.seconds += event.inputBuffer.duration; });
          return processor;
        };
        return audio;
      } });
    });
    page = await context.newPage();
    page.setDefaultTimeout(45000);
    page.on('pageerror', error => errors.push(error.message));
    const get = async url => {
      const response = await context.request.get(`${uiUrl}/api/learning${url}`);
      assert.equal(response.status(), 200, `GET ${url}`);
      return response.json();
    };
    const session = async () => (await get('/sessions')).items[0];
    async function submit(button, suffix, label) {
      const started = Date.now();
      console.log(`Starting ${label}`);
      const acceptedResponse = page.waitForResponse(response => response.request().method() === 'POST'
        && new URL(response.url()).pathname.endsWith(suffix));
      await button.click();
      const response = await acceptedResponse;
      assert.equal(response.status(), 202, `${label}: ${await response.text()}`);
      let operation = await response.json();
      const deadline = Date.now() + 150000;
      while (operation.status === 'pending' && Date.now() < deadline) {
        await delay(500);
        operation = await get(`/operations/${operation.operation_id}`);
      }
      operations.push({ label, ...operation });
      await fs.writeFile(path.join(output, 'operations.json'), JSON.stringify(operations, null, 2));
      timings.push({ action: label, seconds: (Date.now() - started) / 1000 });
      assert.equal(operation.status, 'succeeded', `${label}: ${JSON.stringify(operation.error)}`);
      await page.locator('.learning-pending').waitFor({ state: 'detached' });
      return operation;
    }
    async function recordSpeech() {
      await page.getByRole('button', { name: '开始录音', exact: true }).click();
      await page.getByRole('button', { name: '停止录音', exact: true }).waitFor();
      await page.waitForFunction(seconds => window.__liveCapture.seconds >= seconds, duration + 0.25);
      await page.getByRole('button', { name: '停止录音', exact: true }).click();
      await page.getByRole('button', { name: '试听本次录音', exact: true }).waitFor();
      assert.equal(await page.evaluate(() => window.__liveCapture.streams.every(stream => stream.getTracks().every(track => track.readyState === 'ended'))), true);
    }
    async function play(button) {
      await button.click();
      await page.waitForFunction(() => {
        const audio = document.querySelector('.learning-workspace audio');
        return audio && audio.readyState >= 2 && audio.currentTime > 0.05 && !audio.error;
      });
      const state = await page.locator('.learning-workspace audio').evaluate(audio => ({ duration: audio.duration, readyState: audio.readyState, currentTime: audio.currentTime }));
      const stopButton = page.getByRole('button', { name: '停止学习音频', exact: true });
      if (await stopButton.count()) await stopButton.click();
      return state;
    }
    async function saveAudio(audio, name) {
      assert.equal(audio.state, 'available', `${name} unavailable`);
      const response = await context.request.get(`${uiUrl}${audio.url}`);
      assert.equal(response.status(), 200);
      const bytes = await response.body();
      const file = path.join(output, name);
      await fs.writeFile(file, bytes);
      return { sha256: sha(bytes), bytes: bytes.length, content_type: response.headers()['content-type'], metadata: decodeAudio(file) };
    }
    await page.goto(uiUrl);
    await submit(page.getByRole('button', { name: '开始描述周末', exact: true }), '/sessions', 'start');
    const composer = page.getByRole('textbox', { name: '我的任务回答', exact: true });
    const initialText = 'Last Saturday I go hiking with my friends.';
    await composer.fill(initialText);
    await submit(page.getByRole('button', { name: '给我一个开头', exact: true }), '/hints', 'hint');
    assert.equal(await composer.inputValue(), initialText);
    await page.getByRole('button', { name: '学习记录', exact: true }).click();
    await page.getByRole('button', { name: '打开练习记录', exact: true }).click();
    assert.equal(await composer.inputValue(), initialText);
    await submit(page.getByRole('button', { name: '发送任务回答', exact: true }), '/turns', 'text-turn-1');
    await recordSpeech();
    await submit(page.getByRole('button', { name: '发送任务回答', exact: true }), '/turns', 'audio-turn-2');
    await composer.fill('It was relaxing because we could talk and enjoy the fresh air.');
    await submit(page.getByRole('button', { name: '发送任务回答', exact: true }), '/turns', 'text-turn-3');
    const active = await session();
    assert.equal(active.turns.filter(turn => turn.role === 'user').length, 3);
    const spoken = active.turns.filter(turn => turn.role === 'user')[1];
    assert.match(spoken.text.toLowerCase(), /hiking/);
    assert.match(spoken.text.toLowerCase(), /friends/);
    summary.asr_transcript = spoken.text;
    summary.original_audio = await saveAudio(spoken.audio, 'original-turn.wav');
    summary.coach_audio = await saveAudio(active.turns.filter(turn => turn.role === 'assistant').at(-1).audio, 'coach-reply.mp3');
    summary.coach_playback = await play(page.getByRole('button', { name: '播放教练回复', exact: true }).last());
    await submit(page.getByRole('button', { name: '结束对话，查看反馈', exact: true }), '/finish', 'finish-feedback');
    const completed = await session();
    assert.equal(completed.status, 'completed');
    assert.equal(completed.feedback.status, 'available', completed.feedback.reason);
    assert.ok(completed.feedback.focuses.length > 0, 'The intentional past-tense error should produce a grounded focus');
    assert.ok(completed.feedback.focuses.length <= 2);
    for (const focus of completed.feedback.focuses) {
      assert.ok(completed.turns.find(turn => turn.turn_id === focus.source_turn_id && turn.role === 'user')?.text.includes(focus.original));
    }
    assert.ok(completed.feedback.criteria.every(criterion => criterion.result === 'met'));
    const focusIndex = Math.max(0, completed.feedback.focuses.findIndex(focus => focus.source_turn_id === completed.turns.find(turn => turn.role === 'user').turn_id));
    const focus = completed.feedback.focuses[focusIndex];
    await page.locator('.learning-focus').nth(focusIndex).getByRole('button', { name: '针对这一句重练', exact: true }).click();
    await page.getByRole('textbox', { name: '我的新表达', exact: true }).fill('On Saturday, I hiked with two friends and felt relaxed in the fresh air.');
    await submit(page.getByRole('button', { name: '保存这次重练', exact: true }), '/attempts', 'text-retry-1');
    await recordSpeech();
    await submit(page.getByRole('button', { name: '保存这次重练', exact: true }), '/attempts', 'audio-retry-2');
    const before = await session();
    const chain = before.attempts.filter(attempt => attempt.focus_id === focus.focus_id && attempt.mode === 'free_response');
    assert.equal(chain.length, 3);
    assert.equal(chain[1].parent_attempt_id, chain[0].attempt_id);
    assert.equal(chain[2].parent_attempt_id, chain[1].attempt_id);
    assert.ok(chain.slice(1).every(attempt => attempt.evaluation.status === 'available' && attempt.evaluation.result === 'met'));
    summary.retry_audio = await saveAudio(chain[2].audio, 'retry-2.wav');
    const exampleOperation = await submit(page.getByRole('button', { name: '获取目标范读', exact: true }), '/example-audio', 'example-audio');
    summary.example_audio = await saveAudio(exampleOperation.result, 'target-example.mp3');
    summary.example_playback = await play(page.getByRole('button', { name: '播放目标范读', exact: true }));
    await page.screenshot({ path: path.join(output, 'live-weekend-desktop.png'), fullPage: true });
    const cookieBefore = (await context.cookies()).find(cookie => cookie.name === 'chatterpal_learner');
    assert.ok(cookieBefore?.httpOnly);
    await page.reload();
    await page.getByRole('button', { name: '学习记录', exact: true }).click();
    await page.getByRole('button', { name: '打开练习记录', exact: true }).click();
    assert.deepEqual(await session(), before);
    await stop(backend);
    await startAPI('restart');
    await page.reload();
    await page.getByRole('button', { name: '学习记录', exact: true }).click();
    await page.getByRole('button', { name: '打开练习记录', exact: true }).click();
    assert.deepEqual(await session(), before);
    assert.equal((await context.cookies()).find(cookie => cookie.name === 'chatterpal_learner').value, cookieBefore.value);
    const recovered = await saveAudio(spoken.audio, 'original-after-restart.wav');
    assert.equal(recovered.sha256, summary.original_audio.sha256);
    await page.locator('.learning-focus').nth(focusIndex).getByRole('button', { name: '针对这一句重练', exact: true }).click();
    await page.getByText('第 3 次练习', { exact: true }).waitFor();
    await page.setViewportSize({ width: 375, height: 812 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.locator('.learning-feedback').scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, 'live-weekend-feedback-mobile.png'), animations: 'disabled' });
    await page.locator('.learning-practice').scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, 'live-weekend-practice-mobile.png'), animations: 'disabled' });
    const events = (await fs.readFile(path.join(output, 'provider-calls-initial.jsonl'), 'utf8')).trim().split('\n').map(line => JSON.parse(line));
    assert.equal(events.filter(event => event.kind === 'asr').length, 2);
    assert.ok(events.every(event => event.succeeded));
    assert.deepEqual(errors, []);
    Object.assign(summary, { passed: true, user_turns: 3, linked_retries: 2, attempts: before.attempts.length,
      feedback_status: before.feedback.status, feedback: before.feedback, retry_evaluations: chain.slice(1).map(attempt => attempt.evaluation),
      browser_reload: true, api_restart: true, audio_recovered: true, hint_and_history_preserved_draft: true,
      provider_calls: { asr: events.filter(event => event.kind === 'asr').length, llm: events.filter(event => event.kind === 'llm').length, tts: events.filter(event => event.kind === 'tts').length },
      timings, errors });
    await fs.writeFile(path.join(output, 'session.json'), JSON.stringify(before, null, 2));
  } catch (error) {
    summary.failure = error.message;
    if (page) { try { await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }); } catch { /* page may have closed */ } }
    throw error;
  } finally {
    await fs.writeFile(path.join(output, 'results.json'), JSON.stringify(summary, null, 2));
    console.log(JSON.stringify(summary, null, 2));
    if (browser) await browser.close();
    await stop(frontend); await stop(backend);
    for (const handle of handles) await handle.close();
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
