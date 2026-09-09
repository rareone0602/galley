import { json } from './client'
import type { UsageReport } from './types'

/** The usage log: what you did with the workbench, kept locally so it can be
 *  made better. Posting is done by `ui/src/usage.ts`, which batches; this is
 *  only for reading the record back. */
export const usageApi = {
  usageReport: (days = 30) => json<UsageReport>(`/api/usage/report?days=${days}`),
}
