// Offline INT2 HTTP contract responses. Never imported by the app.
const task = {
  task_id: 'weekend', content_version: '1', title: '描述周末', description: '说清做了什么、和谁一起、感受如何。', estimated_minutes: 8,
  learner_role: '分享周末的朋友', coach_role: '朋友', objectives: ['描述一次周末活动', '说明和谁一起', '表达感受'],
  examples: ['I went hiking with some friends.', 'It was really enjoyable.'], target_expressions: ['I went hiking.'],
  criteria: [{ criterion_id: 'activity', description: '描述一次活动' }],
};
const feedback = { feedback_id: 'feedback-1', source_revision: 2, status: 'available', reason: '依据文字表达提供建议，尚未评估音素发音。', strength: '你清楚表达了活动内容。', focuses: [
  { focus_id: 'past-tense', source_turn_id: 'turn-1', original: 'I go hiking yesterday.', suggested: 'I went hiking yesterday.', explanation: '昨天发生的活动使用过去时。', category: 'grammar', source: 'text' },
], criteria: [{ criterion_id: 'activity', description: '描述一次活动', result: 'met', evidence: 'I go hiking yesterday.' }] };
const initialSession = () => ({ session_id: 'session-1', task_id: task.task_id, task, revision: 1, status: 'active', correction_mode: 'after_session', turns: [
  { turn_id: 'coach-0', role: 'assistant', text: 'What did you do over the weekend?', audio: null },
], feedback: null, attempts: [], created_at: '2026-10-23T08:00:00Z', updated_at: '2026-10-23T08:00:00Z' });
function fixtureState() { return { session: null, operations: new Map(), requests: new Map(), requestIds: [], holdTurn: false, releaseTurn: null }; }
async function installFixture(page, state) {
  await page.route('**/api/**', async route => {
    const req = route.request(); const pathname = new URL(req.url()).pathname;
    if (!pathname.startsWith('/api/')) return route.continue();
    let value;
    if (pathname === '/api/voices') value = [];
    else if (pathname === '/api/scenarios') value = [];
    else if (pathname === '/api/learning/bootstrap') value = { learner_id: 'learner-1', profile: { goal: 'daily', daily_minutes: 8, correction_mode: 'after_session', retain_audio: false, onboarding_completed: true }, pending_operations: [...state.operations.values()].filter(op => op.status === 'pending') };
    else if (pathname === '/api/learning/tasks') value = [task];
    else if (pathname === '/api/learning/records') value = { items: state.session ? [{ record_id: 'record-1', kind: 'session', session_id: 'session-1', data: state.session, revision: state.session.revision, updated_at: state.session.updated_at }] : [], next_cursor: null };
    else if (pathname === '/api/learning/reviews/due') return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: { code: 'review_unavailable', message: '复习功能尚未接入。' } }) });
    else if (pathname === '/api/learning/progress') value = { sessions_completed: state.session?.status === 'completed' ? 1 : 0, attempts_count: state.session?.attempts.length || 0, retries_count: state.session?.attempts.filter(item => item.parent_attempt_id).length || 0, reviews_completed: 0, independent_transfer_count: 0, evidence: [] };
    else if (pathname.startsWith('/api/learning/operations/')) value = state.operations.get(pathname.split('/').pop());
    else if (pathname.startsWith('/api/learning/requests/')) value = state.requests.get(pathname.split('/').pop());
    else if (req.method() === 'POST') {
      const multipart = (req.headers()['content-type'] || '').includes('multipart');
      const payload = multipart ? Object.fromEntries([...req.postDataBuffer().toString().matchAll(/name="(\w+)"\r\n\r\n([^\r]*)/g)].map(match => [match[1], match[2]])) : req.postDataJSON();
      const key = payload.request_id;
      if (multipart) {
        const bytes = req.postDataBuffer(); const offset = bytes.indexOf(Buffer.from('RIFF'));
        if (offset < 0 || bytes.readUInt32LE(offset + 24) !== 16000 || bytes.readUInt16LE(offset + 22) !== 1 || bytes.readUInt16LE(offset + 34) !== 16) throw new Error('Learning upload is not a mono PCM16 16kHz WAV');
      }
      if (state.requests.has(key)) value = state.requests.get(key);
      else {
        state.requestIds.push(key);
        let resultType = 'session'; let result;
        if (pathname === '/api/learning/transcriptions') {
          resultType = 'transcription';
          result = { transcription_id: `transcription-${state.requestIds.length}`, status: 'ready', context: JSON.parse(payload.context), machine_transcript: 'I went hiking yesterday.',
            audio: { state: 'not_saved', url: null, audio_asset_id: null, expires_at: null }, received_at: new Date().toISOString(), expires_at: new Date(Date.now() + 3600000).toISOString() };
        } else if (pathname === '/api/learning/sessions') state.session = initialSession();
        else if (pathname.endsWith('/turns')) {
          if (Number(payload.expected_revision) !== state.session.revision) throw new Error('Turn must use current HTTP revision');
          state.session.revision++;
          state.session.turns.push({ turn_id: 'turn-1', role: 'user', text: payload.text || 'I go hiking yesterday.', audio: { state: 'not_saved', url: null, audio_asset_id: null, expires_at: null } }, { turn_id: 'coach-1', role: 'assistant', text: 'Who did you go with?', audio: null });
        } else if (pathname.endsWith('/hints')) {
          if (Number(payload.expected_revision) !== state.session.revision) throw new Error('Hint must use current HTTP revision');
          state.session.revision++; resultType = 'hint'; result = { hint: 'Last weekend, I…' };
        } else if (pathname.endsWith('/finish')) {
          if (Number(payload.expected_revision) !== state.session.revision) throw new Error('Finish must use current HTTP revision');
          state.session.status = 'completed'; state.session.feedback = { ...feedback, source_revision: state.session.revision }; state.session.revision++;
          state.session.attempts.push({ attempt_id: 'attempt-1', session_id: 'session-1', source_turn_id: 'turn-1', focus_id: 'past-tense', parent_attempt_id: null,
            mode: 'free_response', transcript: 'I go hiking yesterday.', feedback: state.session.feedback, audio: null, created_at: '2026-10-23T08:00:00Z', evaluation: { status: 'partial', result: 'unverified', source: 'text', reason: '首次表达已保存，尚未评价重练。' } });
        } else if (pathname === '/api/learning/attempts') {
          if (Number(payload.source_revision) !== state.session.revision) throw new Error('Attempt must use current HTTP revision');
          const focusId = payload.focus_id || null;
          const parent = state.session.attempts.filter(item => item.source_turn_id === payload.source_turn_id && item.focus_id === focusId && item.mode === payload.mode).at(-1);
          if ((payload.parent_attempt_id || null) !== (parent?.attempt_id || null)) throw new Error('Parent attempt must use the same source, focus and mode');
          const attempt = { attempt_id: `attempt-${state.session.attempts.length + 1}`, session_id: 'session-1', source_turn_id: payload.source_turn_id, focus_id: focusId, parent_attempt_id: payload.parent_attempt_id || null,
            mode: payload.mode, transcript: payload.confirmed_text || payload.text || 'I went hiking yesterday.', feedback: null,
            evaluation: { status: 'available', result: 'met', source: 'text', reason: '这次使用了过去时。' }, audio: { state: 'not_saved' }, created_at: '2026-10-23T08:00:00Z' };
          state.session.attempts.push(attempt); state.session.revision++; resultType = 'attempt'; result = attempt;
        } else throw new Error(`Unexpected mutation ${pathname}`);
        value = { operation_id: `operation-${state.requestIds.length}`, request_id: key, status: 'succeeded', session_id: state.session?.session_id, result_type: resultType, result: structuredClone(result || state.session), error: null };
        state.operations.set(value.operation_id, value); state.requests.set(key, value);
        if (state.holdTurn && pathname.endsWith('/turns')) {
          const success = { ...value };
          Object.assign(value, { status: 'pending', result: null });
          state.releaseTurn = () => Object.assign(value, success);
        }
      }
    } else throw new Error(`Unexpected fixture URL ${pathname}`);
    if (!value) return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: { code: 'operation_not_found', message: 'Not found' } }) });
    await route.fulfill({ status: value.status === 'pending' ? 202 : 200, contentType: 'application/json', body: JSON.stringify(value) });
  });
}
module.exports = { task, feedback, initialSession, fixtureState, installFixture };
