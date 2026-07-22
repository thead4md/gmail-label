import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'

export interface RelationshipRank {
  sender_email: string
  display_name: string | null
  rank_score: number
  vip: boolean
  trust_tier: string
  approval_rate: number
  reciprocity_days: number | null
  email_count: number
}

export interface InsightsResponse {
  label_distribution: Array<{ label: string; count: number }>
  channel_distribution: Array<{ channel: string; count: number }>
  channel_weekday: Array<{ channel: string; weekday: number; count: number }>
  top_senders: Array<{ sender: string; volume: number; approval_rate: number }>
  decision_times: Array<{ minutes: number }>
  tier_quality: Array<{ source: string; total: number; corrections: number; correction_rate: number }>
  autopilot_precision: { auto_executed: number; later_corrected: number; precision: number | null }
  llm_cost: {
    calls: number
    cost_usd: number
    tokens: number
    avg_latency_ms: number
    by_kind: Array<{ model: string; kind: string; calls: number; cost_usd: number }>
  }
  relationships: RelationshipRank[]
}

export function useInsights(account: string | null, days: number) {
  return useQuery({
    queryKey: ['insights', account, days],
    queryFn: () => api.get<InsightsResponse>('/api/insights', { account, days }),
  })
}
