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
import { Compartment, EditorState, StateEffect, StateField } from '@codemirror/state'
import {
  Decoration,
  type DecorationSet,
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

/** The one thing Ctrl +/- changes. Overleaf calls it the editor font size. */
const SIZE_KEY = 'galley:editor-font-size'
const MIN_SIZE = 8
const MAX_SIZE = 32
const DEFAULT_SIZE = 14

function storedSize(): number {
  try {
    const raw = Number(localStorage.getItem(SIZE_KEY))
    return raw >= MIN_SIZE && raw <= MAX_SIZE ? raw : DEFAULT_SIZE
  } catch {
    return DEFAULT_SIZE
  }
}

const sizeTheme = (px: number) =>
  EditorView.theme({ '&': { fontSize: `${px}px` }, '.cm-gutters': { fontSize: `${px}px` } })

const galleyTheme = EditorView.theme({
  '&': { height: '100%', backgroundColor: 'var(--bg-primary)' },
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

/* Arriving from a double-click on the PDF, the line is flashed rather than
 * selected: a selection here would raise the "Ask Claude" bubble over the
 * text you came to read, and you have not asked for anything yet. */
const flashLine = StateEffect.define<number>()
const clearFlash = StateEffect.define<null>()
const flash = Decoration.line({ class: 'cm-jump-flash' })

const flashField = StateField.define<DecorationSet>({
  create: () => Decoration.none,
  update(marks, tr) {
    marks = marks.map(tr.changes)
    for (const effect of tr.effects) {
      if (effect.is(flashLine)) marks = Decoration.set([flash.range(effect.value)])
      if (effect.is(clearFlash)) marks = Decoration.none
    }
    return marks
  },
  provide: (field) => EditorView.decorations.from(field),
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
  jumpTo,
}: {
  path: string | null
  reloadKey: number
  onDirtyChange: (path: string, dirty: boolean) => void
  onSaved: (path: string) => void
  onAsk: (selection: Selection, instruction: string) => Promise<void>
  busy: boolean
  /** A line to put the cursor on, from double-clicking the PDF. */
  jumpTo?: { path: string; line: number; nonce: number } | null
}) {
  const [host, setHost] = useState<HTMLDivElement | null>(null)
  const view = useRef<EditorView | null>(null)
  const saved = useRef<string>('')
  const pathRef = useRef<string | null>(null)
  const editable = useRef(new Compartment())
  const sizing = useRef(new Compartment())

  const [file, setFile] = useState<FileBody | null>(null)
  const [dirty, setDirty] = useState(false)
  const [status, setStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [bubble, setBubble] = useState<Bubble | null>(null)
  const [asking, setAsking] = useState(false)
  const [instruction, setInstruction] = useState('')
  const [fontSize, setFontSize] = useState(storedSize)

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

  /** Step the font size, or reset it when `direction` is 0. */
  const bumpSize = useCallback((direction: number) => {
    setFontSize((current) => {
      const next = direction === 0 ? DEFAULT_SIZE : current + direction
      return Math.min(MAX_SIZE, Math.max(MIN_SIZE, next))
    })
    return true
  }, [])

  // Applying it is separate from choosing it, so the number shown in the
  // bar, the stored preference and the editor never disagree.
  useEffect(() => {
    view.current?.dispatch({
      effects: sizing.current.reconfigure(sizeTheme(fontSize)),
    })
    try {
      localStorage.setItem(SIZE_KEY, String(fontSize))
    } catch {
      /* a private window; the size just will not be remembered */
    }
  }, [fontSize])

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
            // Ctrl/Cmd +, - and 0, the way every editor does it. The
            // browser would otherwise zoom the whole page, which moves the
            // PDF and the file tree too; preventDefault keeps it here.
            { key: 'Mod-=', preventDefault: true, run: () => bumpSize(+1) },
            { key: 'Mod-Shift-=', preventDefault: true, run: () => bumpSize(+1) },
            { key: 'Mod--', preventDefault: true, run: () => bumpSize(-1) },
            { key: 'Mod-0', preventDefault: true, run: () => bumpSize(0) },
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
          sizing.current.of(sizeTheme(storedSize())),
          flashField,
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

  // The file is opened by the parent; this puts the cursor on the line and
  // holds it in the middle of the view, the way a jump should land.
  useEffect(() => {
    const v = view.current
    if (!v || !jumpTo || jumpTo.path !== path || !file) return
    const total = v.state.doc.lines
    const line = v.state.doc.line(Math.min(Math.max(jumpTo.line, 1), total))
    v.dispatch({
      selection: { anchor: line.from },
      effects: [
        EditorView.scrollIntoView(line.from, { y: 'center' }),
        flashLine.of(line.from),
      ],
    })
    v.focus()
    const fade = window.setTimeout(
      () => view.current?.dispatch({ effects: clearFlash.of(null) }),
      1600,
    )
    return () => window.clearTimeout(fade)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jumpTo?.nonce, path, file])

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
        <div className="seg" title="Editor font size — Ctrl/Cmd with +, − or 0">
          <button onClick={() => bumpSize(-1)} disabled={fontSize <= MIN_SIZE}>
            −
          </button>
          <button onClick={() => bumpSize(0)}>{fontSize}px</button>
          <button onClick={() => bumpSize(1)} disabled={fontSize >= MAX_SIZE}>
            +
          </button>
        </div>
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
