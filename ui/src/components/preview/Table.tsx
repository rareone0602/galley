/**
 * A separated-values file as the table it is.
 *
 * The paper's own numbers arrive this way — one row a cell of the sweep — and
 * a hundred commas in a line is not something you read, it is something you
 * count on your fingers. The first row is the header, which is the convention
 * every file here follows.
 */

/* A table this long is already past what you would read down; the point of
 * the pane is to see the shape of the file, and the footer says what is not
 * on screen rather than pretending the file ends here. */
const MAX_ROWS = 500

/** Split one line, honouring quotes. A field wrapped in `"` may contain the
 *  separator, and `""` inside one is a literal quote — RFC 4180, which is
 *  what pandas and every spreadsheet write. */
function fields(line: string, sep: string): string[] {
  const out: string[] = []
  let field = ''
  let quoted = false
  for (let i = 0; i < line.length; i++) {
    const c = line[i]
    if (quoted) {
      if (c === '"' && line[i + 1] === '"') {
        field += '"'
        i++
      } else if (c === '"') quoted = false
      else field += c
    } else if (c === '"') quoted = true
    else if (c === sep) {
      out.push(field)
      field = ''
    } else field += c
  }
  out.push(field)
  return out
}

/** Whether a column is numbers, so it can be set right-aligned like a table
 *  in the paper. A column of mixed text is left alone. */
const NUMERIC = /^-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?$/

export default function Table({ text, path }: { text: string; path: string }) {
  const sep = path.toLowerCase().endsWith('.tsv') ? '\t' : ','
  const lines = text.split(/\r?\n/).filter((line) => line.length > 0)
  if (lines.length === 0) return <div className="empty small">Nothing in this file yet.</div>

  const header = fields(lines[0], sep)
  const rows = lines.slice(1, MAX_ROWS + 1).map((line) => fields(line, sep))
  const numeric = header.map((_, i) =>
    rows.length > 0 && rows.every((row) => !row[i] || NUMERIC.test(row[i].trim())),
  )

  return (
    <div className="table-view">
      <table>
        <thead>
          <tr>
            {header.map((cell, i) => (
              <th key={i} style={numeric[i] ? { textAlign: 'right' } : undefined}>
                {cell}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, r) => (
            <tr key={r}>
              {header.map((_, i) => (
                <td key={i} style={numeric[i] ? { textAlign: 'right' } : undefined}>
                  {row[i] ?? ''}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="muted small table-foot">
        {rows.length} of {lines.length - 1} rows · {header.length} columns
      </div>
    </div>
  )
}
