import { json, qs } from './client'
import type { FileBody, TreeNode } from './types'

/** The project rail and the editor: what is in the paper, and its contents. */
export const filesApi = {
  tree: (sessionId?: string) =>
    json<{ root: string; tree: TreeNode[] }>('/api/tree' + qs({ session_id: sessionId })),
  file: (path: string, sessionId?: string) =>
    json<FileBody>('/api/file' + qs({ path, session_id: sessionId })),
  blobUrl: (path: string, sessionId?: string) =>
    '/api/blob' + qs({ path, session_id: sessionId }),
  writeFile: (path: string, content: string) =>
    json<{ ok: boolean; bytes: number }>(`/api/files/${path}`, {
      method: 'PUT',
      body: JSON.stringify({ content }),
    }),
}
