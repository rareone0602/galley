import type { ReactNode } from 'react'

/**
 * JSON, laid out.
 *
 * Two spaces an indent, one value a line, and the pieces coloured by what
 * they are — which is the whole difference between reading a config and
 * hunting through one line four thousand characters wide.
 *
 * A file that does not parse is not hidden behind an error. The message says
 * where the parser stopped and the source is underneath it, because a preview
 * that goes blank when the file is broken is blank exactly when you need it.
 */

/* Colouring one node at a time costs a React element per value. On a config
 * that is nothing; on a megabyte of exported data it is hundreds of thousands
 * of them, and the pane would lock the tab for seconds. Past this the same
 * text is laid out in one block, uncoloured. */
const COLOUR_LIMIT = 400 * 1024

export default function Json({ text }: { text: string }) {
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch (e) {
    return (
      <div className="json-view">
        <div className="notice bad">{String(e instanceof Error ? e.message : e)}</div>
        <pre className="mono">{text}</pre>
      </div>
    )
  }
  if (text.length > COLOUR_LIMIT) {
    return (
      <div className="json-view">
        <pre className="mono">{JSON.stringify(parsed, null, 2)}</pre>
      </div>
    )
  }
  return (
    <div className="json-view">
      <pre className="mono">{value(parsed, '')}</pre>
    </div>
  )
}

/** One value, and everything under it. `pad` is the indent it sits at. */
function value(v: unknown, pad: string): ReactNode {
  if (v === null) return <span className="j-null">null</span>
  if (typeof v === 'string') return <span className="j-str">{JSON.stringify(v)}</span>
  if (typeof v === 'number') return <span className="j-num">{String(v)}</span>
  if (typeof v === 'boolean') return <span className="j-bool">{String(v)}</span>

  const inner = pad + '  '
  if (Array.isArray(v)) {
    if (v.length === 0) return '[]'
    return (
      <>
        {'['}
        {v.map((item, i) => (
          <span key={i}>
            {'\n' + inner}
            {value(item, inner)}
            {i < v.length - 1 ? ',' : ''}
          </span>
        ))}
        {'\n' + pad + ']'}
      </>
    )
  }

  const entries = Object.entries(v as Record<string, unknown>)
  if (entries.length === 0) return '{}'
  return (
    <>
      {'{'}
      {entries.map(([key, item], i) => (
        <span key={key}>
          {'\n' + inner}
          <span className="j-key">{JSON.stringify(key)}</span>
          {': '}
          {value(item, inner)}
          {i < entries.length - 1 ? ',' : ''}
        </span>
      ))}
      {'\n' + pad + '}'}
    </>
  )
}
