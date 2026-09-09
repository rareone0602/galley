/**
 * Which folders are shut, remembered per project.
 *
 * What is stored is the shut set rather than the open one. A folder Galley has
 * never seen should be open — that is how Overleaf shows a project you have
 * just opened — and remembering the open set instead would make every folder
 * you add later start closed.
 *
 * The project is keyed by its path on disk, which is what `/api/tree` answers
 * with, so two papers never share a fold.
 */

const KEY = 'galley.tree.shut:'

/** A fold state and the project it belongs to, so a save cannot cross them. */
export type Folds = { project: string; shut: Set<string> }

export function loadFolds(project: string): Folds {
  try {
    const stored = localStorage.getItem(KEY + project)
    const parsed: unknown = stored ? JSON.parse(stored) : []
    const shut = Array.isArray(parsed) ? parsed.filter((p): p is string => typeof p === 'string') : []
    return { project, shut: new Set(shut) }
  } catch {
    // A private window throws on the first read. Folds are a convenience, and
    // losing them is not worth showing anybody an error.
    return { project, shut: new Set() }
  }
}

export function saveFolds({ project, shut }: Folds): void {
  if (!project) return
  try {
    localStorage.setItem(KEY + project, JSON.stringify([...shut]))
  } catch {
    /* nothing to do: the folds simply will not survive this reload */
  }
}
