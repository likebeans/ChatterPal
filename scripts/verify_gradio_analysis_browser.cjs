#!/usr/bin/env node
// Actual Gradio page acceptance; live providers require GRADIO_ENV_FILE.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { spawn, execFileSync } = require('node:child_process');
const { setTimeout: delay } = require('node:timers/promises');
const root = path.resolve(__dirname, '..');
const { chromium } = require(path.join(root, 'frontend/node_modules/playwright'));
const python = process.env.GRADIO_PYTHON || path.join(root, '.venv/bin/python');
const port = Number(process.env.GRADIO_TEST_PORT || 17860);
const origin = `http://127.0.0.1:${port}`;
const transcript = 'Last weekend, I went hiking with two friends. I felt relaxed because the trail was quiet.';
const bootstrap = `
import functools, json, logging, os, sys, threading, time
from pathlib import Path
root, output, port = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
sys.path.insert(0, str(root / 'src'))
os.environ['GRADIO_ANALYTICS_ENABLED'] = 'False'
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
os.environ['GRADIO_TEMP_DIR'] = str(output / 'gradio')
from chatterpal.config.settings import Settings
live = bool(os.environ.get('GRADIO_ENV_FILE'))
settings = Settings(_env_file=os.environ.get('GRADIO_ENV_FILE') if live else None,
    audio_temp_dir=str(output / 'audio'), cache_dir=str(output / 'cache'),
    model_cache_dir=str(output / 'models'), gradio_share=False)
secrets = [value for name, value in settings.model_dump().items()
    if any(part in name.lower() for part in ('key', 'secret', 'token', 'password'))
    and isinstance(value, str) and value]
class Redact(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        for secret in secrets:
            message = message.replace(secret, '[REDACTED]')
        record.msg, record.args = message, ()
        return True
handler = logging.StreamHandler()
handler.addFilter(Redact())
logging.basicConfig(level=logging.WARNING, handlers=[handler], force=True)
from chatterpal.web import app as app_module
from chatterpal.utils.preferences import UserPreferences
import chatterpal.web.components.chat_tab as chat_module
preferences = UserPreferences(config_dir=str(output / 'preferences'))
chat_module.get_preferences_manager = lambda: preferences
if not live:
    from types import SimpleNamespace
    from chatterpal.core.asr.base import ASRBase
    class ASR(ASRBase):
        def recognize(self, audio_data):
            return ${JSON.stringify(transcript)}
        def recognize_file(self, audio_path, **kwargs):
            return self.recognize(audio_path)
        def recognize_gradio_audio(self, audio_data):
            return self.recognize(audio_data)
    asr = ASR()
    llm = SimpleNamespace(chat=lambda *args, **kwargs: json.dumps({
        'feedback': 'Check the transcript and practise the sentence again.', 'suggestions': []}))
    tts = SimpleNamespace(get_supported_voices=lambda: ['longxiaochun'],
        get_voice_info=lambda voice: {'display_name': voice})
    app_module.create_asr = lambda settings: asr
    app_module.create_llm = lambda settings: llm
    app_module.create_tts = lambda settings: tts
application = app_module.create_app(settings)
lock = threading.Lock()
def observe(instance, method, kind):
    original = getattr(instance, method)
    @functools.wraps(original)
    def call(*args, **kwargs):
        started = time.monotonic()
        result = {'kind': kind, 'provider': type(instance).__name__, 'method': method}
        try:
            value = original(*args, **kwargs)
            result['succeeded'] = True
            if kind == 'asr':
                result['transcript'] = value
            return value
        except Exception as error:
            result.update(succeeded=False, error_type=type(error).__name__)
            raise
        finally:
            result['seconds'] = round(time.monotonic() - started, 3)
            serialized = json.dumps(result, ensure_ascii=False)
            for secret in secrets:
                serialized = serialized.replace(secret, '[REDACTED]')
            with lock:
                with (output / 'provider-calls.jsonl').open('a') as stream:
                    stream.write(serialized + '\\n')
    setattr(instance, method, call)
observe(application.asr, 'recognize_gradio_audio', 'asr')
observe(application.llm, 'chat', 'llm')
(output / 'providers.json').write_text(json.dumps({
    'live': live, 'asr': type(application.asr).__name__, 'llm': type(application.llm).__name__,
    'model': (settings.openai_model if settings.llm_provider == 'openai' else settings.alibaba_model) if live else None}))
application.launch(server_name='127.0.0.1', server_port=port, share=False, inbrowser=False)
`;

async function stop(child) {
  if (!child || child.exitCode !== null) return;
  const exited = new Promise(resolve => child.once('exit', resolve));
  child.kill('SIGTERM');
  const timer = setTimeout(() => child.kill('SIGKILL'), 15000);
  await exited;
  clearTimeout(timer);
}
async function ready(child) {
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`Gradio exited ${child.exitCode}`);
    try { if ((await fetch(`${origin}/config`)).ok) return; } catch { /* Starting. */ }
    await delay(200);
  }
  throw new Error('Gradio did not become ready');
}
(async () => {
  const output = process.env.GRADIO_OUTPUT_DIR || await fs.mkdtemp(path.join(os.tmpdir(), 'chatterpal-gradio-analysis-'));
  await fs.mkdir(output, { recursive: true });
  const audio = process.env.GRADIO_INPUT_WAV || path.join(output, 'synthetic-input.wav');
  if (!process.env.GRADIO_INPUT_WAV) {
    const aiff = path.join(output, 'synthetic-input.aiff');
    execFileSync('say', ['-v', 'Samantha', '-r', '145', '-o', aiff, transcript]);
    execFileSync('ffmpeg', ['-nostdin', '-v', 'error', '-i', aiff, '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', audio]);
  }
  const result = { passed: false, output, live: Boolean(process.env.GRADIO_ENV_FILE), synthetic_input: !process.env.GRADIO_INPUT_WAV,
    browser_errors: [], failed_requests: [], checks: [] };
  let child, browser, page;
  const log = await fs.open(path.join(output, 'gradio.log'), 'w');
  try {
    child = spawn(python, ['-c', bootstrap, root, output, String(port)], {
      cwd: output, env: { ...process.env, NO_PROXY: '127.0.0.1,localhost,::1', no_proxy: '127.0.0.1,localhost,::1' },
      stdio: ['ignore', log.fd, log.fd],
    });
    await ready(child);
    browser = await chromium.launch({ headless: true,
      args: ['--disable-gpu', '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
        `--use-file-for-fake-audio-capture=${audio}`],
      ...(process.env.CHROME_EXECUTABLE_PATH ? { executablePath: process.env.CHROME_EXECUTABLE_PATH } : {}) });
    page = await browser.newPage({ viewport: { width: 1280, height: 1000 },
      permissions: ['microphone'], reducedMotion: 'reduce' });
    page.setDefaultTimeout(process.env.GRADIO_ENV_FILE ? 120000 : 20000);
    page.on('pageerror', error => result.browser_errors.push(error.message));
    page.on('requestfailed', request => {
      if (request.url().startsWith(origin)) result.failed_requests.push({ path: new URL(request.url()).pathname, error: request.failure()?.errorText });
    });
    await page.goto(origin);
    const config = await (await fetch(`${origin}/config`)).json();
    assert.ok(config.dependencies.some(event => event.api_name === '_evaluate_pronunciation'));
    assert.ok(config.dependencies.some(event => event.api_name === '_assess_pronunciation'));
    assert.deepEqual(config.components.filter(item => item.type === 'tabitem').map(item => item.props.label),
      ['💬 对话练习', '📊 发音评分', '🔧 发音纠错']);
    result.checks.push('Actual main page registers both service components and keeps the three Chinese tabs');
    await page.getByRole('tab', { name: '📊 发音评分', exact: true }).click();
    const scoreOutput = page.getByRole('textbox', { name: /评价结果/ });
    await page.getByRole('button', { name: '🎯 开始评价', exact: true }).click();
    await page.waitForFunction(() => [...document.querySelectorAll('textarea')].some(item => item.value.includes('请先输入要练习')));
    await page.getByPlaceholder('请输入您想要练习的英文句子...', { exact: true }).fill(transcript);
    await page.getByRole('button', { name: 'Upload file', exact: true }).click();
    await page.locator('[role="tabpanel"]:visible input[type="file"]').setInputFiles(audio);
    await page.getByRole('button', { name: '🎯 开始评价', exact: true }).click();
    await page.waitForFunction(() => [...document.querySelectorAll('textarea')].some(item => item.value.includes('综合评分: 暂不可评估')));
    result.score = await scoreOutput.inputValue();
    assert.ok(result.score.includes('识别文本:'));
    assert.ok(result.score.includes('识别文本一致度:'));
    assert.ok(result.score.includes('非声学发音准确率'));
    assert.ok(!result.score.includes('演示'));
    await page.screenshot({ path: path.join(output, 'score-desktop.png'), fullPage: true });
    result.checks.push('Score button validates input and produces transcript, text alignment and unavailable ability grades');
    await page.getByRole('tab', { name: '🔧 发音纠错', exact: true }).click();
    await page.getByRole('button', { name: 'Upload file', exact: true }).click();
    await page.locator('[role="tabpanel"]:visible input[type="file"]').setInputFiles(audio);
    await page.getByRole('button', { name: '开始练习分析', exact: true }).click();
    await page.waitForFunction(() => [...document.querySelectorAll('textarea')].some(item => item.value.includes('综合评分: 暂不可评估') && item.closest('[role="tabpanel"]')?.checkVisibility()));
    result.correction = {};
    for (const label of ['🔊 语音识别结果', '📋 综合评价与指导建议', '📝 单词级别分析', '🔬 音素级别分析', '💡 练习建议']) {
      result.correction[label] = await page.getByRole('textbox', { name: new RegExp(label.slice(3)) }).inputValue();
    }
    assert.ok(result.correction['🔊 语音识别结果'].trim());
    assert.ok(result.correction['📋 综合评价与指导建议'].includes('综合评分: 暂不可评估'));
    assert.ok(result.correction['🔬 音素级别分析'].includes('不可用'));
    assert.ok(!JSON.stringify(result.correction).includes('演示'));
    await page.screenshot({ path: path.join(output, 'correction-desktop.png'), fullPage: true });
    result.checks.push('Correction button accepts an empty target and renders all five service report fields');
    if (!result.live) {
      const panel = page.getByRole('tabpanel').filter({ visible: true });
      await panel.getByRole('button', { name: '清除', exact: true }).click();
      const analyze = page.getByRole('button', { name: '开始练习分析', exact: true });
      await analyze.focus();
      await page.keyboard.press('Enter');
      await page.waitForFunction(() => [...document.querySelectorAll('[role="tabpanel"] textarea')]
        .some(item => item.closest('[role="tabpanel"]')?.checkVisibility()
          && item.value.includes('请录制音频进行分析')));
      await panel.getByRole('button', { name: 'Record audio', exact: true }).click();
      await panel.getByRole('button', { name: '录制', exact: true }).click();
      await fs.writeFile(path.join(output, 'recording-accessibility.txt'), await panel.ariaSnapshot());
      const stopRecording = panel.getByRole('button', { name: /停止|Stop/i });
      await stopRecording.waitFor({ state: 'visible' });
      await delay(3500);
      const uploaded = page.waitForResponse(response => response.url().startsWith(origin)
        && new URL(response.url()).pathname === '/gradio_api/upload'
        && response.request().method() === 'POST');
      await stopRecording.click();
      assert.equal((await uploaded).status(), 200);
      await panel.getByRole('button', { name: '播放', exact: true }).waitFor();
      await analyze.focus();
      await page.keyboard.press('Enter');
      await page.waitForFunction(() => [...document.querySelectorAll('[role="tabpanel"] textarea')]
        .some(item => item.closest('[role="tabpanel"]')?.checkVisibility()
          && item.value.includes('综合评分: 暂不可评估')));
      result.checks.push('Fake microphone records through the actual audio control and keyboard Enter submits correction');
    }
    for (const report of await page.getByRole('tabpanel').filter({ visible: true }).getByRole('textbox').all()) {
      await report.evaluate(element => { element.scrollTop = 0; });
    }
    await page.setViewportSize({ width: 375, height: 812 });
    await page.evaluate(async () => {
      await document.fonts.ready;
      window.scrollTo(0, 0);
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    });
    result.mobile = await page.evaluate(() => ({ width: innerWidth,
      scrollWidth: document.documentElement.scrollWidth, height: innerHeight,
      scrollHeight: document.documentElement.scrollHeight,
      headings: [...document.querySelectorAll('h1')].map(item => item.textContent),
      page_headings: [...document.querySelectorAll('h1')]
        .filter(item => item.textContent.includes('ChatterPal')).length,
      containers: ['html', 'body', 'gradio-app', '.gradio-container'].map(selector => {
        const element = document.querySelector(selector);
        if (!element) return { selector, missing: true };
        const style = getComputedStyle(element);
        return { selector, height: element.clientHeight, scrollHeight: element.scrollHeight,
          overflowY: style.overflowY, position: style.position };
      }) }));
    assert.ok(result.mobile.scrollWidth <= result.mobile.width);
    assert.equal(result.mobile.page_headings, 1);
    await page.screenshot({ path: path.join(output, 'correction-mobile-top.png') });
    await page.getByRole('textbox', { name: /练习建议 针对性的/ }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, 'correction-mobile-bottom.png') });
    await page.setViewportSize({ width: 375, height: result.mobile.scrollHeight });
    await page.evaluate(async () => {
      window.scrollTo(0, 0);
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    });
    await page.screenshot({ path: path.join(output, 'correction-mobile.png') });
    await page.setViewportSize({ width: 812, height: 375 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    result.checks.push('375px portrait and 812px landscape have no horizontal page overflow; DOM contains one page heading');
    const events = (await fs.readFile(path.join(output, 'provider-calls.jsonl'), 'utf8')).trim().split('\n').filter(Boolean).map(JSON.parse);
    assert.equal(events.filter(item => item.kind === 'asr').length, result.live ? 2 : 3);
    assert.ok(events.filter(item => item.kind === 'asr').every(item => item.succeeded && item.transcript.trim()));
    assert.equal(events.filter(item => item.kind === 'llm').length, 1);
    assert.ok(events.filter(item => item.kind === 'llm').every(item => item.succeeded));
    result.provider_calls = events;
    result.checks.push('Exactly one recognition per submitted recording; missing-input validation does not call providers');
    assert.deepEqual(result.browser_errors, []);
    result.passed = true;
  } catch (error) {
    result.error = error.message;
    if (page) {
      await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }).catch(() => {});
      await fs.writeFile(path.join(output, 'failure-accessibility.txt'), await page.locator('body').ariaSnapshot()).catch(() => {});
    }
    process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
    await stop(child);
    await log.close();
    await fs.writeFile(path.join(output, 'results.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify({ passed: result.passed, output, error: result.error, live: result.live, checks: result.checks }));
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
