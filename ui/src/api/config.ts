import { json } from './client'
import type { Config } from './types'

/** What the server was started with: which repo, which branch, which main.tex. */
export const configApi = {
  config: () => json<Config>('/api/config'),
}
