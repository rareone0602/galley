import { useEffect, useMemo, useRef, useState } from 'react'
import { api, type LogEvent, type Session } from '../api'
import Markdown from './preview/Markdown'

/** Event kinds that are written down. Each arrives once, with an id, and is
 *  replayed from the log when a tab reconnects. */
const KEPT = [
  'prompt', 'text', 'thinking', 'tool_use', 'tool_result',
  'session', 'compact', 'result', 'rate_limit', 'error', 'turn_end', 'other',
] as const

/** Tokens, the way you would say them: 152531 is "153k", 1871 is "1.9k". */
const tokens = (n: number) =>
  n >= 10000 ? `${Math.round(n / 1000)}k` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)

/** Where the next word will appear. A character, not an element — see `Live`. */
const CARET = '\u258c'

/**
 * What the agent writes is Markdown, so it is drawn as Markdown.
 *
 * The same renderer as the preview pane, and for the same reason: it turns
 * the text into React elements rather than into an HTML string, so a `<script>`
 * in something the agent quoted back is shown as the text it is. Galley's page
 * is same-origin with the paper and can write to it, so that is not a nicety.
 *
 * There is no file behind chat text, so links to project files render as plain
 * words rather than as buttons that would not know what to open.
 */
function Prose({ text }: { text: string }) {
  const drawn = useMemo(() => <Markdown text={text} path="" />, [text])
  return drawn
}

/** A block the agent is still writing. It has no id and is in no log: the
 *  finished version arrives a moment later as an ordinary `text` event, and
 *  replaces it. `index` is which block of the current reply this is, so a
 *  reply that thinks first and then writes shows as two, not one run-on. */
type LiveBlock = { index: number; role: 'text' | 'thinking'; text: string; agent: string | null }

/** One block's address while it is being written. A helper writing at the same
 *  time as the agent starts at index 0 too, so the index alone is not enough. */
const at = (agent: string | null, index: number) => `${agent ?? 'you'}:${index}`

/** The agent's live log: text, thinking, and every tool call it makes. */
export default function LogPane({
  session,
  onChanged,
}: {
  session: Session
  /** Something here changed the session — it was stopped, or folded. The rail
   *  polls every few seconds anyway; this is so it does not have to be waited
   *  for after a button you just pressed. */
  onChanged?: () => void
}) {
  const [events, setEvents] = useState<LogEvent[]>([])
  const [live, setLive] = useState<LiveBlock[]>([])
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [compacting, setCompacting] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [trouble, setTrouble] = useState<string | null>(null)
  const scroller = useRef<HTMLDivElement>(null)
  /** Whether the log is following the agent. True while you are at the
   *  bottom; false the moment you scroll up to read something. */
  const [following, setFollowing] = useState(true)
  /** Something arrived while you were reading further up. */
  const [behind, setBehind] = useState(false)

  useEffect(() => {
    setEvents([])
    setLive([])
    setFollowing(true)
    setBehind(false)
    setTrouble(null)
    const source = new EventSource(`/api/sessions/${session.id}/events`)

    // A finished block lands here. It supersedes whatever was being streamed,
    // so the half-written copy goes at the same moment the whole one arrives.
    const keep = (e: Event) => {
      const next = JSON.parse((e as MessageEvent).data)
      setLive([])
      setEvents((prev) => (prev.some((p) => p.id === next.id) ? prev : [...prev, next]))
    }
    source.onmessage = keep
    // Named events arrive with their kind; the default handler misses those.
    for (const kind of KEPT) source.addEventListener(kind, keep)

    source.addEventListener('delta', (e) => {
      const { index, role, text, agent = null } = JSON.parse((e as MessageEvent).data).payload
      setLive((prev) => {
        const found = prev.findIndex((b) => at(b.agent, b.index) === at(agent, index))
        if (found < 0) return [...prev, { index, role, text, agent }]
        const next = prev.slice()
        next[found] = { ...next[found], text: next[found].text + text }
        return next
      })
    })

    return () => source.close()
  }, [session.id])

  // What each helper was started as. The events carry the id of the tool call
  // that started them, which is not a name anybody wants to read; the call
  // itself is in the log and says which kind it asked for.
  const helpers = new Map<string, string>()
  for (const e of events) {
    if (e.kind === 'tool_use' && e.payload?.id && e.payload?.input?.subagent_type) {
      helpers.set(e.payload.id, String(e.payload.input.subagent_type))
    }
  }

  /* Follow the agent, unless you are reading.
   *
   * Scrolling up is the whole of the gesture: the log stops moving, and a
   * button appears saying there is more below. It used to drag you to the
   * bottom on every fragment of every sentence, which made the log unreadable
   * for exactly as long as there was something worth reading in it.
   *
   * The jump is instant rather than smooth on purpose. A smooth scroll is
   * still animating when the next fragment arrives, so the handler below sees
   * a position part-way up and concludes you scrolled away — and the log
   * detaches itself a second after it starts. */
  const toBottom = (behavior: ScrollBehavior = 'auto') => {
    const el = scroller.current
    if (el) el.scrollTo({ top: el.scrollHeight, behavior })
  }

  const written = live.reduce((n, b) => n + b.text.length, 0)
  useEffect(() => {
    if (following) toBottom()
    else if (events.length || written) setBehind(true)
    // `following` is deliberately not a dependency: turning it back on scrolls
    // through the button below, and re-running here on every change of it
    // would fight the scroll that turned it off.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events.length, written])

  /** At the bottom, near enough. A few pixels of slack covers a half-rendered
   *  line and a trackpad that stops just short. */
  const NEAR = 40
  function watchScroll() {
    const el = scroller.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= NEAR
    setFollowing(atBottom)
    if (atBottom) setBehind(false)
  }

  function catchUp() {
    setFollowing(true)
    setBehind(false)
    toBottom('smooth')
  }

  async function send() {
    if (!draft.trim()) return
    setSending(true)
    setTrouble(null)
    try {
      await api.message(session.id, draft)
      setDraft('')
      catchUp()
    } catch (e) {
      setTrouble(String(e))
    } finally {
      setSending(false)
    }
  }

  /* Stop, where you are standing when you want it.
   *
   * It was only ever on the rail, three panes away from the text that made
   * you want it — and the rail is where you pick a session, not where you
   * watch one. This is the button for "that is not what I meant", and it has
   * to be under the thing that was not what you meant.
   *
   * What it does is end the turn: the CLI is shut down, and whatever the agent
   * had already written is committed to the branch, so a half-finished patch
   * is still in Review afterwards. */
  async function stop() {
    setStopping(true)
    setTrouble(null)
    try {
      await api.stopSession(session.id)
      onChanged?.()
    } catch (e) {
      setTrouble(String(e))
    } finally {
      setStopping(false)
    }
  }

  /* Folding the conversation so far into a summary. The session keeps its id
   * and carries on; what changes is that every call after this pays for the
   * summary instead of the whole transcript. The server refuses it mid-turn
   * and before the first turn, and the button says so rather than failing. */
  const cannotCompact = session.running
    ? 'The agent is working. It can be compacted when it stops.'
    : !session.claude_session_id
      ? 'Nothing to compact until the first turn has run.'
      : null
  async function compact() {
    setCompacting(true)
    setTrouble(null)
    try {
      await api.compact(session.id)
      onChanged?.()
    } catch (e) {
      setTrouble(String(e))
    } finally {
      setCompacting(false)
    }
  }
  const soFar = [
    session.context_tokens != null ? `${tokens(session.context_tokens)} tokens in context` : null,
    session.cost_usd != null ? `$${session.cost_usd.toFixed(2)} so far` : null,
  ].filter(Boolean)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div className="log-scroll grow">
        <div
          className="log"
          ref={scroller}
          onScroll={watchScroll}
          style={{ overflowY: 'auto', paddingRight: 6, height: '100%' }}
        >
          {events.length === 0 && live.length === 0 && (
            <div className="empty">Waiting for the agent…</div>
          )}
          {events.map((e) => (
            <Event key={e.id} event={e} helper={helpers.get(e.payload?.agent)} />
          ))}
          {live.map((b) => (
            <Live key={at(b.agent, b.index)} block={b} helper={helpers.get(b.agent ?? '')} />
          ))}
        </div>
        {behind && (
          <button className="tiny catch-up" onClick={catchUp}>
            ↓ more below
          </button>
        )}
      </div>
      {trouble && <div className="notice bad">{trouble}</div>}
      <div className="row" style={{ marginTop: 10, alignItems: 'flex-end' }}>
        <textarea
          rows={2}
          value={draft}
          placeholder={
            session.running
              ? 'The agent is working. Stop it below, or wait for the turn to end.'
              : 'Reply…'
          }
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void send()
          }}
        />
        <button className="primary" onClick={send} disabled={sending || !draft.trim()}>
          Send
        </button>
      </div>
      <div className="log-foot">
        <span className="muted small" title="what the next message pays for again, and what the session has cost">
          {soFar.join(' · ')}
        </span>
        <span className="grow" />
        {session.running ? (
          <button
            className="tiny stop"
            onClick={stop}
            disabled={stopping}
            title="End this turn now. The CLI is shut down and whatever it has already written is committed to the branch, so anything worth keeping is still in Review."
          >
            {stopping ? 'Stopping…' : 'Stop'}
          </button>
        ) : (
          <button
            className="tiny compact"
            onClick={compact}
            disabled={compacting || !!cannotCompact}
            title={
              cannotCompact ??
              'Fold the conversation so far into a short summary. The session carries on from it, and every call after this pays for the summary instead of the whole transcript.'
            }
          >
            {compacting ? 'Compacting…' : 'Compact'}
          </button>
        )}
      </div>
    </div>
  )
}

/** A block as it is being written. Same shape as the finished one, so nothing
 *  jumps when the two swap over — only the caret goes away. */
function Live({ block, helper }: { block: LiveBlock; helper?: string }) {
  const sub = block.agent ? ' sub' : ''
  if (block.role === 'thinking') {
    return (
      <div className={`ev thinking${sub}`}>
        <details open>
          <summary className="who">{helper ? `${helper} · thinking` : 'thinking'}</summary>
          <p>
            {block.text}
            <span className="caret" />
          </p>
        </details>
      </div>
    )
  }
  /* Drawn as Markdown while it is still being typed, so nothing reflows when
   * the finished block arrives and replaces it. A half-written code fence is
   * closed by the parser, which is why an unfinished block looks sensible.
   *
   * The caret goes into the text rather than beside it. Markdown renders as
   * blocks, so a caret element after one would sit on a line of its own
   * underneath; a character on the end lands where the next word will. */
  return (
    <div className={`ev text assistant${sub}`}>
      <div className="who">{helper ?? 'assistant'}</div>
      <Prose text={block.text + CARET} />
    </div>
  )
}

function Event({ event, helper }: { event: LogEvent; helper?: string }) {
  const p = event.payload ?? {}
  // A block a helper produced sits indented under the agent you asked, so a
  // fanned-out turn reads as one conversation with asides rather than three.
  const sub = p.agent ? ' sub' : ''
  switch (event.kind) {
    case 'prompt':
      return (
        <div className="ev text">
          <div className="who">you</div>
          <p>{p.text}</p>
        </div>
      )
    case 'text':
      return (
        <div className={`ev text ${p.role}${sub}`}>
          <div className="who">{helper ?? p.role}</div>
          <Prose text={String(p.text ?? '')} />
        </div>
      )
    case 'thinking':
      return (
        <div className={`ev thinking${sub}`}>
          <details>
            <summary className="who">{helper ? `${helper} · thinking` : 'thinking'}</summary>
            <p>{p.text}</p>
          </details>
        </div>
      )
    case 'tool_use':
      return (
        <div className={`ev tool${sub}`}>
          <div className="name">
            {p.name}
            {p.input?.subagent_type ? ` → ${p.input.subagent_type}` : ''}
          </div>
          <details>
            <summary className="who small">input</summary>
            <pre>{JSON.stringify(p.input, null, 2)}</pre>
          </details>
        </div>
      )
    case 'tool_result':
      return (
        <div className={`ev tool${sub}${p.is_error ? ' err' : ''}`}>
          <details>
            <summary className="who small">{p.is_error ? 'tool error' : 'tool result'}</summary>
            <pre>{String(p.content).slice(0, 4000)}</pre>
          </details>
        </div>
      )
    case 'result':
      return (
        <div className="ev">
          <div className="who">
            turn finished{p.num_turns ? ` · ${p.num_turns} turns` : ''}
            {p.duration_ms ? ` · ${(p.duration_ms / 1000).toFixed(1)}s` : ''}
            {p.total_cost_usd ? ` · $${Number(p.total_cost_usd).toFixed(4)}` : ''}
            {p.context_tokens ? ` · ${tokens(p.context_tokens)} in context` : ''}
          </div>
        </div>
      )
    case 'compact':
      /* The conversation above this line is now a summary. What the next
       * call pays is the summary plus a fixed prefix the CLI does not
       * report, so the footer's count goes blank until that call is made. */
      return (
        <div className="ev compact">
          {p.trigger === 'auto' ? 'the context filled up, so the CLI folded' : 'folded'}
          {p.pre_tokens ? ` ${tokens(p.pre_tokens)} tokens` : ' the conversation so far'}
          {p.post_tokens ? ` into a ${tokens(p.post_tokens)}-token summary` : ' into a summary'}
        </div>
      )
    case 'rate_limit':
      return <RateLimit info={p} />
    case 'error':
      return <div className="ev err">{p.error}</div>
    case 'turn_end':
      /* Ordinary endings are silent — the result line above already says the
       * turn finished. A stop is not ordinary: it is a thing you did, and what
       * happened to the half-written patch is the question you will have. */
      if (!p.stopped) return null
      return (
        <div className="ev compact">
          {p.committed
            ? 'stopped by you; what it had written is committed to the branch'
            : 'stopped by you; it had written nothing yet'}
        </div>
      )
    case 'session':
      return null
    default:
      return null
  }
}

/** How much of the subscription window is gone.
 *
 *  Shown only when it is not simply fine, because this is the one failure that
 *  looks like Galley breaking and is not: the agent stops answering, and the
 *  reason is a clock nothing else on the screen shows. */
function RateLimit({ info }: { info: any }) {
  if (!info?.status || info.status === 'allowed') return null
  const share = typeof info.used === 'number' ? ` (${Math.round(info.used * 100)}% used)` : ''
  const back = info.resets_at ? new Date(info.resets_at * 1000).toLocaleTimeString() : null
  const window = String(info.window ?? 'usage').replace(/_/g, ' ')
  return (
    <div className="ev err">
      {info.status === 'rejected'
        ? `Your ${window} limit is reached${share}.`
        : `Approaching your ${window} limit${share}.`}
      {back ? ` It resets at ${back}.` : ''}
    </div>
  )
}
