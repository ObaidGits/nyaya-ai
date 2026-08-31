/**
 * App-level UI smoke test: verifies the polished shell still exposes every
 * primary control after the visual overhaul (header, status indicator,
 * tabs, empty state, forms panel, session controls).
 */

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

describe('App UI smoke', () => {
  it('renders header with brand, status indicator, tabs, and theme toggle', () => {
    render(<App />)

    expect(screen.getByRole('heading', { name: 'Nyaya', level: 1 })).toBeTruthy()
    // Truthful brain indicator is present and reports the honest unknown
    // state when the readiness payload carries no model check.
    expect(screen.getByRole('status', { name: 'System status: Brain status unknown' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Switch to dark mode' })).toBeTruthy()
    expect(screen.getByRole('tab', { name: 'Chatbot', selected: true })).toBeTruthy()
    expect(screen.getByRole('tab', { name: 'Forms', selected: false })).toBeTruthy()
  })

  it('shows the chat empty state with clickable example questions', async () => {
    const user = userEvent.setup()
    render(<App />)

    const example = screen.getByRole('button', { name: /What is the punishment for murder\?/ })
    expect(example).toBeTruthy()
    // The other suggestion cards are present too.
    expect(screen.getByText('What is criminal conspiracy?')).toBeTruthy()

    // Clicking an example sends it as a user message (streamed answer is
    // stubbed with an empty SSE-ish response; the user bubble must appear).
    await user.click(example)
    await waitFor(() => {
      const messages = screen.getByRole('log', { name: 'Conversation messages' })
      expect(messages.textContent).toContain('What is the punishment for murder?')
    })
  })

  it('switches to the forms panel with search, filter, and bulk download', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('tab', { name: 'Forms' }))
    expect(await screen.findByLabelText('Search forms')).toBeTruthy()
    expect(screen.getByRole('link', { name: /Download all/i })).toBeTruthy()
    expect(screen.getByText('All')).toBeTruthy()
    expect(screen.getByText('Verified')).toBeTruthy()
  })

  it('exposes the mobile sidebar toggle and session controls', () => {
    render(<App />)

    expect(screen.getByRole('button', { name: 'Toggle conversations sidebar' })).toBeTruthy()
    expect(screen.getAllByRole('button', { name: 'New conversation' }).length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: 'Start a new session' })).toBeTruthy()
  })
})
