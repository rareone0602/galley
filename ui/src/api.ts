// Typed access to the Galley backend. Every shape here matches a route in
// galley/app.py; nothing is invented on the client.

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

async function json<T>(input: string, init?: RequestInit): Promise<T> {
  const res = await fetch(input, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      detail = (await res.json()).detail ?? detail
    } catch {
      /* the body was not JSON; the status line will do */
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export const api = {
  config: () => json<Config>('/api/config'),

  sessions: () => json<Session[]>('/api/sessions'),
  session: (id: string) => json<Session>(`/api/sessions/${id}`),
  createSession: (prompt: string, selection?: Selection) =>
    json<Session>('/api/sessions', {
      method: 'POST',
      body: JSON.stringify({ prompt, start: true, selection }),
    }),
  message: (id: string, text: string) =>
    json<{ ok: boolean }>(`/api/sessions/${id}/message`, {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),
  stopSession: (id: string) => json<{ ok: boolean }>(`/api/sessions/${id}/stop`, { method: 'POST' }),
  removeSession: (id: string, keepBranch = true) =>
    json<{ ok: boolean }>(`/api/sessions/${id}?keep_branch=${keepBranch}`, { method: 'DELETE' }),

  tree: (sessionId?: string) =>
    json<{ root: string; tree: TreeNode[] }>(
      '/api/tree' + (sessionId ? `?session_id=${sessionId}` : ''),
    ),
  file: (path: string, sessionId?: string) =>
    json<FileBody>(
      `/api/file?path=${encodeURIComponent(path)}` + (sessionId ? `&session_id=${sessionId}` : ''),
    ),
  blobUrl: (path: string, sessionId?: string) =>
    `/api/blob?path=${encodeURIComponent(path)}` + (sessionId ? `&session_id=${sessionId}` : ''),

  diff: (sessionId: string, path?: string) =>
    json<{ base: string; head: string; files: FileDiff[] }>(
      `/api/diff?session_id=${sessionId}` + (path ? `&path=${encodeURIComponent(path)}` : ''),
    ),
  writeFile: (path: string, content: string) =>
    json<{ ok: boolean; bytes: number }>(`/api/files/${path}`, {
      method: 'PUT',
      body: JSON.stringify({ content }),
    }),

  /** Reverse search: a point on the paper, in big points from the page's
   *  top-left corner, back to the file and line that produced it. */
  synctexEdit: (page: number, x: number, y: number, sessionId?: string, review = false) =>
    json<SourceLocation>(
      `/api/synctex/edit?page=${page}&x=${x.toFixed(2)}&y=${y.toFixed(2)}` +
        (sessionId ? `&session_id=${sessionId}` : '') +
        (review ? '&review=true' : ''),
    ),

  gitStatus: () => json<GitStatus>('/api/git/status'),
  commit: (message: string, paths: string[]) =>
    json<{ ok: boolean; sha: string }>('/api/git/commit', {
      method: 'POST',
      body: JSON.stringify({ message, paths }),
    }),
  sync: (push = true) =>
    json<SyncResult>('/api/git/sync', { method: 'POST', body: JSON.stringify({ push }) }),
  rebase: (action: 'continue' | 'abort') =>
    json<SyncResult>(`/api/git/rebase/${action}`, { method: 'POST' }),


  compile: (sessionId?: string) =>
    json<Work>('/api/compile', {
      method: 'POST',
      body: JSON.stringify(sessionId ? { session_id: sessionId } : {}),
    }),
  compileStatus: (sessionId?: string) =>
    json<Work>('/api/compile' + (sessionId ? `?session_id=${sessionId}` : '')),
  review: (sessionId: string) =>
    json<Work>('/api/review', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId }),
    }),
  reviewStatus: (sessionId: string) => json<Work>(`/api/review?session_id=${sessionId}`),
}

/** The whole file, as your choices make it.
 *
 * Three ways a change can end up: your wording (the default), Claude's
 * (accepted), or something you typed yourself, which beats both.
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
