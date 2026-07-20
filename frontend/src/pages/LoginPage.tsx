import { useState, type FormEvent } from 'react'
import { useLogin } from '../hooks/useAuth'
import { Button } from '../components/ui/Button'
import { ApiError } from '../lib/api'

export function LoginPage() {
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const login = useLogin()

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    try {
      await login.mutateAsync(password)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong.')
    }
  }

  return (
    <div className="flex h-screen items-center justify-center bg-bg">
      <form onSubmit={onSubmit} className="w-full max-w-xs">
        <div className="mb-1 flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent text-sm font-bold text-white">
            M
          </div>
          <div className="text-lg font-bold">MailMind</div>
        </div>
        <div className="mb-6 text-xs text-text-faint">Enter the dashboard password to continue.</div>
        <input
          type="password"
          autoFocus
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Password"
          className="mb-3 w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-[13px] outline-none focus:border-accent"
        />
        {error && <div className="mb-3 text-xs text-danger">{error}</div>}
        <Button type="submit" variant="primary" className="w-full" disabled={login.isPending}>
          {login.isPending ? 'Checking…' : 'Unlock'}
        </Button>
      </form>
    </div>
  )
}
