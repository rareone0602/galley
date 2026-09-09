import { json, qs } from './client'
import type { Selection, Session } from './types'

/** Claude sessions: create one, talk to it, stop it, put its worktree away. */
export const sessionsApi = {
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
  stopSession: (id: string) =>
    json<{ ok: boolean }>(`/api/sessions/${id}/stop`, { method: 'POST' }),
  removeSession: (id: string, keepBranch = true) =>
    json<{ ok: boolean }>(`/api/sessions/${id}${qs({ keep_branch: keepBranch })}`, {
      method: 'DELETE',
    }),
}
