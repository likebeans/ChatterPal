const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const assert = require('node:assert/strict');

async function launch() {
  return chromium.launch({
    ...(process.env.CHROME_EXECUTABLE_PATH ? { executablePath: process.env.CHROME_EXECUTABLE_PATH } : {}),
    headless: true,
    args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream', '--autoplay-policy=no-user-gesture-required'],
  });
}

async function createPage(browser, options = {}) {
  const context = await browser.newContext({
    permissions: ['microphone'],
    viewport: options.viewport || { width: 1100, height: 850 },
    reducedMotion: 'reduce',
  });
  await context.addInitScript(config => {
    window.__probe = {
      streams: [], contexts: [], processors: [], captureBatches: [], captureFrames: [], workers: [], created: new Map(), revoked: [],
      holdPermission: false, releasePermission: null, failInitialization: false,
    };
    const probe = window.__probe;
    const NativeWorker = window.Worker;
    window.Worker = new Proxy(NativeWorker, {
      construct(target, args) {
        const worker = new target(...args);
        const record = { terminated: false };
        probe.workers.push(record);
        const terminate = worker.terminate.bind(worker);
        worker.terminate = () => { record.terminated = true; terminate(); };
        return worker;
      },
    });
    const getUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = async (...args) => {
      const stream = await getUserMedia(...args);
      probe.streams.push(stream);
      if (probe.holdPermission) await new Promise(resolve => { probe.releasePermission = resolve; });
      return stream;
    };
    const NativeContext = window.AudioContext;
    window.AudioContext = new Proxy(NativeContext, {
      construct(target, args) {
        const context = new target(config.force48k ? { ...args[0], sampleRate: 48000 } : args[0]);
        probe.contexts.push(context);
        const createProcessor = context.createScriptProcessor.bind(context);
        context.createScriptProcessor = (...processorArgs) => {
          const node = createProcessor(...processorArgs);
          probe.processors.push(node);
          let batches = 0;
          const index = probe.captureBatches.push(0) - 1;
          probe.captureFrames.push(0);
          node.addEventListener('audioprocess', event => {
            probe.captureBatches[index] = ++batches;
            probe.captureFrames[index] += event.inputBuffer.length;
          });
          return node;
        };
        const createSource = context.createMediaStreamSource.bind(context);
        context.createMediaStreamSource = (...sourceArgs) => {
          if (probe.failInitialization) throw new Error('Test audio initialization failure');
          return createSource(...sourceArgs);
        };
        return context;
      },
    });
    const createObjectURL = URL.createObjectURL.bind(URL);
    URL.createObjectURL = blob => {
      const url = createObjectURL(blob);
      probe.created.set(url, blob);
      return url;
    };
    const revokeObjectURL = URL.revokeObjectURL.bind(URL);
    URL.revokeObjectURL = url => {
      probe.revoked.push(url);
      revokeObjectURL(url);
    };
  }, { force48k: Boolean(options.force48k) });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  return { context, page, errors };
}

async function readResources(page) {
  return page.evaluate(() => ({
    tracks: window.__probe.streams.flatMap(stream => stream.getTracks().map(track => track.readyState)),
    contexts: window.__probe.contexts.map(context => ({ state: context.state, sampleRate: context.sampleRate })),
    created: [...window.__probe.created.keys()],
    revoked: [...window.__probe.revoked],
  }));
}

async function assertReleased(page) {
  await page.waitForFunction(() => window.__probe.streams.every(stream => stream.getTracks().every(track => track.readyState === 'ended'))
    && window.__probe.contexts.every(context => context.state === 'closed'));
  const resources = await readResources(page);
  assert.ok(resources.tracks.every(state => state === 'ended'));
  assert.ok(resources.contexts.every(context => context.state === 'closed'));
  return resources;
}

async function keyboardActivate(locator) {
  await locator.focus();
  await locator.press('Enter');
}

async function inspectWav(page, url) {
  return page.evaluate(async value => {
    const blob = window.__probe.created.get(value);
    if (!blob) throw new Error('Audio URL was not created by the app');
    const buffer = await blob.arrayBuffer();
    const view = new DataView(buffer);
    const audio = new AudioContext();
    try {
      const decoded = await audio.decodeAudioData(buffer.slice(0));
      let maximum = 0;
      for (const sample of decoded.getChannelData(0)) maximum = Math.max(maximum, Math.abs(sample));
      return {
        type: blob.type, bytes: buffer.byteLength,
        riff: new TextDecoder().decode(buffer.slice(0, 4)),
        wave: new TextDecoder().decode(buffer.slice(8, 12)),
        encoding: view.getUint16(20, true), channels: view.getUint16(22, true),
        sampleRate: view.getUint32(24, true), bits: view.getUint16(34, true),
        dataBytes: view.getUint32(40, true),
        decodedChannels: decoded.numberOfChannels, decodedDuration: decoded.duration,
        decodedMaximum: maximum,
      };
    } finally {
      await audio.close();
    }
  }, url);
}

function assertWav(value) {
  assert.equal(value.type, 'audio/wav');
  assert.equal(value.riff, 'RIFF');
  assert.equal(value.wave, 'WAVE');
  assert.equal(value.encoding, 1);
  assert.equal(value.channels, 1);
  assert.equal(value.sampleRate, 16000);
  assert.equal(value.bits, 16);
  assert.equal(value.dataBytes, value.bytes - 44);
  assert.equal(value.decodedChannels, 1);
  assert.ok(value.decodedDuration > 0.05);
  assert.ok(value.decodedMaximum > 0);
}

module.exports = { launch, createPage, readResources, assertReleased, keyboardActivate, inspectWav, assertWav };
