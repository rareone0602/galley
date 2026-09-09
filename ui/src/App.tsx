import { useCallback, useEffect, useState } from 'react'
import { api, type Config, type Session } from './api'
import GitPanel from './components/GitPanel'
import LogPane from './components/LogPane'
import MergePane from './components/MergePane'
import PdfPane from './components/PdfPane'

type Tab = 'log' | 'merge' | 'git' | 'pdf'

export default function App() {
  const [config, setConfig] = useState<Config | null>(null)
  const [sessions, setSessions] = useState<Session[]>([])
  const [current, setCurrent] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('log')
  const [prompt, setPrompt] = useState('')
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(async () => {
    try {
      setSessions(await api.sessions())
    } catch (e) {
      setError(String(e))
    }
  }, [])

  useEffect(() => {
    api.config().then(setConfig).catch((e) => setError(String(e)))
    void reload()
    const timer = setInterval(reload, 5000)
    return () => clearInterval(timer)
  }, [reload])

  const live = sessions.filter((s) => s.status !== 'removed')
  const session = live.find((s) => s.id === current) ?? null

  async function start() {
    if (!prompt.trim()) return
    try {
      const s = await api.createSession(prompt)
      setPrompt('')
      setCurrent(s.id)
      setTab('log')
      await reload()
    } catch (e) {
      setError(String(e))
    }
  }

  return (
    <div className="app">
      <aside className="rail">
        <header>
          <h1>Galley</h1>
          <div className="sub">
            {config ? config.paper_repo.split('/').slice(-1)[0] : '…'} ·{' '}
            {config?.main_branch ?? '…'} · {config?.bind ?? ''}
          </div>
        </header>

        <div style={{ padding: 12, borderBottom: '1px solid var(--line)' }}>
          <textarea
            rows={3}
            placeholder="What should Claude work on? It gets its own worktree."
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void start()
            }}
          />
          <button
            className="primary"
            style={{ width: '100%', marginTop: 6 }}
            onClick={start}
            disabled={!prompt.trim()}
          >
            New session
          </button>
        </div>

        <div className="scroll">
          {live.length === 0 && <div className="empty small">No sessions yet.</div>}
          {live.map((s) => (
            <div
              key={s.id}
              className={`session${s.id === current ? ' on' : ''}`}
              onClick={() => setCurrent(s.id)}
            >
              <div className="title">{s.prompt.slice(0, 70)}</div>
              <div className="meta">
                <span className={`dot ${s.running ? 'running' : s.status === 'error' ? 'error' : 'idle'}`} />
                <span className="mono">{s.branch.replace('claude/', '')}</span>
              </div>
            </div>
          ))}
        </div>

        {session && (
          <div style={{ padding: 10, borderTop: '1px solid var(--line)' }} className="row">
            <button
              className="tiny"
              onClick={() => api.stopSession(session.id).then(reload)}
              disabled={!session.running}
            >
              Stop
            </button>
            <button
              className="tiny"
              title="Remove the worktree; the branch is kept as provenance"
              onClick={() => api.removeSession(session.id).then(() => { setCurrent(null); return reload() })}
            >
              Close worktree
            </button>
          </div>
        )}
      </aside>

      <main className="main">
        <nav className="tabs">
          {(['log', 'merge', 'pdf'] as Tab[]).map((t) => (
            <button
              key={t}
              className={tab === t ? 'on' : ''}
              onClick={() => setTab(t)}
              disabled={!session && t !== 'pdf'}
            >
              {t === 'log' ? 'Session log' : t === 'merge' ? 'Merge' : 'PDF'}
            </button>
          ))}
          <button className={tab === 'git' ? 'on' : ''} onClick={() => setTab('git')}>
            Git &amp; Overleaf
          </button>
          <span className="spacer" />
          {session && (
            <span className="muted small" style={{ alignSelf: 'center' }}>
              {session.branch}
            </span>
          )}
        </nav>

        <div className="pane">
          {error && <div className="notice bad">{error}</div>}
          {tab === 'log' &&
            (session ? <LogPane session={session} /> : <div className="empty">Pick a session.</div>)}
          {tab === 'merge' &&
            (session ? (
              <MergePane sessionId={session.id} />
            ) : (
              <div className="empty">Pick a session to review its changes.</div>
            ))}
          {tab === 'git' && <GitPanel />}
          {tab === 'pdf' && (
            <PdfPane sessionId={session?.id ?? null} latexdiffAvailable={config?.latexdiff ?? false} />
          )}
        </div>
      </main>
    </div>
  )
}
