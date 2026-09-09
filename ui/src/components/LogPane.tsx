import { useEffect, useRef, useState } from 'react'
import { api, type LogEvent, type Session } from '../api'

/** The agent's live log: text, thinking, and every tool call it makes. */
export default function LogPane({ session }: { session: Session }) {
  const [events, setEvents] = useState<LogEvent[]>([])
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setEvents([])
    const source = new EventSource(`/api/sessions/${session.id}/events`)
    source.onmessage = (e) => setEvents((prev) => [...prev, JSON.parse(e.data)])
    // Named events arrive with their kind; the default handler misses those.
    for (const kind of [
      'prompt', 'text', 'thinking', 'tool_use', 'tool_result',
      'session', 'result', 'error', 'turn_end', 'other',
    ]) {
      source.addEventListener(kind, (e) =>
        setEvents((prev) => {
          const next = JSON.parse((e as MessageEvent).data)
          return prev.some((p) => p.id === next.id) ? prev : [...prev, next]
        }),
      )
    }
    return () => source.close()
  }, [session.id])

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events.length])

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
        {events.length === 0 && <div className="empty">Waiting for the agent…</div>}
        {events.map((e) => (
          <Event key={e.id} event={e} />
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

function Event({ event }: { event: LogEvent }) {
  const p = event.payload ?? {}
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
        <div className={`ev text ${p.role}`}>
          <div className="who">{p.role}</div>
          <p>{p.text}</p>
        </div>
      )
    case 'thinking':
      return (
        <div className="ev thinking">
          <details>
            <summary className="who">thinking</summary>
            <p>{p.text}</p>
          </details>
        </div>
      )
    case 'tool_use':
      return (
        <div className="ev tool">
          <div className="name">{p.name}</div>
          <details>
            <summary className="who small">input</summary>
            <pre>{JSON.stringify(p.input, null, 2)}</pre>
          </details>
        </div>
      )
    case 'tool_result':
      return (
        <div className={`ev tool${p.is_error ? ' err' : ''}`}>
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
    case 'error':
      return <div className="ev err">{p.error}</div>
    case 'session':
    case 'turn_end':
      return null
    default:
      return null
  }
}
