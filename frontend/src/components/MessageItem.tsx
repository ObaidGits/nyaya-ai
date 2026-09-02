/** One chat message: markdown answer, citation chips, copy, refused/error states. */

import { useState } from 'react'
import { Markdown } from './Markdown'
import { ListenButton } from './ListenButton'
import { matchCitations, type Citation } from '../lib/citations'
import type { ChatMessage } from '../lib/conversations'
import { BookOpenIcon, CheckIcon, CopyIcon, FileTextIcon, RefreshIcon, ScaleIcon, AlertIcon } from './icons'

interface MessageItemProps {
  message: ChatMessage
  streaming?: boolean
  isLastAssistant?: boolean
  onRegenerate?: () => void
  onSelectCitation: (citation: Citation) => void
  /** Session identity for speech synthesis (D-079). */
  sessionId?: string
}

export function MessageItem({
  message,
  streaming = false,
  isLastAssistant = false,
  onRegenerate,
  onSelectCitation,
  sessionId,
}: MessageItemProps) {
  const [copied, setCopied] = useState(false)
  const citations = matchCitations(message.content, message.sources ?? [])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(message.content)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
    }
  }

  if (message.role === 'user') {
    return (
      <div className="flex animate-rise justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-brand-700 px-4 py-2.5 text-white shadow-sm dark:bg-brand-500 dark:text-ink-950">
          <p className="whitespace-pre-wrap">{message.content}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="group max-w-full animate-rise">
      <div className="flex gap-3">
        <div
          className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full bg-brand-700 text-white shadow-sm dark:bg-brand-500 dark:text-ink-950"
          aria-hidden="true"
        >
          <ScaleIcon className="size-4" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="mb-1 text-xs font-semibold tracking-wide text-ink-500 dark:text-ink-400">
            Nyaya
          </p>
          <div
            className={`rounded-2xl rounded-tl-md border px-4 py-3 transition-colors ${
              message.error
                ? 'border-red-300 bg-red-50 dark:border-red-900 dark:bg-red-950/40'
                : 'border-ink-200 bg-white shadow-xs dark:border-ink-800 dark:bg-ink-900'
            }`}
          >
            <div
              aria-live={streaming ? 'polite' : undefined}
              className="text-[15px] leading-relaxed"
            >
              {streaming && !message.content ? (
                <span
                  className="inline-flex items-center gap-1 py-1.5"
                  aria-label="Generating answer"
                >
                  <span className="size-1.5 animate-pulse rounded-full bg-ink-400" />
                  <span className="size-1.5 animate-pulse rounded-full bg-ink-400 [animation-delay:150ms]" />
                  <span className="size-1.5 animate-pulse rounded-full bg-ink-400 [animation-delay:300ms]" />
                </span>
              ) : (
                <Markdown text={message.content} />
              )}
              {streaming && message.content && (
                <span className="ml-0.5 inline-block h-4 w-2 animate-pulse rounded-[1px] bg-brand-600 align-middle dark:bg-brand-400" />
              )}
            </div>

            {message.refused && (
              <p className="mt-2 flex items-start gap-1.5 rounded-lg border border-amber-300/80 bg-amber-50/70 px-2.5 py-1.5 text-xs text-amber-800 dark:border-amber-700/80 dark:bg-amber-950/40 dark:text-amber-300">
                <AlertIcon className="mt-0.5 size-3.5 shrink-0" />
                The assistant refused to answer because the retrieved source material was
                insufficient. Nothing was invented.
              </p>
            )}

            {citations.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {citations.map((citation) => (
                  <button
                    key={citation.label}
                    type="button"
                    onClick={() => onSelectCitation(citation)}
                    className="inline-flex items-center gap-1.5 rounded-full border border-brand-300/80 bg-brand-50 px-2.5 py-1 text-xs font-medium text-brand-800 transition-colors hover:border-brand-500 hover:bg-brand-100 dark:border-brand-700 dark:bg-brand-900/40 dark:text-brand-200 dark:hover:bg-brand-900"
                    aria-label={`Show source for ${citation.label}`}
                  >
                    {citation.source?.source_type === 'user_document' ? (
                      <FileTextIcon className="size-3 shrink-0 opacity-70" />
                    ) : (
                      <BookOpenIcon className="size-3 shrink-0 opacity-70" />
                    )}
                    {citation.label}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="mt-1 flex gap-1 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100">
            <button
              type="button"
              onClick={copy}
              className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-xs text-ink-500 transition-colors hover:bg-ink-100 hover:text-ink-800 dark:hover:bg-ink-800 dark:hover:text-ink-200"
              aria-label="Copy answer"
            >
              {copied ? <CheckIcon className="size-3.5" /> : <CopyIcon className="size-3.5" />}
              {copied ? 'Copied' : 'Copy'}
            </button>
            {!streaming && message.content && sessionId && (
              <ListenButton
                sessionId={sessionId}
                text={message.content}
                language={message.language ?? 'en'}
              />
            )}
            {isLastAssistant && !streaming && onRegenerate && (
              <button
                type="button"
                onClick={onRegenerate}
                className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-xs text-ink-500 transition-colors hover:bg-ink-100 hover:text-ink-800 dark:hover:bg-ink-800 dark:hover:text-ink-200"
                aria-label="Regenerate answer"
              >
                <RefreshIcon className="size-3.5" />
                Regenerate
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
