import { useEffect } from 'react'

export type MenuItem = {
  label: string
  /** The key that does the same thing, shown the way Overleaf shows one. */
  hint?: string
  danger?: boolean
  run: () => void
}

/**
 * The right-click menu on the rail.
 *
 * Anything at all closes it — a click, a key, the rail scrolling underneath —
 * because a menu should never be the thing standing between you and the file
 * you were reaching for.
 */
export default function ContextMenu({
  x,
  y,
  items,
  onClose,
}: {
  x: number
  y: number
  items: MenuItem[]
  onClose: () => void
}) {
  useEffect(() => {
    const shut = () => onClose()
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('mousedown', shut)
    window.addEventListener('keydown', key)
    window.addEventListener('resize', shut)
    window.addEventListener('scroll', shut, true)
    return () => {
      window.removeEventListener('mousedown', shut)
      window.removeEventListener('keydown', key)
      window.removeEventListener('resize', shut)
      window.removeEventListener('scroll', shut, true)
    }
  }, [onClose])

  // Right-clicking near the bottom of a narrow rail should still show the menu.
  const style = {
    left: Math.min(x, Math.max(4, window.innerWidth - 190)),
    top: Math.min(y, Math.max(4, window.innerHeight - (items.length * 26 + 10))),
  }

  return (
    <div
      className="tree-menu"
      style={style}
      role="menu"
      onMouseDown={(e) => e.stopPropagation()}
    >
      {items.map((item) => (
        <button
          key={item.label}
          role="menuitem"
          className={item.danger ? 'danger' : undefined}
          onClick={() => {
            onClose()
            item.run()
          }}
        >
          <span>{item.label}</span>
          {item.hint && <span className="hint">{item.hint}</span>}
        </button>
      ))}
    </div>
  )
}
