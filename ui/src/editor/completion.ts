/* Completing what the project already contains.
 *
 * Overleaf finishes `\cite{`, `\ref{` and your own macros for you, and the
 * reason is not typing speed: a key on its own tells you nothing. You cannot
 * choose between `sweep` and `sweeplaws` from the keys, so every option here
 * carries what you would otherwise open another file to find — the author and
 * year beside a citation, the section or caption beside a label, the argument
 * count beside a macro.
 *
 * The project index comes from `GET /api/project/index`, which is cheap to ask
 * again but not free, so it is fetched on the first completion and re-read on
 * an interval rather than on a keystroke.
 */
import {
  autocompletion,
  snippetCompletion,
  type Completion,
  type CompletionContext,
  type CompletionResult,
  type CompletionSection,
} from '@codemirror/autocomplete'
import { EditorState, type Extension } from '@codemirror/state'
import { EditorView } from '@codemirror/view'
import {
  projectApi,
  type ProjectCitation,
  type ProjectIndex,
  type ProjectLabel,
} from '../api/project'

/** The commands whose braces hold a bibliography key. */
const CITE_COMMANDS = ['cite', 'citep', 'citet', 'citealp']
/** The commands whose braces hold a label. */
const REF_COMMANDS = ['ref', 'eqref', 'autoref', 'cref', 'Cref']

/* How long an index is trusted before the next completion re-reads it. The
 * index only changes when a file on disk does, so a keystroke is never a
 * reason to ask again — but having just edited the buffer is a fair sign that
 * a save, and a new label, is close behind. */
const IDLE_MS = 60_000
const AFTER_EDIT_MS = 5_000

/* Room for the grey text beside a key. Long enough for "Hoffmann et al. 2022 ·
 * Training Compute-Optimal…", short enough not to push the key off the left. */
const TITLE_LIMIT = 64
const DETAIL_LIMIT = 92

const PROJECT: CompletionSection = { name: 'This project', rank: 0 }
const LATEX: CompletionSection = { name: 'LaTeX', rank: 1 }

/* The commands worth offering that no project defines, with the number of
 * arguments each takes so the completion can leave the cursor between braces.
 * Deliberately short: this is the set you reach for in a paper, not a manual. */
const COMMON_COMMANDS: readonly (readonly [string, number])[] = [
  ['begin', 1], ['end', 1], ['item', 0],
  ['section', 1], ['subsection', 1], ['subsubsection', 1], ['paragraph', 1],
  ['label', 1], ['caption', 1],
  ['ref', 1], ['eqref', 1], ['autoref', 1], ['cref', 1], ['Cref', 1],
  ['cite', 1], ['citep', 1], ['citet', 1], ['citealp', 1],
  ['emph', 1], ['textbf', 1], ['textit', 1], ['texttt', 1], ['textsc', 1],
  ['footnote', 1], ['input', 1], ['include', 1], ['includegraphics', 1],
  ['url', 1], ['href', 2],
  ['frac', 2], ['sqrt', 1], ['text', 1], ['mathrm', 1], ['mathbb', 1],
  ['mathcal', 1], ['left', 0], ['right', 0],
  ['sum', 0], ['prod', 0], ['int', 0], ['log', 0], ['exp', 0],
  ['cdot', 0], ['times', 0], ['leq', 0], ['geq', 0], ['approx', 0], ['sim', 0],
  ['alpha', 0], ['beta', 0], ['gamma', 0], ['delta', 0], ['lambda', 0],
  ['mu', 0], ['sigma', 0], ['Delta', 0], ['Lambda', 0], ['Sigma', 0],
]

const COMMON_ENVIRONMENTS = [
  'abstract', 'align', 'align*', 'center', 'enumerate', 'equation', 'equation*',
  'figure', 'figure*', 'itemize', 'proof', 'quote', 'table', 'table*',
  'tabular', 'theorem', 'verbatim',
]

/** `\citep[see][]{first, seco` — the partial key at the cursor is the capture.
 *
 *  Several keys in one pair of braces is normal (`\cite{a,b,c}`) and each one
 *  completes, so everything up to the last comma is skipped over. */
function keyArgument(commands: readonly string[]): RegExp {
  return new RegExp(
    `\\\\(?:${commands.join('|')})\\*?(?:\\[[^\\]]*\\])*\\{(?:[^{}]*,)?\\s*([^{},\\s]*)$`,
  )
}

const CITE_KEY = keyArgument(CITE_COMMANDS)
const REF_KEY = keyArgument(REF_COMMANDS)
const ENVIRONMENT_NAME = /\\(?:begin|end)\s*\{([A-Za-z*]*)$/
const CONTROL_SEQUENCE = /\\([A-Za-z]*)$/

/** While what you type still looks like a key, the list is filtered in place
 *  rather than asked for again. */
const KEY_CHARS = /^[^{},\s]*$/
const NAME_CHARS = /^[A-Za-z*]*$/
const COMMAND_CHARS = /^\\[A-Za-z]*$/

export type LatexCompletionOptions = {
  /** Complete against a session's checkout instead of the main worktree. */
  sessionId?: string
}

/** LaTeX completion for the editor: citations, labels, macros, environments. */
export function latexCompletion(options: LatexCompletionOptions = {}): Extension {
  const index = indexHolder(options.sessionId)
  /* Built once, deliberately. CodeMirror tells one completion source from
   * another by identity, so handing back a fresh function each time it asks
   * makes every update look like a new source: the query it already had in
   * flight is thrown away and started again, and the list never settles. */
  const languageData = [{ autocomplete: source(index) }]
  return [
    // No icons: Overleaf's list is text, and a column of glyphs would be a
    // second visual language for the same thing.
    autocompletion({ icons: false }),
    EditorState.languageData.of(() => languageData),
    EditorView.updateListener.of((update) => {
      if (update.docChanged) index.touch()
    }),
  ]
}

/** The one copy of the index this editor holds, and when it was last read. */
function indexHolder(sessionId: string | undefined) {
  let current: ProjectIndex | null = null
  let fetchedAt = 0
  let editedAt = 0
  let inflight: Promise<ProjectIndex | null> | null = null

  const stale = () =>
    Date.now() - fetchedAt > (editedAt > fetchedAt ? AFTER_EDIT_MS : IDLE_MS)

  return {
    touch: () => {
      editedAt = Date.now()
    },
    read(): Promise<ProjectIndex | null> {
      if (current && !stale()) return Promise.resolve(current)
      if (!inflight) {
        inflight = projectApi
          .projectIndex(sessionId)
          .then((fresh) => (current = fresh))
          // Keep whatever we already had and wait out the usual interval. An
          // editor that cannot complete is a nuisance; one that retries the
          // failure on every keystroke is a stampede.
          .catch(() => current)
          .finally(() => {
            fetchedAt = Date.now()
            inflight = null
          })
      }
      return inflight
    },
  }
}

type IndexHolder = ReturnType<typeof indexHolder>

function source(index: IndexHolder) {
  return async (context: CompletionContext): Promise<CompletionResult | null> => {
    const cite = context.matchBefore(CITE_KEY)
    const ref = context.matchBefore(REF_KEY)
    const environment = context.matchBefore(ENVIRONMENT_NAME)
    const command = context.matchBefore(CONTROL_SEQUENCE)
    if (!cite && !ref && !environment && !command) return null

    const project = await index.read()

    if (cite) {
      const typed = CITE_KEY.exec(cite.text)?.[1] ?? ''
      return {
        from: context.pos - typed.length,
        options: unique(project?.citations ?? [], (x) => x.key).map(citationOption),
        validFor: KEY_CHARS,
      }
    }
    if (ref) {
      const typed = REF_KEY.exec(ref.text)?.[1] ?? ''
      return {
        from: context.pos - typed.length,
        options: unique(project?.labels ?? [], (x) => x.key).map(labelOption),
        validFor: KEY_CHARS,
      }
    }
    if (environment) {
      const typed = ENVIRONMENT_NAME.exec(environment.text)?.[1] ?? ''
      return {
        from: context.pos - typed.length,
        options: environmentOptions(project),
        validFor: NAME_CHARS,
      }
    }
    // The backslash is part of the completion, so that the list reads as
    // `\qlambda` rather than `qlambda` and what you have typed still matches.
    return {
      from: context.pos - (CONTROL_SEQUENCE.exec(command!.text)?.[1] ?? '').length - 1,
      options: commandOptions(project),
      validFor: COMMAND_CHARS,
    }
  }
}

/* CodeMirror's `type` is presentational — it chooses an icon — so it is the
 * wrong thing to read for "which kind of completion was that". This table
 * lives here, beside the builders that set it, so a `type` changed for how the
 * list looks cannot silently relabel the usage log from another file. An
 * unrecognised type answers undefined rather than guessing: "was completion
 * used at all" then survives a drift even where "which kind" does not. */
const KIND_OF_TYPE: Record<string, string> = {
  variable: 'cite',
  constant: 'ref',
  keyword: 'macro',
  type: 'environment',
}

/** Which kind of completion this option offers: cite, ref, macro, environment. */
export function completionKind(option: Completion): string | undefined {
  return option.type ? KIND_OF_TYPE[option.type] : undefined
}

// -- turning the index into what the list shows -------------------------------

function citationOption(entry: ProjectCitation): Completion {
  const who = [shortAuthor(entry.author), entry.year].filter(Boolean).join(' ')
  const title = clip(entry.title ?? '', TITLE_LIMIT)
  const full = [entry.author, entry.title].filter(Boolean).join(' — ')
  return {
    label: entry.key,
    detail: clip([who, title].filter(Boolean).join(' · '), DETAIL_LIMIT) || undefined,
    info: [full, `${entry.file}:${entry.line}`].filter(Boolean).join('  ·  '),
    type: 'variable',
  }
}

function labelOption(entry: ProjectLabel): Completion {
  const detail = [entry.kind, entry.context].filter(Boolean).join(' · ')
  return {
    label: entry.key,
    detail: clip(detail, DETAIL_LIMIT) || undefined,
    info: `${entry.file}:${entry.line}`,
    type: 'constant',
  }
}

function commandOptions(project: ProjectIndex | null): Completion[] {
  const options: Completion[] = []
  const seen = new Set<string>()
  for (const macro of project?.macros ?? []) {
    if (macro.kind !== 'command' || seen.has(macro.name)) continue
    seen.add(macro.name)
    options.push({
      ...commandOption(macro.name, macro.args, macro.optional, PROJECT),
      info: `defined in ${macro.file}:${macro.line}`,
    })
  }
  for (const [name, args] of COMMON_COMMANDS) {
    if (seen.has(name)) continue
    seen.add(name)
    options.push(commandOption(name, args, false, LATEX))
  }
  return options
}

function environmentOptions(project: ProjectIndex | null): Completion[] {
  const options: Completion[] = []
  const seen = new Set<string>()
  for (const macro of project?.macros ?? []) {
    if (macro.kind !== 'environment' || seen.has(macro.name)) continue
    seen.add(macro.name)
    options.push({
      label: macro.name,
      info: `defined in ${macro.file}:${macro.line}`,
      type: 'type',
      section: PROJECT,
    })
  }
  for (const name of COMMON_ENVIRONMENTS) {
    if (seen.has(name)) continue
    seen.add(name)
    options.push({ label: name, type: 'type', section: LATEX })
  }
  return options
}

/** One command in the list. Anything that takes arguments arrives with its
 *  braces already written and the cursor inside the first pair — a macro you
 *  half-remember is no use if you then have to remember its shape. */
function commandOption(
  name: string,
  args: number,
  optional: boolean,
  section: CompletionSection,
): Completion {
  const label = '\\' + name
  if (args === 0) return { label, type: 'keyword', section }
  const slots: string[] = []
  for (let i = 1; i <= args; i += 1) {
    slots.push(i === 1 && optional ? `[#{${i}}]` : `{#{${i}}}`)
  }
  return snippetCompletion(label + slots.join(''), {
    label,
    detail: args === 1 ? '1 argument' : `${args} arguments`,
    type: 'keyword',
    section,
  })
}

/** The surname you would write in prose, from a BibTeX author field. */
function shortAuthor(author: string | null): string {
  if (!author) return ''
  const first = author.split(/\s+and\s+/)[0].trim()
  const surname = first.includes(',')
    ? first.slice(0, first.indexOf(','))
    : (first.split(/\s+/).pop() ?? first)
  return /\sand\s/.test(author) ? `${surname} et al.` : surname
}

function clip(text: string, limit: number): string {
  return text.length <= limit ? text : text.slice(0, limit - 1).trimEnd() + '…'
}

/** The same key defined in two files is one option; the first site wins.
 *
 *  A paper with an `archive/` folder has plenty of these, and the list can
 *  only ever insert the key itself. */
function unique<T>(items: readonly T[], name: (item: T) => string): T[] {
  const seen = new Set<string>()
  const out: T[] = []
  for (const item of items) {
    const key = name(item)
    if (seen.has(key)) continue
    seen.add(key)
    out.push(item)
  }
  return out
}
