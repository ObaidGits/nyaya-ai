/** Chat panel: message list, streaming, composer, examples, documents, disclaimer. */

import { useEffect, useRef, useState } from 'react'
import { MessageItem } from './MessageItem'
import { DocumentsPanel } from './DocumentsPanel'
import { LanguageSelector } from './LanguageSelector'
import { VoiceInput } from './VoiceInput'
import { FileTextIcon, ScaleIcon, SendIcon, StopIcon } from './icons'
import { useDocuments } from '../hooks/useDocuments'
import { streamChat } from '../lib/sse'
import type { Citation } from '../lib/citations'
import type { ChatMessage, Conversation } from '../lib/conversations'
import type { Source } from '../types'

interface ChatPanelProps {
  sessionId: string
  conversation: Conversation
  onMessagesChange: (messages: ChatMessage[]) => void
  onSelectCitation: (citation: Citation) => void
  /** Answer-language preference ("auto" or a supported code, D-077). */
  language: string
  onLanguageChange: (language: string) => void
}

/** Monotonic id source: stable across re-renders, unique per message. */
let messageCounter = 0
const nextMessageId = (suffix: string) => `m-${(messageCounter += 1)}-${suffix}`

const EXAMPLES = [
  'What is the punishment for murder?',
  'What does section 103 BNS say?',
  'What is criminal conspiracy?',
  'Explain the difference between theft and extortion.',
]

export function ChatPanel({
  sessionId,
  conversation,
  onMessagesChange,
  onSelectCitation,
  language,
  onLanguageChange,
}: ChatPanelProps) {
  const [input, setInput] = useState('')
  const [streamingMessage, setStreamingMessage] = useState<ChatMessage | null>(null)
  const [streaming, setStreaming] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  // Stick-to-bottom: autoscroll follows the stream only while the user is at
  // (or near) the bottom. Scrolling up to reread pauses it — the answer keeps
  // streaming without yanking the viewport back down on every token.
  const stickToBottomRef = useRef(true)
  const documents = useDocuments(sessionId)

  const onScroll = () => {
    const el = scrollRef.current
    if (!el) return
    stickToBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  }

  useEffect(() => {
    if (!stickToBottomRef.current) return
    scrollRef.current?.scrollTo?.({ top: scrollRef.current.scrollHeight })
  }, [conversation.messages.length, streamingMessage?.content])

  const send = async (messageText: string, historyOverride?: ChatMessage[]) => {
    const text = messageText.trim()
    if (!text || streaming) return

    const userMessage: ChatMessage = {
      id: nextMessageId('u'),
      role: 'user',
      content: text,
    }
    const baseMessages = historyOverride ?? [...conversation.messages, userMessage]
    if (!historyOverride) onMessagesChange(baseMessages)
    setInput('')

    const assistant: ChatMessage = {
      id: nextMessageId('a'),
      role: 'assistant',
      content: '',
    }
    setStreamingMessage(assistant)
    setStreaming(true)

    const controller = new AbortController()
    abortRef.current = controller
    let answer = ''
    let sources: Source[] = []
    let refused = false
    let answerLanguage = 'en'
    let error: ChatMessage['error'] | undefined

    try {
      await streamChat(
        sessionId,
        text,
        baseMessages
          .filter((m) => m.role === 'user' || (m.role === 'assistant' && !m.refused && !m.error))
          .map((m) => ({ role: m.role, content: m.content })),
        {
          onToken: (token) => {
            answer += token
            setStreamingMessage({ ...assistant, content: answer })
          },
          onSources: (payload) => {
            sources = payload
          },
          onDone: (meta) => {
            refused = meta.refused
            answerLanguage = meta.language ?? 'en'
          },
          onError: (payload) => {
            error = { code: payload.code, message: payload.message }
          },
        },
        controller.signal,
        language,
      )
    } catch {
      // Unexpected stream failure: surface it but NEVER leave the
      // composer stuck in the streaming state.
      error = { code: 'STREAM_FAILED', message: 'The response stream failed.' }
    } finally {
      abortRef.current = null
      setStreaming(false)
      setStreamingMessage(null)
    }

    const final: ChatMessage = {
      ...assistant,
      content: answer || error?.message || '',
      sources,
      refused,
      error,
      language: answerLanguage,
    }
    onMessagesChange([...baseMessages, final])
  }

  const stop = () => {
    abortRef.current?.abort()
  }

  const regenerate = () => {
    // Re-ask the last user message, dropping the previous assistant answer.
    const messages = conversation.messages
    let lastUserIndex = -1
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i].role === 'user') {
        lastUserIndex = i
        break
      }
    }
    if (lastUserIndex === -1) return
    const history = messages.slice(0, lastUserIndex + 1)
    const question = messages[lastUserIndex].content
    onMessagesChange(history)
    void send(question, history)
  }

  const messages = conversation.messages
  const lastMessage = messages[messages.length - 1]

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex items-center justify-between border-b border-ink-200 px-4 py-2.5 dark:border-ink-800">
        <div>
          <h2 className="font-serif text-base font-semibold leading-tight">Chatbot</h2>
          <p className="text-xs text-ink-500 dark:text-ink-400">Answers grounded in retrieved sources</p>
        </div>
        <div className="flex items-center gap-2">
          <LanguageSelector language={language} onChange={onLanguageChange} disabled={streaming} />
          <p
            className="rounded-full border border-amber-300 bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-800 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-300"
            title="Answers are generated from retrieved source material and are not legal advice."
          >
            Not legal advice
          </p>
        </div>
      </header>

      {/* Explicit minmax(0,1fr) rows keep children bounded by the grid's
          flex-1 height budget; auto rows would size to content and push the
          page taller than the viewport. */}
      <div className="grid min-h-0 flex-1 grid-rows-[minmax(0,1fr)_auto] lg:grid-cols-[minmax(0,1fr)_300px] lg:grid-rows-[minmax(0,1fr)]">
        <div className="flex min-h-0 flex-col">
          <div
            ref={scrollRef}
            onScroll={onScroll}
            className="scroll-thin flex-1 overflow-y-auto"
            role="log"
            aria-label="Conversation messages"
          >
            {messages.length === 0 && !streamingMessage && (
              <div className="mx-auto flex min-h-full max-w-2xl flex-col items-center justify-center px-4 py-10 text-center">
                <div className="flex size-14 items-center justify-center rounded-2xl border border-brand-200 bg-brand-50 dark:border-brand-800 dark:bg-brand-900/40">
                  <ScaleIcon className="size-7 text-brand-700 dark:text-brand-300" />
                </div>
                <h3 className="mt-5 font-serif text-2xl font-semibold tracking-tight">
                  Ask about the law
                </h3>
                <p className="mt-2 max-w-md text-sm leading-relaxed text-ink-500 dark:text-ink-400">
                  Nyaya answers from the retrieved source material only. Every claim carries a
                  citation you can open to read the exact text it rests on.
                </p>
                <div className="mt-6 grid w-full gap-2 sm:grid-cols-2">
                  {EXAMPLES.map((example) => (
                    <button
                      key={example}
                      type="button"
                      onClick={() => void send(example)}
                      className="group flex h-full flex-col rounded-xl border border-ink-200 bg-white px-3.5 py-3 text-left text-sm text-ink-700 shadow-xs transition-colors hover:border-brand-400 hover:bg-brand-50/60 dark:border-ink-800 dark:bg-ink-900 dark:text-ink-300 dark:hover:border-brand-600 dark:hover:bg-ink-800"
                    >
                      <span>{example}</span>
                      <span className="mt-1 text-xs text-ink-400 transition-colors group-hover:text-brand-600 dark:group-hover:text-brand-400">
                        Try this question
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.length > 0 && (
              // max-w-2xl (minus the avatar column) keeps the reading measure
              // near 65-80 characters per line instead of stretching full width.
              <div className="mx-auto w-full max-w-2xl space-y-6 px-4 py-6">
                {messages.map((message) => (
                  <MessageItem
                    key={message.id}
                    message={message}
                    sessionId={sessionId}
                    isLastAssistant={
                      message.id === lastMessage?.id &&
                      message.role === 'assistant' &&
                      !streaming
                    }
                    onRegenerate={regenerate}
                    onSelectCitation={onSelectCitation}
                  />
                ))}
                {streamingMessage && (
                  <MessageItem
                    message={streamingMessage}
                    streaming
                    onSelectCitation={onSelectCitation}
                  />
                )}
              </div>
            )}
          </div>

          <form
            className="border-t border-ink-200 px-3 py-3 sm:px-4 dark:border-ink-800"
            onSubmit={(e) => {
              e.preventDefault()
              void send(input)
            }}
          >
            <div className="mx-auto w-full max-w-2xl">
              <div className="flex items-end gap-2 rounded-2xl border border-ink-300 bg-white p-1.5 shadow-sm transition-colors focus-within:border-brand-500 focus-within:ring-2 focus-within:ring-brand-500/20 dark:border-ink-700 dark:bg-ink-900">
                <label htmlFor="chat-input" className="sr-only">
                  Ask a legal question
                </label>
                <VoiceInput
                  sessionId={sessionId}
                  language={language}
                  onTranscript={(text) =>
                    setInput((current) => (current ? `${current} ${text}` : text))
                  }
                  disabled={streaming}
                />
                <textarea
                  id="chat-input"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault()
                      void send(input)
                    }
                  }}
                  rows={Math.min(4, Math.max(1, input.split('\n').length))}
                  placeholder="Ask a legal question…"
                  className="max-h-40 min-h-[44px] flex-1 resize-none bg-transparent px-2.5 py-2 text-sm outline-none placeholder:text-ink-400"
                />
                {streaming ? (
                  <button
                    type="button"
                    onClick={stop}
                    aria-label="Stop generating"
                    className="inline-flex h-[44px] shrink-0 items-center gap-1.5 rounded-xl border border-ink-300 px-3.5 text-sm font-medium transition-colors hover:bg-ink-100 dark:border-ink-700 dark:hover:bg-ink-800"
                  >
                    <StopIcon className="size-4" />
                    Stop
                  </button>
                ) : (
                  <button
                    type="submit"
                    disabled={!input.trim()}
                    aria-label="Send"
                    className="inline-flex size-[44px] shrink-0 items-center justify-center rounded-xl bg-brand-700 text-white transition-colors hover:bg-brand-800 disabled:cursor-not-allowed disabled:opacity-40 dark:bg-brand-500 dark:hover:bg-brand-400 dark:text-ink-950"
                  >
                    <SendIcon className="size-4.5" />
                  </button>
                )}
              </div>
              <p className="mt-1.5 hidden text-center text-[11px] text-ink-400 sm:block">
                Enter to send · Shift+Enter for a new line
              </p>
            </div>
          </form>
        </div>

        <aside
          className="flex max-h-60 min-h-0 flex-col border-t border-ink-200 lg:max-h-none lg:border-t-0 lg:border-l dark:border-ink-800"
          aria-label="Uploaded documents"
        >
          <div className="flex items-center gap-2 px-3 pt-3 lg:pt-4">
            <FileTextIcon className="size-4 text-ink-400" />
            <h3 className="text-sm font-semibold">Your documents</h3>
          </div>
          <div className="scroll-thin flex-1 overflow-y-auto p-3">
            <DocumentsPanel store={documents} />
          </div>
        </aside>
      </div>
    </div>
  )
}
