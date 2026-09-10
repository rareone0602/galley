/**
 * The review keyboard, named once.
 *
 * The table below is the only place a key is bound to a meaning: the pane
 * dispatches through `actionFor`, the legend prints `shows`, and the buttons
 * put `chordFor` in their tooltips. A key that is not in this table does
 * nothing, and a key in it cannot go missing from the legend.
 *
 * The letters are the ones a review tool teaches you elsewhere — J/K to move,
 * A to accept — with the bulk answers on the same letters held with Shift, so
 * there is one thing to remember rather than two.
 */

export type Action =
  | 'next'
  | 'prev'
  | 'claude'
  | 'keep'
  | 'rewrite'
  | 'claudeAll'
  | 'keepAll'
  | 'undo'
  | 'save'
  | 'help'
  | 'close'

type Binding = {
  action: Action
  /** `KeyboardEvent.key` values; `Mod+x` means Ctrl, or ⌘ on a Mac. */
  chords: string[]
  says: string
}

export const BINDINGS: Binding[] = [
  { action: 'next', chords: ['j', 'n'], says: 'next change' },
  { action: 'prev', chords: ['k', 'p'], says: 'previous change' },
  { action: 'claude', chords: ['a'], says: "take Claude's wording, then step on" },
  { action: 'keep', chords: ['r'], says: 'keep your wording, then step on' },
  { action: 'rewrite', chords: ['e'], says: 'write your own wording instead (or click into the text)' },
  { action: 'claudeAll', chords: ['A'], says: "take Claude's for this whole file, leaving your rewrites" },
  { action: 'keepAll', chords: ['R'], says: 'keep yours for this whole file, leaving your rewrites' },
  { action: 'undo', chords: ['u', 'Mod+z'], says: 'undo the last decision' },
  { action: 'save', chords: ['Mod+s'], says: 'show what Save would write' },
  { action: 'help', chords: ['?'], says: 'this list' },
  { action: 'close', chords: ['Escape'], says: 'close this list, or leave a rewrite' },
]

const MOD = 'Mod+'

function matches(chord: string, event: KeyboardEvent): boolean {
  const wantsMod = chord.startsWith(MOD)
  if (event.altKey) return false
  if (wantsMod !== (event.ctrlKey || event.metaKey)) return false
  return event.key === (wantsMod ? chord.slice(MOD.length) : chord)
}

export function actionFor(event: KeyboardEvent): Action | null {
  for (const binding of BINDINGS) {
    if (binding.chords.some((chord) => matches(chord, event))) return binding.action
  }
  return null
}

/** How a chord is printed: `A`, `⇧A`, `Ctrl S`. */
function shows(chord: string): string {
  if (chord.startsWith(MOD)) return `Ctrl ${chord.slice(MOD.length).toUpperCase()}`
  if (chord.length === 1 && chord !== chord.toLowerCase()) return `⇧${chord}`
  if (chord.length === 1) return chord.toUpperCase()
  return chord
}

/** The first chord for an action, for a button's tooltip. */
export function chordFor(action: Action): string {
  const binding = BINDINGS.find((b) => b.action === action)
  return binding ? shows(binding.chords[0]) : ''
}

/** Whether a key press belongs to whatever the user is typing into. */
export function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  if (!el || !el.tagName) return false
  return (
    el.isContentEditable ||
    el.tagName === 'INPUT' ||
    el.tagName === 'TEXTAREA' ||
    el.tagName === 'SELECT'
  )
}

export default function Shortcuts({ onClose }: { onClose: () => void }) {
  return (
    <div className="shortcuts" role="dialog" aria-label="Keyboard shortcuts">
      <div className="row">
        <strong>Keyboard</strong>
        <span className="grow" />
        <button className="tiny" onClick={onClose}>
          Close
        </button>
      </div>
      <table>
        <tbody>
          {BINDINGS.map((binding) => (
            <tr key={binding.action}>
              <td className="keys">
                {binding.chords.map((chord) => (
                  <kbd key={chord}>{shows(chord)}</kbd>
                ))}
              </td>
              <td>{binding.says}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="muted small">
        Ctrl is ⌘ on a Mac. Letters are ignored while you are typing a rewrite;
        Ctrl S still works there. You can also click into either side of a
        change and type. Nothing here writes to the paper — only Save does.
      </div>
    </div>
  )
}
