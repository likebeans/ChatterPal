import { useEffect, useState } from 'react'
import axios from 'axios'
import { useAudioRecording } from '../hooks/useAudioRecording'
import { useAudioPlayback } from '../hooks/useAudioPlayback'
import { useLatestRequest } from '../hooks/useLatestRequest'
import { useObjectUrls } from '../hooks/useObjectUrls'
import { requestError } from '../api/errors'
import { isAIResult, isBasicResult } from '../api/pronunciation'
import type { AIResult, BasicResult, Correction, Scenario, ScenarioContext } from '../api/pronunciation'

function CorrectionList({ items }: { items: Correction[] }) {
  return <div className="corrections-list">{items.map((correction, index) => (
    <div key={index} className="correction-item">
      <div className="correction-type">{({ grammar: '语法', pronunciation: '发音', vocabulary: '词汇', prosody: '韵律' } as Record<string, string>)[correction.type] || '其他'}</div>
      <div className="correction-content">
        <div className="correction-change"><span className="original">「{correction.original}」</span><span className="arrow">→</span><span className="corrected">「{correction.corrected}」</span></div>
        <p className="correction-explanation">{correction.explanation}</p>
      </div>
    </div>
  ))}</div>
}

function Transcript({ text }: { text: string }) {
  return <div className="text-section"><h4>识别文本</h4><p className="original-text">{text.trim() || '识别文本暂不可用，请重新录音后重试。'}</p></div>
}

function PronunciationCorrection({ active = true }: { active?: boolean }) {
  const [audioBlob, setAudioBlob] = useState<Blob | null>(null)
  const [audioUrl, setAudioUrl] = useState<string | null>(null)
  const [isAnalyzing, setIsAnalyzing] = useState(false)
  const [result, setResult] = useState<BasicResult | null>(null)
  const [aiResult, setAiResult] = useState<AIResult | null>(null)
  const [error, setError] = useState('')
  const [referenceText, setReferenceText] = useState('')
  const [analysisMode, setAnalysisMode] = useState<'basic' | 'ai'>('basic')
  const [scenarios, setScenarios] = useState<Scenario[]>([])
  const [selectedScenario, setSelectedScenario] = useState('')
  const [scenarioContext, setScenarioContext] = useState<ScenarioContext | null>(null)
  const [scenarioError, setScenarioError] = useState('')
  const [scenarioListError, setScenarioListError] = useState('')
  const [scenarioLoading, setScenarioLoading] = useState(false)
  const [scenarioRetry, setScenarioRetry] = useState(0)
  const [userId] = useState(() => 'user_' + Date.now())
  const { begin, cancel: cancelAnalysis, isPending } = useLatestRequest(active)
  const { create: createUrl, revoke: revokeUrl } = useObjectUrls()
  const { audioRef, playingId, play, stop: stopPlayback, error: playbackError } = useAudioPlayback(active)

  const invalidateAnalysis = () => {
    cancelAnalysis()
    setIsAnalyzing(false)
    setResult(null)
    setAiResult(null)
    setError('')
  }
  const discardAudio = () => {
    stopPlayback()
    if (audioUrl) revokeUrl(audioUrl)
    setAudioUrl(null)
    setAudioBlob(null)
  }
  const recording = useAudioRecording({
    active,
    onStart: () => { invalidateAnalysis(); discardAudio() },
    onError: setError,
    onAudioReady: blob => {
      try {
        const url = createUrl(blob)
        setAudioBlob(blob)
        setAudioUrl(url)
        setError('')
      } catch {
        setError('录音回放初始化失败，请重新录制')
      }
    },
  })

  useEffect(() => {
    if (!active) setIsAnalyzing(false)
  }, [active])

  useEffect(() => {
    const controller = new AbortController()
    let current = true
    void axios.get<Scenario[]>('/api/scenarios', { signal: controller.signal }).then(({ data }) => {
      if (current && Array.isArray(data)) {
        setScenarios(data)
        setScenarioListError('')
      }
    }).catch(() => {
      if (current) setScenarioListError('场景列表暂不可用，仍可不选场景进行分析。')
    })
    return () => { current = false; controller.abort() }
  }, [scenarioRetry])

  useEffect(() => {
    const controller = new AbortController()
    let current = true
    setScenarioContext(null)
    setScenarioLoading(false)
    setScenarioError('')
    if (!active || analysisMode !== 'ai' || !selectedScenario) return () => { current = false; controller.abort() }
    const scenario = scenarios.find(item => `${item.scenario_type}-${item.difficulty_level}` === selectedScenario)
    if (!scenario) return () => { current = false; controller.abort() }
    setScenarioLoading(true)
    void axios.get<ScenarioContext>(`/api/scenarios/${encodeURIComponent(scenario.scenario_type)}/${encodeURIComponent(scenario.difficulty_level)}`, { signal: controller.signal }).then(({ data }) => {
      if (current) setScenarioContext(data)
    }).catch(() => {
      if (current) setScenarioError('场景信息暂不可用，请重试；仍可提交分析。')
    }).finally(() => { if (current) setScenarioLoading(false) })
    return () => { current = false; controller.abort() }
  }, [selectedScenario, scenarios, analysisMode, active, scenarioRetry])

  const changeScenario = (value: string) => {
    invalidateAnalysis()
    recording.cancel()
    stopPlayback()
    setScenarioContext(null)
    setSelectedScenario(value)
  }
  const changeMode = (mode: 'basic' | 'ai') => {
    if (mode === analysisMode) return
    invalidateAnalysis()
    recording.cancel()
    stopPlayback()
    setAnalysisMode(mode)
  }

  const analyzeAudio = async () => {
    if (!active || isPending() || recording.isBusy) return
    if (!audioBlob) { setError('请先录制音频'); return }
    if (!referenceText.trim()) { setError('请输入参考文本'); return }
    const request = begin()
    setIsAnalyzing(true)
    setError('')
    setResult(null)
    setAiResult(null)
    stopPlayback()
    try {
      const form = new FormData()
      form.append('audio', audioBlob, 'recording.wav')
      form.append('reference_text', referenceText.trim())
      if (analysisMode === 'ai') {
        if (selectedScenario) form.append('current_scenario', selectedScenario)
        form.append('user_id', userId)
      }
      const { data } = await axios.post<unknown>(analysisMode === 'ai' ? '/api/pronunciation/ai-analyze' : '/api/pronunciation/analyze', form, { signal: request.signal })
      if (!request.isCurrent()) return
      if (analysisMode === 'ai' && isAIResult(data)) setAiResult(data)
      else if (analysisMode === 'basic' && isBasicResult(data)) setResult(data)
      else setError('分析结果暂不可用：服务返回的数据不完整，请重试')
    } catch (failure) {
      if (request.isCurrent()) setError(requestError(failure, '分析失败，请稍后重试'))
    } finally {
      if (request.isCurrent()) { setIsAnalyzing(false); request.finish() }
    }
  }

  const resetRecording = () => {
    invalidateAnalysis()
    recording.cancel()
    discardAudio()
  }
  const label = recording.status === 'requesting' ? '取消录音请求' : recording.status === 'encoding' ? '取消音频处理' : recording.isRecording ? '停止录音' : '开始录音'

  return (
    <div className="pronunciation-correction">
      <div className="correction-header"><h2>口语纠正</h2><p>录制您的语音，我们将为您提供发音和语法建议</p></div>
      <div className="reference-text-section">
        <label htmlFor="reference-text">参考文本</label>
        <textarea id="reference-text" value={referenceText} onChange={event => { invalidateAnalysis(); setReferenceText(event.target.value) }} placeholder="请输入您要练习的英文句子或段落..." className="reference-text-input" rows={3} />
      </div>
      <div className="analysis-mode-section">
        <label>分析模式</label>
        <div className="mode-selector">
          <button type="button" className={`mode-button ${analysisMode === 'basic' ? 'active' : ''}`} aria-pressed={analysisMode === 'basic'} onClick={() => changeMode('basic')}>基础分析</button>
          <button type="button" className={`mode-button ${analysisMode === 'ai' ? 'active' : ''}`} aria-pressed={analysisMode === 'ai'} onClick={() => changeMode('ai')}>AI智能分析</button>
        </div>
      </div>
      {analysisMode === 'ai' && (
        <div className="scenario-section">
          <label htmlFor="scenario-select">练习场景（可选）</label>
          <select id="scenario-select" value={selectedScenario} onChange={event => changeScenario(event.target.value)} className="scenario-select">
            <option value="">选择练习场景...</option>
            {scenarios.map(scenario => <option key={`${scenario.scenario_type}-${scenario.difficulty_level}`} value={`${scenario.scenario_type}-${scenario.difficulty_level}`}>{scenario.display_name}</option>)}
          </select>
          {scenarioLoading && <p role="status">正在加载场景...</p>}
          {(scenarioListError || scenarioError) && <div role="status"><p>{scenarioListError || scenarioError}</p><button type="button" onClick={() => setScenarioRetry(value => value + 1)}>重试加载场景</button></div>}
          {scenarioContext && (
            <div className="scenario-context">
              <h4>场景描述</h4><p>{scenarioContext.context_description}</p>
              {scenarioContext.sample_dialogues?.length > 0 && <div className="sample-dialogues"><h5>示例对话</h5><ul>{scenarioContext.sample_dialogues.map((dialogue, index) => <li key={index}>{dialogue}</li>)}</ul></div>}
              {scenarioContext.key_vocabulary?.length > 0 && <div className="key-vocabulary"><h5>关键词汇</h5><div className="vocabulary-tags">{scenarioContext.key_vocabulary.map((word, index) => <span key={index} className="vocabulary-tag">{word}</span>)}</div></div>}
              {scenarioContext.pronunciation_targets?.length > 0 && <div className="pronunciation-targets"><h5>发音重点</h5><ul>{scenarioContext.pronunciation_targets.map((target, index) => <li key={index}>{target}</li>)}</ul></div>}
            </div>
          )}
        </div>
      )}
      <div className="recording-section">
        <div className="recording-controls">
          <button type="button" onClick={recording.status === 'requesting' || recording.status === 'encoding' ? recording.cancel : recording.isRecording ? recording.stop : recording.start} className={`record-button ${recording.isBusy ? 'stop' : 'start'}`} title={label} aria-label={label} aria-busy={recording.status === 'requesting' || recording.status === 'encoding'} disabled={!active}>{label}</button>
          {audioBlob && !recording.isBusy && <>
            <button type="button" onClick={() => { if (audioUrl) void play(audioUrl, 'recording') }} className="play-button" aria-pressed={playingId === 'recording'} disabled={!active}>{playingId ? '暂停录音' : '播放录音'}</button>
            <button type="button" onClick={() => { void analyzeAudio() }} className="analyze-button" disabled={isAnalyzing || !active} aria-busy={isAnalyzing}>{isAnalyzing ? '分析中...' : '分析语音'}</button>
            <button type="button" onClick={resetRecording} className="reset-button">重新录制</button>
          </>}
        </div>
        {recording.isBusy && <div className="recording-indicator" role="status"><div className="recording-dot" /><span>{recording.status === 'requesting' ? '等待麦克风权限，可取消...' : recording.status === 'encoding' ? '正在处理音频，可取消...' : '正在录音...'}</span></div>}
        {isAnalyzing && <p role="status">正在分析，可重新录制或切换场景取消本次分析。</p>}
        {(error || playbackError) && <div className="error-message" role="alert">{error || playbackError}</div>}
      </div>
      {result && (
        <div className="analysis-results">
          <div className="score-section"><h3>总体评分</h3><div className="score-circle"><span>{result.overall_score}</span></div><p className="score-notice">当前评分尚未校准，仅供练习参考。</p></div>
          <div className="text-comparison"><Transcript text={result.recognized_text} /></div>
          {result.corrections.length > 0 && <div className="corrections-section"><h4>详细建议</h4><CorrectionList items={result.corrections} /></div>}
          {result.suggestions.length > 0 && <div className="personalized-tips-section"><h4>练习建议</h4><ul className="tips-list">{result.suggestions.map((tip, index) => <li key={index} className="tip-item">{tip}</li>)}</ul></div>}
        </div>
      )}
      {aiResult && (
        <div className="ai-analysis-results">
          <div className="ai-score-section">
            <h3>分析结果（AI模式）</h3>
            <p className="score-notice">当前评分尚未校准，仅供练习参考。AI 不可用时服务可能回退基础分析，请以详细分析说明为准。</p>
            <div className="score-grid">
              <div className="score-circle"><span>{aiResult.overall_score}</span><small>总体评分</small></div>
              <div className="confidence-score"><span className="confidence-value">{aiResult.confidence_score}</span><small>服务置信度（未校准）</small></div>
              <div className="difficulty-level"><span className="difficulty-value">{aiResult.difficulty_level || '暂不可用'}</span><small>难度等级</small></div>
            </div>
          </div>
          <div className="ai-text-comparison"><Transcript text={aiResult.recognized_text} /></div>
          {aiResult.grammar_corrections.length > 0 && <div className="grammar-corrections-section"><h4>语法纠正</h4><CorrectionList items={aiResult.grammar_corrections} /></div>}
          {aiResult.pronunciation_feedback.length > 0 && <div className="pronunciation-feedback-section"><h4>发音指导</h4><CorrectionList items={aiResult.pronunciation_feedback} /></div>}
          {aiResult.personalized_tips.length > 0 && <div className="personalized-tips-section"><h4>个性化建议</h4><ul className="tips-list">{aiResult.personalized_tips.map((tip, index) => <li key={index} className="tip-item">{tip}</li>)}</ul></div>}
          {aiResult.scenario_suggestions.length > 0 && <div className="scenario-suggestions-section"><h4>场景建议</h4><div className="scenario-suggestions">{aiResult.scenario_suggestions.map((suggestion, index) => <div key={index} className="scenario-suggestion"><div className="scenario-info"><span className="scenario-name">{suggestion.scenario_type} - {suggestion.difficulty_level}</span><p className="scenario-reason">{suggestion.reason}</p></div></div>)}</div></div>}
          {aiResult.next_scenario && <div className="next-scenario-section"><h4>推荐下一个练习</h4><div className="next-scenario"><span className="next-scenario-name">{aiResult.next_scenario}</span><button type="button" className="apply-scenario-button" onClick={() => changeScenario(aiResult.next_scenario!)} disabled={!scenarios.some(scenario => `${scenario.scenario_type}-${scenario.difficulty_level}` === aiResult.next_scenario)}>应用此场景</button></div></div>}
          <div className="detailed-analysis-section"><h4>详细分析与可用状态</h4><div className="detailed-analysis"><p>{aiResult.detailed_analysis || '服务未提供分析来源说明，无法确认是否完成 AI 分析。'}</p></div></div>
        </div>
      )}
      <audio ref={audioRef} />
    </div>
  )
}

export default PronunciationCorrection
