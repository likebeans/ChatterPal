const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const fsSync = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { launch, createPage, readResources, assertReleased, keyboardActivate, inspectWav, assertWav } = require('./harness.cjs');

const baseUrl = process.argv[2] || process.env.BASE_URL || 'http://127.0.0.1:3012';
const outputDir = process.env.BROWSER_OUTPUT_DIR || fsSync.mkdtempSync(path.join(os.tmpdir(), 'chatterpal-b-browser-'));
fsSync.mkdirSync(outputDir, { recursive: true });
const reports = [];
let uploadCount = 0;

function wavFromUpload(request) {
  const body = request.postDataBuffer();
  const start = body.indexOf(Buffer.from('RIFF'));
  assert.ok(start >= 0, 'Uploaded file contains a real RIFF header');
  assert.equal(body.toString('ascii', start + 8, start + 12), 'WAVE');
  assert.equal(body.readUInt16LE(start + 20), 1);
  assert.equal(body.readUInt16LE(start + 22), 1);
  assert.equal(body.readUInt32LE(start + 24), 16000);
  assert.equal(body.readUInt16LE(start + 34), 16);
  const length = body.readUInt32LE(start + 40);
  assert.ok(length > 0, 'Uploaded PCM is nonempty');
  const file = path.join(outputDir, `upload-${++uploadCount}.wav`);
  fsSync.writeFileSync(file, body.subarray(start, start + 44 + length));
  return { file, bytes: length + 44, channels: 1, sampleRate: 16000, bits: 16 };
}

async function addApi(page, state) {
  await page.route('**/api/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (!url.pathname.startsWith('/api/')) return route.continue();
    let data;
    if (url.pathname.startsWith('/api/learning/')) return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Learning API not included in legacy regression fixtures' }) });
    if (url.pathname === '/api/voices') data = [{ id: 'longxiaochun', name: '测试音色' }];
    else if (url.pathname === '/api/scenarios') data = [
      { scenario_type: 'daily_conversation', difficulty_level: 'beginner', display_name: '日常会话 A' },
      { scenario_type: 'job_interview', difficulty_level: 'intermediate', display_name: '求职面试 B' },
    ];
    else if (url.pathname.startsWith('/api/scenarios/')) {
      const isA = url.pathname.includes('daily_conversation');
      if (isA && state.holdScenario) await new Promise(resolve => { state.releaseScenario = resolve; });
      data = {
        scenario_type: isA ? 'daily_conversation' : 'job_interview',
        difficulty_level: isA ? 'beginner' : 'intermediate',
        context_description: isA ? 'Scenario A context' : 'Scenario B context',
        sample_dialogues: [], key_vocabulary: [], grammar_focus: [], pronunciation_targets: [],
      };
    } else if (url.pathname === '/api/chat/audio') {
      state.uploads.push(wavFromUpload(request));
      data = { recognized_text: 'Hello recorded audio', response: 'Browser voice reply', session_id: 'browser-session', audio_url: null };
    } else if (url.pathname === '/api/chat') {
      const payload = request.postDataJSON();
      state.chatRequests.push(payload);
      if (payload.message.includes('late')) await new Promise(resolve => { state.releaseChat = resolve; });
      data = { response: payload.message.includes('late') ? 'Stale chat response' : 'Current chat response', session_id: payload.message.includes('late') ? 'stale-session' : 'current-session', audio_url: null };
    } else if (url.pathname === '/api/pronunciation/analyze') {
      state.uploads.push(wavFromUpload(request));
      if (state.failAnalysis) return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Analysis provider unavailable' }) });
      const stale = Boolean(state.holdAnalysis);
      if (stale) await new Promise(resolve => { state.releaseAnalysis = resolve; });
      data = {
        overall_score: null, recognized_text: stale ? 'Stale recognized words' : 'Actual recognized words',
        corrections: [{ type: 'grammar', original: 'go', corrected: 'went', explanation: 'Use the past tense' }], suggestions: ['Repeat the sentence slowly'],
        calibrated: false, scope: '识别文本对齐和录音声学摘要；未校准，不能判断发音能力。',
        dimensions: {
          overall: { score: null, status: 'unavailable', source: 'none', reason: '没有经过校准的综合评分方法', evidence: {}, unit: null, calibrated: false },
          accuracy: { score: 75, status: 'measured', source: 'text_sequence_alignment', reason: '文本有序词编辑一致度，差异可能来自识别错误', evidence: { edit_distance: 1 }, unit: 'percent', calibrated: false },
        },
      };
    } else if (url.pathname === '/api/pronunciation/ai-analyze') {
      state.uploads.push(wavFromUpload(request));
      data = {
        overall_score: null, recognized_text: 'AI recognized words',
        grammar_corrections: [], pronunciation_feedback: [], scenario_suggestions: [],
        personalized_tips: ['Speak steadily'], difficulty_level: null,
        next_scenario: null, confidence_score: null,
        detailed_analysis: 'Browser AI analysis contract', calibrated: false,
        scope: '语言模型仅提供文字练习建议；未校准，不代表音素准确率或能力等级。',
        dimensions: { ai_feedback: { score: null, status: 'estimated', source: 'configured_llm', reason: '练习建议，非实测错误', evidence: {}, unit: null, calibrated: false } },
      };
    } else throw new Error(`Unexpected API request: ${url.pathname}`);
    try { await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) }); }
    catch (error) { if (!/closed|handled|intercepted/i.test(error.message)) throw error; }
  });
}

async function capture(page, section) {
  const previous = await page.evaluate(() => window.__probe.captureBatches.length);
  await keyboardActivate(section.getByRole('button', { name: '开始录音', exact: true }));
  await section.getByRole('button', { name: '停止录音', exact: true }).waitFor();
  await page.waitForFunction(index => window.__probe.captureBatches[index] >= 12, previous);
  await keyboardActivate(section.getByRole('button', { name: '停止录音', exact: true }));
}

(async () => {
  const browser = await launch();
  try {
    {
      const { context, page, errors } = await createPage(browser, { force48k: true });
      const state = { uploads: [], chatRequests: [] };
      await addApi(page, state);
      await page.goto(baseUrl);
      await page.getByRole('button', { name: '对话练习', exact: true }).click();
      const chat = page.locator('.chat-interface');
      const pronunciation = page.locator('.pronunciation-correction');
      await chat.getByRole('heading', { name: '对话练习', exact: true }).waitFor();
      const desktopChatWidth = await chat.evaluate(element => element.getBoundingClientRect().width);
      const availableChatWidth = await chat.evaluate(element => element.parentElement.getBoundingClientRect().width);
      assert.ok(desktopChatWidth > 0, 'Desktop chat has a visible layout');
      assert.equal(desktopChatWidth, availableChatWidth, 'Desktop chat fills the available panel width');
      await capture(page, chat);
      await page.getByText('Browser voice reply', { exact: true }).waitFor();
      await keyboardActivate(chat.locator('.audio-play-btn').first());
      await page.waitForFunction(() => [...document.querySelectorAll('.chat-interface audio')].some(audio => audio.src.startsWith('blob:') && !audio.paused));
      const audioUrl = await chat.locator('audio[src^="blob:"]').evaluate(audio => audio.src);
      const decoded = await inspectWav(page, audioUrl);
      assertWav(decoded);
      const captured = await page.evaluate(() => ({ frames: window.__probe.captureFrames[0], sampleRate: window.__probe.contexts[0].sampleRate }));
      assert.ok(Math.abs(decoded.decodedDuration - captured.frames / captured.sampleRate) < 0.01, 'Resampling preserves capture duration');
      assert.ok((await assertReleased(page)).contexts.some(item => item.sampleRate === 48000));
      const workers = await page.evaluate(() => window.__probe.workers);
      assert.ok(workers.length > 0 && workers.every(worker => worker.terminated), 'Actual encoding Workers finish and terminate');
      await page.getByRole('button', { name: '口语纠正', exact: true }).click();
      await page.getByRole('button', { name: '对话练习', exact: true }).click();
      await page.getByText('Browser voice reply', { exact: true }).waitFor();
      const existingCount = await chat.locator('.message-bubble').count();
      await keyboardActivate(chat.getByRole('button', { name: '开始录音', exact: true }));
      await chat.getByRole('button', { name: '停止录音', exact: true }).waitFor();
      await page.getByRole('button', { name: '口语纠正', exact: true }).click();
      const resources = await assertReleased(page);
      await page.getByRole('button', { name: '对话练习', exact: true }).click();
      assert.equal(await chat.locator('.message-bubble').count(), existingCount);
      await chat.locator('.message-input').fill('late browser request');
      const pendingChat = page.waitForRequest(request => request.url().endsWith('/api/chat') && request.postDataJSON().message.includes('late'));
      await chat.getByRole('button', { name: '发送', exact: true }).click();
      await pendingChat;
      await chat.getByRole('button', { name: /清除对话/ }).click();
      state.releaseChat();
      await chat.locator('.message-input').fill('fresh browser request');
      await chat.getByRole('button', { name: '发送', exact: true }).click();
      await page.getByText('Current chat response', { exact: true }).waitFor();
      assert.equal(await page.getByText('Stale chat response', { exact: true }).count(), 0);
      assert.equal(state.chatRequests.at(-1).session_id ?? null, null);
      assert.ok((await readResources(page)).revoked.includes(audioUrl));
      await chat.locator('.message-input').fill('late retained request');
      const retainedRequest = page.waitForRequest(request => request.url().endsWith('/api/chat') && request.postDataJSON().message === 'late retained request');
      await chat.getByRole('button', { name: '发送', exact: true }).click();
      await retainedRequest;
      await page.getByRole('button', { name: '口语纠正', exact: true }).click();
      state.releaseChat();
      await page.getByRole('button', { name: '对话练习', exact: true }).click();
      await page.getByText('Stale chat response', { exact: true }).waitFor();
      await chat.locator('.message-input').fill('verify retained session');
      await chat.getByRole('button', { name: '发送', exact: true }).click();
      await page.getByText('Current chat response', { exact: true }).last().waitFor();
      assert.equal(state.chatRequests.at(-1).session_id, 'stale-session');
      await page.screenshot({ path: path.join(outputDir, 'chat.png'), fullPage: true });

      await page.getByRole('button', { name: '口语纠正', exact: true }).click();
      await pronunciation.locator('#reference-text').fill('Practice actual English words');
      await capture(page, pronunciation);
      await keyboardActivate(pronunciation.getByRole('button', { name: /播放录音/ }));
      const pronunciationUrl = await pronunciation.locator('audio').evaluate(audio => audio.src);
      const pronunciationWav = await inspectWav(page, pronunciationUrl);
      assertWav(pronunciationWav);
      await pronunciation.getByRole('button', { name: /分析语音/ }).click();
      await page.getByText('Actual recognized words', { exact: true }).waitFor();
      await page.getByText('Repeat the sentence slowly', { exact: true }).waitFor();
      assert.ok((await pronunciation.innerText()).includes('暂不可评估'));
      assert.ok((await pronunciation.innerText()).includes('文本序列匹配 75.0%'));
      assert.equal(await pronunciation.locator('.score-circle, .confidence-score, .difficulty-level').count(), 0);
      assert.ok((await pronunciation.innerText()).includes('尚未校准'));
      await page.getByRole('button', { name: '对话练习', exact: true }).click();
      await page.getByText('Current chat response', { exact: true }).last().waitFor();
      await page.getByRole('button', { name: '口语纠正', exact: true }).click();
      await page.getByText('Actual recognized words', { exact: true }).waitFor();
      await pronunciation.getByRole('button', { name: 'AI智能分析', exact: true }).click();
      state.holdScenario = true;
      const scenarioARequest = page.waitForRequest(request => request.url().includes('/api/scenarios/daily_conversation/'));
      await pronunciation.locator('#scenario-select').selectOption('daily_conversation-beginner');
      await scenarioARequest;
      await pronunciation.locator('#scenario-select').selectOption('job_interview-intermediate');
      await page.getByText('Scenario B context', { exact: true }).waitFor();
      state.releaseScenario();
      assert.equal(await page.getByText('Scenario A context', { exact: true }).count(), 0);
      await pronunciation.getByRole('button', { name: /分析语音/ }).click();
      try {
        await page.getByText('AI recognized words', { exact: true }).waitFor();
      } catch (error) {
        await page.screenshot({ path: path.join(outputDir, 'failure-ai.png'), fullPage: true });
        console.error(JSON.stringify({ outputDir, errors, analysisPage: await pronunciation.innerText() }, null, 2));
        throw error;
      }
      await page.getByText('Browser AI analysis contract', { exact: true }).waitFor();
      await page.screenshot({ path: path.join(outputDir, 'pronunciation.png'), fullPage: true });
      await pronunciation.getByRole('button', { name: /重新录制/ }).click();
      assert.ok((await readResources(page)).revoked.includes(pronunciationUrl));
      assert.equal(await page.getByText('AI recognized words', { exact: true }).count(), 0);

      await pronunciation.getByRole('button', { name: '基础分析', exact: true }).click();
      await pronunciation.locator('#reference-text').fill('New analysis recording');
      await capture(page, pronunciation);
      state.holdAnalysis = true;
      const pendingAnalysis = page.waitForRequest(request => request.url().endsWith('/api/pronunciation/analyze'));
      await pronunciation.getByRole('button', { name: /分析语音/ }).click();
      await pendingAnalysis;
      await pronunciation.getByRole('button', { name: /重新录制/ }).click();
      state.releaseAnalysis();
      state.holdAnalysis = false;
      await pronunciation.locator('#reference-text').fill('Recovery recording');
      await capture(page, pronunciation);
      state.failAnalysis = true;
      await pronunciation.getByRole('button', { name: /分析语音/ }).click();
      await pronunciation.getByRole('alert').waitFor();
      state.failAnalysis = false;
      await pronunciation.getByRole('button', { name: /分析语音/ }).click();
      await page.getByText('Actual recognized words', { exact: true }).waitFor();
      assert.equal(await page.getByText('Stale recognized words', { exact: true }).count(), 0);
      assert.deepEqual(errors, []);
      reports.push({ case: 'record, resample, play, upload, retained tabs, clear, scenario, re-record races and API contracts', decoded, pronunciationWav, uploads: state.uploads, resources, workers, errors });
      await context.close();
    }
    {
      const { context, page, errors } = await createPage(browser);
      await addApi(page, { uploads: [], chatRequests: [] });
      await page.goto(baseUrl);
      await page.getByRole('button', { name: '对话练习', exact: true }).click();
      const chat = page.locator('.chat-interface');
      await page.evaluate(() => { window.__probe.holdPermission = true; });
      await keyboardActivate(chat.getByRole('button', { name: '开始录音', exact: true }));
      await page.waitForFunction(() => typeof window.__probe.releasePermission === 'function');
      await page.getByRole('button', { name: '口语纠正', exact: true }).click();
      await page.evaluate(() => window.__probe.releasePermission());
      const afterLatePermission = await assertReleased(page);
      assert.equal(afterLatePermission.contexts.length, 0);
      await page.getByRole('button', { name: '对话练习', exact: true }).click();
      await page.evaluate(() => {
        window.__probe.holdPermission = false;
        window.__probe.failInitialization = true;
      });
      await keyboardActivate(chat.getByRole('button', { name: '开始录音', exact: true }));
      await chat.getByRole('alert').waitFor();
      const afterInitializationFailure = await assertReleased(page);
      await page.evaluate(() => { window.__probe.failInitialization = false; });
      await capture(page, chat);
      await page.getByText('Browser voice reply', { exact: true }).waitFor();
      await assertReleased(page);
      assert.deepEqual(errors, []);
      reports.push({ case: 'late permission cleanup, partial initialization failure and recovery', afterLatePermission, afterInitializationFailure, errors });
      await context.close();
    }
    {
      const { context, page, errors } = await createPage(browser, { viewport: { width: 375, height: 812 } });
      await addApi(page, { uploads: [], chatRequests: [] });
      await page.goto(baseUrl);
      await page.getByRole('button', { name: '对话练习', exact: true }).click();
      await page.getByRole('heading', { name: '对话练习', exact: true }).waitFor();
      const initialOverflow = await page.evaluate(() => ({ viewport: innerWidth, width: document.documentElement.scrollWidth }));
      assert.ok(initialOverflow.width <= initialOverflow.viewport);
      await page.getByRole('button', { name: '口语纠正', exact: true }).click();
      const correctionOverflow = await page.evaluate(() => ({ viewport: innerWidth, width: document.documentElement.scrollWidth }));
      assert.ok(correctionOverflow.width <= correctionOverflow.viewport);
      const pronunciation = page.locator('.pronunciation-correction');
      await keyboardActivate(pronunciation.getByRole('button', { name: '开始录音', exact: true }));
      await pronunciation.getByRole('button', { name: '停止录音', exact: true }).waitFor();
      await page.getByRole('button', { name: '对话练习', exact: true }).click();
      const mobileResources = await assertReleased(page);
      await page.getByRole('button', { name: '口语纠正', exact: true }).click();
      await page.screenshot({ path: path.join(outputDir, 'mobile.png'), fullPage: true });
      await page.setViewportSize({ width: 812, height: 375 });
      const landscapeOverflow = await page.evaluate(() => ({ viewport: innerWidth, width: document.documentElement.scrollWidth }));
      assert.ok(landscapeOverflow.width <= landscapeOverflow.viewport);
      assert.deepEqual(errors, []);
      reports.push({ case: '375px and landscape layout, reduced motion, pronunciation recording tab cleanup', initialOverflow, correctionOverflow, landscapeOverflow, mobileResources, errors });
      await context.close();
    }
    {
      const { context, page, errors } = await createPage(browser, { force48k: true });
      await addApi(page, { uploads: [], chatRequests: [] });
      await page.goto(baseUrl);
      await page.getByRole('button', { name: '对话练习', exact: true }).click();
      await page.getByRole('button', { name: '口语纠正', exact: true }).click();
      const pronunciation = page.locator('.pronunciation-correction');
      await keyboardActivate(pronunciation.getByRole('button', { name: '开始录音', exact: true }));
      await pronunciation.getByRole('button', { name: '停止录音', exact: true }).waitFor();
      // Inject a paragraph's worth of PCM at the capture boundary so the actual
      // worker/UI path is exercised without waiting a full minute in real time.
      await page.evaluate(() => {
        const rate = 48000;
        const samples = Float32Array.from({ length: rate * 60 }, (_, index) => 0.5 * Math.sin(2 * Math.PI * 440 * index / rate));
        window.__probe.processors.at(-1).onaudioprocess({ inputBuffer: { length: samples.length, numberOfChannels: 1, getChannelData: () => samples } });
      });
      const stopTime = await pronunciation.getByRole('button', { name: '停止录音', exact: true }).evaluate(button => {
        const start = performance.now();
        button.click();
        return performance.now() - start;
      });
      assert.ok(stopTime < 250, `Stopping capture should yield promptly, took ${stopTime}ms`);
      await pronunciation.getByRole('button', { name: '取消音频处理', exact: true }).waitFor();
      await page.getByRole('button', { name: '对话练习', exact: true }).click();
      await assertReleased(page);
      await page.waitForFunction(() => window.__probe.workers.length > 0 && window.__probe.workers.every(worker => worker.terminated));
      await page.getByRole('button', { name: '口语纠正', exact: true }).click();
      assert.equal(await pronunciation.getByRole('button', { name: '播放录音', exact: true }).count(), 0);
      assert.deepEqual(errors, []);
      reports.push({ case: '60-second PCM conversion keeps UI responsive and terminates on tab change', stopTimeMs: stopTime, workers: await page.evaluate(() => window.__probe.workers), errors });
      await context.close();
    }
    await fs.writeFile(path.join(outputDir, 'results.json'), JSON.stringify(reports, null, 2));
    console.log(JSON.stringify({ outputDir, reports }, null, 2));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
