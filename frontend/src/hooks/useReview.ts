import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { NewSender, QueueItem } from '../lib/types'

export function useNewSenders(account: string | null) {
  return useQuery({
    queryKey: ['review-new-senders', account],
    queryFn: () => api.get<NewSender[]>('/api/review/new-senders', { account }),
  })
}

export function useSenderAction() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ sender, action }: { sender: string; action: 'know' | 'mute' | 'block' }) =>
      api.post(`/api/review/new-senders/${encodeURIComponent(sender)}/${action}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['review-new-senders'] })
      qc.invalidateQueries({ queryKey: ['automate'] })
    },
  })
}

export function usePendingQueue(account: string | null, offset: number, limit: number) {
  return useQuery({
    queryKey: ['review-pending', account, offset, limit],
    queryFn: () => api.get<{ total: number; items: QueueItem[] }>('/api/review/pending', { account, offset, limit }),
  })
}
