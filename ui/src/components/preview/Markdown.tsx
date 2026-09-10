import type { ReactNode } from 'react'
import { marked, type Token, type Tokens } from 'marked'
import { api } from '../../api'

/**
 * Markdown, drawn.
 *
 * The whole file goes through `marked`'s lexer and then straight to React
 * elements — the HTML string that library usually produces is never made, and
 * nothing here calls `dangerouslySetInnerHTML`. That is not caution for its
 * own sake. Galley's page can write to the paper through its own API, and it
 * is same-origin with every file it shows, so one `<script>` in a `.md` that
 * arrived with a downloaded template would be running as you. Rendering
 * tokens instead of markup makes that impossible by construction rather than
 * by a filter somebody has to keep correct.
 *
 * Raw HTML in the source is therefore shown as the text it is. Seeing the tag
 * is a truer preview than quietly dropping it.
 */

/** What every branch below needs: where the file is, and how to leave it. */
type Ctx = {
  /** The folder the file is in, so `figures/loss.png` means the right one. */
  base: string
  onOpenFile?: (path: string) => void
}

/** Only a scheme a browser can follow harmlessly. `javascript:` is a script
 *  that runs with Galley's own privileges, and React will happily render it. */
const SAFE_SCHEME = /^(?:https?|mailto):/i
const HAS_SCHEME = /^[a-z][a-z0-9+.-]*:/i

/** The project file a relative link points at, or null when it leaves the
 *  project — `../` past the root, an anchor, or an address on the web. */
function projectPath(base: string, href: string): string | null {
  if (!href || HAS_SCHEME.test(href) || href.startsWith('//') || href.startsWith('#')) {
    return null
  }
  const from = href.startsWith('/') ? href.slice(1) : base ? `${base}/${href}` : href
  const out: string[] = []
  for (const part of from.split('?')[0].split('#')[0].split('/')) {
    if (!part || part === '.') continue
    if (part === '..') {
      if (out.length === 0) return null
      out.pop()
      continue
    }
    out.push(part)
  }
  return out.length > 0 ? out.join('/') : null
}

export default function Markdown({
  text,
  path,
  onOpenFile,
}: {
  text: string
  path: string
  onOpenFile?: (path: string) => void
}) {
  const ctx: Ctx = { base: path.split('/').slice(0, -1).join('/'), onOpenFile }
  return <div className="md">{blocks(marked.lexer(text), ctx)}</div>
}

function blocks(tokens: Token[], ctx: Ctx): ReactNode[] {
  return tokens.map((token, i) => block(token, ctx, i))
}

function block(token: Token, ctx: Ctx, key: number): ReactNode {
  switch (token.type) {
    case 'space':
    case 'def':
      return null

    // marked hands a ticked box back twice: once as the item's own `task` and
    // `checked`, which is where the input below comes from, and once as a
    // token in the item's contents. Drawing both would put an empty paragraph
    // under every task.
    case 'checkbox':
      return null

    case 'heading': {
      const t = token as Tokens.Heading
      const Tag = `h${Math.min(6, t.depth)}` as 'h1'
      return <Tag key={key}>{inline(t.tokens, ctx)}</Tag>
    }

    case 'paragraph':
      return <p key={key}>{inline((token as Tokens.Paragraph).tokens, ctx)}</p>

    case 'text': {
      const t = token as Tokens.Text
      return <p key={key}>{t.tokens ? inline(t.tokens, ctx) : t.text}</p>
    }

    case 'code': {
      const t = token as Tokens.Code
      return (
        <pre key={key} className="md-code">
          <code data-lang={t.lang || undefined}>{t.text}</code>
        </pre>
      )
    }

    case 'blockquote':
      return <blockquote key={key}>{blocks((token as Tokens.Blockquote).tokens, ctx)}</blockquote>

    case 'hr':
      return <hr key={key} />

    case 'list': {
      const t = token as Tokens.List
      /* A tight list — no blank line between the items — is one line each,
       * and wrapping each line in a paragraph would break it onto its own
       * line under the bullet. Markdown calls the other kind loose, and there
       * the paragraphs are what the author asked for. */
      const items = t.items.map((item, i) => (
        <li key={i} className={item.task ? 'md-task' : undefined}>
          {item.task && <input type="checkbox" checked={!!item.checked} readOnly />}
          {t.loose ? blocks(item.tokens, ctx) : tight(item.tokens, ctx)}
        </li>
      ))
      return t.ordered ? (
        <ol key={key} start={typeof t.start === 'number' ? t.start : undefined}>
          {items}
        </ol>
      ) : (
        <ul key={key}>{items}</ul>
      )
    }

    case 'table': {
      const t = token as Tokens.Table
      const align = (i: number) => t.align[i] ?? undefined
      return (
        <div key={key} className="md-table-wrap">
          <table>
            <thead>
              <tr>
                {t.header.map((cell, i) => (
                  <th key={i} style={{ textAlign: align(i) }}>
                    {inline(cell.tokens, ctx)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {t.rows.map((row, r) => (
                <tr key={r}>
                  {row.map((cell, i) => (
                    <td key={i} style={{ textAlign: align(i) }}>
                      {inline(cell.tokens, ctx)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )
    }

    case 'html':
      return (
        <pre key={key} className="md-html" title="raw HTML, shown rather than run">
          {(token as Tokens.HTML).text}
        </pre>
      )

    default:
      // An inline token the lexer put at the top level, and anything a future
      // version of marked adds. Its own text is a better answer than nothing.
      return <p key={key}>{'text' in token ? String(token.text) : null}</p>
  }
}

/** The contents of a tight list item: its one paragraph unwrapped, and
 *  anything else — a nested list, a code block — left as the block it is. */
function tight(tokens: Token[], ctx: Ctx): ReactNode[] {
  return tokens.map((token, i) => {
    if (token.type !== 'text' && token.type !== 'paragraph') return block(token, ctx, i)
    const t = token as Tokens.Text
    return <span key={i}>{t.tokens ? inline(t.tokens, ctx) : t.text}</span>
  })
}

function inline(tokens: Token[] | undefined, ctx: Ctx): ReactNode[] {
  return (tokens ?? []).map((token, i) => piece(token, ctx, i))
}

function piece(token: Token, ctx: Ctx, key: number): ReactNode {
  switch (token.type) {
    case 'text':
    case 'escape':
      return (token as Tokens.Text).text

    case 'strong':
      return <strong key={key}>{inline((token as Tokens.Strong).tokens, ctx)}</strong>

    case 'em':
      return <em key={key}>{inline((token as Tokens.Em).tokens, ctx)}</em>

    case 'del':
      return <del key={key}>{inline((token as Tokens.Del).tokens, ctx)}</del>

    case 'codespan':
      return <code key={key}>{(token as Tokens.Codespan).text}</code>

    case 'br':
      return <br key={key} />

    case 'link': {
      const t = token as Tokens.Link
      const inside = projectPath(ctx.base, t.href)
      if (inside) {
        // A link to another file in the project opens it, which is what you
        // want a README's links to do. Rendered as a button so a click that
        // cannot work — no handler — is not offered as a link that does.
        return ctx.onOpenFile ? (
          <button
            key={key}
            className="md-link"
            title={inside}
            onClick={() => ctx.onOpenFile?.(inside)}
          >
            {inline(t.tokens, ctx)}
          </button>
        ) : (
          <span key={key}>{inline(t.tokens, ctx)}</span>
        )
      }
      if (!SAFE_SCHEME.test(t.href)) return <span key={key}>{inline(t.tokens, ctx)}</span>
      return (
        <a key={key} href={t.href} title={t.title ?? undefined} target="_blank" rel="noreferrer">
          {inline(t.tokens, ctx)}
        </a>
      )
    }

    case 'image': {
      const t = token as Tokens.Image
      const inside = projectPath(ctx.base, t.href)
      // A figure in the repository is served by Galley; anything else has to
      // be a plain web address, or it is not drawn at all.
      const src = inside ? api.blobUrl(inside) : SAFE_SCHEME.test(t.href) ? t.href : null
      if (!src) return <span key={key}>{t.text}</span>
      return <img key={key} src={src} alt={t.text} title={t.title ?? undefined} />
    }

    case 'html':
      return <span key={key} className="md-html-inline">{(token as Tokens.HTML).text}</span>

    default:
      return 'text' in token ? String(token.text) : null
  }
}
