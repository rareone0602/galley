/* Typed access to the Galley backend, one module per area of the API.
 *
 * Adding an area is one import and one spread below, plus a new file beside
 * this one; nothing that calls `api.something()` has to know the split exists.
 */
import { buildApi } from './build'
import { configApi } from './config'
import { diffApi } from './diff'
import { filesApi } from './files'
import { gitApi } from './git'
import { projectApi } from './project'
import { sessionsApi } from './sessions'
import { synctexApi } from './synctex'

export * from './types'
export * from './project'
export { applyOps } from './diff'
export { json, qs } from './client'

export const api = {
  ...configApi,
  ...sessionsApi,
  ...filesApi,
  ...diffApi,
  ...gitApi,
  ...projectApi,
  ...buildApi,
  ...synctexApi,
}
