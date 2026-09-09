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
  running?: boolean
  files?: { path: string; added: number | null; removed: number | null }[]
}

export type Job = {
  id: string
  session_id: string | null
  scheduler_id: string | null
  state: string
  exit_code: number | null
  code_sha: string | null
  note: string | null
  artifacts_local: string | null
  submitted_at: number
  started_at: number | null
  finished_at: number | null
  tail?: string
}

export type GitStatus = {
  branch: string
  head: string
  clean: boolean
  files: { path: string; index: string; worktree: string }[]
  worktrees: { path: string; branch: string }[]
  log: { sha: string; author: string; ts: number; subject: string }[]
  unbacked_results: string[]
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
  backend: string
  scratch: string
  max_concurrent_sessions: number
  max_queued_jobs: number
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

export type CompileResult = {
  ok: boolean
  pdf: string | null
  errors: string[]
  undefined: string[]
  log_tail: string
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
  createSession: (prompt: string, start = true) =>
    json<Session>('/api/sessions', { method: 'POST', body: JSON.stringify({ prompt, start }) }),
  message: (id: string, text: string) =>
    json<{ ok: boolean }>(`/api/sessions/${id}/message`, {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),
  stopSession: (id: string) => json<{ ok: boolean }>(`/api/sessions/${id}/stop`, { method: 'POST' }),
  removeSession: (id: string, keepBranch = true) =>
    json<{ ok: boolean }>(`/api/sessions/${id}?keep_branch=${keepBranch}`, { method: 'DELETE' }),

  diff: (sessionId: string, path?: string) =>
    json<{ base: string; head: string; files: FileDiff[] }>(
      `/api/diff?session_id=${sessionId}` + (path ? `&path=${encodeURIComponent(path)}` : ''),
    ),
  writeFile: (path: string, content: string) =>
    json<{ ok: boolean; bytes: number }>(`/api/files/${path}`, {
      method: 'PUT',
      body: JSON.stringify({ content }),
    }),

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

  jobs: () => json<Job[]>('/api/jobs'),
  job: (id: string) => json<Job>(`/api/jobs/${id}`),
  submitJob: (script: string, note: string, gpus: number, hours: number) =>
    json<Job>('/api/jobs', {
      method: 'POST',
      body: JSON.stringify({ script, note, gpus, hours }),
    }),
  fetchArtifacts: (id: string) =>
    json<{ ok: boolean; artifacts: string; files: string[] }>(`/api/jobs/${id}/fetch`, {
      method: 'POST',
    }),
  cancelJob: (id: string) => json<{ ok: boolean }>(`/api/jobs/${id}/cancel`, { method: 'POST' }),
  handoff: (id: string) => json<Session>(`/api/jobs/${id}/handoff`, { method: 'POST' }),

  compile: (sessionId?: string) =>
    json<CompileResult>('/api/compile', {
      method: 'POST',
      body: JSON.stringify(sessionId ? { session_id: sessionId } : {}),
    }),
  review: (sessionId: string) =>
    json<CompileResult>('/api/review', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId }),
    }),
}

/** Apply a set of accepted change ids to the ops, giving the whole file. */
export function applyOps(ops: DiffOp[], accepted: Set<number>): string {
  return ops.map((op) => (op.type === 'equal' || accepted.has(op.id) ? op.new : op.old)).join('')
}
