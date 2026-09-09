import { json, qs } from './client'
import type { SourceLocation } from './types'

/** SyncTeX: the map between the printed page and the source that made it. */
export const synctexApi = {
  /** Reverse search: a point on the paper, in big points from the page's
   *  top-left corner, back to the file and line that produced it. */
  synctexEdit: (page: number, x: number, y: number, sessionId?: string, review = false) =>
    json<SourceLocation>(
      '/api/synctex/edit' +
        qs({ page, x: x.toFixed(2), y: y.toFixed(2), session_id: sessionId, review }),
    ),
}
