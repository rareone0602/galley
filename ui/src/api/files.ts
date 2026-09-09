import { json, qs } from './client'
import type { FileBody, TreeNode } from './types'

/** What a route that changed the project answers with. */
type Written = { ok: boolean; path: string }

/** Escape each segment but keep the slashes: the route matches a whole path. */
const asUrl = (path: string) => path.split('/').map(encodeURIComponent).join('/')

/**
 * The project rail and the editor: what is in the paper, and its contents.
 *
 * The four that change the project always act on the main worktree — the
 * backend gives them no session to work in, because rearranging the project is
 * your edit rather than the agent's.
 */
export const filesApi = {
  tree: (sessionId?: string) =>
    json<{ root: string; tree: TreeNode[] }>('/api/tree' + qs({ session_id: sessionId })),
  file: (path: string, sessionId?: string) =>
    json<FileBody>('/api/file' + qs({ path, session_id: sessionId })),
  blobUrl: (path: string, sessionId?: string) =>
    '/api/blob' + qs({ path, session_id: sessionId }),
  writeFile: (path: string, content: string) =>
    json<{ ok: boolean; bytes: number }>(`/api/files/${asUrl(path)}`, {
      method: 'PUT',
      body: JSON.stringify({ content }),
    }),

  /** `parent` is a folder already in the project, or '' for the root. */
  createFile: (parent: string, name: string) =>
    json<Written>('/api/files', {
      method: 'POST',
      body: JSON.stringify({ parent, name, folder: false }),
    }),
  createFolder: (parent: string, name: string) =>
    json<Written>('/api/files', {
      method: 'POST',
      body: JSON.stringify({ parent, name, folder: true }),
    }),
  /** `to` is a whole path: renaming to `sections/intro.tex` is how you move. */
  renameFile: (path: string, to: string) =>
    json<Written & { was: string }>('/api/files/rename', {
      method: 'POST',
      body: JSON.stringify({ path, to }),
    }),
  deleteFile: (path: string) =>
    json<Written>(`/api/files/${asUrl(path)}`, { method: 'DELETE' }),
  /**
   * The file itself is the request body. `json()` sets a JSON content type by
   * default and the header given here replaces it, which is the whole reason
   * this can go through the same wrapper as everything else.
   */
  uploadFile: (parent: string, file: File, replace = false) =>
    json<Written & { bytes: number }>(
      '/api/files/upload' + qs({ name: file.name, parent, replace }),
      {
        method: 'POST',
        headers: { 'Content-Type': file.type || 'application/octet-stream' },
        body: file,
      },
    ),
}
