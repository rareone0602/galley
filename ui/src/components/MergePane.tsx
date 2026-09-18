import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, type FileDiff } from '../api'
import { record } from '../usage'
import ChangeMap from './merge/ChangeMap'
import { ChangeRow, EqualRow, stateOf, type View } from './merge/ChangeRow'
import FileOverview from './merge/FileOverview'
import SavePlan from './merge/SavePlan'
import Shortcuts, { actionFor, chordFor, isTyping } from './merge/Shortcuts'
import {
  changesIn,
  combine,
  plan,
  tally,
  yoursFor,
  type Answer,
  type Answers,
  type Decisions,
} from './merge/decisions'

/** One step you can take back: what you did, and the answers you did it to. */
type Step = { says: string; decisions: Decisions }

/**
 * Review a branch's draft the way Diffchecker shows a comparison: your text on
 * the left, theirs on the right, changed passages tinted, the exact words that
 * moved picked out inside them.
 *
 * What is on the right is whatever the branch has — a Claude session's last
 * commit, or another agent's checkout as it stands. This pane compares two
 * texts and never needs to know which agent wrote one of them. `theirs` is a
 * label, and that is the whole of what it knows.
 *
 * The merge is the middle column, and it has three answers, not two: keep
 * yours, take theirs, or **write a third thing**. That third one is the one
 * this pane is really for, so it costs nothing: click into either side and
 * type, right there in the row, with the other version still beside you. The
 * caret lands where you pointed. Yours-rewritten wins over both, which is the
 * point — their draft is a suggestion, and the sentence that lands is the one
 * you decided on.
 *
 * A branch across a real paper is dozens of changes in several files, so this
 * is a review tool rather than a long scroll: the files are listed first and
 * can be answered whole, the keyboard steps through the sentences of one
 * (`?` for the list), the header says where you are and how much is left, the
 * strip down the right says where the changes are and which are answered, and
 * every decision — the bulk ones included — is undoable right up until Save.
 *
 * Nothing here applies a patch, and nothing but Save writes. The backend hands
 * over ops covering the whole file, the result is those ops with your choices
 * substituted in, and Save writes that entire buffer, after telling you what it
 * is about to write. There is no patch offset to get wrong. It writes your
 * working copy and nothing else — never the branch, never anyone's checkout.
 */
export default function MergePane({
  branch,
  theirs,
  liveState,
  onSaved,
}: {
  branch: string
  /** Who wrote the other side, for the columns and the controls to name. */
  theirs: string
  /** The branch's fingerprint as the rail last saw it. When it stops matching
   *  the one this diff was read at, the ground has moved under the review —
   *  another agent is a process, not a document. */
  liveState?: string
  onSaved: (path: string) => void
}) {
  const [files, setFiles] = useState<FileDiff[]>([])
  /** The state the diff was read at, to notice the branch moving underneath. */
  const [readAt, setReadAt] = useState<string | null>(null)
  /** The list of files, or the sentences of one. A source that touched a single
   *  file goes straight to the sentences; anything larger is triaged first,
   *  which is the difference between a review and an ordeal. */
  const [overview, setOverview] = useState(true)
  const [active, setActive] = useState(0)
  const [decisions, setDecisions] = useState<Decisions>({})
  const [past, setPast] = useState<Step[]>([])
  /** Each file as this pane last saw it in the working copy. */
  const [disk, setDisk] = useState<Record<string, string>>({})
  const [editing, setEditing] = useState<number | null>(null)
  /* What is in the open box. Held apart from the answers on purpose: clicking
   * into a sentence to look at it more closely is not a decision, and until a
   * key is pressed the change is still one of the ones left to go. */
  const [draft, setDraft] = useState('')
  const [cursor, setCursor] = useState(0)
  const [view, setView] = useState<View>('split')
  const [planning, setPlanning] = useState(false)
  const [helping, setHelping] = useState(false)
  const [saved, setSaved] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [scroller, setScroller] = useState<HTMLDivElement | null>(null)

  const reload = useCallback(async () => {
    setBusy(true)
    try {
      const body = await api.diff(branch)
      // A review starts when there is something in front of you to answer, so
      // that reviews opened against reviews saved reads as the drop-off. A
      // session Claude changed nothing on is not a review you walked away from.
      if (body.files.length) {
        record('review.open', {
          files: body.files.length,
          changes: body.files.reduce((sum, f) => sum + f.changes, 0),
          // Whether the branch list earns its place, answerable in `galley
          // usage` without adding to a vocabulary that is closed on purpose.
          kind: body.kind,
        })
      }
      setFiles(body.files)
      setReadAt(body.state)
      setOverview(body.files.length > 1)
      setDisk(Object.fromEntries(body.files.map((f) => [f.path, yoursFor(f.ops)])))
      setDecisions({})
      setPast([])
      setEditing(null)
      setActive(0)
      setCursor(0)
      setPlanning(false)
      setSaved(null)
      setError(null)
    } catch (e) {
      record('error.shown', { where: 'review', reason: 'diff' })
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }, [branch])

  useEffect(() => {
    void reload()
  }, [reload])

  const file = files[active] ?? null
  const changes = useMemo(() => (file ? changesIn(file) : []), [file])
  const positions = useMemo(() => new Map(changes.map((op, at) => [op.id, at])), [changes])
  const ids = useMemo(() => changes.map((op) => op.id), [changes])
  const answers: Answers = (file && decisions[file.path]) || {}
  const here = changes[cursor] ?? null
  const counts = useMemo(() => tally(changes, answers), [changes, answers])
  const everywhere = useMemo(
    () => combine(files.map((f) => tally(changesIn(f), decisions[f.path] ?? {}))),
    [files, decisions],
  )
  const writes = useMemo(() => plan(files, decisions, disk), [files, decisions, disk])

  // -- moving about -------------------------------------------------------

  const reveal = useCallback(
    (at: number) => {
      const op = changes[at]
      if (!op || !scroller) return
      scroller
        .querySelector(`[data-change-id="${op.id}"]`)
        ?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    },
    [changes, scroller],
  )

  const goTo = useCallback(
    (at: number) => {
      if (!changes.length) return
      const clamped = Math.max(0, Math.min(changes.length - 1, at))
      setCursor(clamped)
      reveal(clamped)
    },
    [changes.length, reveal],
  )

  // The scroller is one element for every file, so without this you arrive in
  // a newly picked file at whatever depth you had reached in the last one.
  useEffect(() => {
    scroller?.scrollTo({ top: 0 })
  }, [active, scroller])

  // -- answering ----------------------------------------------------------

  /** Take a copy of where the answers stood, so the last one can be undone. */
  function remember(says: string) {
    setPast((steps) => [...steps.slice(-99), { says, decisions }])
  }

  function put(id: number, answer: Answer) {
    if (!file) return
    setDecisions((prev) => ({
      ...prev,
      [file.path]: { ...(prev[file.path] ?? {}), [id]: answer },
    }))
  }

  function answerOne(id: number, answer: Answer, andStepOn: boolean) {
    const at = positions.get(id)
    if (at === undefined) return
    remember(`${answer.kind === 'theirs' ? 'took theirs' : 'kept yours'} on change ${at + 1}`)
    record('review.decide', { answer: answer.kind, bulk: false })
    put(id, answer)
    if (andStepOn) goTo(at + 1)
    else setCursor(at)
  }

  /**
   * Answer the whole file one way. A passage you took the trouble to rewrite
   * is left alone: the bulk keys are a convenience, and silently throwing away
   * a sentence you wrote is not one.
   */
  function answerAllIn(path: string, kind: 'theirs' | 'keep') {
    const target = files.find((f) => f.path === path)
    if (!target) return
    const already = decisions[path] ?? {}
    const every = changesIn(target)
    const settled = every.filter((op) => already[op.id]?.kind !== 'rewrite')
    const rewritten = every.length - settled.length
    remember(
      `${kind === 'theirs' ? 'took theirs' : 'kept yours'} on ${settled.length} changes in ` +
        `${short(path)}${rewritten ? `, leaving ${rewritten} rewritten` : ''}`,
    )
    const bulk: Answers = { ...already }
    // One record per change, as for a change answered on its own: what these
    // are here to show is how much of the paper is read one sentence at a time
    // and how much is swept, and a single event for a sweep loses exactly that.
    for (const op of settled) {
      bulk[op.id] = { kind }
      record('review.decide', { answer: kind, bulk: true })
    }
    setDecisions((prev) => ({ ...prev, [path]: bulk }))
  }

  /** The same, for the file you are reading. */
  function answerAll(kind: 'theirs' | 'keep') {
    if (file) answerAllIn(file.path, kind)
  }

  /** Open the box on a change. Answers nothing: see `draft`. */
  function startRewrite(id: number, seed: string) {
    const at = positions.get(id)
    if (at === undefined) return
    const already = answers[id]
    setDraft(already?.kind === 'rewrite' ? already.text : seed)
    setCursor(at)
    setEditing(id)
  }

  /** Every keystroke in the box. Undo winds back decisions, not typing — but
   *  the *first* keystroke is a decision, and it is the one that turns a
   *  sentence you were reading into a sentence you are writing. */
  function typeInto(id: number, text: string) {
    setDraft(text)
    if (answers[id]?.kind !== 'rewrite') {
      remember(`started rewriting change ${(positions.get(id) ?? 0) + 1}`)
      record('review.decide', { answer: 'rewrite', bulk: false })
    }
    put(id, { kind: 'rewrite', text })
  }

  function discardRewrite(id: number) {
    if (!file) return
    const at = positions.get(id)
    remember(`discarded the rewrite of change ${(at ?? 0) + 1}`)
    setDecisions((prev) => {
      const forFile = { ...(prev[file.path] ?? {}) }
      delete forFile[id]
      return { ...prev, [file.path]: forFile }
    })
    setDraft('')
    setEditing(null)
  }

  function undo() {
    const last = past[past.length - 1]
    if (!last) return
    record('review.undo')
    setDecisions(last.decisions)
    setPast(past.slice(0, -1))
    setEditing(null)
  }

  // -- the keyboard -------------------------------------------------------

  function handleKey(event: KeyboardEvent) {
    const action = actionFor(event)
    // While you are typing a rewrite, the letters belong to the sentence. Save
    // is the exception and has to be: it is the one chord the browser also
    // wants, so leaving it alone means Ctrl-S inside the box opens "save page"
    // — which is what it did, and why this line is here.
    if (isTyping(event.target)) {
      if (action !== 'save') return
      event.preventDefault()
      setEditing(null)
      setPlanning(true)
      return
    }
    if (!action) return
    // A key this pane claims is this pane's, even in the moment it is ignoring
    // it: letting Ctrl-S through to the browser's "save page" would be worse.
    event.preventDefault()
    // While the plan is up, the review keys would be changing the very thing
    // it is describing.
    if (planning && action !== 'close') return

    switch (action) {
      case 'next':
        return goTo(cursor + 1)
      case 'prev':
        return goTo(cursor - 1)
      case 'theirs':
        if (here) answerOne(here.id, { kind: 'theirs' }, true)
        return
      case 'keep':
        if (here) answerOne(here.id, { kind: 'keep' }, true)
        return
      case 'rewrite':
        if (here) {
          startRewrite(here.id, answers[here.id]?.kind === 'theirs' ? here.new : here.old)
        }
        return
      case 'theirsAll':
        return answerAll('theirs')
      case 'keepAll':
        return answerAll('keep')
      case 'undo':
        return undo()
      case 'save':
        return setPlanning(true)
      case 'help':
        return setHelping((on) => !on)
      case 'close':
        if (planning) return setPlanning(false)
        if (helping) return setHelping(false)
        return setEditing(null)
    }
  }

  // The listener is registered once; the handler it calls is this render's, so
  // it never reads a stale cursor or a stale set of answers.
  const latest = useRef(handleKey)
  useEffect(() => {
    latest.current = handleKey
  })
  useEffect(() => {
    const listener = (event: KeyboardEvent) => latest.current(event)
    window.addEventListener('keydown', listener)
    return () => window.removeEventListener('keydown', listener)
  }, [])

  // -- writing ------------------------------------------------------------

  async function write() {
    setBusy(true)
    const done: string[] = []
    try {
      for (const item of writes) {
        // The fingerprint the diff was read at. If you went back to the editor
        // and typed a sentence into this file while the review was open, the
        // backend refuses rather than overwriting it, and says so.
        await api.writeFile(item.path, item.text, item.sha)
        setDisk((prev) => ({ ...prev, [item.path]: item.text }))
        onSaved(item.path)
        done.push(item.path)
      }
      // The size shown is the one the plan promised: UTF-8 bytes, which is
      // what lands on disk. (The write route reports `len(content)`, which is
      // characters — the same number for ASCII LaTeX, and not for a file with
      // an em dash in it.)
      const bytes = writes.reduce((sum, item) => sum + item.after, 0)
      record('review.save', {
        files: done.length,
        taken: writes.reduce((sum, item) => sum + item.tally.taken, 0),
        rewritten: writes.reduce((sum, item) => sum + item.tally.rewritten, 0),
        bytes,
      })
      setSaved(
        `Wrote ${done.length === 1 ? done[0] : `${done.length} files`} · ` +
          `${bytes.toLocaleString()} bytes. Nothing was committed or pushed.`,
      )
      setPlanning(false)
      setError(null)
    } catch (e) {
      record('error.shown', { where: 'review', reason: 'save' })
      setError(`${String(e)}${done.length ? ` (wrote ${done.join(', ')} first)` : ''}`)
    } finally {
      setBusy(false)
    }
  }

  if (error && !files.length) return <div className="notice bad">{error}</div>
  if (!files.length)
    return (
      <div className="empty">
        {busy ? 'Reading the diff…' : `Nothing has changed on ${branch} yet.`}
      </div>
    )

  /* The branch moved while you were reading it. Not an error — another agent is
   * a process, not a document — but you are answering a copy that is no longer
   * what is there, so say so once and offer the reload. */
  const moved = readAt !== null && liveState !== undefined && liveState !== readAt

  const notices = (
    <>
      {moved && (
        <div className="notice warn merge-saved">
          {theirs} has changed {branch} since you opened this.{' '}
          <button className="tiny" onClick={() => void reload()}>
            Reload
          </button>
        </div>
      )}
      {error && <div className="notice bad merge-saved">{error}</div>}
      {saved && <div className="notice good merge-saved">{saved}</div>}
    </>
  )

  if (overview)
    return (
      <div className="merge">
        <div className="merge-bar">
          <span className="where">
            Reviewing <strong>{theirs}</strong>
          </span>
          <span className="grow" />
          <span className="muted small">
            {everywhere.total - everywhere.open} of {everywhere.total} answered
          </span>
          <button className="tiny" onClick={() => void reload()} disabled={busy}>
            Reload
          </button>
          <button
            className="tiny primary"
            onClick={() => setPlanning(true)}
            disabled={busy || !writes.length}
            title={`What Save would write (${chordFor('save')})`}
          >
            Save…
          </button>
        </div>
        {notices}
        {planning && (
          <SavePlan
            writes={writes}
            busy={busy}
            onCancel={() => setPlanning(false)}
            onWrite={() => void write()}
          />
        )}
        <FileOverview
          files={files}
          decisions={decisions}
          theirs={theirs}
          onOpen={(at) => {
            setActive(at)
            setCursor(0)
            setEditing(null)
            setOverview(false)
          }}
          onAnswerAll={answerAllIn}
        />
      </div>
    )

  if (!file) return <div className="empty">Nothing to read in this file.</div>

  return (
    <div className="merge">
      <div className="merge-bar">
        {files.length > 1 && (
          <button className="tiny" onClick={() => setOverview(true)} title="Back to the file list">
            ← Files
          </button>
        )}
        <div className="filepicker">
          {files.map((f, i) => {
            const left = tally(changesIn(f), decisions[f.path] ?? {}).open
            return (
              <button
                key={f.path}
                className={i === active ? 'on' : ''}
                onClick={() => {
                  setActive(i)
                  setCursor(0)
                  setEditing(null)
                }}
                title={`${f.path} — ${f.changes} changes, ${left} still open`}
              >
                {short(f.path)}
                <span className={`count${left ? '' : ' done'}`}>{left || '✓'}</span>
              </button>
            )
          })}
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
        <button
          className={`tiny${helping ? ' on' : ''}`}
          onClick={() => setHelping(!helping)}
          title={`Keyboard shortcuts (${chordFor('help')})`}
        >
          ?
        </button>
        <button className="tiny" onClick={() => void reload()} disabled={busy}>
          Reload
        </button>
      </div>

      <div className="merge-bar second">
        <button
          className="tiny"
          onClick={() => goTo(cursor - 1)}
          disabled={cursor === 0 || !changes.length}
          title={`Previous change (${chordFor('prev')})`}
        >
          ↑
        </button>
        <button
          className="tiny"
          onClick={() => goTo(cursor + 1)}
          disabled={cursor >= changes.length - 1}
          title={`Next change (${chordFor('next')})`}
        >
          ↓
        </button>
        {changes.length ? (
          <span className="where">
            Change <strong>{cursor + 1}</strong> of {changes.length}
            <span className="muted"> · </span>
            <span className="muted small">
              {counts.taken} taken · {counts.rewritten} rewritten · {counts.kept} kept ·{' '}
              {counts.open} to go
            </span>
          </span>
        ) : (
          <span className="muted small">no changes in this file</span>
        )}
        <span className="grow" />
        {files.length > 1 && (
          <span className="muted small session-tally">
            {everywhere.total - everywhere.open} of {everywhere.total} answered across{' '}
            {files.length} files
          </span>
        )}
        <button
          className="tiny"
          onClick={() => answerAll('theirs')}
          disabled={!changes.length}
          title={`Take theirs for this whole file (${chordFor('theirsAll')})`}
        >
          Take all
        </button>
        <button
          className="tiny"
          onClick={() => answerAll('keep')}
          disabled={!changes.length}
          title={`Keep yours for this whole file (${chordFor('keepAll')})`}
        >
          Keep all
        </button>
        <button
          className="tiny"
          onClick={undo}
          disabled={!past.length}
          title={
            past.length
              ? `Undo — you ${past[past.length - 1].says} (${chordFor('undo')})`
              : 'Nothing to undo'
          }
        >
          Undo
        </button>
        <button
          className="tiny primary"
          onClick={() => setPlanning(true)}
          disabled={busy}
          title={`What Save would write (${chordFor('save')})`}
        >
          Save…
        </button>
      </div>

      {helping && <Shortcuts onClose={() => setHelping(false)} />}
      {planning && (
        <SavePlan
          writes={writes}
          busy={busy}
          onCancel={() => setPlanning(false)}
          onWrite={() => void write()}
        />
      )}
      {notices}
      {file.yours_moved && (
        <div className="notice warn merge-saved">
          You have changed {short(file.path)} too since {branch} forked. Taking theirs on a
          sentence you have rewritten since will replace it with their version.
        </div>
      )}

      <div className="merge-body">
        <div className={`diff ${view}`} ref={setScroller}>
          {view === 'split' && (
            <div className="diff-head">
              <div className="col-head yours">Yours — {file.path}</div>
              <div className="col-head gutter" />
              <div className="col-head theirs">{theirs} proposes</div>
            </div>
          )}
          <div className="diff-body">
            {file.ops.map((op) =>
              op.type === 'equal' ? (
                <EqualRow key={op.id} text={op.new} view={view} />
              ) : (
                <ChangeRow
                  key={op.id}
                  op={op}
                  view={view}
                  theirs={theirs}
                  index={(positions.get(op.id) ?? 0) + 1}
                  current={here?.id === op.id}
                  answer={answers[op.id]}
                  editing={editing === op.id}
                  draft={draft}
                  onAnswer={(answer) => answerOne(op.id, answer, false)}
                  onRewrite={(seed) => startRewrite(op.id, seed)}
                  onType={(text) => typeInto(op.id, text)}
                  onDone={() => setEditing(null)}
                  onDiscard={() => discardRewrite(op.id)}
                />
              ),
            )}
          </div>
        </div>
        <ChangeMap
          key={`${file.path}:${view}`}
          scroller={scroller}
          ids={ids}
          stateOf={(id) => stateOf(answers[id])}
          current={here?.id ?? null}
          onPick={(id) => goTo(positions.get(id) ?? 0)}
        />
      </div>
    </div>
  )
}

/** A path as the file picker says it: the name, not the whole way there. */
function short(path: string): string {
  return path.split('/').pop() ?? path
}
