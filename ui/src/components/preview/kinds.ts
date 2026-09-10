/* Which files have something to show beyond their own source.
 *
 * The rule the rest of the UI works from: a `.tex` is drawn by LaTeX and
 * belongs to the PDF pane, and these three are drawn by the browser. Anything
 * not named here has no preview, so the right-hand column stays the paper.
 *
 * Keyed by extension rather than by the server's file type, because the
 * server answers a different question — whether the bytes are text at all —
 * and one fact with two owners is how the two answers drift apart.
 */

/** What the right-hand column shows instead of the PDF, if anything. */
export type PreviewKind = 'markdown' | 'json' | 'table'

const BY_EXTENSION = new Map<string, PreviewKind>([
  ['md', 'markdown'],
  ['markdown', 'markdown'],
  ['json', 'json'],
  ['csv', 'table'],
  ['tsv', 'table'],
])

/** What this file previews as, or null when it previews as nothing. */
export function previewKind(path: string | null): PreviewKind | null {
  if (!path) return null
  const name = (path.split('/').pop() ?? '').toLowerCase()
  const dot = name.lastIndexOf('.')
  // A leading dot is part of the name: `.json` is a file called `.json`.
  return dot > 0 ? (BY_EXTENSION.get(name.slice(dot + 1)) ?? null) : null
}

/** How the preview names itself in its own header. */
export const PREVIEW_LABEL: Record<PreviewKind, string> = {
  markdown: 'Markdown',
  json: 'JSON',
  table: 'Table',
}
