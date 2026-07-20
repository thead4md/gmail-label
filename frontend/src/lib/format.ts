export function timeAgo(ts?: number | null): string {
  if (!ts) return 'Never'
  const deltaSec = Math.floor(Date.now() / 1000) - ts
  if (deltaSec < 60) return '< 1 min ago'
  if (deltaSec < 3600) return `${Math.floor(deltaSec / 60)} min ago`
  if (deltaSec < 86400) return `${Math.floor(deltaSec / 3600)}h ago`
  return `${Math.floor(deltaSec / 86400)}d ago`
}

export function formatTs(ts?: number | null): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toISOString().replace('T', ' ').slice(0, 19)
}

export function truncate(s: string | null | undefined, n: number): string {
  const v = s || ''
  return v.length > n ? v.slice(0, n) + '…' : v
}

export function extractDisplayName(sender: string | null | undefined): string {
  const s = (sender || 'Unknown').trim()
  return s.split('<')[0].trim() || s
}

export function extractEmailAddr(sender: string | null | undefined): string {
  const s = sender || ''
  if (s.includes('<') && s.includes('>')) {
    return s.split('<')[1].split('>')[0].trim()
  }
  return s.trim()
}

export function initial(sender: string | null | undefined): string {
  const name = extractDisplayName(sender)
  return (name[0] || '?').toUpperCase()
}

const AVATAR_HUES: Record<string, number> = {
  A: 220, B: 170, C: 280, D: 30, E: 190, F: 340, G: 120, H: 200,
  I: 260, J: 40, K: 155, L: 310, M: 20, N: 230, O: 50, P: 270,
  Q: 80, R: 0, S: 140, T: 200, U: 310, V: 60, W: 180, X: 300,
  Y: 90, Z: 230,
}

export function avatarColor(sender: string | null | undefined): string {
  const hue = AVATAR_HUES[initial(sender)] ?? 220
  return `hsl(${hue}, 55%, 45%)`
}
