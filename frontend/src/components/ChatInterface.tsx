import React, { useState, useRef, useEffect } from 'react'
import axios from 'axios'
import AudioRecorder from './AudioRecorder'
import MessageBubble from './MessageBubble'

interface Message {
  id: string
  type: 'user' | 'assistant'
  content: string
  audio?: string
  timestamp: Date
  hasAudio?: boolean
  recognizedText?: string
}

interface VoiceOption {
  id: string
  name: string
}

const ChatInterface: React.FC = () => {
  const [messages, setMessages] = useState<Message[]>([])
  const [inputText, setInputText] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [selectedVoice, setSelectedVoice] = useState('longxiaochun')
  const [voices, setVoices] = useState<VoiceOption[]>([])
  const [audioError, setAudioError] = useState('')
  const [isTranscribing, setIsTranscribing] = useState(false)
  const [currentPlayingId, setCurrentPlayingId] = useState<string | null>(null)
  const [isAudioPlaying, setIsAudioPlaying] = useState(false)
  const [sessionId, setSessionId] = useState<string | null>(null)
  
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const audioRef = useRef<HTMLAudioElement>(null)

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages])

  useEffect(() => {
    // 获取音色列表
    const fetchVoices = async () => {
      try {
        const response = await axios.get('/api/voices')
        if (response.data && Array.isArray(response.data)) {
          const voiceOptions = response.data.map((voice: any) => ({
            id: voice.id,
            name: voice.name
          }))
          setVoices(voiceOptions)
          if (voiceOptions.length > 0 && !selectedVoice) {
            setSelectedVoice(voiceOptions[0].id)
          }
        }
      } catch (error) {
        console.error('获取音色列表失败:', error)
        // 使用默认音色列表作为备选
        setVoices([
          { id: 'longxiaochun', name: '龙小春' },
          { id: 'longfei', name: '龙飞' },
          { id: 'longtian', name: '龙天' },
          { id: 'longxiaoxia', name: '龙小夏' }
        ])
      }
    }
    fetchVoices()
  }, [])

  const sendMessage = async (messageText?: string) => {
    const textToSend = messageText || inputText
    if (!textToSend.trim() || isLoading) return

    const userMessage: Message = {
      id: Date.now().toString(),
      type: 'user',
      content: textToSend,
      hasAudio: false,
      timestamp: new Date()
    }

    setMessages(prev => [...prev, userMessage])
    setInputText('')
    setIsLoading(true)

    try {
      const response = await axios.post('/api/chat', {
        message: textToSend,
        voice: selectedVoice,
        session_id: sessionId
      })

      // 保存会话ID
      if (response.data.session_id && !sessionId) {
        setSessionId(response.data.session_id)
      }

      const assistantMessage: Message = {
        id: (Date.now() + 1).toString(),
        type: 'assistant',
        content: response.data.response,
        audio: response.data.audio_url,
        hasAudio: !!response.data.audio_url,
        timestamp: new Date()
      }

      setMessages(prev => [...prev, assistantMessage])
    } catch (error) {
      console.error('发送消息失败:', error)
      const errorMessage: Message = {
        id: (Date.now() + 1).toString(),
        type: 'assistant',
        content: '抱歉，发生了错误，请稍后重试。',
        hasAudio: false,
        timestamp: new Date()
      }
      setMessages(prev => [...prev, errorMessage])
    } finally {
      setIsLoading(false)
    }
  }

  const handleAudioReady = async (audioBlob: Blob) => {
    setIsTranscribing(true)
    setAudioError('')
    
    // 将用户录音转换为可播放的URL
    const userAudioUrl = URL.createObjectURL(audioBlob)
    
    try {
      const formData = new FormData()
      // 根据实际的MIME类型设置文件扩展名
      const mimeType = audioBlob.type
      let fileName = 'recording.wav'
      if (mimeType.includes('webm')) {
        fileName = 'recording.webm'
      } else if (mimeType.includes('ogg')) {
        fileName = 'recording.ogg'
      } else if (mimeType.includes('mp4')) {
        fileName = 'recording.mp4'
      }
      formData.append('audio', audioBlob, fileName)
      formData.append('voice', selectedVoice)
      if (sessionId) {
        formData.append('session_id', sessionId)
      }
      
      const response = await axios.post('/api/chat/audio', formData, {
        headers: {
          'Content-Type': 'multipart/form-data'
        }
      })

      // 保存会话ID
      if (response.data.session_id && !sessionId) {
        setSessionId(response.data.session_id)
      }
      
      if (response.data.recognized_text && response.data.response) {
        // 创建用户语音消息（包含音频和识别文本）
        const userMessage: Message = {
          id: Date.now().toString(),
          type: 'user',
          content: response.data.recognized_text, // 用户语音识别结果
          audio: userAudioUrl, // 用户录音的URL
          hasAudio: true,
          recognizedText: response.data.recognized_text,
          timestamp: new Date()
        }
        
        setMessages(prev => [...prev, userMessage])
        
        // 创建助手回复消息（包含语音和文本）
        const assistantMessage: Message = {
          id: (Date.now() + 1).toString(),
          type: 'assistant',
          content: response.data.response, // AI回复文本
          audio: response.data.audio_url,
          hasAudio: !!response.data.audio_url,
          timestamp: new Date()
        }
        
        setMessages(prev => [...prev, assistantMessage])
      }
    } catch (error) {
      console.error('语音转文本失败:', error)
      setAudioError('语音识别失败，请重试')
    } finally {
      setIsTranscribing(false)
    }
  }

  const handleAudioError = (error: string) => {
    setAudioError(error)
  }

  const clearConversation = () => {
    setMessages([])
    setSessionId(null)
  }

  const playAudio = (audioData: string, messageId: string) => {
    if (audioRef.current && audioData) {
      // 如果当前正在播放同一个音频，则暂停
      if (currentPlayingId === messageId && isAudioPlaying) {
        audioRef.current.pause()
        setIsAudioPlaying(false)
        setCurrentPlayingId(null)
        return
      }
      
      // 如果正在播放其他音频，先停止
      if (isAudioPlaying) {
        audioRef.current.pause()
      }
      
      // 检查是否是URL还是base64数据
      if (audioData.startsWith('http') || audioData.startsWith('/')) {
        // 如果是URL，直接使用
        audioRef.current.src = audioData
      } else {
        // 如果是base64数据，添加前缀
        audioRef.current.src = `data:audio/mp3;base64,${audioData}`
      }
      
      setCurrentPlayingId(messageId)
      setIsAudioPlaying(true)
      
      audioRef.current.play().catch(console.error)
      
      // 监听播放结束事件
      audioRef.current.onended = () => {
        setIsAudioPlaying(false)
        setCurrentPlayingId(null)
      }
    }
  }

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  return (
    <div className="chat-interface">
      <div className="chat-header">
        <h2>对话练习</h2>
        <div className="header-controls">
          <div className="voice-selector">
            <label htmlFor="voice-select">选择音色：</label>
            <select 
              id="voice-select"
              value={selectedVoice} 
              onChange={(e) => setSelectedVoice(e.target.value)}
              className="voice-select"
            >
              {voices.map(voice => (
                <option key={voice.id} value={voice.id}>
                  {voice.name}
                </option>
              ))}
            </select>
          </div>
          <button 
            onClick={clearConversation}
            className="clear-button"
            title="开始新对话"
          >
            🗑️ 清除对话
          </button>
        </div>
      </div>

      <div className="messages-container">
        {messages.map(message => (
          <MessageBubble
            key={message.id}
            message={message}
            onPlayAudio={(audioData) => playAudio(audioData, message.id)}
            isPlaying={currentPlayingId === message.id && isAudioPlaying}
          />
        ))}
        {isLoading && (
          <div className="message assistant loading">
            <div className="message-content">
              <p>正在思考中...</p>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className="input-container">
        <div className="input-row">
          <AudioRecorder
            onAudioReady={handleAudioReady}
            onError={handleAudioError}
            disabled={isLoading || isTranscribing}
            className="voice-input-left"
          />
          <div className="input-wrapper">
            <textarea
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              onKeyPress={handleKeyPress}
              placeholder="输入您想要练习的对话内容..."
              className="message-input"
              rows={3}
              disabled={isLoading || isTranscribing}
            />
            <button 
              onClick={() => sendMessage()}
              disabled={!inputText.trim() || isLoading || isTranscribing}
              className="send-button-inside"
            >
              {isLoading ? '发送中...' : isTranscribing ? '识别中...' : '发送'}
            </button>
          </div>
        </div>
        {audioError && (
          <div className="audio-error">
            ❌ {audioError}
          </div>
        )}
      </div>

      <audio ref={audioRef} />
    </div>
  )
}

export default ChatInterface