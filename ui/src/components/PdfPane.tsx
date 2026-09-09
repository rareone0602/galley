import { useState } from 'react'
import { api, type CompileResult } from '../api'

/**
 * The right-hand pane: the paper as it will look in print, always on screen.
 *
 * Two views of the same change. The merge pane beside it catches wording; the
 * marked-up latexdiff PDF catches meaning — a claim that got quietly
 * strengthened while every individual sentence looked reasonable.
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
  const [result, setResult] = useState<CompileResult | null>(null)
  const [mode, setMode] = useState<'accepted' | 'branch' | 'review'>('accepted')
  const [busy, setBusy] = useState(false)
  const [stamp, setStamp] = useState(0)

  async function run(which: 'accepted' | 'branch' | 'review') {
    setBusy(true)
    setMode(which)
    try {
      if (which === 'review' && sessionId) setResult(await api.review(sessionId))
      else setResult(await api.compile(which === 'branch' && sessionId ? sessionId : undefined))
      setStamp(Date.now())
    } catch (e) {
      setResult({ ok: false, pdf: null, errors: [String(e)], undefined: [], log_tail: '' })
    } finally {
      setBusy(false)
    }
  }

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
                : 'Marked up: accepted state against this branch'
            }
          >
            Marked up
          </button>
        </div>
        <span className="spacer" />
        {busy && <span className="muted small">latexmk…</span>}
        {!busy && result?.ok && <span className="muted small">built</span>}
      </header>

      <div className="pdf-body">
        {result && !result.ok && (
          <div className="notice bad">
            <strong>Compile failed.</strong>
            <pre style={{ marginTop: 6 }}>{result.errors.join('\n')}</pre>
          </div>
        )}
        {result?.undefined.length ? (
          <div className="notice warn">
            Undefined references: <span className="mono">{result.undefined.join(', ')}</span>
          </div>
        ) : null}

        {result?.ok ? (
          <iframe className="pdf" src={src} title="paper" />
        ) : (
          !result && (
            <div className="empty">
              Compile to see the paper.
              <div style={{ marginTop: 10 }}>
                <button className="primary" onClick={() => run('accepted')} disabled={busy}>
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
