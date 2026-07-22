import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'

export function useApproveCalendarHold(account: string | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (holdId: number) => api.post(`/api/calendar-holds/${holdId}/approve`, { account }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['now'] }),
  })
}

export function useDiscardCalendarHold() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (holdId: number) => api.post(`/api/calendar-holds/${holdId}/discard`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['now'] }),
  })
}
