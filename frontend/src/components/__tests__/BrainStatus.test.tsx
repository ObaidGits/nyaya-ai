/** BrainStatus derives its label ONLY from the backend's classified probe
 * (GET /api/v1/health/llm) — never from local state like "settings exist". */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { BrainStatus } from '../BrainStatus'

function stubHealth(state: string | null, ok = true) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      ok
        ? new Response(JSON.stringify({ state, detail: '' }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          })
        : new Response('boom', { status: 500 }),
    ),
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('BrainStatus', () => {
  it('shows "Brain active" only for a healthy classified probe', async () => {
    stubHealth('healthy')
    render(<BrainStatus />)
    expect(await screen.findByText('Brain active')).toBeTruthy()
  })

  it.each([
    ['degraded', 'Brain degraded'],
    ['invalid_configuration', 'Brain misconfigured'],
    ['unavailable', 'Brain unavailable'],
    ['not_configured', 'Brain not configured'],
  ])('never claims active for backend state %s', async (state, label) => {
    stubHealth(state)
    render(<BrainStatus />)
    expect(await screen.findByText(label)).toBeTruthy()
    expect(screen.queryByText('Brain active')).toBeNull()
  })

  it('reports unknown when the health endpoint fails or is unreadable', async () => {
    stubHealth('healthy', false)
    render(<BrainStatus />)
    expect(await screen.findByText('Brain status unknown')).toBeTruthy()
  })
})
