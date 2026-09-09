import { useCallback, useEffect, useRef, useState } from 'react'
import * as pdfjs from 'pdfjs-dist'
import type { PDFDocumentProxy } from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { api, type PageArea, type SourceLocation } from '../api'

// PDF.js does its parsing off the main thread; Vite hands us the worker's URL.
pdfjs.GlobalWorkerOptions.workerSrc = workerUrl

/** Somewhere to go and mark. The nonce is what makes the same place asked for
 *  twice count as two arrivals, so the flash runs again. */
export type Mark = { areas: PageArea[]; nonce: number }

/**
 * The paper, rendered by PDF.js — the same renderer Overleaf uses.
 *
 * The reason to draw it ourselves rather than hand the file to the browser's
 * built-in viewer is two gestures. **Double-click a word and the editor goes to
 * the line that wrote it**, and the same arrow the other way: put the cursor on
 * a line and the page it printed on scrolls up and flashes. A viewer in an
 * iframe can neither tell us where you clicked nor be told where to go. Here the
 * click is a point on a canvas, which converts to a point on the page, which
 * SyncTeX turns back into a file and a line — and back again.
 */
export default function PdfViewer({
  src,
  sessionId,
  review,
  onJump,
  mark,
}: {
  src: string
  sessionId: string | null
  review: boolean
  onJump: (where: SourceLocation) => void
  /** Where the editor has asked the paper to go. */
  mark?: Mark | null
}) {
  const scroller = useRef<HTMLDivElement>(null)
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null)
  const [scale, setScale] = useState<number | 'fit'>('fit')
  const [fitScale, setFitScale] = useState(1)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [atPage, setAtPage] = useState(1)

  // -- load, and keep your place across a recompile ---------------------
  useEffect(() => {
    let stale = false
    const keepAt = scroller.current?.scrollTop ?? 0
    const task = pdfjs.getDocument({ url: src, cMapPacked: true })

    task.promise
      .then((d) => {
        if (stale) return
        setDoc(d)
        setError(null)
        // A recompile replaces the file; you should not lose your place.
        requestAnimationFrame(() => {
          if (scroller.current) scroller.current.scrollTop = keepAt
        })
      })
      .catch((e) => !stale && setError(String(e)))

    return () => {
      stale = true
      void task.destroy()
    }
  }, [src])

  // -- fit to width, and follow the divider -----------------------------
  useEffect(() => {
    const el = scroller.current
    if (!el || !doc) return
    let cancelled = false
    const measure = async () => {
      const page = await doc.getPage(1)
      if (cancelled) return
      const width = page.getViewport({ scale: 1 }).width
      setFitScale(Math.max(0.2, (el.clientWidth - 26) / width))
    }
    void measure()
    const observer = new ResizeObserver(() => void measure())
    observer.observe(el)
    return () => {
      cancelled = true
      observer.disconnect()
    }
  }, [doc])

  const zoom = scale === 'fit' ? fitScale : scale

  // -- which page you are on --------------------------------------------
  // Read off the layout on each scroll rather than tracked page by page: the
  // heights are already in the DOM, and one number stays true more easily than
  // two dozen booleans that each have to be told when they stop applying.
  useEffect(() => {
    const el = scroller.current
    if (!el || !doc) return
    let queued = false
    const read = () => {
      queued = false
      const middle = el.scrollTop + el.clientHeight / 2
      let showing = 1
      for (const page of el.querySelectorAll<HTMLElement>('[data-page]')) {
        if (page.offsetTop <= middle) showing = Number(page.dataset.page)
      }
      setAtPage(showing)
    }
    const onScroll = () => {
      if (queued) return
      queued = true
      requestAnimationFrame(read)
    }
    read()
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [doc, zoom])

  // -- arriving from the editor -----------------------------------------
  useEffect(() => {
    const el = scroller.current
    const first = mark?.areas[0]
    if (!el || !first) return
    const page = el.querySelector<HTMLElement>(`[data-page="${first.page}"]`)
    if (!page) return
    // A third of the way down rather than hard against the top: what you came
    // for reads better with the lines above it still on screen.
    const top = page.offsetTop + first.y * zoom - el.clientHeight / 3
    el.scrollTo({ top: Math.max(0, top), behavior: 'smooth' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mark?.nonce])

  const jump = useCallback(
    async (page: number, x: number, y: number) => {
      setNote(null)
      try {
        const where = await api.synctexEdit(page, x, y, sessionId ?? undefined, review)
        if (!where.in_project) {
          setNote(`That came from ${where.path}, which is not part of the project.`)
          return
        }
        onJump(where)
      } catch (e) {
        setNote(String(e))
      }
    },
    [onJump, sessionId, review],
  )

  if (error) return <div className="notice bad">{error}</div>
  if (!doc) return <div className="empty small">Opening the PDF…</div>

  return (
    <>
      <div className="pdf-zoom">
        <div className="seg">
          <button onClick={() => setScale(zoom / 1.2)} title="Zoom out">
            −
          </button>
          <button onClick={() => setScale('fit')} className={scale === 'fit' ? 'on' : ''}>
            {Math.round(zoom * 100)}%
          </button>
          <button onClick={() => setScale(zoom * 1.2)} title="Zoom in">
            +
          </button>
        </div>
        <span className="muted small">
          Page {atPage} of {doc.numPages}
        </span>
        <span className="grow" />
        <span className="muted small">double-click a word to go to its source</span>
      </div>
      {note && <div className="notice info">{note}</div>}
      <div className="pdf-scroll" ref={scroller}>
        {Array.from({ length: doc.numPages }, (_, i) => (
          <Page
            key={i + 1}
            doc={doc}
            number={i + 1}
            scale={zoom}
            onPick={jump}
            mark={mark ?? null}
          />
        ))}
      </div>
    </>
  )
}

/** One page. It draws itself only once it is close to the viewport. */
function Page({
  doc,
  number,
  scale,
  onPick,
  mark,
}: {
  doc: PDFDocumentProxy
  number: number
  scale: number
  onPick: (page: number, x: number, y: number) => void
  mark: Mark | null
}) {
  const holder = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const [size, setSize] = useState<{ w: number; h: number } | null>(null)
  const [near, setNear] = useState(number <= 2)

  useEffect(() => {
    const el = holder.current
    if (!el || near) return
    const observer = new IntersectionObserver(
      (entries) => entries.some((e) => e.isIntersecting) && setNear(true),
      { rootMargin: '600px 0px' },
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [near])

  useEffect(() => {
    let cancelled = false
    let task: { cancel: () => void } | null = null
    doc.getPage(number).then((page) => {
      if (cancelled) return
      const viewport = page.getViewport({ scale })
      setSize({ w: viewport.width, h: viewport.height })
      const target = canvas.current
      if (!target || !near) return
      // Drawn at device resolution, laid out at CSS resolution: the page
      // stays sharp, and a click at (offsetX / scale) is still a point on
      // the page whatever the screen's pixel ratio is.
      const ratio = window.devicePixelRatio || 1
      const bitmap = page.getViewport({ scale: scale * ratio })
      target.width = Math.floor(bitmap.width)
      target.height = Math.floor(bitmap.height)
      const context = target.getContext('2d')
      if (!context) return
      const render = page.render({ canvasContext: context, viewport: bitmap })
      task = render
      // Scrolling or zooming cancels a render in flight. PDF.js rejects the
      // promise when that happens, which is expected, not a failure.
      render.promise.catch(() => undefined)
    })
    return () => {
      cancelled = true
      task?.cancel()
    }
  }, [doc, number, scale, near])

  // The rectangle is in big points at scale 1, and the page is laid out at
  // `scale`, which is the same conversion a double-click makes in reverse.
  const here = mark?.areas.filter((a) => a.page === number) ?? []

  return (
    <div
      className="pdf-page"
      data-page={number}
      ref={holder}
      style={size ? { width: size.w, height: size.h } : undefined}
      onDoubleClick={(e) => {
        const box = (e.target as HTMLElement).getBoundingClientRect()
        onPick(number, (e.clientX - box.left) / scale, (e.clientY - box.top) / scale)
      }}
    >
      {near && <canvas ref={canvas} style={size ? { width: size.w, height: size.h } : undefined} />}
      {here.map((area, i) => (
        // Keyed by the arrival as well as the place, so coming back to a line
        // you have already visited starts the flash again instead of leaving a
        // finished animation on screen.
        <span
          key={`${mark?.nonce}-${i}`}
          className="pdf-mark"
          style={{
            left: area.x * scale,
            top: area.y * scale,
            width: Math.max(area.width * scale, 8),
            height: Math.max(area.height * scale, 10),
          }}
        />
      ))}
      <span className="pdf-page-number">{number}</span>
    </div>
  )
}
