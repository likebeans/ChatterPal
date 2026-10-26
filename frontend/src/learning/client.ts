import axios from 'axios'
import { isBootstrap, isOperation, isProfile, isProgress, isReview, isSession, isTask } from './validation'
import type {
  LearningBootstrap, LearningOperation, LearningProfile, LearningProgress,
  LearningReview, LearningSession, LearningSessionSummary, LearningTask,
} from './types'

const ROOT = '/api/learning'
const transport = axios.create({ baseURL: ROOT, withCredentials: true, timeout: 20000 })

export class LearningApiError extends Error {
  readonly status?: number
  constructor(message: string, public readonly code: string, public readonly uncertain = false, status?: number) {
    super(message)
    this.name = 'LearningApiError'
    this.status = status ?? (/^\d{3}$/.test(code) ? Number(code) : undefined)
  }
}

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}
function requireShape<T>(value: unknown, check: (value: Record<string, unknown>) => boolean): T {
  if (!record(value) || !check(value)) throw new LearningApiError('学习服务返回的数据不完整，请稍后重新加载。', 'invalid_response')
  return value as T
}
export function parseSession(value: unknown): LearningSession {
  return requireShape(value, isSession)
}
export function parseOperation(value: unknown): LearningOperation {
  return requireShape(value, isOperation)
}

async function request<T>(method: 'get' | 'post' | 'patch' | 'delete', path: string, data?: unknown, signal?: AbortSignal): Promise<T> {
  try {
    const response = await transport.request<T>({ method, url: path, data, signal })
    return response.data
  } catch (failure) {
    if (axios.isCancel(failure)) throw failure
    if (axios.isAxiosError(failure)) {
      const detail = failure.response?.data?.detail
      const message = typeof detail === 'string' ? detail : record(detail) && typeof detail.message === 'string' ? detail.message : null
      const status = failure.response?.status
      const code = record(detail) && typeof detail.code === 'string' ? detail.code : String(status || 'network')
      const uncertain = method !== 'get' && (!status || status >= 500)
      const fallback = code === 'review_unavailable' ? '复习功能尚未接入，已保存的原句和重练仍可从学习记录查看。'
        : status === 404 ? '找不到这项学习记录或操作，可能已删除或不属于当前身份。'
          : status === 503 ? '学习服务暂未就绪，稍后可以重新连接。'
            : status === 401 ? '学习身份已失效，请重新连接。'
              : status === 409 ? '这项练习已有更新，请刷新本次记录后继续。'
                : '暂时无法连接学习服务，请检查网络后重试。'
      throw new LearningApiError(message || fallback, code, uncertain, status)
    }
    throw failure
  }
}

// Share the first handshake across StrictMode effects. No learner credential is
// stored in JavaScript; the server sets and checks its own HttpOnly cookie.
let bootstrapPromise: Promise<LearningBootstrap> | null = null
export function bootstrapLearning(): Promise<LearningBootstrap> {
  if (!bootstrapPromise) {
    const handshake = request<unknown>('post', '/bootstrap').then(value => requireShape<LearningBootstrap>(value, isBootstrap))
      .finally(() => { if (bootstrapPromise === handshake) bootstrapPromise = null })
    bootstrapPromise = handshake
  }
  return bootstrapPromise
}
export function resetLearningIdentity() { bootstrapPromise = null }

function page<T>(value: unknown): { items: T[]; next_cursor: string | null } {
  return requireShape(value, item => Array.isArray(item.items) && (item.next_cursor === null || typeof item.next_cursor === 'string'))
}

export const learningApi = {
  bootstrap: bootstrapLearning,
  async tasks(signal?: AbortSignal): Promise<LearningTask[]> {
    const value = await request<unknown>('get', '/tasks', undefined, signal)
    const items = Array.isArray(value) ? value : page<LearningTask>(value).items
    if (!items.every(isTask)) {
      throw new LearningApiError('任务内容暂不可用，请重新加载。', 'invalid_response')
    }
    return items
  },
  async history(cursor?: string, signal?: AbortSignal) {
    const records = page<unknown>(await request('get', `/records?kind=session&limit=20${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`, undefined, signal))
    const items: LearningSessionSummary[] = records.items.map(value => {
      const stored = requireShape<Record<string, unknown>>(value, item => record(item.data))
      const session = parseSession(stored.data)
      return { session_id: session.session_id, task_id: session.task_id, title: session.task.title, status: session.status, updated_at: String(stored.updated_at || session.updated_at) }
    })
    return { items, next_cursor: records.next_cursor }
  },
  async session(id: string, signal?: AbortSignal) {
    const records = page<unknown>(await request('get', `/records?kind=session&session_id=${encodeURIComponent(id)}&limit=1`, undefined, signal))
    const stored = records.items[0]
    if (!record(stored) || !record(stored.data)) throw new LearningApiError('找不到这项练习，请从学习记录重新打开。', '404')
    return parseSession(stored.data)
  },
  async reviews(signal?: AbortSignal): Promise<LearningReview[]> {
    const items = page<unknown>(await request('get', '/reviews/due', undefined, signal)).items
    if (!items.every(isReview)) throw new LearningApiError('复习列表数据不完整，请重新加载。', 'invalid_response')
    return items
  },
  async review(id: string, signal?: AbortSignal): Promise<LearningReview> {
    return requireShape(await request('get', `/reviews/${encodeURIComponent(id)}`, undefined, signal), isReview)
  },
  async progress(signal?: AbortSignal): Promise<LearningProgress> {
    return requireShape(await request('get', '/progress', undefined, signal), isProgress)
  },
  async profile(profile: LearningProfile): Promise<LearningProfile> {
    return requireShape(await request('patch', '/profile', profile), isProfile)
  },
  async mutate(path: string, payload: Record<string, unknown> | FormData, requestId: string): Promise<LearningOperation> {
    if (payload instanceof FormData) payload.set('request_id', requestId)
    const value = await request('post', path, payload instanceof FormData ? payload : { ...payload, request_id: requestId })
    try {
      const operation = parseOperation(value)
      if (operation.request_id !== requestId) throw new LearningApiError('返回的操作编号与本次提交不一致。', 'invalid_response')
      return operation
    } catch (failure) {
      // A malformed success response does not prove that the write failed.
      if (failure instanceof LearningApiError) throw new LearningApiError(failure.message, failure.code, true)
      throw failure
    }
  },
  async operation(id: string, signal?: AbortSignal) { return parseOperation(await request('get', `/operations/${encodeURIComponent(id)}`, undefined, signal)) },
  async requestStatus(id: string) { return parseOperation(await request('get', `/requests/${encodeURIComponent(id)}`)) },
  async exportData(): Promise<Blob> {
    const data = await request<unknown>('get', '/export')
    return new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  },
  async deleteData(requestId: string) { return request<void>('delete', '/data', { request_id: requestId }) },
}

export type LearningApi = typeof learningApi

export function submissionBody(fields: Record<string, unknown>, audio?: Blob): Record<string, unknown> | FormData {
  if (!audio) return fields
  const form = new FormData()
  Object.entries(fields).forEach(([key, value]) => { if (value !== null && value !== undefined) form.append(key, String(value)) })
  form.append('audio', audio, 'recording.wav')
  return form
}
