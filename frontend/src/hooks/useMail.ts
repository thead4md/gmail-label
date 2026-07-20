import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { EmailListItem } from '../lib/types'

export function useInbox(account: string | null, limit = 50) {
  return useQuery({
    queryKey: ['inbox', account, limit],
    queryFn: () => api.get<{ items: EmailListItem[] }>('/api/inbox', { account, limit }),
  })
}

export function useSearch(account: string | null, q: string) {
  return useQuery({
    queryKey: ['search', account, q],
    queryFn: () => api.get<{ items: EmailListItem[]; query: string }>('/api/search', { account, q, limit: 50 }),
    enabled: q.trim().length > 0,
  })
}

export function useFolders(account: string | null) {
  return useQuery({
    queryKey: ['folders-list', account],
    queryFn: () => api.get<string[]>('/api/folders', { account }),
  })
}

export function useFolderEmails(account: string | null, label: string | null) {
  return useQuery({
    queryKey: ['folder', account, label],
    queryFn: () => api.get<{ items: EmailListItem[]; label: string }>(`/api/folders/${encodeURIComponent(label!)}`, { account, limit: 50 }),
    enabled: !!label,
  })
}

export function useThread(account: string | null, threadId: string | null | undefined) {
  return useQuery({
    queryKey: ['thread', account, threadId],
    queryFn: () => api.get<EmailListItem[]>(`/api/inbox/threads/${threadId}`, { account }),
    enabled: !!threadId,
  })
}

export function useBulkAction(account: string | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { ids: string[]; action: 'label' | 'archive'; label?: string }) =>
      api.post<{ success: number; failed: number }>('/api/inbox/bulk', { ...body, account }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['inbox'] })
      qc.invalidateQueries({ queryKey: ['search'] })
      qc.invalidateQueries({ queryKey: ['folder'] })
    },
  })
}

export function useInboxLabels(account: string | null) {
  return useQuery({
    queryKey: ['inbox-labels', account],
    queryFn: () => api.get<string[]>('/api/inbox/labels', { account }),
  })
}
