import type { EditorState } from '@codemirror/state'
import type { EditorView } from '@codemirror/view'
import type { FileBody } from '../../api'

/* What you have typed and not saved, kept per file, outside React.
 *
 * The editor is thrown away whenever you leave the Editor tab, and it is
 * re-used for whichever file you open next. Neither is a reason to lose work,
 * so the buffers live here instead of in the component: switching files or
 * tabs becomes a move, and coming back restores the text, the cursor, the undo
 * history and the scroll position exactly as you left them.
 *
 * This module also owns the browser's "leave site?" prompt, because it is the
 * only thing that knows whether anything anywhere is unsaved.
 */

/** What `EditorView.scrollSnapshot()` hands back. The class is not exported. */
export type ScrollSnapshot = ReturnType<EditorView['scrollSnapshot']>

/** What the editor bar says about a file — the same facts whether it is the
 *  file you are looking at or one you left with work in it. */
export type SaveStatus = {
  dirty: boolean
  /** When Galley last wrote the file, and how big it was. */
  savedAt: number | null
  savedBytes: number | null
  /** Something else — a merge, usually — wrote the file under an unsaved
   *  buffer, so what is on disk is no longer what you started from. */
  changedOnDisk: boolean
}

export const CLEAN: SaveStatus = {
  dirty: false,
  savedAt: null,
  savedBytes: null,
  changedOnDisk: false,
}

export type FileBuffer = {
  /** Text, cursor and undo history: everything needed to resume the file.
   *  While a file is open the view holds the live state; this copy is written
   *  when you leave it, and when the editor is taken down. */
  state: EditorState
  /** The text this buffer was based on — the last thing read from or written
   *  to disk. Dirtiness is measured against it, not against the first load. */
  saved: string
  /** A newer text found on disk under an unsaved buffer, kept so the bar's
   *  Reload has something to put in. Null when the two agree. */
  diskText: string | null
  scroll: ScrollSnapshot | null
  status: SaveStatus
  /** The file as the server last described it; the bar needs its type. */
  meta: FileBody
  /** When it was last opened or touched, so clean buffers can be dropped
   *  oldest-first. */
  touched: number
}

const buffers = new Map<string, FileBuffer>()

/** A clean buffer is only a convenience — where you were in a file you have
 *  already visited — so a long session does not keep every one of them, undo
 *  history and all. An unsaved buffer is never dropped. */
const CLEAN_BUFFER_LIMIT = 24

export function bufferFor(path: string): FileBuffer | undefined {
  return buffers.get(path)
}

export function keepBuffer(path: string, buffer: FileBuffer): void {
  buffers.set(path, { ...buffer, touched: Date.now() })
  dropOldCleanBuffers(path)
  syncUnloadGuard()
}

/** Merge into an existing buffer. Silent when there is none: the caller is
 *  reporting on a file that was closed or never finished loading. */
export function updateBuffer(path: string, patch: Partial<FileBuffer>): void {
  const current = buffers.get(path)
  if (!current) return
  buffers.set(path, { ...current, ...patch, touched: Date.now() })
  syncUnloadGuard()
}

/** Every file with work in it, whether or not it is the one on screen. */
export function unsavedPaths(): string[] {
  return [...buffers].filter(([, buffer]) => buffer.status.dirty).map(([path]) => path)
}

function dropOldCleanBuffers(keep: string): void {
  const droppable = [...buffers]
    .filter(([path, buffer]) => path !== keep && !buffer.status.dirty)
    .sort((a, b) => a[1].touched - b[1].touched)
  const excess = Math.max(0, droppable.length - CLEAN_BUFFER_LIMIT)
  for (const [path] of droppable.slice(0, excess)) buffers.delete(path)
}

let guarding = false

/* Browsers ignore a custom message here and show their own, so there is
 * nothing to write; what matters is that the prompt appears at all. */
function warnBeforeUnload(event: BeforeUnloadEvent): void {
  event.preventDefault()
  event.returnValue = ''
}

function syncUnloadGuard(): void {
  const wanted = unsavedPaths().length > 0
  if (wanted === guarding) return
  guarding = wanted
  if (wanted) window.addEventListener('beforeunload', warnBeforeUnload)
  else window.removeEventListener('beforeunload', warnBeforeUnload)
}
