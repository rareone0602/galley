import type { TreeNode } from '../../api'

/**
 * Turning the nested tree into the list you actually see.
 *
 * The rail is drawn as a flat list rather than by recursion, because the
 * arrow keys need to know what the next line is — and "the next line" is a
 * question about the screen, not about the tree.
 */

/** One line of the rail: the node, how deep it sits, and the folder above it. */
export type Row = { node: TreeNode; depth: number; parent: string }

export function flatten(
  nodes: TreeNode[],
  shut: Set<string>,
  depth = 0,
  parent = '',
): Row[] {
  const rows: Row[] = []
  for (const node of nodes) {
    rows.push({ node, depth, parent })
    if (node.type === 'dir' && !shut.has(node.path)) {
      rows.push(...flatten(node.children ?? [], shut, depth + 1, node.path))
    }
  }
  return rows
}

/**
 * Keep what the filter matches.
 *
 * The needle is tried against the whole path, so `sec/in` finds
 * `sections/intro.tex`; and a folder whose path matches keeps everything under
 * it, so filtering by a folder's name shows you the folder rather than
 * nothing. The order the backend sorted into is preserved throughout.
 */
export function prune(nodes: TreeNode[], needle: string): TreeNode[] {
  const kept: TreeNode[] = []
  for (const node of nodes) {
    const hit = node.path.toLowerCase().includes(needle)
    if (node.type !== 'dir') {
      if (hit) kept.push(node)
    } else if (hit) {
      kept.push(node)
    } else {
      const children = prune(node.children ?? [], needle)
      if (children.length) kept.push({ ...node, children })
    }
  }
  return kept
}

/** Files, not folders — a count of folders would not answer "found how many". */
export function countFiles(nodes: TreeNode[]): number {
  let n = 0
  for (const node of nodes) {
    if (node.type === 'dir') n += countFiles(node.children ?? [])
    else n += 1
  }
  return n
}

/** Every path in the tree, for asking "is this name taken" without a round trip. */
export function paths(nodes: TreeNode[], into = new Set<string>()): Set<string> {
  for (const node of nodes) {
    into.add(node.path)
    if (node.type === 'dir') paths(node.children ?? [], into)
  }
  return into
}
