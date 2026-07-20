// Ported from mailmind/dashboard/theme.py so labels/channels/trust tiers keep
// the exact same colors the user already associates with them.

export const LABEL_COLORS: Record<string, string> = {
  URGENT: '#EF4444',
  WORK: '#6366F1',
  FINANCE: '#22C55E',
  PERSONAL: '#F59E0B',
  NOTIFICATION: '#747D8C',
  NEWSLETTER: '#A78BFA',
  SPAMCANDIDATE: '#FF6B81',
  DEFER: '#57606F',
  CALENDAR: '#1DBAB4',
  IMPORTANT: '#FF6348',
  MASS_EMAIL: '#FD79A8',
  ACTION_REQUIRED: '#FF7F50',
  MEETING: '#00CEC9',
  OE: '#00B894',
  'HIRDETES-L': '#E17055',
  'INFO-L': '#4285F4',
  '811/BCS': '#E84393',
  '811/CSPK LISTA': '#6C5CE7',
  'VÉLEMÉNY-L': '#F4B400',
}
const DEFAULT_LABEL_COLOR = '#6366F1'

const LABEL_PALETTE = [
  '#FF4757', '#6366F1', '#2ED573', '#FFA502', '#A78BFA', '#1DBAB4',
  '#FF6B81', '#4285F4', '#F4B400', '#0F9D58', '#E84393', '#00B894',
  '#E17055', '#6C5CE7', '#FD79A8', '#00CEC9', '#FAB1A0', '#A29BFE',
  '#55EFC4', '#FAB04F', '#74B9FF', '#FF7675',
]

function hashLabelColor(key: string): string {
  let h = 2166136261
  for (let i = 0; i < key.length; i++) {
    h = Math.imul(h ^ key.charCodeAt(i), 16777619)
  }
  const idx = ((h >>> 0) % LABEL_PALETTE.length + LABEL_PALETTE.length) % LABEL_PALETTE.length
  return LABEL_PALETTE[idx]
}

export function labelColor(label?: string | null): string {
  const key = (label || '').toUpperCase()
  if (!key) return DEFAULT_LABEL_COLOR
  return LABEL_COLORS[key] || hashLabelColor(key)
}

export const CHANNEL_COLORS: Record<string, string> = {
  newsletter: '#A78BFA',
  transactional: '#1DBAB4',
  team: '#6366F1',
  personal: '#F59E0B',
  marketing: '#FF6B81',
  automated: '#747D8C',
  docs: '#4285F4',
  calendar: '#0F9D58',
  tasks: '#F4B400',
  unknown: '#4A5568',
}

export const CHANNEL_ICONS: Record<string, string> = {
  newsletter: '📨',
  transactional: '🧾',
  team: '👥',
  personal: '💬',
  marketing: '📣',
  automated: '🤖',
}

export function channelColor(channel?: string | null): string {
  return CHANNEL_COLORS[(channel || 'unknown').toLowerCase()] || CHANNEL_COLORS.unknown
}

export function channelIcon(channel?: string | null): string {
  return CHANNEL_ICONS[(channel || '').toLowerCase()] || '📧'
}

export const TRUST_COLORS: Record<string, string> = {
  trusted: '#22C55E',
  neutral: '#F59E0B',
  watchlist: '#EF4444',
}

export function trustColor(tier?: string | null): string {
  return TRUST_COLORS[(tier || 'neutral').toLowerCase()] || TRUST_COLORS.neutral
}

export function confidenceColor(conf: number): string {
  if (conf > 0.8) return '#22C55E'
  if (conf > 0.5) return '#F59E0B'
  return '#EF4444'
}
