import { json, qs } from './client'
import type { Work } from './types'

/** latexmk and latexdiff. Both are started and then polled: a real paper takes
 *  tens of seconds, and latexdiff takes minutes. */
export const buildApi = {
  /** `auto` says the build was not asked for by a press — a save set it off.
   *  It changes nothing about the build; it is there so the usage log can
   *  answer whether building on save earns its place. */
  compile: (sessionId?: string, auto = false) =>
    json<Work>('/api/compile', {
      method: 'POST',
      body: JSON.stringify({ ...(sessionId ? { session_id: sessionId } : {}), auto }),
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
