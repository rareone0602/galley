import { json, qs } from './client'

/** A `\label` in the project, and enough context to tell two of them apart.
 *
 *  `kind` is what the label appears to name — read off the structure around
 *  it, not off the `fig:`/`tab:` prefix — and is null when nothing in the
 *  source says. `context` is the enclosing caption or the section title, kept
 *  as LaTeX source: the server does not pretend to render it. */
export type ProjectLabel = {
  key: string
  file: string
  line: number
  kind: string | null
  context: string | null
}

/** One `.bib` entry key, with what you actually choose a citation by. */
export type ProjectCitation = {
  key: string
  entry_type: string
  title: string | null
  author: string | null
  year: string | null
  file: string
  line: number
}

/** A command or environment the project defines. `name` carries no backslash. */
export type ProjectMacro = {
  name: string
  kind: 'command' | 'environment'
  args: number
  /** The first argument is optional, as in `\newcommand{\cell}[3][term]`. */
  optional: boolean
  file: string
  line: number
}

export type ProjectIndex = {
  labels: ProjectLabel[]
  citations: ProjectCitation[]
  macros: ProjectMacro[]
  /** How many files were read, and how long reading them took. */
  files: number
  built_ms: number
}

/** What the project defines, so the editor can offer it back to you. */
export const projectApi = {
  projectIndex: (sessionId?: string) =>
    json<ProjectIndex>('/api/project/index' + qs({ session_id: sessionId })),
}
