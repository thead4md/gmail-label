import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { SenderProfile } from '../lib/types'

export interface LabelSuggestion {
  id: number
  suggested_label: string
  rationale: string | null
  cluster_terms: string | null
  email_count: number
}

export interface AutomateResponse {
  digest: {
    classified: number
    executed: number
    queued: number
    corrections: number
    execute_failed: number
    pending_reply_needed: number
    top_labels: Array<{ label: string; count: number }>
  }
  sender_profiles: SenderProfile[]
  label_priorities: Record<string, number>
  all_labels: string[]
  model_health: { created_at: number; accuracy: number | null; training_samples: number } | null
  newsletters: Array<{ sender: string; unsubscribe_url: string; email_count: number }>
  queue_stats: Record<string, number>
  label_suggestions: LabelSuggestion[]
}

export function useAutomate(account: string | null, days: number) {
  return useQuery({
    queryKey: ['automate', account, days],
    queryFn: () => api.get<AutomateResponse>('/api/automate', { account, days }),
  })
}

export function useSetAutopilot() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ email, enabled }: { email: string; enabled: boolean }) =>
      api.post(`/api/automate/senders/${encodeURIComponent(email)}/autopilot`, { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['automate'] }),
  })
}

export function useSetLabelPriority() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { label: string; weight: number }) => api.post('/api/automate/label-priority', body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['automate'] }),
  })
}

export function useCreateNlRule(account: string | null) {
  return useMutation({
    mutationFn: (text: string) =>
      api.post<{ sender: string; label: string; match_pattern: string | null }>('/api/automate/rules/nl', { account, text }),
  })
}

export function useDecideLabelSuggestion() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, decision }: { id: number; decision: 'accepted' | 'dismissed' }) =>
      api.post(`/api/automate/label-suggestions/${id}/${decision}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['automate'] }),
  })
}
