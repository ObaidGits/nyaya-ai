import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FormsPanel } from '../FormsPanel'

const FORMS = [
  {
    form_number: 12,
    title: 'Warrant to Search Suspected Place of Deposit',
    source_page_start: 201,
    source_page_end: 201,
    output_filename: 'FORM-12_Warrant-to-Search.pdf',
    byte_size: 12345,
    needs_review: false,
  },
  {
    form_number: 33,
    title: 'CHARGES',
    source_page_start: 222,
    source_page_end: 224,
    output_filename: 'FORM-33_Charges.pdf',
    byte_size: 626218,
    needs_review: false,
  },
]

afterEach(() => vi.unstubAllGlobals())

function mockForms(urlMatch: (url: string) => Response | Promise<Response>) {
  vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => Promise.resolve(urlMatch(String(url)))))
}

describe('FormsPanel', () => {
  it('lists forms with metadata and download links', async () => {
    mockForms((url) =>
      url.includes('/search')
        ? new Response('[]', { status: 200 })
        : new Response(JSON.stringify(FORMS), { status: 200 }),
    )
    render(<FormsPanel />)

    expect(await screen.findByText('Form 12')).toBeTruthy()
    expect(screen.getByText('CHARGES')).toBeTruthy()
    const pageText = document.body.textContent ?? ''
    expect(pageText).toContain('Pages 222–224')
    expect(pageText).toContain('Pages 201–201')

    const download = screen.getAllByRole('link', { name: 'Download' })[0] as HTMLAnchorElement
    expect(download.getAttribute('href')).toBe('/api/v1/forms/12/download')
    expect(download.getAttribute('download')).toBe('FORM-12_Warrant-to-Search.pdf')
  })

  it('searches forms by query', async () => {
    const user = userEvent.setup()
    mockForms((url) => {
      if (url.includes('q=warrant')) {
        return Promise.resolve(new Response(JSON.stringify([FORMS[0]]), { status: 200 }))
      }
      return Promise.resolve(new Response(JSON.stringify(FORMS), { status: 200 }))
    })
    render(<FormsPanel />)
    await screen.findByText('Form 12')

    await user.type(screen.getByLabelText('Search forms'), 'warrant')
    await waitFor(() => expect(screen.queryByText('Form 33')).toBeNull())
    expect(screen.getByText('Form 12')).toBeTruthy()
  })

  it('shows a friendly error when the forms service is unavailable', async () => {
    mockForms(() =>
      Promise.resolve(
        new Response(JSON.stringify({ error: { code: 'FORMS_NOT_CONFIGURED', message: 'not configured' } }), {
          status: 503,
        }),
      ),
    )
    render(<FormsPanel />)
    expect(await screen.findByText('not configured')).toBeTruthy()
  })

  it('shows an empty state when no forms match', async () => {
    mockForms((url) =>
      url.includes('/search')
        ? new Response('[]', { status: 200 })
        : new Response(JSON.stringify(FORMS), { status: 200 }),
    )
    render(<FormsPanel />)
    await screen.findByText('Form 12')

    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Search forms'), 'zzz')
    expect(await screen.findByText(/No forms match this search/i)).toBeTruthy()
  })

  it('offers a bulk ZIP download', () => {
    mockForms(() => Promise.resolve(new Response('[]', { status: 200 })))
    render(<FormsPanel />)
    const zip = screen.getByRole('link', { name: /Download all/i }) as HTMLAnchorElement
    expect(zip.getAttribute('href')).toBe('/api/v1/forms/download-all')
  })

  it('opens a PDF preview modal', async () => {
    const user = userEvent.setup()
    mockForms((url) => {
      if (url.includes('/download')) {
        return Promise.resolve(new Response('%PDF-1.4 preview', { status: 200 }))
      }
      return Promise.resolve(new Response(JSON.stringify(FORMS), { status: 200 }))
    })
    render(<FormsPanel />)
    await user.click(await screen.findAllByRole('button', { name: 'Preview' }).then((b) => b[0]))

    const dialog = await screen.findByRole('dialog')
    expect(dialog.textContent).toContain('Warrant to Search')
    expect(document.querySelector('iframe')).toBeTruthy()

    await user.click(screen.getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})
