import { useCallback, useEffect, useRef } from 'react'

export function useObjectUrls() {
  const ownedRef = useRef(new Set<string>())
  const create = useCallback((blob: Blob) => {
    const url = URL.createObjectURL(blob)
    ownedRef.current.add(url)
    return url
  }, [])
  const revoke = useCallback((url: string) => {
    if (ownedRef.current.delete(url)) URL.revokeObjectURL(url)
  }, [])
  const clear = useCallback(() => {
    for (const url of ownedRef.current) URL.revokeObjectURL(url)
    ownedRef.current.clear()
  }, [])
  useEffect(() => clear, [clear])
  return { create, revoke, clear }
}
