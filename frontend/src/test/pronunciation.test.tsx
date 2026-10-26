import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axios from 'axios'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import PronunciationCorrection from '../components/PronunciationCorrection'
import { aiResult, basicResult, recordPronunciation, scenarioContext, scenarios } from './flows'
import { deferred, objectUrls } from './setup'

beforeEach(() => {
  vi.spyOn(axios, 'get').mockImplementation(url => Promise.resolve({ data: url === '/api/scenarios' ? scenarios : scenarioContext('Current scenario') }))
})

function reference(text = 'I went home.') {
  fireEvent.change(screen.getByLabelText('参考文本'), { target: { value: text } })
}
function analyze() {
  fireEvent.click(screen.getByRole('button', { name: /分析语音/ }))
}
async function prepareAI() {
  fireEvent.click(screen.getByRole('button', { name: 'AI智能分析' }))
  await screen.findByRole('option', { name: 'Scenario A' })
  fireEvent.change(screen.getByLabelText('练习场景（可选）'), { target: { value: 'daily_conversation-beginner' } })
  reference()
  await recordPronunciation()
}

describe('pronunciation API contracts and recovery', () => {
  it('accepts explicit unknown scores and shows evidence scope without numerical meters', async () => {
    vi.spyOn(axios, 'post').mockResolvedValue({ data: {
      ...aiResult,
      overall_score: null,
      confidence_score: null,
      difficulty_level: null,
      next_scenario: null,
      calibrated: false,
      scope: '仅识别文本对齐；未校准，不能判断音素准确率。',
      dimensions: {
        pronunciation: { score: null, status: 'unavailable', source: 'none', reason: '尚未进行声学音素对齐', evidence: {}, unit: null, calibrated: false },
        accuracy: { score: 75, status: 'measured', source: 'text_sequence_alignment', reason: '识别文本与参考文本的序列匹配', evidence: { deletions: 1 }, unit: 'percent', calibrated: false },
      },
    } })
    const { container } = render(<PronunciationCorrection />)
    await prepareAI()
    analyze()
    expect(await screen.findByText(aiResult.recognized_text, { selector: 'p' })).toBeVisible()
    expect(screen.getByText('尚未进行声学音素对齐')).toBeVisible()
    expect(screen.getByText('仅识别文本对齐；未校准，不能判断音素准确率。')).toBeVisible()
    expect(screen.getByText(/文本序列匹配.*75/)).toBeVisible()
    expect(container.querySelector('.score-circle, .confidence-score, .difficulty-level')).toBeNull()
    expect(screen.queryByRole('meter')).not.toBeInTheDocument()
  })

  it('rejects nonfinite nested evidence instead of displaying a misleading measured result', async () => {
    vi.spyOn(axios, 'post').mockResolvedValue({ data: { ...basicResult, dimensions: {
      accuracy: { score: 95, status: 'measured', source: 'text_sequence_alignment', reason: 'Text only', evidence: { edit_distance: NaN }, calibrated: false },
    } } })
    render(<PronunciationCorrection />)
    reference()
    await recordPronunciation()
    analyze()
    expect(await screen.findByRole('alert')).toHaveTextContent('服务返回的数据不完整')
    expect(screen.queryByText(basicResult.recognized_text, { selector: 'p' })).not.toBeInTheDocument()
  })

  it('hides legacy scores and ability labels when their measurement evidence is absent', async () => {
    vi.spyOn(axios, 'post').mockResolvedValue({ data: aiResult })
    const { container } = render(<PronunciationCorrection />)
    await prepareAI()
    analyze()
    await screen.findByText(aiResult.recognized_text, { selector: 'p' })
    expect(container.querySelector('.score-circle, .confidence-score, .difficulty-level')).toBeNull()
    expect(screen.getByText(/服务未提供测量依据/)).toBeVisible()
  })

  it.each([NaN, Infinity, -1, 101])('rejects invalid numeric output %s and retains the recording for retry', async invalid => {
    vi.spyOn(axios, 'post').mockResolvedValueOnce({ data: { ...basicResult, overall_score: invalid } }).mockResolvedValue({ data: { ...basicResult, overall_score: null } })
    render(<PronunciationCorrection />)
    reference()
    await recordPronunciation()
    analyze()
    expect(await screen.findByRole('alert')).toHaveTextContent('服务返回的数据不完整')
    analyze()
    expect(await screen.findByText(basicResult.recognized_text, { selector: 'p' })).toBeVisible()
  })

  it('clears a failed scenario context when choosing no scenario', async () => {
    vi.mocked(axios.get).mockImplementation(url => url === '/api/scenarios' ? Promise.resolve({ data: scenarios }) : Promise.reject(new Error('Offline')))
    render(<PronunciationCorrection />)
    fireEvent.click(screen.getByRole('button', { name: 'AI智能分析' }))
    await screen.findByRole('option', { name: 'Scenario A' })
    fireEvent.change(screen.getByLabelText('练习场景（可选）'), { target: { value: 'daily_conversation-beginner' } })
    await screen.findByText('场景信息暂不可用，请重试；仍可提交分析。')
    fireEvent.change(screen.getByLabelText('练习场景（可选）'), { target: { value: '' } })
    expect(screen.queryByText('场景信息暂不可用，请重试；仍可提交分析。')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '重试加载场景' })).not.toBeInTheDocument()
  })

  it('clears the scenario-list warning after a successful retry without requiring a selection', async () => {
    vi.mocked(axios.get).mockRejectedValueOnce(new Error('Offline')).mockResolvedValue({ data: scenarios })
    render(<PronunciationCorrection />)
    fireEvent.click(screen.getByRole('button', { name: 'AI智能分析' }))
    await screen.findByText('场景列表暂不可用，仍可不选场景进行分析。')
    fireEvent.click(screen.getByRole('button', { name: '重试加载场景' }))
    await screen.findByRole('option', { name: 'Scenario A' })
    expect(screen.queryByText('场景列表暂不可用，仍可不选场景进行分析。')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '重试加载场景' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('练习场景（可选）')).toHaveValue('')
  })

  it('clears playback errors after successful retry without clearing analysis errors', async () => {
    render(<PronunciationCorrection />)
    await recordPronunciation()
    vi.mocked(HTMLMediaElement.prototype.play).mockRejectedValueOnce(new Error('Unavailable'))
    fireEvent.click(screen.getByRole('button', { name: '播放录音' }))
    await screen.findByRole('alert')
    fireEvent.click(screen.getByRole('button', { name: '播放录音' }))
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '分析语音' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('请输入参考文本')
    fireEvent.click(screen.getByRole('button', { name: '暂停录音' }))
    fireEvent.click(screen.getByRole('button', { name: '播放录音' }))
    expect(screen.getByRole('alert')).toHaveTextContent('请输入参考文本')
  })

  it('renders all AI response fields and fallback explanation without claiming calibrated results', async () => {
    const post = vi.spyOn(axios, 'post').mockResolvedValue({ data: aiResult })
    render(<PronunciationCorrection />)
    await prepareAI()
    analyze()
    expect(await screen.findByText(aiResult.recognized_text, { selector: 'p' })).toBeVisible()
    expect(screen.getByText('Use the plural verb.')).toBeVisible()
    expect(screen.getByText('Stress the first syllable.')).toBeVisible()
    expect(screen.getByText('Read aloud slowly.')).toBeVisible()
    expect(screen.getByText(aiResult.detailed_analysis)).toBeVisible()
    expect(screen.getByText(/当前评分尚未校准/)).toBeVisible()
    const form = post.mock.calls[0][1] as FormData
    expect(post.mock.calls[0][0]).toBe('/api/pronunciation/ai-analyze')
    expect(form.get('current_scenario')).toBe('daily_conversation-beginner')
    expect(form.get('reference_text')).toBe('I went home.')
    expect(form.get('user_id')).toMatch(/^user_/)
    fireEvent.click(screen.getByRole('button', { name: '应用此场景' }))
    expect(screen.getByLabelText('练习场景（可选）')).toHaveValue('daily_conversation-intermediate')
    expect(screen.queryByText(aiResult.recognized_text, { selector: 'p' })).not.toBeInTheDocument()
  })

  it('preserves the current scenario context and completed analysis when its recommendation is applied again', async () => {
    const user = userEvent.setup()
    const currentResult = { ...aiResult, next_scenario: 'daily_conversation-beginner' }
    vi.spyOn(axios, 'post').mockResolvedValue({ data: currentResult })
    render(<PronunciationCorrection />)
    await prepareAI()
    await screen.findByText('Current scenario')
    analyze()
    await screen.findByText(currentResult.recognized_text, { selector: 'p' })

    await user.click(screen.getByRole('button', { name: '应用此场景' }))

    expect(screen.getByLabelText('练习场景（可选）')).toHaveValue('daily_conversation-beginner')
    expect(screen.getByText('Current scenario')).toBeVisible()
    expect(screen.getByText(currentResult.recognized_text, { selector: 'p' })).toBeVisible()
    expect(screen.getByText(currentResult.detailed_analysis)).toBeVisible()
  })

  it('reports incomplete API data and permits retry with the same recording', async () => {
    vi.spyOn(axios, 'post').mockResolvedValueOnce({ data: { score: 80, original_text: 'old contract' } }).mockResolvedValue({ data: basicResult })
    render(<PronunciationCorrection />)
    reference()
    await recordPronunciation()
    analyze()
    expect(await screen.findByRole('alert')).toHaveTextContent('服务返回的数据不完整')
    expect(screen.queryByText('old contract')).not.toBeInTheDocument()
    analyze()
    expect(await screen.findByText(basicResult.recognized_text, { selector: 'p' })).toBeVisible()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(objectUrls.size).toBe(1)
  })

  it('shows service errors and an explicit unavailable transcript, with successful retry', async () => {
    vi.spyOn(axios, 'post').mockRejectedValueOnce({ isAxiosError: true, response: { data: { detail: 'ASR provider unavailable' } } }).mockResolvedValue({ data: { ...basicResult, recognized_text: '' } })
    render(<PronunciationCorrection />)
    reference()
    await recordPronunciation()
    analyze()
    expect(await screen.findByRole('alert')).toHaveTextContent('ASR provider unavailable')
    analyze()
    expect(await screen.findByText('识别文本暂不可用，请重新录音后重试。')).toBeVisible()
    expect(screen.queryByText('I went home.', { selector: 'p' })).not.toBeInTheDocument()
  })

  it('retains the recording, reference and completed analysis across app tabs', async () => {
    vi.spyOn(axios, 'post').mockResolvedValue({ data: basicResult })
    render(<App initialTab="chat" />)
    fireEvent.click(screen.getByRole('button', { name: '口语纠正' }))
    reference()
    await recordPronunciation()
    analyze()
    await screen.findByText(basicResult.recognized_text, { selector: 'p' })
    fireEvent.click(screen.getByRole('button', { name: '对话练习' }))
    fireEvent.click(screen.getByRole('button', { name: '口语纠正' }))
    expect(screen.getByText(basicResult.recognized_text, { selector: 'p' })).toBeVisible()
    expect(screen.getByLabelText('参考文本')).toHaveValue('I went home.')
    expect(screen.getByRole('button', { name: '播放录音' })).toBeEnabled()
    expect(objectUrls.size).toBe(1)
  })
})

describe('pronunciation request generations', () => {
  it.each(['reference', 'mode', 'scenario'] as const)('%s changes abort and ignore a late AI result even if the provider ignores cancellation', async change => {
    const pending = deferred<{ data: typeof aiResult }>()
    const post = vi.spyOn(axios, 'post').mockReturnValue(pending.promise)
    render(<PronunciationCorrection />)
    await prepareAI()
    analyze()
    if (change === 'reference') reference('A newer reference sentence.')
    else if (change === 'mode') fireEvent.click(screen.getByRole('button', { name: '基础分析' }))
    else fireEvent.change(screen.getByLabelText('练习场景（可选）'), { target: { value: 'business_meeting-intermediate' } })
    expect(post.mock.calls[0][2]?.signal?.aborted).toBe(true)
    await act(async () => pending.resolve({ data: aiResult }))
    expect(screen.queryByText(aiResult.recognized_text, { selector: 'p' })).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '分析语音' })).toBeEnabled()
  })

  it('an old analysis finally cannot unlock the controls or render while the next analysis is pending', async () => {
    const first = deferred<{ data: typeof basicResult }>()
    const second = deferred<{ data: typeof basicResult }>()
    const post = vi.spyOn(axios, 'post').mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    render(<PronunciationCorrection />)
    reference()
    await recordPronunciation()
    analyze()
    fireEvent.click(screen.getByRole('button', { name: '重新录制' }))
    await recordPronunciation()
    analyze()
    await act(async () => first.resolve({ data: { ...basicResult, recognized_text: 'Stale transcript' } }))
    expect(screen.getByRole('button', { name: '分析中...' })).toBeDisabled()
    expect(screen.queryByText('Stale transcript')).not.toBeInTheDocument()
    expect(post.mock.calls[0][2]?.signal?.aborted).toBe(true)
    await act(async () => second.resolve({ data: basicResult }))
    expect(screen.getByText(basicResult.recognized_text, { selector: 'p' })).toBeVisible()
    expect(screen.getByRole('button', { name: '分析语音' })).toBeEnabled()
  })

  it('deactivating the page cancels analysis and ignores a late failure after returning', async () => {
    const pending = deferred<{ data: typeof basicResult }>()
    const post = vi.spyOn(axios, 'post').mockReturnValue(pending.promise)
    const view = render(<PronunciationCorrection active />)
    reference()
    await recordPronunciation()
    analyze()
    view.rerender(<PronunciationCorrection active={false} />)
    expect(post.mock.calls[0][2]?.signal?.aborted).toBe(true)
    view.rerender(<PronunciationCorrection active />)
    await act(async () => pending.reject(new Error('Late service failure')))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '分析语音' })).toBeEnabled()
  })

  it('unmount aborts analysis and scenario requests and revokes the recording URL', async () => {
    const pending = deferred<{ data: typeof aiResult }>()
    const post = vi.spyOn(axios, 'post').mockReturnValue(pending.promise)
    const view = render(<PronunciationCorrection />)
    await prepareAI()
    analyze()
    await waitFor(() => expect(objectUrls.size).toBe(1))
    view.unmount()
    expect(post.mock.calls[0][2]?.signal?.aborted).toBe(true)
    expect(vi.mocked(axios.get).mock.calls.every(([, config]) => config?.signal?.aborted)).toBe(true)
    expect(objectUrls.size).toBe(0)
    await act(async () => pending.resolve({ data: aiResult }))
  })
})
