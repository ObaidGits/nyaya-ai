/** Lock background scrolling while a modal / sheet / drawer is open. */

import { useEffect } from 'react'

/**
 * Sets `overflow: hidden` on <body> while active so the page behind an
 * overlay cannot scroll. Handles StrictMode double-mount via idempotent
 * save/restore of the previous inline style.
 */
export function useScrollLock(active: boolean): void {
  useEffect(() => {
    if (!active) return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previous
    }
  }, [active])
}
