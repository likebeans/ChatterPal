import { useEffect, useRef, useState } from 'react'
import { useAudioPlayback } from '../hooks/useAudioPlayback'
import { transcriptionBody } from './client'
import type { LearningApi } from './client'
import { PracticeInput } from './PracticeInput'
import { isAudio, isHint, isReviewHint, isReviewResult, isReviewReveal } from './validation'
import { useLearningFlow } from './useLearningFlow'
import { practiceDraftKey, usePracticeDrafts } from './usePracticeDrafts'
import type { LearningView } from './useLearningFlow'
import type {
  LearningAudio, LearningCoaching, LearningFocus, LearningProfile, LearningSession,
  ConfirmedTranscription, LearningSubmission, LearningTurn, PracticeDraftChanges, PracticeMode, TranscriptionContext,
} from './types'
import './learning.css'

const labels: Array<{ id: LearningView; label: string }> = [
  { id: 'today', label: '今日任务' }, { id: 'history', label: '学习记录' },
  { id: 'review', label: '到期复习' }, { id: 'progress', label: '练习进度' }, { id: 'settings', label: '学习设置' },
]
function date(value: string) { const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? '时间暂不可用' : parsed.toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) }
const stateLabel = { met: '已找到完成证据', not_met: '还可以再练', unverified: '尚不能确认' }
const sourceLabel = { text: '文字依据', acoustic: '声学依据', model: 'AI 建议', self_report: '个人自评' }

function AudioButton({ audio, id, label, onPlay }: { audio?: LearningAudio | null; id: string; label: string; onPlay: (source: string, id: string) => void }) {
  if (!audio || audio.state !== 'available') return <small className="learning-muted">{audio?.state === 'expired' ? '录音已到期，文字记录仍保留。' : '未保存可回放的音频。'}</small>
  const source = audio.url || (audio.audio_asset_id ? `/api/learning/audio/${encodeURIComponent(audio.audio_asset_id)}` : null)
  return source ? <button type="button" onClick={() => onPlay(source, id)}>{label}</button> : <small>音频暂不可用。</small>
}

function TranscriptHistory({ value }: { value: ConfirmedTranscription }) {
  if (!value.transcription_id) return null
  return <div className="learning-transcription-history"><strong>已核对的文字</strong>
    <p className="learning-muted">文字反馈依据核对后的表达，不代表发音测量。</p>
    {value.machine_transcript != null && value.machine_transcript !== value.confirmed_text && <details><summary>查看机器识别原文</summary><p lang="en">{value.machine_transcript}</p></details>}
  </div>
}

function TurnCoaching({ coaching }: { coaching?: LearningCoaching }) {
  if (!coaching) return null
  return <section aria-label="本句文字建议">
    <strong>本句文字建议</strong>
    {coaching.status === 'available' ? <>
      <p className="learning-muted">建议引用本句文字，未评估发音。</p>
      {coaching.suggestions.length ? coaching.suggestions.map(suggestion => <div key={suggestion.original}>
        <p>原表达：<span lang="en">{suggestion.original}</span></p>
        <p>可以改为：<strong lang="en">{suggestion.corrected}</strong></p>
        <p>{suggestion.explanation}</p>
      </div>) : <p>本句暂无需要提醒的文字调整，可以继续表达。</p>}
    </> : <><p className="learning-muted">{coaching.status === 'deferred' ? '本句建议将在结束对话后集中反馈。' : '本句文字建议暂不可用，原话和教练回复已保留。'}</p>{coaching.reason && <p className="learning-muted">{coaching.reason}</p>}</>}
  </section>
}

function ProfileForm({ profile, disabled, onSave, onboarding = false }: { profile: LearningProfile; disabled: boolean; onSave: (profile: LearningProfile) => Promise<void>; onboarding?: boolean }) {
  const [draft, setDraft] = useState(profile)
  const [saving, setSaving] = useState(false)
  useEffect(() => { setDraft(profile) }, [profile])
  const save = async () => { if (saving) return; setSaving(true); try { await onSave({ ...draft, onboarding_completed: true }) } finally { setSaving(false) } }
  return <section className="learning-preferences" aria-label={onboarding ? '开始前的学习偏好' : '学习偏好'}>
    <h3>{onboarding ? '先选一个适合自己的节奏' : '学习偏好'}</h3>
    {onboarding && <p>可以跳过，直接选一项任务。之后随时调整。</p>}
    <div className="learning-fields">
      <label>常用情境<select value={draft.goal} onChange={event => setDraft({ ...draft, goal: event.target.value as LearningProfile['goal'] })}>
        <option value="daily">日常交流</option><option value="travel">旅行出行</option><option value="work">工作交流</option></select></label>
      <label>每天可用时间<select value={draft.daily_minutes} onChange={event => setDraft({ ...draft, daily_minutes: Number(event.target.value) })}>
        <option value={5}>5 分钟</option><option value={8}>8 分钟</option><option value={15}>15 分钟</option></select></label>
      <label>纠错时机<select value={draft.correction_mode} onChange={event => setDraft({ ...draft, correction_mode: event.target.value as LearningProfile['correction_mode'] })}>
        <option value="after_session">结束后集中反馈</option><option value="immediate">对话中适量提醒</option></select></label>
    </div>
    <label className="learning-checkbox"><input type="checkbox" checked={draft.retain_audio} onChange={event => setDraft({ ...draft, retain_audio: event.target.checked })} />将原音保留 7 天，方便之后对照</label>
    <p className="learning-muted">默认保留文字学习记录，原音仅供 1 小时临时回放。当前浏览器身份用于找回记录；清除站点数据后暂不支持找回。未发送的文字和录音仅暂存于当前页面；已经识别的录音可在有效期内恢复核对，刷新后未确认的修改会丢失。</p>
    <div className="learning-actions">
      <button type="button" className="learning-primary" disabled={disabled || saving} onClick={() => { void save() }}>{saving ? '正在保存…' : '保存偏好'}</button>
      {onboarding && <button type="button" disabled={disabled || saving} onClick={() => { void onSave({ ...profile, onboarding_completed: true }) }}>跳过引导</button>}
    </div>
  </section>
}

export default function LearningWorkspace({ active, api }: { active: boolean; api?: LearningApi }) {
  const flow = useLearningFlow(api)
  const playback = useAudioPlayback(active)
  const [speed, setSpeed] = useState(1)
  const [practiceBySession, setPracticeBySession] = useState<Record<string, { turnId: string; focusId: string | null; mode: PracticeMode } | null>>({})
  const review = flow.selectedReview
  const setReview = flow.setSelectedReview
  const [exampleAttemptId, setExampleAttemptId] = useState<string | null>(null)
  const [deleteConfirm, setDeleteConfirm] = useState(false)
  const [exportError, setExportError] = useState('')
  const focusHeading = useRef<HTMLHeadingElement>(null)
  const session = flow.session
  const owner = flow.identity?.learner_id
  const drafts = usePracticeDrafts(owner, flow.confirmation, flow.readyTranscriptions)
  const selectedPractice = session ? practiceBySession[session.session_id] : null
  const practiceTurnSnapshot = session?.turns.find(turn => turn.turn_id === selectedPractice?.turnId && turn.role === 'user')
  const practiceFocusSnapshot = session?.feedback?.focuses.find(focus => focus.focus_id === selectedPractice?.focusId && focus.source_turn_id === practiceTurnSnapshot?.turn_id) || null
  const practice = selectedPractice && practiceTurnSnapshot ? { turn: practiceTurnSnapshot, focus: practiceFocusSnapshot, mode: selectedPractice.mode } : null
  const setPractice = (value: typeof practice) => {
    if (session) setPracticeBySession(previous => ({ ...previous, [session.session_id]: value ? { turnId: value.turn.turn_id, focusId: value.focus?.focus_id || null, mode: value.mode } : null }))
  }
  const turnDraftKey = practiceDraftKey(owner, session?.session_id || '', 'turn')
  const attemptDraftKey = practiceDraftKey(owner, session?.session_id || '', 'attempt', practice?.turn.turn_id || '', selectedPractice?.focusId || '', practice?.mode || '')
  const reviewDraftKey = practiceDraftKey(owner, review?.session_id || '', 'review', review?.review_item_id || '')
  const busy = Boolean(flow.pending) || flow.loading || Boolean(flow.recovery)
  const inSession = active && flow.view === 'session'
  const play = (source: string, id: string) => { void playback.play(source, id, speed) }
  useEffect(() => { playback.stop() }, [session?.session_id, playback.stop])
  useEffect(() => { playback.stop(); setPracticeBySession({}); setReview(null); setExampleAttemptId(null); setDeleteConfirm(false) }, [owner, playback.stop, setReview])
  useEffect(() => { if (active) focusHeading.current?.focus() }, [flow.view, active])
  useEffect(() => { if (playback.audioRef.current) playback.audioRef.current.playbackRate = speed }, [speed, playback.audioRef])
  const chooseView = (view: LearningView) => { playback.stop(); flow.setView(view) }
  const practiceTurn = (turn: LearningTurn, focus: LearningFocus | null) => { playback.stop(); setPractice({ turn, focus, mode: 'free_response' }) }
  const perform = (action: Parameters<typeof flow.mutate>[0], path: string, data: Record<string, unknown> | FormData, sessionId = session?.session_id) => flow.mutate(action, path, data, sessionId)
  const sendTurn = (submission: LearningSubmission) => {
    if (!session) return Promise.resolve()
    return flow.mutate('turn', `/sessions/${encodeURIComponent(session.session_id)}/turns`, { expected_revision: session.revision, ...submission }, session.session_id,
      { draft_key: turnDraftKey, draft_version: drafts.read(turnDraftKey).version })
  }
  const linkedAttempts = session?.attempts.filter(attempt => attempt.source_turn_id === practice?.turn.turn_id && attempt.focus_id === (practice?.focus?.focus_id || null)) || []
  const practiceAttempts = linkedAttempts.filter(attempt => attempt.mode === practice?.mode)
  const lastAttempt = practiceAttempts[practiceAttempts.length - 1]
  const exampleAttempt = lastAttempt || linkedAttempts[0]
  const turnContext: TranscriptionContext = { kind: 'turn', session_id: session?.session_id || '', expected_revision: session?.revision || 1 }
  const attemptContext: TranscriptionContext = { kind: 'attempt', session_id: session?.session_id || '', source_revision: session?.revision || 1,
    source_turn_id: practice?.turn.turn_id || '', focus_id: practice?.focus?.focus_id || null, parent_attempt_id: lastAttempt?.attempt_id || null,
    mode: practice?.mode || 'free_response', target_text: practice?.mode === 'read_aloud' ? practice.focus?.suggested || practice.turn.text : null }
  const reviewContext: TranscriptionContext = { kind: 'review', review_item_id: review?.review_item_id || '', expected_revision: review?.revision || 1 }
  const inputProps = (key: string, context: TranscriptionContext) => ({
    context, draft: drafts.read(key),
    transcribing: flow.pending?.action === 'transcribe' && flow.pending.draft_key === key,
    onDraftChange: (changes: PracticeDraftChanges) => drafts.update(key, changes),
    onTranscribe: (audio: Blob, pinnedContext: TranscriptionContext) => flow.mutate('transcribe', '/transcriptions', transcriptionBody(pinnedContext, audio),
      pinnedContext.kind === 'review' ? review?.session_id : pinnedContext.session_id, { draft_key: key, draft_version: drafts.read(key).version }),
    onDiscard: async () => {
      const draft = drafts.read(key)
      const draftVersion = flow.pending?.action === 'transcribe' && flow.pending.draft_key === key ? flow.pending.draft_version ?? draft.version : draft.version
      const consumed = await flow.cancelTranscription({ draft_key: key, draft_version: draftVersion }, draft.transcription?.transcription_id)
      drafts.discard(key, draftVersion, consumed)
    },
  })
  const submitAttempt = (submission: LearningSubmission) => {
    if (!session || !practice) return Promise.resolve()
    const { kind: _kind, ...fields } = attemptContext
    return flow.mutate('attempt', '/attempts', { ...fields, ...submission }, session.session_id,
      { draft_key: attemptDraftKey, draft_version: drafts.read(attemptDraftKey).version })
  }
  const resumeTranscription = async (context: TranscriptionContext) => {
    playback.stop()
    if (context.kind === 'review') await flow.openReview(context.review_item_id)
    else {
      await flow.openSession(context.session_id)
      setPracticeBySession(previous => ({ ...previous, [context.session_id]: context.kind === 'attempt'
        ? { turnId: context.source_turn_id, focusId: context.focus_id, mode: context.mode } : null }))
    }
  }
  const exportData = async () => {
    setExportError('')
    try {
      const data = await flow.exportData()
      const url = URL.createObjectURL(data)
      const link = document.createElement('a'); link.href = url; link.download = 'chatterpal-learning.json'; link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (error) { setExportError(error instanceof Error ? error.message : '导出失败，请重试。') }
  }
  const selectedReviewResult = flow.lastOperation?.result_type === 'review' && isReviewResult(flow.lastOperation.result)
    && flow.lastOperation.result.review_item_id === review?.review_item_id ? flow.lastOperation.result : null
  const exampleAudio = exampleAttemptId === exampleAttempt?.attempt_id && flow.lastOperation?.result_type === 'audio'
    && isAudio(flow.lastOperation.result) ? flow.lastOperation.result : undefined
  const revealedAnswer = flow.lastOperation?.result_type === 'review_reveal' && isReviewReveal(flow.lastOperation.result)
    && flow.lastOperation.result.review_item_id === review?.review_item_id && flow.lastOperation.result.cycle === review.cycle ? flow.lastOperation.result.answer : null
  const reviewHint = flow.lastOperation?.result_type === 'review_hint' && isReviewHint(flow.lastOperation.result)
    && flow.lastOperation.result.review_item_id === review?.review_item_id && flow.lastOperation.result.cycle === review.cycle ? flow.lastOperation.result.hint : null
  const saveReview = (sourceTurnId: string, focusId: string | null, attemptId?: string) => {
    if (!session) return
    void perform('save-review', '/reviews', { session_id: session.session_id, source_revision: session.revision, source_turn_id: sourceTurnId, focus_id: focusId, ...(attemptId ? { attempt_id: attemptId } : {}) })
  }

  return <div className="learning-workspace">
    <div className="learning-heading"><div><p className="learning-eyebrow">每天练会一个表达</p><h2 ref={focusHeading} tabIndex={-1}>把今天说的话，变成下次会用的表达</h2></div>
      <label className="learning-speed">播放速度<select aria-label="学习音频播放速度" value={speed} onChange={event => setSpeed(Number(event.target.value))}><option value={0.75}>0.75× 慢速</option><option value={1}>1× 正常</option><option value={1.25}>1.25×</option></select></label></div>
    <nav className="learning-nav" aria-label="学习流程">
      {labels.map(item => <button type="button" key={item.id} aria-pressed={flow.view === item.id} onClick={() => chooseView(item.id)}>{item.label}{item.id === 'review' && flow.reviews.length > 0 ? `（${flow.reviews.length}）` : ''}</button>)}
      {session && <button type="button" aria-pressed={flow.view === 'session'} onClick={() => chooseView('session')}>返回本次练习</button>}
    </nav>
    {flow.loading && <p role="status">正在加载学习记录…</p>}
    {flow.error && <div className="learning-error" role="alert"><p>{flow.error}</p><button type="button" disabled={flow.loading} onClick={() => { void (flow.pending || flow.recovery === 'refresh' ? flow.retryPending() : flow.connect()) }}>{flow.recovery === 'connect' ? '重新连接学习服务' : flow.recovery === 'refresh' ? '刷新本次记录后继续' : flow.pending ? '查询或重试原操作' : '重新连接学习服务'}</button></div>}
    {flow.notice && <p className="learning-notice" role="status">{flow.notice}</p>}
    {flow.pending && <div className="learning-pending" role="status"><p>{flow.pollingPaused ? '已暂停自动查询。未发送的内容会保留，恢复连接后可继续核对原操作。' : flow.pending.operation_id ? '正在处理这次练习，完成后会自动显示。可以切换页面，录音和播放会停止。' : '这次提交正在确认中，请勿重复创建练习。'}</p>
      <button type="button" disabled={flow.loading} onClick={() => { void flow.retryPending() }}>{flow.recovery === 'connect' ? '重新连接并查询原操作' : '查询原操作'}</button>
      {!flow.pending.operation_id && !flow.canReplay && <button type="button" onClick={() => { void flow.acknowledgeRecovery() }}>已核对历史，结束恢复提示</button>}
    </div>}
    {flow.readyTranscriptions.length > 0 && <aside className="learning-notice" aria-label="待核对的录音"><p>还有录音等待核对，确认后才会提交回答。</p><div className="learning-actions">{flow.readyTranscriptions.map(({ receipt, transcription }) => <button type="button" key={receipt.transcription_id} disabled={flow.loading} onClick={() => { void resumeTranscription(transcription.context) }}>{transcription.context.kind === 'turn' ? '继续核对任务录音' : transcription.context.kind === 'attempt' ? '继续核对重练录音' : '继续核对复习录音'}</button>)}</div></aside>}
    {playback.error && <p className="learning-error" role="alert">{playback.error}</p>}
    {playback.playingId && <button type="button" onClick={playback.stop}>停止学习音频</button>}
    {!flow.identity && !flow.loading && !flow.error && <button type="button" onClick={() => { void flow.connect() }}>连接学习服务</button>}
    {flow.identity && <>
      {flow.warnings.length > 0 && <details className="learning-muted"><summary>部分学习内容暂不可用</summary><ul>{flow.warnings.map(item => <li key={item}>{item}</li>)}</ul><button type="button" onClick={() => { void flow.connect() }}>重新加载</button></details>}
      {flow.view === 'today' && <div className="learning-section">
        {!flow.identity.profile.onboarding_completed && <ProfileForm profile={flow.identity.profile} disabled={busy} onSave={flow.saveProfile} onboarding />}
        <header><h3>今天想在哪个情境开口？</h3><p>完成一段对话，查看有原句依据的反馈，再选一句重练。已保存的文字和尝试可以随时回看。</p></header>
        <div className="learning-task-grid">{flow.tasks.map(task => <article className="learning-task" key={task.task_id}>
          <span className="learning-muted">约 {task.estimated_minutes} 分钟</span><h4>{task.title}</h4><p>{task.description}</p>
          <ul>{task.objectives.map(objective => <li key={objective}>{objective}</li>)}</ul>
          <button type="button" className="learning-primary" disabled={busy} onClick={() => { setPractice(null); void flow.mutate('start', '/sessions', { task_id: task.task_id, content_version: task.content_version, correction_mode: flow.identity!.profile.correction_mode }) }}>开始{task.title}</button>
        </article>)}</div>
        {flow.tasks.length === 0 && !flow.loading && <p>任务还未加载。可以重新连接学习服务，或先使用上方的自由对话。</p>}
      </div>}
      {flow.view === 'history' && <section className="learning-section"><h3>学习记录</h3><p>继续未完成的任务，或回看原句、反馈和重练尝试。</p>
        {flow.history.length === 0 ? <p>还没有可显示的学习记录。完成第一项任务后会出现在这里。</p> : <ul className="learning-history">{flow.history.map(item => <li key={item.session_id}><div><strong>{item.title}</strong><p>{date(item.updated_at)} · {item.status === 'active' ? '练习中' : item.status === 'completed' ? '已结束' : '已归档'}</p></div><button type="button" onClick={() => { void flow.openSession(item.session_id) }}>打开练习记录</button></li>)}</ul>}
        {flow.cursor && <button type="button" onClick={() => { void flow.loadMore() }}>加载更多记录</button>}
      </section>}
      {flow.view === 'session' && session && <section className="learning-section" aria-label="当前任务练习">
        <header className="learning-session-title"><div><span className="learning-eyebrow">{session.status === 'active' ? '正在练习' : '练习回顾'}</span><h3>{session.task.title}</h3><p>你是{session.task.learner_role}，教练扮演{session.task.coach_role}。</p></div><button type="button" onClick={() => { void flow.openSession(session.session_id) }}>刷新本次记录</button></header>
        <div className="learning-objectives"><strong>这次要说清楚</strong><ul>{session.task.objectives.map(item => <li key={item}>{item}</li>)}</ul></div>
        <details><summary>需要时看看示例表达</summary><ul>{session.task.examples.map(item => <li key={item}>{item}</li>)}</ul></details>
        <div className="learning-conversation" role="log" aria-label="任务对话" aria-busy={Boolean(flow.pending)}>{session.turns.map(turn => <article key={turn.turn_id} className={`learning-turn learning-turn-${turn.role}`}><span>{turn.role === 'user' ? '我的表达' : '教练'}</span><p lang="en">{turn.text}</p><TranscriptHistory value={turn} />{turn.role === 'user' && <TurnCoaching coaching={session.coaching?.find(item => item.source_turn_id === turn.turn_id)} />}<div className="learning-inline"><AudioButton audio={turn.audio} id={turn.turn_id} label={turn.role === 'user' ? '回放这句原音' : '播放教练回复'} onPlay={play} />{turn.role === 'user' && <button type="button" onClick={() => practiceTurn(turn, session.feedback?.focuses.find(focus => focus.source_turn_id === turn.turn_id) || null)}>练这一句</button>}</div></article>)}</div>
        {session.status === 'active' && !practice && <>
          <div className="learning-actions"><button type="button" disabled={busy} onClick={() => { void perform('hint', `/sessions/${encodeURIComponent(session.session_id)}/hints`, { expected_revision: session.revision, kind: 'sentence_starter' }) }}>给我一个开头</button><button type="button" disabled={busy || session.turns.every(turn => turn.role !== 'user')} onClick={() => { void perform('finish', `/sessions/${encodeURIComponent(session.session_id)}/finish`, { expected_revision: session.revision }) }}>结束对话，查看反馈</button></div>
          {flow.lastOperation?.session_id === session.session_id && isHint(flow.lastOperation.result) && <p className="learning-notice" role="status">{flow.lastOperation.result.hint}</p>}
          <PracticeInput key={turnDraftKey} {...inputProps(turnDraftKey, turnContext)} active={inSession} disabled={busy} label="我的任务回答" submitLabel="发送任务回答" onSubmit={sendTurn} onPlay={play} onStopPlayback={playback.stop} />
        </>}
        {session.status === 'completed' && (!session.feedback || session.feedback.status === 'unavailable') && <button type="button" className="learning-primary" disabled={busy} onClick={() => { void perform('feedback', `/sessions/${encodeURIComponent(session.session_id)}/feedback`, { expected_revision: session.revision }) }}>{session.feedback ? '重新生成本次反馈' : '生成本次重点反馈'}</button>}
        {session.feedback && <section className="learning-feedback" aria-label="练后重点反馈"><h4>这次练习的重点</h4>
          {session.feedback.strength && <p><strong>做得好的表达：</strong>{session.feedback.strength}</p>}
          <p className="learning-muted">{session.feedback.reason || (session.feedback.status === 'unavailable' ? '本次暂时不能完成评估。' : '反馈基于本次表达，不代表已完成发音评分。')}</p>
          {session.feedback.criteria.map(item => <div className="learning-criterion" key={item.criterion_id}><strong>{item.description}</strong><span>{stateLabel[item.result]}</span>{item.evidence && <p>依据：{item.evidence}</p>}</div>)}
          {session.feedback.focuses.slice(0, 2).map(focus => <article className="learning-focus" key={focus.focus_id}><span className="learning-muted">{sourceLabel[focus.source]}</span><p lang="en">{focus.original}</p><p lang="en"><strong>{focus.suggested}</strong></p><p>{focus.explanation}</p><div className="learning-actions"><button type="button" className="learning-primary" onClick={() => { const turn = session.turns.find(item => item.turn_id === focus.source_turn_id); if (turn) practiceTurn(turn, focus) }}>针对这一句重练</button><button type="button" disabled={busy} onClick={() => saveReview(focus.source_turn_id, focus.focus_id)}>保存这个表达到复习</button></div></article>)}
        </section>}
        {practice && <section className="learning-practice" aria-label="单句重练"><header className="learning-inline"><h4>把这一句再说一次</h4><button type="button" onClick={() => { playback.stop(); setPractice(null) }}>返回任务对话</button></header>
          <p className="learning-muted">原句、首次尝试和每次重练都会保留，方便对照。文字差异不等于发音错误。</p>
          <article className="learning-original"><h5>任务中的原话</h5><p lang="en">{practice.turn.text}</p>{practice.focus && practice.focus.original !== practice.turn.text && <p>本次重点片段：<span lang="en">{practice.focus.original}</span></p>}<small className="learning-muted">原音对应任务中的整段表达。</small></article>
          <div className="learning-mode" role="group" aria-label="重练方式"><button type="button" aria-pressed={practice.mode === 'free_response'} disabled={busy} onClick={() => { playback.stop(); setPractice({ ...practice, mode: 'free_response' }) }}>自由表达</button><button type="button" aria-pressed={practice.mode === 'read_aloud'} disabled={busy} onClick={() => { playback.stop(); setPractice({ ...practice, mode: 'read_aloud' }) }}>跟读练习</button></div>
          {practice.mode === 'read_aloud' ? <p className="learning-target" lang="en">{practice.focus?.suggested || practice.turn.text}</p> : <p>围绕「{session.task.title}」换一种说法，不需要填写参考句。</p>}
          <div className="learning-inline"><AudioButton audio={practice.turn.audio} id={`original-${practice.turn.turn_id}`} label="播放首次原音" onPlay={play} />{exampleAttempt && <button type="button" disabled={busy} onClick={() => { setExampleAttemptId(exampleAttempt.attempt_id); void perform('example', `/attempts/${encodeURIComponent(exampleAttempt.attempt_id)}/example-audio`, {}) }}>获取目标范读</button>}</div>
          {exampleAudio && <AudioButton audio={exampleAudio} id={`example-${exampleAttemptId}`} label="播放目标范读" onPlay={play} />}
          <div className="learning-attempts">{practiceAttempts.map((attempt, index) => {
            const parentIndex = practiceAttempts.findIndex(item => item.attempt_id === attempt.parent_attempt_id)
            return <article key={attempt.attempt_id}><h5>{index === 0 ? '第一次练习' : `第 ${index + 1} 次练习`}</h5><p className="learning-muted">{attempt.parent_attempt_id ? `重练 · 接续${parentIndex >= 0 ? `第 ${parentIndex + 1} 次练习` : '此前的尝试'}` : '首次尝试 · 关联任务原话'}</p><p lang="en">{attempt.transcript}</p><TranscriptHistory value={attempt} /><p>{stateLabel[attempt.evaluation.result]} · {sourceLabel[attempt.evaluation.source]}</p><p>{attempt.evaluation.reason}</p><AudioButton audio={attempt.audio} id={attempt.attempt_id} label={`回放第 ${index + 1} 次练习`} onPlay={play} /><button type="button" disabled={busy} onClick={() => saveReview(attempt.source_turn_id, attempt.focus_id, attempt.attempt_id)}>保存这次尝试到复习</button></article>
          })}</div>
          <PracticeInput key={attemptDraftKey} {...inputProps(attemptDraftKey, attemptContext)} active={inSession} disabled={busy} label={practice.mode === 'read_aloud' ? '我的跟读' : '我的新表达'} submitLabel={lastAttempt ? '保存这次重练' : '提交这次练习'} onSubmit={submitAttempt} onPlay={play} onStopPlayback={playback.stop} />
        </section>}
      </section>}
      {flow.view === 'review' && <section className="learning-section"><h3>先想一想，再开口</h3>
        {flow.reviewUnavailable ? <p role="status">{flow.reviewUnavailable}</p> : <p>到期后先回答新问题，需要时再看提示。原句复述、看过答案和重复问题的练习会分别记录。</p>}
        {!flow.reviewUnavailable && !review && (flow.reviews.length ? <ul className="learning-history">{flow.reviews.map(item => <li key={item.review_item_id}><div><strong>{item.prompt || '等待到期的新问题'}</strong><p>{date(item.due_at)} 到期</p>{!item.independent_variant_available && <p className="learning-muted">新问题已用完，可以继续重复练习。</p>}</div><button type="button" onClick={() => { playback.stop(); setReview(item) }}>开始这项复习</button></li>)}</ul> : <p>当前没有到期复习。可以从练后反馈或已保存的尝试安排次日复习。</p>)}
        {!flow.reviewUnavailable && review && <div className="learning-practice" aria-busy={busy}><h4>{review.prompt || '等待到期的新问题'}</h4><p className="learning-muted">复习时间：{date(review.due_at)}</p>
          {review.availability_reason && <p role="status">{review.availability_reason}</p>}
          {review.revealed_answer ? <p className="learning-muted">本轮已经看过参考表达，之后的回答按有提示练习记录。</p> : review.used_hint && <p className="learning-muted">本轮已经使用提示，之后的回答按有提示练习记录。</p>}
          <div className="learning-actions"><button type="button" onClick={() => { playback.stop(); setReview(null) }}>返回复习列表</button><button type="button" disabled={busy} onClick={() => { void flow.openReview(review.review_item_id) }}>刷新本项复习</button>
            <button type="button" disabled={busy || !review.prompt_available} onClick={() => { void perform('review-hint', `/reviews/${encodeURIComponent(review.review_item_id)}/hints`, { expected_revision: review.revision, kind: 'sentence_starter' }, review.session_id) }}>给我一个复习开头并记录提示</button>
            <button type="button" disabled={busy || !review.prompt_available} onClick={() => { void perform('reveal', `/reviews/${encodeURIComponent(review.review_item_id)}/reveal`, { expected_revision: review.revision }, review.session_id) }}>显示参考表达并记录提示</button>
            <button type="button" disabled={busy} onClick={() => { void perform('defer-review', `/reviews/${encodeURIComponent(review.review_item_id)}/defer`, { expected_revision: review.revision, days: 1 }, review.session_id) }}>明天再复习</button></div>
          {reviewHint && <p className="learning-notice" role="status" lang="en">{reviewHint}</p>}
          {revealedAnswer && <div><p className="learning-muted">保存时的参考表达，新的情境允许其他正确说法。</p><p className="learning-target" lang="en">{revealedAnswer}</p></div>}
          {selectedReviewResult?.attempt && <div role="status"><p>刚才回答的问题：<span lang="en">{selectedReviewResult.attempt.prompt}</span></p><p lang="en">{selectedReviewResult.attempt.transcript}</p><TranscriptHistory value={selectedReviewResult.attempt} /><p>{stateLabel[selectedReviewResult.attempt.evaluation.result]}：{selectedReviewResult.attempt.evaluation.reason}</p>
            <p>{selectedReviewResult.attempt.evidence_kind === 'shown_answer' ? '本次看过参考表达，按有提示练习记录。' : selectedReviewResult.attempt.evidence_kind === 'hinted' ? '本次使用提示，未计为独立迁移。' : selectedReviewResult.attempt.evidence_kind === 'early_practice' ? '本次在到期前作答，按提前练习记录。' : selectedReviewResult.attempt.evidence_kind === 'source_repeat' ? '本次复述原句或参考表达，未计为独立迁移。' : selectedReviewResult.attempt.evidence_kind === 'repeated_prompt' ? '新问题已用完，本次按重复问题练习记录。' : selectedReviewResult.attempt.schedule_advanced ? '本次到期后独立回答新问题，已保存文字达成证据。' : '本次回答已保存，尚无独立达成的完整证据。'}</p>
            <p>{selectedReviewResult.attempt.evaluation.status === 'unavailable' ? '评价暂不可用，复习时间和新问题均保留。' : selectedReviewResult.attempt.schedule_advanced ? '复习时间已按本次证据更新。' : '本次未延后复习时间。'}</p><p>下次复习：{date(selectedReviewResult.next_due_at)}</p><AudioButton audio={selectedReviewResult.attempt.audio} id={selectedReviewResult.attempt.attempt_id} label="回放本次复习录音" onPlay={play} /></div>}
          {review.attempts && review.attempts.some(attempt => attempt.attempt_id !== selectedReviewResult?.attempt.attempt_id) && <section className="learning-attempts" aria-label="已保存的复习回答">{review.attempts.filter(attempt => attempt.attempt_id !== selectedReviewResult?.attempt.attempt_id).map(attempt => <article key={attempt.attempt_id}><h5>{date(attempt.created_at)} 的回答</h5><p lang="en">{attempt.prompt}</p><p lang="en">{attempt.transcript}</p><TranscriptHistory value={attempt} /><p>{stateLabel[attempt.evaluation.result]}：{attempt.evaluation.reason}</p><p>{attempt.schedule_advanced ? '到期后独立回答新问题，已有文字达成证据。' : attempt.used_hint ? '本次使用提示，按有提示练习保存。' : '本次按练习记录保存，未增加独立迁移记录。'}</p><AudioButton audio={attempt.audio} id={attempt.attempt_id} label="回放已保存的复习录音" onPlay={play} /></article>)}</section>}
          <PracticeInput key={reviewDraftKey} {...inputProps(reviewDraftKey, reviewContext)} active={active && flow.view === 'review'} disabled={busy || !review.prompt_available} label="我的复习回答" submitLabel="提交复习回答" onSubmit={submission => flow.mutate('review', `/reviews/${encodeURIComponent(review.review_item_id)}/attempts`, { expected_revision: review.revision, ...submission }, review.session_id, { draft_key: reviewDraftKey, draft_version: drafts.read(reviewDraftKey).version })} onPlay={play} onStopPlayback={playback.stop} />
        </div>}
      </section>}
      {flow.view === 'progress' && <section className="learning-section"><h3>看得见依据的练习进度</h3><p>练习投入、同题重练和新情境中的使用分别记录。当前没有经过校准的能力总分。</p>
        {flow.progress ? <><dl className="learning-stat-list"><div><dt>完成的任务</dt><dd>{flow.progress.sessions_completed}</dd></div><div><dt>保存的练习尝试</dt><dd>{flow.progress.attempts_count}</dd></div><div><dt>同题重练</dt><dd>{flow.progress.retries_count}</dd></div><div><dt>到期且已评估的回答</dt><dd>{flow.progress.reviews_completed}</dd></div><div><dt>独立迁移的记录</dt><dd>{flow.progress.independent_transfer_count}</dd></div></dl><p className="learning-muted">到期且已评估只说明已有本次文字判断；独立迁移另需未看提示、新问题和达成证据。</p><dl className="learning-stat-list" aria-label="复习记录分类"><div><dt>保存的复习回答</dt><dd>{flow.progress.review_attempts_count ?? 0}</dd></div><div><dt>提前练习</dt><dd>{flow.progress.early_practice_count ?? 0}</dd></div><div><dt>原句或参考表达复述</dt><dd>{flow.progress.source_repeat_count ?? 0}</dd></div><div><dt>有提示复习</dt><dd>{flow.progress.hinted_review_count ?? 0}</dd></div><div><dt>重复问题练习</dt><dd>{flow.progress.repeated_prompt_count ?? 0}</dd></div><div><dt>评估尚不完整的复习</dt><dd>{flow.progress.unassessed_reviews_count ?? 0}</dd></div></dl><h4>具体记录</h4>{flow.progress.evidence.length === 0 ? <p>暂时没有可展示的改善证据。继续练习后可以回看对应尝试。</p> : <ul className="learning-history">{flow.progress.evidence.map(item => <li key={item.attempt_id}><div><strong>{item.kind === 'transfer' ? '换情境使用' : item.kind === 'same_task' ? '同题重练' : '练习记录'}</strong><p>{item.description}</p>{item.status !== 'available' && <span>评估尚不完整</span>}</div><button type="button" onClick={() => { playback.stop(); void (item.review_item_id ? flow.openReview(item.review_item_id) : flow.openSession(item.session_id)) }}>查看对应尝试</button></li>)}</ul>}</> : <p>进度服务暂不可用，请稍后重新加载。</p>}
      </section>}
      {flow.view === 'settings' && <section className="learning-section"><ProfileForm profile={flow.identity.profile} disabled={busy} onSave={flow.saveProfile} />
        <h3>管理学习数据</h3><p>导出内容包括文字学习记录及关联信息。删除后无法通过当前浏览器恢复。</p><div className="learning-actions"><button type="button" disabled={busy} onClick={() => { void exportData() }}>导出学习记录 JSON</button><button type="button" className="learning-danger" onClick={() => setDeleteConfirm(true)}>删除我的学习数据</button></div>
        {exportError && <p role="alert">{exportError}</p>}
        {deleteConfirm && <div className="learning-confirm" role="group" aria-label="确认删除学习数据"><p>将删除当前学习身份下的记录、复习和已保存音频。此操作无法撤销。</p><div className="learning-actions"><button type="button" className="learning-danger" disabled={flow.loading} onClick={() => { playback.stop(); setPractice(null); setReview(null); void flow.deleteData().then(() => setDeleteConfirm(false)) }}>确认删除全部学习数据</button><button type="button" onClick={() => setDeleteConfirm(false)}>保留数据</button></div></div>}
      </section>}
    </>}
    <audio ref={playback.audioRef} />
  </div>
}

export type { LearningSession }
