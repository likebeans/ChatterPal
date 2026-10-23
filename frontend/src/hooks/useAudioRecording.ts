import { useCallback, useEffect, useRef, useState } from 'react'
import { encodeRecording } from '../audio/encodeRecording'

type RecordingStatus = 'idle' | 'requesting' | 'recording' | 'encoding'
interface Options {
  active?: boolean
  onAudioReady: (blob: Blob) => void
  onError: (message: string) => void
  onStart?: () => void
}
interface Resources {
  stream?: MediaStream
  context?: AudioContext
  source?: MediaStreamAudioSourceNode
  processor?: ScriptProcessorNode
  mute?: GainNode
  chunks: Float32Array[]
  removeTrackListeners?: () => void
  released?: boolean
}

function release(resources: Resources) {
  if (resources.released) return
  resources.released = true
  resources.removeTrackListeners?.()
  if (resources.processor) resources.processor.onaudioprocess = null
  // Cleanup continues even if a browser rejects disconnect/close on a partial graph.
  for (const node of [resources.source, resources.processor, resources.mute]) {
    try { node?.disconnect() } catch { /* Already disconnected. */ }
  }
  for (const track of resources.stream?.getTracks() ?? []) {
    try { track.stop() } catch { /* Continue releasing the remaining resources. */ }
  }
  if (resources.context && resources.context.state !== 'closed') {
    try { void resources.context.close().catch(() => {}) } catch { /* Already closing. */ }
  }
}

function recordingError(error: unknown): string {
  const name = error && typeof error === 'object' && 'name' in error ? error.name : ''
  if (typeof name === 'string' && ['NotAllowedError', 'SecurityError'].includes(name)) {
    return '无法访问麦克风，请检查权限设置后重试'
  }
  if (name === 'NotFoundError') return '未找到麦克风，请连接设备后重试'
  return '录音初始化失败，请检查麦克风后重试'
}

export function useAudioRecording({ active = true, onAudioReady, onError, onStart }: Options) {
  const [status, setStatus] = useState<RecordingStatus>('idle')
  const resourcesRef = useRef<Resources | null>(null)
  const encodingRef = useRef<AbortController | null>(null)
  const mountedRef = useRef(false)
  const callbacksRef = useRef({ active, onAudioReady, onError, onStart })
  callbacksRef.current = { active, onAudioReady, onError, onStart }

  const cancel = useCallback(() => {
    encodingRef.current?.abort()
    encodingRef.current = null
    const resources = resourcesRef.current
    resourcesRef.current = null
    if (resources) {
      release(resources)
      resources.chunks = []
    }
    if (mountedRef.current) setStatus('idle')
  }, [])

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      cancel()
    }
  }, [cancel])

  useEffect(() => {
    if (!active) cancel()
  }, [active, cancel])

  const start = useCallback(async () => {
    if (!mountedRef.current || !callbacksRef.current.active || resourcesRef.current || encodingRef.current) return
    const resources: Resources = { chunks: [] }
    resourcesRef.current = resources
    const isCurrent = () => mountedRef.current && callbacksRef.current.active && resourcesRef.current === resources
    setStatus('requesting')
    try {
      callbacksRef.current.onStart?.()
      if (!navigator.mediaDevices?.getUserMedia || !window.AudioContext) {
        throw new Error('Audio recording is not supported')
      }
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true } })
      // getUserMedia cannot be aborted. A cancelled permission request still owns its late stream.
      if (!isCurrent()) {
        stream.getTracks().forEach(track => track.stop())
        return
      }
      resources.stream = stream
      const context = new AudioContext()
      resources.context = context
      resources.source = context.createMediaStreamSource(stream)
      resources.processor = context.createScriptProcessor(4096, 1, 1)
      resources.mute = context.createGain()
      resources.mute.gain.value = 0
      resources.processor.onaudioprocess = ({ inputBuffer }) => {
        if (!isCurrent()) return
        const mono = new Float32Array(inputBuffer.length)
        for (let channel = 0; channel < inputBuffer.numberOfChannels; channel++) {
          const samples = inputBuffer.getChannelData(channel)
          for (let index = 0; index < mono.length; index++) mono[index] += samples[index] / inputBuffer.numberOfChannels
        }
        resources.chunks.push(mono)
      }
      resources.source.connect(resources.processor)
      resources.processor.connect(resources.mute)
      resources.mute.connect(context.destination)
      const ended = () => {
        if (!isCurrent()) return
        cancel()
        callbacksRef.current.onError('麦克风已断开，请连接设备后重新录音')
      }
      stream.getTracks().forEach(track => track.addEventListener('ended', ended))
      resources.removeTrackListeners = () => stream.getTracks().forEach(track => track.removeEventListener('ended', ended))
      if (context.state === 'suspended') await context.resume()
      if (!isCurrent()) return
      setStatus('recording')
    } catch (error) {
      const report = isCurrent()
      if (resourcesRef.current === resources) resourcesRef.current = null
      release(resources)
      resources.chunks = []
      if (report) {
        setStatus('idle')
        callbacksRef.current.onError(recordingError(error))
      }
    }
  }, [cancel])

  const stop = useCallback(() => {
    const resources = resourcesRef.current
    if (!resources) return
    resourcesRef.current = null
    release(resources)
    if (!mountedRef.current) return
    setStatus('idle')
    if (!callbacksRef.current.active || !resources.context) return
    const controller = new AbortController()
    encodingRef.current = controller
    const current = () => mountedRef.current && callbacksRef.current.active && encodingRef.current === controller && !controller.signal.aborted
    const ready = (blob: Blob) => {
      if (!current()) return
      encodingRef.current = null
      setStatus('idle')
      callbacksRef.current.onAudioReady(blob)
    }
    const failed = (error: unknown) => {
      if (!current()) return
      encodingRef.current = null
      setStatus('idle')
      callbacksRef.current.onError(error instanceof Error ? error.message : '音频编码失败，请重新录制')
    }
    try {
      const encoded = encodeRecording(resources.chunks, resources.context.sampleRate, controller.signal)
      resources.chunks = []
      if (encoded instanceof Blob) ready(encoded)
      else {
        setStatus('encoding')
        void encoded.then(ready, failed)
      }
    } catch (error) {
      resources.chunks = []
      failed(error)
    }
  }, [])

  return { status, start, stop, cancel, isRecording: status === 'recording', isBusy: status !== 'idle' }
}
