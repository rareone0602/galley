import { useCallback, useEffect, useRef, useState } from 'react'
import * as pdfjs from 'pdfjs-dist'
import type { PDFDocumentProxy } from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { api, type SourceLocation } from '../api'

// PDF.js does its parsing off the main thread; Vite hands us the worker's URL.
pdfjs.GlobalWorkerOptions.workerSrc = workerUrl

/**
 * The paper, rendered by PDF.js — the same renderer Overleaf uses.
 *
 * The reason to draw it ourselves rather than hand the file to the browser's
 * built-in viewer is one gesture: **double-click a word and the editor goes to
 * the line that wrote it.** A native viewer in an iframe cannot tell us where
 * you clicked. Here the click is a point on a canvas, which converts to a point
 * on the page, which SyncTeX turns back into a file and a line.
 */
export default function PdfViewer({
  src,
  sessionId,
  review,
  onJump,
}: {
  src: string
  sessionId: string | null
  review: boolean
  onJump: (where: SourceLocation) => void
}) {
  const scroller = useRef<HTMLDivElement>(null)
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null)
  const [scale, setScale] = useState<number | 'fit'>('fit')
  const [fitScale, setFitScale] = useState(1)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

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
        <span className="muted small">{doc.numPages} pages</span>
        <span className="grow" />
        <span className="muted small">double-click a word to go to its source</span>
      </div>
      {note && <div className="notice info">{note}</div>}
      <div className="pdf-scroll" ref={scroller}>
        {Array.from({ length: doc.numPages }, (_, i) => (
          <Page key={i + 1} doc={doc} number={i + 1} scale={zoom} onPick={jump} />
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
}: {
  doc: PDFDocumentProxy
  number: number
  scale: number
  onPick: (page: number, x: number, y: number) => void
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

  return (
    <div
      className="pdf-page"
      ref={holder}
      style={size ? { width: size.w, height: size.h } : undefined}
      onDoubleClick={(e) => {
        const box = (e.target as HTMLElement).getBoundingClientRect()
        onPick(number, (e.clientX - box.left) / scale, (e.clientY - box.top) / scale)
      }}
    >
      {near && <canvas ref={canvas} style={size ? { width: size.w, height: size.h } : undefined} />}
      <span className="pdf-page-number">{number}</span>
    </div>
  )
}
