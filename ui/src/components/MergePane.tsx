import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, applyOps, type DiffOp, type FileDiff, type WordSpan } from '../api'

type View = 'split' | 'inline'

/**
 * Review Claude's draft the way Diffchecker shows a comparison: your text on
 * the left, Claude's on the right, changed passages tinted, the exact words
 * that moved picked out inside them.
 *
 * The merge part is the middle column. Nothing here applies a patch: the
 * backend hands over ops covering the whole file, the result is those ops with
 * your choices substituted in, and Save writes that entire buffer. There is no
 * patch offset to get wrong and no half-applied hunk to go stale.
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
      const res = await api.writeFile(file.path, applyOps(file.ops, chosen))
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
            ? `Change ${cursor + 1} of ${changes.length} · ${chosen.size} accepted`
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
                index={changes.findIndex((c) => c.id === op.id) + 1}
                onToggle={() => toggle(op.id)}
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
  index,
  onToggle,
  bind,
}: {
  op: DiffOp
  view: View
  accepted: boolean
  index: number
  onToggle: () => void
  bind: (el: HTMLDivElement | null) => void
}) {
  const control = (
    <button
      className={`take${accepted ? ' on' : ''}`}
      onClick={onToggle}
      title={
        accepted
          ? 'Accepted — click to keep your wording instead'
          : "Take Claude's wording for this passage"
      }
    >
      {accepted ? '✓' : '→'}
    </button>
  )

  if (view === 'inline')
    return (
      <div className={`drow change inline${accepted ? ' accepted' : ''}`} ref={bind}>
        <div className="cell stacked">
          {op.old.trim() && (
            <div className="line old">
              <span className="marker">−</span>
              <Words spans={op.old_words} kind="del" fallback={op.old} />
            </div>
          )}
          {op.new.trim() && (
            <div className="line new">
              <span className="marker">+</span>
              <Words spans={op.new_words} kind="ins" fallback={op.new} />
            </div>
          )}
          <div className="controls">
            {control}
            <span className="muted small">
              {accepted ? "Claude's wording will be written" : 'your wording will be kept'}
            </span>
          </div>
        </div>
      </div>
    )

  return (
    <div className={`drow change${accepted ? ' accepted' : ''}`} ref={bind}>
      <div className="cell old">
        <span className="idx">{index}</span>
        {op.old.trim() ? (
          <Words spans={op.old_words} kind="del" fallback={op.old} />
        ) : (
          <span className="nothing">nothing here</span>
        )}
      </div>
      <div className="gutter">{control}</div>
      <div className="cell new">
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
