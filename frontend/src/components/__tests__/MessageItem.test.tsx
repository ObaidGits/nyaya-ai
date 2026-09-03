/** MessageItem tests: refusal rendering with the contextual reason sentence. */

import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MessageItem } from '../MessageItem'
import type { ChatMessage } from '../../lib/conversations'

const assistant = (overrides: Partial<ChatMessage>): ChatMessage => ({
  id: 'm-1-a',
  role: 'assistant',
  content: '',
  ...overrides,
})

describe('MessageItem refusal rendering', () => {
  it('renders the refusal line and the contextual reason sentence from the streamed content', () => {
    const message = assistant({
      refused: true,
      content: (
        'I don\'t know based on the available source material. The BNS corpus does not cover ' +
        'New York law; this assistant answers from the Bharatiya Nyaya Sanhita and your ' +
        'uploaded documents only.'
      ),
    })
    render(
      <MessageItem message={message} streaming={false} onSelectCitation={() => {}} />,
    )
    // The refusal line itself is present...
    expect(screen.getByText(/I don't know based on the available source material/, { exact: false })).toBeTruthy()
    // ...and the backend-authored reason sentence is NOT truncated away.
    expect(screen.getByText(/does not cover New York law/, { exact: false })).toBeTruthy()
    // The amber refusal banner still appears below the content.
    expect(screen.getByText(/refused to answer/i)).toBeTruthy()
  })

  it('renders the plain refusal line unchanged when no reason sentence is present', () => {
    const message = assistant({
      refused: true,
      content: "I don't know based on the available source material.",
    })
    render(
      <MessageItem message={message} streaming={false} onSelectCitation={() => {}} />,
    )
    expect(screen.getByText(/refused to answer/i)).toBeTruthy()
    expect(screen.getByText(/available source material/, { exact: false })).toBeTruthy()
    expect(screen.queryByText(/does not cover/i)).toBeNull()
  })

  it('renders a refusal reason about uploading a document without truncation', () => {
    const message = assistant({
      refused: true,
      content:
        "I don't know based on the available source material. No documents are uploaded in " +
        'this session. Upload a document and ask again.',
    })
    render(
      <MessageItem message={message} streaming={false} onSelectCitation={() => {}} />,
    )
    expect(screen.getByText(/Upload a document and ask again/, { exact: false })).toBeTruthy()
    expect(screen.getByText(/refused to answer/i)).toBeTruthy()
  })
})
