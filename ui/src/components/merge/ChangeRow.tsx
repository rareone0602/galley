import { useState } from 'react'
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

export function ChangeRow({
  op,
  view,
  index,
  current,
  answer,
  editing,
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

  // Editing takes the whole row: the passage is one sentence, and you are
  // writing prose, not filling in a field.
  if (editing) {
    const text = answer?.kind === 'rewrite' ? answer.text : op.old
    return (
      <div className={`${classes} editing`} data-change-id={op.id}>
        <div className="editing-cell">
          <div className="row small muted">
            <span>Your wording for change {index}</span>
            <span className="grow" />
            <button className="tiny" onClick={() => onType(op.old)}>
              Start from yours
            </button>
            <button className="tiny" onClick={() => onType(op.new)}>
              Start from Claude's
            </button>
          </div>
          <textarea
            autoFocus
            value={text}
            rows={Math.min(10, text.split('\n').length + 1)}
            onChange={(e) => onType(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') onDone()
            }}
          />
          <div className="row">
            <span className="muted small">
              This text is written verbatim. Keep the trailing newline if it had one.
            </span>
            <span className="grow" />
            <button className="tiny" onClick={onDiscard}>
              Discard
            </button>
            <button className="tiny primary" onClick={onDone}>
              Done
            </button>
          </div>
        </div>
      </div>
    )
  }

  if (answer?.kind === 'rewrite')
    return (
      <div className={classes} data-change-id={op.id}>
        <div className="cell rewritten" onDoubleClick={() => onRewrite(seed)}>
          <span className="idx">{index}</span>
          <span className="tag">yours, rewritten</span>
          {answer.text.replace(/\n+$/, '') || <span className="nothing">deleted</span>}
        </div>
        <div className="gutter">{controls}</div>
        <div className="cell muted-side">
          <span className="tag">Claude proposed</span>
          {op.new.replace(/\n+$/, '') || <span className="nothing">deleted</span>}
        </div>
      </div>
    )

  if (view === 'inline')
    return (
      <div className={`${classes} inline`} data-change-id={op.id}>
        <div className="cell stacked">
          {op.old.trim() && (
            <div className="line old" onDoubleClick={() => onRewrite(op.old)}>
              <span className="marker">−</span>
              <Words spans={op.old_words} kind="del" fallback={op.old} />
            </div>
          )}
          {op.new.trim() && (
            <div className="line new" onDoubleClick={() => onRewrite(op.new)}>
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
      <div className="cell old" onDoubleClick={() => onRewrite(op.old)}>
        <span className="idx">{index}</span>
        {op.old.trim() ? (
          <Words spans={op.old_words} kind="del" fallback={op.old} />
        ) : (
          <span className="nothing">nothing here</span>
        )}
      </div>
      <div className="gutter">{controls}</div>
      <div className="cell new" onDoubleClick={() => onRewrite(op.new)}>
        {op.new.trim() ? (
          <Words spans={op.new_words} kind="ins" fallback={op.new} />
        ) : (
          <span className="nothing">deleted</span>
        )}
      </div>
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
