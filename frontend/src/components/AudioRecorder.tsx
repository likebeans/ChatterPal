import { useEffect } from 'react'
import { useAudioRecording } from '../hooks/useAudioRecording'

interface AudioRecorderProps {
  onAudioReady: (audioBlob: Blob) => void
  onError: (error: string) => void
  onBusyChange?: (busy: boolean) => void
  disabled?: boolean
  active?: boolean
  resetKey?: number
  className?: string
}

function AudioRecorder({ onAudioReady, onError, onBusyChange, disabled = false, active = true, resetKey = 0, className = '' }: AudioRecorderProps) {
  const { status, start, stop, cancel, isBusy } = useAudioRecording({ active, onAudioReady, onError })
  useEffect(() => { cancel() }, [resetKey, cancel])
  useEffect(() => { onBusyChange?.(isBusy) }, [isBusy, onBusyChange])
  const requesting = status === 'requesting'
  const recording = status === 'recording'
  const encoding = status === 'encoding'
  const label = requesting ? '取消录音请求' : encoding ? '取消音频处理' : recording ? '停止录音' : '开始录音'

  return (
    <div className={`audio-recorder ${className}`}>
      <div className="recording-controls">
        <button
          type="button"
          onClick={requesting || encoding ? cancel : recording ? stop : start}
          className={`record-button ${isBusy ? 'stop recording' : 'start'}`}
          disabled={!active || (!isBusy && disabled)}
          title={label}
          aria-label={label}
          aria-busy={requesting || encoding}
        >
          <span aria-hidden="true">{isBusy ? '■' : '●'}</span>
        </button>
      </div>
      {isBusy && (
        <div className="recording-indicator" role="status">
          <div className="recording-dot" />
          <span>{requesting ? '等待麦克风权限，可取消...' : encoding ? '正在处理音频，可取消...' : '录音中...'}</span>
        </div>
      )}
    </div>
  )
}

export default AudioRecorder
