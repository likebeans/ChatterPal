import React from 'react'

interface MessageBubbleProps {
  message: {
    id: string
    type: 'user' | 'assistant'
    content: string
    audio?: string
    timestamp: Date
    hasAudio?: boolean
    recognizedText?: string
  }
  onPlayAudio: (audioData: string) => void
  isPlaying?: boolean
}

const MessageBubble: React.FC<MessageBubbleProps> = ({ message, onPlayAudio, isPlaying = false }) => {
  const handlePlayAudio = () => {
    if (message.audio) {
      onPlayAudio(message.audio)
    }
  }

  return (
    <div className={`message-bubble ${message.type}`}>
      {/* 语音气泡 - 用户语音输入或助手语音回复时显示 */}
      {message.hasAudio && (
        <div className="audio-bubble">
          <button 
            className={`audio-play-btn ${isPlaying ? 'playing' : ''}`}
            onClick={handlePlayAudio}
            disabled={!message.audio}
            aria-label={`${isPlaying ? '暂停' : '播放'}${message.type === 'user' ? '用户录音' : '助手语音'}`}
            aria-pressed={isPlaying}
          >
            <div className="audio-icon" aria-hidden="true">
              {isPlaying ? (
                <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M6 4h4v16H6V4zm8 0h4v16h-4V4z"/>
                </svg>
              ) : (
                <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M8 5v14l11-7z"/>
                </svg>
              )}
            </div>
            <span className="audio-duration">{isPlaying ? '暂停' : '播放语音'}</span>
          </button>
        </div>
      )}
      
      {/* 文本气泡 */}
      <div className="text-bubble">
        <p>{message.content}</p>
      </div>
      
      {/* 时间戳 */}
      <div className="message-time">
        {message.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
      </div>
    </div>
  )
}

export default MessageBubble