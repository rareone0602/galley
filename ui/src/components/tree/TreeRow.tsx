import type { TreeNode } from '../../api'
import type { Row } from './rows'

/** One line of the rail. Everything it knows, it is told. */
export default function TreeRow({
  row,
  open,
  cursor,
  shut,
  dirty,
  dropTarget,
  onPick,
  onContext,
  onDragInto,
}: {
  row: Row
  open: boolean
  cursor: boolean
  shut: boolean
  dirty: boolean
  dropTarget: boolean
  onPick: () => void
  onContext: (e: React.MouseEvent) => void
  onDragInto: () => void
}) {
  const { node, depth } = row
  const isDir = node.type === 'dir'
  const marks = [
    'fnode',
    isDir ? 'dir' : '',
    open ? 'on' : '',
    cursor ? 'cursor' : '',
    dropTarget ? 'droptarget' : '',
  ]

  return (
    <div
      className={marks.filter(Boolean).join(' ')}
      data-path={node.path}
      style={{ paddingLeft: 8 + depth * 13 }}
      onClick={onPick}
      onContextMenu={onContext}
      onDragOver={(e) => {
        // The row claims the drag before the rail does, so the file lands in
        // the folder under the pointer rather than at the project root.
        e.preventDefault()
        e.stopPropagation()
        onDragInto()
      }}
      title={node.path}
      role="treeitem"
      aria-selected={open}
      aria-expanded={isDir ? !shut : undefined}
    >
      <span className="twist">{isDir ? (shut ? '▸' : '▾') : ''}</span>
      <Icon type={node.type} />
      <span className="fname">{node.name}</span>
      {dirty && <span className="dirty" title="unsaved changes" />}
    </div>
  )
}

/** Overleaf marks file types by icon; these are the same four distinctions. */
function Icon({ type }: { type: TreeNode['type'] }) {
  const glyph =
    type === 'dir' ? '\u{1f4c1}' : type === 'image' ? '\u{1f5bc}' : type === 'figure' ? '\u{1f4c4}' : type === 'tex' ? '\u{1d413}' : '•'
  return <span className={`ficon ${type}`}>{glyph}</span>
}
