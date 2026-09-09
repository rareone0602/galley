import { json, qs } from './client'
import type { SourceLocation, SourceView } from './types'

/** SyncTeX: the map between the printed page and the source that made it. */
export const synctexApi = {
  /** Reverse search: a point on the paper, in big points from the page's
   *  top-left corner, back to the file and line that produced it. */
  synctexEdit: (page: number, x: number, y: number, sessionId?: string, review = false) =>
    json<SourceLocation>(
      '/api/synctex/edit' +
        qs({ page, x: x.toFixed(2), y: y.toFixed(2), session_id: sessionId, review }),
    ),

  /** Forward search: the line the cursor is on, to the places on the page it
   *  printed. The rectangles come back in the same big points `synctexEdit`
   *  takes, so the two directions are measuring the same thing. */
  synctexView: (path: string, line: number, sessionId?: string, review = false) =>
    json<SourceView>('/api/synctex/view' + qs({ path, line, session_id: sessionId, review })),
}
