import { applyOps, type DiffOp, type FileDiff } from '../../api'

/**
 * What you answered for one change, and what a session's answers add up to.
 *
 * A change you have not looked at yet and a change you deliberately kept write
 * exactly the same bytes, so `applyOps` has no reason to tell them apart. A
 * review tool does: "six still to go" is only sayable if "I read that one and
 * kept my sentence" is an answer you can give. So the answer is recorded here
 * rather than inferred from the set of accepted ids, and the accepted set is
 * derived from it at the one moment it is needed — assembling the file.
 */
export type Answer =
  | { kind: 'claude' }
  | { kind: 'keep' }
  | { kind: 'rewrite'; text: string }

/** One file's answers, by change id. No entry means the question is still open. */
export type Answers = Record<number, Answer>

/** Every file's answers, by path. This is the thing Undo winds back. */
export type Decisions = Record<string, Answers>

export type Tally = {
  total: number
  taken: number
  kept: number
  rewritten: number
  open: number
}

/** The reviewable ops: the `equal` runs are context, not questions. */
export function changesIn(file: FileDiff): DiffOp[] {
  return file.ops.filter((op) => op.type === 'change')
}

export function tally(changes: DiffOp[], answers: Answers): Tally {
  let taken = 0
  let kept = 0
  let rewritten = 0
  for (const op of changes) {
    const answer = answers[op.id]
    if (!answer) continue
    if (answer.kind === 'claude') taken += 1
    else if (answer.kind === 'keep') kept += 1
    else rewritten += 1
  }
  return {
    total: changes.length,
    taken,
    kept,
    rewritten,
    open: changes.length - taken - kept - rewritten,
  }
}

/** The same counts across every file in the session. */
export function combine(parts: Tally[]): Tally {
  return parts.reduce(
    (sum, part) => ({
      total: sum.total + part.total,
      taken: sum.taken + part.taken,
      kept: sum.kept + part.kept,
      rewritten: sum.rewritten + part.rewritten,
      open: sum.open + part.open,
    }),
    { total: 0, taken: 0, kept: 0, rewritten: 0, open: 0 },
  )
}

/** The whole file these answers make. */
export function resultFor(ops: DiffOp[], answers: Answers): string {
  const accepted = new Set<number>()
  const edits: Record<number, string> = {}
  for (const [key, answer] of Object.entries(answers)) {
    if (answer.kind === 'claude') accepted.add(Number(key))
    else if (answer.kind === 'rewrite') edits[Number(key)] = answer.text
  }
  return applyOps(ops, accepted, edits)
}

/** The file as it is on disk: every change answered your way. */
export function yoursFor(ops: DiffOp[]): string {
  return applyOps(ops, new Set())
}

/** UTF-8 bytes, which is what lands on disk — not characters. */
export function byteLength(text: string): number {
  return new TextEncoder().encode(text).length
}

/** One file Save would write, and what writing it would do. */
export type PlannedWrite = {
  path: string
  text: string
  before: number
  after: number
  tally: Tally
}

/**
 * What Save will do, worked out before it does it.
 *
 * A file whose answers reproduce what is already on disk is left out: writing
 * it would change nothing, and listing it would suggest it might. `disk` is
 * what this pane last saw in the working copy — the reconstruction it read the
 * diff from, or the buffer it wrote there since.
 */
export function plan(
  files: FileDiff[],
  decisions: Decisions,
  disk: Record<string, string>,
): PlannedWrite[] {
  const out: PlannedWrite[] = []
  for (const file of files) {
    const answers = decisions[file.path] ?? {}
    const text = resultFor(file.ops, answers)
    const before = disk[file.path] ?? yoursFor(file.ops)
    if (text === before) continue
    out.push({
      path: file.path,
      text,
      before: byteLength(before),
      after: byteLength(text),
      tally: tally(changesIn(file), answers),
    })
  }
  return out
}
