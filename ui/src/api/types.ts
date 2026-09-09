// Every shape here matches a route in galley/routes/; nothing is invented on
// the client. One file so a new area can read the shared vocabulary without
// importing a sibling's module.

export type WordSpan = { op: 'same' | 'del' | 'ins'; text: string }

export type DiffOp = {
  id: number
  type: 'equal' | 'change'
  old: string
  new: string
  old_words: WordSpan[]
  new_words: WordSpan[]
  kinds: string[]
}

export type FileDiff = { path: string; ops: DiffOp[]; changes: number }

/** One entry in the project rail. Folders carry children; files do not. */
export type TreeNode = {
  name: string
  path: string
  type: 'dir' | 'tex' | 'text' | 'image' | 'figure' | 'binary'
  children?: TreeNode[]
}

/** A file as the editor gets it. `content` is null for anything not text. */
export type FileBody = {
  path: string
  type: TreeNode['type']
  content: string | null
  bytes: number
}

/** A block you highlighted, and where in the file it came from. */
export type Selection = { path: string; start: number; end: number; text: string }

/** Where a point on the printed page came from. `line` is 1-based. */
export type SourceLocation = { path: string; line: number; in_project: boolean }

export type Session = {
  id: string
  slug: string
  branch: string
  worktree_path: string
  prompt: string
  status: string
  base_sha: string | null
  error: string | null
  created_at: number
  ended_at: number | null
  sel_path: string | null
  sel_start: number | null
  sel_end: number | null
  sel_text: string | null
  running?: boolean
  files?: { path: string; added: number | null; removed: number | null }[]
}

export type GitStatus = {
  branch: string
  head: string
  clean: boolean
  files: { path: string; index: string; worktree: string }[]
  worktrees: { path: string; branch: string }[]
  log: { sha: string; author: string; ts: number; subject: string }[]
  overleaf: {
    branch: string
    on_main: boolean
    clean: boolean
    remote: string
    remote_branch: string
    ahead: number | null
    behind: number | null
    conflicts: string[]
  }
}

export type Config = {
  paper_repo: string
  code_mirror: string
  main_branch: string
  main_tex: string
  overleaf: string
  max_concurrent_sessions: number
  latexdiff: boolean
  bind: string
}

export type LogEvent = {
  id: number
  session_id: string | null
  job_id: string | null
  kind: string
  payload: any
  ts: number
}

/** A long job's state. `ok`/`errors` are only present once state is "done". */
export type Work = {
  state: 'idle' | 'running' | 'done' | 'failed'
  elapsed_seconds: number | null
  error?: string
  ok?: boolean
  pdf?: string | null
  errors?: string[]
  undefined?: string[]
  log_tail?: string
}

export type SyncResult = {
  ok: boolean
  step: string
  message: string
  conflicts: string[]
  pushed: boolean
}
