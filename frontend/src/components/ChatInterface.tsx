import { useEffect, useRef, useState } from 'react'
import axios from 'axios'
import AudioRecorder from './AudioRecorder'
import MessageBubble from './MessageBubble'
import { Icon } from './Icon'
import { useLatestRequest } from '../hooks/useLatestRequest'
import { useObjectUrls } from '../hooks/useObjectUrls'
import { useAudioPlayback } from '../hooks/useAudioPlayback'
import { requestError } from '../api/errors'

interface Message {
  id: string
  type: 'user' | 'assistant'
  content: string
  audio?: string
  timestamp: Date
  hasAudio?: boolean
}
interface VoiceOption { id: string; name: string }
interface ChatResponse { response: string; session_id: string; audio_url?: string | null; recognized_text?: string }
const fallbackVoices: VoiceOption[] = [
  { id: 'longxiaochun', name: '龙小春' },
  { id: 'longfei', name: '龙飞' },
  { id: 'longtian', name: '龙天' },
  { id: 'longxiaoxia', name: '龙小夏' },
]

function ChatInterface({ active = true }: { active?: boolean }) {
  const [messages, setMessages] = useState<Message[]>([])
  const [inputText, setInputText] = useState('')
  const [pending, setPending] = useState<'text' | 'audio' | null>(null)
  const [selectedVoice, setSelectedVoice] = useState('longxiaochun')
  const [voices, setVoices] = useState<VoiceOption[]>(fallbackVoices)
  const [error, setError] = useState('')
  const [voiceNotice, setVoiceNotice] = useState('')
  const [recordingBusy, setRecordingBusy] = useState(false)
  const [recordingReset, setRecordingReset] = useState(0)
  const sessionRef = useRef<string | null>(null)
  const idRef = useRef(0)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  // Keep submitted turns alive while the panel is hidden: the server may already
  // have added their replies to this session before the HTTP response arrives.
  const { begin, cancel, isPending } = useLatestRequest()
  const { create: createUrl, revoke: revokeUrl, clear: clearUrls } = useObjectUrls()
  const { audioRef, playingId, play, stop: stopPlayback, error: playbackError } = useAudioPlayback(active)
  const nextId = () => String(++idRef.current)

  useEffect(() => {
    if (active) messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, active])

  useEffect(() => {
    const controller = new AbortController()
    let current = true
    void axios.get<VoiceOption[]>('/api/voices', { signal: controller.signal }).then(({ data }) => {
      if (!current || !Array.isArray(data) || data.length === 0) return
      setVoices(data)
      setSelectedVoice(voice => data.some(option => option.id === voice) ? voice : data[0].id)
    }).catch(() => {
      if (current) setVoiceNotice('音色列表暂不可用，使用默认音色；您仍可发送消息。')
    })
    return () => { current = false; controller.abort() }
  }, [])

  useEffect(() => {
    if (!active) setRecordingBusy(false)
  }, [active])

  const sendMessage = async () => {
    const text = inputText.trim()
    if (!active || !text || isPending() || recordingBusy) return
    const request = begin()
    setMessages(previous => [...previous, { id: nextId(), type: 'user', content: text, timestamp: new Date() }])
    setInputText('')
    setPending('text')
    setError('')
    try {
      const { data } = await axios.post<ChatResponse>('/api/chat', {
        message: text, voice: selectedVoice, session_id: sessionRef.current,
      }, { signal: request.signal })
      if (!request.isCurrent()) return
      if (typeof data.response !== 'string' || !data.response.trim()) throw new Error('Empty reply')
      sessionRef.current = data.session_id || sessionRef.current
      setMessages(previous => [...previous, {
        id: nextId(), type: 'assistant', content: data.response,
        audio: data.audio_url || undefined, hasAudio: Boolean(data.audio_url), timestamp: new Date(),
      }])
    } catch (failure) {
      if (request.isCurrent()) setError(requestError(failure, '发送消息失败，请重试'))
    } finally {
      if (request.isCurrent()) { setPending(null); request.finish() }
    }
  }

  const handleAudioReady = async (blob: Blob) => {
    if (!active || isPending()) return
    const request = begin()
    let ownedUrl: string | null = null
    setPending('audio')
    setError('')
    try {
      const form = new FormData()
      form.append('audio', blob, 'recording.wav')
      form.append('voice', selectedVoice)
      if (sessionRef.current) form.append('session_id', sessionRef.current)
      ownedUrl = createUrl(blob)
      const { data } = await axios.post<ChatResponse>('/api/chat/audio', form, { signal: request.signal })
      if (!request.isCurrent()) return
      const recognized = data.recognized_text?.trim()
      if (!recognized || /^(语音识别失败|识别失败)$/.test(recognized)) {
        throw new Error('Speech recognition unavailable')
      }
      if (typeof data.response !== 'string' || !data.response.trim()) throw new Error('Empty reply')
      sessionRef.current = data.session_id || sessionRef.current
      const url = ownedUrl
      setMessages(previous => [...previous,
        { id: nextId(), type: 'user', content: recognized, audio: url || undefined, hasAudio: true, timestamp: new Date() },
        { id: nextId(), type: 'assistant', content: data.response, audio: data.audio_url || undefined, hasAudio: Boolean(data.audio_url), timestamp: new Date() },
      ])
      ownedUrl = null // The displayed user message now owns the URL until clear/unmount.
    } catch (failure) {
      if (request.isCurrent()) setError(requestError(failure, '语音识别或回复暂不可用，请重新录音后重试'))
    } finally {
      if (ownedUrl) revokeUrl(ownedUrl)
      if (request.isCurrent()) { setPending(null); request.finish() }
    }
  }

  const clearConversation = () => {
    cancel()
    stopPlayback()
    clearUrls()
    sessionRef.current = null
    setMessages([])
    setInputText('')
    setError('')
    setPending(null)
    setRecordingBusy(false)
    setRecordingReset(value => value + 1)
  }
  const busy = pending !== null || recordingBusy

  return (
    <div className="chat-interface">
      <div className="chat-header">
        <div className="chat-heading">
          <p className="section-eyebrow">CONVERSATION STUDIO</p>
          <h2>对话练习</h2>
          <p>从熟悉的话题开始，用自己的节奏开口。</p>
        </div>
        <div className="header-controls">
          <div className="voice-selector">
            <label htmlFor="voice-select">选择音色：</label>
            <select id="voice-select" value={selectedVoice} onChange={event => setSelectedVoice(event.target.value)} className="voice-select" disabled={busy}>
              {voices.map(voice => <option key={voice.id} value={voice.id}>{voice.name}</option>)}
            </select>
          </div>
          <button type="button" onClick={clearConversation} className="clear-button" title="开始新对话"><Icon name="review" size={16} />清除对话</button>
        </div>
      </div>
      <div className="messages-container" role="log" aria-label="对话消息" aria-busy={pending !== null}>
        {messages.length === 0 && !pending && <div className="chat-empty">
          <span className="chat-empty-icon"><Icon name="chat" size={26} /></span>
          <h3>今天，想聊点什么？</h3>
          <p>打字或点击麦克风开始对话。可以聊聊今天的计划，也可以练习一个日常场景。</p>
          <p className="chat-empty-example" lang="en">“I’d like to talk about my weekend.”</p>
        </div>}
        {messages.map(message => <MessageBubble key={message.id} message={message} onPlayAudio={source => { void play(source, message.id) }} isPlaying={playingId === message.id} />)}
        {pending && <div className="message assistant loading" role="status"><div className="message-content"><Icon name="clock" size={16} /><p>{pending === 'text' ? '正在思考中...' : '正在识别语音...'}</p></div></div>}
        <div ref={messagesEndRef} />
      </div>
      <div className="input-container">
        <label htmlFor="chat-message" className="composer-label">对话内容</label>
        <div className="input-row">
          <AudioRecorder onAudioReady={handleAudioReady} onError={setError} onBusyChange={setRecordingBusy} active={active} resetKey={recordingReset} disabled={pending !== null} className="voice-input-left" />
          <div className="input-wrapper">
            <textarea
              id="chat-message" aria-label="对话内容" aria-describedby="chat-message-hint" value={inputText} onChange={event => setInputText(event.target.value)}
              onKeyDown={event => {
                if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void sendMessage() }
              }}
              placeholder="输入您想要练习的对话内容..." className="message-input" rows={3} disabled={busy || !active}
            />
            <button type="button" onClick={() => { void sendMessage() }} disabled={!inputText.trim() || busy || !active} className="send-button-inside" aria-busy={pending !== null}>
              {pending === 'text' ? '发送中...' : pending === 'audio' ? '识别中...' : '发送'}<Icon name="arrow-right" size={16} />
            </button>
          </div>
        </div>
        <p id="chat-message-hint" className="composer-hint">Enter 发送 · Shift + Enter 换行 · 点击麦克风录音，再次点击结束并发送</p>
        {voiceNotice && <p className="availability-notice" role="status">{voiceNotice}</p>}
        {(error || playbackError) && <div className="audio-error" role="alert">{error || playbackError}</div>}
      </div>
      <audio ref={audioRef} />
    </div>
  )
}

export default ChatInterface
