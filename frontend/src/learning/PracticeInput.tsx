import { useEffect, useState } from 'react'
import { useAudioRecording } from '../hooks/useAudioRecording'
import { useObjectUrls } from '../hooks/useObjectUrls'
import type { LearningSubmission, PracticeDraft } from './types'

interface Props {
  active: boolean
  disabled: boolean
  label: string
  submitLabel: string
  draft: PracticeDraft
  onDraftChange: (changes: Partial<Pick<PracticeDraft, 'text' | 'audio'>>) => void
  onSubmit: (submission: LearningSubmission) => Promise<void>
  onPlay: (source: string, id: string) => void
  onStopPlayback: () => void
}

export function PracticeInput({ active, disabled, label, submitLabel, draft, onDraftChange, onSubmit, onPlay, onStopPlayback }: Props) {
  const [preview, setPreview] = useState<{ blob: Blob; url: string } | null>(null)
  const [error, setError] = useState('')
  const { create, revoke } = useObjectUrls()
  useEffect(() => {
    if (!draft.audio) { setPreview(null); return }
    const url = create(draft.audio)
    setPreview({ blob: draft.audio, url })
    return () => { onStopPlayback(); revoke(url) }
  }, [draft.audio, create, revoke, onStopPlayback])
  const recording = useAudioRecording({ active,
    onStart: () => { onStopPlayback(); setError('') },
    onAudioReady: blob => { onDraftChange({ audio: blob }); setError('') }, onError: setError,
  })
  useEffect(() => { if (!active) onStopPlayback() }, [active, onStopPlayback])
  useEffect(() => () => { onStopPlayback() }, [onStopPlayback])
  const discard = () => { onStopPlayback(); onDraftChange({ audio: null }) }
  const send = async () => {
    if (disabled || !active || recording.isBusy || (!draft.audio && !draft.text.trim())) return
    setError('')
    await onSubmit(draft.audio ? { audio: draft.audio } : { text: draft.text.trim() })
  }
  const recordLabel = recording.status === 'requesting' ? '取消录音请求' : recording.status === 'encoding' ? '取消音频处理' : recording.isRecording ? '停止录音' : '开始录音'
  return <div className="learning-composer">
    <label>
      <span>{label}</span>
      <textarea value={draft.text} onChange={event => onDraftChange({ text: event.target.value })} rows={3}
        placeholder="可以直接开口，也可以先写下来。" disabled={disabled || !active || recording.isBusy || Boolean(draft.audio)}
        onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send() } }} />
    </label>
    {draft.audio && <div className="learning-inline">
      <span>录音已准备好，发送前可以试听。</span>
      <button type="button" onClick={() => { if (preview?.blob === draft.audio) onPlay(preview.url, 'draft') }} disabled={!active || preview?.blob !== draft.audio}>试听本次录音</button>
      <button type="button" onClick={discard} disabled={disabled || !active}>放弃本次录音</button>
    </div>}
    <div className="learning-actions">
      <button type="button" className="learning-record" onClick={recording.status === 'requesting' || recording.status === 'encoding' ? recording.cancel : recording.isRecording ? recording.stop : recording.start}
        disabled={!active || (!recording.isBusy && disabled)} aria-busy={recording.status === 'requesting' || recording.status === 'encoding'}>{recordLabel}</button>
      <button type="button" className="learning-primary" onClick={() => { void send() }} disabled={!active || disabled || recording.isBusy || (!draft.audio && !draft.text.trim())}>{submitLabel}</button>
    </div>
    {recording.isBusy && <p role="status">{recording.status === 'requesting' ? '等待麦克风授权，可以取消。' : recording.status === 'encoding' ? '正在处理录音，可以取消。' : '正在录音，停止后可以试听再发送。'}</p>}
    {error && <p className="learning-error" role="alert">{error}</p>}
  </div>
}
