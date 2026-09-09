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

/** A rectangle on one page, in big points from that page's top-left corner —
 *  the same measure a double-click on the PDF sends back the other way. */
export type PageArea = { page: number; x: number; y: number; width: number; height: number }

/** Where a source line ended up in print. `line` is the line that was actually
 *  found: it differs from `asked_line` when the line you asked about printed
 *  nothing and the search fell forward to the next one that did. */
export type SourceView = {
  path: string
  asked_line: number
  line: number
  fell_forward: boolean
  areas: PageArea[]
}

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
  publish: {
    branch: string
    on_main: boolean
    clean: boolean
    remote: string
    remote_branch: string
    /** Where the remote stands, read locally: `ready` means we hold a ref for
     *  its branch and the counts are real, `unpushed` that this machine has
     *  never seen that branch on it, `no_remote` that git has no remote by
     *  that name — ordinary for a new project. */
    state: 'ready' | 'unpushed' | 'no_remote'
    /** Why Sync cannot run right now, or null when it can. The backend owns
     *  this sentence: pressing Sync would refuse with exactly these words. */
    blocked: string | null
    ahead: number | null
    behind: number | null
    conflicts: string[]
  }
}

export type Config = {
  paper_repo: string
  /** Null when the project has no companion codebase, which is the usual case. */
  code_mirror: string | null
  main_branch: string
  main_tex: string
  /** Whether `main_tex` is really there. Everything LaTeX — the PDF, SyncTeX,
   *  `\cite` completion — is hidden rather than offered when it is not. */
  builds_pdf: boolean
  /** `remote/branch`, the place Sync pushes to. */
  publish: string
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

/** One thing the compile log says you should go and look at. `path` and `line`
 *  are null unless the log said so plainly or the guess could be checked: being
 *  sent to the wrong sentence is worse than being sent nowhere. */
export type Problem = {
  severity: 'error' | 'warning'
  message: string
  path: string | null
  line: number | null
}

/** A long job's state. `ok`/`problems` are only present once state is "done". */
export type Work = {
  state: 'idle' | 'running' | 'done' | 'failed'
  elapsed_seconds: number | null
  error?: string
  ok?: boolean
  pdf?: string | null
  problems?: Problem[]
  log_tail?: string
}

export type SyncResult = {
  ok: boolean
  step: string
  message: string
  conflicts: string[]
  pushed: boolean
}
