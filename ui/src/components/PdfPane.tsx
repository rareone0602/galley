import { useState } from 'react'
import { api, type CompileResult } from '../api'

/**
 * Two views of the same change. The merge pane catches wording; the marked-up
 * latexdiff PDF catches meaning — a claim that got quietly strengthened while
 * every individual sentence looked reasonable.
 */
export default function PdfPane({
  sessionId,
  latexdiffAvailable,
}: {
  sessionId: string | null
  latexdiffAvailable: boolean
}) {
  const [result, setResult] = useState<CompileResult | null>(null)
  const [mode, setMode] = useState<'accepted' | 'review'>('accepted')
  const [busy, setBusy] = useState(false)
  const [stamp, setStamp] = useState(0)

  async function run(which: 'accepted' | 'review') {
    setBusy(true)
    setMode(which)
    try {
      setResult(which === 'review' && sessionId ? await api.review(sessionId) : await api.compile())
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
      : `/api/pdf?t=${stamp}`

  return (
    <>
      <div className="row" style={{ marginBottom: 10 }}>
        <button onClick={() => run('accepted')} disabled={busy}>
          Compile the paper
        </button>
        <button
          onClick={() => run('review')}
          disabled={busy || !sessionId || !latexdiffAvailable}
          title={
            !latexdiffAvailable
              ? 'latexdiff is not installed'
              : !sessionId
                ? 'pick a session first'
                : 'Marked-up PDF: accepted state versus this branch'
          }
        >
          latexdiff review
        </button>
        {busy && <span className="muted small">Running latexmk…</span>}
      </div>

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
        !result && <div className="empty">Compile to see the PDF.</div>
      )}
    </>
  )
}
