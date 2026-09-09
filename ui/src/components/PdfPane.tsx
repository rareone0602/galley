import { useEffect, useRef, useState } from 'react'
import { api, type Work } from '../api'

type Mode = 'accepted' | 'branch' | 'review'

/**
 * The right-hand pane: the paper as it will look in print, always on screen.
 *
 * Both builds run on the server and are polled rather than awaited — latexmk
 * takes tens of seconds on a real paper and latexdiff takes minutes, which is
 * far too long to hold a request open.
 */
export default function PdfPane({
  sessionId,
  latexdiffAvailable,
  onCollapse,
}: {
  sessionId: string | null
  latexdiffAvailable: boolean
  onCollapse: () => void
}) {
  const [work, setWork] = useState<Work | null>(null)
  const [mode, setMode] = useState<Mode>('accepted')
  const [stamp, setStamp] = useState(0)
  const poll = useRef<number | null>(null)

  function stopPolling() {
    if (poll.current) window.clearInterval(poll.current)
    poll.current = null
  }
  useEffect(() => stopPolling, [])

  async function run(which: Mode) {
    setMode(which)
    stopPolling()
    const kick = () =>
      which === 'review' && sessionId
        ? api.review(sessionId)
        : api.compile(which === 'branch' && sessionId ? sessionId : undefined)
    const check = () =>
      which === 'review' && sessionId
        ? api.reviewStatus(sessionId)
        : api.compileStatus(which === 'branch' && sessionId ? sessionId : undefined)

    try {
      const first = await kick()
      setWork(first)
      if (first.state !== 'running') return setStamp(Date.now())
      poll.current = window.setInterval(async () => {
        try {
          const next = await check()
          setWork(next)
          if (next.state !== 'running') {
            stopPolling()
            setStamp(Date.now())
          }
        } catch (e) {
          stopPolling()
          setWork({ state: 'failed', elapsed_seconds: null, error: String(e) })
        }
      }, 2000)
    } catch (e) {
      setWork({ state: 'failed', elapsed_seconds: null, error: String(e) })
    }
  }

  const busy = work?.state === 'running'
  const src =
    mode === 'review' && sessionId
      ? `/api/pdf?session_id=${sessionId}&review=true&t=${stamp}`
      : mode === 'branch' && sessionId
        ? `/api/pdf?session_id=${sessionId}&t=${stamp}`
        : `/api/pdf?t=${stamp}`

  return (
    <section className="pane-column pdf-pane">
      <header className="pdf-bar">
        <button className="tiny" onClick={onCollapse} title="Hide the PDF pane">
          ‹
        </button>
        <div className="seg">
          <button
            className={mode === 'accepted' ? 'on' : ''}
            onClick={() => run('accepted')}
            disabled={busy}
            title="The paper as it stands, with everything you have accepted"
          >
            Accepted
          </button>
          <button
            className={mode === 'branch' ? 'on' : ''}
            onClick={() => run('branch')}
            disabled={busy || !sessionId}
            title="The paper as Claude proposes it, on this branch"
          >
            Proposed
          </button>
          <button
            className={mode === 'review' ? 'on' : ''}
            onClick={() => run('review')}
            disabled={busy || !sessionId || !latexdiffAvailable}
            title={
              !latexdiffAvailable
                ? 'latexdiff is not installed'
                : 'Marked up: accepted state against this branch. Takes a few minutes.'
            }
          >
            Marked up
          </button>
        </div>
        <span className="spacer" />
        {busy && (
          <span className="muted small">
            {mode === 'review' ? 'latexdiff' : 'latexmk'} · {work?.elapsed_seconds ?? 0}s
          </span>
        )}
        {!busy && work?.state === 'done' && work.ok && (
          <span className="muted small">built in {work.elapsed_seconds}s</span>
        )}
      </header>

      <div className="pdf-body">
        {busy && mode === 'review' && (
          <div className="notice warn">
            latexdiff flattens and compares the whole paper, which takes a few
            minutes on one this size. It keeps going if you look away.
          </div>
        )}
        {work?.state === 'failed' && (
          <div className="notice bad">
            <strong>Could not build.</strong>
            <pre style={{ marginTop: 6 }}>{work.error}</pre>
          </div>
        )}
        {work?.state === 'done' && !work.ok && (
          <div className="notice bad">
            <strong>Compile failed.</strong>
            <pre style={{ marginTop: 6 }}>{(work.errors ?? []).join('\n')}</pre>
          </div>
        )}
        {work?.undefined?.length ? (
          <div className="notice warn">
            Undefined references: <span className="mono">{work.undefined.join(', ')}</span>
          </div>
        ) : null}

        {work?.state === 'done' && work.ok ? (
          <iframe className="pdf" src={src} title="paper" />
        ) : (
          !work && (
            <div className="empty">
              Compile to see the paper.
              <div style={{ marginTop: 10 }}>
                <button className="primary" onClick={() => run('accepted')}>
                  Compile
                </button>
              </div>
            </div>
          )
        )}
      </div>
    </section>
  )
}
