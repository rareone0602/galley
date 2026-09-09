import { useEffect, useState } from 'react'
import { api, type GitStatus, type SyncResult } from '../api'

/**
 * Ordinary staging and committing on the main branch, plus one compound Sync.
 *
 * Overleaf is a single-branch remote with a second writer attached — you are
 * collaborating with yourself — so Sync always rebases before it pushes, and
 * never forces.
 */
export default function GitPanel() {
  const [status, setStatus] = useState<GitStatus | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [message, setMessage] = useState('')
  const [sync, setSync] = useState<SyncResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function reload() {
    try {
      setStatus(await api.gitStatus())
      setError(null)
    } catch (e) {
      setError(String(e))
    }
  }

  useEffect(() => {
    void reload()
  }, [])

  if (error) return <div className="notice bad">{error}</div>
  if (!status) return <div className="empty">Reading the repository…</div>

  const ol = status.overleaf
  const canSync = ol.on_main && ol.clean && !busy

  return (
    <>
      {status.unbacked_results.length > 0 && (
        <div className="notice warn">
          <strong>A number appeared without a job behind it.</strong> These result files
          name a job id Galley has never run: {status.unbacked_results.join(', ')}. Every
          figure in the paper should trace back to a run.
        </div>
      )}

      <div className="card">
        <h3>Working tree — {status.branch} @ {status.head}</h3>
        {status.files.length === 0 ? (
          <div className="muted small">Clean.</div>
        ) : (
          <>
            <table>
              <tbody>
                {status.files.map((f) => (
                  <tr key={f.path}>
                    <td style={{ width: 24 }}>
                      <input
                        type="checkbox"
                        style={{ width: 'auto' }}
                        checked={picked.has(f.path)}
                        onChange={() =>
                          setPicked((prev) => {
                            const next = new Set(prev)
                            next.has(f.path) ? next.delete(f.path) : next.add(f.path)
                            return next
                          })
                        }
                      />
                    </td>
                    <td className="mono" style={{ width: 40 }}>
                      {f.index}
                      {f.worktree}
                    </td>
                    <td className="mono">{f.path}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="row" style={{ marginTop: 10 }}>
              <input
                placeholder="commit message"
                value={message}
                onChange={(e) => setMessage(e.target.value)}
              />
              <button
                disabled={!message.trim() || picked.size === 0 || busy}
                onClick={async () => {
                  setBusy(true)
                  try {
                    await api.commit(message, [...picked])
                    setMessage('')
                    setPicked(new Set())
                    await reload()
                  } catch (e) {
                    setError(String(e))
                  } finally {
                    setBusy(false)
                  }
                }}
              >
                Commit {picked.size} file{picked.size === 1 ? '' : 's'}
              </button>
            </div>
            <div className="muted small" style={{ marginTop: 6 }}>
              Galley stages the paths you tick, never everything.
            </div>
          </>
        )}
      </div>

      <div className="card">
        <h3>Overleaf — {ol.remote}/{ol.remote_branch}</h3>
        <div className="row small" style={{ marginBottom: 8 }}>
          <span className={ol.on_main ? 'muted' : ''} style={{ color: ol.on_main ? undefined : 'var(--del)' }}>
            on {ol.branch}
          </span>
          <span className="muted">·</span>
          <span style={{ color: ol.clean ? undefined : 'var(--del)' }}>
            {ol.clean ? 'clean' : 'uncommitted changes'}
          </span>
          {ol.ahead !== null && (
            <>
              <span className="muted">·</span>
              <span className="muted">
                {ol.ahead} ahead, {ol.behind} behind
              </span>
            </>
          )}
        </div>

        {ol.conflicts.length > 0 && (
          <div className="notice warn">
            <strong>The remote moved and these files conflict:</strong>{' '}
            {ol.conflicts.join(', ')}. Resolve them in the merge pane — it is the same
            sentence-level tool — then continue the rebase.
            <div className="row" style={{ marginTop: 8 }}>
              <button className="tiny" onClick={() => api.rebase('continue').then(setSync).then(reload)}>
                Continue rebase
              </button>
              <button className="tiny" onClick={() => api.rebase('abort').then(setSync).then(reload)}>
                Abort rebase
              </button>
            </div>
          </div>
        )}

        {sync && (
          <div className={`notice ${sync.ok ? 'good' : 'bad'}`}>
            <strong>{sync.step}:</strong> {sync.message}
          </div>
        )}

        <div className="row">
          <button
            className="primary"
            disabled={!canSync}
            onClick={async () => {
              setBusy(true)
              try {
                setSync(await api.sync(true))
                await reload()
              } finally {
                setBusy(false)
              }
            }}
          >
            Sync
          </button>
          <span className="muted small">
            Pull with rebase, then push. Never a force. Claude's branches stay local.
          </span>
        </div>
      </div>

      <div className="card">
        <h3>Recent commits</h3>
        <table>
          <tbody>
            {status.log.map((c) => (
              <tr key={c.sha}>
                <td className="mono" style={{ width: 80 }}>{c.sha.slice(0, 8)}</td>
                <td>{c.subject}</td>
                <td className="muted small" style={{ width: 130 }}>{c.author}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
