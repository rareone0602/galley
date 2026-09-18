import { json } from './client'
import type { Branch } from './types'

/** The work you could review, whoever wrote it. */
export const branchesApi = {
  branches: () => json<Branch[]>('/api/branches'),
}
