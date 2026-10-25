import { useCallback, useEffect, useRef, useState } from 'react'
import { learningApi, LearningApiError, parseSession, resetLearningIdentity } from './client'
import type { LearningApi } from './client'
import type {
  DraftReceipt, LearningAction, LearningBootstrap, LearningConfirmation, LearningOperation, LearningPending, LearningProfile,
  LearningProgress, LearningReview, LearningSession, LearningSessionSummary, LearningTask,
} from './types'

const PENDING_KEY = 'chatterpal.learning.pending.v1'
const actions: LearningAction[] = ['start', 'turn', 'hint', 'finish', 'feedback', 'attempt', 'example', 'save-review', 'review', 'reveal', 'profile', 'delete']
function readPending(): LearningPending | null {
  try {
    const value = JSON.parse(window.localStorage.getItem(PENDING_KEY) || 'null')
    return value && typeof value.request_id === 'string' && typeof value.learner_id === 'string' && actions.includes(value.action)
      && (value.operation_id == null || typeof value.operation_id === 'string') && (value.session_id == null || typeof value.session_id === 'string')
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
  const mounted = useRef(false)
  const generation = useRef(0)
  const selection = useRef(0)
  const owner = useRef<string | null>(null)
  const deleting = useRef(false)
  const pendingRef = useRef<LearningPending | null>(null)
  const paused = useRef(false)
  const recoverySession = useRef<string | null>(null)
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

  const recoverFailure = useCallback((failure: unknown, sessionId?: string | null, query = false) => {
    // Browsing and already running requests cannot dismiss a required handshake.
    if (recoveryRef.current === 'connect') return true
    if (!(failure instanceof LearningApiError)) return false
    if (failure.status === 401) {
      pausePolling(true)
      recoverySession.current = null
      setRecovery('connect')
      setError('学习身份已失效，已暂停查询。请重新连接学习服务；同一身份的未发送内容会保留。')
      return true
    }
    if (failure.status === 409 || conflict(failure.code)) {
      updatePending(null); retry.current = null
      pausePolling(true)
      recoverySession.current = sessionId || null
      setRecovery(sessionId ? 'refresh' : 'connect')
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
    if (reviewResult.status === 'fulfilled') { setReviews(reviewResult.value); setReviewUnavailable('') }
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
        setSession(null); setHistory([]); setCursor(null); setReviews([]); setProgress(null); setLastOperation(null); setConfirmation(null); setView('today')
        setNotice('已连接新的学习身份，旧身份的记录和未发送内容已清除。')
      }
      if (saved && saved.learner_id !== data.learner_id) { retry.current = null; updatePending(null) }
      owner.current = data.learner_id
      setIdentity(data)
      setRecovery(null)
      recoverySession.current = null
      const matching = saved?.learner_id === data.learner_id ? saved : null
      const operation = data.pending_operations.find(item => item.request_id === matching?.request_id) || data.pending_operations[0]
      if (operation) {
        updatePending({ ...matching, request_id: operation.request_id, operation_id: operation.operation_id,
          action: matching?.request_id === operation.request_id ? matching.action : 'turn',
          session_id: operation.session_id || (matching?.request_id === operation.request_id ? matching.session_id : undefined), learner_id: data.learner_id,
          draft_key: matching?.request_id === operation.request_id ? matching.draft_key : undefined,
          draft_version: matching?.request_id === operation.request_id ? matching.draft_version : undefined })
      } else if (matching) {
        updatePending(matching)
        setNotice('发现一次尚未确认的提交。请先查询操作或打开学习历史核对，避免重复创建。')
      } else updatePending(null)
      pausePolling(false)
      await loadCollections(version, request.signal)
    } catch (failure) {
      if (current(version)) { setError(message(failure)); setRecovery('connect') }
    } finally { if (current(version)) setLoading(false) }
  }, [api, loadCollections, pausePolling, setRecovery, updatePending])

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

  const complete = useCallback(async (operation: LearningOperation, operationPending: LearningPending) => {
    const version = generation.current
    const activeOperation = () => current(version) && recoveryRef.current !== 'connect' && pendingRef.current?.request_id === operationPending.request_id
    if (!activeOperation()) return
    const sessionId = operation.session_id || operationPending.session_id
    if (operation.status === 'failed' || operation.status === 'interrupted') {
      updatePending(null)
      retry.current = null
      const code = operation.error?.code || operation.status
      const failure = new LearningApiError(operation.error?.message || (operation.status === 'interrupted' ? '服务重启中断了这次处理，请核对记录后重新提交。' : '这次处理未完成，可以重新提交。'), code)
      if (!recoverFailure(failure, sessionId)) { setRecovery(null); setError(failure.message) }
      return
    }
    if (operation.status !== 'succeeded') return
    // Keep the mutation lock until the new HTTP revision is available.
    try {
      let updated: LearningSession | null = null
      if (operation.result_type === 'session') updated = parseSession(operation.result)
      else if (operation.result_type === 'record' && operation.result && typeof operation.result === 'object' && 'data' in operation.result) updated = parseSession(operation.result.data)
      else if (sessionId) updated = await api.session(sessionId)
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
      setLastOperation({ ...operation, session_id: sessionId })
      setConfirmation({ ...operationPending, session_id: sessionId })
      setError(''); setRecovery(null)
      setNotice('已保存本次结果。')
      retry.current = null
      updatePending(null)
      void loadCollections(version)
    } catch (failure) {
      if (current(version) && pendingRef.current?.request_id === operationPending.request_id && !recoverFailure(failure, sessionId, true)) setError(`操作已经完成，读取结果暂时失败：${message(failure)} 请查询原操作，无需重新提交。`)
    }
  }, [api, loadCollections, recoverFailure, setRecovery, updatePending])

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
      if (recoverFailure(failure, entry.pending.session_id)) return
      setError(message(failure))
      if (!(failure instanceof LearningApiError) || failure.uncertain) setNotice('提交结果尚未确认。重试会沿用同一个请求编号，不会主动创建第二次练习。')
    }
  }, [api, complete, pausePolling, recoverFailure, setRecovery, updatePending])

  const mutate = useCallback(async (action: LearningAction, path: string, payload: Record<string, unknown> | FormData, sessionId?: string, receipt?: DraftReceipt) => {
    if (deleting.current || !owner.current || pendingRef.current || paused.current) return
    setLastOperation(null)
    await send({ path, payload, pending: { action, request_id: requestId(), session_id: sessionId, learner_id: owner.current, ...receipt } })
  }, [send])

  const poll = useCallback(async (signal?: AbortSignal) => {
    const waiting = pendingRef.current
    if (deleting.current || paused.current || !waiting?.operation_id) return
    const version = generation.current
    try {
      const operation = await api.operation(waiting.operation_id, signal)
      if (current(version) && pendingRef.current?.request_id === waiting.request_id) await complete(operation, waiting)
    } catch (failure) {
      if (current(version) && pendingRef.current?.request_id === waiting.request_id && !signal?.aborted && !recoverFailure(failure, waiting.session_id, true)) setError(`查询暂时失败：${message(failure)} 原操作编号已保留。`)
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
        if (!recoverFailure(failure, waiting.session_id, true)) setError(message(failure))
        return
      }
    }
    if (retry.current) { await send(retry.current); return }
    await connect()
  }, [api, complete, connect, openSession, pausePolling, poll, recoverFailure, send, updatePending])

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
      retry.current = null
      owner.current = null
      recoverySession.current = null
      setIdentity(null); setSession(null); setHistory([]); setCursor(null); setReviews([]); setProgress(null); setTasks([]); setWarnings([]); setReviewUnavailable('')
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
    lastOperation, confirmation, recovery, pollingPaused, connect, openSession, mutate, retryPending, saveProfile, loadMore, deleteData, acknowledgeRecovery,
    exportData: api.exportData, canReplay: Boolean(retry.current) }
}
