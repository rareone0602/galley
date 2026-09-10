import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { DiffOp, WordSpan } from '../../api'
import type { Answer } from './decisions'
import { chordFor } from './Shortcuts'

/** Two columns like Diffchecker, or one stacked column for a narrow pane. */
export type View = 'split' | 'inline'

/** Text both of you agree on. Long runs fold away, like Diffchecker's context. */
export function EqualRow({ text, view }: { text: string; view: View }) {
  const [open, setOpen] = useState(false)
  const lines = text.replace(/\n+$/, '').split('\n')
  const long = lines.length > 6

  const body =
    !long || open ? (
      lines.join('\n')
    ) : (
      <>
        {lines.slice(0, 2).join('\n')}
        {'\n'}
        <span className="fold">⋯ {lines.length - 4} unchanged lines ⋯</span>
        {'\n'}
        {lines.slice(-2).join('\n')}
      </>
    )

  const cell = (
    <div className="cell equal" onClick={() => long && setOpen(!open)}>
      {body}
    </div>
  )
  if (view === 'inline') return <div className="drow equal">{cell}</div>
  return (
    <div className="drow equal">
      {cell}
      <div className="gutter" />
      <div className="cell equal" onClick={() => long && setOpen(!open)}>
        {body}
      </div>
    </div>
  )
}

/** Which of the four states a change is in, as a class and as a word. */
export function stateOf(answer: Answer | undefined): 'open' | 'taken' | 'kept' | 'rewritten' {
  if (!answer) return 'open'
  if (answer.kind === 'claude') return 'taken'
  if (answer.kind === 'keep') return 'kept'
  return 'rewritten'
}

/**
 * Where in the text a click landed, counted in characters from the start of
 * `holder`.
 *
 * Without this, clicking into your sentence to change one word would open the
 * box with the caret at the end, and you would have to find your place again.
 * The offset is measured inside `holder` alone, so the change number and the
 * little label beside it are not counted. Null when the browser will not say
 * (or the click missed the text), and the caller then falls back to the end.
 */
function caretOffsetIn(holder: HTMLElement | null, clientX: number, clientY: number): number | null {
  if (!holder) return null
  const doc = document as Document & {
    caretRangeFromPoint?: (x: number, y: number) => Range | null
  }
  const hit = doc.caretRangeFromPoint?.(clientX, clientY)
  if (!hit || !holder.contains(hit.startContainer)) return null
  const upTo = document.createRange()
  upTo.selectNodeContents(holder)
  try {
    upTo.setEnd(hit.startContainer, hit.startOffset)
  } catch {
    return null
  }
  return upTo.toString().length
}

/** A click that ends a drag-selection is not a request to start typing. */
function isSelecting(): boolean {
  const selection = window.getSelection()
  return !!selection && !selection.isCollapsed
}

export function ChangeRow({
  op,
  view,
  index,
  current,
  answer,
  editing,
  draft,
  onAnswer,
  onRewrite,
  onType,
  onDone,
  onDiscard,
}: {
  op: DiffOp
  view: View
  /** 1-based position among this file's changes, as the header counts them. */
  index: number
  /** The one the keyboard is on. */
  current: boolean
  answer: Answer | undefined
  editing: boolean
  /** What is in the open box. Only meaningful while `editing`, and held by the
   *  pane rather than by the answer, so that opening a box is not an answer. */
  draft: string
  onAnswer: (answer: Answer) => void
  /** Start writing your own, seeded from the side you were reading. */
  onRewrite: (seed: string) => void
  /** Every keystroke inside the box. Not a decision, so not an undo step. */
  onType: (text: string) => void
  onDone: () => void
  onDiscard: () => void
}) {
  const state = stateOf(answer)
  const classes = `drow change ${state}${current ? ' current' : ''}`
  const seed = state === 'taken' ? op.new : op.old
  const text = editing ? draft : answer?.kind === 'rewrite' ? answer.text : op.old

  /* Where to put the caret when the box appears: set by the click that opened
   * it, read once. Null when the keyboard opened it, where there is no point
   * on screen to aim at and the end of the text is the right answer. */
  const caret = useRef<number | null>(null)
  const box = useRef<HTMLTextAreaElement | null>(null)
  const mine = useRef<HTMLSpanElement | null>(null)

  function startFrom(text: string, event: React.MouseEvent, holder: HTMLElement | null) {
    if (isSelecting()) return
    caret.current = caretOffsetIn(holder, event.clientX, event.clientY)
    onRewrite(text)
  }

  /* Focus and caret before the browser paints, so the box never shows up with
   * the caret in the wrong place first. */
  useLayoutEffect(() => {
    const el = box.current
    if (!editing || !el) return
    el.focus()
    const at = Math.min(caret.current ?? el.value.length, el.value.length)
    el.setSelectionRange(at, at)
    caret.current = null
  }, [editing])

  /* The box is as tall as what is in it. This row is one half of a comparison,
   * and a scrollbar inside one side hides the very words being compared. */
  useEffect(() => {
    const el = box.current
    if (!editing || !el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
  }, [editing, text])

  const controls = (
    <>
      <button
        className={`take${state === 'taken' ? ' on' : ''}`}
        onClick={() => onAnswer({ kind: 'claude' })}
        title={`Take Claude's wording (${chordFor('claude')})`}
      >
        ✓
      </button>
      <button
        className={`take keep${state === 'kept' ? ' on' : ''}`}
        onClick={() => onAnswer({ kind: 'keep' })}
        title={`Keep your wording (${chordFor('keep')})`}
      >
        ✗
      </button>
      <button
        className={`take pen${state === 'rewritten' ? ' on' : ''}`}
        onClick={() => onRewrite(seed)}
        title={`Write your own wording (${chordFor('rewrite')})`}
      >
        ✎
      </button>
    </>
  )

  /* Typing happens in the left cell, in place. The whole point of the pane is
   * that Claude's sentence sits beside yours; taking the comparison away at
   * the moment you are moving one towards the other is the one thing it must
   * not do. So the row keeps its shape and only your side becomes a box. */
  const writing = (
    <div className="cell old writing">
      {view === 'split' && <span className="idx">{index}</span>}
      <span className="tag">yours — editing</span>
      <textarea
        ref={box}
        value={text}
        spellCheck
        onChange={(e) => onType(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            e.stopPropagation()
            onDone()
          }
        }}
      />
      <div className="writing-actions">
        <button
          className="tiny"
          onClick={() => onType(op.new)}
          title="Replace the box with Claude's sentence, then edit that"
        >
          Start from Claude's
        </button>
        <button
          className="tiny"
          onClick={() => onType(op.old)}
          title="Replace the box with your original sentence"
        >
          Start from yours
        </button>
        <span className="grow" />
        <button className="tiny" onClick={onDiscard}>
          Discard
        </button>
        <button className="tiny primary" onClick={onDone}>
          Done
        </button>
      </div>
    </div>
  )

  /** Claude's side. Clicking it starts a rewrite from his sentence — the way
   *  you take his wording and then change one word of it. */
  const theirs = (label: string) => (
    <div className="cell new" onClick={(e) => startFrom(op.new, e, null)}>
      {label && <span className="tag">{label}</span>}
      {op.new.trim() ? (
        <Words spans={op.new_words} kind="ins" fallback={op.new} />
      ) : (
        <span className="nothing">deleted</span>
      )}
    </div>
  )

  if (editing) {
    if (view === 'inline')
      return (
        <div className={`${classes} inline editing`} data-change-id={op.id}>
          <div className="cell stacked">
            {writing}
            {op.new.trim() && (
              <div className="line new" onClick={(e) => startFrom(op.new, e, null)}>
                <span className="marker">+</span>
                <Words spans={op.new_words} kind="ins" fallback={op.new} />
              </div>
            )}
          </div>
        </div>
      )
    return (
      <div className={`${classes} editing`} data-change-id={op.id}>
        {writing}
        <div className="gutter">{controls}</div>
        {theirs('Claude proposed')}
      </div>
    )
  }

  if (answer?.kind === 'rewrite')
    return (
      <div className={classes} data-change-id={op.id}>
        <div className="cell rewritten" onClick={(e) => startFrom(answer.text, e, mine.current)}>
          <span className="idx">{index}</span>
          <span className="tag">yours, rewritten — click to carry on</span>
          <span className="txt" ref={mine}>
            {answer.text.replace(/\n+$/, '') || <span className="nothing">deleted</span>}
          </span>
        </div>
        <div className="gutter">{controls}</div>
        {theirs('Claude proposed')}
      </div>
    )

  if (view === 'inline')
    return (
      <div className={`${classes} inline`} data-change-id={op.id}>
        <div className="cell stacked">
          {op.old.trim() && (
            <div className="line old" onClick={(e) => startFrom(op.old, e, mine.current)}>
              <span className="marker">−</span>
              <span className="txt" ref={mine}>
                <Words spans={op.old_words} kind="del" fallback={op.old} />
              </span>
            </div>
          )}
          {op.new.trim() && (
            <div className="line new" onClick={(e) => startFrom(op.new, e, null)}>
              <span className="marker">+</span>
              <Words spans={op.new_words} kind="ins" fallback={op.new} />
            </div>
          )}
          <div className="controls">
            {controls}
            <span className="muted small">
              {state === 'taken'
                ? "Claude's wording will be written"
                : state === 'kept'
                  ? 'your wording will be kept'
                  : `change ${index}, still open`}
            </span>
          </div>
        </div>
      </div>
    )

  return (
    <div className={classes} data-change-id={op.id}>
      <div className="cell old" onClick={(e) => startFrom(op.old, e, mine.current)}>
        <span className="idx">{index}</span>
        {op.old.trim() ? (
          <span className="txt" ref={mine}>
            <Words spans={op.old_words} kind="del" fallback={op.old} />
          </span>
        ) : (
          <span className="nothing">nothing here</span>
        )}
      </div>
      <div className="gutter">{controls}</div>
      {theirs('')}
    </div>
  )
}

/** Word-level emphasis inside one changed sentence. */
function Words({
  spans,
  kind,
  fallback,
}: {
  spans: WordSpan[]
  kind: 'del' | 'ins'
  fallback: string
}) {
  if (!spans.length) return <>{fallback.replace(/\n+$/, '')}</>
  return (
    <>
      {spans.map((s, i) =>
        s.op === 'same' ? (
          <span key={i}>{s.text}</span>
        ) : (
          <mark key={i} className={kind}>
            {s.text}
          </mark>
        ),
      )}
    </>
  )
}
