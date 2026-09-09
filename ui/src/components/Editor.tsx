import { useCallback, useEffect, useRef, useState } from 'react'
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'
import {
  HighlightStyle,
  StreamLanguage,
  bracketMatching,
  indentOnInput,
  syntaxHighlighting,
} from '@codemirror/language'
import { stex } from '@codemirror/legacy-modes/mode/stex'
import { highlightSelectionMatches, search, searchKeymap } from '@codemirror/search'
import { Compartment, EditorState } from '@codemirror/state'
import {
  EditorView,
  drawSelection,
  highlightActiveLine,
  highlightActiveLineGutter,
  keymap,
  lineNumbers,
  rectangularSelection,
} from '@codemirror/view'
import { tags as t } from '@lezer/highlight'
import { api, type FileBody, type Selection } from '../api'

/* Overleaf's own source editor is CodeMirror 6, so this is the same editor,
 * with the LaTeX mode from @codemirror/legacy-modes rather than their Lezer
 * grammar. Colours follow their light theme: commands green, maths blue,
 * comments grey. */
const latexHighlight = HighlightStyle.define([
  { tag: t.tagName, color: 'var(--green-60)' },        // \command
  { tag: t.bracket, color: 'var(--neutral-60)' },
  { tag: t.atom, color: 'var(--blue-50)' },            // $ maths $
  { tag: t.string, color: 'var(--blue-50)' },
  { tag: t.comment, color: 'var(--neutral-50)', fontStyle: 'italic' },
  { tag: t.keyword, color: 'var(--green-60)', fontWeight: '600' },
  { tag: t.variableName, color: 'var(--content-primary)' },
  { tag: t.className, color: 'var(--yellow-50)' },
])

const galleyTheme = EditorView.theme({
  '&': { height: '100%', fontSize: 'var(--fs)', backgroundColor: 'var(--bg-primary)' },
  '.cm-scroller': { fontFamily: 'var(--mono)', lineHeight: '1.6' },
  '.cm-content': { padding: '10px 0', caretColor: 'var(--content-primary)' },
  '.cm-gutters': {
    backgroundColor: 'var(--bg-primary)',
    color: 'var(--content-disabled)',
    border: 'none',
    paddingRight: '4px',
  },
  '.cm-activeLineGutter': { backgroundColor: 'var(--bg-secondary)', color: 'var(--content-secondary)' },
  '.cm-activeLine': { backgroundColor: 'rgba(9, 136, 66, 0.04)' },
  '.cm-selectionBackground, &.cm-focused .cm-selectionBackground, ::selection': {
    backgroundColor: 'rgba(9, 136, 66, 0.18) !important',
  },
  '.cm-selectionMatch': { backgroundColor: 'var(--yellow-10)' },
  '&.cm-focused': { outline: 'none' },
})

/** Where the "Ask Claude" bubble should sit, in editor-relative pixels. */
type Bubble = { top: number; left: number; selection: Selection }

export default function Editor({
  path,
  reloadKey,
  onDirtyChange,
  onSaved,
  onAsk,
  busy,
}: {
  path: string | null
  reloadKey: number
  onDirtyChange: (path: string, dirty: boolean) => void
  onSaved: (path: string) => void
  onAsk: (selection: Selection, instruction: string) => Promise<void>
  busy: boolean
}) {
  const [host, setHost] = useState<HTMLDivElement | null>(null)
  const view = useRef<EditorView | null>(null)
  const saved = useRef<string>('')
  const pathRef = useRef<string | null>(null)
  const editable = useRef(new Compartment())

  const [file, setFile] = useState<FileBody | null>(null)
  const [dirty, setDirty] = useState(false)
  const [status, setStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [bubble, setBubble] = useState<Bubble | null>(null)
  const [asking, setAsking] = useState(false)
  const [instruction, setInstruction] = useState('')

  pathRef.current = path

  const save = useCallback(async () => {
    const v = view.current
    const rel = pathRef.current
    if (!v || !rel) return
    const content = v.state.doc.toString()
    try {
      const res = await api.writeFile(rel, content)
      saved.current = content
      setDirty(false)
      onDirtyChange(rel, false)
      setStatus(`Saved · ${res.bytes.toLocaleString()} bytes`)
      setError(null)
      onSaved(rel)
    } catch (e) {
      setError(String(e))
    }
  }, [onDirtyChange, onSaved])

  // -- build the editor once the host element exists --------------------
  useEffect(() => {
    if (!host) return
    const v = new EditorView({
      parent: host,
      state: EditorState.create({
        doc: '',
        extensions: [
          lineNumbers(),
          highlightActiveLine(),
          highlightActiveLineGutter(),
          history(),
          drawSelection(),
          rectangularSelection(),
          indentOnInput(),
          bracketMatching(),
          search(),
          highlightSelectionMatches(),
          StreamLanguage.define(stex),
          syntaxHighlighting(latexHighlight),
          galleyTheme,
          EditorView.lineWrapping,
          keymap.of([
            {
              key: 'Mod-s',
              preventDefault: true,
              run: () => {
                void save()
                return true
              },
            },
            ...defaultKeymap,
            ...historyKeymap,
            ...searchKeymap,
            indentWithTab,
          ]),
          editable.current.of(EditorView.editable.of(true)),
          EditorView.updateListener.of((update) => {
            if (update.docChanged) {
              const now = update.state.doc.toString()
              const isDirty = now !== saved.current
              setDirty(isDirty)
              setStatus(null)
              if (pathRef.current) onDirtyChange(pathRef.current, isDirty)
            }
            if (update.selectionSet || update.docChanged) refreshBubble(update.view)
          }),
        ],
      }),
    })
    view.current = v
    return () => {
      v.destroy()
      view.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [host])

  /** Show the bubble only for a real block of prose, not a stray caret. */
  function refreshBubble(v: EditorView) {
    const { from, to } = v.state.selection.main
    const rel = pathRef.current
    if (!rel || to - from < 3) {
      setBubble(null)
      setAsking(false)
      return
    }
    const coords = v.coordsAtPos(from)
    const box = v.dom.parentElement?.getBoundingClientRect()
    if (!coords || !box) return
    setBubble({
      top: coords.bottom - box.top + 6,
      left: Math.max(8, Math.min(coords.left - box.left, box.width - 340)),
      selection: { path: rel, start: from, end: to, text: v.state.sliceDoc(from, to) },
    })
  }

  // -- load whatever file is open --------------------------------------
  useEffect(() => {
    const v = view.current
    if (!v) return
    setBubble(null)
    setAsking(false)
    setStatus(null)
    setError(null)
    if (!path) {
      setFile(null)
      return
    }
    let stale = false
    api
      .file(path)
      .then((body) => {
        if (stale || !view.current) return
        setFile(body)
        const text = body.content ?? ''
        saved.current = text
        setDirty(false)
        onDirtyChange(path, false)
        view.current.dispatch({
          changes: { from: 0, to: view.current.state.doc.length, insert: text },
          selection: { anchor: 0 },
          effects: editable.current.reconfigure(EditorView.editable.of(body.content !== null)),
          scrollIntoView: true,
        })
      })
      .catch((e) => !stale && setError(String(e)))
    return () => {
      stale = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, reloadKey, host])

  async function ask() {
    if (!bubble || !instruction.trim()) return
    try {
      await onAsk(bubble.selection, instruction.trim())
      setInstruction('')
      setAsking(false)
      setBubble(null)
    } catch (e) {
      setError(String(e))
    }
  }

  const binary = file !== null && file.content === null

  return (
    <div className="editor-wrap">
      <div className="editor-bar">
        <span className="path mono">{path ?? 'no file open'}</span>
        {dirty && <span className="badge amber">unsaved</span>}
        {status && <span className="muted small">{status}</span>}
        <span className="grow" />
        <span className="muted small hint">select a passage to ask Claude</span>
        <button className="tiny primary" onClick={() => void save()} disabled={!dirty || binary}>
          Save
        </button>
      </div>

      {error && <div className="notice bad" style={{ margin: '8px 12px' }}>{error}</div>}

      {binary && path && (
        <div className="binary-view">
          {file.type === 'image' ? (
            <img src={api.blobUrl(path)} alt={path} />
          ) : (
            <iframe className="pdf" src={api.blobUrl(path)} title={path} />
          )}
        </div>
      )}

      <div className="editor-host" style={binary ? { display: 'none' } : undefined}>
        <div ref={setHost} className="cm-host" style={path ? undefined : { display: 'none' }} />
        {!path && (
          <div className="empty">
            Pick a file in the rail to start writing. Select a passage and Claude
            can propose a rewrite of just that passage.
          </div>
        )}

        {bubble && !asking && (
          <button
            className="ask-bubble"
            style={{ top: bubble.top, left: bubble.left }}
            onClick={() => setAsking(true)}
          >
            <span className="spark">✦</span> Ask Claude
          </button>
        )}

        {bubble && asking && (
          <div className="ask-popover" style={{ top: bubble.top, left: bubble.left }}>
            <div className="quoted">{bubble.selection.text.slice(0, 220)}</div>
            <textarea
              autoFocus
              rows={3}
              value={instruction}
              placeholder="What should Claude do with this passage?"
              onChange={(e) => setInstruction(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') setAsking(false)
                if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void ask()
              }}
            />
            <div className="row">
              <span className="muted small">Claude drafts on its own branch.</span>
              <span className="grow" />
              <button className="tiny" onClick={() => setAsking(false)}>
                Cancel
              </button>
              <button
                className="tiny primary"
                onClick={() => void ask()}
                disabled={!instruction.trim() || busy}
              >
                {busy ? 'Starting…' : 'Ask Claude'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
