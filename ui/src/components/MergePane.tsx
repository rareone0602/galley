import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, applyOps, type DiffOp, type FileDiff, type WordSpan } from '../api'

type View = 'split' | 'inline'
/** Per file, per change: the wording you typed instead of either side's. */
type Edits = Record<string, Record<number, string>>

/**
 * Review Claude's draft the way Diffchecker shows a comparison: your text on
 * the left, Claude's on the right, changed passages tinted, the exact words
 * that moved picked out inside them.
 *
 * The merge is the middle column, and it has three answers, not two: keep
 * yours, take Claude's, or **write a third thing**. Double-click either side to
 * edit it. Yours-rewritten wins over both, which is the point — Claude's draft
 * is a suggestion, and the sentence that lands is the one you decided on.
 *
 * Nothing here applies a patch. The backend hands over ops covering the whole
 * file, the result is those ops with your choices substituted in, and Save
 * writes that entire buffer. There is no patch offset to get wrong.
 */
export default function MergePane({
  sessionId,
  onSaved,
}: {
  sessionId: string
  onSaved: (path: string) => void
}) {
  const [files, setFiles] = useState<FileDiff[]>([])
  const [active, setActive] = useState(0)
  const [accepted, setAccepted] = useState<Record<string, Set<number>>>({})
  const [edits, setEdits] = useState<Edits>({})
  const [editing, setEditing] = useState<number | null>(null)
  const [saved, setSaved] = useState<Record<string, string>>({})
  const [view, setView] = useState<View>('split')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [cursor, setCursor] = useState(0)
  const rows = useRef<Map<number, HTMLDivElement>>(new Map())

  const reload = useCallback(async () => {
    setBusy(true)
    try {
      const body = await api.diff(sessionId)
      setFiles(body.files)
      setAccepted(Object.fromEntries(body.files.map((f) => [f.path, new Set<number>()])))
      setEdits({})
      setEditing(null)
      setActive(0)
      setCursor(0)
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }, [sessionId])

  useEffect(() => {
    void reload()
  }, [reload])

  const file = files[active] ?? null
  const changes = useMemo(
    () => (file ? file.ops.filter((o) => o.type === 'change') : []),
    [file],
  )
  const chosen = file ? accepted[file.path] ?? new Set<number>() : new Set<number>()
  const mine = file ? edits[file.path] ?? {} : {}

  function toggle(id: number) {
    if (!file) return
    setAccepted((prev) => {
      const next = new Set(prev[file.path] ?? [])
      next.has(id) ? next.delete(id) : next.add(id)
      return { ...prev, [file.path]: next }
    })
  }

  function setAll(on: boolean) {
    if (!file) return
    setAccepted((prev) => ({
      ...prev,
      [file.path]: on ? new Set(changes.map((o) => o.id)) : new Set(),
    }))
  }

  function writeEdit(id: number, text: string | null) {
    if (!file) return
    setEdits((prev) => {
      const forFile = { ...(prev[file.path] ?? {}) }
      if (text === null) delete forFile[id]
      else forFile[id] = text
      return { ...prev, [file.path]: forFile }
    })
  }

  function jump(delta: number) {
    if (!changes.length) return
    const next = (cursor + delta + changes.length) % changes.length
    setCursor(next)
    rows.current.get(changes[next].id)?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }

  async function save() {
    if (!file) return
    setBusy(true)
    try {
      const res = await api.writeFile(file.path, applyOps(file.ops, chosen, mine))
      setSaved((p) => ({ ...p, [file.path]: `Wrote ${res.bytes.toLocaleString()} bytes` }))
      setError(null)
      onSaved(file.path)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  if (error) return <div className="notice bad">{error}</div>
  if (!file)
    return (
      <div className="empty">
        {busy ? 'Reading the diff…' : 'Claude has not changed anything on this branch yet.'}
      </div>
    )

  const editedCount = Object.keys(mine).length

  return (
    <div className="merge">
      <div className="merge-bar">
        <div className="filepicker">
          {files.map((f, i) => (
            <button
              key={f.path}
              className={i === active ? 'on' : ''}
              onClick={() => {
                setActive(i)
                setCursor(0)
                setEditing(null)
              }}
              title={f.path}
            >
              {f.path.split('/').pop()}
              <span className="count">{f.changes}</span>
            </button>
          ))}
        </div>
        <span className="grow" />
        <div className="seg">
          <button className={view === 'split' ? 'on' : ''} onClick={() => setView('split')}>
            Side by side
          </button>
          <button className={view === 'inline' ? 'on' : ''} onClick={() => setView('inline')}>
            Inline
          </button>
        </div>
        <button className="tiny" onClick={() => void reload()} disabled={busy}>
          Reload
        </button>
      </div>

      <div className="merge-bar second">
        <button className="tiny" onClick={() => jump(-1)} disabled={!changes.length}>
          ↑
        </button>
        <button className="tiny" onClick={() => jump(1)} disabled={!changes.length}>
          ↓
        </button>
        <span className="muted small">
          {changes.length
            ? `Change ${cursor + 1} of ${changes.length} · ${chosen.size} accepted` +
              (editedCount ? ` · ${editedCount} rewritten` : '')
            : 'no changes in this file'}
        </span>
        <span className="grow" />
        <button className="tiny" onClick={() => setAll(true)}>
          Accept all
        </button>
        <button className="tiny" onClick={() => setAll(false)}>
          Reject all
        </button>
        <button className="tiny primary" onClick={() => void save()} disabled={busy}>
          Save to {file.path.split('/').pop()}
        </button>
      </div>

      {saved[file.path] && <div className="notice good merge-saved">{saved[file.path]}</div>}

      <div className={`diff ${view}`}>
        {view === 'split' && (
          <div className="diff-head">
            <div className="col-head yours">Yours — {file.path}</div>
            <div className="col-head gutter" />
            <div className="col-head theirs">Claude's proposal</div>
          </div>
        )}
        <div className="diff-body">
          {file.ops.map((op) =>
            op.type === 'equal' ? (
              <Equal key={op.id} text={op.new} view={view} />
            ) : (
              <Change
                key={op.id}
                op={op}
                view={view}
                accepted={chosen.has(op.id)}
                edited={mine[op.id]}
                editing={editing === op.id}
                index={changes.findIndex((c) => c.id === op.id) + 1}
                onToggle={() => toggle(op.id)}
                onEdit={(text) => writeEdit(op.id, text)}
                onEditing={(on, seed) => {
                  setEditing(on ? op.id : null)
                  if (on && mine[op.id] === undefined && seed !== undefined) writeEdit(op.id, seed)
                }}
                bind={(el) => {
                  el ? rows.current.set(op.id, el) : rows.current.delete(op.id)
                }}
              />
            ),
          )}
        </div>
      </div>
    </div>
  )
}

/** Text both of you agree on. Long runs fold away, like Diffchecker's context. */
function Equal({ text, view }: { text: string; view: View }) {
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

function Change({
  op,
  view,
  accepted,
  edited,
  editing,
  index,
  onToggle,
  onEdit,
  onEditing,
  bind,
}: {
  op: DiffOp
  view: View
  accepted: boolean
  edited: string | undefined
  editing: boolean
  index: number
  onToggle: () => void
  onEdit: (text: string | null) => void
  onEditing: (on: boolean, seed?: string) => void
  bind: (el: HTMLDivElement | null) => void
}) {
  const isEdited = edited !== undefined
  const state = isEdited ? ' rewritten' : accepted ? ' accepted' : ''

  const controls = (
    <>
      <button
        className={`take${accepted && !isEdited ? ' on' : ''}`}
        onClick={onToggle}
        disabled={isEdited}
        title={
          isEdited
            ? 'You rewrote this one; revert it to choose a side again'
            : accepted
              ? 'Accepted — click to keep your wording instead'
              : "Take Claude's wording for this passage"
        }
      >
        {accepted ? '✓' : '→'}
      </button>
      <button
        className={`take pen${isEdited ? ' on' : ''}`}
        onClick={() =>
          isEdited && !editing
            ? onEditing(true)
            : onEditing(!editing, accepted ? op.new : op.old)
        }
        title="Write your own wording for this passage"
      >
        ✎
      </button>
    </>
  )

  // Editing takes the whole row: the passage is one sentence, and you are
  // writing prose, not filling in a field.
  if (editing)
    return (
      <div className={`drow change editing${state}`} ref={bind}>
        <div className="editing-cell">
          <div className="row small muted">
            <span>Your wording for change {index}</span>
            <span className="grow" />
            <button className="tiny" onClick={() => onEdit(op.old)}>
              Start from yours
            </button>
            <button className="tiny" onClick={() => onEdit(op.new)}>
              Start from Claude's
            </button>
          </div>
          <textarea
            autoFocus
            value={edited ?? op.old}
            rows={Math.min(10, (edited ?? op.old).split('\n').length + 1)}
            onChange={(e) => onEdit(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') onEditing(false)
            }}
          />
          <div className="row">
            <span className="muted small">
              This text is written verbatim. Keep the trailing newline if it had one.
            </span>
            <span className="grow" />
            <button
              className="tiny"
              onClick={() => {
                onEdit(null)
                onEditing(false)
              }}
            >
              Discard
            </button>
            <button className="tiny primary" onClick={() => onEditing(false)}>
              Done
            </button>
          </div>
        </div>
      </div>
    )

  if (isEdited)
    return (
      <div className={`drow change${state}`} ref={bind}>
        <div className="cell rewritten" onDoubleClick={() => onEditing(true)}>
          <span className="idx">{index}</span>
          <span className="tag">yours, rewritten</span>
          {edited.replace(/\n+$/, '') || <span className="nothing">deleted</span>}
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
      <div className={`drow change inline${state}`} ref={bind}>
        <div className="cell stacked">
          {op.old.trim() && (
            <div className="line old" onDoubleClick={() => onEditing(true, op.old)}>
              <span className="marker">−</span>
              <Words spans={op.old_words} kind="del" fallback={op.old} />
            </div>
          )}
          {op.new.trim() && (
            <div className="line new" onDoubleClick={() => onEditing(true, op.new)}>
              <span className="marker">+</span>
              <Words spans={op.new_words} kind="ins" fallback={op.new} />
            </div>
          )}
          <div className="controls">
            {controls}
            <span className="muted small">
              {accepted ? "Claude's wording will be written" : 'your wording will be kept'}
            </span>
          </div>
        </div>
      </div>
    )

  return (
    <div className={`drow change${state}`} ref={bind}>
      <div className="cell old" onDoubleClick={() => onEditing(true, op.old)}>
        <span className="idx">{index}</span>
        {op.old.trim() ? (
          <Words spans={op.old_words} kind="del" fallback={op.old} />
        ) : (
          <span className="nothing">nothing here</span>
        )}
      </div>
      <div className="gutter">{controls}</div>
      <div className="cell new" onDoubleClick={() => onEditing(true, op.new)}>
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
