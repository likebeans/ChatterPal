import { useCallback, useEffect, useRef, useState } from 'react'
import { learningApi, LearningApiError, parseSession, resetLearningIdentity } from './client'
import type { LearningApi } from './client'
import { isReview, isReviewResult, isTranscription, sameTranscriptionContext } from './validation'
import { readTranscriptionReceipts, storeTranscriptionReceipts } from './transcriptionReceipts'
import type {
  DraftReceipt, LearningAction, LearningBootstrap, LearningConfirmation, LearningOperation, LearningPending, LearningProfile,
  LearningProgress, LearningReview, LearningSession, LearningSessionSummary, LearningTask, ReadyTranscription, TranscriptionReceipt,
} from './types'

const PENDING_KEY = 'chatterpal.learning.pending.v1'
const actions: LearningAction[] = ['transcribe', 'start', 'turn', 'hint', 'finish', 'feedback', 'attempt', 'example', 'save-review', 'review', 'reveal', 'review-hint', 'defer-review', 'profile', 'delete']
function readPending(): LearningPending | null {
  try {
    const value = JSON.parse(window.localStorage.getItem(PENDING_KEY) || 'null')
    return value && typeof value.request_id === 'string' && typeof value.learner_id === 'string' && actions.includes(value.action)
      && (value.operation_id == null || typeof value.operation_id === 'string') && (value.session_id == null || typeof value.session_id === 'string')
      && (value.review_item_id === undefined || typeof value.review_item_id === 'string')
      && (value.draft_key === undefined || typeof value.draft_key === 'string') && (value.draft_version === undefined || Number.isInteger(value.draft_version)) ? value : null
  } catch { return null }
}
function storePending(value: LearningPending | null) {
  try {
    if (value) window.localStorage.setItem(PENDING_KEY, JSON.stringify(value))
    else window.localStorage.removeItem(PENDING_KEY)
  } catch { /* The server remains the source of truth if browser storage is blocked. */ }
}
export function requestId() { return crypto.randomUUID() }
function message(error: unknown) { return error instanceof Error ? error.message : '学习服务暂不可用，请重试。' }
function conflict(code: string) { return ['409', 'revision_conflict', 'session_busy', 'session_completed', 'session_conflict'].includes(code) }

interface RetryPayload { path: string; payload: Record<string, unknown> | FormData; pending: LearningPending }
export type LearningView = 'today' | 'session' | 'history' | 'review' | 'progress' | 'settings'
type Recovery = 'connect' | 'refresh' | 'query' | null

export function useLearningFlow(api: LearningApi = learningApi) {
  const [identity, setIdentity] = useState<LearningBootstrap | null>(null)
  const [tasks, setTasks] = useState<LearningTask[]>([])
  const [history, setHistory] = useState<LearningSessionSummary[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [session, setSession] = useState<LearningSession | null>(null)
  const [reviews, setReviews] = useState<LearningReview[]>([])
  const [selectedReview, setSelectedReview] = useState<LearningReview | null>(null)
  const [reviewUnavailable, setReviewUnavailable] = useState('')
  const [progress, setProgress] = useState<LearningProgress | null>(null)
  const [view, setView] = useState<LearningView>('today')
  const [loading, setLoading] = useState(true)
  const [pending, setPending] = useState<LearningPending | null>(null)
  const [error, setError] = useState('')
  const [recovery, setRecoveryState] = useState<Recovery>(null)
  const recoveryRef = useRef<Recovery>(null)
  const setRecovery = useCallback((value: Recovery) => { recoveryRef.current = value; setRecoveryState(value) }, [])
  const [pollingPaused, setPollingPaused] = useState(false)
  const [warnings, setWarnings] = useState<string[]>([])
  const [notice, setNotice] = useState('')
  const [lastOperation, setLastOperation] = useState<LearningOperation | null>(null)
  const [confirmation, setConfirmation] = useState<LearningConfirmation | null>(null)
  const [readyTranscriptions, setReadyTranscriptions] = useState<ReadyTranscription[]>([])
  const transcriptionReceipts = useRef<TranscriptionReceipt[]>(readTranscriptionReceipts())
  const cancellingTranscription = useRef<string | null>(null)
  const saveTranscriptionReceipts = useCallback((receipts: TranscriptionReceipt[]) => {
    transcriptionReceipts.current = receipts
    storeTranscriptionReceipts(receipts)
  }, [])
  const forgetTranscription = useCallback((receipt: DraftReceipt) => {
    saveTranscriptionReceipts(transcriptionReceipts.current.filter(item => item.draft_key !== receipt.draft_key || item.draft_version > receipt.draft_version))
    setReadyTranscriptions(previous => previous.filter(item => item.receipt.draft_key !== receipt.draft_key || item.receipt.draft_version > receipt.draft_version))
  }, [saveTranscriptionReceipts])
  const mounted = useRef(false)
  const generation = useRef(0)
  const selection = useRef(0)
  const owner = useRef<string | null>(null)
  const deleting = useRef(false)
  const pendingRef = useRef<LearningPending | null>(null)
  const paused = useRef(false)
  const recoverySession = useRef<string | null>(null)
  const recoveryReview = useRef<string | null>(null)
  const retry = useRef<RetryPayload | null>(null)
  const controller = useRef<AbortController | null>(null)

  const pausePolling = useCallback((value: boolean) => {
    paused.current = value
    if (mounted.current) setPollingPaused(value)
  }, [])
  const updatePending = useCallback((value: LearningPending | null) => {
    pendingRef.current = value
    storePending(value)
    if (mounted.current) setPending(value)
  }, [])
  const current = (version: number) => mounted.current && generation.current === version

  const recoverFailure = useCallback((failure: unknown, sessionId?: string | null, query = false, reviewId?: string) => {
    // Browsing and already running requests cannot dismiss a required handshake.
    if (recoveryRef.current === 'connect') return true
    if (!(failure instanceof LearningApiError)) return false
    if (failure.status === 401) {
      pausePolling(true)
      recoverySession.current = null
      recoveryReview.current = null
      setRecovery('connect')
      setError('学习身份已失效，已暂停查询。请重新连接学习服务；同一身份的未发送内容会保留。')
      return true
    }
    if (['transcription_context_changed', 'transcription_expired', 'transcription_consumed'].includes(failure.code)) {
      updatePending(null); retry.current = null
      setError(failure.code === 'transcription_consumed' ? '这段录音已经提交，请刷新记录核对结果。核对文字已保留。'
        : failure.code === 'transcription_expired' ? '这段录音已过期，请重新录音或放弃录音后使用文字。核对文字已保留。'
          : '这段录音对应的练习内容已有更新，请重新录音或放弃录音后使用文字。核对文字已保留。')
      return true
    }
    if (failure.status === 409 || conflict(failure.code)) {
      updatePending(null); retry.current = null
      pausePolling(true)
      recoverySession.current = reviewId ? null : sessionId || null
      recoveryReview.current = reviewId || null
      setRecovery(sessionId || reviewId ? 'refresh' : 'connect')
      setError('这项练习已有更新或仍有操作在处理。请先刷新本次记录，再继续提交。未发送的内容已保留。')
      return true
    }
    if (query && (failure.status && failure.status >= 400 && failure.status < 500 || failure.code === 'invalid_response')) {
      pausePolling(true)
      if (failure.code === 'invalid_response') setRecovery('query')
      else { updatePending(null); retry.current = null; setRecovery('connect') }
      setError(failure.status === 404 || failure.status === 410
        ? '原操作已不可查询，可能已删除、过期或不属于当前身份。已停止自动查询，请打开学习记录核对；未发送的内容已保留。'
        : `无法继续查询原操作，已停止自动查询：${message(failure)}`)
      return true
    }
    return false
  }, [pausePolling, setRecovery, updatePending])

  const loadCollections = useCallback(async (version: number, signal?: AbortSignal) => {
    const results = await Promise.allSettled([api.tasks(signal), api.history(undefined, signal), api.reviews(signal), api.progress(signal)])
    if (!current(version)) return
    const missing: string[] = []
    const [taskResult, historyResult, reviewResult, progressResult] = results
    if (taskResult.status === 'fulfilled') setTasks(taskResult.value)
    else missing.push('任务列表暂未加载，可重新连接。')
    if (historyResult.status === 'fulfilled') { setHistory(historyResult.value.items); setCursor(historyResult.value.next_cursor) }
    else missing.push('学习历史暂未加载。')
    if (reviewResult.status === 'fulfilled') {
      setReviews(reviewResult.value); setReviewUnavailable('')
      setSelectedReview(previous => {
        const updated = reviewResult.value.find(item => item.review_item_id === previous?.review_item_id)
        return updated && previous && updated.revision >= previous.revision ? { ...updated, attempts: previous.attempts } : previous
      })
    }
    else {
      setReviews([])
      const unavailable = reviewResult.reason instanceof LearningApiError && (reviewResult.reason.status === 503 || reviewResult.reason.code === 'review_unavailable')
        ? '复习功能尚未接入。已保存的原句和重练可以从学习记录查看，目前不会安排或生成复习结果。'
        : '复习服务暂不可用，请稍后重新加载。'
      setReviewUnavailable(unavailable)
      missing.push(unavailable)
    }
    if (progressResult.status === 'fulfilled') setProgress(progressResult.value)
    else missing.push('进度服务暂未就绪。')
    setWarnings(missing)
    const unauthorized = results.find(result => result.status === 'rejected' && result.reason instanceof LearningApiError && result.reason.status === 401)
    if (unauthorized?.status === 'rejected') recoverFailure(unauthorized.reason)
  }, [api, recoverFailure])

  const connect = useCallback(async () => {
    if (deleting.current) return
    const version = ++generation.current
    ++selection.current
    controller.current?.abort()
    pausePolling(true)
    const request = new AbortController()
    controller.current = request
    setLoading(true)
    setError('')
    try {
      const data = await api.bootstrap()
      if (!current(version)) return
      const saved = pendingRef.current || readPending()
      if (owner.current && owner.current !== data.learner_id) {
        retry.current = null
        updatePending(null)
        saveTranscriptionReceipts([]); setReadyTranscriptions([])
        setSession(null); setHistory([]); setCursor(null); setReviews([]); setSelectedReview(null); setProgress(null); setLastOperation(null); setConfirmation(null); setView('today')
        setNotice('已连接新的学习身份，旧身份的记录和未发送内容已清除。')
      }
      if (saved && saved.learner_id !== data.learner_id) { retry.current = null; updatePending(null) }
      owner.current = data.learner_id
      setIdentity(data)
      const receipts = transcriptionReceipts.current.filter(item => item.learner_id === data.learner_id)
      saveTranscriptionReceipts(receipts)
      setReadyTranscriptions(previous => previous.filter(item => item.receipt.learner_id === data.learner_id))
      setRecovery(null)
      recoverySession.current = null
      recoveryReview.current = null
      const matching = saved?.learner_id === data.learner_id ? saved : null
      const operation = data.pending_operations.find(item => item.request_id === matching?.request_id) || data.pending_operations[0]
      if (operation) {
        updatePending({ ...matching, request_id: operation.request_id, operation_id: operation.operation_id,
          action: matching?.request_id === operation.request_id ? matching.action : operation.action === 'transcribe' ? 'transcribe' : 'turn',
          session_id: operation.session_id || (matching?.request_id === operation.request_id ? matching.session_id : undefined), learner_id: data.learner_id,
          draft_key: matching?.request_id === operation.request_id ? matching.draft_key : undefined,
          draft_version: matching?.request_id === operation.request_id ? matching.draft_version : undefined })
      } else if (matching) {
        updatePending(matching)
        setNotice('发现一次尚未确认的提交。请先查询操作或打开学习历史核对，避免重复创建。')
      } else updatePending(null)
      pausePolling(false)
      await loadCollections(version, request.signal)
      const recovered = await Promise.allSettled(receipts.map(async receipt => ({ receipt, transcription: await api.getTranscription(receipt.transcription_id, request.signal) })))
      if (current(version)) {
        setReadyTranscriptions(previous => {
          const next = [...previous]
          recovered.forEach(result => {
            if (result.status !== 'fulfilled' || !transcriptionReceipts.current.some(item => item.transcription_id === result.value.receipt.transcription_id)) return
            const index = next.findIndex(item => item.receipt.draft_key === result.value.receipt.draft_key)
            if (index < 0) next.push(result.value)
            else if (next[index].receipt.draft_version <= result.value.receipt.draft_version) next[index] = result.value
          })
          return next
        })
        const failed = recovered.find(result => result.status === 'rejected')
        if (failed?.status === 'rejected' && !recoverFailure(failed.reason)) setError(`无法恢复待核对的录音：${message(failed.reason)} 恢复编号已保留，请重新连接后重试。`)
      }
    } catch (failure) {
      if (current(version)) { setError(message(failure)); setRecovery('connect') }
    } finally { if (current(version)) setLoading(false) }
  }, [api, loadCollections, pausePolling, recoverFailure, saveTranscriptionReceipts, setRecovery, updatePending])

  useEffect(() => {
    mounted.current = true
    void connect()
    return () => { mounted.current = false; generation.current++; controller.current?.abort() }
  }, [connect])

  const openSession = useCallback(async (id: string) => {
    if (deleting.current || !owner.current) return
    const selected = ++selection.current
    const version = generation.current
    setLoading(true)
    if (!recoveryRef.current) setError('')
    try {
      const data = await api.session(id)
      if (!current(version) || selected !== selection.current) return
      setSession(previous => previous?.session_id === data.session_id && previous.revision > data.revision ? previous : data)
      setView('session')
      if (recoveryRef.current === 'refresh' && recoverySession.current === id) { recoverySession.current = null; setRecovery(null); setError(''); pausePolling(false) }
    } catch (failure) {
      if (current(version) && selected === selection.current && !recoverFailure(failure, id)) setError(message(failure))
    } finally { if (current(version) && selected === selection.current) setLoading(false) }
  }, [api, pausePolling, recoverFailure, setRecovery])

  const openReview = useCallback(async (id: string) => {
    if (deleting.current || !owner.current) return
    const selected = ++selection.current
    const version = generation.current
    setLoading(true)
    if (!recoveryRef.current) setError('')
    try {
      const data = await api.review(id)
      if (!current(version) || selected !== selection.current) return
      setSelectedReview(previous => previous?.review_item_id === data.review_item_id && previous.revision > data.revision ? previous : data)
      setView('review')
      if (recoveryRef.current === 'refresh' && recoveryReview.current === id) { recoveryReview.current = null; setRecovery(null); setError(''); pausePolling(false) }
    } catch (failure) {
      if (current(version) && selected === selection.current && !recoverFailure(failure, undefined, false, id)) setError(message(failure))
    } finally { if (current(version) && selected === selection.current) setLoading(false) }
  }, [api, pausePolling, recoverFailure, setRecovery])

  const complete = useCallback(async (operation: LearningOperation, operationPending: LearningPending) => {
    const version = generation.current
    const activeOperation = () => current(version) && recoveryRef.current !== 'connect' && pendingRef.current?.request_id === operationPending.request_id
    if (!activeOperation() || cancellingTranscription.current === operationPending.request_id) return
    const sessionId = operation.session_id || operationPending.session_id
    if (operation.status === 'failed' || operation.status === 'interrupted') {
      updatePending(null)
      retry.current = null
      const code = operation.error?.code || operation.status
      const failure = new LearningApiError(operation.error?.message || (operation.status === 'interrupted' ? '服务重启中断了这次处理，请核对记录后重新提交。' : '这次处理未完成，可以重新提交。'), code)
      if (!recoverFailure(failure, sessionId, false, operationPending.review_item_id)) { setRecovery(null); setError(failure.message) }
      return
    }
    if (operation.status !== 'succeeded') return
    // Keep the mutation lock until the new HTTP revision is available.
    try {
      if (operationPending.action === 'transcribe') {
        if (operation.result_type !== 'transcription' || !isTranscription(operation.result)) throw new LearningApiError('录音识别结果不完整，请查询原操作。', 'invalid_response')
        const sent = retry.current?.pending.request_id === operationPending.request_id ? retry.current.payload : null
        if (sent instanceof FormData && !sameTranscriptionContext(operation.result.context, JSON.parse(String(sent.get('context'))))) {
          throw new LearningApiError('返回的录音来源与原请求不一致，请查询原操作。', 'invalid_response')
        }
        if (operationPending.draft_key !== undefined && operationPending.draft_version !== undefined) {
          const receipt: TranscriptionReceipt = { learner_id: operationPending.learner_id, request_id: operationPending.request_id,
            operation_id: operation.operation_id, transcription_id: operation.result.transcription_id,
            draft_key: operationPending.draft_key, draft_version: operationPending.draft_version }
          const transcription = operation.result
          saveTranscriptionReceipts([...transcriptionReceipts.current.filter(item => item.draft_key !== receipt.draft_key), receipt])
          setReadyTranscriptions(previous => [...previous.filter(item => item.receipt.draft_key !== receipt.draft_key), { receipt, transcription }])
        }
        setLastOperation(operation)
        setError(''); setRecovery(null); setNotice('录音已识别，请核对文字后再提交。')
        retry.current = null
        updatePending(null)
        return
      }
      let updated: LearningSession | null = null
      const reviewOperation = ['review_saved', 'review_reveal', 'review_hint', 'review_deferred', 'review'].includes(operation.result_type || '')
      const reviewResult = operation.result_type === 'review' && isReviewResult(operation.result) ? operation.result.review
        : reviewOperation && isReview(operation.result) ? operation.result : null
      if (reviewOperation && !reviewResult) throw new LearningApiError('复习结果不完整，请查询原操作。', 'invalid_response')
      if (operationPending.review_item_id && reviewResult?.review_item_id !== operationPending.review_item_id) throw new LearningApiError('返回的复习与原操作不一致，请查询原操作。', 'invalid_response')
      if (operation.result_type === 'session') updated = parseSession(operation.result)
      else if (operation.result_type === 'record' && operation.result && typeof operation.result === 'object' && 'data' in operation.result) updated = parseSession(operation.result.data)
      else if (sessionId && !reviewOperation) updated = await api.session(sessionId)
      if (!activeOperation()) return
      if (updated && sessionId && updated.session_id !== sessionId) throw new LearningApiError('返回的练习与原操作不一致，请重新查询或核对学习记录。', 'invalid_response')
      if (updated) {
        const snapshot = updated
        setSession(previous => {
          if (previous?.session_id === snapshot.session_id) return previous.revision > snapshot.revision ? previous : snapshot
          return !previous || operationPending.action === 'start' ? snapshot : previous
        })
        if (operationPending.action === 'start') setView('session')
      }
      if (reviewResult) {
        const snapshot = reviewResult
        const answered = operation.result_type === 'review' && isReviewResult(operation.result) ? operation.result.attempt : null
        setSelectedReview(previous => {
          if (previous?.review_item_id !== snapshot.review_item_id || previous.revision > snapshot.revision) return previous
          const attempts = previous.attempts || []
          return { ...snapshot, attempts: answered && !attempts.some(item => item.attempt_id === answered.attempt_id) ? [...attempts, answered] : attempts }
        })
        setReviews(previous => previous.map(item => item.review_item_id === snapshot.review_item_id && item.revision <= snapshot.revision ? snapshot : item))
      }
      setLastOperation({ ...operation, session_id: sessionId })
      setConfirmation({ ...operationPending, session_id: sessionId })
      if (['turn', 'attempt', 'review'].includes(operationPending.action) && operationPending.draft_key !== undefined && operationPending.draft_version !== undefined) {
        forgetTranscription({ draft_key: operationPending.draft_key, draft_version: operationPending.draft_version })
      }
      setError(''); setRecovery(null)
      setNotice('已保存本次结果。')
      retry.current = null
      updatePending(null)
      void loadCollections(version)
    } catch (failure) {
      if (current(version) && pendingRef.current?.request_id === operationPending.request_id && !recoverFailure(failure, sessionId, true, operationPending.review_item_id)) setError(`操作已经完成，读取结果暂时失败：${message(failure)} 请查询原操作，无需重新提交。`)
    }
  }, [api, forgetTranscription, loadCollections, recoverFailure, saveTranscriptionReceipts, setRecovery, updatePending])

  const send = useCallback(async (entry: RetryPayload) => {
    const version = generation.current
    retry.current = entry
    pausePolling(false)
    updatePending(entry.pending)
    setError(''); setRecovery(null)
    setNotice('')
    try {
      const operation = await api.mutate(entry.path, entry.payload, entry.pending.request_id)
      if (!current(version) || recoveryRef.current === 'connect' || pendingRef.current?.request_id !== entry.pending.request_id) return
      const next = { ...entry.pending, operation_id: operation.operation_id, session_id: operation.session_id || entry.pending.session_id }
      updatePending(next)
      if (operation.status !== 'pending') await complete(operation, next)
    } catch (failure) {
      if (!current(version) || recoveryRef.current === 'connect' || pendingRef.current?.request_id !== entry.pending.request_id) return
      if (failure instanceof LearningApiError && !failure.uncertain) { updatePending(null); retry.current = null }
      if (recoverFailure(failure, entry.pending.session_id, false, entry.pending.review_item_id)) return
      setError(message(failure))
      if (!(failure instanceof LearningApiError) || failure.uncertain) setNotice('提交结果尚未确认。重试会沿用同一个请求编号，不会主动创建第二次练习。')
    }
  }, [api, complete, pausePolling, recoverFailure, setRecovery, updatePending])

  const mutate = useCallback(async (action: LearningAction, path: string, payload: Record<string, unknown> | FormData, sessionId?: string, receipt?: DraftReceipt) => {
    if (deleting.current || !owner.current || pendingRef.current || paused.current || cancellingTranscription.current) return
    setLastOperation(null)
    const reviewId = path.match(/^\/reviews\/([^/]+)\//)?.[1] || (action === 'transcribe' && payload instanceof FormData ? (() => {
      try { const context = JSON.parse(String(payload.get('context'))); return context.kind === 'review' ? encodeURIComponent(context.review_item_id) : undefined } catch { return undefined }
    })() : undefined)
    await send({ path, payload, pending: { action, request_id: requestId(), session_id: sessionId, review_item_id: reviewId ? decodeURIComponent(reviewId) : undefined, learner_id: owner.current, ...receipt } })
  }, [send])

  const cancelTranscription = useCallback(async (receipt: DraftReceipt, transcriptionId?: string) => {
    if (deleting.current || !owner.current) throw new LearningApiError('请先连接学习身份，再放弃录音。', 'identity_required')
    const waiting = pendingRef.current
    if (waiting && (waiting.action !== 'transcribe' || waiting.draft_key !== receipt.draft_key || waiting.draft_version !== receipt.draft_version)) {
      throw new LearningApiError('请等待当前提交确认后再更换录音。', 'operation_pending')
    }
    const saved = transcriptionReceipts.current.find(item => item.draft_key === receipt.draft_key && item.draft_version === receipt.draft_version)
    const id = transcriptionId || saved?.transcription_id
    if (!waiting && !id) { forgetTranscription(receipt); return }
    const version = generation.current
    const request = waiting?.request_id || saved?.request_id || id!
    if (cancellingTranscription.current) throw new LearningApiError('正在放弃录音，请稍候。', 'operation_pending')
    cancellingTranscription.current = request
    const wasPaused = paused.current
    pausePolling(true)
    try {
      try {
        if (waiting) await api.deleteTranscriptionRequest(waiting.request_id)
        else await api.deleteTranscription(id!)
      } catch (failure) {
        if (!waiting || !(failure instanceof LearningApiError) || failure.status !== 404) throw failure
        // Keep the receipt until the original POST is visible and a DELETE succeeds.
        const existing = await api.requestStatus(waiting.request_id)
        if (!current(version)) throw new LearningApiError('学习身份已更新，请重新打开练习。', 'identity_changed')
        if (existing.request_id !== waiting.request_id) throw new LearningApiError('无法核对原识别请求，请稍后重试放弃录音。', 'invalid_response')
        await api.deleteTranscriptionRequest(waiting.request_id)
      }
      if (!current(version)) throw new LearningApiError('学习身份已更新，请重新打开练习。', 'identity_changed')
      if (waiting && pendingRef.current?.request_id === waiting.request_id) { updatePending(null); retry.current = null; setError(''); setRecovery(null) }
      forgetTranscription(receipt)
      setNotice('已放弃本次录音。')
    } catch (failure) {
      if (current(version)) setError(`无法放弃本次录音：${message(failure)} 录音与恢复编号已保留。`)
      throw failure
    } finally {
      cancellingTranscription.current = null
      if (current(version)) pausePolling(wasPaused)
    }
  }, [api, forgetTranscription, pausePolling, setRecovery, updatePending])

  const poll = useCallback(async (signal?: AbortSignal) => {
    const waiting = pendingRef.current
    if (deleting.current || paused.current || !waiting?.operation_id) return
    const version = generation.current
    try {
      const operation = await api.operation(waiting.operation_id, signal)
      if (current(version) && pendingRef.current?.request_id === waiting.request_id) await complete(operation, waiting)
    } catch (failure) {
      if (current(version) && pendingRef.current?.request_id === waiting.request_id && !signal?.aborted && !recoverFailure(failure, waiting.session_id, true, waiting.review_item_id)) setError(`查询暂时失败：${message(failure)} 原操作编号已保留。`)
    }
  }, [api, complete, recoverFailure])

  useEffect(() => {
    if (!pending?.operation_id || pollingPaused) return
    const request = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    const run = async () => {
      await poll(request.signal)
      if (!request.signal.aborted && !paused.current && pendingRef.current?.operation_id) timer = setTimeout(() => { void run() }, 1500)
    }
    void run()
    return () => { request.abort(); clearTimeout(timer) }
  }, [pending?.operation_id, pollingPaused, poll])

  const retryPending = useCallback(async () => {
    if (deleting.current) return
    const activeRecovery = recoveryRef.current
    if (activeRecovery === 'connect') { await connect(); return }
    if (activeRecovery === 'refresh' && recoveryReview.current) { await openReview(recoveryReview.current); return }
    if (activeRecovery === 'refresh' && recoverySession.current) { await openSession(recoverySession.current); return }
    if (pendingRef.current?.operation_id) { pausePolling(false); await poll(); return }
    const waiting = pendingRef.current
    if (waiting) {
      const version = generation.current
      try {
        const operation = await api.requestStatus(waiting.request_id)
        if (!current(version) || recoveryRef.current === 'connect' || pendingRef.current?.request_id !== waiting.request_id) return
        const found = { ...waiting, operation_id: operation.operation_id, session_id: operation.session_id || waiting.session_id }
        updatePending(found)
        pausePolling(false)
        if (operation.status !== 'pending') await complete(operation, found)
        return
      } catch (failure) {
        if (!current(version) || recoveryRef.current === 'connect' || pendingRef.current?.request_id !== waiting.request_id) return
        if (failure instanceof LearningApiError && failure.status === 404 && retry.current) { await send(retry.current); return }
        if (!recoverFailure(failure, waiting.session_id, true, waiting.review_item_id)) setError(message(failure))
        return
      }
    }
    if (retry.current) { await send(retry.current); return }
    await connect()
  }, [api, complete, connect, openReview, openSession, pausePolling, poll, recoverFailure, send, updatePending])

  const saveProfile = async (profile: LearningProfile) => {
    if (deleting.current || pendingRef.current || paused.current) return
    const version = generation.current
    setError('')
    try {
      const saved = await api.profile(profile)
      if (current(version)) { setIdentity(previous => previous ? { ...previous, profile: saved } : previous); setNotice('学习偏好已保存。') }
    } catch (failure) { if (current(version) && !recoverFailure(failure)) setError(message(failure)) }
  }

  const loadMore = async () => {
    if (deleting.current || !owner.current || !cursor) return
    const version = generation.current
    try {
      const page = await api.history(cursor)
      if (current(version)) { setHistory(previous => [...previous, ...page.items.filter(item => !previous.some(existing => existing.session_id === item.session_id))]); setCursor(page.next_cursor) }
    } catch (failure) { if (current(version) && !recoverFailure(failure)) setError(message(failure)) }
  }

  const deleteData = async () => {
    if (deleting.current || !owner.current) return
    deleting.current = true
    ++generation.current
    ++selection.current
    controller.current?.abort()
    pausePolling(true)
    setLoading(true)
    if (!recoveryRef.current) setError('')
    try {
      await api.deleteData(requestId())
      if (!mounted.current) return
      updatePending(null)
      saveTranscriptionReceipts([]); setReadyTranscriptions([])
      retry.current = null
      owner.current = null
      recoverySession.current = null
      recoveryReview.current = null
      setIdentity(null); setSession(null); setHistory([]); setCursor(null); setReviews([]); setSelectedReview(null); setProgress(null); setTasks([]); setWarnings([]); setReviewUnavailable('')
      setLastOperation(null); setConfirmation(null); setRecovery(null); setNotice('学习数据已删除。重新连接后可以开始新的练习。'); setView('today')
      resetLearningIdentity()
    } catch (failure) {
      if (mounted.current && !recoverFailure(failure)) { setError(message(failure)); pausePolling(false) }
    } finally { deleting.current = false; if (mounted.current) setLoading(false) }
  }

  const acknowledgeRecovery = async () => {
    if (pendingRef.current?.operation_id || retry.current) return
    updatePending(null)
    setRecovery(null)
    pausePolling(false)
    setNotice('已结束本地恢复提示。请根据历史记录继续，避免再次创建相同练习。')
  }

  return { identity, tasks, history, cursor, session, reviews, reviewUnavailable, progress, view, setView, loading, pending, error, warnings, notice,
    selectedReview, setSelectedReview, openReview, lastOperation, confirmation, readyTranscriptions, cancelTranscription, forgetTranscription, recovery, pollingPaused, connect, openSession, mutate, retryPending, saveProfile, loadMore, deleteData, acknowledgeRecovery,
    exportData: api.exportData, canReplay: Boolean(retry.current) }
}
