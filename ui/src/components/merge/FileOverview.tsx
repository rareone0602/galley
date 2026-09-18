import type { FileDiff } from '../../api'
import { changesIn, tally, type Decisions } from './decisions'

/**
 * What a branch touched, before you read a word of it.
 *
 * A session usually changes one file and you go straight to the sentences. A
 * branch another agent worked on for an afternoon is a different animal: the
 * one that prompted this screen moved 1,537 lines across ten files, which is
 * more decisions in one sitting than this workbench had seen in a month. Met
 * one sentence at a time, that is not a review, it is an endurance test.
 *
 * So: the files first, how much moved in each, and the two answers you can give
 * to a whole file without reading it — take theirs, or keep mine. Those are the
 * same decisions the pane records one at a time, so they undo the same way and
 * nothing is written until Save. A passage you already rewrote is left alone.
 *
 * The bar is the size of the change, not its importance. It is there so a
 * 421-line cut does not look like a typo fix in a list of ten rows.
 */
export default function FileOverview({
  files,
  decisions,
  theirs,
  onOpen,
  onAnswerAll,
}: {
  files: FileDiff[]
  decisions: Decisions
  /** Whoever wrote the other side, for the buttons to name. */
  theirs: string
  onOpen: (at: number) => void
  onAnswerAll: (path: string, kind: 'theirs' | 'keep') => void
}) {
  const widest = Math.max(1, ...files.map((f) => (f.added ?? 0) + (f.removed ?? 0)))

  return (
    <div className="overview">
      <div className="overview-head">
        <h2>{files.length} files changed</h2>
        <p className="muted small">
          Open one to answer it sentence by sentence, or answer a whole file here. Nothing is
          written until you Save.
        </p>
      </div>

      <div className="overview-list">
        {files.map((file, at) => {
          const counts = tally(changesIn(file), decisions[file.path] ?? {})
          const moved = (file.added ?? 0) + (file.removed ?? 0)
          const settled = counts.total > 0 && counts.open === 0
          return (
            <div key={file.path} className={`orow${settled ? ' done' : ''}`}>
              <button className="oname" onClick={() => file.editable && onOpen(at)}>
                <span className="path">{file.path}</span>
                <span className="lines">
                  {file.added !== null && <span className="plus">+{file.added}</span>}
                  {file.removed !== null && <span className="minus">−{file.removed}</span>}
                </span>
                <span className="bar" title={`${moved} lines moved`}>
                  <span className="fill" style={{ width: `${Math.round((moved / widest) * 100)}%` }} />
                </span>
              </button>

              {/* A file with no sentences in it is named and explained, never
                  offered. Taking "everything replaced by nothing" on a deleted
                  file would write an empty file over your copy, which is not
                  what deleting a file means. */}
              {file.deleted ? (
                <span className="note gone">
                  {theirs} deleted this file. Galley will not delete yours — the rail does that.
                </span>
              ) : !file.editable ? (
                <span className="note">not text, so there is nothing to compare here</span>
              ) : (
                <>
                  {file.yours_moved && (
                    <span className="note moved" title="both of you changed this file">
                      you changed this too
                    </span>
                  )}
                  <span className="state">
                    {settled
                      ? `${counts.taken} taken · ${counts.rewritten} rewritten · ${counts.kept} kept`
                      : `${counts.total} changes, ${counts.open} to go`}
                  </span>
                  <button className="tiny" onClick={() => onAnswerAll(file.path, 'theirs')}>
                    Take theirs
                  </button>
                  <button className="tiny" onClick={() => onAnswerAll(file.path, 'keep')}>
                    Keep mine
                  </button>
                  <button className="tiny primary" onClick={() => onOpen(at)}>
                    Open
                  </button>
                </>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
