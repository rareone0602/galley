import { useCallback, useEffect, useState } from 'react'

/**
 * The shape of the file down one edge: a tick where each change is, coloured
 * by the answer it has, and the part of the file you are looking at drawn
 * behind them. Clicking a tick goes there.
 *
 * The ticks are measured from the rows themselves rather than counted out
 * evenly, because a sentence rewritten into a paragraph is taller than a
 * comma, and a map that lies about where things are is worse than none. Every
 * change row carries `data-change-id`, so this is the only thing the map needs
 * to know about the pane.
 */
export default function ChangeMap({
  scroller,
  ids,
  stateOf,
  current,
  onPick,
}: {
  /** The scrolling element the rows live in. */
  scroller: HTMLElement | null
  ids: number[]
  stateOf: (id: number) => string
  current: number | null
  onPick: (id: number) => void
}) {
  const [spots, setSpots] = useState<Record<number, number>>({})
  const [window_, setWindow] = useState({ top: 0, height: 1 })

  const measure = useCallback(() => {
    if (!scroller) return
    const height = scroller.scrollHeight || 1
    // Where the scrolled content starts, in viewport coordinates. Offsets
    // taken against this survive sticky headers and any positioned ancestor.
    const origin = scroller.getBoundingClientRect().top - scroller.scrollTop
    const found: Record<number, number> = {}
    scroller.querySelectorAll<HTMLElement>('[data-change-id]').forEach((row) => {
      const id = Number(row.dataset.changeId)
      found[id] = (row.getBoundingClientRect().top - origin) / height
    })
    setSpots(found)
    setWindow({ top: scroller.scrollTop / height, height: scroller.clientHeight / height })
  }, [scroller])

  useEffect(() => {
    if (!scroller) return
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(scroller)
    for (const child of Array.from(scroller.children)) observer.observe(child)
    const onScroll = () => {
      const height = scroller.scrollHeight || 1
      setWindow({ top: scroller.scrollTop / height, height: scroller.clientHeight / height })
    }
    scroller.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      observer.disconnect()
      scroller.removeEventListener('scroll', onScroll)
    }
  }, [scroller, measure, ids])

  if (!ids.length) return null

  return (
    <div className="changemap" aria-hidden>
      <div
        className="here"
        style={{ top: `${window_.top * 100}%`, height: `${Math.min(1, window_.height) * 100}%` }}
      />
      {ids.map((id) => (
        <button
          key={id}
          type="button"
          tabIndex={-1}
          className={`tick ${stateOf(id)}${id === current ? ' current' : ''}`}
          style={{ top: `${(spots[id] ?? 0) * 100}%` }}
          onClick={() => onPick(id)}
        />
      ))}
    </div>
  )
}
