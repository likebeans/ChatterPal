import { useEffect, useRef, useState } from 'react'
import { useAudioRecording } from '../hooks/useAudioRecording'
import { useObjectUrls } from '../hooks/useObjectUrls'
import { sameTranscriptionContext } from './validation'
import type { LearningSubmission, PracticeDraft, PracticeDraftChanges, TranscriptionContext } from './types'

interface Props {
  active: boolean
  disabled: boolean
  transcribing: boolean
  context: TranscriptionContext
  label: string
  submitLabel: string
  draft: PracticeDraft
  onDraftChange: (changes: PracticeDraftChanges) => void
  onTranscribe: (audio: Blob, context: TranscriptionContext) => Promise<void>
  onDiscard: () => Promise<void>
  onSubmit: (submission: LearningSubmission) => Promise<void>
  onPlay: (source: string, id: string) => void
  onStopPlayback: () => void
}

export function PracticeInput({ active, disabled, transcribing, context, label, submitLabel, draft, onDraftChange, onTranscribe, onDiscard, onSubmit, onPlay, onStopPlayback }: Props) {
  const [preview, setPreview] = useState<{ blob: Blob; url: string } | null>(null)
  const [error, setError] = useState('')
  const [discarding, setDiscarding] = useState(false)
  const working = useRef(false)
  const activeRef = useRef(active)
  const mounted = useRef(false)
  const recordingContext = useRef(context)
  const confirmationField = useRef<HTMLTextAreaElement>(null)
  const textField = useRef<HTMLTextAreaElement>(null)
  const restoreTextFocus = useRef(false)
  activeRef.current = active
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const { create, revoke } = useObjectUrls()
  useEffect(() => {
    if (!draft.audio) { setPreview(null); return }
    const url = create(draft.audio)
    setPreview({ blob: draft.audio, url })
    return () => { onStopPlayback(); revoke(url) }
  }, [draft.audio, create, revoke, onStopPlayback])
  const recording = useAudioRecording({ active,
    onStart: () => { onStopPlayback(); setError('') },
    onAudioReady: blob => { onDraftChange({ audio: blob, context: recordingContext.current }); setError('') }, onError: setError,
  })
  useEffect(() => { if (!active) onStopPlayback() }, [active, onStopPlayback])
  useEffect(() => () => { onStopPlayback() }, [onStopPlayback])
  useEffect(() => { if (active && draft.transcription) confirmationField.current?.focus() }, [active, draft.transcription?.transcription_id])
  const transcription = draft.transcription
  const hasRecording = Boolean(draft.audio || transcription || transcribing)
  useEffect(() => {
    if (restoreTextFocus.current && active && !disabled && !discarding && !hasRecording) { restoreTextFocus.current = false; textField.current?.focus() }
  }, [active, disabled, discarding, hasRecording])
  const stale = Boolean(draft.context && !sameTranscriptionContext(draft.context, context))
  const unavailable = Boolean(transcription && transcription.status !== 'ready')
  const canManage = active && !discarding && (!disabled || transcribing)
  const serverAudio = transcription?.audio
  const serverSource = serverAudio?.state === 'available' ? serverAudio.url || (serverAudio.audio_asset_id ? `/api/learning/audio/${encodeURIComponent(serverAudio.audio_asset_id)}` : null) : null
  const previewSource = preview?.blob === draft.audio ? preview?.url : serverSource
  const discard = async (rerecord = false) => {
    if (!canManage || working.current) return
    working.current = true
    setDiscarding(true); setError(''); onStopPlayback()
    try {
      await onDiscard()
      if (!activeRef.current) return
      if (rerecord) { recordingContext.current = context; await recording.start() }
      else restoreTextFocus.current = true
    } catch (failure) {
      if (mounted.current) setError(`无法放弃本次录音：${failure instanceof Error ? failure.message : '请稍后重试。'} 录音和核对文字已保留。`)
    } finally { working.current = false; if (mounted.current) setDiscarding(false) }
  }
  const record = () => {
    if (recording.status === 'requesting' || recording.status === 'encoding') recording.cancel()
    else if (recording.isRecording) recording.stop()
    else if (hasRecording || transcribing) void discard(true)
    else { recordingContext.current = context; void recording.start() }
  }
  const send = async () => {
    if (disabled || !active || recording.isBusy || discarding) return
    setError('')
    if (transcription) {
      if (stale || unavailable || !draft.confirmationText.trim()) return
      await onSubmit({ transcription_id: transcription.transcription_id, confirmed_text: draft.confirmationText.trim() })
    } else if (draft.audio) {
      if (stale) return
      await onTranscribe(draft.audio, draft.context || context)
    } else if (draft.text.trim()) await onSubmit({ text: draft.text.trim() })
  }
  const recordLabel = recording.status === 'requesting' ? '取消录音请求' : recording.status === 'encoding' ? '取消音频处理' : recording.isRecording ? '停止录音' : hasRecording ? '重新录音' : '开始录音'
  const sendLabel = transcription ? '确认文字并提交' : draft.audio ? '识别并核对录音' : submitLabel
  return <div className="learning-composer" aria-busy={transcribing || discarding}>
    <label>
      <span>{label}</span>
      <textarea ref={textField} value={draft.text} onChange={event => onDraftChange({ text: event.target.value })} rows={3}
        placeholder="可以直接开口，也可以先写下来。" disabled={disabled || !active || recording.isBusy || discarding || hasRecording}
        onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send() } }} />
    </label>
    {hasRecording && <div className="learning-inline">
      <span>{transcription ? '录音已识别，请核对后再提交。' : '录音已准备好，识别前可以试听。'}</span>
      <button type="button" onClick={() => { if (previewSource) onPlay(previewSource, 'draft') }} disabled={!active || !previewSource}>试听本次录音</button>
      <button type="button" onClick={() => { void discard() }} disabled={!canManage}>放弃本次录音</button>
    </div>}
    {transcription && <div className="learning-transcription">
      <label><span>核对录音文字</span><textarea ref={confirmationField} rows={3} lang="en" value={draft.confirmationText}
        onChange={event => onDraftChange({ confirmationText: event.target.value })} disabled={!active || disabled || discarding || recording.isBusy} /></label>
      <p className="learning-muted">请对照录音修改识别错误。确认后的文字用于对话和文字反馈，不代表发音测量。</p>
      {draft.confirmationText !== transcription.machine_transcript && <details><summary>查看机器识别原文</summary><p lang="en">{transcription.machine_transcript}</p></details>}
    </div>}
    {stale && <p className="learning-error" role="alert">这段录音对应的练习内容已有更新。请重新录音，或放弃本次录音后使用文字回答；已核对的文字会保留到放弃录音为止。</p>}
    {unavailable && <p className="learning-error" role="alert">{transcription?.status === 'consumed' ? '这段录音已经提交，请刷新记录核对结果。' : '这段录音已过期，请重新录音或使用文字回答。'}</p>}
    <div className="learning-actions">
      <button type="button" className="learning-record" onClick={record}
        disabled={!active || discarding || (!recording.isBusy && !canManage)} aria-busy={discarding || recording.status === 'requesting' || recording.status === 'encoding'}>{recordLabel}</button>
      <button type="button" className="learning-primary" onClick={() => { void send() }} disabled={!active || disabled || discarding || recording.isBusy || (hasRecording ? stale || unavailable || Boolean(transcription && !draft.confirmationText.trim()) : !draft.text.trim())}>{sendLabel}</button>
    </div>
    {transcribing && <p role="status">正在识别录音，完成后请核对文字。可以放弃或重新录音。</p>}
    {discarding && <p role="status">正在撤销录音草稿…</p>}
    {recording.isBusy && <p role="status">{recording.status === 'requesting' ? '等待麦克风授权，可以取消。' : recording.status === 'encoding' ? '正在处理录音，可以取消。' : '正在录音，停止后可以试听并识别。'}</p>}
    {error && <p className="learning-error" role="alert">{error}</p>}
  </div>
}
