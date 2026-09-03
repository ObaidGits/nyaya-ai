/** App shell: two primary panels (Chat / Forms), theme, conversations, session. */

import { useEffect, useRef, useState } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { Sidebar } from './components/Sidebar'
import { ChatPanel } from './components/ChatPanel'
import { ToastHost } from './components/Toast'
import { FormsPanel } from './components/FormsPanel'
import { AdminPanel } from './components/admin/AdminPanel'
import { SourceDrawer } from './components/SourceDrawer'
import { BrainStatus } from './components/BrainStatus'
import { ChatIcon, FileTextIcon, MenuIcon, MoonIcon, ScaleIcon, SunIcon } from './components/icons'
import { getSessionId } from './lib/api'
import { useFocusTrap } from './hooks/useFocusTrap'
import { useScrollLock } from './hooks/useScrollLock'
import { loadLanguage, saveLanguage } from './lib/languages'
import type { Citation } from './lib/citations'
import {
  conversationTitle,
  loadConversations,
  newConversation,
  saveConversations,
  type ChatMessage,
  type Conversation,
} from './lib/conversations'

type Panel = 'chat' | 'forms'

function initialTheme(): 'light' | 'dark' {
  const stored = localStorage.getItem('nyaya.theme')
  if (stored === 'light' || stored === 'dark') return stored
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/settings" element={<AdminRoute />} />
        <Route path="/" element={<MainApp />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <ToastHost />
    </BrowserRouter>
  )
}

/** Hidden admin console (D-080): real path, deliberately absent from nav. */
function AdminRoute() {
  const navigate = useNavigate()
  return <AdminPanel onExit={() => navigate('/')} />
}

function MainApp() {
  const [theme, setTheme] = useState<'light' | 'dark'>(initialTheme)
  const [panel, setPanel] = useState<Panel>('chat')
  // Fresh visits start with one open conversation so the chat is usable
  // immediately; returning visits restore the saved list.
  const initial = (() => {
    const loaded = loadConversations()
    return loaded.length > 0 ? loaded : [newConversation()]
  })()
  const [conversations, setConversations] = useState<Conversation[]>(initial)
  const [activeId, setActiveId] = useState<string | null>(initial[0].id)
  const [sessionId, setSessionId] = useState<string>(() => getSessionId())
  const [citation, setCitation] = useState<Citation | null>(null)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  // Answer-language preference (D-077): "auto" or a supported code.
  const [language, setLanguage] = useState<string>(() => loadLanguage())

  // Mobile sidebar drawer (a11y): trap focus, lock scroll, Escape closes,
  // focus moves into the drawer on open and back to the toggle on close.
  const menuButtonRef = useRef<HTMLButtonElement>(null)
  const sidebarPanelRef = useFocusTrap<HTMLDivElement>(sidebarOpen)
  useScrollLock(sidebarOpen)

  useEffect(() => {
    if (!sidebarOpen) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setSidebarOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [sidebarOpen])

  useEffect(() => {
    if (!sidebarOpen) return
    const node = sidebarPanelRef.current
    const first =
      node?.querySelector<HTMLElement>('button, a[href], input, select, textarea') ?? null
    ;(first ?? node)?.focus()
    return () => {
      menuButtonRef.current?.focus()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sidebarOpen])

  useEffect(() => {
    saveLanguage(language)
  }, [language])

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
    localStorage.setItem('nyaya.theme', theme)
  }, [theme])

  useEffect(() => {
    saveConversations(conversations)
  }, [conversations])

  const active = conversations.find((c) => c.id === activeId) ?? null

  const handleNew = () => {
    const conversation = newConversation()
    setConversations((prev) => [conversation, ...prev])
    setActiveId(conversation.id)
    setPanel('chat')
    setSidebarOpen(false)
  }

  const handleSelect = (id: string) => {
    setActiveId(id)
    setPanel('chat')
    setSidebarOpen(false)
  }

  const handleDelete = (id: string) => {
    setConversations((prev) => {
      const next = prev.filter((c) => c.id !== id)
      if (id === activeId) setActiveId(next[0]?.id ?? null)
      return next
    })
  }

  const handleRename = (id: string, title: string) => {
    setConversations((prev) => prev.map((c) => (c.id === id ? { ...c, title } : c)))
  }

  // First user message names the conversation (C-008 rename covers the rest).
  const updateMessagesWithNaming: (messages: ChatMessage[]) => void = (messages) => {
    if (!active) return
    setConversations((prev) =>
      prev.map((c) => {
        if (c.id !== active.id) return c
        const firstUser = messages.find((m) => m.role === 'user')
        const title =
          c.messages.length === 0 && firstUser ? conversationTitle(firstUser.content) : c.title
        return { ...c, title, messages }
      }),
    )
  }

  // Drop the stored session id and make getSessionId mint a fresh one.
  // Server-side session state (documents, history) is cleared by the backend.
  const resetSession = () => {
    sessionStorage.removeItem('nyaya.session-id')
    setSessionId(getSessionId())
  }

  return (
    <div className="flex h-dvh flex-col overflow-hidden">
      <a href="#main" className="sr-only-focusable absolute left-2 top-2 z-50 rounded bg-brand-600 px-3 py-1.5 text-sm text-white">
        Skip to content
      </a>

      <header className="flex h-14 shrink-0 items-center justify-between gap-2 border-b border-ink-200 bg-white/85 px-3 backdrop-blur sm:px-4 dark:border-ink-800 dark:bg-ink-950/85">
        <div className="flex min-w-0 items-center gap-2">
          <button
            ref={menuButtonRef}
            type="button"
            className="inline-flex size-11 shrink-0 items-center justify-center rounded-lg text-ink-600 transition-colors hover:bg-ink-100 hover:text-ink-900 md:hidden md:size-9 dark:text-ink-300 dark:hover:bg-ink-800 dark:hover:text-ink-100"
            onClick={() => setSidebarOpen((open) => !open)}
            aria-expanded={sidebarOpen}
            aria-label="Toggle conversations sidebar"
          >
            <MenuIcon className="size-5" />
          </button>
          <div className="flex min-w-0 items-center gap-2">
            <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-brand-700 text-white shadow-sm dark:bg-brand-500 dark:text-ink-950">
              <ScaleIcon className="size-4.5" />
            </span>
            <h1 className="truncate font-serif text-lg font-bold leading-none tracking-tight">
              Nyaya
            </h1>
          </div>
        </div>

        <div
          role="tablist"
          aria-label="Primary panels"
          onKeyDown={(event) => {
            if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return
            event.preventDefault()
            const next: Panel = panel === 'chat' ? 'forms' : 'chat'
            setPanel(next)
            document.getElementById(`tab-${next}`)?.focus()
          }}
          className="flex gap-0.5 rounded-full border border-ink-200 bg-ink-100/80 p-0.5 dark:border-ink-800 dark:bg-ink-900"
        >
          {(
            [
              ['chat', 'Chatbot', ChatIcon],
              ['forms', 'Forms', FileTextIcon],
            ] as const
          ).map(([value, label, Icon]) => (
            <button
              key={value}
              type="button"
              role="tab"
              id={`tab-${value}`}
              aria-selected={panel === value}
              aria-controls="main"
              tabIndex={panel === value ? 0 : -1}
              onClick={() => setPanel(value)}
              className={`flex items-center gap-1.5 rounded-full px-2.5 py-1.5 text-sm font-medium transition-colors sm:px-3 ${
                panel === value
                  ? 'bg-white text-ink-900 shadow-sm dark:bg-ink-800 dark:text-ink-100'
                  : 'text-ink-600 hover:text-ink-900 dark:text-ink-300 dark:hover:text-ink-100'
              }`}
            >
              <Icon className="size-4 shrink-0" />
              {/* Label hides below sm (360-390px headers stay uncrowded) but
                  remains the accessible name for the tab role. */}
              <span className="hidden sm:inline">{label}</span>
            </button>
          ))}
        </div>

        <div className="flex items-center gap-2">
          <BrainStatus />
          <button
            type="button"
            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
            aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
            className="inline-flex size-11 items-center justify-center rounded-lg text-ink-600 transition-colors hover:bg-ink-100 hover:text-ink-900 md:size-9 dark:text-ink-300 dark:hover:bg-ink-800 dark:hover:text-ink-100"
          >
            {theme === 'dark' ? <SunIcon className="size-4.5" /> : <MoonIcon className="size-4.5" />}
          </button>
        </div>
      </header>

      <div className="relative flex min-h-0 flex-1">
        {sidebarOpen && (
          <div
            className="absolute inset-0 top-0 z-20 bg-ink-950/40 md:hidden"
            onClick={() => setSidebarOpen(false)}
            aria-hidden="true"
          />
        )}
        <div
          ref={sidebarPanelRef}
          role={sidebarOpen ? 'dialog' : undefined}
          aria-modal={sidebarOpen ? true : undefined}
          aria-label={sidebarOpen ? 'Conversations' : undefined}
          className={`${
            sidebarOpen
              ? 'absolute inset-x-0 top-0 bottom-0 z-30 w-72 animate-rise bg-ink-50 shadow-xl md:relative md:inset-auto md:z-auto md:w-64 md:animate-none md:shrink-0 md:border-r md:shadow-none dark:bg-ink-950'
              : 'hidden md:block md:w-64 md:shrink-0 md:border-r'
          } border-ink-200 dark:border-ink-800`}
        >
          <Sidebar
            conversations={conversations}
            activeId={activeId}
            onSelect={handleSelect}
            onNew={handleNew}
            onRename={handleRename}
            onDelete={handleDelete}
          />
          <div className="border-t border-ink-200 px-3 py-2.5 text-xs text-ink-500 dark:border-ink-800">
            <p>Session documents are scoped to this browser tab.</p>
            <button
              type="button"
              onClick={resetSession}
              className="mt-1 text-ink-600 underline underline-offset-2 transition-colors hover:text-ink-900 dark:text-ink-300 dark:hover:text-ink-100"
            >
              Start a new session
            </button>
          </div>
        </div>

        <main
          id="main"
          className="min-h-0 min-w-0 flex-1"
          role="tabpanel"
          aria-labelledby={`tab-${panel}`}
        >
          {panel === 'chat' ? (
            active ? (
              // The key forces a full remount when the conversation or the
              // session changes, so ChatPanel reloads its server-side history.
              <ChatPanel
                key={`${active.id}-${sessionId}`}
                sessionId={sessionId}
                conversation={active}
                onMessagesChange={updateMessagesWithNaming}
                onSelectCitation={setCitation}
                language={language}
                onLanguageChange={setLanguage}
              />
            ) : (
              <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
                <h2 className="font-serif text-xl">Start a conversation</h2>
                <p className="max-w-sm text-sm text-ink-500">
                  Create a conversation to ask grounded legal questions.
                </p>
                <button
                  type="button"
                  onClick={handleNew}
                  className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-700 dark:bg-brand-500 dark:text-ink-950 dark:hover:bg-brand-400"
                >
                  New conversation
                </button>
              </div>
            )
          ) : (
            <FormsPanel />
          )}
        </main>
      </div>

      <SourceDrawer citation={citation} onClose={() => setCitation(null)} />
    </div>
  )
}
