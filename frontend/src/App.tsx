import { useState } from 'react'
import ChatInterface from './components/ChatInterface.tsx'
import PronunciationCorrection from './components/PronunciationCorrection.tsx'
import LearningWorkspace from './learning/LearningWorkspace'
import './App.css'

function App({ initialTab = 'learning' }: { initialTab?: 'learning' | 'chat' | 'pronunciation' }) {
  const [activeTab, setActiveTab] = useState(initialTab)
  const [learningOpened, setLearningOpened] = useState(initialTab === 'learning')

  return (
    <div className="app">
      <header className="app-header">
        <h1>ChatterPal - 口语辅导助手</h1>
        <nav className="tab-nav" aria-label="练习入口">
          <button
            className={`tab-button ${activeTab === 'learning' ? 'active' : ''}`}
            aria-pressed={activeTab === 'learning'}
            aria-controls="learning-panel"
            onClick={() => { setLearningOpened(true); setActiveTab('learning') }}
          >
            今日练习
          </button>
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
        <section id="learning-panel" className="tab-panel" hidden={activeTab !== 'learning'} aria-label="任务学习">
          {learningOpened && <LearningWorkspace active={activeTab === 'learning'} />}
        </section>
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