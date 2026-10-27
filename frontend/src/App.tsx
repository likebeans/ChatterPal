import { useState } from 'react'
import ChatInterface from './components/ChatInterface.tsx'
import PronunciationCorrection from './components/PronunciationCorrection.tsx'
import LearningWorkspace from './learning/LearningWorkspace'
import { Icon } from './components/Icon'
import './App.css'

function App({ initialTab = 'learning' }: { initialTab?: 'learning' | 'chat' | 'pronunciation' }) {
  const [activeTab, setActiveTab] = useState(initialTab)
  const [learningOpened, setLearningOpened] = useState(initialTab === 'learning')

  return (
    <div className="app">
      <a className="skip-link" href="#practice-main">跳到练习内容</a>
      <header className="app-header">
        <div className="app-header-inner">
        <div className="app-brand"><span className="app-brand-glyph"><Icon name="chat" size={23} /></span><div><h1>ChatterPal</h1><p>让每一次开口，都有收获</p></div></div>
        <nav className="tab-nav" aria-label="练习入口">
          <button
            className={`tab-button ${activeTab === 'learning' ? 'active' : ''}`}
            aria-pressed={activeTab === 'learning'}
            aria-controls="learning-panel"
            onClick={() => { setLearningOpened(true); setActiveTab('learning') }}
          >
            <Icon name="book" size={18} />今日练习
          </button>
          <button
            className={`tab-button ${activeTab === 'chat' ? 'active' : ''}`}
            aria-pressed={activeTab === 'chat'}
            aria-controls="chat-panel"
            onClick={() => setActiveTab('chat')}
          >
            <Icon name="chat" size={18} />对话练习
          </button>
          <button 
            className={`tab-button ${activeTab === 'pronunciation' ? 'active' : ''}`}
            aria-pressed={activeTab === 'pronunciation'}
            aria-controls="pronunciation-panel"
            onClick={() => setActiveTab('pronunciation')}
          >
            <Icon name="mic" size={18} />口语纠正
          </button>
        </nav>
        <span className="app-header-note"><Icon name="headphones" size={17} />你的口语练习空间</span>
        </div>
      </header>

      <main className="app-main" id="practice-main" tabIndex={-1}>
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