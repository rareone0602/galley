/* Which language the editor speaks, decided from the file's name.
 *
 * Galley grew up around one paper, and for a while the editor simply *was* a
 * LaTeX editor: every file it opened was parsed as `stex`, whether that file
 * was `main.tex`, `refs.bib`, a `Makefile` or a `.json` config. Even in this
 * paper's own repository that is wrong for a good share of the rail, and in a
 * repository of code it would be wrong for nearly all of it. Choosing the
 * language is now one function with one table behind it, so nothing else in
 * the editor has to guess.
 *
 * The modes are CodeMirror 5's, ported in `@codemirror/legacy-modes` (MIT) and
 * already a dependency here for the LaTeX one. They are stream parsers rather
 * than Lezer grammars: coarser highlighting, and no syntax tree worth querying,
 * but one import per language and nothing to build.
 *
 * A file that matches nothing gets no language rather than the nearest thing to
 * hand. Plain text is honest; YAML coloured as LaTeX is not, and the wrongness
 * is quiet — the file looks highlighted, so you read it as if it were.
 */
import { StreamLanguage, type StreamParser } from '@codemirror/language'
import { css } from '@codemirror/legacy-modes/mode/css'
import { dockerFile } from '@codemirror/legacy-modes/mode/dockerfile'
import { javascript, json, typescript } from '@codemirror/legacy-modes/mode/javascript'
import { python } from '@codemirror/legacy-modes/mode/python'
import { shell } from '@codemirror/legacy-modes/mode/shell'
import { stex } from '@codemirror/legacy-modes/mode/stex'
import { toml } from '@codemirror/legacy-modes/mode/toml'
import { html, xml } from '@codemirror/legacy-modes/mode/xml'
import { yaml } from '@codemirror/legacy-modes/mode/yaml'
import type { Extension } from '@codemirror/state'
import { tags } from '@lezer/highlight'

/* BibTeX, which is the one language here that is written rather than imported.
 *
 * Neither `@codemirror/legacy-modes` nor CodeMirror 5 before it has a BibTeX
 * mode, and a paper's `.bib` is not an incidental file — it is the second one
 * you open. The grammar worth colouring is small enough to be honest about:
 * the entry type, the field names before their `=`, the delimiters, and bare
 * numbers. Field *values* are left as plain text, because they are prose and
 * a whole file of tinted titles is harder to read, not easier.
 *
 * Every branch either matches at least one character or falls through to
 * `next()`, so the stream always advances. */
const bibtex: StreamParser<unknown> = {
  name: 'bibtex',
  token(stream) {
    if (stream.eatSpace()) return null
    // BibTeX has no comment character of its own — it ignores whatever sits
    // between entries — but `%` is what a LaTeX author reaches for, and those
    // lines are read as notes by everyone including BibTeX.
    if (stream.match(/^%.*/)) return 'comment'
    if (stream.match(/^@[A-Za-z]+/)) return 'keyword'
    if (stream.match(/^[A-Za-z][\w.:-]*(?=\s*=)/)) return 'propertyName'
    if (stream.match(/^\d+/)) return 'number'
    if (stream.match(/^[{}()]/)) return 'bracket'
    stream.next()
    return null
  },
  languageData: { commentTokens: { line: '%' } },
}

/* CodeMirror translates CodeMirror 5's token names — `property`, `variable`,
 * `error` — when a mode returns one on its own, and it looks up nothing else.
 * Two of the modes here return two names at once (`string property` for a JSON
 * key, `string error` for an unterminated Python string), and each half is
 * looked up separately, so the second half arrives unknown and warns on the
 * console. Naming the halves settles it for every mode at once. */
const LEGACY_TOKEN_NAMES = {
  property: tags.propertyName,
  variable: tags.variableName,
  error: tags.invalid,
}

/** One language, built once. A compartment tells one configuration from
 *  another by identity, so handing it a fresh copy of the same language on
 *  every file change would re-parse the whole document for nothing.
 *
 *  The mode's own word list is left behind on the way through. Several of the
 *  ported modes carry a static `autocomplete` list — every Python builtin,
 *  every CSS property — which CodeMirror would pop up as you typed. Galley
 *  completes what the project actually contains and nothing else, and a mode's
 *  dictionary is a different feature rather than a side effect of colouring.
 *  Everything else the mode declares is kept, so `Ctrl-/` comments a Python
 *  line with `#` and a `.tex` line with `%`. */
function mode<State>(parser: StreamParser<State>): Extension {
  const { autocomplete, ...languageData } = parser.languageData ?? {}
  return StreamLanguage.define({
    ...parser,
    languageData,
    tokenTable: { ...LEGACY_TOKEN_NAMES, ...parser.tokenTable },
  })
}

const latexMode = mode(stex)
const bibtexMode = mode(bibtex)
const pythonMode = mode(python)
const shellMode = mode(shell)
const javascriptMode = mode(javascript)
const typescriptMode = mode(typescript)
const jsonMode = mode(json)
const yamlMode = mode(yaml)
const tomlMode = mode(toml)
const cssMode = mode(css)
const htmlMode = mode(html)
const xmlMode = mode(xml)
const dockerfileMode = mode(dockerFile)

/* Keyed by extension, lower-cased. A Map rather than an object because the
 * key comes from a filename: `notes.constructor` would find something on an
 * object's prototype and nothing here. */
const BY_EXTENSION = new Map<string, Extension>([
  // The LaTeX family is four. `.sty` and `.cls` are where a paper keeps the
  // names it invented; `.bst` is not TeX at all but a stack language, and it
  // is here because it shares TeX's braces and its `%` comment, which is most
  // of what stex finds in one.
  ['tex', latexMode],
  ['sty', latexMode],
  ['cls', latexMode],
  ['bst', latexMode],
  ['bib', bibtexMode],
  ['py', pythonMode],
  ['sh', shellMode],
  ['bash', shellMode],
  ['zsh', shellMode],
  ['js', javascriptMode],
  ['mjs', javascriptMode],
  ['cjs', javascriptMode],
  // The mode understands neither JSX nor TSX markup, so tags in a `.tsx` file
  // are read as comparisons. It is still the right dialect for the other
  // nine-tenths of the file, which is more than plain text would give.
  ['jsx', javascriptMode],
  ['ts', typescriptMode],
  ['tsx', typescriptMode],
  ['json', jsonMode],
  ['yaml', yamlMode],
  ['yml', yamlMode],
  ['toml', tomlMode],
  ['css', cssMode],
  ['html', htmlMode],
  ['htm', htmlMode],
  ['xml', xmlMode],
])

/* Keyed by the whole name, for files that carry their type in the name rather
 * than after a dot. `Makefile` belongs here too and is missing for a plain
 * reason: no ported mode covers it, and nor does one cover Markdown or
 * gnuplot, so those three are plain text until a mode for them is a
 * dependency. */
const BY_NAME = new Map<string, Extension>([['dockerfile', dockerfileMode]])

/** The language to parse `path` with, or null when nothing here fits it. */
export function languageFor(path: string | null): Extension | null {
  if (!path) return null
  const name = (path.split('/').pop() ?? '').toLowerCase()
  // A leading dot is part of the name, not a separator: `.gitignore` is a file
  // called `.gitignore`, not a file with a `gitignore` extension.
  const dot = name.lastIndexOf('.')
  const extension = dot > 0 ? name.slice(dot + 1) : ''
  return BY_EXTENSION.get(extension) ?? BY_NAME.get(name) ?? null
}
