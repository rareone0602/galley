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
  onFix,
  sessionBusy,
  showInPdf,
  buildsPdf,
  savedAt,
}: {
  sessionId: string | null
  latexdiffAvailable: boolean
  onCollapse: () => void
  onJump: (where: SourceLocation) => void
  /** Hand a failed build to Claude. `onBranch` says the build that failed was
   *  a session's own, where the fix belongs to that session: a fresh one is
   *  forked from the working copy and would not contain the change that broke
   *  it, so it would go looking for an error that is not there. */
  onFix: (prompt: string, onBranch: boolean) => Promise<void>
  /** Whether the open session's agent is mid-turn. It cannot be told anything
   *  while it is, so the button says why rather than failing on the click. */
  sessionBusy: boolean
  /** Whether the project has the LaTeX root it names. When it does not there
   *  is no paper to draw, and saying so beats a button that cannot work. */
  buildsPdf: boolean
  /** A source line to go to, from the editor. The nonce is the gesture: the
   *  same line asked for twice should scroll and flash twice. */
  showInPdf?: { path: string; line: number; nonce: number } | null
  /** When you last saved a file the paper is built from. Changing it is the
   *  gesture, the way `showInPdf`'s nonce is: two saves are two builds. */
  savedAt?: number
}) {
  const [work, setWork] = useState<Work | null>(null)
  const [mode, setMode] = useState<Mode>('accepted')
  const [shown, setShown] = useState<Shown | null>(null)
  const [listing, setListing] = useState(false)
  const [mark, setMark] = useState<Mark | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [asking, setAsking] = useState(false)
  const poll = useRef<number | null>(null)
  /** A save that arrived while a build was running, still to be built. */
  const missed = useRef(false)

  function stopPolling() {
    if (poll.current) window.clearInterval(poll.current)
    poll.current = null
  }
  useEffect(() => stopPolling, [])

  async function run(which: Mode, auto = false) {
    setMode(which)
    stopPolling()
    // The build itself is recorded by the server, which knows its real
    // duration and its real outcome and does not stop knowing them when this
    // tab closes. What is recorded here is only what the server cannot see:
    // the request never landing, or the polling failing.
    const kick = () =>
      which === 'review' && sessionId
        ? api.review(sessionId)
        : api.compile(which === 'branch' && sessionId ? sessionId : undefined, auto)
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
      /* A save that landed while this build was running is not in it: asking
       * to compile while latexmk runs joins the run already going, and that
       * run read the file before you saved. So the save is remembered and
       * built now, or the PDF would sit there a version behind with nothing
       * on screen saying so. */
      if (missed.current) {
        missed.current = false
        void run(which, true)
      }
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

  /* Save is Recompile, the way it is in Overleaf.
   *
   * Only the accepted build, because that is the only one a save can make
   * stale: the editor writes to the paper's own working copy and never to a
   * session's checkout, so a saved file changes nothing about what is on a
   * branch. Looking at a proposed or a marked-up build therefore keeps it —
   * and a marked-up one takes minutes, which is not something to start
   * because somebody pressed Ctrl-S. */
  const lastSaved = useRef(savedAt)
  useEffect(() => {
    if (savedAt === undefined || savedAt === lastSaved.current) return
    lastSaved.current = savedAt
    if (!buildsPdf || mode !== 'accepted') return
    if (busy) {
      missed.current = true
      return
    }
    void run('accepted', true)
    // The gesture is the save. Re-running for anything else here would build
    // the paper every time the pane re-rendered.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [savedAt])

  /* A failed build, with the words to ask about it. The server writes those
   * words and withholds them when there is nothing to ask — a build that
   * worked, or the marked-up review, whose line numbers are in generated
   * files. So the button is here exactly when there is something to send. */
  const fixPrompt = work?.state === 'done' && !work.ok ? (work.fix_prompt ?? null) : null
  const onBranch = mode === 'branch'
  const cannotAsk = onBranch && sessionBusy

  async function askForFix() {
    if (!fixPrompt) return
    setAsking(true)
    record('compile.ask_fix', { mode, errors, continued: onBranch })
    try {
      await onFix(fixPrompt, onBranch)
    } catch {
      /* The shell shows it and counts it; a second notice here would be the
       * same failure twice. */
    } finally {
      setAsking(false)
    }
  }

  const askButton = fixPrompt && (
    <button
      className="tiny ask-fix"
      onClick={askForFix}
      disabled={asking || cannotAsk}
      title={
        cannotAsk
          ? 'That session is still working. It can be told when it stops.'
          : onBranch
            ? 'Tell this session what its own branch does not compile'
            : 'Start a session on the errors above, with the log'
      }
    >
      {asking ? 'Asking…' : 'Ask Claude to fix'}
    </button>
  )

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
          title={
            mode === 'accepted'
              ? 'Build the paper again. Saving a file does this for you.'
              : 'Build this version of the paper again'
          }
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
            {/* Nothing to click through to, which is precisely when handing the
                log to someone who can read it is worth the most. */}
            {askButton && <div style={{ marginTop: 8 }}>{askButton}</div>}
          </div>
        )}

        {problems.length > 0 && (
          <div className={`pdf-problems${errors ? ' failed' : ''}`}>
            <div className="pdf-problems-head">
              <button className="fold" onClick={() => setListing((open) => !open)}>
                <span className="caret">{listing ? '▾' : '▸'}</span>
                <span>
                  {errors > 0 && <strong>{errors === 1 ? '1 error' : `${errors} errors`}</strong>}
                  {errors > 0 && warnings > 0 && ', '}
                  {warnings > 0 && (warnings === 1 ? '1 warning' : `${warnings} warnings`)}
                </span>
              </button>
              {askButton}
            </div>
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
