import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'

interface AuthStatus {
  required: boolean
  authenticated: boolean
}

export function useAuthStatus() {
  return useQuery({
    queryKey: ['auth-status'],
    queryFn: () => api.get<AuthStatus>('/api/auth/status'),
    staleTime: Infinity,
  })
}

export function useLogin() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (password: string) => api.post<{ authenticated: boolean }>('/api/auth/login', { password }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['auth-status'] }),
  })
}

export function useLogout() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.post('/api/auth/logout'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['auth-status'] }),
  })
}
