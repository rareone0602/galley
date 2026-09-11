import { useCallback, useEffect, useRef, useState } from 'react'
import { type ImperativePanelHandle, Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels'
import { api, type Config, type Selection, type Session, type SourceLocation } from './api'
import Editor from './components/Editor'
import FileTree from './components/FileTree'
import GitPanel from './components/GitPanel'
import LogPane from './components/LogPane'
import MergePane from './components/MergePane'
import PdfPane from './components/PdfPane'
import PreviewPane from './components/PreviewPane'
import { previewKind } from './components/preview/kinds'
import { configure, record } from './usage'

type Tab = 'editor' | 'review' | 'chat' | 'git'

/** Whether saving this file could change the printed page. A note, a script or
 *  a data file cannot, and rebuilding the paper because you edited a README
 *  would be latexmk for nothing. */
const BUILT_FROM = /\.(tex|sty|cls|bib|bst|ltx|def|clo|cfg)$/i

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
  const [error, setErrorText] = useState<string | null>(null)

  /* Every error the UI puts in front of you, counted by where it came from.
   * A wrapper rather than a call beside each `setError`, so a new one cannot
   * quietly go unrecorded. */
  const setError = useCallback((message: string | null, where = 'shell') => {
    setErrorText(message)
    if (message) record('error.shown', { where })
  }, [])
  const [resizing, setResizing] = useState(false)
  const [pdfOpen, setPdfOpen] = useState(true)
  const [starting, setStarting] = useState(false)

  const [openPath, setOpenPath] = useState<string | null>(null)
  /* What is in the editor this moment, for the preview pane. The path travels
   * with it because the editor sends its text a beat after you stop typing,
   * which can be a beat after you have opened something else. */
  const [liveText, setLiveText] = useState<{ path: string; text: string } | null>(null)
  /* Which file you have told the preview to get out of the way for. Keyed by
   * path rather than a flag, so dismissing it on one file does not silently
   * turn it off for the next one. */
  const [previewOffFor, setPreviewOffFor] = useState<string | null>(null)
  const [dirty, setDirty] = useState<Set<string>>(new Set())
  /* Ctrl-S pressed outside the editor's own text. A number rather than a flag,
   * so two presses are two saves. */
  const [saveNow, setSaveNow] = useState(0)
  /* When a file the paper is built from was last written. The PDF pane watches
   * it and rebuilds, which is what Save means in Overleaf and is what you
   * expect from anything that looks like Overleaf. */
  const [savedAt, setSavedAt] = useState(0)
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
        // The server owns the switch, so nothing is recorded until it says so.
        configure(c.usage)
        record('app.open')
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
  /* What the right-hand column draws. A `.tex` is drawn by LaTeX and belongs
   * to the PDF pane; a note, a config or a table the browser can draw itself,
   * and then it does — in the same place, so the source stays on the left. */
  const preview = previewOffFor === openPath ? null : previewKind(openPath)

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
    // And so is the editor text the preview was drawing: a merge has just
    // written the file underneath it. Dropping it sends the preview back to
    // the copy on disk until the editor speaks again.
    setLiveText(null)
  }, [])

  const takeEditorText = useCallback((path: string, text: string) => {
    setLiveText({ path, text })
  }, [])

  /* Ctrl-S is the workbench's, wherever you are standing.
   *
   * CodeMirror binds it too, so inside the text this listener never runs — the
   * editor has already saved and called preventDefault by the time the press
   * reaches the window. Everywhere else it used to fall through to the
   * browser, which offered to save the *page*: click the file rail, or the
   * preview, or the ask box, then reach for Ctrl-S, and you got a download
   * dialog and an unsaved file. The review tab keeps the chord for itself,
   * where it means "show me what Save would write". */
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.altKey || !(event.ctrlKey || event.metaKey)) return
      if (event.key.toLowerCase() !== 's') return
      if (event.defaultPrevented) return
      if (tab === 'review') return
      event.preventDefault()
      if (tab === 'editor' && openPath) setSaveNow(Date.now())
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [tab, openPath])

  const noteSaved = useCallback((path: string) => {
    setTreeKey((k) => k + 1)
    if (BUILT_FROM.test(path)) setSavedAt(Date.now())
  }, [])

  const openFile = useCallback((path: string) => {
    setOpenPath(path)
    setTab('editor')
  }, [])

  /** Double-clicking the PDF: open that file and put the cursor on the line. */
  const jumpToSource = useCallback((where: SourceLocation) => {
    // Not recorded here: this runs for a double-click on the page *and* for a
    // click in the compile-problem list, which is a different gesture. The PDF
    // pane records the one that is a jump.
    setOpenPath(where.path)
    setTab('editor')
    setJumpTo({ path: where.path, line: where.line, nonce: Date.now() })
  }, [])

  /* The arrow the other way: the line you are writing, found on the page.
   * Handed to the editor only when there is a page — with no LaTeX root the
   * button would be there and could never work. */
  const showLineInPdf = useCallback((path: string, line: number) => {
    // Recorded by the editor, which knows whether the gesture got this far.
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
        // The raw setter: this is rethrown, and the editor records the error
        // it shows you. Counting it here as well would make one failed ask
        // look like two.
        setErrorText(String(e))
        throw e
      } finally {
        setStarting(false)
      }
    },
    [reload],
  )

  /* A build that failed, handed to Claude.
   *
   * Which Claude is the whole of the decision. A failure in the paper itself
   * starts a session: its worktree is forked from the working copy, so the
   * broken line is in it. A failure on a session's own branch belongs to that
   * session and to no other — a fresh one would be forked from the working
   * copy, which does not have the change that broke, and would go looking for
   * an error that is not there.
   *
   * The words are the server's (`latex.fix_request`), so they can be tested;
   * this decides only where they go. */
  const askClaudeToFix = useCallback(
    async (fixPrompt: string, onBranch: boolean) => {
      setStarting(true)
      try {
        if (onBranch && current) {
          await api.message(current, fixPrompt)
        } else {
          const s = await api.createSession(fixPrompt)
          setCurrent(s.id)
        }
        setTab('chat')
        setError(null)
        await reload()
      } catch (e) {
        setError(String(e), 'pdf')
        throw e
      } finally {
        setStarting(false)
      }
    },
    [current, reload, setError],
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
        <span className="meta mono" title="the model your sessions talk to">
          {config?.agent_model ?? ''}
        </span>
        <span className="spacer" />
        <span className="meta">{config?.publish ?? ''}</span>
        {!pdfOpen && (
          <button className="tiny" onClick={togglePdf}>
            {preview ? 'Show preview' : 'Show PDF'}
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
                  onOpen={openFile}
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
                        {s.cost_usd != null && s.cost_usd > 0 && (
                          <span className="cost" title="what this session has cost so far">
                            ${s.cost_usd.toFixed(2)}
                          </span>
                        )}
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
                      title="Delete the worktree and take the session off the rail. Its branch stays."
                      onClick={() => {
                        /* The one click here that cannot be taken back from
                         * inside Galley. The branch survives it; the chat and
                         * an unsaved review do not. */
                        const sure = window.confirm(
                          `Remove this session?\n\nIts worktree is deleted and its chat leaves the rail. ` +
                            `Its branch (${session.branch}) is kept, so nothing Claude wrote is lost — ` +
                            `but anything in Review you have not saved is.`,
                        )
                        if (!sure) return
                        void api.removeSession(session.id).then(() => {
                          setCurrent(null)
                          return reload()
                        })
                      }}
                    >
                      Remove
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
                  onClick={() => {
                    setTab(t)
                    record('tab.show', { tab: t })
                  }}
                  disabled={!session && (t === 'review' || t === 'chat')}
                >
                  {t === 'editor'
                    ? 'Editor'
                    : t === 'review'
                      ? 'Review'
                      : t === 'chat'
                        ? 'Chat'
                        : 'Git & publish'}
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
                  onSaved={noteSaved}
                  onAsk={askAboutSelection}
                  busy={starting}
                  jumpTo={jumpTo}
                  onShowInPdf={config?.builds_pdf ? showLineInPdf : undefined}
                  onText={takeEditorText}
                  saveNow={saveNow}
                />
              )}
              {tab === 'review' &&
                (session ? (
                  <MergePane sessionId={session.id} onSaved={refreshFiles} />
                ) : (
                  <div className="empty">Pick a session to review its changes.</div>
                ))}
              {tab === 'chat' &&
                (session ? (
                  <LogPane session={session} onChanged={reload} />
                ) : (
                  <div className="empty">Pick a session.</div>
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
          defaultSize={38}
          minSize={20}
          collapsible
          collapsedSize={0}
          onCollapse={() => setPdfOpen(false)}
          onExpand={() => setPdfOpen(true)}
        >
          {/* The preview covers the PDF rather than replacing it. Unmounting
              the PDF pane would throw away the built paper and the compile it
              is polling for, and you would be waiting on latexmk again for
              having glanced at a README. */}
          <div className="right-column">
            <PdfPane
              sessionId={session?.id ?? null}
              latexdiffAvailable={config?.latexdiff ?? false}
              onCollapse={togglePdf}
              onJump={jumpToSource}
              onFix={askClaudeToFix}
              sessionBusy={session?.running ?? false}
              showInPdf={showInPdf}
              buildsPdf={config?.builds_pdf ?? true}
              savedAt={savedAt}
            />
            {preview && openPath && (
              <PreviewPane
                path={openPath}
                kind={preview}
                text={liveText?.path === openPath ? liveText.text : undefined}
                reloadKey={fileKey}
                onOpenFile={openFile}
                onShowPdf={() => {
                  setPreviewOffFor(openPath)
                  record('preview.dismiss', { kind: preview })
                }}
              />
            )}
          </div>
        </Panel>
      </PanelGroup>
    </div>
  )
}
