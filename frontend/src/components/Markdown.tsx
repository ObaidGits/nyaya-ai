/**
 * Minimal Markdown renderer for streamed legal answers (C-021..C-023).
 *
 * Supports the constructs grounded answers actually produce — paragraphs,
 * fenced code blocks, block quotes, bullet/numbered lists, inline
 * bold/italic/code — without pulling in a markdown dependency. Unsupported
 * syntax falls through as plain text (never swallowed).
 */

import { Fragment, type ReactNode } from 'react'

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = []
  // Bold, italic, and inline code in one pass.
  const pattern = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g
  let lastIndex = 0
  let match: RegExpExecArray | null
  let i = 0
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) nodes.push(text.slice(lastIndex, match.index))
    const token = match[0]
    const key = `${keyPrefix}-i${i++}`
    if (token.startsWith('**')) {
      nodes.push(
        <strong key={key} className="font-semibold">
          {token.slice(2, -2)}
        </strong>,
      )
    } else if (token.startsWith('`')) {
      nodes.push(
        <code key={key} className="rounded bg-ink-100 px-1 py-0.5 font-mono text-[0.85em] dark:bg-ink-800">
          {token.slice(1, -1)}
        </code>,
      )
    } else {
      nodes.push(<em key={key}>{token.slice(1, -1)}</em>)
    }
    lastIndex = match.index + token.length
  }
  if (lastIndex < text.length) nodes.push(text.slice(lastIndex))
  return nodes
}

export function Markdown({ text }: { text: string }): ReactNode {
  const blocks: ReactNode[] = []
  const lines = text.split('\n')
  let index = 0
  let key = 0

  while (index < lines.length) {
    const line = lines[index]

    if (line.trim() === '') {
      index += 1
      continue
    }

    // Fenced code block.
    if (line.trim().startsWith('```')) {
      const code: string[] = []
      index += 1
      while (index < lines.length && !lines[index].trim().startsWith('```')) {
        code.push(lines[index])
        index += 1
      }
      index += 1 // closing fence
      blocks.push(
        <pre
          key={`b${key++}`}
          className="scroll-thin overflow-x-auto rounded-lg border border-ink-200 bg-ink-100 p-3 font-mono text-[13px] dark:border-ink-800 dark:bg-ink-950"
        >
          <code>{code.join('\n')}</code>
        </pre>,
      )
      continue
    }

    // Block quote.
    if (line.trimStart().startsWith('>')) {
      const quote: string[] = []
      while (index < lines.length && lines[index].trimStart().startsWith('>')) {
        quote.push(lines[index].trimStart().slice(1).trim())
        index += 1
      }
      blocks.push(
        <blockquote
          key={`b${key++}`}
          className="rounded-r-md border-l-4 border-brand-400 bg-brand-50/50 py-1.5 pl-3.5 pr-2 text-ink-700 italic dark:border-brand-500 dark:bg-brand-900/20 dark:text-ink-300"
        >
          {renderInline(quote.join(' '), `q${key}`)}
        </blockquote>,
      )
      continue
    }

    // Bullet list.
    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = []
      while (index < lines.length && /^\s*[-*]\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*[-*]\s+/, ''))
        index += 1
      }
      blocks.push(
        <ul key={`b${key++}`} className="list-disc space-y-1.5 pl-5 marker:text-ink-400">
          {items.map((item, i) => (
            <li key={i}>{renderInline(item, `l${key}-${i}`)}</li>
          ))}
        </ul>,
      )
      continue
    }

    // Numbered list.
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: string[] = []
      while (index < lines.length && /^\s*\d+[.)]\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*\d+[.)]\s+/, ''))
        index += 1
      }
      blocks.push(
        <ol key={`b${key++}`} className="list-decimal space-y-1.5 pl-5 marker:text-ink-400">
          {items.map((item, i) => (
            <li key={i}>{renderInline(item, `o${key}-${i}`)}</li>
          ))}
        </ol>,
      )
      continue
    }

    // Paragraph: consecutive plain lines.
    const paragraph: string[] = []
    while (
      index < lines.length &&
      lines[index].trim() !== '' &&
      !lines[index].trim().startsWith('```') &&
      !lines[index].trimStart().startsWith('>') &&
      !/^\s*[-*]\s+/.test(lines[index]) &&
      !/^\s*\d+[.)]\s+/.test(lines[index])
    ) {
      paragraph.push(lines[index])
      index += 1
    }
    blocks.push(<p key={`b${key++}`}>{renderInline(paragraph.join(' '), `p${key}`)}</p>)
  }

  return <div className="space-y-3">{blocks.map((b, i) => <Fragment key={i}>{b}</Fragment>)}</div>
}
