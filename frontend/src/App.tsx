import { useEffect, useState, type FormEvent } from 'react'
import {
  Activity,
  ArrowDownToLine,
  ArrowRight,
  Bell,
  BookOpenText,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Clock3,
  FileText,
  Filter,
  History,
  Layers3,
  LockKeyhole,
  MessageSquareText,
  MoreHorizontal,
  Plus,
  Send,
  Settings2,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
  Upload,
} from 'lucide-react'
import { KnowledgePanel } from './KnowledgePanel'
import { AuditPanel } from './AuditPanel'
import { askKnowledgeBase, clearAnswerHistory, deleteAnswerHistory, listAnswerHistory, reviewConflict, setAccessTokenProvider, type AnswerHistoryItem, type AskResult, type ConflictFinding } from './api'
import { UserManager, WebStorageStateStore, type User } from 'oidc-client-ts'
import './App.css'

const oidcAuthority = import.meta.env.VITE_OIDC_AUTHORITY as string | undefined
const oidcClientId = import.meta.env.VITE_OIDC_CLIENT_ID as string | undefined
const oidcManager = oidcAuthority && oidcClientId ? new UserManager({
  authority: oidcAuthority,
  client_id: oidcClientId,
  redirect_uri: `${window.location.origin}/oidc-callback`,
  post_logout_redirect_uri: window.location.origin,
  response_type: 'code',
  scope: 'openid profile email',
  automaticSilentRenew: true,
  userStore: new WebStorageStateStore({ store: window.sessionStorage }),
}) : null

const suggestions = [
  'What changed in the incident response policy?',
  'Who needs to be notified for a critical incident?',
  'Show me procedures that conflict with this policy',
]

function CitedAnswer({ answer, citations, onSelect }: {
  answer: string
  citations: AskResult['citations']
  onSelect: (chunkId: string) => void
}) {
  const parts = answer.split(/(\[\d+\])/g)
  return <p className="answer-intro">{parts.map((part, index) => {
    const match = /^\[(\d+)\]$/.exec(part)
    const source = match ? citations[Number(match[1]) - 1] : undefined
    if (!match || !source) return <span key={index}>{part}</span>
    return <button
      key={index}
      type="button"
      className="inline-citation"
      aria-label={`Open source ${match[1]}: ${source.document_title}, version ${source.version_number}`}
      onClick={() => onSelect(source.chunk_id)}
    >{part}</button>
  })}</p>
}

function App() {
  const [question, setQuestion] = useState('')
  const [submitted, setSubmitted] = useState('')
  const [result, setResult] = useState<AskResult | null>(null)
  const [answerError, setAnswerError] = useState('')
  const [asking, setAsking] = useState(false)
  const [isHistorical, setIsHistorical] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [historyItems, setHistoryItems] = useState<AnswerHistoryItem[]>([])
  const [historyEnabled, setHistoryEnabled] = useState(true)
  const [historyRetentionDays, setHistoryRetentionDays] = useState(30)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState('')
  const [conflictReviewError, setConflictReviewError] = useState('')
  const [reviewingConflictId, setReviewingConflictId] = useState<string | null>(null)
  const [conflictReviewNotes, setConflictReviewNotes] = useState<Record<string, string>>({})
  const [user, setUser] = useState<User | null>(null)
  const [authReady, setAuthReady] = useState(!oidcManager)
  const [authError, setAuthError] = useState('')
  const [activeNav, setActiveNav] = useState('Ask')
  const [selectedSource, setSelectedSource] = useState<string | null>(null)

  async function askQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const prompt = question.trim()
    if (!prompt || asking) return
    setSubmitted(prompt)
    setResult(null)
    setIsHistorical(false)
    setAnswerError('')
    setConflictReviewError('')
    setAsking(true)
    setQuestion('')
    try {
      const response = await askKnowledgeBase(prompt)
      setResult(response)
      setSelectedSource(response.citations[0]?.chunk_id ?? null)
    } catch (err) {
      setAnswerError(err instanceof Error ? err.message : 'The answer service could not be reached.')
    } finally {
      setAsking(false)
    }
  }

  const citations = result?.citations ?? []
  const selectedCitation = citations.find((citation) => citation.chunk_id === selectedSource) ?? citations[0]
  const historicalAnswer = result ? historyItems.find((item) => item.trace_id === result.trace_id) : undefined

  async function showHistory() {
    if (historyOpen) { setHistoryOpen(false); return }
    setHistoryOpen(true)
    setHistoryLoading(true)
    setHistoryError('')
    try {
      const history = await listAnswerHistory()
      setHistoryEnabled(history.enabled)
      setHistoryRetentionDays(history.retention_days)
      setHistoryItems(history.traces)
    } catch (err) {
      setHistoryError(err instanceof Error ? err.message : 'Could not load answer history.')
    } finally {
      setHistoryLoading(false)
    }
  }

  function restoreHistory(item: AnswerHistoryItem) {
    if (item.redacted) {
      setAnswerError('This answer is hidden because you no longer have access to one or more cited sources.')
      setHistoryOpen(false)
      return
    }
    setSubmitted(item.question)
    setResult({ ...item, history_enabled: true, history_saved: true })
    setSelectedSource(item.citations[0]?.chunk_id ?? null)
    setIsHistorical(true)
    setAnswerError('')
    setConflictReviewError('')
    setHistoryOpen(false)
  }

  async function removeHistory(traceId: string) {
    try {
      await deleteAnswerHistory(traceId)
      setHistoryItems((items) => items.filter((item) => item.trace_id !== traceId))
      if (result?.trace_id === traceId) { setResult(null); setSubmitted('') }
    } catch (err) {
      setHistoryError(err instanceof Error ? err.message : 'Could not delete this history entry.')
    }
  }

  async function clearHistory() {
    try {
      await clearAnswerHistory()
      setHistoryItems([])
      setResult(null)
      setSubmitted('')
    } catch (err) {
      setHistoryError(err instanceof Error ? err.message : 'Could not clear history.')
    }
  }

  async function setConflictDisposition(conflictId: string, disposition: 'confirmed' | 'dismissed') {
    if (!result?.history_saved || !result.trace_id) return
    setReviewingConflictId(conflictId)
    setConflictReviewError('')
    try {
      const reviewed = await reviewConflict(result.trace_id, conflictId, disposition, conflictReviewNotes[conflictId])
      const updateFinding = (finding: ConflictFinding) => finding.conflict_id === conflictId ? reviewed : finding
      setResult((current) => current ? { ...current, conflicts: current.conflicts.map(updateFinding) } : current)
      setHistoryItems((items) => items.map((item) => item.trace_id === result.trace_id
        ? { ...item, conflicts: item.conflicts.map(updateFinding) }
        : item))
      setConflictReviewNotes((notes) => ({ ...notes, [conflictId]: '' }))
    } catch (err) {
      setConflictReviewError(err instanceof Error ? err.message : 'Could not save the conflict review.')
    } finally {
      setReviewingConflictId(null)
    }
  }

  useEffect(() => {
    if (!oidcManager) return
    let active = true
    const loadIdentity = async () => {
      try {
        if (window.location.pathname === '/oidc-callback') {
          await oidcManager.signinRedirectCallback()
          window.history.replaceState({}, document.title, '/')
        }
        const current = await oidcManager.getUser()
        if (active) setUser(current)
      } catch (err) {
        if (active) setAuthError(err instanceof Error ? err.message : 'Sign-in could not be completed.')
      } finally {
        if (active) setAuthReady(true)
      }
    }
    setAccessTokenProvider(async () => (await oidcManager.getUser())?.access_token)
    void loadIdentity()
    const onUserLoaded = (next: User) => setUser(next)
    const onUserUnloaded = () => setUser(null)
    oidcManager.events.addUserLoaded(onUserLoaded)
    oidcManager.events.addUserUnloaded(onUserUnloaded)
    return () => {
      active = false
      setAccessTokenProvider(null)
      oidcManager.events.removeUserLoaded(onUserLoaded)
      oidcManager.events.removeUserUnloaded(onUserUnloaded)
    }
  }, [])

  useEffect(() => {
    const clearDeletedSourceEvidence = (event: Event) => {
      const documentId = (event as CustomEvent<{ documentId?: string }>).detail?.documentId
      if (!documentId) return
      setResult(null)
      setSubmitted('')
      setIsHistorical(false)
      setSelectedSource(null)
      setHistoryItems((items) => items.filter((item) => !item.citations.some((citation) => citation.document_id === documentId)))
    }
    window.addEventListener('sourcebound:document-deleted', clearDeletedSourceEvidence)
    return () => window.removeEventListener('sourcebound:document-deleted', clearDeletedSourceEvidence)
  }, [])

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup">
          <div className="brand-mark"><Layers3 size={19} strokeWidth={2.3} /></div>
          <div className="brand-name">Source<span>bound</span></div>
          <button className="icon-button sidebar-menu" aria-label="Open workspace menu"><MoreHorizontal size={19} /></button>
        </div>

        <button className="workspace-switcher">
          <span className="workspace-avatar">N</span>
          <span className="workspace-copy"><strong>Local workspace</strong><small>Self-hosted instance</small></span>
          <ChevronDown size={15} />
        </button>

        <div className="sidebar-group">
          <div className="group-label">WORKSPACE</div>
          <nav className="nav-list" aria-label="Main navigation">
            <button className={`nav-item ${activeNav === 'Ask' ? 'active' : ''}`} onClick={() => setActiveNav('Ask')}><MessageSquareText size={17} />Ask</button>
            <button className={`nav-item ${activeNav === 'Knowledge' ? 'active' : ''}`} onClick={() => setActiveNav('Knowledge')}><BookOpenText size={17} />Knowledge</button>
            <button className={`nav-item ${activeNav === 'Changes' ? 'active' : ''}`} onClick={() => setActiveNav('Changes')}><History size={17} />Changes</button>
            {(!oidcManager || (Array.isArray(user?.profile.roles) && user.profile.roles.includes('sourcebound_admin'))) && <button className={`nav-item ${activeNav === 'Audit' ? 'active' : ''}`} onClick={() => setActiveNav('Audit')}><Activity size={17} />Audit</button>}
          </nav>
        </div>

        <div className="sidebar-group sources-group">
          <div className="group-heading"><span className="group-label">CONNECTED SOURCES</span><button className="mini-icon" aria-label="Add a source"><Plus size={15} /></button></div>
          <div className="source-nav"><span className="source-type wordpress">PDF</span><span>Local PDF uploads</span></div>
          <div className="connect-source"><Plus size={14} />Connectors planned</div>
        </div>

        <div className="sidebar-bottom">
          <div className="system-status"><span className="status-pulse" /><span>Local development</span><button className="mini-icon" aria-label="View sync status"><ChevronRight size={15} /></button></div>
          <button className="nav-item" onClick={() => setActiveNav('Settings')}><Settings2 size={17} />Settings</button>
          <button className="nav-item" onClick={() => setActiveNav('Help')}><CircleHelp size={17} />Help & feedback</button>
          <button className="user-profile" onClick={() => { if (oidcManager && user) void oidcManager.signoutRedirect() }} title={user ? 'Sign out' : 'Local development user'}><div className="user-avatar">{user ? String(user.profile.name ?? user.profile.preferred_username ?? 'U').slice(0, 2).toUpperCase() : 'LD'}</div><div className="user-copy"><strong>{user ? String(user.profile.name ?? user.profile.preferred_username ?? 'Signed in') : 'Local developer'}</strong><small>{user ? 'Signed in · click to sign out' : 'No OIDC session'}</small></div><MoreHorizontal size={17} className="profile-more" /></button>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="breadcrumbs"><span>Workspace</span><ChevronRight size={14} /><strong>{activeNav === 'Ask' ? 'Ask' : activeNav}</strong></div>
          <div className="topbar-actions"><div className="secure-badge"><CircleHelp size={13} />{oidcManager ? user ? 'OIDC authenticated' : 'Sign-in required' : 'Local · no authentication'}</div><button className="icon-button" aria-label="Notifications"><Bell size={17} /><i /></button><button className="help-link">Help center <ArrowRight size={13} /></button></div>
        </header>

        {oidcManager && (!authReady || !user) ? <section className="auth-gate"><div className="summary-icon"><LockKeyhole size={20} /></div><h1>{authReady ? 'Sign in to Sourcebound' : 'Checking your session…'}</h1><p>Use your organization’s identity provider to access this knowledge workspace.</p>{authError && <div className="upload-notice error-notice">{authError}</div>}{authReady && <button className="upload-primary" onClick={() => void oidcManager.signinRedirect()}>Continue with organization sign-in <ArrowRight size={14} /></button>}</section> : activeNav === 'Knowledge' || activeNav === 'Changes' ? <KnowledgePanel view={activeNav} isWorkspaceAdmin={!oidcManager || (Array.isArray(user?.profile.roles) && user.profile.roles.includes('sourcebound_admin'))} /> : activeNav === 'Audit' ? <AuditPanel /> : <div className="content-wrap">
          <section className="chat-column">
            <div className="page-heading">
              <div><div className="eyebrow"><span className="eyebrow-dot" />KNOWLEDGE ASSISTANT</div><h1>Ask your knowledge base</h1><p>Answers grounded in the sources your team already trusts.</p></div>
              <button className="history-button" onClick={() => void showHistory()}><Clock3 size={15} />History<ChevronDown size={13} /></button>
            </div>

            {historyOpen && <section className="answer-history-panel" aria-label="Answer history">
              <div className="answer-history-heading"><div><strong>Recent answers</strong><small>Only your account’s answers · deleted after {historyRetentionDays} days</small></div><button className="mini-icon" onClick={() => setHistoryOpen(false)} aria-label="Close history">×</button></div>
              {historyLoading ? <p className="history-state">Loading history…</p> : historyError ? <p className="history-error">{historyError}</p> : !historyEnabled ? <p className="history-state">Answer history is disabled by the instance administrator.</p> : historyItems.length === 0 ? <p className="history-state">No saved answers yet. Ask a question to start your history.</p> : <div className="answer-history-list">{historyItems.map((item) => <div className="answer-history-row" key={item.trace_id}>
                <button className="answer-history-select" onClick={() => restoreHistory(item)}><strong>{item.redacted ? 'Source access changed' : item.question || 'Question unavailable'}</strong><span>{new Date(item.created_at).toLocaleString()} · {item.redacted ? 'Evidence revoked' : `${item.citations.length} citations`}</span></button>
                <button className="history-delete" aria-label="Delete answer from history" onClick={() => void removeHistory(item.trace_id)}>×</button>
              </div>)}</div>}
              {historyEnabled && historyItems.length > 0 && <button className="clear-history-button" onClick={() => void clearHistory()}>Clear my answer history</button>}
            </section>}

            <div className="conversation-meta"><span className="live-indicator" /><span>{citations.length ? `${citations.length} cited passages` : 'Your indexed knowledge'}</span><span className="meta-divider" /><span><ShieldCheck size={14} /> Single local workspace</span></div>

            <div className="conversation">
              {submitted ? <div className="user-question"><div className="question-avatar">HH</div><div><div className="message-label">YOU</div><p>{submitted}</p></div></div> : <div className="ask-empty"><Sparkles size={18} /><strong>Ask across your indexed documents</strong><span>Answers are generated from retrieved passages and link back to their exact source version.</span></div>}

              {asking && <div className="answer-block"><div className="answer-avatar"><Sparkles size={16} /></div><div className="answer-content"><div className="message-label assistant-label">SOURCEBOUND</div><p className="answer-intro">Searching your indexed sources…</p></div></div>}
              {answerError && <div className="upload-notice error-notice"><TriangleAlert size={15} /><span>{answerError}<small>Check that the API, Qdrant, and configured model endpoint are running.</small></span></div>}
              {result && <div className="answer-block">
                <div className="answer-avatar"><Sparkles size={16} /></div>
                <div className="answer-content">
                  <div className="message-label assistant-label">SOURCEBOUND <span>· {isHistorical ? 'Saved answer' : result.abstained ? 'Insufficient evidence' : `Trace ${result.trace_id}`}</span><button className="mini-icon answer-menu" aria-label="More answer options"><MoreHorizontal size={16} /></button></div>
                  <CitedAnswer answer={result.answer} citations={citations} onSelect={(chunkId) => {
                    setSelectedSource(chunkId)
                    document.getElementById('evidence-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                  }} />
                  {result.retrieval_scope !== 'latest' && <div className="answer-note"><History size={14} /><span>{result.retrieval_scope.includes('history') && (isHistorical ? 'This saved answer searched across current and older indexed versions. ' : 'This time-aware question searched across current and older indexed versions. ')}{result.retrieval_scope.includes('related') && 'Curated links to related documents also expanded the search. '}Citations show the exact source version used.</span></div>}
                  {result.conflicts.length > 0 && <section className="conflict-findings" aria-label="Potential source conflicts">
                    <div className="conflict-findings-heading"><TriangleAlert size={14} /><strong>Potential source conflict{result.conflicts.length > 1 ? 's' : ''}</strong><span>Review evidence</span></div>
                    {result.conflicts.map((conflict, index) => <div className="conflict-finding" key={`${index}-${conflict.citation_chunk_ids.join('-')}`}>
                      <p>{conflict.summary}</p>
                      <div>{conflict.citation_chunk_ids.map((chunkId) => {
                        const source = citations.find((citation) => citation.chunk_id === chunkId)
                        return source ? <button key={chunkId} onClick={() => { setSelectedSource(chunkId); document.getElementById('evidence-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }}>{source.document_title} · v{source.version_number}<ChevronRight size={12} /></button> : null
                      })}</div>
                      <div className="conflict-review-actions">
                        {conflict.disposition === 'unreviewed' && result.history_saved ? <>
                          <textarea maxLength={1000} value={conflictReviewNotes[conflict.conflict_id] ?? ''} onChange={(event) => setConflictReviewNotes((notes) => ({ ...notes, [conflict.conflict_id]: event.target.value }))} placeholder="Optional reviewer rationale" aria-label="Optional reviewer rationale" />
                          <button disabled={reviewingConflictId === conflict.conflict_id} onClick={() => void setConflictDisposition(conflict.conflict_id, 'confirmed')}>Confirm conflict</button>
                          <button disabled={reviewingConflictId === conflict.conflict_id} onClick={() => void setConflictDisposition(conflict.conflict_id, 'dismissed')}>Mark as false positive</button>
                        </> : <><span className={`conflict-disposition ${conflict.disposition}`}>{conflict.disposition === 'confirmed' ? 'Confirmed by reviewer' : conflict.disposition === 'dismissed' ? 'Marked as false positive' : 'Review requires saved answer history'}</span>{conflict.review_note && <p className="conflict-review-note">Reviewer note: {conflict.review_note}</p>}</>}
                      </div>
                    </div>)}
                    {conflictReviewError && <p className="conflict-review-error" role="status">{conflictReviewError}</p>}
                  </section>}
                  {isHistorical && <div className="answer-note"><History size={14} /><span>Original evidence snapshot{historicalAnswer ? ` from ${new Date(historicalAnswer.created_at).toLocaleString()}` : ''}. The cited version is preserved even if the source has changed.</span></div>}
                  {!isHistorical && !result.history_enabled && <div className="answer-note"><TriangleAlert size={14} /><span>Answer history is disabled by this instance’s administrator. This answer will not be retained.</span></div>}
                  {!isHistorical && result.history_enabled && !result.history_saved && <div className="answer-note"><TriangleAlert size={14} /><span>This answer could not be saved to history. Copy or review its evidence before leaving.</span></div>}
                  {result.abstained && <div className="answer-note"><TriangleAlert size={15} /><span>Sourcebound did not find enough evidence to answer confidently.</span></div>}

                  <div className="inline-evidence-head"><span><span className="evidence-check"><ShieldCheck size={12} /></span> {citations.length ? `CITES ${citations.length} PASSAGES` : 'NO CITATIONS FOUND'}</span>{citations.length > 0 && <button className="view-evidence" onClick={() => document.getElementById('evidence-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>View evidence <ArrowRight size={13} /></button>}</div>
                  <div className="citation-row">
                    {citations.map((citation, index) => <button key={citation.chunk_id} className={`citation-chip ${selectedSource === citation.chunk_id ? 'selected' : ''}`} onClick={() => setSelectedSource(citation.chunk_id)}><span className="citation-number">{String(index + 1).padStart(2, '0')}</span>{citation.document_title}<ChevronRight size={13} /></button>)}
                  </div>

                  <div className="answer-feedback"><span>Evidence is retrieved live. Verify important decisions against the linked source.</span></div>
                </div>
              </div>}

              {!submitted && <><div className="follow-up-label">TRY A QUESTION</div><div className="suggestion-list">{suggestions.map((item) => <button key={item} onClick={() => setQuestion(item)}>{item}<ArrowRight size={14} /></button>)}</div></>}
            </div>

            <div className="composer-wrap">
              <form className="composer" onSubmit={askQuestion}>
                <textarea value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit() } }} placeholder="Ask a question across your organization's knowledge…" rows={2} aria-label="Ask a question" />
                <div className="composer-footer"><span><LockKeyhole size={12} />{oidcManager ? 'OIDC identity verified · document access enforced' : 'Local workspace · authentication not configured'}</span><button type="submit" disabled={!question.trim() || asking}><span>{asking ? 'Searching' : 'Ask'}</span><Send size={14} /></button></div>
              </form>
              <p className="disclaimer">AI answers can be incomplete. Verify important decisions against the linked sources.</p>
            </div>
          </section>

          <aside className="evidence-column" id="evidence-panel">
            <div className="evidence-panel-heading"><div><div className="panel-kicker">ANSWER DETAILS</div><h2>Evidence ledger</h2></div><button className="icon-button" aria-label="Evidence options"><MoreHorizontal size={18} /></button></div>
            <p className="ledger-intro">Retrieved passages retain their source version and page provenance.</p>

            <div className="ledger-summary"><div className="summary-icon"><ShieldCheck size={16} /></div><div><strong>{citations.length ? `${citations.length} source passages` : 'Evidence appears here'}</strong><span>{result ? `Trace ${result.trace_id}` : 'Ask a question to retrieve evidence'}</span></div><span className="verified-mark">i</span></div>

            <div className="panel-section-title"><span>RETRIEVED SOURCES</span><button className="filter-button"><Filter size={13} />Filters</button></div>
            <div className="evidence-list">
              {selectedCitation ? <div className="evidence-card is-selected">
                <div className="evidence-card-top"><span className="source-index">{String(citations.indexOf(selectedCitation) + 1).padStart(2, '0')}</span><span className="version-status recent">{isHistorical ? 'Saved source snapshot' : `Indexed version · v${selectedCitation.version_number}`}</span></div>
                <h3>{selectedCitation.document_title}</h3><div className="source-location"><FileText size={12} />{selectedCitation.filename} · {selectedCitation.page_numbers.length ? `Page${selectedCitation.page_numbers.length > 1 ? 's' : ''} ${selectedCitation.page_numbers.join(', ')}` : 'Page unavailable'}</div>
                <blockquote>{selectedCitation.text}</blockquote>
                <div className="version-line"><History size={12} />Version {selectedCitation.version_number} · {selectedCitation.version_id.slice(0, 8)}<ChevronDown size={12} /></div>
              </div> : <div className="document-empty"><FileText size={18} /><span>No evidence retrieved yet.</span></div>}
            </div>

            <div className="ledger-footer">
              <div className="ledger-trace-row"><div><span className="ledger-footer-label">ANSWER HISTORY ID</span><code>{result?.trace_id ?? '—'}</code></div><button className="icon-button" aria-label="Copy answer history ID" disabled={!result} onClick={() => result && void navigator.clipboard.writeText(result.trace_id)}><ArrowDownToLine size={15} /></button></div>
              {result?.observability_trace_id && <div className="ledger-trace-row"><div><span className="ledger-footer-label">OTEL TRACE ID</span><code>{result.observability_trace_id}</code></div><button className="icon-button" aria-label="Copy OpenTelemetry trace ID" onClick={() => void navigator.clipboard.writeText(result.observability_trace_id!)}><ArrowDownToLine size={15} /></button></div>}
            </div>
          </aside>
        </div>}

        <footer className="bottom-status"><span><span className="status-pulse" />Self-hosted development</span><span>API retrieval {result ? 'connected' : 'ready'}</span><span className="status-spacer" /><button onClick={() => setActiveNav('Knowledge')}><Upload size={13} />Add documents</button><button><Activity size={13} />Usage <ChevronDown size={12} /></button></footer>
      </main>
    </div>
  )
}

export default App
