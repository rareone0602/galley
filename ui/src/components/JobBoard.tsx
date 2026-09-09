import { useEffect, useState } from 'react'
import { api, type Job } from '../api'

/**
 * Jobs outlive the sessions that created them, so this is a board rather than
 * a transcript. A finished job can hand itself to a *new* session with its
 * results already in the prompt.
 */
export default function JobBoard({ onHandoff }: { onHandoff: (sessionId: string) => void }) {
  const [jobs, setJobs] = useState<Job[]>([])
  const [open, setOpen] = useState<string | null>(null)
  const [detail, setDetail] = useState<Job | null>(null)
  const [note, setNote] = useState('')
  const [script, setScript] = useState('echo hello > "$GALLEY_ARTIFACTS/metrics.json"\n')
  const [gpus, setGpus] = useState(1)
  const [hours, setHours] = useState(8)
  const [error, setError] = useState<string | null>(null)

  async function reload() {
    try {
      setJobs(await api.jobs())
    } catch (e) {
      setError(String(e))
    }
  }

  useEffect(() => {
    void reload()
    const source = new EventSource('/api/jobs/events')
    const refresh = () => void reload()
    source.onmessage = refresh
    for (const kind of ['job_state', 'job_finished', 'poller_error'])
      source.addEventListener(kind, refresh)
    const timer = setInterval(refresh, 30000)
    return () => {
      source.close()
      clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    if (!open) return setDetail(null)
    void api.job(open).then(setDetail).catch(() => setDetail(null))
  }, [open, jobs])

  const queued = jobs.filter((j) => j.state === 'PENDING' || j.state === 'SUBMITTED').length

  return (
    <>
      {error && <div className="notice bad">{error}</div>}
      <div className="card">
        <h3>Submit an experiment</h3>
        {queued > 0 && (
          <div className="notice warn">
            You already have {queued} job waiting. gpuq admits on a VRAM sample taken at
            submit time, so two of your own queued waiters take the same freed card. Galley
            submits one at a time.
          </div>
        )}
        <div className="row" style={{ marginBottom: 8 }}>
          <input
            placeholder="note — what this run is for"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <label className="small muted">GPUs</label>
          <input
            type="number"
            min={1}
            max={4}
            value={gpus}
            style={{ width: 64 }}
            onChange={(e) => setGpus(Number(e.target.value))}
          />
          <label className="small muted">hours</label>
          <input
            type="number"
            min={0.1}
            max={12}
            value={hours}
            style={{ width: 72 }}
            onChange={(e) => setHours(Number(e.target.value))}
          />
        </div>
        <textarea rows={5} value={script} onChange={(e) => setScript(e.target.value)} />
        <div className="row" style={{ marginTop: 8 }}>
          <span className="muted small">
            Runs at the code mirror's current commit, in a fresh checkout. Write anything
            you want kept into <code>$GALLEY_ARTIFACTS</code>.
          </span>
          <span className="grow" />
          <button
            className="primary"
            disabled={queued > 0}
            onClick={async () => {
              try {
                await api.submitJob(script, note, gpus, hours)
                setError(null)
                void reload()
              } catch (e) {
                setError(String(e))
              }
            }}
          >
            Submit
          </button>
        </div>
      </div>

      <div className="card">
        <h3>Jobs</h3>
        {jobs.length === 0 && <div className="empty">Nothing submitted yet.</div>}
        {jobs.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>State</th>
                <th>Job</th>
                <th>Note</th>
                <th>SHA</th>
                <th>Exit</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.id}>
                  <td>
                    <span className={`badge ${j.state}`}>{j.state}</span>
                  </td>
                  <td className="mono">{j.id}</td>
                  <td>{j.note}</td>
                  <td className="mono">{(j.code_sha ?? '').slice(0, 8)}</td>
                  <td className="mono">{j.exit_code ?? ''}</td>
                  <td>
                    <div className="row">
                      <button className="tiny" onClick={() => setOpen(open === j.id ? null : j.id)}>
                        {open === j.id ? 'Hide' : 'Output'}
                      </button>
                      {['PENDING', 'SUBMITTED', 'RUNNING'].includes(j.state) && (
                        <button className="tiny" onClick={() => api.cancelJob(j.id).then(reload)}>
                          Cancel
                        </button>
                      )}
                      {['COMPLETED', 'FAILED'].includes(j.state) && (
                        <>
                          <button
                            className="tiny"
                            onClick={() => api.fetchArtifacts(j.id).then(reload)}
                          >
                            Pull
                          </button>
                          <button
                            className="tiny primary"
                            title="Open a new session with these results in the prompt"
                            onClick={() => api.handoff(j.id).then((s) => onHandoff(s.id))}
                          >
                            Hand off
                          </button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {detail && (
        <div className="card">
          <h3>{detail.id} — output</h3>
          <pre style={{ maxHeight: 320, overflow: 'auto' }}>{detail.tail}</pre>
        </div>
      )}
    </>
  )
}
