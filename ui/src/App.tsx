import { useCallback, useEffect, useRef, useState } from 'react'
import { type ImperativePanelHandle, Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels'
import { api, type Config, type Selection, type Session, type SourceLocation } from './api'
import Editor from './components/Editor'
import FileTree from './components/FileTree'
import GitPanel from './components/GitPanel'
import LogPane from './components/LogPane'
import MergePane from './components/MergePane'
import PdfPane from './components/PdfPane'

type Tab = 'editor' | 'review' | 'chat' | 'git'

/**
 * Overleaf's shape: project files on the left, source in the middle, PDF on
 * the right, dividers you can drag.
 *
 * What Galley adds sits inside that shape rather than beside it. Select a
 * passage in the editor and ask Claude about it; the answer arrives as a diff
 * you accept a sentence at a time in Review. Claude never writes to the file
 * you are editing — it works on its own branch, and Save in Review is yours.
 *
 * The resizing comes from `react-resizable-panels`, the same MIT library
 * Overleaf uses, rather than from their AGPL source.
 */
export default function App() {
  const [config, setConfig] = useState<Config | null>(null)
  const [sessions, setSessions] = useState<Session[]>([])
  const [current, setCurrent] = useState<string | null>(null)
  const [detail, setDetail] = useState<Session | null>(null)
  const [tab, setTab] = useState<Tab>('editor')
  const [prompt, setPrompt] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [resizing, setResizing] = useState(false)
  const [pdfOpen, setPdfOpen] = useState(true)
  const [starting, setStarting] = useState(false)

  const [openPath, setOpenPath] = useState<string | null>(null)
  const [dirty, setDirty] = useState<Set<string>>(new Set())
  const [treeKey, setTreeKey] = useState(0)
  const [fileKey, setFileKey] = useState(0)
  const [jumpTo, setJumpTo] = useState<{ path: string; line: number; nonce: number } | null>(
    null,
  )
  const [showInPdf, setShowInPdf] = useState<{
    path: string
    line: number
    nonce: number
  } | null>(null)

  const pdfPanel = useRef<ImperativePanelHandle>(null)

  const reload = useCallback(async () => {
    try {
      setSessions(await api.sessions())
    } catch (e) {
      setError(String(e))
    }
  }, [])

  useEffect(() => {
    api
      .config()
      .then((c) => {
        setConfig(c)
        setOpenPath((p) => p ?? c.main_tex)
      })
      .catch((e) => setError(String(e)))
    void reload()
    const timer = setInterval(reload, 5000)
    return () => clearInterval(timer)
  }, [reload])

  // The session's own detail carries what it changed, which is what the Review
  // tab's badge counts. The list route does not run git per session.
  useEffect(() => {
    if (!current) {
      setDetail(null)
      return
    }
    let stale = false
    const poll = () =>
      api
        .session(current)
        .then((s) => !stale && setDetail(s))
        .catch(() => undefined)
    void poll()
    const timer = setInterval(poll, 4000)
    return () => {
      stale = true
      clearInterval(timer)
    }
  }, [current])

  const live = sessions.filter((s) => s.status !== 'removed')
  const session = live.find((s) => s.id === current) ?? null
  const pending = detail?.id === current ? (detail.files ?? []).length : 0

  const markDirty = useCallback((path: string, isDirty: boolean) => {
    setDirty((prev) => {
      if (prev.has(path) === isDirty) return prev
      const next = new Set(prev)
      isDirty ? next.add(path) : next.delete(path)
      return next
    })
  }, [])

  /** After anything writes to the paper, the rail and the open file are stale. */
  const refreshFiles = useCallback(() => {
    setTreeKey((k) => k + 1)
    setFileKey((k) => k + 1)
  }, [])

  /** Double-clicking the PDF: open that file and put the cursor on the line. */
  const jumpToSource = useCallback((where: SourceLocation) => {
    setOpenPath(where.path)
    setTab('editor')
    setJumpTo({ path: where.path, line: where.line, nonce: Date.now() })
  }, [])

  /** The arrow the other way: the line you are writing, found on the page. */
  const showLineInPdf = useCallback((path: string, line: number) => {
    setShowInPdf({ path, line, nonce: Date.now() })
  }, [])

  const askAboutSelection = useCallback(
    async (selection: Selection, instruction: string) => {
      setStarting(true)
      try {
        const s = await api.createSession(instruction, selection)
        setCurrent(s.id)
        setTab('chat')
        setError(null)
        await reload()
      } catch (e) {
        setError(String(e))
        throw e
      } finally {
        setStarting(false)
      }
    },
    [reload],
  )

  async function startPlain() {
    if (!prompt.trim()) return
    setStarting(true)
    try {
      const s = await api.createSession(prompt)
      setPrompt('')
      setCurrent(s.id)
      setTab('chat')
      await reload()
    } catch (e) {
      setError(String(e))
    } finally {
      setStarting(false)
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
        {/* -- rail: the project, then the sessions ---------------------- */}
        <Panel id="rail" order={1} defaultSize={19} minSize={12} maxSize={34}>
          <PanelGroup autoSaveId="galley-rail" direction="vertical" className="rail">
            <Panel id="files" order={1} defaultSize={62} minSize={20}>
              <aside className="rail-section">
                <div className="rail-head">File tree</div>
                <FileTree
                  open={openPath}
                  onOpen={(p) => {
                    setOpenPath(p)
                    setTab('editor')
                  }}
                  reloadKey={treeKey}
                  dirty={dirty}
                  // A file that moved has to take the editor with it. The
                  // editor reloads on `path` alone, so this is the whole of it.
                  onRenamed={(from, to) => {
                    if (openPath === from) setOpenPath(to)
                  }}
                  // One that has gone would leave the editor pointing at
                  // nothing, so fall back to what the build compiles.
                  onDeleted={(path) => {
                    if (openPath === path) setOpenPath(config?.main_tex ?? null)
                  }}
                />
              </aside>
            </Panel>

            <PanelResizeHandle className="handle horizontal" onDragging={setResizing} />

            <Panel id="sessions" order={2} defaultSize={38} minSize={12}>
              <aside className="rail-section">
                <div className="rail-head">Claude sessions</div>

                <div className="new-session">
                  <textarea
                    rows={2}
                    placeholder="Ask about the whole paper. For one passage, select it in the editor."
                    value={prompt}
                    onChange={(e) => setPrompt(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void startPlain()
                    }}
                  />
                  <button
                    className="primary"
                    onClick={startPlain}
                    disabled={!prompt.trim() || starting}
                  >
                    {starting ? 'Starting…' : 'New session'}
                  </button>
                </div>

                <div className="scroll">
                  {live.length === 0 && <div className="empty small">No sessions yet.</div>}
                  {live.map((s) => (
                    <div
                      key={s.id}
                      className={`session${s.id === current ? ' on' : ''}`}
                      onClick={() => {
                        setCurrent(s.id)
                        setTab('chat')
                      }}
                    >
                      <div className="title">{s.prompt.slice(0, 70)}</div>
                      <div className="meta">
                        <span
                          className={`dot ${s.running ? 'running' : s.status === 'error' ? 'error' : 'idle'}`}
                        />
                        {s.sel_path && (
                          <span className="chip" title={s.sel_path}>
                            {s.sel_path.split('/').pop()}
                          </span>
                        )}
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
          </PanelGroup>
        </Panel>

        <PanelResizeHandle
          className="handle"
          onDragging={setResizing}
          aria-label="Resize the project rail"
        />

        {/* -- middle: write, then review ------------------------------- */}
        <Panel id="work" order={2} minSize={25}>
          <section className="pane-column">
            <nav className="tabs">
              {(['editor', 'review', 'chat', 'git'] as Tab[]).map((t) => (
                <button
                  key={t}
                  className={tab === t ? 'on' : ''}
                  onClick={() => setTab(t)}
                  disabled={!session && (t === 'review' || t === 'chat')}
                >
                  {t === 'editor'
                    ? 'Editor'
                    : t === 'review'
                      ? 'Review'
                      : t === 'chat'
                        ? 'Chat'
                        : 'Git & Overleaf'}
                  {t === 'review' && pending > 0 && <span className="count">{pending}</span>}
                </button>
              ))}
              <span className="spacer" />
              {session && <span className="branch">{session.branch}</span>}
            </nav>

            <div className={`pane${tab === 'editor' || tab === 'review' ? ' flush' : ''}`}>
              {error && <div className="notice bad">{error}</div>}
              {tab === 'editor' && (
                <Editor
                  path={openPath}
                  reloadKey={fileKey}
                  onDirtyChange={markDirty}
                  onSaved={() => setTreeKey((k) => k + 1)}
                  onAsk={askAboutSelection}
                  busy={starting}
                  jumpTo={jumpTo}
                  onShowInPdf={showLineInPdf}
                />
              )}
              {tab === 'review' &&
                (session ? (
                  <MergePane sessionId={session.id} onSaved={refreshFiles} />
                ) : (
                  <div className="empty">Pick a session to review its changes.</div>
                ))}
              {tab === 'chat' &&
                (session ? <LogPane session={session} /> : <div className="empty">Pick a session.</div>)}
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
          defaultSize={38}
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
            onJump={jumpToSource}
            showInPdf={showInPdf}
          />
        </Panel>
      </PanelGroup>
    </div>
  )
}
