import type { ReactNode } from 'react'

const paths = {
  chat: <><path d="M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9H13a8.5 8.5 0 0 1 8 8v.5Z" /><path d="M8 10h8M8 14h5" /></>,
  mic: <><rect x="9" y="2" width="6" height="13" rx="3" /><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8" /></>,
  play: <path d="m9 5 11 7-11 7V5Z" />,
  pause: <><path d="M8 5v14M16 5v14" /></>,
  stop: <rect x="5" y="5" width="14" height="14" rx="2" />,
  check: <path d="m5 12 4 4L19 6" />,
  'arrow-right': <><path d="M4 12h16m-6-6 6 6-6 6" /></>,
  'arrow-left': <><path d="M20 12H4m6-6-6 6 6 6" /></>,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  weekend: <><path d="M3 20h18M5 17l5-9 4 6 3-4 4 7M5 4v3M3.5 5.5h3" /><circle cx="17" cy="5" r="2" /></>,
  restaurant: <><path d="M4 3v6a3 3 0 0 0 6 0V3M7 3v18M18 3c-3 3-4 6-4 9h5V3h-1Zm1 9v9" /></>,
  hotel: <><rect x="5" y="3" width="14" height="18" rx="2" /><path d="M9 21v-5h6v5M9 7h1m4 0h1M9 11h1m4 0h1M3 21h18" /></>,
  history: <><path d="M3 11a9 9 0 1 1 2.6 7M3 4v7h7M12 7v5l3 2" /></>,
  review: <><path d="m17 2 4 4-4 4M3 11V9a3 3 0 0 1 3-3h15M7 22l-4-4 4-4m14-1v2a3 3 0 0 1-3 3H3" /></>,
  progress: <><path d="M4 20h17M7 16v-4m5 4V8m5 8V4" /></>,
  settings: <><path d="M4 7h16M4 17h16" /><circle cx="9" cy="7" r="3" /><circle cx="15" cy="17" r="3" /></>,
  spark: <><path d="m12 3 2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4L12 3Z" /></>,
  close: <path d="m6 6 12 12M6 18 18 6" />,
  headphones: <><path d="M4 13v-1a8 8 0 0 1 16 0v1" /><rect x="3" y="12" width="4" height="8" rx="2" /><rect x="17" y="12" width="4" height="8" rx="2" /></>,
  book: <><path d="M12 6c-3-2-6-2-9-1v15c3-1 6-1 9 1 3-2 6-2 9-1V5c-3-1-6-1-9 1Zm0 0v15" /></>,
} satisfies Record<string, ReactNode>

export type IconName = keyof typeof paths

export function Icon({ name, size = 20, className = '' }: { name: IconName; size?: number; className?: string }) {
  return <svg className={`ui-icon ${className}`} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">{paths[name]}</svg>
}
