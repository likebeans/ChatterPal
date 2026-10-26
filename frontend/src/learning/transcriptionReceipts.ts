import type { TranscriptionReceipt } from './types'

export const TRANSCRIPTION_RECEIPTS_KEY = 'chatterpal.learning.transcriptions.v1'

export function readTranscriptionReceipts(): TranscriptionReceipt[] {
  try {
    const stored: unknown = JSON.parse(window.localStorage.getItem(TRANSCRIPTION_RECEIPTS_KEY) || '[]')
    if (!Array.isArray(stored)) return []
    return stored.filter((value): value is TranscriptionReceipt => value !== null && typeof value === 'object'
      && ['learner_id', 'request_id', 'transcription_id', 'draft_key'].every(key => typeof value[key] === 'string')
      && (value.operation_id === undefined || typeof value.operation_id === 'string') && Number.isSafeInteger(value.draft_version) && value.draft_version >= 0)
  } catch { return [] }
}

export function storeTranscriptionReceipts(receipts: TranscriptionReceipt[]) {
  try {
    if (!receipts.length) { window.localStorage.removeItem(TRANSCRIPTION_RECEIPTS_KEY); return }
    // Explicitly allow only opaque identifiers and draft ownership into browser storage.
    window.localStorage.setItem(TRANSCRIPTION_RECEIPTS_KEY, JSON.stringify(receipts.map(receipt => ({
      learner_id: receipt.learner_id, request_id: receipt.request_id, operation_id: receipt.operation_id,
      transcription_id: receipt.transcription_id, draft_key: receipt.draft_key, draft_version: receipt.draft_version,
    }))))
  } catch { /* The in-memory draft remains usable when browser storage is blocked. */ }
}
