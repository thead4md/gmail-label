import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api } from '../lib/api'

interface Heartbeat {
  status: 'never' | 'fresh' | 'stale'
  seconds_ago: number | null
  human: string
}

interface Meta {
  accounts: string[]
  heartbeat: Heartbeat
}

export function useMeta() {
  return useQuery({
    queryKey: ['meta'],
    queryFn: () => api.get<Meta>('/api/meta'),
    staleTime: 60_000,
    refetchInterval: 60_000,
  })
}

const STORAGE_KEY = 'mm_account'

/** Currently-selected mailbox account, persisted across reloads. Undefined
 * account (single-mailbox deployments) resolves to null, matching the old
 * dashboard's sidebar behavior of only showing a switcher for 2+ accounts. */
export function useAccount(): [string | null, (a: string | null) => void, string[]] {
  const { data } = useMeta()
  const accounts = data?.accounts ?? []
  const [account, setAccountState] = useState<string | null>(() => localStorage.getItem(STORAGE_KEY))

  useEffect(() => {
    if (accounts.length === 0) return
    if (account && accounts.includes(account)) return
    setAccountState(accounts[0])
  }, [accounts, account])

  const setAccount = (a: string | null) => {
    setAccountState(a)
    if (a) localStorage.setItem(STORAGE_KEY, a)
    else localStorage.removeItem(STORAGE_KEY)
  }

  return [accounts.length > 1 ? account : accounts[0] ?? null, setAccount, accounts]
}
