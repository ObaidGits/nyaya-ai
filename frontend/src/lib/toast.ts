/**
 * Toast pub-sub channel (see components/Toast.tsx).
 *
 * Plain module (no component exports) so react-refresh lint stays clean:
 * call sites `toast.error(msg)`, `ToastHost` subscribes.
 */

export type ToastKind = 'error' | 'info'

export interface ToastItem {
  id: number
  kind: ToastKind
  message: string
}

type Listener = (item: ToastItem) => void

let counter = 0
const listeners = new Set<Listener>()

function emit(kind: ToastKind, message: string): void {
  const item: ToastItem = { id: (counter += 1), kind, message }
  listeners.forEach((listener) => listener(item))
}

export const toast = {
  error(message: string): void {
    emit('error', message)
  },
  info(message: string): void {
    emit('info', message)
  },
}

export function subscribe(listener: Listener): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}
