import { useCallback, useEffect, useRef } from 'react'

// Aborting saves client work; identity guards also reject responses from adapters/providers
// that have already completed, or ignore cancellation.
export function useLatestRequest(active = true) {
  const controllerRef = useRef<AbortController | null>(null)
  const mountedRef = useRef(false)
  const activeRef = useRef(active)
  activeRef.current = active
  const cancel = useCallback(() => {
    controllerRef.current?.abort()
    controllerRef.current = null
  }, [])

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      cancel()
    }
  }, [cancel])

  useEffect(() => { if (!active) cancel() }, [active, cancel])

  const begin = useCallback(() => {
    cancel()
    const controller = new AbortController()
    controllerRef.current = controller
    return {
      signal: controller.signal,
      isCurrent: () => mountedRef.current && activeRef.current && controllerRef.current === controller && !controller.signal.aborted,
      finish: () => { if (controllerRef.current === controller) controllerRef.current = null },
    }
  }, [cancel])
  const isPending = useCallback(() => controllerRef.current !== null, [])
  return { begin, cancel, isPending }
}
