declare module 'wav-decoder' {
  export function decode(data: ArrayBuffer): Promise<{ sampleRate: number; channelData: Float32Array[] }>
}
