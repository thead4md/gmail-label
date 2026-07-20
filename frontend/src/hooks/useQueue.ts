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
  qc.invalidateQueries({ queryKey: ['now'] })
  qc.invalidateQueries({ queryKey: ['review'] })
  qc.invalidateQueries({ queryKey: ['history'] })
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
