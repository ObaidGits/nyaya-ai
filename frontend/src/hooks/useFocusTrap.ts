/**
 * Focus trap for modal dialogs (source drawer, form preview).
 *
 * Keeps Tab/Shift+Tab cycling inside the dialog while it is open, so
 * keyboard users cannot tab into the obscured page behind the overlay.
 * Escape handling and initial focus stay with the caller.
 */

import { useEffect, useRef } from 'react'

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), iframe, [tabindex]:not([tabindex="-1"])'

export function useFocusTrap<T extends HTMLElement>(active: boolean) {
  const ref = useRef<T | null>(null)

  useEffect(() => {
    const node = ref.current
    if (!active || !node) return

    const onKeyDown = (event: Event) => {
      if (!(event instanceof KeyboardEvent) || event.key !== 'Tab') return
      const focusables = Array.from(
        node.querySelectorAll<HTMLElement>(FOCUSABLE),
      ).filter((element) => element.offsetParent !== null || element === document.activeElement)
      if (focusables.length === 0) return
      const first = focusables[0]
      const last = focusables[focusables.length - 1]
      if (!node.contains(document.activeElement)) {
        event.preventDefault()
        first.focus()
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    node.addEventListener('keydown', onKeyDown)
    return () => node.removeEventListener('keydown', onKeyDown)
  }, [active])

  return ref
}
