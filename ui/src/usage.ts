/* What you did with the workbench, recorded so it can be made better.
 *
 * Three promises this module has to keep, in this order.
 *
 * It never breaks the editor. Every call is wrapped, every failure is
 * swallowed, and nothing here is awaited by anything you can see. A telemetry
 * bug that stops you typing would be worse than no telemetry at all.
 *
 * It never changes what it measures. Interactions are queued and posted in
 * batches on a timer, so a click costs nothing; a request per click would make
 * the UI slower and the numbers a description of the instrument.
 *
 * It never records prose. Call sites pass shapes — a path, a count, a tab
 * name — never a sentence. The server scrubs anything that slips through, so
 * this is a courtesy rather than the guarantee, but it is where the habit
 * lives.
 *
 * The list of kinds is the server's (`galley/services/usage.py`), not a second
 * copy here. A kind it does not know is bounced back and warned about in the
 * console, so a typo shows up at the first click rather than as a silent gap
 * in the record months later.
 */

type Detail = Record<string, string | number | boolean | null | undefined>
type Entry = { kind: string; at: number; detail?: Detail }

const FLUSH_MS = 5000
const MAX_QUEUE = 60

let enabled = false
let queue: Entry[] = []
let timer: number | null = null
let warned = new Set<string>()

/** Turn recording on or off. The server decides; `[usage] enabled` in the
 *  config is the switch, and the answer arrives with the rest of it. */
export function configure(on: boolean): void {
  enabled = on
  if (!on) queue = []
}

/** Record one thing that happened. Cheap, and safe to call from anywhere. */
export function record(kind: string, detail?: Detail): void {
  if (!enabled) return
  try {
    queue.push({ kind, at: Date.now() / 1000, detail })
    if (queue.length >= MAX_QUEUE) void flush()
    else schedule()
  } catch {
    /* recording must never be the thing that breaks */
  }
}

/** Time something, and record how long it took.
 *
 *  Returns the function that ends it; extra detail known only at the end — a
 *  build's success, a review's size — goes in there. */
export function timed(kind: string, detail?: Detail): (extra?: Detail) => void {
  const started = performance.now()
  let done = false
  return (extra?: Detail) => {
    if (done) return
    done = true
    record(kind, { ...detail, ...extra, ms: Math.round(performance.now() - started) })
  }
}

function schedule(): void {
  if (timer !== null) return
  timer = window.setTimeout(() => {
    timer = null
    void flush()
  }, FLUSH_MS)
}

async function flush(): Promise<void> {
  if (!enabled || queue.length === 0) return
  const batch = queue
  queue = []
  const body = JSON.stringify({ entries: batch })
  try {
    const res = await fetch('/api/usage', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
    })
    const answer = (await res.json()) as { unknown?: string[] }
    for (const kind of answer.unknown ?? []) {
      if (warned.has(kind)) continue
      warned.add(kind)
      // eslint-disable-next-line no-console
      console.warn(
        `usage: "${kind}" is not a kind the server knows, so it was not ` +
          'recorded. Add it to KINDS in galley/services/usage.py.',
      )
    }
  } catch {
    /* The backend is down or restarting. These are notes about how the tool is
     * used, not the work itself — dropping them is the right trade, and
     * retrying would build a queue that outlives the reason for it. */
  }
}

/* Leaving the page is the one flush that cannot wait for the timer, and the
 * one a normal fetch is not allowed to finish. `sendBeacon` is made for it. */
function flushOnExit(): void {
  if (!enabled || queue.length === 0) return
  const batch = queue
  queue = []
  try {
    navigator.sendBeacon(
      '/api/usage',
      new Blob([JSON.stringify({ entries: batch })], { type: 'application/json' }),
    )
  } catch {
    /* nothing to do at this point in a page's life */
  }
}

if (typeof window !== 'undefined') {
  window.addEventListener('pagehide', flushOnExit)
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flushOnExit()
  })
}
