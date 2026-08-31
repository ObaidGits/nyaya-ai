/** Admin login card — credentials go straight to the backend, never stored. */

import { useState } from 'react'
import { adminLogin } from '../../lib/admin'
import { ScaleIcon } from '../icons'

interface Props {
  onAuthenticated: () => void
}

export function AdminLogin({ onAuthenticated }: Props) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    const failure = await adminLogin(username, password)
    setBusy(false)
    if (failure) {
      setError(failure.message)
      return
    }
    onAuthenticated()
  }

  return (
    <div className="flex min-h-dvh items-center justify-center p-6">
      <form
        onSubmit={submit}
        className="w-full max-w-sm rounded-xl border border-ink-200 bg-white p-6 shadow-sm dark:border-ink-800 dark:bg-ink-900"
        aria-label="Admin sign in"
      >
        <span className="mb-4 flex size-10 items-center justify-center rounded-lg bg-brand-700 text-white dark:bg-brand-500 dark:text-ink-950">
          <ScaleIcon className="size-5" />
        </span>
        <h1 className="font-serif text-xl font-bold">Nyaya admin console</h1>
        <p className="mt-1 mb-4 text-sm text-ink-500 dark:text-ink-400">
          Sign in with the administrator credentials configured on the server.
        </p>
        <label className="block text-sm font-medium" htmlFor="admin-username">
          Username
        </label>
        <input
          id="admin-username"
          autoComplete="username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          className="mb-3 mt-1 w-full rounded-lg border border-ink-300 bg-white px-3 py-2 text-sm outline-none transition-colors focus:border-brand-600 focus:ring-2 focus:ring-brand-600/30 dark:border-ink-700 dark:bg-ink-950"
        />
        <label className="block text-sm font-medium" htmlFor="admin-password">
          Password
        </label>
        <input
          id="admin-password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="mb-4 mt-1 w-full rounded-lg border border-ink-300 bg-white px-3 py-2 text-sm outline-none transition-colors focus:border-brand-600 focus:ring-2 focus:ring-brand-600/30 dark:border-ink-700 dark:bg-ink-950"
        />
        {error && (
          <p role="alert" className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/60 dark:text-red-300">
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={busy || !username || !password}
          className="w-full rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-700 disabled:opacity-50 dark:bg-brand-500 dark:text-ink-950 dark:hover:bg-brand-400"
        >
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}
