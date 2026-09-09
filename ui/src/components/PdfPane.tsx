import { useEffect, useRef, useState } from 'react'
import { api, type Problem, type SourceLocation, type Work } from '../api'
import { record } from '../usage'
import PdfViewer, { type Mark } from './PdfViewer'

type Mode = 'accepted' | 'branch' | 'review'

/** The build on screen, as against the one you last asked for. They come apart
 *  while latexmk runs, and stay apart if it fails: the paper you were reading
 *  is better company than an empty pane. */
type Shown = { mode: Mode; stamp: number }

/** Which session's PDF a shown build is, if any. `accepted` is the paper
 *  itself, so it belongs to no session even when one is open. */
const shownSession = (shown: Shown, sessionId: string | null) =>
  shown.mode === 'accepted' ? null : sessionId

/**
 * The right-hand pane: the paper as it will look in print, always on screen.
 *
 * Both builds run on the server and are polled rather than awaited — latexmk
 * takes tens of seconds on a real paper and latexdiff takes minutes, which is
 * far too long to hold a request open. The paper stays up throughout: a build
 * you cannot see the result of is a build you have stopped reading during.
 */
export default function PdfPane({
  sessionId,
  latexdiffAvailable,
  onCollapse,
  onJump,
  showInPdf,
  buildsPdf,
}: {
  sessionId: string | null
  latexdiffAvailable: boolean
  onCollapse: () => void
  onJump: (where: SourceLocation) => void
  /** Whether the project has the LaTeX root it names. When it does not there
   *  is no paper to draw, and saying so beats a button that cannot work. */
  buildsPdf: boolean
  /** A source line to go to, from the editor. The nonce is the gesture: the
   *  same line asked for twice should scroll and flash twice. */
  showInPdf?: { path: string; line: number; nonce: number } | null
}) {
  const [work, setWork] = useState<Work | null>(null)
  const [mode, setMode] = useState<Mode>('accepted')
  const [shown, setShown] = useState<Shown | null>(null)
  const [listing, setListing] = useState(false)
  const [mark, setMark] = useState<Mark | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const poll = useRef<number | null>(null)

  function stopPolling() {
    if (poll.current) window.clearInterval(poll.current)
    poll.current = null
  }
  useEffect(() => stopPolling, [])

  async function run(which: Mode) {
    setMode(which)
    stopPolling()
    // The build itself is recorded by the server, which knows its real
    // duration and its real outcome and does not stop knowing them when this
    // tab closes. What is recorded here is only what the server cannot see:
    // the request never landing, or the polling failing.
    const kick = () =>
      which === 'review' && sessionId
        ? api.review(sessionId)
        : api.compile(which === 'branch' && sessionId ? sessionId : undefined)
    const check = () =>
      which === 'review' && sessionId
        ? api.reviewStatus(sessionId)
        : api.compileStatus(which === 'branch' && sessionId ? sessionId : undefined)

    const settle = (result: Work) => {
      setWork(result)
      // Only a build that produced a PDF replaces the one on screen. A failed
      // recompile leaves the last good paper where it was, which is what the
      // errors beside it are about.
      if (result.state === 'done' && result.ok) setShown({ mode: which, stamp: Date.now() })
    }

    try {
      const first = await kick()
      setWork(first)
      if (first.state !== 'running') return settle(first)
      poll.current = window.setInterval(async () => {
        try {
          const next = await check()
          setWork(next)
          if (next.state !== 'running') {
            stopPolling()
            settle(next)
          }
        } catch (e) {
          stopPolling()
          record('error.shown', { where: 'pdf', reason: 'build' })
          setWork({ state: 'failed', elapsed_seconds: null, error: String(e) })
        }
      }, 2000)
    } catch (e) {
      record('error.shown', { where: 'pdf', reason: 'build' })
      setWork({ state: 'failed', elapsed_seconds: null, error: String(e) })
    }
  }

  const problems: Problem[] = work?.state === 'done' ? (work.problems ?? []) : []
  const errors = problems.filter((p) => p.severity === 'error').length
  const warnings = problems.length - errors

  // Open the list when something actually failed, and leave warnings folded
  // away: on a paper this size they are a dozen underfull boxes you have
  // already decided to live with.
  useEffect(() => {
    if (work?.state === 'done') setListing(errors > 0)
  }, [work, errors])

  // -- the editor asking the paper to go somewhere ----------------------
  useEffect(() => {
    if (!showInPdf) return
    if (!shown) {
      setNote('Compile the paper and it will follow the line you are on.')
      return
    }
    let stale = false
    setNote(null)
    api
      .synctexView(
        showInPdf.path,
        showInPdf.line,
        shown.mode === 'accepted' ? undefined : (sessionId ?? undefined),
        shown.mode === 'review',
      )
      .then((view) => {
        if (stale) return
        setMark({ areas: view.areas, nonce: showInPdf.nonce })
        if (view.fell_forward)
          setNote(
            `Line ${view.asked_line} prints nothing, so this is line ${view.line}, the next one that does.`,
          )
      })
      .catch((e) => {
        if (stale) return
        record('error.shown', { where: 'pdf', reason: 'synctex' })
        setNote(String(e))
      })
    return () => {
      stale = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showInPdf?.nonce])

  const busy = work?.state === 'running'

  return (
    <section className="pane-column pdf-pane">
      <header className="pdf-bar">
        <button className="tiny" onClick={onCollapse} title="Hide the PDF pane">
          ‹
        </button>
        <button
          className="primary tiny"
          onClick={() => run(mode)}
          disabled={busy}
          title="Build this version of the paper again"
        >
          {busy ? 'Compiling…' : 'Recompile'}
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
        {work?.state === 'done' && !work.ok && errors === 0 && (
          <div className="notice bad">
            <strong>Compile failed</strong>, and the log says nothing this reader
            could pin down. The end of it is in the terminal Galley is running in.
          </div>
        )}

        {problems.length > 0 && (
          <div className={`pdf-problems${errors ? ' failed' : ''}`}>
            <button className="pdf-problems-head" onClick={() => setListing((open) => !open)}>
              <span className="caret">{listing ? '▾' : '▸'}</span>
              <span>
                {errors > 0 && <strong>{errors === 1 ? '1 error' : `${errors} errors`}</strong>}
                {errors > 0 && warnings > 0 && ', '}
                {warnings > 0 && (warnings === 1 ? '1 warning' : `${warnings} warnings`)}
              </span>
            </button>
            {listing && (
              <ul className="pdf-problem-list">
                {problems.map((problem, i) => (
                  <li key={i} className={problem.severity}>
                    <button
                      className="pdf-problem"
                      disabled={!problem.path}
                      title={problem.path ? `Open ${problem.path}` : 'The log did not say where'}
                      onClick={() =>
                        problem.path &&
                        onJump({
                          path: problem.path,
                          line: problem.line ?? 1,
                          in_project: true,
                        })
                      }
                    >
                      {problem.path && (
                        <span className="where mono">
                          {problem.path.split('/').pop()}
                          {problem.line ? `:${problem.line}` : ''}
                        </span>
                      )}
                      <span className="what">{problem.message}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {note && <div className="notice info">{note}</div>}

        {shown ? (
          <PdfViewer
            src={api.pdfUrl(shownSession(shown, sessionId), shown.mode === 'review', shown.stamp)}
            sessionId={shownSession(shown, sessionId)}
            review={shown.mode === 'review'}
            // Recorded here rather than where the jump lands: the viewer calls
            // this only for a double-click that found its source, which is the
            // gesture worth counting. The problem list uses `onJump` too, and
            // clicking an error is not the same thing at all.
            onJump={(where) => {
              record('editor.jump_from_pdf')
              onJump(where)
            }}
            mark={mark}
          />
        ) : busy ? (
          <div className="empty small">Building the paper…</div>
        ) : (
          <div className="empty">
            {buildsPdf ? (
              <>
                Compile to see the paper.
                <div style={{ marginTop: 10 }}>
                  <button className="primary" onClick={() => run('accepted')}>
                    Compile
                  </button>
                </div>
              </>
            ) : (
              <>
                This project builds no PDF.
                <div className="small" style={{ marginTop: 8 }}>
                  Nothing here matches <span className="mono">[paper] main_tex</span>.
                  Point it at the file latexmk should build, or leave it — the
                  editor, the rail, Claude and the merge pane all work without one.
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </section>
  )
}
