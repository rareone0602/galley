import { useEffect, useRef, useState } from 'react'
import { api, type LogEvent, type Session } from '../api'

/** Event kinds that are written down. Each arrives once, with an id, and is
 *  replayed from the log when a tab reconnects. */
const KEPT = [
  'prompt', 'text', 'thinking', 'tool_use', 'tool_result',
  'session', 'result', 'rate_limit', 'error', 'turn_end', 'other',
] as const

/** A block the agent is still writing. It has no id and is in no log: the
 *  finished version arrives a moment later as an ordinary `text` event, and
 *  replaces it. `index` is which block of the current reply this is, so a
 *  reply that thinks first and then writes shows as two, not one run-on. */
type LiveBlock = { index: number; role: 'text' | 'thinking'; text: string; agent: string | null }

/** One block's address while it is being written. A helper writing at the same
 *  time as the agent starts at index 0 too, so the index alone is not enough. */
const at = (agent: string | null, index: number) => `${agent ?? 'you'}:${index}`

/** The agent's live log: text, thinking, and every tool call it makes. */
export default function LogPane({ session }: { session: Session }) {
  const [events, setEvents] = useState<LogEvent[]>([])
  const [live, setLive] = useState<LiveBlock[]>([])
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setEvents([])
    setLive([])
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

  // Follow the stream, not just the finished blocks.
  const written = live.reduce((n, b) => n + b.text.length, 0)
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events.length, written])

  async function send() {
    if (!draft.trim()) return
    setSending(true)
    try {
      await api.message(session.id, draft)
      setDraft('')
    } catch (e) {
      alert(String(e))
    } finally {
      setSending(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div className="log grow" style={{ overflowY: 'auto', paddingRight: 6 }}>
        {events.length === 0 && live.length === 0 && (
          <div className="empty">Waiting for the agent…</div>
        )}
        {events.map((e) => (
          <Event key={e.id} event={e} helper={helpers.get(e.payload?.agent)} />
        ))}
        {live.map((b) => (
          <Live key={at(b.agent, b.index)} block={b} helper={helpers.get(b.agent ?? '')} />
        ))}
        <div ref={bottom} />
      </div>
      <div className="row" style={{ marginTop: 10, alignItems: 'flex-end' }}>
        <textarea
          rows={2}
          value={draft}
          placeholder={
            session.running ? 'The agent is working; your message queues behind it.' : 'Reply…'
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
  return (
    <div className={`ev text assistant${sub}`}>
      <div className="who">{helper ?? 'assistant'}</div>
      <p>
        {block.text}
        <span className="caret" />
      </p>
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
          <p>{p.text}</p>
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
          </div>
        </div>
      )
    case 'rate_limit':
      return <RateLimit info={p} />
    case 'error':
      return <div className="ev err">{p.error}</div>
    case 'session':
    case 'turn_end':
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
