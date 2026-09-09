import { useEffect, useRef, useState } from 'react'

export type Question =
  | { kind: 'file'; parent: string }
  | { kind: 'folder'; parent: string }
  | { kind: 'rename'; path: string }
  | { kind: 'delete'; path: string; folder: boolean; held: number }
  | { kind: 'replace'; parent: string; files: File[] }

/**
 * The one thing the rail ever asks before it changes the project.
 *
 * A panel at the top of the rail rather than a dialogue in the middle of the
 * screen: the question is always about the file you are looking at, and a
 * modal for "what shall I call it" is more ceremony than that deserves.
 *
 * Where it explains a rule — that only an empty folder can go — it is
 * describing what the backend will do, not deciding it. The refusal itself
 * lives in `galley/services/files.py`, so a request made by hand meets it too.
 */
export default function Ask({
  question,
  busy,
  onConfirm,
  onCancel,
}: {
  question: Question
  busy: boolean
  onConfirm: (value: string) => void
  onCancel: () => void
}) {
  const [value, setValue] = useState(() => startingText(question))
  const field = useRef<HTMLInputElement>(null)
  const panel = useRef<HTMLDivElement>(null)

  const typed = question.kind === 'file' || question.kind === 'folder' || question.kind === 'rename'
  const ready = !busy && (!typed || value.trim().length > 0)

  useEffect(() => {
    setValue(startingText(question))
    const input = field.current
    if (!input) {
      // A confirmation focuses the panel rather than its button, so that
      // Escape works and a stray Enter cannot delete anything.
      panel.current?.focus()
      return
    }
    input.focus()
    // Select the stem and leave the suffix alone: a rename is almost always a
    // new name for the same kind of file, in the same folder.
    const stem = input.value.lastIndexOf('/') + 1
    const dot = input.value.lastIndexOf('.')
    input.setSelectionRange(stem, dot > stem ? dot : input.value.length)
  }, [question])

  return (
    <div
      className={`tree-ask${question.kind === 'delete' ? ' danger' : ''}`}
      ref={panel}
      tabIndex={-1}
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.stopPropagation()
          onCancel()
        }
      }}
    >
      <div className="tree-ask-says">{says(question)}</div>
      {typed && (
        <input
          ref={field}
          value={value}
          spellCheck={false}
          aria-label={says(question)}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && ready) onConfirm(value.trim())
          }}
        />
      )}
      <div className="row">
        <button
          className={question.kind === 'delete' ? 'danger' : 'primary'}
          disabled={!ready || blocked(question)}
          onClick={() => onConfirm(value.trim())}
        >
          {verb(question)}
        </button>
        <button onClick={onCancel}>Cancel</button>
      </div>
    </div>
  )
}

function startingText(question: Question): string {
  return question.kind === 'rename' ? question.path : ''
}

function verb(question: Question): string {
  switch (question.kind) {
    case 'file':
    case 'folder':
      return 'Create'
    case 'rename':
      return 'Rename'
    case 'delete':
      return 'Delete'
    case 'replace':
      return 'Replace'
  }
}

/** A folder with things in it is refused, so do not offer the button. */
function blocked(question: Question): boolean {
  return question.kind === 'delete' && question.folder && question.held > 0
}

function inside(parent: string): string {
  return parent ? `in ${parent}/` : 'in the project root'
}

function says(question: Question): string {
  switch (question.kind) {
    case 'file':
      return `New file ${inside(question.parent)}`
    case 'folder':
      return `New folder ${inside(question.parent)}`
    case 'rename':
      return 'New path — change the folder to move it'
    case 'delete':
      if (!question.folder)
        return `Delete ${question.path}? Every version you committed stays in git.`
      return question.held > 0
        ? `${question.path} still holds ${question.held} thing${question.held === 1 ? '' : 's'}. Galley removes only empty folders — delete what is inside first.`
        : `Delete the empty folder ${question.path}?`
    case 'replace':
      return `${listed(question.files)} already ${question.files.length === 1 ? 'is' : 'are'} ${inside(question.parent)}. Replace ${question.files.length === 1 ? 'it' : 'them'}?`
  }
}

function listed(files: File[]): string {
  const names = files.map((f) => f.name)
  return names.length > 3 ? `${names.slice(0, 3).join(', ')} and ${names.length - 3} more` : names.join(', ')
}
