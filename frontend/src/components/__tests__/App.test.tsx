import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from '../../App'

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation((url: string) =>
      Promise.resolve(
        String(url).includes('/api/v1/documents')
          ? new Response('[]', { status: 200 })
          : new Response(JSON.stringify([]), { status: 200 }),
      ),
    ),
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('App shell', () => {
  it('renders the two primary panels and switches between them', async () => {
    const user = userEvent.setup()
    render(<App />)

    expect(screen.getByRole('tab', { name: 'Chatbot', selected: true })).toBeTruthy()
    expect(screen.getByRole('tab', { name: 'Forms', selected: false })).toBeTruthy()

    await user.click(screen.getByRole('tab', { name: 'Forms' }))
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: 'Forms', selected: true })).toBeTruthy(),
    )
    expect(screen.getByRole('link', { name: /Download all/i })).toBeTruthy()
  })

  it('creates, renames, and deletes conversations', async () => {
    const user = userEvent.setup()
    render(<App />)

    // The app opens with one fresh conversation; rename it.
    await user.click(screen.getByRole('button', { name: 'Rename New conversation' }))
    const renameInput = await screen.findByLabelText('Rename New conversation')
    await user.clear(renameInput)
    await user.type(renameInput, 'My theft question{Enter}')
    expect(screen.getByText('My theft question')).toBeTruthy()

    // Delete is a two-step action: the first click only arms the button.
    await user.click(screen.getByRole('button', { name: 'Delete My theft question' }))
    expect(screen.getByText('My theft question')).toBeTruthy()
    // The second click on the armed button confirms the deletion.
    await user.click(screen.getByRole('button', { name: 'Confirm delete My theft question' }))
    await waitFor(() => expect(screen.getByText('No conversations yet.')).toBeTruthy())
  })

  it('toggles dark mode and persists it', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('button', { name: /Switch to dark mode/i }))
    expect(document.documentElement.classList.contains('dark')).toBe(true)
    expect(localStorage.getItem('nyaya.theme')).toBe('dark')

    await user.click(screen.getByRole('button', { name: /Switch to light mode/i }))
    expect(document.documentElement.classList.contains('dark')).toBe(false)
  })

  it('keeps one session id and regenerates it on reset', async () => {
    const user = userEvent.setup()
    render(<App />)

    const first = sessionStorage.getItem('nyaya.session-id')
    expect(first).toBeTruthy()

    // Reload-like remount keeps the session (same storage).
    const { unmount } = render(<App />)
    unmount()
    expect(sessionStorage.getItem('nyaya.session-id')).toBe(first)

    await user.click(screen.getByRole('button', { name: 'Start a new session' }))
    const second = sessionStorage.getItem('nyaya.session-id')
    expect(second).toBeTruthy()
    expect(second).not.toBe(first)
  })

  it('exposes a skip link for keyboard users', () => {
    render(<App />)
    const skip = screen.getByRole('link', { name: 'Skip to content' })
    expect(skip.getAttribute('href')).toBe('#main')
  })
})

describe('App routing (react-router)', () => {
  it('renders the admin console at /settings', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ enabled: true, authenticated: false }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )
    window.history.pushState({}, '', '/settings')
    render(<App />)
    expect(await screen.findByRole('form', { name: 'Admin sign in' })).toBeTruthy()
  })

  it('redirects unknown paths to the main app', async () => {
    window.history.pushState({}, '', '/nope')
    render(<App />)
    expect(await screen.findByRole('tab', { name: 'Chatbot', selected: true })).toBeTruthy()
  })
})
