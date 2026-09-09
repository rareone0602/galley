import { useEffect, useMemo, useState } from 'react'
import { api, type TreeNode } from '../api'

/**
 * The project rail, the way Overleaf's file tree works: folders you can fold,
 * a click opens the file, the open one stays marked.
 *
 * What is *in* the project is git's answer, not the filesystem's — so build
 * artefacts, `.worktrees/`, and anything `.gitignore` covers never appear here.
 */
export default function FileTree({
  open,
  onOpen,
  reloadKey,
  dirty,
}: {
  open: string | null
  onOpen: (path: string) => void
  reloadKey: number
  dirty: Set<string>
}) {
  const [tree, setTree] = useState<TreeNode[]>([])
  const [error, setError] = useState<string | null>(null)
  const [folded, setFolded] = useState<Set<string>>(new Set())
  const [filter, setFilter] = useState('')

  useEffect(() => {
    api
      .tree()
      .then((b) => {
        setTree(b.tree)
        setError(null)
      })
      .catch((e) => setError(String(e)))
  }, [reloadKey])

  const shown = useMemo(() => (filter.trim() ? prune(tree, filter.toLowerCase()) : tree), [tree, filter])

  return (
    <div className="filetree">
      <div className="filetree-search">
        <input
          value={filter}
          placeholder="Filter files"
          onChange={(e) => setFilter(e.target.value)}
        />
      </div>
      <div className="scroll">
        {error && <div className="notice bad">{error}</div>}
        {!error && shown.length === 0 && (
          <div className="empty small">{filter ? 'Nothing matches.' : 'Empty project.'}</div>
        )}
        {shown.map((node) => (
          <Row
            key={node.path}
            node={node}
            depth={0}
            open={open}
            onOpen={onOpen}
            folded={filter ? new Set() : folded}
            setFolded={setFolded}
            dirty={dirty}
          />
        ))}
      </div>
    </div>
  )
}

function Row({
  node,
  depth,
  open,
  onOpen,
  folded,
  setFolded,
  dirty,
}: {
  node: TreeNode
  depth: number
  open: string | null
  onOpen: (path: string) => void
  folded: Set<string>
  setFolded: (f: (prev: Set<string>) => Set<string>) => void
  dirty: Set<string>
}) {
  const isDir = node.type === 'dir'
  const shut = folded.has(node.path)
  const pad = { paddingLeft: 8 + depth * 13 }

  function toggle() {
    setFolded((prev) => {
      const next = new Set(prev)
      next.has(node.path) ? next.delete(node.path) : next.add(node.path)
      return next
    })
  }

  return (
    <>
      <div
        className={`fnode${node.path === open ? ' on' : ''}${isDir ? ' dir' : ''}`}
        style={pad}
        onClick={() => (isDir ? toggle() : onOpen(node.path))}
        title={node.path}
      >
        <span className="twist">{isDir ? (shut ? '▸' : '▾') : ''}</span>
        <Icon type={node.type} />
        <span className="fname">{node.name}</span>
        {dirty.has(node.path) && <span className="dirty" title="unsaved changes" />}
      </div>
      {isDir &&
        !shut &&
        (node.children ?? []).map((child) => (
          <Row
            key={child.path}
            node={child}
            depth={depth + 1}
            open={open}
            onOpen={onOpen}
            folded={folded}
            setFolded={setFolded}
            dirty={dirty}
          />
        ))}
    </>
  )
}

/** Overleaf marks file types by icon; these are the same four distinctions. */
function Icon({ type }: { type: TreeNode['type'] }) {
  const glyph =
    type === 'dir' ? '\u{1f4c1}' : type === 'image' ? '\u{1f5bc}' : type === 'figure' ? '\u{1f4c4}' : type === 'tex' ? '\u{1d413}' : '•'
  return <span className={`ficon ${type}`}>{glyph}</span>
}

/** Keep a folder only if something under it matches the filter. */
function prune(nodes: TreeNode[], needle: string): TreeNode[] {
  const out: TreeNode[] = []
  for (const node of nodes) {
    if (node.type === 'dir') {
      const children = prune(node.children ?? [], needle)
      if (children.length) out.push({ ...node, children })
    } else if (node.path.toLowerCase().includes(needle)) {
      out.push(node)
    }
  }
  return out
}
