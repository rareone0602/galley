import { useEffect, useMemo, useState } from 'react'
import { api, applyOps, type DiffOp, type FileDiff, type WordSpan } from '../api'

/**
 * Accept or reject Claude's changes one sentence at a time.
 *
 * Nothing here applies a patch. The backend hands over a list of ops covering
 * the whole file; the resulting buffer is just those ops with your choices
 * substituted in, and Save writes that entire buffer. There is no patch offset
 * to get wrong and no partial application to go stale.
 */
export default function MergePane({ sessionId }: { sessionId: string }) {
  const [files, setFiles] = useState<FileDiff[]>([])
  const [accepted, setAccepted] = useState<Record<string, Set<number>>>({})
  const [saved, setSaved] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function reload() {
    setBusy(true)
    try {
      const body = await api.diff(sessionId)
      setFiles(body.files)
      setAccepted(Object.fromEntries(body.files.map((f) => [f.path, new Set<number>()])))
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    void reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId])

  function toggle(path: string, id: number) {
    setAccepted((prev) => {
      const next = new Set(prev[path] ?? [])
      next.has(id) ? next.delete(id) : next.add(id)
      return { ...prev, [path]: next }
    })
  }

  function setAll(path: string, ops: DiffOp[], on: boolean) {
    setAccepted((prev) => ({
      ...prev,
      [path]: on ? new Set(ops.filter((o) => o.type === 'change').map((o) => o.id)) : new Set(),
    }))
  }

  async function save(file: FileDiff) {
    setBusy(true)
    try {
      const content = applyOps(file.ops, accepted[file.path] ?? new Set())
      const res = await api.writeFile(file.path, content)
      setSaved((p) => ({ ...p, [file.path]: `wrote ${res.bytes} bytes` }))
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  const totalChanges = useMemo(() => files.reduce((n, f) => n + f.changes, 0), [files])

  if (error) return <div className="notice bad">{error}</div>
  if (!files.length)
    return (
      <div className="empty">
        {busy ? 'Reading the diff…' : 'No changes on this branch yet.'}
      </div>
    )

  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}>
        <span className="muted small">
          {totalChanges} change{totalChanges === 1 ? '' : 's'} across {files.length} file
          {files.length === 1 ? '' : 's'}
        </span>
        <span className="grow" />
        <button className="tiny" onClick={reload} disabled={busy}>
          Reload diff
        </button>
      </div>

      {files.map((file) => {
        const chosen = accepted[file.path] ?? new Set<number>()
        const changes = file.ops.filter((o) => o.type === 'change')
        return (
          <div className="merge-file" key={file.path}>
            <header>
              <span className="path">{file.path}</span>
              <span className="muted small">
                {chosen.size}/{changes.length} accepted
              </span>
              <span className="grow" />
              <button className="tiny" onClick={() => setAll(file.path, file.ops, true)}>
                Accept all
              </button>
              <button className="tiny" onClick={() => setAll(file.path, file.ops, false)}>
                Reject all
              </button>
              <button className="tiny primary" onClick={() => save(file)} disabled={busy}>
                Save to {file.path.split('/').pop()}
              </button>
            </header>
            {saved[file.path] && <div className="notice good">{saved[file.path]}</div>}
            <div>
              {file.ops.map((op) =>
                op.type === 'equal' ? (
                  <Context key={op.id} text={op.new} />
                ) : (
                  <Change
                    key={op.id}
                    op={op}
                    accepted={chosen.has(op.id)}
                    onToggle={() => toggle(file.path, op.id)}
                  />
                ),
              )}
            </div>
          </div>
        )
      })}
    </>
  )
}

/** Unchanged text, elided in the middle when there is a lot of it. */
function Context({ text }: { text: string }) {
  const [open, setOpen] = useState(false)
  const lines = text.split('\n')
  if (lines.length <= 6 || open)
    return (
      <div className="op equal" onClick={() => setOpen(false)}>
        {text.replace(/\n+$/, '')}
      </div>
    )
  return (
    <div className="op equal" style={{ cursor: 'pointer' }} onClick={() => setOpen(true)}>
      {lines.slice(0, 2).join('\n')}
      {'\n'}
      <span className="small" style={{ fontFamily: 'var(--mono)' }}>
        ⋯ {lines.length - 4} unchanged lines ⋯
      </span>
      {'\n'}
      {lines.slice(-2).join('\n')}
    </div>
  )
}

function Change({
  op,
  accepted,
  onToggle,
}: {
  op: DiffOp
  accepted: boolean
  onToggle: () => void
}) {
  const hasOld = op.old.trim().length > 0
  const hasNew = op.new.trim().length > 0
  return (
    <div className={`op change${accepted ? ' accepted' : ''}`}>
      {hasOld && (
        <div className="side old">
          <span className="marker">−</span>
          <span>
            <Words spans={op.old_words} kind="del" fallback={op.old} />
          </span>
        </div>
      )}
      {hasNew && (
        <div className="side new">
          <span className="marker">+</span>
          <span>
            <Words spans={op.new_words} kind="ins" fallback={op.new} />
          </span>
        </div>
      )}
      <div className="controls">
        <button className="tiny" onClick={onToggle}>
          {accepted ? '↩ Reject' : '✓ Accept'}
        </button>
        <span className="muted small">
          {accepted ? "Claude's wording will be written" : 'your wording will be kept'}
        </span>
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
