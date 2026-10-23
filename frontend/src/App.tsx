import { useState } from 'react'
import ChatInterface from './components/ChatInterface.tsx'
import PronunciationCorrection from './components/PronunciationCorrection.tsx'
import './App.css'

function App() {
  const [activeTab, setActiveTab] = useState<'chat' | 'pronunciation'>('chat')

  return (
    <div className="app">
      <header className="app-header">
        <h1>ChatterPal - 口语辅导助手</h1>
        <nav className="tab-nav">
          <button 
            className={`tab-button ${activeTab === 'chat' ? 'active' : ''}`}
            aria-pressed={activeTab === 'chat'}
            aria-controls="chat-panel"
            onClick={() => setActiveTab('chat')}
          >
            对话练习
          </button>
          <button 
            className={`tab-button ${activeTab === 'pronunciation' ? 'active' : ''}`}
            aria-pressed={activeTab === 'pronunciation'}
            aria-controls="pronunciation-panel"
            onClick={() => setActiveTab('pronunciation')}
          >
            口语纠正
          </button>
        </nav>
      </header>
      
      <main className="app-main">
        <section id="chat-panel" className="tab-panel" hidden={activeTab !== 'chat'} aria-label="对话练习">
          <ChatInterface active={activeTab === 'chat'} />
        </section>
        <section id="pronunciation-panel" className="tab-panel" hidden={activeTab !== 'pronunciation'} aria-label="口语纠正">
          <PronunciationCorrection active={activeTab === 'pronunciation'} />
        </section>
      </main>
    </div>
  )
}

export default App