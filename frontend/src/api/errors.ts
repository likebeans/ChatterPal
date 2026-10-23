import axios from 'axios'

export function requestError(error: unknown, fallback: string): string {
  if (axios.isAxiosError(error)) {
    const detail: unknown = error.response?.data?.detail
    if (typeof detail === 'string' && detail.trim()) return `${fallback}：${detail}`
    if (!error.response) return `${fallback}，请检查网络连接后重试`
  }
  return fallback
}
