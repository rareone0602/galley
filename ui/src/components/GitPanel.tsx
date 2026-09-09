import { useEffect, useState } from 'react'
import { api, type GitStatus, type SyncResult } from '../api'

/**
 * Ordinary staging and committing on the main branch, plus one compound Sync.
 *
 * The remote this was built against is Overleaf's git bridge: a single branch
 * with a second writer attached, so you are collaborating with yourself. Hence
 * Sync always rebases before it pushes, and never forces. Any ordinary remote
 * behaves correctly under those rules, and a project with no remote at all is
 * a normal state the panel names rather than hides.
 *
 * Why Sync is unavailable is the backend's sentence, not one written twice
 * here: `publish.blocked` is exactly what pressing it would have refused with.
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

  const pub = status.publish
  const canSync = pub.blocked === null && !busy
  const target =
    pub.state === 'no_remote' ? 'no remote configured' : `${pub.remote}/${pub.remote_branch}`

  return (
    <>
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
        <h3>Publish — {target}</h3>
        <div className="row small" style={{ marginBottom: 8 }}>
          <span className={pub.on_main ? 'muted' : ''} style={{ color: pub.on_main ? undefined : 'var(--del)' }}>
            on {pub.branch}
          </span>
          <span className="muted">·</span>
          <span style={{ color: pub.clean ? undefined : 'var(--del)' }}>
            {pub.clean ? 'clean' : 'uncommitted changes'}
          </span>
          <span className="muted">·</span>
          <span className="muted">
            {pub.state === 'ready'
              ? `${pub.ahead} ahead, ${pub.behind} behind`
              : pub.state === 'unpushed'
                ? 'never published from here'
                : 'nowhere to publish to'}
          </span>
        </div>

        {pub.conflicts.length > 0 && (
          <div className="notice warn">
            <strong>The remote moved and these files conflict:</strong>{' '}
            {pub.conflicts.join(', ')}. Resolve them in the merge pane — it is the same
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
            {pub.blocked
              ? `Sync is unavailable: ${pub.blocked}`
              : "Pull with rebase, then push. Never a force. Claude's branches stay local."}
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
