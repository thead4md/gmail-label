import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { QueueItem } from '../lib/types'

export interface Correction {
  created_at: number
  email_gmail_id: string
  original_label: string
  corrected_label: string
  source: string
}

export function useExecutedHistory(account: string | null, days: number) {
  return useQuery({
    queryKey: ['history-executed', account, days],
    queryFn: () => api.get<{ total: number; items: QueueItem[] }>('/api/history/executed', { account, days, limit: 100 }),
  })
}

export function useCorrections() {
  return useQuery({
    queryKey: ['history-corrections'],
    queryFn: () => api.get<Correction[]>('/api/history/corrections', { limit: 50 }),
  })
}
