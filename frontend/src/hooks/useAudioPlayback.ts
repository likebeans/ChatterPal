import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { audioSource } from '../audio/wav'

function releaseAudio(audio: HTMLAudioElement | null) {
  if (!audio) return
  audio.onended = null
  audio.onerror = null
  audio.pause()
  audio.removeAttribute('src')
  audio.load()
}

export function useAudioPlayback(active: boolean) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const [playingId, setPlayingId] = useState<string | null>(null)
  const playingRef = useRef<string | null>(null)
  const sequenceRef = useRef(0)
  const [error, setError] = useState('')

  const stop = useCallback(() => {
    sequenceRef.current++
    playingRef.current = null
    setPlayingId(null)
    setError('')
    releaseAudio(audioRef.current)
  }, [])

  useEffect(() => { if (!active) stop() }, [active, stop])
  useLayoutEffect(() => {
    // React detaches DOM refs before passive unmount cleanup. Capture the element
    // while it is attached so unmount still pauses and removes its event handlers.
    const audio = audioRef.current
    return () => {
      sequenceRef.current++
      playingRef.current = null
      releaseAudio(audio)
    }
  }, [])

  const play = useCallback(async (source: string, id: string, rate = 1) => {
    if (playingRef.current === id) { stop(); return }
    stop()
    const audio = audioRef.current
    if (!audio) return
    const sequence = sequenceRef.current
    const failed = () => {
      if (sequenceRef.current !== sequence) return
      stop()
      setError('音频播放失败，请重试或重新录制')
    }
    audio.src = audioSource(source)
    audio.playbackRate = rate
    audio.onended = () => { if (sequenceRef.current === sequence) stop() }
    audio.onerror = failed
    playingRef.current = id
    setPlayingId(id)
    try { await audio.play() } catch { failed() }
  }, [stop])

  return { audioRef, playingId, play, stop, error }
}
