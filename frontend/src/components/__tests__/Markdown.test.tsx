import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Markdown } from '../Markdown'

describe('Markdown', () => {
  it('renders paragraphs with inline bold/italic/code', () => {
    render(<Markdown text={'Plain **bold** and *italic* and `code`.'} />)
    expect(screen.getByText('bold').tagName).toBe('STRONG')
    expect(screen.getByText('italic').tagName).toBe('EM')
    expect(screen.getByText('code').tagName).toBe('CODE')
  })

  it('renders fenced code blocks distinctly', () => {
    render(<Markdown text={'```\nsection 103\n```\n'} />)
    expect(screen.getByText('section 103').closest('pre')).not.toBeNull()
  })

  it('renders block quotes', () => {
    render(<Markdown text={'> Whoever commits murder\n> shall be punished.'} />)
    const quote = document.querySelector('blockquote')
    expect(quote?.textContent).toContain('Whoever commits murder')
  })

  it('renders bullet and numbered lists', () => {
    const { container } = render(<Markdown text={'- one\n- two\n\n1. first\n2. second'} />)
    expect(container.querySelectorAll('ul li')).toHaveLength(2)
    expect(container.querySelectorAll('ol li')).toHaveLength(2)
  })
})
