/** D-098: the secret field surfaces server-side decryption failures. */

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { SecretField } from '../admin/SecretField'
import type { SettingField } from '../../lib/adminSchema'

const field: SettingField = {
  key: 'llm_api_key',
  label: 'API key',
  kind: 'secret',
} as SettingField

describe('SecretField persistence states (D-098)', () => {
  it('shows the unreadable-key warning when the stored key cannot be decrypted', () => {
    render(
      <SecretField
        field={field}
        value=""
        secretSet={false}
        source="env"
        unreadable
        onChange={() => {}}
      />,
    )
    const alert = screen.getByRole('alert')
    expect(alert.textContent).toContain('cannot be decrypted')
    expect(alert.textContent).toContain('preserved')
  })

  it('describes the saved key as stored encrypted when readable', () => {
    render(
      <SecretField
        field={field}
        value=""
        secretSet
        source="console"
        onChange={() => {}}
      />,
    )
    expect(screen.getByText(/stored encrypted/i)).toBeTruthy()
  })
})
