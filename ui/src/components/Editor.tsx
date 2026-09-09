import { useCallback, useEffect, useRef, useState } from 'react'
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'
import {
  HighlightStyle,
  bracketMatching,
  indentOnInput,
  syntaxHighlighting,
} from '@codemirror/language'
import { highlightSelectionMatches, search, searchKeymap } from '@codemirror/search'
import { Compartment, EditorState, StateEffect, StateField, Transaction } from '@codemirror/state'
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
import { latexCompletion } from '../editor/completion'
import { languageFor } from '../editor/languages'
import {
  CLEAN,
  type SaveStatus,
  type ScrollSnapshot,
  bufferFor,
  keepBuffer,
  unsavedPaths,
  updateBuffer,
} from './editor/buffers'

/* Overleaf's own source editor is CodeMirror 6, so this is the same editor,
 * with the modes from @codemirror/legacy-modes rather than their Lezer
 * grammars. Colours follow their light theme: commands green, maths blue,
 * comments grey.
 *
 * One palette serves every language rather than one palette per mode, because
 * what is being coloured is a tag and not a language — a comment is the same
 * thing in Python as it is in LaTeX, and two palettes would be two owners for
 * the question of what colour it is. The second block below is the tags the
 * other modes reach for; of those, LaTeX emits only `number`, inside maths,
 * where blue is what the surrounding maths already is. */
const syntaxColours = HighlightStyle.define([
  { tag: t.tagName, color: 'var(--green-60)' },        // \command, and <div>
  { tag: t.bracket, color: 'var(--neutral-60)' },
  { tag: t.atom, color: 'var(--blue-50)' },            // $ maths $, and true
  { tag: t.string, color: 'var(--blue-50)' },
  { tag: t.comment, color: 'var(--neutral-50)', fontStyle: 'italic' },
  { tag: t.keyword, color: 'var(--green-60)', fontWeight: '600' },
  { tag: t.variableName, color: 'var(--content-primary)' },
  { tag: t.className, color: 'var(--yellow-50)' },
  { tag: t.number, color: 'var(--blue-50)' },
  { tag: t.propertyName, color: 'var(--yellow-50)' },  // a key in JSON or BibTeX
  { tag: t.attributeName, color: 'var(--yellow-50)' },
  { tag: t.operator, color: 'var(--neutral-70)' },
  { tag: t.meta, color: 'var(--neutral-60)' },         // a shebang, a decorator
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

/** How wide the ask panel is. The number lives here rather than in the
 *  stylesheet because the placement arithmetic needs it, and one owner is
 *  better than two that can drift; the panel is given it as an inline style. */
const PANEL_WIDTH = 330
/* Roughly how much room each panel needs, used only to choose a side. The real
 * height is whatever the content comes to, and is never measured. */
const BUBBLE_ROOM = 34
const POPOVER_ROOM = 200

/** Where the "Ask Claude" panel may sit, in editor-relative pixels. */
type Bubble = {
  /** Under the end of the selection, and above its start: the two places a
   *  panel can go without landing on the passage it is about. */
  below: number
  above: number
  left: number
  boxHeight: number
  /** How much is selected, in the units you would say it in. */
  size: string
  selection: Selection
}

/** Put the panel where it does not cover the passage. `above` is applied as a
 *  class that shifts the panel up by its own height, so the height itself
 *  never has to be measured. */
function place(bubble: Bubble, room: number): { top: number; above: boolean } {
  if (bubble.below + room <= bubble.boxHeight) return { top: bubble.below, above: false }
  if (bubble.above - room >= 0) return { top: bubble.above, above: true }
  return { top: Math.max(6, bubble.boxHeight - room), above: false }
}

function describeSelection(text: string, lines: number): string {
  const trimmed = text.trim()
  const words = trimmed ? trimmed.split(/\s+/).length : 0
  const counted = `${words} word${words === 1 ? '' : 's'}`
  return lines > 1 ? `${lines} lines · ${counted}` : counted
}

/** A wall-clock time, which is what "when did I save this" wants; a relative
 *  time would have to tick to stay true. */
function clock(at: number): string {
  return new Date(at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export default function Editor({
  path,
  reloadKey,
  onDirtyChange,
  onSaved,
  onAsk,
  busy,
  jumpTo,
  onShowInPdf,
}: {
  path: string | null
  reloadKey: number
  onDirtyChange: (path: string, dirty: boolean) => void
  onSaved: (path: string) => void
  onAsk: (selection: Selection, instruction: string) => Promise<void>
  busy: boolean
  /** A line to put the cursor on, from double-clicking the PDF. */
  jumpTo?: { path: string; line: number; nonce: number } | null
  /** The same arrow the other way: show the line you are on in the PDF. */
  onShowInPdf?: (path: string, line: number) => void
}) {
  const [host, setHost] = useState<HTMLDivElement | null>(null)
  const view = useRef<EditorView | null>(null)
  const editable = useRef(new Compartment())
  const sizing = useRef(new Compartment())
  /* The language is a compartment for the same reason the other two are: one
   * editor shows every file, and `Makefile` is not LaTeX. */
  const language = useRef(new Compartment())

  const [file, setFile] = useState<FileBody | null>(null)
  const [status, setStatus] = useState<SaveStatus>(CLEAN)
  const [error, setError] = useState<string | null>(null)
  const [bubble, setBubble] = useState<Bubble | null>(null)
  const [asking, setAsking] = useState(false)
  const [instruction, setInstruction] = useState('')
  const [fontSize, setFontSize] = useState(storedSize)

  /* The editor is built once and re-used for every file, so anything a
   * keystroke reaches for has to be read at the moment of the keystroke rather
   * than captured when the keymap was built. The parent hands us a fresh
   * `onSaved` on each of its renders, and rebuilding the editor for that would
   * throw away the undo history. */
  const pathRef = useRef<string | null>(null)
  const fontSizeRef = useRef(fontSize)
  const bubbleRef = useRef<Bubble | null>(null)
  const reportDirty = useRef(onDirtyChange)
  const reportSaved = useRef(onSaved)
  const showInPdf = useRef(onShowInPdf)
  pathRef.current = path
  fontSizeRef.current = fontSize
  bubbleRef.current = bubble
  reportDirty.current = onDirtyChange
  reportSaved.current = onSaved
  showInPdf.current = onShowInPdf

  /* Read from the store during the render because every change to it happens
   * beside a state change here, so there is always a render to carry it. */
  const unsavedElsewhere = unsavedPaths().filter((other) => other !== path)

  const save = useCallback(async () => {
    const v = view.current
    const rel = pathRef.current
    if (!v || !rel || !bufferFor(rel)) return
    const written = v.state.doc.toString()
    try {
      const res = await api.writeFile(rel, written)
      /* Typing carries on during the write. The file now holds `written`, so
       * that is what the buffer is measured against, and anything added since
       * is still unsaved. */
      const live =
        view.current === v && pathRef.current === rel ? v.state.doc.toString() : written
      const next: SaveStatus = {
        dirty: live !== written,
        savedAt: Date.now(),
        savedBytes: res.bytes,
        changedOnDisk: false,
      }
      updateBuffer(rel, { saved: written, diskText: null, status: next })
      if (pathRef.current === rel) setStatus(next)
      reportDirty.current(rel, next.dirty)
      setError(null)
      reportSaved.current(rel)
    } catch (e) {
      setError(String(e))
    }
  }, [])

  /** Overleaf's arrow: put the line the cursor is on up on the printed page.
   *  Read at the moment of the gesture, never captured — the editor outlives
   *  every file it shows. */
  const showCursorInPdf = useCallback(() => {
    const v = view.current
    const rel = pathRef.current
    if (!v || !rel || !showInPdf.current) return false
    showInPdf.current(rel, v.state.doc.lineAt(v.state.selection.main.head).number)
    return true
  }, [])

  /** Step the font size, or reset it when `direction` is 0. */
  const bumpSize = useCallback((direction: number) => {
    setFontSize((current) => {
      const next = direction === 0 ? DEFAULT_SIZE : current + direction
      return Math.min(MAX_SIZE, Math.max(MIN_SIZE, next))
    })
    return true
  }, [])

  /** Show the bubble only for a real block of prose, not a stray caret. */
  const refreshBubble = useCallback((v: EditorView) => {
    const { from, to } = v.state.selection.main
    const rel = pathRef.current
    if (!rel || to - from < 3) {
      setBubble(null)
      setAsking(false)
      return
    }
    // Either end of the selection can be scrolled out of the rendered range.
    // One end is enough to place the panel; if neither is drawn, the passage
    // is not on screen and neither should the panel be.
    const start = v.coordsAtPos(from) ?? v.coordsAtPos(to)
    const end = v.coordsAtPos(to) ?? start
    const box = v.dom.parentElement?.getBoundingClientRect()
    if (!start || !end || !box) {
      setBubble(null)
      return
    }
    const lines = v.state.doc.lineAt(to).number - v.state.doc.lineAt(from).number + 1
    const text = v.state.sliceDoc(from, to)
    setBubble({
      below: end.bottom - box.top + 6,
      above: start.top - box.top - 6,
      left: Math.max(8, Math.min(start.left - box.left, box.width - PANEL_WIDTH - 8)),
      boxHeight: box.height,
      size: describeSelection(text, lines),
      selection: { path: rel, start: from, end: to, text },
    })
  }, [])

  /** Escape puts the bubble away. It declines the key when there is nothing to
   *  dismiss, so the search panel keeps its own Escape. */
  const dismissBubble = useCallback(() => {
    if (!bubbleRef.current) return false
    setBubble(null)
    setAsking(false)
    return true
  }, [])

  const makeState = useCallback(
    (doc: string, canEdit: boolean) =>
      EditorState.create({
        doc,
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
          // The file decides the language. Read from the ref rather than taken
          // as an argument for the same reason the font size is: a state is
          // kept and restored later, so what it was built with is only ever a
          // starting value — `showState` sets both again on the way in.
          language.current.of(languageFor(pathRef.current) ?? []),
          syntaxHighlighting(syntaxColours),
          galleyTheme,
          EditorView.lineWrapping,
          // \cite{, \ref{ and the macros this paper defines for itself,
          // read from the project rather than from a fixed word list.
          latexCompletion(),
          keymap.of([
            { key: 'Escape', run: dismissBubble },
            // Ctrl/Cmd +, - and 0, the way every editor does it. The
            // browser would otherwise zoom the whole page, which moves the
            // PDF and the file tree too; preventDefault keeps it here.
            { key: 'Mod-=', preventDefault: true, run: () => bumpSize(+1) },
            { key: 'Mod-Shift-=', preventDefault: true, run: () => bumpSize(+1) },
            { key: 'Mod--', preventDefault: true, run: () => bumpSize(-1) },
            { key: 'Mod-0', preventDefault: true, run: () => bumpSize(0) },
            { key: 'Mod-Alt-j', preventDefault: true, run: () => showCursorInPdf() },
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
          editable.current.of(EditorView.editable.of(canEdit)),
          sizing.current.of(sizeTheme(fontSizeRef.current)),
          flashField,
          EditorView.updateListener.of((update) => {
            if (update.docChanged) {
              const rel = pathRef.current
              const buffer = rel ? bufferFor(rel) : undefined
              if (rel && buffer) {
                const dirty = update.state.doc.toString() !== buffer.saved
                if (dirty !== buffer.status.dirty) {
                  const next = { ...buffer.status, dirty }
                  updateBuffer(rel, { status: next })
                  setStatus(next)
                  reportDirty.current(rel, dirty)
                }
              }
            }
            if (update.selectionSet || update.docChanged) refreshBubble(update.view)
          }),
        ],
      }),
    [bumpSize, dismissBubble, refreshBubble, save],
  )

  /** Put a state on screen. The font size and the language both live in the
   *  state's own compartments, so a state built at another size, or for
   *  another file, would drag that size and that language back with it — and a
   *  stale language is silent, because the file looks right and only the
   *  colours are lying. Both are set from the here and now; the scroll
   *  position is restored the same way. */
  const showState = useCallback(
    (v: EditorView, state: EditorState, scroll: ScrollSnapshot | null) => {
      v.setState(state)
      const settings = [
        sizing.current.reconfigure(sizeTheme(fontSizeRef.current)),
        language.current.reconfigure(languageFor(pathRef.current) ?? []),
      ]
      v.dispatch({ effects: scroll ? [...settings, scroll] : settings })
    },
    [],
  )

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

  /* The rail's unsaved marks are the parent's state; the buffers are ours.
   * Leaving the Editor tab unmounts this component, so on the way back it says
   * again which files have work in them. */
  useEffect(() => {
    for (const unsaved of unsavedPaths()) reportDirty.current(unsaved, true)
  }, [])

  // -- build the editor once the host element exists --------------------
  useEffect(() => {
    if (!host) return
    const v = new EditorView({ parent: host, state: makeState('', false) })
    view.current = v
    return () => {
      // The buffer is only worth keeping if it is current, and this is the
      // last moment the view exists: leaving the Editor tab lands here.
      const rel = pathRef.current
      if (rel && bufferFor(rel)) updateBuffer(rel, { state: v.state, scroll: v.scrollSnapshot() })
      v.destroy()
      view.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [host])

  /** Bring the buffer and the file on disk back into agreement.
   *
   * Claude's merges are written into the paper while you are editing it, so
   * the two can genuinely differ. Nothing typed is thrown away here: a clean
   * buffer takes the newer text, an unsaved one keeps yours and the bar says
   * the file moved underneath it. */
  const reconcile = useCallback(
    (v: EditorView, rel: string, body: FileBody) => {
      const disk = body.content ?? ''
      const remembered = bufferFor(rel)
      if (!remembered) {
        const state = makeState(disk, body.content !== null)
        keepBuffer(rel, {
          state,
          saved: disk,
          diskText: null,
          scroll: null,
          status: CLEAN,
          meta: body,
          touched: Date.now(),
        })
        showState(v, state, null)
        setFile(body)
        setStatus(CLEAN)
        reportDirty.current(rel, false)
        return
      }
      setFile(body)
      if (disk === remembered.saved) {
        updateBuffer(rel, { meta: body })
        setStatus(remembered.status)
        return
      }
      if (!remembered.status.dirty) {
        // The baseline moves before the text does, so the update listener sees
        // a file that agrees with disk rather than a moment of false dirt.
        const status = { ...remembered.status, dirty: false, changedOnDisk: false }
        updateBuffer(rel, { saved: disk, diskText: null, status, meta: body })
        // Replacing the whole document would otherwise leave the cursor at the
        // end of it; the same offset is the best guess at where you were.
        const head = Math.min(v.state.selection.main.head, disk.length)
        v.dispatch({
          changes: { from: 0, to: v.state.doc.length, insert: disk },
          selection: { anchor: head },
          effects: editable.current.reconfigure(EditorView.editable.of(body.content !== null)),
          // Undo should not reach back into a version of the file that is
          // gone, and there is nothing of yours in it to reach back for.
          annotations: Transaction.addToHistory.of(false),
        })
        updateBuffer(rel, { state: v.state })
        setStatus(status)
        reportDirty.current(rel, false)
        return
      }
      const status = { ...remembered.status, changedOnDisk: true }
      updateBuffer(rel, { diskText: disk, status, meta: body })
      setStatus(status)
    },
    [makeState, showState],
  )

  // -- load whatever file is open --------------------------------------
  useEffect(() => {
    const v = view.current
    if (!host || !v) return
    setBubble(null)
    setAsking(false)
    setError(null)
    if (!path) {
      setFile(null)
      setStatus(CLEAN)
      return
    }
    const remembered = bufferFor(path)
    if (remembered) {
      // Exactly what you left here: text, cursor, undo history, scroll. When
      // only the reload key bumped, the view already holds this very state and
      // rebuilding it would be a flicker for nothing.
      if (v.state !== remembered.state) showState(v, remembered.state, remembered.scroll)
      setFile(remembered.meta)
      setStatus(remembered.status)
    } else {
      // Nothing to show yet, and nowhere for a keystroke to go: an empty
      // read-only state means the file you just left cannot be typed into.
      showState(v, makeState('', false), null)
      setFile(null)
      setStatus(CLEAN)
    }
    let stale = false
    api
      .file(path)
      .then((body) => {
        if (!stale && view.current === v) reconcile(v, path, body)
      })
      .catch((e) => !stale && setError(String(e)))
    return () => {
      stale = true
      // On the way out, the buffer takes over from the view.
      if (view.current === v && bufferFor(path)) {
        updateBuffer(path, { state: v.state, scroll: v.scrollSnapshot() })
      }
    }
  }, [path, reloadKey, host, makeState, reconcile, showState])

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

  /** Take the file on disk, losing the unsaved buffer. Deliberately undoable:
   *  the replacement goes into the history, so Ctrl+Z brings yours back. */
  function takeDisk() {
    const v = view.current
    const rel = pathRef.current
    const buffer = rel ? bufferFor(rel) : undefined
    if (!v || !rel || !buffer || buffer.diskText === null) return
    const ok = window.confirm(
      `${rel} changed on disk. Replace your unsaved changes with the file on disk?\n\nCtrl+Z brings yours back.`,
    )
    if (!ok) return
    const next = { ...buffer.status, dirty: false, changedOnDisk: false }
    updateBuffer(rel, { saved: buffer.diskText, diskText: null, status: next })
    v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: buffer.diskText } })
    updateBuffer(rel, { state: v.state })
    setStatus(next)
    reportDirty.current(rel, false)
  }

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

  /** Back to the editor, where Escape reaches the bubble again. */
  function stopAsking() {
    setAsking(false)
    view.current?.focus()
  }

  const binary = file !== null && file.content === null
  /* A file with no mode is shown as it is rather than coloured as something it
   * is not, and the bar says so — otherwise the only difference between "plain
   * text" and "highlighted" is that nothing happened to be highlighted. */
  const plainText = file !== null && !binary && languageFor(path) === null
  const bubbleAt = bubble && place(bubble, BUBBLE_ROOM)
  const popoverAt = bubble && place(bubble, POPOVER_ROOM)

  return (
    <div className="editor-wrap">
      <div className={`editor-bar${status.dirty ? ' unsaved' : ''}`}>
        <span className="path mono" title={path ?? undefined}>
          {path ?? 'no file open'}
        </span>
        {status.dirty && <span className="badge amber">unsaved</span>}
        {status.savedAt !== null && (
          <span
            className="muted small"
            title={
              status.savedBytes === null
                ? undefined
                : `${status.savedBytes.toLocaleString()} bytes written`
            }
          >
            {status.dirty ? 'last saved' : 'saved'} {clock(status.savedAt)}
          </span>
        )}
        {status.changedOnDisk && (
          <>
            <span className="badge amber on-disk">changed on disk</span>
            <button
              className="tiny"
              title="Replace your unsaved changes with the file on disk; Ctrl+Z brings yours back"
              onClick={takeDisk}
            >
              Reload
            </button>
          </>
        )}
        {binary && (
          <span className="muted small">
            read-only · {file.type === 'image' ? 'image' : 'not text'}
          </span>
        )}
        {plainText && <span className="muted small">plain text</span>}
        <span className="grow" />
        {unsavedElsewhere.length > 0 && (
          <span className="muted small" title={unsavedElsewhere.join('\n')}>
            {unsavedElsewhere.length} more unsaved
          </span>
        )}
        <span className="muted small hint">select a passage to ask Claude</span>
        {onShowInPdf && !binary && (
          <button
            className="tiny"
            onClick={showCursorInPdf}
            disabled={!path}
            title="Show this line in the PDF (Ctrl/Cmd+Alt+J)"
          >
            Show in PDF ›
          </button>
        )}
        <div className="seg" title="Editor font size — Ctrl/Cmd with +, − or 0">
          <button onClick={() => bumpSize(-1)} disabled={fontSize <= MIN_SIZE}>
            −
          </button>
          <button onClick={() => bumpSize(0)}>{fontSize}px</button>
          <button onClick={() => bumpSize(1)} disabled={fontSize >= MAX_SIZE}>
            +
          </button>
        </div>
        <button
          className="tiny primary"
          onClick={() => void save()}
          disabled={!status.dirty || binary}
        >
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

        {bubble && bubbleAt && !asking && (
          <button
            className={`ask-bubble${bubbleAt.above ? ' above' : ''}`}
            style={{ top: bubbleAt.top, left: bubble.left }}
            onKeyDown={(e) => {
              if (e.key === 'Escape') dismissBubble()
            }}
            onClick={() => setAsking(true)}
          >
            <span className="spark">✦</span> Ask Claude
            <span className="size">· {bubble.size}</span>
          </button>
        )}

        {bubble && popoverAt && asking && (
          <div
            className={`ask-popover${popoverAt.above ? ' above' : ''}`}
            style={{ top: popoverAt.top, left: bubble.left, width: PANEL_WIDTH }}
          >
            <span className="muted small">{bubble.size} selected</span>
            <div className="quoted">{bubble.selection.text.slice(0, 220)}</div>
            <textarea
              autoFocus
              rows={3}
              value={instruction}
              placeholder="What should Claude do with this passage?"
              onChange={(e) => setInstruction(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') {
                  // The editor's own Escape would put the whole bubble away;
                  // from in here it should only close the form.
                  e.stopPropagation()
                  stopAsking()
                }
                if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void ask()
              }}
            />
            <div className="row">
              <span className="muted small">Claude drafts on its own branch.</span>
              <span className="grow" />
              <button className="tiny" onClick={stopAsking}>
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
