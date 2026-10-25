import { useCallback, useEffect, useRef, useState } from 'react'
import type { LearningConfirmation, PracticeDraft } from './types'

const EMPTY_DRAFT: PracticeDraft = { text: '', audio: null, version: 0 }

// Blobs stay in memory for this workspace. Only the request receipt is persisted.
export function usePracticeDrafts(owner: string | undefined, confirmation: LearningConfirmation | null) {
  const [state, setState] = useState<{ owner?: string; drafts: Map<string, PracticeDraft> }>({ owner, drafts: new Map() })
  const version = useRef(0)
  useEffect(() => { setState(previous => previous.owner === owner ? previous : { owner, drafts: new Map() }) }, [owner])
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
  const update = useCallback((key: string, changes: Partial<Pick<PracticeDraft, 'text' | 'audio'>>) => {
    if (!owner) return
    const nextVersion = ++version.current
    setState(previous => {
      const drafts = new Map(previous.owner === owner ? previous.drafts : [])
      drafts.set(key, { ...(drafts.get(key) || EMPTY_DRAFT), ...changes, version: nextVersion })
      return { owner, drafts }
    })
  }, [owner])
  const read = (key: string) => state.owner === owner ? state.drafts.get(key) || EMPTY_DRAFT : EMPTY_DRAFT
  return { read, update }
}

export function practiceDraftKey(owner: string | undefined, session: string, ...context: string[]) {
  return JSON.stringify([owner, session, ...context])
}
