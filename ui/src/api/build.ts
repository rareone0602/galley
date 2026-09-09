import { json, qs } from './client'
import type { Work } from './types'

/** latexmk and latexdiff. Both are started and then polled: a real paper takes
 *  tens of seconds, and latexdiff takes minutes. */
export const buildApi = {
  compile: (sessionId?: string) =>
    json<Work>('/api/compile', {
      method: 'POST',
      body: JSON.stringify(sessionId ? { session_id: sessionId } : {}),
    }),
  compileStatus: (sessionId?: string) =>
    json<Work>('/api/compile' + qs({ session_id: sessionId })),
  review: (sessionId: string) =>
    json<Work>('/api/review', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId }),
    }),
  reviewStatus: (sessionId: string) => json<Work>('/api/review' + qs({ session_id: sessionId })),
  pdfUrl: (sessionId: string | null, review: boolean, stamp: number) =>
    '/api/pdf' + qs({ session_id: sessionId, review, t: stamp }),
}
