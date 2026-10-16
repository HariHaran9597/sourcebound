import { useCallback, useEffect, useState } from 'react'
import { AlertCircle, Activity, RefreshCw } from 'lucide-react'
import { listAuditEvents, type AuditEvent } from './api'
import './KnowledgePanel.css'

export function AuditPanel() {
  const [events, setEvents] = useState<AuditEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const refresh = useCallback(async (showLoading = true) => {
    if (showLoading) {
      setLoading(true)
      setError('')
    }
    try {
      setEvents(await listAuditEvents())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load the workspace audit trail.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    let active = true
    void Promise.resolve()
      .then(() => listAuditEvents())
      .then((latest) => {
        if (active) {
          setEvents(latest)
          setError('')
        }
      })
      .catch((err: unknown) => {
        if (active) setError(err instanceof Error ? err.message : 'Could not load the workspace audit trail.')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => { active = false }
  }, [])

  return <section className="knowledge-page">
    <div className="knowledge-page-heading">
      <div><div className="eyebrow"><span className="eyebrow-dot" />WORKSPACE ADMINISTRATION</div><h1>Security audit trail</h1><p>Recent document and access changes recorded for this organization.</p></div>
      <button className="refresh-button" onClick={() => void refresh()} disabled={loading}><RefreshCw size={13} />Refresh</button>
    </div>
    <div className="local-data-notice"><AlertCircle size={15} /><span>Audit records include actors, event times, affected document IDs, and policy metadata. They do not include document text or answers.</span></div>
    {error && <div className="upload-notice error-notice"><AlertCircle size={15} />{error}</div>}
    {loading ? <div className="document-empty"><Activity size={19} /><span>Loading audit events…</span></div> : events.length === 0 ? <div className="document-empty"><Activity size={19} /><strong>No audit events yet</strong><span>Document uploads, processing retries, and access-policy changes will appear here.</span></div> : <div className="audit-event-list">{events.map((event) => <article className="audit-event" key={event.id}>
      <div className="audit-event-icon"><Activity size={14} /></div>
      <div className="audit-event-main"><div><strong>{event.action.replaceAll('.', ' ')}</strong><time>{new Date(event.created_at).toLocaleString()}</time></div><p>Actor <code>{event.actor_subject}</code> · {event.resource_type} <code>{event.resource_id}</code></p>{Object.keys(event.details).length > 0 && <pre>{JSON.stringify(event.details, null, 2)}</pre>}</div>
    </article>)}</div>}
    <p className="audit-retention-note">This is an application audit log stored in the workspace database. Database administrators can alter or delete records.</p>
  </section>
}
