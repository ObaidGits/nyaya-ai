import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SourceDrawer } from '../SourceDrawer'
import type { Citation } from '../../lib/citations'

const citation: Citation = {
  label: 'BNS s.103(1)',
  source: {
    citation: 'BNS s.103(1)',
    source_type: 'statute',
    act: 'Bharatiya Nyaya Sanhita, 2023',
    section_number: '103(1)',
    section_title: 'Murder',
    text: 'Whoever commits murder shall be punished with death or imprisonment for life.',
    page_start: 45,
    page_end: 45,
  },
}

describe('SourceDrawer', () => {
  it('shows only backend-provided source details', () => {
    render(<SourceDrawer citation={citation} onClose={() => {}} />)
    expect(screen.getByRole('dialog', { name: 'Source for BNS s.103(1)' })).toBeTruthy()
    expect(screen.getByText('Bharatiya Nyaya Sanhita, 2023')).toBeTruthy()
    expect(screen.getByText(/Whoever commits murder/)).toBeTruthy()
  })

  it('renders the no-source state instead of inventing details', () => {
    render(<SourceDrawer citation={{ label: 'BNS s.999', source: undefined }} onClose={() => {}} />)
    expect(screen.getByText(/no matching retrieved source on record/i)).toBeTruthy()
  })

  it('closes on Escape', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(<SourceDrawer citation={citation} onClose={onClose} />)
    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('traps Tab focus inside the open drawer', async () => {
    const user = userEvent.setup()
    render(
      <div>
        <button type="button">outside button</button>
        <SourceDrawer citation={citation} onClose={() => {}} />
      </div>,
    )
    // Initial focus lands on the close button.
    await screen.findByRole('dialog')
    expect(document.activeElement?.getAttribute('aria-label')).toBe('Close')
    // Tab cycles within the dialog; focus never reaches the page behind it.
    await user.tab()
    await user.tab()
    await user.tab()
    const dialog = screen.getByRole('dialog')
    expect(dialog.contains(document.activeElement)).toBe(true)
  })
})
