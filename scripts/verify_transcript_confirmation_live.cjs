#!/usr/bin/env node
// Explicit real-provider acceptance; synthetic speech enters Chrome's recording UI.
// No teaching API interception, provider substitution, or direct business-data writes.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const { createHash } = require('node:crypto');
const { spawn, execFileSync } = require('node:child_process');
const { setTimeout: delay } = require('node:timers/promises');
const root = path.resolve(__dirname, '..');
const { chromium } = require(path.join(root, 'frontend/node_modules/playwright'));
const python = process.env.TRANSCRIPT_PYTHON || path.join(root, '.venv/bin/python');
const envFile = process.env.TRANSCRIPT_ENV_FILE;
const apiPort = Number(process.env.TRANSCRIPT_API_PORT || 18260);
const uiPort = Number(process.env.TRANSCRIPT_UI_PORT || 13260);
const apiUrl = `http://127.0.0.1:${apiPort}`;
const uiUrl = `http://127.0.0.1:${uiPort}`;
const speechText = 'Last weekend, I went hiking with two friends. I felt relaxed because the trail was quiet.';
const initialClock = '2026-10-26T08:00:00+00:00';
const reviewClock = '2026-10-27T08:00:00+00:00';
const sha = bytes => createHash('sha256').update(bytes).digest('hex');

const bootstrap = String.raw`
import functools, hashlib, json, logging, os, re, sys, threading, time
from datetime import datetime
from pathlib import Path
from dotenv import dotenv_values
root, output, port, origin, phase, env_file = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]), sys.argv[4], sys.argv[5], sys.argv[6]
# Secrets stay in this process and never enter Node output or evidence metadata.
private = dotenv_values(env_file)
secret_pattern = re.compile(r'key|token|secret|password|credential|authorization', re.I)
secrets = sorted({str(value) for name, value in private.items() if value and secret_pattern.search(name)}, key=len, reverse=True)
def redact(value):
    value = str(value)
    for secret in secrets:
        value = value.replace(secret, '[REDACTED]')
    return value
class SafeStream:
    def __init__(self, stream):
        self.stream, self.pending = stream, ''
    def write(self, value):
        self.pending += str(value)
        while '\n' in self.pending:
            line, self.pending = self.pending.split('\n', 1)
            self.stream.write(redact(line) + '\n')
        return len(value)
    def flush(self):
        if self.pending:
            self.stream.write(redact(self.pending)); self.pending = ''
        self.stream.flush()
    def isatty(self): return False
    def fileno(self): return self.stream.fileno()
    @property
    def encoding(self): return self.stream.encoding
sys.stdout, sys.stderr = SafeStream(sys.stdout), SafeStream(sys.stderr)
sys.excepthook = lambda kind, value, trace: print('Live acceptance startup failed: ' + kind.__name__, file=sys.stderr)
class SafeFormatter(logging.Formatter):
    def format(self, record): return redact(super().format(record))
handler = logging.StreamHandler()
handler.setFormatter(SafeFormatter('%(levelname)s %(name)s %(message)s'))
logging.basicConfig(level=logging.WARNING, handlers=[handler], force=True)
sys.path.insert(0, str(root / 'src'))
os.environ['GRADIO_ANALYTICS_ENABLED'] = 'False'
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
from chatterpal.config.settings import Settings
# The given private file is the sole provider configuration source. Only an
# explicitly supplied timeout may override it; isolation settings are below.
for name in Settings.model_fields:
    if name != 'openai_timeout':
        for variant in (name, name.upper()): os.environ.pop(variant, None)
try:
    settings = Settings(_env_file=env_file, learning_db_path=str(output / 'learning.sqlite3'),
        learning_allowed_origins=origin, learning_shutdown_timeout=2,
        audio_temp_dir=str(output / 'audio'), cache_dir=str(output / 'cache'),
        model_cache_dir=str(output / 'models'), log_file=None)
except Exception as error:
    raise SystemExit('Invalid live-provider settings: ' + type(error).__name__) from None
secrets.extend(str(value) for name, value in settings.model_dump().items() if value and secret_pattern.search(name))
if settings.get_configuration_errors():
    raise SystemExit('Required provider configuration is missing')
from chatterpal.web.api_server import APIServer
server = APIServer(settings)
server.temp_dir = output
def clock(): return datetime.fromisoformat((output / 'clock.txt').read_text().strip())
server.app.state.learning_repository().clock = lambda: clock().timestamp()
server.app.state.learning_service().clock = clock
server.app.state.learning_review_service().clock = clock
lock = threading.Lock()
audit = output / 'provider-calls.jsonl'
def observe(instance, name, kind):
    original = getattr(instance, name)
    @functools.wraps(original)
    def call(*args, **kwargs):
        started = time.monotonic()
        event = {'phase': phase, 'provider': type(instance).__name__, 'method': name, 'kind': kind,
            'started_at': datetime.now().astimezone().isoformat(), 'virtual_time': clock().isoformat()}
        if kind == 'llm' and args and isinstance(args[0], list):
            first = args[0][0].get('content', '')
            event['action'] = 'feedback' if first.startswith('INT2_FEEDBACK') else 'evaluation' if first.startswith('INT2_EVALUATE_FREE_RESPONSE') else 'coaching'
        try:
            value = original(*args, **kwargs)
            event['succeeded'] = True
            if isinstance(value, bytes):
                event.update(bytes=len(value), sha256=hashlib.sha256(value).hexdigest())
            elif isinstance(value, str):
                event.update(characters=len(value), sha256=hashlib.sha256(value.encode()).hexdigest())
                if kind == 'asr': event['machine_transcript'] = value
            return value
        except Exception as error:
            event.update(succeeded=False, error_type=type(error).__name__)
            raise
        finally:
            event['seconds'] = round(time.monotonic() - started, 3)
            with lock:
                with audit.open('a') as stream: stream.write(redact(json.dumps(event, ensure_ascii=False)) + '\n')
    setattr(instance, name, call)
observe(server.asr, 'recognize', 'asr')
observe(server.llm, 'chat', 'llm')
observe(server.tts, 'synthesize', 'tts')
metadata = {'asr': {'type': type(server.asr).__name__, 'model': settings.whisper_model if settings.asr_provider == 'whisper' else 'configured-cloud-asr'},
    'llm': {'type': type(server.llm).__name__, 'model': settings.openai_model if settings.llm_provider == 'openai' else settings.alibaba_model},
    'tts': {'type': type(server.tts).__name__, 'voice': settings.edge_tts_voice if settings.tts_provider == 'edge' else settings.alibaba_tts_voice},
    'openai_timeout_seconds': settings.openai_timeout,
    'module_path': str(Path(sys.modules['chatterpal.web.api_server'].__file__).resolve())}
(output / 'providers.json').write_text(redact(json.dumps(metadata, indent=2)))
server.run(host='127.0.0.1', port=port)
`;

const databaseProbe = String.raw`
import hashlib, json, sqlite3, sys
connection = sqlite3.connect('file:' + sys.argv[1] + '?mode=ro', uri=True)
connection.row_factory = sqlite3.Row
if sys.argv[2] == 'formal':
    result = [dict(row) for row in connection.execute("SELECT record_id, kind, revision, data_json FROM records WHERE kind != 'transcription' ORDER BY sequence")]
else:
    row = connection.execute('SELECT rowid, audio_asset_id, record_id, sha256, size_bytes, content FROM audio_assets WHERE audio_asset_id=?', (sys.argv[2],)).fetchone()
    result = None if row is None else {key: row[key] for key in ('rowid', 'audio_asset_id', 'record_id', 'sha256', 'size_bytes')}
    if result is not None: result['blob_sha256'] = hashlib.sha256(bytes(row['content'])).hexdigest() if row['content'] is not None else None
print(json.dumps(result))
connection.close()
`;

async function ready(url, child) {
  const deadline = Date.now() + 60000;
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
async function portFree(port) {
  return new Promise(resolve => {
    const socket = net.connect({ host: '127.0.0.1', port });
    socket.once('connect', () => { socket.destroy(); resolve(false); });
    socket.once('error', () => resolve(true));
  });
}
function decodeAudio(file) {
  const metadata = JSON.parse(execFileSync('ffprobe', ['-v', 'error', '-show_entries',
    'format=duration,format_name:stream=codec_name,sample_rate,channels', '-of', 'json', file], { encoding: 'utf8' }));
  execFileSync('ffmpeg', ['-nostdin', '-v', 'error', '-i', file, '-f', 'null', '-']);
  assert.ok(Number(metadata.format.duration) > 0, 'Audio must be decodable and nonempty');
  return metadata;
}

(async () => {
  assert.ok(envFile, 'Set TRANSCRIPT_ENV_FILE to the existing private provider configuration');
  const output = process.env.TRANSCRIPT_OUTPUT_DIR || await fs.mkdtemp(path.join(os.tmpdir(), 'chatterpal-transcript-live-'));
  await fs.mkdir(output, { recursive: true, mode: 0o700 });
  const operations = [], requests = [], errors = [], handles = [];
  const summary = { passed: false, output, source_root: root,
    source_head: execFileSync('git', ['-C', root, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
    synthetic_input: true, input_text: speechText, physical_microphone: false, human_pronunciation: false,
    browser: 'Chrome headless fake microphone', teaching_api_intercepted: false, providers_replaced: false,
    provider_configuration_copied: false, provider_counts_measure: 'adapter method invocations, not remote HTTP retries',
    virtual_clock: { initial: initialClock, review: reviewClock, targets: ['repository', 'task_service', 'review_service'] },
    operation_recoveries: [], degraded_feedback: [], assertions: {}, entrances: {}, timings: [], screenshots: [], audio: [] };
  let backend, frontend, browser, page;
  const db = target => JSON.parse(execFileSync(python, ['-I', '-c', databaseProbe, path.join(output, 'learning.sqlite3'), target], { encoding: 'utf8' }));
  async function events() {
    const text = await fs.readFile(path.join(output, 'provider-calls.jsonl'), 'utf8');
    return text.trim() ? text.trim().split('\n').map(line => JSON.parse(line)) : [];
  }
  async function counts() {
    const values = await events();
    return Object.fromEntries(['asr', 'llm', 'tts'].map(kind => [kind, values.filter(value => value.kind === kind).length]));
  }
  async function persist() {
    await fs.writeFile(path.join(output, 'results.json'), JSON.stringify(summary, null, 2));
    await fs.writeFile(path.join(output, 'operations.json'), JSON.stringify(operations, null, 2));
    await fs.writeFile(path.join(output, 'requests.json'), JSON.stringify(requests, null, 2));
  }
  async function screenshot(name, locator = null) {
    const file = path.join(output, name);
    if (locator) await locator.screenshot({ path: file, animations: 'disabled' });
    else await page.screenshot({ path: file, fullPage: true, animations: 'disabled' });
    summary.screenshots.push(file);
    await persist();
  }
  async function startAPI(phase) {
    const log = await fs.open(path.join(output, `api-${phase}.log`), 'w'); handles.push(log);
    backend = spawn(python, ['-I', '-c', bootstrap, root, output, String(apiPort), uiUrl, phase, envFile], {
      cwd: output, env: { ...process.env, GRADIO_ANALYTICS_ENABLED: 'False', HF_HUB_DISABLE_TELEMETRY: '1' },
      stdio: ['ignore', log.fd, log.fd],
    });
    backend.on('error', error => errors.push(`API spawn: ${error.code || error.name}`));
    await ready(`${apiUrl}/health`, backend);
  }
  try {
    assert.equal(await portFree(apiPort), true, 'API port is already occupied');
    assert.equal(await portFree(uiPort), true, 'UI port is already occupied');
    await fs.writeFile(path.join(output, 'clock.txt'), initialClock);
    await fs.writeFile(path.join(output, 'provider-calls.jsonl'), '');
    const inputFile = path.join(output, 'synthetic-input.wav');
    execFileSync('say', ['-v', 'Samantha', '-r', '145', '-o', path.join(output, 'synthetic-input.aiff'), speechText]);
    execFileSync('ffmpeg', ['-nostdin', '-v', 'error', '-i', path.join(output, 'synthetic-input.aiff'),
      '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', '-af', 'adelay=500|500,apad=pad_dur=0.5', inputFile]);
    summary.synthetic_audio = { file: inputFile, sha256: sha(await fs.readFile(inputFile)), metadata: decodeAudio(inputFile) };
    await persist();
    await startAPI('initial');
    summary.providers = JSON.parse(await fs.readFile(path.join(output, 'providers.json'), 'utf8'));
    assert.equal(summary.providers.module_path.startsWith(path.join(root, 'src') + path.sep), true, 'Use this worktree, not a shared editable install');
    const log = await fs.open(path.join(output, 'vite.log'), 'w'); handles.push(log);
    frontend = spawn(process.execPath, [path.join(root, 'frontend/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(uiPort), '--strictPort'], {
      cwd: path.join(root, 'frontend'), env: { ...process.env, CHATTERPAL_API_TARGET: apiUrl }, stdio: ['ignore', log.fd, log.fd],
    });
    await ready(uiUrl, frontend);
    browser = await chromium.launch({
      ...(process.env.CHROME_EXECUTABLE_PATH ? { executablePath: process.env.CHROME_EXECUTABLE_PATH } : {}),
      headless: true, args: ['--disable-gpu', '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream', `--use-file-for-fake-audio-capture=${inputFile}`],
    });
    const context = await browser.newContext({ permissions: ['microphone'], viewport: { width: 1280, height: 900 }, reducedMotion: 'reduce' });
    await context.addInitScript(() => {
      // Chrome omits multipart bytes from Playwright's request body. Observe the
      // FormData metadata, then send the original body unchanged to the real API.
      window.__liveMultipart = [];
      const originalSend = XMLHttpRequest.prototype.send;
      XMLHttpRequest.prototype.send = function (body) {
        if (body instanceof FormData && body.has('context')) {
          const audio = body.get('audio');
          window.__liveMultipart.push({ request_id: body.get('request_id'), context: JSON.parse(String(body.get('context'))),
            audio_bytes: audio instanceof Blob ? audio.size : null });
        }
        return originalSend.call(this, body);
      };
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
    page.on('response', response => {
      if (response.url().includes('/api/learning/') && response.request().method() !== 'GET') {
        requests.push({ method: response.request().method(), path: new URL(response.url()).pathname, status: response.status() });
      }
    });
    const get = async suffix => {
      const response = await context.request.get(`${uiUrl}/api/learning${suffix}`);
      assert.equal(response.status(), 200, `GET ${suffix}`);
      return response.json();
    };
    let sessionId;
    const session = () => get(`/sessions/${sessionId}`);
    async function submit(label, suffix, tag, retryAllowed = true) {
      const before = db('formal');
      const started = Date.now();
      console.log(`Starting ${tag}`);
      const responsePromise = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname.endsWith(suffix));
      await page.getByRole('button', { name: label, exact: true }).click();
      const response = await responsePromise;
      assert.equal(response.status(), 202, `${tag}: expected operation acceptance`);
      let operation = await response.json();
      const deadline = Date.now() + 150000;
      while (operation.status === 'pending' && Date.now() < deadline) {
        await delay(400);
        operation = await get(`/operations/${operation.operation_id}`);
      }
      operations.push({ label: tag, ...operation });
      summary.timings.push({ action: tag, seconds: (Date.now() - started) / 1000, status: operation.status, error_type: operation.error?.code || null });
      await persist();
      await page.locator('.learning-pending').waitFor({ state: 'detached' });
      if (operation.status === 'failed' && operation.error?.code === 'provider_failed' && retryAllowed && suffix !== '/transcriptions') {
        assert.deepEqual(db('formal'), before, 'Failed provider work must not alter saved business records');
        const payload = response.request().postDataJSON();
        if (payload.transcription_id) {
          assert.equal((await get(`/transcriptions/${payload.transcription_id}`)).status, 'ready');
          assert.equal(await page.getByRole('textbox', { name: '核对录音文字', exact: true }).inputValue(), payload.confirmed_text);
        } else if (payload.text) {
          const field = suffix === '/turns' ? '我的任务回答' : '我的新表达';
          assert.equal(await page.getByRole('textbox', { name: field, exact: true }).inputValue(), payload.text);
        }
        summary.operation_recoveries.push({ label: tag, failed_operation_id: operation.operation_id, error: operation.error, explicit_ui_retry: true });
        const retried = await submit(label, suffix, `${tag}-explicit-retry`, false);
        assert.notEqual(retried.operation.request_id, operation.request_id, 'An explicit terminal-failure retry needs a new request_id');
        return retried;
      }
      assert.equal(operation.status, 'succeeded', `${tag}: ${operation.error?.code || operation.status}`);
      return { operation, request: response.request() };
    }
    async function record() {
      const before = await counts(), formal = db('formal');
      await page.getByRole('button', { name: '开始录音', exact: true }).click();
      await page.getByRole('button', { name: '停止录音', exact: true }).waitFor();
      await page.waitForFunction(seconds => window.__liveCapture.seconds >= seconds, Number(summary.synthetic_audio.metadata.format.duration) + 0.25);
      await page.getByRole('button', { name: '停止录音', exact: true }).click();
      await page.getByRole('button', { name: '试听本次录音', exact: true }).waitFor();
      assert.equal(await page.evaluate(() => window.__liveCapture.streams.every(stream => stream.getTracks().every(track => track.readyState === 'ended'))), true);
      assert.deepEqual(await counts(), before, 'Recording alone must not call providers');
      assert.deepEqual(db('formal'), formal, 'Recording alone must not save a formal result');
    }
    async function play(label, index = 0) {
      await page.getByRole('button', { name: label, exact: true }).nth(index).click();
      await page.waitForFunction(() => {
        const audio = document.querySelector('.learning-workspace audio');
        return audio && audio.readyState >= 2 && audio.currentTime > 0.05 && !audio.error;
      });
      const state = await page.locator('.learning-workspace audio').evaluate(audio => ({ duration: audio.duration, readyState: audio.readyState, currentTime: audio.currentTime }));
      await page.getByRole('button', { name: '停止学习音频', exact: true }).click();
      return state;
    }
    async function saveAudio(audio, name) {
      assert.equal(audio.state, 'available', `${name}: audio unavailable`);
      const response = await context.request.get(`${uiUrl}${audio.url}`);
      assert.equal(response.status(), 200);
      const bytes = await response.body();
      const file = path.join(output, name);
      await fs.writeFile(file, bytes);
      const database = db(audio.audio_asset_id);
      assert.ok(database, 'Audio must exist in SQLite');
      assert.equal(database.sha256, sha(bytes));
      assert.equal(database.blob_sha256, sha(bytes));
      assert.equal(database.size_bytes, bytes.length);
      const evidence = { file, audio_asset_id: audio.audio_asset_id, sha256: sha(bytes), bytes: bytes.length, content_type: response.headers()['content-type'], database, metadata: decodeAudio(file) };
      summary.audio.push(evidence);
      return evidence;
    }
    async function recognize(kind, tag) {
      const before = await counts(), formal = db('formal');
      const saved = await submit('识别并核对录音', '/transcriptions', tag, false);
      const receipt = saved.operation.result;
      assert.equal(saved.operation.result_type, 'transcription');
      assert.equal(receipt.status, 'ready');
      assert.equal(receipt.context.kind, kind);
      assert.match(saved.request.headers()['content-type'], /multipart\/form-data/);
      const sent = await page.evaluate(requestId => window.__liveMultipart.find(item => item.request_id === requestId), saved.operation.request_id);
      assert.ok(sent && sent.audio_bytes > 0, 'Observe the original multipart context and audio before sending unchanged');
      assert.deepEqual(receipt.context, sent.context, 'Receipt must retain the original context');
      assert.match(receipt.machine_transcript.toLowerCase(), /hiking/);
      assert.match(receipt.machine_transcript.toLowerCase(), /friends/);
      await page.getByRole('textbox', { name: '核对录音文字', exact: true }).waitFor();
      assert.equal(await page.getByRole('textbox', { name: '核对录音文字', exact: true }).inputValue(), receipt.machine_transcript);
      assert.deepEqual(await counts(), { ...before, asr: before.asr + 1 }, 'Recognition may call ASR exactly once and no LLM/TTS');
      assert.deepEqual(db('formal'), formal, 'Preview must preserve formal records, revisions, history, and schedule');
      const audio = await saveAudio(receipt.audio, `${tag}-preview.wav`);
      await fs.writeFile(path.join(output, `${tag}-receipt.json`), JSON.stringify(receipt, null, 2));
      return { receipt, audio, provider_counts_before: before, provider_counts_after: await counts() };
    }
    async function confirm(preview, text, suffix, tag) {
      await page.getByRole('textbox', { name: '核对录音文字', exact: true }).fill(text);
      assert.notEqual(text, preview.receipt.machine_transcript, 'Exercise a deliberate transcript edit');
      const before = await counts();
      const saved = await submit('确认文字并提交', suffix, tag);
      assert.equal((await counts()).asr, before.asr, 'Confirmation must reuse recognition');
      assert.match(saved.request.headers()['content-type'], /application\/json/);
      const payload = saved.request.postDataJSON();
      const { kind, session_id, review_item_id, ...contextFields } = preview.receipt.context;
      const expectedContext = kind === 'attempt' ? { session_id, ...contextFields } : contextFields;
      assert.deepEqual(payload, { ...expectedContext, transcription_id: preview.receipt.transcription_id, confirmed_text: text, request_id: saved.operation.request_id },
        'Send only transcription_id, confirmed_text, request_id and the unchanged context');
      assert.equal((await get(`/transcriptions/${preview.receipt.transcription_id}`)).status, 'consumed');
      saved.payload = payload;
      return saved;
    }
    async function assertProvenance(preview, value, text, tag) {
      assert.equal(value.machine_transcript, preview.receipt.machine_transcript);
      assert.equal(value.confirmed_text, text);
      assert.equal(value.text || value.transcript, text);
      assert.equal(value.transcription_id, preview.receipt.transcription_id);
      assert.ok(value.confirmed_at);
      assert.equal(value.audio.audio_asset_id, preview.receipt.audio.audio_asset_id);
      const audio = await saveAudio(value.audio, `${tag}-confirmed.wav`);
      assert.equal(audio.sha256, preview.audio.sha256);
      assert.equal(audio.database.rowid, preview.audio.database.rowid, 'Reuse the original SQLite BLOB row');
      assert.equal(audio.database.blob_sha256, preview.audio.database.blob_sha256);
      summary.entrances[tag] = { passed: true, machine_transcript: value.machine_transcript, confirmed_text: value.confirmed_text,
        transcription_id: value.transcription_id, context: preview.receipt.context, audio_asset_id: value.audio.audio_asset_id,
        same_blob_rowid: audio.database.rowid, sha256: audio.sha256, no_asr_on_confirmation: true,
        preview_only_one_asr: true, preview_formal_records_unchanged: true };
      await persist();
    }
    async function idempotency(saved, suffix, revisionField, revision) {
      const before = await counts(), formal = db('formal');
      const replay = await context.request.post(`${uiUrl}/api/learning${suffix}`, { data: saved.payload });
      assert.equal(replay.status(), 202);
      assert.equal((await replay.json()).operation_id, saved.operation.operation_id);
      const recovered = await get(`/requests/${saved.operation.request_id}`);
      assert.equal(recovered.operation_id, saved.operation.operation_id);
      const reuse = await context.request.post(`${uiUrl}/api/learning${suffix}`, { data: { ...saved.payload, request_id: `live-reuse-${saved.operation.request_id}`, [revisionField]: revision } });
      assert.equal(reuse.status(), 409, 'Another request_id cannot consume an already consumed transcription');
      assert.deepEqual(await counts(), before, 'Replay/reuse must not call providers');
      assert.deepEqual(db('formal'), formal);
      return { same_operation_recovered: true, another_request_rejected: 409 };
    }
    async function reopenSession() {
      await page.getByRole('button', { name: '学习记录', exact: true }).click();
      await page.getByRole('button', { name: '打开练习记录', exact: true }).first().click();
    }
    await page.goto(uiUrl);
    await page.getByRole('checkbox', { name: '将原音保留 7 天，方便之后对照', exact: true }).waitFor();
    await context.storageState({ path: path.join(output, 'browser-state.json') });
    await fs.chmod(path.join(output, 'browser-state.json'), 0o600);
    await page.getByRole('checkbox', { name: '将原音保留 7 天，方便之后对照', exact: true }).check();
    const profileResponse = page.waitForResponse(response => response.request().method() === 'PATCH' && new URL(response.url()).pathname.endsWith('/profile'));
    await page.getByRole('button', { name: '保存偏好', exact: true }).click();
    assert.equal((await profileResponse).status(), 200);
    summary.audio_retention_via_real_profile_ui = '7 days';
    const { operation: opened } = await submit('开始描述周末', '/sessions', 'start');
    sessionId = opened.session_id;
    const initial = await session();
    await record();
    await page.getByRole('button', { name: '放弃本次录音', exact: true }).click();
    assert.deepEqual(await session(), initial);
    summary.assertions.unrecognized_recording_abandoned_without_result = true;

    await record();
    const turnPreview = await recognize('turn', 'task-turn');
    assert.deepEqual(await session(), initial);
    const turnText = 'Last Saturday I go hiking with my friends.';
    await page.getByRole('textbox', { name: '核对录音文字', exact: true }).fill(turnText);
    await reopenSession();
    assert.equal(await page.getByRole('textbox', { name: '核对录音文字', exact: true }).inputValue(), turnText);
    summary.assertions.internal_navigation_preserved_edit = true;
    await screenshot('live-transcript-task-ready-desktop.png');
    await page.setViewportSize({ width: 375, height: 812 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await screenshot('live-transcript-task-ready-mobile.png', page.locator('.learning-composer'));
    await page.setViewportSize({ width: 1280, height: 900 });
    const cookieBefore = (await context.cookies()).find(cookie => cookie.name === 'chatterpal_learner');
    assert.ok(cookieBefore?.httpOnly);
    await context.storageState({ path: path.join(output, 'browser-state.json') });
    await fs.chmod(path.join(output, 'browser-state.json'), 0o600);
    const beforeRestartCounts = await counts(), beforeRestartFormal = db('formal');
    await page.reload();
    await reopenSession();
    await page.getByRole('textbox', { name: '核对录音文字', exact: true }).waitFor();
    assert.equal(await page.getByRole('textbox', { name: '核对录音文字', exact: true }).inputValue(), turnPreview.receipt.machine_transcript);
    assert.equal((await get(`/transcriptions/${turnPreview.receipt.transcription_id}`)).status, 'ready');
    summary.assertions.ready_browser_reload = true;
    await stop(backend);
    assert.equal(await portFree(apiPort), true);
    await startAPI('ready-restart');
    await page.reload();
    await reopenSession();
    await page.getByRole('textbox', { name: '核对录音文字', exact: true }).waitFor();
    assert.equal((await context.cookies()).find(cookie => cookie.name === 'chatterpal_learner').value, cookieBefore.value);
    assert.deepEqual(await counts(), beforeRestartCounts);
    assert.deepEqual(db('formal'), beforeRestartFormal);
    const restartedDraft = await get(`/transcriptions/${turnPreview.receipt.transcription_id}`);
    assert.deepEqual(restartedDraft, turnPreview.receipt);
    const restartedAudio = await saveAudio(restartedDraft.audio, 'task-ready-after-restart.wav');
    assert.equal(restartedAudio.sha256, turnPreview.audio.sha256);
    summary.assertions.ready_api_restart = true;
    summary.ready_playback = await play('试听本次录音');
    const turnSaved = await confirm(turnPreview, turnText, '/turns', 'confirm-task-turn');
    const spoken = turnSaved.operation.result.turns.find(turn => turn.role === 'user');
    await assertProvenance(turnPreview, spoken, turnText, 'task_turn');
    summary.entrances.task_turn.idempotency = await idempotency(turnSaved, `/sessions/${sessionId}/turns`, 'expected_revision', turnSaved.operation.result.revision);
    summary.coach_audio = await saveAudio(turnSaved.operation.result.turns.filter(turn => turn.role === 'assistant').at(-1).audio, 'real-coach-reply.mp3');
    summary.coach_playback = await play('播放教练回复');

    // A recognized but unconfirmed draft must also be revocable without a result.
    await record();
    const abandonedBefore = db('formal');
    const abandoned = await recognize('turn', 'abandoned-ready-turn');
    const abandonedCounts = await counts();
    const deletion = page.waitForResponse(response => response.request().method() === 'DELETE' && new URL(response.url()).pathname.endsWith(`/transcriptions/${abandoned.receipt.transcription_id}`));
    await page.getByRole('button', { name: '放弃本次录音', exact: true }).click();
    assert.equal((await deletion).status(), 204);
    await page.getByRole('button', { name: '开始录音', exact: true }).waitFor();
    assert.deepEqual(db('formal'), abandonedBefore);
    assert.deepEqual(await counts(), abandonedCounts);
    assert.equal((await context.request.get(`${uiUrl}/api/learning/transcriptions/${abandoned.receipt.transcription_id}`)).status(), 404);
    assert.equal((await context.request.get(`${uiUrl}${abandoned.receipt.audio.url}`)).status(), 404);
    summary.assertions.ready_draft_abandoned_without_result = true;
    summary.abandoned_draft = { transcription_id: abandoned.receipt.transcription_id, formal_records_unchanged: true, deleted_status: 204 };

    for (const [index, text] of ['I was with my friends.', 'It was relaxing because we had time to talk and enjoy the fresh air.'].entries()) {
      await page.getByRole('textbox', { name: '我的任务回答', exact: true }).fill(text);
      await submit('发送任务回答', '/turns', `text-turn-${index + 2}`);
    }
    await submit('结束对话，查看反馈', '/finish', 'finish-feedback');
    let completed = await session();
    summary.degraded_feedback.push({ status: completed.feedback.status, reason: completed.feedback.reason });
    if (completed.feedback.status === 'unavailable') {
      assert.equal(completed.turns.filter(turn => turn.role === 'user').length, 3);
      await submit('重新生成本次反馈', '/feedback', 'explicit-feedback-retry', false);
      completed = await session();
      summary.degraded_feedback.push({ status: completed.feedback.status, reason: completed.feedback.reason });
    }
    assert.ok(['available', 'partial'].includes(completed.feedback.status));
    assert.ok(completed.feedback.focuses.length >= 1 && completed.feedback.focuses.length <= 2);
    assert.ok(completed.feedback.criteria.every(item => item.result === 'met'));
    for (const focus of completed.feedback.focuses) {
      assert.ok(completed.turns.find(turn => turn.turn_id === focus.source_turn_id && turn.role === 'user')?.text.includes(focus.original));
    }
    const focusIndex = Math.max(0, completed.feedback.focuses.findIndex(item => item.source_turn_id === spoken.turn_id));
    const focus = completed.feedback.focuses[focusIndex];
    const focusPanel = page.locator('.learning-focus').nth(focusIndex);
    await focusPanel.getByRole('button', { name: '保存这个表达到复习', exact: true }).waitFor();
    const saveReviewResponse = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname.endsWith('/reviews'));
    await focusPanel.getByRole('button', { name: '保存这个表达到复习', exact: true }).click();
    const reviewAccepted = await saveReviewResponse;
    assert.equal(reviewAccepted.status(), 202);
    let savedReview = await reviewAccepted.json();
    const saveReviewDeadline = Date.now() + 20000;
    while (savedReview.status === 'pending' && Date.now() < saveReviewDeadline) {
      await delay(100); savedReview = await get(`/operations/${savedReview.operation_id}`);
    }
    operations.push({ label: 'save-review', ...savedReview });
    assert.equal(savedReview.status, 'succeeded');
    await page.locator('.learning-pending').waitFor({ state: 'detached' });
    const reviewId = savedReview.result.review_item_id;
    assert.equal(Date.parse(savedReview.result.due_at) - Date.parse(initialClock), 86400000);
    assert.equal((await get('/reviews/due')).items.length, 0);
    summary.feedback = completed.feedback;
    summary.review_schedule_before_due = savedReview.result;

    await focusPanel.getByRole('button', { name: '针对这一句重练', exact: true }).click();
    const beforeRetry = await session();
    const parent = beforeRetry.attempts.filter(attempt => attempt.focus_id === focus.focus_id && attempt.mode === 'free_response').at(-1);
    assert.ok(parent);
    await record();
    const retryPreview = await recognize('attempt', 'linked-retry');
    assert.deepEqual(await session(), beforeRetry);
    assert.equal(retryPreview.receipt.context.parent_attempt_id, parent.attempt_id);
    const retryText = 'I spent Saturday hiking with my friends and felt relaxed outdoors.';
    const retrySaved = await confirm(retryPreview, retryText, '/attempts', 'confirm-linked-retry');
    await assertProvenance(retryPreview, retrySaved.operation.result, retryText, 'linked_retry');
    assert.equal(retrySaved.operation.result.parent_attempt_id, parent.attempt_id);
    assert.equal(retrySaved.operation.result.evaluation.status, 'available');
    assert.equal(retrySaved.operation.result.evaluation.result, 'met');
    const afterRetry = await session();
    assert.equal(afterRetry.attempts.length, beforeRetry.attempts.length + 1);
    summary.entrances.linked_retry.idempotency = await idempotency(retrySaved, '/attempts', 'source_revision', afterRetry.revision);
    summary.retry_playback = await play('回放第 2 次练习');
    await screenshot('live-transcript-linked-retry-desktop.png');
    const beforeDayChange = db('formal');

    await fs.writeFile(path.join(output, 'clock.txt'), reviewClock);
    await page.reload();
    assert.deepEqual(db('formal'), beforeDayChange, 'Advancing the clock must not rewrite persisted history');
    // Generated coach audio has a one-hour TTL, so its public state expires.
    // Compare the live projection at the same virtual time and the stored rows.
    const sessionAtReviewTime = await session();
    const sourceRowsAtReviewTime = db('formal');
    await page.getByRole('button', { name: /^到期复习/ }).click();
    await page.getByRole('button', { name: '开始这项复习', exact: true }).click();
    const beforeReview = await get(`/reviews/${reviewId}`);
    assert.ok(beforeReview.prompt && beforeReview.question_id);
    assert.ok(Date.parse(beforeReview.due_at) <= Date.parse(reviewClock));
    await record();
    const reviewPreview = await recognize('review', 'due-review');
    assert.deepEqual(await get(`/reviews/${reviewId}`), beforeReview);
    const reviewText = 'I cooked a meal at home alone last weekend and felt peaceful.';
    const reviewAnswer = await confirm(reviewPreview, reviewText, `/reviews/${reviewId}/attempts`, 'confirm-due-review');
    const reviewAttempt = reviewAnswer.operation.result.attempt;
    await assertProvenance(reviewPreview, reviewAttempt, reviewText, 'due_review');
    assert.equal(reviewAttempt.evaluation.status, 'available');
    assert.equal(reviewAttempt.evaluation.result, 'met');
    assert.equal(reviewAttempt.schedule_advanced, true);
    assert.equal(Date.parse(reviewAnswer.operation.result.next_due_at) - Date.parse(reviewClock), 3 * 86400000);
    summary.entrances.due_review.idempotency = await idempotency(reviewAnswer, `/reviews/${reviewId}/attempts`, 'expected_revision', reviewAnswer.operation.result.review.revision);
    summary.review_playback = await play('回放本次复习录音');
    summary.review_result = reviewAnswer.operation.result;
    assert.deepEqual(await session(), sessionAtReviewTime, 'Review confirmation must not rewrite the original task history');
    const sourceRecordIds = new Set(sourceRowsAtReviewTime.filter(row => !['review', 'review_attempt'].includes(row.kind)).map(row => row.record_id));
    assert.deepEqual(db('formal').filter(row => sourceRecordIds.has(row.record_id)), sourceRowsAtReviewTime.filter(row => sourceRecordIds.has(row.record_id)), 'Review confirmation preserves persisted source records');
    const stranger = await browser.newContext();
    await stranger.request.post(`${uiUrl}/api/learning/bootstrap`);
    for (const preview of [turnPreview, retryPreview, reviewPreview]) {
      assert.equal((await stranger.request.get(`${uiUrl}/api/learning/transcriptions/${preview.receipt.transcription_id}`)).status(), 404);
      assert.equal((await stranger.request.get(`${uiUrl}${preview.receipt.audio.url}`)).status(), 404);
    }
    await stranger.close();
    summary.assertions.owner_isolation = true;
    await screenshot('live-transcript-due-review-desktop.png');

    await page.reload();
    await page.getByRole('button', { name: '练习进度', exact: true }).click();
    const progress = await get('/progress');
    const reviewIndex = progress.evidence.findIndex(item => item.review_item_id === reviewId && item.attempt_id === reviewAttempt.attempt_id);
    assert.ok(reviewIndex >= 0);
    await page.getByRole('button', { name: '查看对应尝试', exact: true }).nth(reviewIndex).click();
    const history = page.getByRole('region', { name: '已保存的复习回答', exact: true });
    await history.getByText(reviewText, { exact: true }).waitFor();
    await history.getByText(reviewPreview.receipt.machine_transcript, { exact: true }).waitFor({ state: 'attached' });
    summary.historical_review_playback = await play('回放已保存的复习录音');
    await screenshot('live-transcript-history-desktop.png');
    await reopenSession();
    assert.deepEqual(await session(), sessionAtReviewTime);
    summary.historical_task_playback = await play('回放这句原音');
    await page.locator('.learning-focus').nth(focusIndex).getByRole('button', { name: '针对这一句重练', exact: true }).click();
    await page.getByText(retryText, { exact: true }).waitFor();
    summary.historical_retry_playback = await play('回放第 2 次练习');
    const historicalAudio = await saveAudio(spoken.audio, 'task-historical-after-virtual-day.wav');
    assert.equal(historicalAudio.sha256, turnPreview.audio.sha256);
    assert.equal(historicalAudio.database.rowid, turnPreview.audio.database.rowid);
    summary.assertions.completed_history_restored = true;
    await page.setViewportSize({ width: 375, height: 812 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await screenshot('live-transcript-history-mobile.png', page.locator('.learning-practice'));
    assert.deepEqual(errors, []);
    const providerEvents = await events();
    assert.equal(providerEvents.filter(event => event.kind === 'asr').length, 4, 'Three confirmed entrances plus one explicitly discarded ready draft');
    assert.equal(providerEvents.filter(event => event.kind === 'asr' && !event.succeeded).length, 0);
    summary.assertions.three_input_contexts = true;
    summary.assertions.no_asr_on_any_confirmation = true;
    summary.assertions.original_audio_asset_and_blob_reused = true;
    summary.assertions.preview_only_asr_and_no_formal_mutation = true;
    summary.feedback_status = completed.feedback.status;
    summary.progress = progress;
    summary.passed = true;
    await fs.writeFile(path.join(output, 'session.json'), JSON.stringify(await session(), null, 2));
  } catch (error) {
    summary.failure = { type: error.name, message: error.message };
    if (page) await screenshot('failure.png').catch(() => {});
  } finally {
    if (browser) await browser.close().catch(() => {});
    await stop(frontend); await stop(backend);
    for (const handle of handles) await handle.close();
    summary.ports_released = { [apiPort]: await portFree(apiPort), [uiPort]: await portFree(uiPort) };
    if (!Object.values(summary.ports_released).every(Boolean)) summary.passed = false;
    const providerEvents = await events().catch(() => []);
    summary.provider_counts = Object.fromEntries(['asr', 'llm', 'tts'].map(kind => [kind, {
      total: providerEvents.filter(event => event.kind === kind).length,
      succeeded: providerEvents.filter(event => event.kind === kind && event.succeeded).length,
      failed: providerEvents.filter(event => event.kind === kind && !event.succeeded).length,
    }]));
    summary.provider_failures = providerEvents.filter(event => !event.succeeded);
    summary.failed_operations = operations.filter(operation => operation.status !== 'succeeded');
    summary.errors = errors;
    await persist();
    console.log(JSON.stringify({ passed: summary.passed, output, provider_counts: summary.provider_counts,
      failure: summary.failure || null, ports_released: summary.ports_released }, null, 2));
    if (!summary.passed) process.exitCode = 1;
  }
})().catch(error => { console.error(`${error.name}: ${error.message}`); process.exitCode = 1; });
