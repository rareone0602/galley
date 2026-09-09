import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, type TreeNode } from '../api'
import { record } from '../usage'
import Ask, { type Question } from './tree/Ask'
import ContextMenu, { type MenuItem } from './tree/ContextMenu'
import TreeRow from './tree/TreeRow'
import { loadFolds, saveFolds, type Folds } from './tree/folds'
import { countFiles, flatten, paths, prune, type Row } from './tree/rows'

/**
 * The project rail, the way Overleaf's file tree works: folders you can fold,
 * a click opens the file, the open one stays marked — and the four things
 * Overleaf's rail can do to a project, which are create, rename, delete and
 * take an upload.
 *
 * What is *in* the project is git's answer, not the filesystem's — so build
 * artefacts, `.worktrees/`, and anything `.gitignore` covers never appear here,
 * and a name `.gitignore` would cover is refused rather than created.
 *
 * Every refusal comes from the backend. Where the rail explains one before it
 * asks — that an unsaved file should be saved first, that only an empty folder
 * can go — it is describing what will happen, not deciding it.
 */
export default function FileTree({
  open,
  onOpen,
  reloadKey,
  dirty,
  onRenamed,
  onDeleted,
}: {
  open: string | null
  onOpen: (path: string) => void
  reloadKey: number
  dirty: Set<string>
  /** The editor has to follow a file that moved out from under it. */
  onRenamed?: (from: string, to: string) => void
  /** ...and let go of one that is no longer there. */
  onDeleted?: (path: string) => void
}) {
  const [tree, setTree] = useState<TreeNode[]>([])
  const [error, setError] = useState<string | null>(null)
  const [folds, setFolds] = useState<Folds>({ project: '', shut: new Set() })
  const [filter, setFilter] = useState('')
  const [cursor, setCursor] = useState<string | null>(null)
  const [question, setQuestion] = useState<Question | null>(null)
  const [menu, setMenu] = useState<{ x: number; y: number; row: Row | null } | null>(null)
  const [busy, setBusy] = useState(false)
  const [dropInto, setDropInto] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)

  const scroll = useRef<HTMLDivElement>(null)
  const picker = useRef<HTMLInputElement>(null)
  const typed = useRef({ text: '', at: 0 })

  const refresh = useCallback(() => setNonce((n) => n + 1), [])

  useEffect(() => {
    api
      .tree()
      .then((body) => {
        setTree(body.tree)
        setError(null)
        // The folds belong to the project the server is serving, so they are
        // loaded when that changes rather than on every reload of the tree.
        setFolds((was) => (was.project === body.root ? was : loadFolds(body.root)))
      })
      .catch((e) => {
        record('error.shown', { where: 'rail', reason: 'tree' })
        setError(String(e))
      })
  }, [reloadKey, nonce])

  useEffect(() => saveFolds(folds), [folds])

  const needle = filter.trim().toLowerCase()
  const total = useMemo(() => countFiles(tree), [tree])
  const shown = useMemo(() => (needle ? prune(tree, needle) : tree), [tree, needle])
  const found = useMemo(() => (needle ? countFiles(shown) : total), [shown, needle, total])
  // Filtering opens everything: leaving a match behind a fold would be a lie.
  const visible = useMemo(
    () => flatten(shown, needle ? new Set<string>() : folds.shut),
    [shown, needle, folds.shut],
  )
  const taken = useMemo(() => paths(tree), [tree])

  /* One record per query you settle on, not one per keystroke. What is worth
   * knowing is whether the filter is reached for and whether it finds
   * anything; a letter at a time would be a transcript of your typing. */
  useEffect(() => {
    if (!needle) return
    const settled = window.setTimeout(() => record('rail.filter', { matched: found }), 800)
    return () => window.clearTimeout(settled)
  }, [needle, found])

  // Also on the row count, so a file you have just made is scrolled to once
  // the reloaded tree contains it — but not on a plain reload after a save,
  // which would yank the rail back from wherever you had scrolled it.
  useEffect(() => {
    if (!cursor) return
    scroll.current
      ?.querySelector(`[data-path="${CSS.escape(cursor)}"]`)
      ?.scrollIntoView({ block: 'nearest' })
  }, [cursor, visible.length])

  const rowAt = (path: string | null) => visible.find((r) => r.node.path === path) ?? null

  /** Where a new file goes: the folder you are on, or the one holding you. */
  function targetFolder(row: Row | null = rowAt(cursor)): string {
    if (!row) return ''
    return row.node.type === 'dir' ? row.node.path : row.parent
  }

  function setFold(path: string, wantShut: boolean) {
    setFolds((was) => {
      const shut = new Set(was.shut)
      if (wantShut) shut.add(path)
      else shut.delete(path)
      return { project: was.project, shut }
    })
  }

  /** Run one change to the project, and say plainly if it would not go. */
  async function act<T>(work: () => Promise<T>): Promise<T | null> {
    setBusy(true)
    try {
      const done = await work()
      setError(null)
      refresh()
      return done
    } catch (e) {
      record('error.shown', { where: 'rail', reason: 'change' })
      setError(e instanceof Error ? e.message : String(e))
      return null
    } finally {
      setBusy(false)
    }
  }

  /**
   * Uploads go one at a time and every failure is reported: dropping four
   * figures and being told about only the first would hide the other three.
   */
  async function send(parent: string, list: File[], replace = false) {
    setBusy(true)
    const failed: string[] = []
    for (const file of list) {
      try {
        await api.uploadFile(parent, file, replace)
      } catch (e) {
        failed.push(e instanceof Error ? e.message : String(e))
      }
    }
    setBusy(false)
    record('rail.action', { action: 'upload', ok: failed.length === 0 })
    if (failed.length) record('error.shown', { where: 'rail', reason: 'upload' })
    setError(failed.length ? failed.join(' · ') : null)
    refresh()
  }

  /** Anything already there is asked about once, rather than overwritten. */
  function upload(parent: string, list: File[]) {
    if (!list.length) return
    const clashing = list.filter((f) => taken.has(parent ? `${parent}/${f.name}` : f.name))
    const fresh = list.filter((f) => !clashing.includes(f))
    if (fresh.length) void send(parent, fresh)
    if (clashing.length) setQuestion({ kind: 'replace', parent, files: clashing })
  }

  function askRename(row: Row) {
    if (dirty.has(row.node.path)) {
      record('error.shown', { where: 'rail', reason: 'unsaved' })
      setError(`${row.node.path} has unsaved changes — save it before moving it.`)
      return
    }
    setQuestion({ kind: 'rename', path: row.node.path })
  }

  function askDelete(row: Row) {
    if (dirty.has(row.node.path)) {
      record('error.shown', { where: 'rail', reason: 'unsaved' })
      setError(`${row.node.path} has unsaved changes — save it before deleting it.`)
      return
    }
    setQuestion({
      kind: 'delete',
      path: row.node.path,
      folder: row.node.type === 'dir',
      held: row.node.children?.length ?? 0,
    })
  }

  async function answer(value: string) {
    const asked = question
    if (!asked) return
    setQuestion(null)

    if (asked.kind === 'file' || asked.kind === 'folder') {
      const made = await act(() =>
        asked.kind === 'folder'
          ? api.createFolder(asked.parent, value)
          : api.createFile(asked.parent, value),
      )
      // The question's own name for what it is doing, so the log and the panel
      // that asked cannot drift apart. A replace is an upload, and says so in
      // `send`, which is where it ends up.
      record('rail.action', { action: asked.kind, ok: made !== null })
      if (!made) return
      setFold(asked.parent, false)
      setCursor(made.path)
      if (asked.kind === 'file') onOpen(made.path)
      return
    }

    if (asked.kind === 'rename') {
      const moved = await act(() => api.renameFile(asked.path, value))
      record('rail.action', { action: asked.kind, ok: moved !== null })
      if (!moved) return
      setCursor(moved.path)
      if (onRenamed) onRenamed(moved.was, moved.path)
      else if (moved.was === open) onOpen(moved.path)
      return
    }

    if (asked.kind === 'delete') {
      // Worked out first: once the row has gone there is nothing to be beside.
      const next = neighbour(visible, asked.path)
      const gone = await act(() => api.deleteFile(asked.path))
      record('rail.action', { action: asked.kind, ok: gone !== null })
      if (!gone) return
      setCursor(next?.node.path ?? null)
      if (asked.path !== open) return
      if (onDeleted) onDeleted(asked.path)
      else if (next && next.node.type !== 'dir') onOpen(next.node.path)
      return
    }

    void send(asked.parent, asked.files, true)
  }

  function menuFor(row: Row | null): MenuItem[] {
    const parent = targetFolder(row)
    const items: MenuItem[] = [
      { label: 'New file', run: () => setQuestion({ kind: 'file', parent }) },
      { label: 'New folder', run: () => setQuestion({ kind: 'folder', parent }) },
    ]
    if (row) {
      items.push({ label: 'Rename', hint: 'F2', run: () => askRename(row) })
      items.push({ label: 'Delete', hint: 'Del', danger: true, run: () => askDelete(row) })
    }
    return items
  }

  /** Typing letters jumps to a file, the way every file browser does. */
  function jump(key: string, at: number) {
    const now = Date.now()
    // A pause starts a new word; otherwise the letters build up, so "int"
    // finds intro.tex rather than everything beginning with t.
    typed.current = {
      text: now - typed.current.at > 700 ? key : typed.current.text + key,
      at: now,
    }
    const want = typed.current.text.toLowerCase()
    // One letter on its own cycles through the files that start with it.
    const from = want.length === 1 ? at + 1 : Math.max(at, 0)
    for (let step = 0; step < visible.length; step++) {
      const row = visible[(from + step + visible.length) % visible.length]
      if (row.node.name.toLowerCase().startsWith(want)) {
        setCursor(row.node.path)
        return
      }
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (question || !visible.length) return
    const at = visible.findIndex((r) => r.node.path === cursor)
    const row = at >= 0 ? visible[at] : null
    const move = (to: number) => {
      e.preventDefault()
      setCursor(visible[Math.max(0, Math.min(visible.length - 1, to))].node.path)
    }

    switch (e.key) {
      case 'ArrowDown':
        return move(at + 1)
      case 'ArrowUp':
        return move(at < 0 ? visible.length - 1 : at - 1)
      case 'ArrowRight':
        if (row?.node.type === 'dir' && folds.shut.has(row.node.path)) {
          e.preventDefault()
          return setFold(row.node.path, false)
        }
        return move(at + 1)
      case 'ArrowLeft':
        if (row?.node.type === 'dir' && !folds.shut.has(row.node.path)) {
          e.preventDefault()
          return setFold(row.node.path, true)
        }
        if (row?.parent) {
          e.preventDefault()
          return setCursor(row.parent)
        }
        return
      case 'Enter':
        if (!row) return
        e.preventDefault()
        if (row.node.type === 'dir') return setFold(row.node.path, !folds.shut.has(row.node.path))
        return onOpen(row.node.path)
      case 'F2':
        if (row) {
          e.preventDefault()
          askRename(row)
        }
        return
      case 'Delete':
      case 'Backspace':
        if (row) {
          e.preventDefault()
          askDelete(row)
        }
        return
      case 'Escape':
        e.preventDefault()
        if (filter) return setFilter('')
        return setCursor(null)
    }

    if (e.key.length === 1 && !e.metaKey && !e.ctrlKey && !e.altKey) {
      e.preventDefault()
      jump(e.key, at)
    }
  }

  const dragging = dropInto !== null

  return (
    <div
      className={`filetree${dragging ? ' dropping' : ''}`}
      onDragOver={(e) => {
        if (!e.dataTransfer.types.includes('Files')) return
        e.preventDefault()
        setDropInto('')
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDropInto(null)
      }}
      onDrop={(e) => {
        e.preventDefault()
        const parent = dropInto ?? ''
        setDropInto(null)
        upload(parent, [...e.dataTransfer.files])
      }}
    >
      <div className="filetree-tools">
        <button
          className="tool"
          title="New file"
          aria-label="New file"
          disabled={busy}
          onClick={() => setQuestion({ kind: 'file', parent: targetFolder() })}
        >
          {'\u{1f4c4}'}
        </button>
        <button
          className="tool"
          title="New folder"
          aria-label="New folder"
          disabled={busy}
          onClick={() => setQuestion({ kind: 'folder', parent: targetFolder() })}
        >
          {'\u{1f4c1}'}
        </button>
        <button
          className="tool"
          title="Upload a file — or drop one anywhere on the rail"
          aria-label="Upload a file"
          disabled={busy}
          onClick={() => picker.current?.click()}
        >
          ↑
        </button>
        <input
          ref={picker}
          type="file"
          multiple
          style={{ display: 'none' }}
          onChange={(e) => {
            const list = [...(e.target.files ?? [])]
            e.target.value = '' // so choosing the same file twice still fires
            upload(targetFolder(), list)
          }}
        />
      </div>

      <div className="filetree-search">
        <input
          value={filter}
          placeholder="Filter files"
          spellCheck={false}
          onChange={(e) => setFilter(e.target.value)}
          onKeyDown={(e) => e.key === 'Escape' && setFilter('')}
        />
      </div>

      {needle && (
        <div className="filetree-count">
          {found} of {total} file{total === 1 ? '' : 's'}
        </div>
      )}
      {dragging && (
        <div className="filetree-count">
          Drop to add to {dropInto ? `${dropInto}/` : 'the project'}
        </div>
      )}

      {question && (
        <Ask
          question={question}
          busy={busy}
          onConfirm={answer}
          onCancel={() => setQuestion(null)}
        />
      )}
      {error && <div className="notice bad">{error}</div>}

      <div
        className="scroll"
        ref={scroll}
        tabIndex={0}
        role="tree"
        aria-label="Project files"
        onKeyDown={onKeyDown}
        onContextMenu={(e) => {
          e.preventDefault()
          setMenu({ x: e.clientX, y: e.clientY, row: null })
        }}
      >
        {!error && visible.length === 0 && (
          <div className="empty small">{needle ? 'Nothing matches.' : 'Empty project.'}</div>
        )}
        {visible.map((row) => (
          <TreeRow
            key={row.node.path}
            row={row}
            open={row.node.path === open}
            cursor={row.node.path === cursor}
            shut={folds.shut.has(row.node.path)}
            dirty={dirty.has(row.node.path)}
            dropTarget={dragging && row.node.type === 'dir' && dropInto === row.node.path}
            onPick={() => {
              setCursor(row.node.path)
              if (row.node.type === 'dir') setFold(row.node.path, !folds.shut.has(row.node.path))
              else onOpen(row.node.path)
            }}
            onContext={(e) => {
              e.preventDefault()
              e.stopPropagation()
              setCursor(row.node.path)
              setMenu({ x: e.clientX, y: e.clientY, row })
            }}
            onDragInto={() => setDropInto(targetFolder(row))}
          />
        ))}
      </div>

      {menu && (
        <ContextMenu x={menu.x} y={menu.y} items={menuFor(menu.row)} onClose={() => setMenu(null)} />
      )}
    </div>
  )
}

/** The row that should hold the cursor once this one has gone. */
function neighbour(rows: Row[], path: string): Row | null {
  const at = rows.findIndex((r) => r.node.path === path)
  if (at < 0) return null
  return rows[at + 1] ?? rows[at - 1] ?? null
}
