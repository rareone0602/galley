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

/** A file as the editor gets it.
 *
 * `content` is null for anything with no text in it — a picture, a figure, a
 * payload. `editable` is the one to obey before writing: a file can be
 * perfectly readable and still not safe to save, because Galley read only the
 * front of it or because its bytes are not UTF-8. `bytes` is always the size
 * on disk, which is not the length of `content` when `truncated`. */
export type FileBody = {
  path: string
  type: TreeNode['type']
  content: string | null
  bytes: number
  /** Too big to open whole: `content` is the first part of it. */
  truncated: boolean
  /** Null when there is no text at all. */
  encoding: 'utf-8' | 'unknown' | null
  editable: boolean
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
  /** The CLI's own id for the conversation. Null until the first turn has
   *  started, which is also when there is nothing yet to compact. */
  claude_session_id: string | null
  /** How big the conversation was on the agent's last call, in tokens — what
   *  a follow-up pays for again on every call. Null after a compaction until
   *  the next turn says. */
  context_tokens: number | null
  /** What the session has cost so far, over every turn. */
  cost_usd: number | null
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
  /** Which Claude answers — an alias like `opus`, or a pinned model id. */
  agent_model: string
  latexdiff: boolean
  /** Whether Galley keeps a local record of how you use it. Never leaves this
   *  machine; `[usage] enabled` in the config is the switch. */
  usage: boolean
  bind: string
}

/** The usage log, turned into the questions it was kept to answer. Shapes
 *  only — this record contains no sentence of the paper. */
export type UsageReport = {
  days: number
  events: number
  kept_since: number | null
  kept_total: number
  counts: Record<string, number>
  never_used: string[]
  attention: {
    opened: number
    tabs: Record<string, number>
    files_opened: number
    distinct_files: number
    busiest_files: [string, number][]
    saves: number
  }
  loop: {
    sessions: Record<string, number>
    sessions_total: number
    continued: number
    stopped_early: number
    reviews_opened: number
    saves: number
    changes_offered: number
    changes_taken: number
    changes_rewritten: number
    decisions: Record<string, number>
    undos: number
  }
  waiting: {
    compiles: number
    compile_seconds: number
    compile_median_seconds: number | null
    compiles_failed: number
    agent_turns: number
    agent_seconds: number
    agent_cost_usd: number
  }
  friction: {
    errors: number
    where: Record<string, number>
    refusals: Record<string, number>
    reloaded_under_you: number
    buffers_restored: number
  }
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
  /** What to ask Claude when this build failed, written by the server so the
   *  wording has one owner and can be tested. Null on a build that worked, and
   *  absent on the marked-up review: that one compiles a latexdiff scratch
   *  tree, whose line numbers belong to files nobody edits. */
  fix_prompt?: string | null
}

export type SyncResult = {
  ok: boolean
  step: string
  message: string
  conflicts: string[]
  pushed: boolean
}
