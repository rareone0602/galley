import { json } from './client'
import type { GitStatus, SyncResult } from './types'

/** Git, and publishing to the remote. Every one of these is something you do,
 *  never the agent. */
export const gitApi = {
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
}
