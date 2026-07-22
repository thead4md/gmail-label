import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'

export interface Project {
  id: number
  account: string | null
  thread_id: string
  title: string | null
  participants: Array<{ email: string; name: string | null }>
  action_items: string[]
  deadline_ts: number | null
  status: string
  created_at: number
  updated_at: number | null
}

export function useProjects(account: string | null, status: string | null = 'active') {
  return useQuery({
    queryKey: ['projects', account, status],
    queryFn: () => api.get<{ items: Project[] }>('/api/projects', { account, status }),
  })
}

export function useProject(projectId: number | null) {
  return useQuery({
    queryKey: ['project', projectId],
    queryFn: () => api.get<Project>(`/api/projects/${projectId}`),
    enabled: projectId !== null,
  })
}

export function usePromoteThreadToProject(account: string | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (threadId: string) => api.post<Project>(`/api/projects/from-thread/${threadId}`, { account }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['projects'] }),
  })
}

export function useCloseProject() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (projectId: number) => api.post(`/api/projects/${projectId}/close`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['projects'] }),
  })
}
