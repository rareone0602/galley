import { json, qs } from './client'
import type { DiffOp, FileDiff } from './types'

/** The merge pane: what Claude changed, sentence by sentence. */
export const diffApi = {
  diff: (sessionId: string, path?: string) =>
    json<{ base: string; head: string; files: FileDiff[] }>(
      '/api/diff' + qs({ session_id: sessionId, path }),
    ),
}

/** The whole file, as your choices make it.
 *
 * Three ways a change can end up: your wording (the default), Claude's
 * (accepted), or something you typed yourself, which beats both.
 *
 * This is the client half of a pair — `galley.segment.diff.apply_ops` is the
 * server half, and `tests/test_diff.py` pins them against each other.
 */
export function applyOps(
  ops: DiffOp[],
  accepted: Set<number>,
  edits: Record<number, string> = {},
): string {
  return ops
    .map((op) => {
      if (op.id in edits) return edits[op.id]
      return op.type === 'equal' || accepted.has(op.id) ? op.new : op.old
    })
    .join('')
}
