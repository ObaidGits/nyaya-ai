/** Conversation sidebar: list, new, rename, delete (C-007..C-009). */

import { useEffect, useRef, useState } from 'react'
import type { Conversation } from '../lib/conversations'
import { ChatIcon, PencilIcon, PlusIcon, TrashIcon } from './icons'

interface SidebarProps {
  conversations: Conversation[]
  activeId: string | null
  onSelect: (id: string) => void
  onNew: () => void
  onRename: (id: string, title: string) => void
  onDelete: (id: string) => void
}

export function Sidebar({
  conversations,
  activeId,
  onSelect,
  onNew,
  onRename,
  onDelete,
}: SidebarProps) {
  const [editingId, setEditingId] = useState<string | null>(null)
  const [draftTitle, setDraftTitle] = useState('')
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)
  const editInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (editingId) editInputRef.current?.focus()
  }, [editingId])

  // Deleting a conversation destroys its history permanently, so the trash
  // button arms first ("Delete?") and only a second click confirms. The arm
  // state self-clears so a stray click can never linger as a loaded gun.
  useEffect(() => {
    if (!confirmDeleteId) return
    const timer = window.setTimeout(() => setConfirmDeleteId(null), 4000)
    return () => window.clearTimeout(timer)
  }, [confirmDeleteId])

  const commitRename = (id: string) => {
    const title = draftTitle.trim()
    if (title) onRename(id, title)
    setEditingId(null)
  }

  return (
    <nav aria-label="Conversations" className="flex h-full flex-col gap-2 p-3">
      <button
        type="button"
        onClick={onNew}
        className="flex items-center justify-center gap-1.5 rounded-lg bg-brand-700 px-3 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-brand-800 dark:bg-brand-500 dark:hover:bg-brand-400 dark:text-ink-950"
      >
        <PlusIcon className="size-4" />
        New conversation
      </button>

      <ul className="scroll-thin flex-1 space-y-1 overflow-y-auto pt-1" role="list">
        {conversations.length === 0 && (
          <li className="px-2 py-4 text-sm text-ink-500">No conversations yet.</li>
        )}
        {conversations.map((conversation) => {
          const active = conversation.id === activeId
          return (
            <li key={conversation.id}>
              {editingId === conversation.id ? (
                <form
                  onSubmit={(e) => {
                    e.preventDefault()
                    commitRename(conversation.id)
                  }}
                  className="flex gap-1"
                >
                  <input
                    ref={editInputRef}
                    value={draftTitle}
                    onChange={(e) => setDraftTitle(e.target.value)}
                    onBlur={() => commitRename(conversation.id)}
                    aria-label={`Rename ${conversation.title}`}
                    className="min-w-0 flex-1 rounded-md border border-brand-500 bg-white px-2 py-1 text-sm ring-2 ring-brand-500/20 dark:border-brand-400 dark:bg-ink-900"
                  />
                </form>
              ) : (
                <div
                  className={`group flex items-center gap-1 rounded-lg pr-1 transition-colors ${
                    active
                      ? 'bg-brand-100/80 dark:bg-ink-800'
                      : 'hover:bg-ink-100 dark:hover:bg-ink-900'
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => onSelect(conversation.id)}
                    className={`flex min-w-0 flex-1 items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm ${
                      active
                        ? 'font-medium text-brand-900 dark:text-ink-100'
                        : 'text-ink-700 dark:text-ink-300'
                    }`}
                    aria-current={active ? 'true' : undefined}
                  >
                    <ChatIcon
                      className={`size-3.5 shrink-0 ${active ? 'text-brand-600 dark:text-brand-400' : 'text-ink-400'}`}
                    />
                    <span className="min-w-0 truncate">{conversation.title}</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setEditingId(conversation.id)
                      setDraftTitle(conversation.title)
                    }}
                    aria-label={`Rename ${conversation.title}`}
                    className="inline-flex size-7 shrink-0 items-center justify-center rounded-md text-ink-500 opacity-0 transition-opacity hover:bg-ink-200/70 hover:text-ink-800 focus-visible:opacity-100 group-hover:opacity-100 dark:hover:bg-ink-700 dark:hover:text-ink-200"
                  >
                    <PencilIcon className="size-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      if (confirmDeleteId === conversation.id) {
                        onDelete(conversation.id)
                        setConfirmDeleteId(null)
                      } else {
                        setConfirmDeleteId(conversation.id)
                      }
                    }}
                    aria-label={
                      confirmDeleteId === conversation.id
                        ? `Confirm delete ${conversation.title}`
                        : `Delete ${conversation.title}`
                    }
                    className={`inline-flex size-7 shrink-0 items-center justify-center rounded-md transition-opacity focus-visible:opacity-100 group-hover:opacity-100 ${
                      confirmDeleteId === conversation.id
                        ? 'bg-red-600 text-white opacity-100 dark:bg-red-500'
                        : 'text-ink-500 opacity-0 hover:bg-red-100 hover:text-red-700 dark:hover:bg-red-950/60 dark:hover:text-red-300'
                    }`}
                  >
                    <TrashIcon className="size-3.5" />
                  </button>
                </div>
              )}
            </li>
          )
        })}
      </ul>
    </nav>
  )
}
