import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { QueueItem } from '../lib/types'

interface Kpi {
  icon: string
  label: string
  value: number
  delta: number | null
}

interface NowResponse {
  kpis: Kpi[]
  items: QueueItem[]
  gmail_labels: string[]
}

export function useNow(account: string | null) {
  return useQuery({
    queryKey: ['now', account],
    queryFn: () => api.get<NowResponse>('/api/now', { account }),
    refetchInterval: 30_000,
  })
}

export function useDailyBrief(account: string | null) {
  return useQuery({
    queryKey: ['now-brief', account],
    queryFn: () => api.get<{ brief: string | null }>('/api/now/brief', { account }),
    staleTime: 60 * 60_000,
  })
}

function invalidateQueueViews(qc: ReturnType<typeof useQueryClient>) {
  // Must match the actual queryKey prefixes used by useReview/useHistory
  // ('review-pending'/'review-new-senders', 'history-executed'/
  // 'history-corrections') — invalidating the bare strings 'review'/'history'
  // here doesn't match any real key (React Query matches by exact prefix
  // elements), so approve/reject/label/correct silently left the Review and
  // History pages showing stale data until an unrelated remount or
  // window-focus refetch.
  qc.invalidateQueries({ queryKey: ['now'] })
  qc.invalidateQueries({ queryKey: ['review-pending'] })
  qc.invalidateQueries({ queryKey: ['review-new-senders'] })
  qc.invalidateQueries({ queryKey: ['history-executed'] })
  qc.invalidateQueries({ queryKey: ['history-corrections'] })
  qc.invalidateQueries({ queryKey: ['automate'] })
  qc.invalidateQueries({ queryKey: ['insights'] })
}

export function useApproveQueueItem(account: string | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, correctedLabel }: { id: number; correctedLabel?: string }) =>
      api.post(`/api/queue/${id}/approve`, { account, corrected_label: correctedLabel }),
    onSuccess: () => invalidateQueueViews(qc),
  })
}

export function useRejectQueueItem() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api.post(`/api/queue/${id}/reject`, {}),
    onSuccess: () => invalidateQueueViews(qc),
  })
}

export function useLabelQueueItem(account: string | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, label, scope }: { id: number; label: string; scope: 'email' | 'thread' | 'sender' }) =>
      api.post(`/api/queue/${id}/label`, { account, label, scope }),
    onSuccess: () => invalidateQueueViews(qc),
  })
}

export function useCorrectQueueItem(account: string | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, label }: { id: number; label: string }) =>
      api.post(`/api/queue/${id}/correct`, { account, label }),
    onSuccess: () => invalidateQueueViews(qc),
  })
}
