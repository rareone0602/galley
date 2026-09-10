import { useEffect, useState } from 'react'
import { api, type FileBody } from '../api'
import { record } from '../usage'
import Json from './preview/Json'
import Markdown from './preview/Markdown'
import Table from './preview/Table'
import { PREVIEW_LABEL, type PreviewKind } from './preview/kinds'

/**
 * The right-hand column when the file you are editing is not the paper.
 *
 * The pane is the PDF for a `.tex`, because LaTeX is what draws one. For the
 * three kinds of file the browser can draw itself — a note, a config, a table
 * of numbers — it is this instead, in the same place, so the source is still
 * on the left and the rendered thing is still on the right.
 *
 * It shows what is in the editor rather than what is on disk. A preview of
 * the last save is a preview of the wrong thing the moment you start typing,
 * and this is the pane you keep open *while* you write.
 */
export default function PreviewPane({
  path,
  kind,
  text,
  reloadKey,
  onOpenFile,
  onShowPdf,
}: {
  path: string
  kind: PreviewKind
  /** The live editor buffer, when the editor has this file. Undefined when it
   *  does not — another tab is open — and then the copy on disk is shown. */
  text?: string
  reloadKey: number
  onOpenFile: (path: string) => void
  /** Put the paper back, for a project where the PDF is the point. */
  onShowPdf: () => void
}) {
  const [body, setBody] = useState<FileBody | null>(null)
  const [error, setError] = useState<string | null>(null)

  /* The disk copy, read once per file rather than per keystroke: it is the
   * fallback for when the editor is not showing this file, and it carries
   * whether the file is text at all. */
  useEffect(() => {
    let stale = false
    setBody(null)
    setError(null)
    api
      .file(path)
      .then((b) => !stale && setBody(b))
      .catch((e) => !stale && setError(String(e)))
    return () => {
      stale = true
    }
  }, [path, reloadKey])

  useEffect(() => {
    record('preview.show', { kind, path })
  }, [kind, path])

  const shown = text ?? body?.content ?? null

  return (
    <section className="pane-column preview-pane">
      <header className="pdf-bar">
        <span className="chip">{PREVIEW_LABEL[kind]}</span>
        <span className="preview-bar-name mono" title={path}>
          {path.split('/').pop()}
        </span>
        {text !== undefined && <span className="muted small">live</span>}
        {body?.truncated && (
          <span
            className="badge amber"
            title={`${body.bytes.toLocaleString()} bytes on disk — too big to open whole`}
          >
            first {Math.round((body.content?.length ?? 0) / 1024).toLocaleString()} KB
          </span>
        )}
        <span className="spacer" />
        <button className="tiny" onClick={onShowPdf} title="Back to the paper">
          PDF ›
        </button>
      </header>

      <div className="preview-body">
        {error && <div className="notice bad">{error}</div>}
        {!error && shown === null && (
          <div className="empty small">
            {body === null ? 'Reading…' : 'There is no text in this file to draw.'}
          </div>
        )}
        {shown !== null && kind === 'markdown' && (
          <Markdown text={shown} path={path} onOpenFile={onOpenFile} />
        )}
        {shown !== null && kind === 'json' && <Json text={shown} />}
        {shown !== null && kind === 'table' && <Table text={shown} path={path} />}
      </div>
    </section>
  )
}
