import { useCallback, useEffect, useRef, useState } from 'react'
import { type ImperativePanelHandle, Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels'
import { api, type Config, type Session } from './api'
import GitPanel from './components/GitPanel'
import LogPane from './components/LogPane'
import MergePane from './components/MergePane'
import PdfPane from './components/PdfPane'

type Tab = 'log' | 'merge' | 'git'

/**
 * Split view, the way Overleaf does it: source on the left, PDF on the right,
 * both always visible, a draggable divider between them.
 *
 * The resizing mechanics come from `react-resizable-panels` — the same MIT
 * library Overleaf itself uses, rather than their AGPL source. `autoSaveId`
 * is what remembers your divider positions between visits.
 */
export default function App() {
  const [config, setConfig] = useState<Config | null>(null)
  const [sessions, setSessions] = useState<Session[]>([])
  const [current, setCurrent] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('log')
  const [prompt, setPrompt] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [resizing, setResizing] = useState(false)
  const [pdfOpen, setPdfOpen] = useState(true)
  const pdfPanel = useRef<ImperativePanelHandle>(null)

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

  function togglePdf() {
    const panel = pdfPanel.current
    if (!panel) return
    panel.isCollapsed() ? panel.expand() : panel.collapse()
  }

  return (
    <div className="ide">
      <header className="toolbar">
        <span className="brand">Galley</span>
        <span className="project">
          {config ? config.paper_repo.split('/').slice(-1)[0] : '…'}
        </span>
        <span className="meta mono">{config?.main_branch ?? ''}</span>
        <span className="spacer" />
        <span className="meta">{config?.overleaf ?? ''}</span>
        {!pdfOpen && (
          <button className="tiny" onClick={togglePdf}>
            Show PDF
          </button>
        )}
      </header>

      <PanelGroup
        autoSaveId="galley-outer"
        direction="horizontal"
        className={`ide-body${resizing ? ' resizing' : ''}`}
      >
      {/* -- sessions rail -------------------------------------------- */}
      <Panel id="rail" order={1} defaultSize={19} minSize={12} maxSize={34}>
        <aside className="rail">
          <div className="rail-head">Sessions</div>

          <div className="new-session">
            <textarea
              rows={3}
              placeholder="What should Claude work on? It gets its own worktree."
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void start()
              }}
            />
            <button className="primary" onClick={start} disabled={!prompt.trim()}>
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
                  <span
                    className={`dot ${s.running ? 'running' : s.status === 'error' ? 'error' : 'idle'}`}
                  />
                  <span className="mono">{s.branch.replace('claude/', '')}</span>
                </div>
              </div>
            ))}
          </div>

          {session && (
            <div className="rail-foot row">
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
                onClick={() =>
                  api.removeSession(session.id).then(() => {
                    setCurrent(null)
                    return reload()
                  })
                }
              >
                Close worktree
              </button>
            </div>
          )}
        </aside>
      </Panel>

      <PanelResizeHandle
        className="handle"
        onDragging={setResizing}
        aria-label="Resize the session list"
      />

      {/* -- left: the work ------------------------------------------- */}
      <Panel id="work" order={2} minSize={25}>
        <section className="pane-column">
          <nav className="tabs">
            {(['log', 'merge', 'git'] as Tab[]).map((t) => (
              <button
                key={t}
                className={tab === t ? 'on' : ''}
                onClick={() => setTab(t)}
                disabled={!session && t !== 'git'}
              >
                {t === 'log' ? 'Session log' : t === 'merge' ? 'Merge' : 'Git & Overleaf'}
              </button>
            ))}
            <span className="spacer" />
            {session && <span className="branch">{session.branch}</span>}
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
          </div>
        </section>
      </Panel>

      <PanelResizeHandle
        className="handle"
        onDragging={setResizing}
        aria-label="Resize the PDF preview"
      />

      {/* -- right: the PDF, always there ----------------------------- */}
      <Panel
          id="pdf"
          order={3}
          ref={pdfPanel}
          defaultSize={42}
          minSize={20}
          collapsible
          collapsedSize={0}
          onCollapse={() => setPdfOpen(false)}
          onExpand={() => setPdfOpen(true)}
        >
          <PdfPane
            sessionId={session?.id ?? null}
            latexdiffAvailable={config?.latexdiff ?? false}
            onCollapse={togglePdf}
          />
        </Panel>
      </PanelGroup>
    </div>
  )
}
