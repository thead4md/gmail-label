import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '../lib/api'

export interface Draft {
  id: number
  status: 'pending_review' | 'approved' | 'sent' | 'send_failed' | 'discarded'
  kind: string
  to_addrs: string
  cc_addrs: string | null
  subject: string
  body_text: string
  gmail_message_id: string | null
  in_reply_to_gmail_id: string | null
  thread_id: string | null
}

export function useDraft(draftId: number | null) {
  return useQuery({
    queryKey: ['draft', draftId],
    queryFn: () => api.get<Draft>(`/api/drafts/${draftId}`),
    enabled: draftId !== null,
    refetchInterval: false,
  })
}

function invalidateMailLists(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: ['now'] })
  qc.invalidateQueries({ queryKey: ['inbox'] })
  qc.invalidateQueries({ queryKey: ['thread'] })
  qc.invalidateQueries({ queryKey: ['search'] })
}

export function useReplyDefaults(gmailId: string | undefined) {
  return useQuery({
    queryKey: ['reply-defaults', gmailId],
    queryFn: () => api.get<{ to_addrs: string; subject: string; thread_id: string | null }>(
      `/api/drafts/reply-defaults/${gmailId}`,
    ),
    enabled: !!gmailId,
  })
}

export function useCreateDraft() {
  return useMutation({
    mutationFn: (body: {
      account: string | null
      in_reply_to_gmail_id?: string
      thread_id?: string | null
      to_addrs: string
      subject: string
      body_text: string
    }) => api.post<{ id: number }>('/api/drafts', body),
  })
}

export function useAiDraft() {
  // Takes gmail_id, not a draft_id: "Draft with AI" happens on the initial
  // compose form (Step 1, before Save Draft), so there is no draft row yet
  // to key off of — the endpoint reads the original message directly.
  return useMutation({
    mutationFn: (gmailId: string) => api.post<{ body_text: string }>('/api/drafts/ai-draft', { gmail_id: gmailId }),
  })
}

export function useApproveDraft(draftId: number | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.post(`/api/drafts/${draftId}/approve`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['draft', draftId] }),
  })
}

export function useDiscardDraft(draftId: number | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.post(`/api/drafts/${draftId}/discard`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['draft', draftId] }),
  })
}

export function useSendDraft(draftId: number | null, account: string | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.post(`/api/drafts/${draftId}/send`, { account }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['draft', draftId] })
      invalidateMailLists(qc)
    },
  })
}

export function draftErrorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message
  return 'Something went wrong.'
}
