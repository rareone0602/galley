import type { PlannedWrite } from './decisions'

/**
 * What Save is about to do, said before it does it.
 *
 * Galley's whole promise is that he merges by hand and is never surprised by
 * what lands on disk, so the write is two steps: this panel, then the button
 * on it. Every file named here is written whole — there is no patch — and
 * files whose answers reproduce what is already on disk are simply absent.
 */
export default function SavePlan({
  writes,
  busy,
  onCancel,
  onWrite,
}: {
  writes: PlannedWrite[]
  busy: boolean
  onCancel: () => void
  onWrite: () => void
}) {
  const bytes = writes.reduce((sum, w) => sum + w.after, 0)
  const grew = writes.reduce((sum, w) => sum + (w.after - w.before), 0)

  return (
    <div className="save-plan" role="dialog" aria-label="What Save will write">
      <div className="row">
        <strong>
          {writes.length === 0
            ? 'Save has nothing to write'
            : `Save writes ${writes.length === 1 ? 'one file' : `${writes.length} files`}`}
        </strong>
        <span className="grow" />
        <button className="tiny" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button className="tiny primary" onClick={onWrite} disabled={busy || !writes.length}>
          {busy ? 'Writing…' : `Write ${writes.length === 1 ? 'it' : `${writes.length} files`}`}
        </button>
      </div>

      {writes.length === 0 ? (
        <div className="muted small">
          Nothing to write: every file would come out exactly as it already is on
          disk. Take a change, or rewrite one, and it will appear here.
        </div>
      ) : (
        <table>
          <tbody>
            {writes.map((write) => (
              <tr key={write.path}>
                <td className="mono">{write.path}</td>
                <td className="counts">
                  {write.tally.taken} taken
                  {write.tally.rewritten > 0 && ` · ${write.tally.rewritten} rewritten`}
                  {write.tally.open > 0 && ` · ${write.tally.open} left open, kept as yours`}
                </td>
                <td className="bytes mono">
                  {write.before.toLocaleString()} → {write.after.toLocaleString()} bytes
                  <span className="delta">
                    {write.after >= write.before ? ' +' : ' −'}
                    {Math.abs(write.after - write.before).toLocaleString()}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="muted small">
        {writes.length > 0 && (
          <>
            {bytes.toLocaleString()} bytes in all, {grew >= 0 ? 'up' : 'down'}{' '}
            {Math.abs(grew).toLocaleString()} on what is there now.{' '}
          </>
        )}
        Each file is written whole, into your working copy. Nothing is committed
        and nothing is pushed.
      </div>
    </div>
  )
}
