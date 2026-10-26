import { useCallback, useEffect, useRef, useState } from 'react'
import type { LearningConfirmation, PracticeDraft, PracticeDraftChanges, ReadyTranscription } from './types'

const EMPTY_DRAFT: PracticeDraft = { text: '', audio: null, version: 0, confirmationText: '', transcription: null, context: null }

// Audio and edited text stay in memory. Recovery receipts contain identifiers only.
export function usePracticeDrafts(owner: string | undefined, confirmation: LearningConfirmation | null, ready: ReadyTranscription[]) {
  const [state, setState] = useState<{ owner?: string; drafts: Map<string, PracticeDraft> }>({ owner, drafts: new Map() })
  const version = useRef(0)
  const currentOwner = useRef(owner)
  currentOwner.current = owner
  useEffect(() => { setState(previous => previous.owner === owner ? previous : { owner, drafts: new Map() }) }, [owner])
  useEffect(() => {
    if (!owner) return
    setState(previous => {
      const drafts = new Map(previous.owner === owner ? previous.drafts : [])
      let changed = false
      for (const { receipt, transcription } of ready) {
        if (receipt.learner_id !== owner) continue
        const draft = drafts.get(receipt.draft_key)
        if (draft?.transcription?.transcription_id === transcription.transcription_id) {
          if (draft.transcription.status !== transcription.status) { drafts.set(receipt.draft_key, { ...draft, transcription }); changed = true }
          continue
        }
        if (draft && draft.version !== receipt.draft_version) continue
        version.current = Math.max(version.current, receipt.draft_version)
        drafts.set(receipt.draft_key, { ...(draft || EMPTY_DRAFT), version: receipt.draft_version,
          transcription, confirmationText: transcription.machine_transcript, context: transcription.context })
        changed = true
      }
      return changed ? { owner, drafts } : previous
    })
  }, [owner, ready])
  useEffect(() => {
    if (!confirmation?.draft_key || !['turn', 'attempt', 'review'].includes(confirmation.action)) return
    const { draft_key, draft_version } = confirmation
    setState(previous => {
      if (previous.owner !== owner || previous.drafts.get(draft_key)?.version !== draft_version) return previous
      const drafts = new Map(previous.drafts)
      drafts.delete(draft_key)
      return { ...previous, drafts }
    })
  }, [confirmation, owner])
  const update = useCallback((key: string, changes: PracticeDraftChanges) => {
    if (!owner || currentOwner.current !== owner) return
    // A confirmation edit belongs to the same recording; replacing audio gets a new version.
    const nextVersion = 'confirmationText' in changes && Object.keys(changes).length === 1 ? null : ++version.current
    setState(previous => {
      const drafts = new Map(previous.owner === owner ? previous.drafts : [])
      const draft = drafts.get(key) || EMPTY_DRAFT
      drafts.set(key, { ...draft, ...changes, version: nextVersion ?? draft.version,
        ...('audio' in changes ? { transcription: null, confirmationText: '' } : {}) })
      return { owner, drafts }
    })
  }, [owner])
  const discard = useCallback((key: string, expectedVersion: number, preserveConfirmation = false) => {
    if (!owner || currentOwner.current !== owner) return
    const nextVersion = ++version.current
    setState(previous => {
      const draft = previous.drafts.get(key)
      if (previous.owner !== owner || draft && draft.version !== expectedVersion) return previous
      const drafts = new Map(previous.drafts)
      drafts.set(key, { ...(draft || EMPTY_DRAFT), text: draft?.text || (preserveConfirmation ? draft?.confirmationText || '' : ''),
        audio: null, transcription: null, confirmationText: '', context: null, version: nextVersion })
      return { ...previous, drafts }
    })
  }, [owner])
  const read = (key: string) => state.owner === owner ? state.drafts.get(key) || EMPTY_DRAFT : EMPTY_DRAFT
  return { read, update, discard }
}

export function practiceDraftKey(owner: string | undefined, session: string, ...context: string[]) {
  return JSON.stringify([owner, session, ...context])
}
