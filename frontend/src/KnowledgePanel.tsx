import { useCallback, useEffect, useRef, useState, type ChangeEvent } from 'react'
import { AlertCircle, ArrowRight, CheckCircle2, Clock3, FileText, Link2, LoaderCircle, LockKeyhole, RefreshCw, Trash2, Upload, Users } from 'lucide-react'
import { createAccessGroup, createDocumentRelationship, deleteAccessGroup, deleteDocument, deleteDocumentRelationship, getDocumentChanges, getOrganizationUsage, listAccessGroups, listDocumentRelationships, listDocuments, reprocessDocument, updateDocumentAccess, uploadDocument, uploadDocumentVersion, type AccessGroup, type DocumentChanges, type DocumentRecord, type DocumentRelationship, type OrganizationUsage } from './api'
import './KnowledgePanel.css'

type Props = { view: 'Knowledge' | 'Changes'; isWorkspaceAdmin: boolean }
const oidcConfigured = Boolean(import.meta.env.VITE_OIDC_AUTHORITY && import.meta.env.VITE_OIDC_CLIENT_ID)

function statusLabel(status: DocumentRecord['status']) {
  switch (status) {
    case 'queued': return 'Queued'
    case 'processing': return 'Parsing'
    case 'indexing': return 'Indexing'
    case 'ready': return 'Parsed'
    case 'failed': return 'Needs attention'
  }
}

function formatBytes(bytes: number) {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KiB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GiB`
}

export function KnowledgePanel({ view, isWorkspaceAdmin }: Props) {
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [versionTarget, setVersionTarget] = useState<string | null>(null)
  const [changes, setChanges] = useState<DocumentChanges | null>(null)
  const [loadingChanges, setLoadingChanges] = useState(false)
  const [changeError, setChangeError] = useState('')
  const [accessDocument, setAccessDocument] = useState<DocumentRecord | null>(null)
  const [accessRestricted, setAccessRestricted] = useState(false)
  const [selectedAccessGroups, setSelectedAccessGroups] = useState<string[]>([])
  const [accessGroups, setAccessGroups] = useState<AccessGroup[]>([])
  const [organizationUsage, setOrganizationUsage] = useState<OrganizationUsage | null>(null)
  const [newAccessGroup, setNewAccessGroup] = useState('')
  const [savingAccessGroup, setSavingAccessGroup] = useState(false)
  const [savingAccess, setSavingAccess] = useState(false)
  const [retryingDocument, setRetryingDocument] = useState<string | null>(null)
  const [deletingDocument, setDeletingDocument] = useState<string | null>(null)
  const [relationshipDocument, setRelationshipDocument] = useState<DocumentRecord | null>(null)
  const [relationships, setRelationships] = useState<DocumentRelationship[]>([])
  const [relationshipTargetId, setRelationshipTargetId] = useState('')
  const [relationshipType, setRelationshipType] = useState<DocumentRelationship['relationship_type']>('related_to')
  const [loadingRelationships, setLoadingRelationships] = useState(false)
  const [savingRelationship, setSavingRelationship] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const refresh = useCallback(async () => {
    try {
      const latest = await listDocuments()
      setDocuments(latest)
      setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The API could not be reached.')
    } finally {
      setLoading(false)
    }
  }, [])

  const refreshAccessGroups = useCallback(async () => {
    if (!isWorkspaceAdmin) return
    try {
      setAccessGroups(await listAccessGroups())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load access groups.')
    }
  }, [isWorkspaceAdmin])

  const refreshOrganizationUsage = useCallback(async () => {
    if (!isWorkspaceAdmin) return
    try {
      setOrganizationUsage(await getOrganizationUsage())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load organization usage.')
    }
  }, [isWorkspaceAdmin])

  useEffect(() => {
    let active = true
    const loadInitialData = async () => {
      try {
        const latest = await listDocuments()
        if (active) {
          setDocuments(latest)
          setError('')
        }
      } catch (err) {
        if (active) setError(err instanceof Error ? err.message : 'The API could not be reached.')
      } finally {
        if (active) setLoading(false)
      }
      if (!active || !isWorkspaceAdmin) return
      const [groupsResult, usageResult] = await Promise.allSettled([listAccessGroups(), getOrganizationUsage()])
      if (!active) return
      if (groupsResult.status === 'fulfilled') setAccessGroups(groupsResult.value)
      else setError(groupsResult.reason instanceof Error ? groupsResult.reason.message : 'Could not load access groups.')
      if (usageResult.status === 'fulfilled') setOrganizationUsage(usageResult.value)
      else setError(usageResult.reason instanceof Error ? usageResult.reason.message : 'Could not load organization usage.')
    }
    void loadInitialData()
    const timer = window.setInterval(() => {
      void Promise.resolve().then(refresh)
      void Promise.resolve().then(refreshOrganizationUsage)
    }, 4000)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [isWorkspaceAdmin, refresh, refreshOrganizationUsage])

  async function onFileSelected(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    const target = versionTarget
    setVersionTarget(null)
    setUploading(true)
    setNotice('')
    setError('')
    try {
      if (target) {
        await uploadDocumentVersion(target, file)
        setNotice(`${file.name} was uploaded as a new document version.`)
      } else {
        const created = await uploadDocument(file)
        setNotice(`${created.filename} was added. PDF parsing is queued.`)
      }
      await refresh()
      await refreshAccessGroups()
      await refreshOrganizationUsage()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed.')
    } finally {
      setUploading(false)
    }
  }

  async function saveAccess() {
    if (!accessDocument) return
    setSavingAccess(true)
    setError('')
    try {
      const groups = accessRestricted ? selectedAccessGroups : null
      const updated = await updateDocumentAccess(accessDocument.id, groups)
      setDocuments((current) => current.map((document) => document.id === updated.id ? { ...document, access_groups: updated.access_groups } : document))
      await refreshAccessGroups()
      setAccessDocument(null)
      setNotice(`Access updated for ${updated.title}.`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update document access.')
    } finally {
      setSavingAccess(false)
    }
  }

  async function addAccessGroup() {
    const name = newAccessGroup.trim()
    if (!name) return
    setSavingAccessGroup(true)
    setError('')
    try {
      const created = await createAccessGroup(name)
      setAccessGroups((current) => [...current, created].sort((left, right) => left.name.localeCompare(right.name)))
      setNewAccessGroup('')
      setNotice(`Access group “${created.name}” is ready to use in document policies.`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create access group.')
    } finally {
      setSavingAccessGroup(false)
    }
  }

  async function removeAccessGroup(group: AccessGroup) {
    if (group.assigned_document_count) return
    setSavingAccessGroup(true)
    setError('')
    try {
      await deleteAccessGroup(group.id)
      setAccessGroups((current) => current.filter((item) => item.id !== group.id))
      setNotice(`Access group “${group.name}” was deleted.`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete access group.')
      await refresh()
    } finally {
      setSavingAccessGroup(false)
    }
  }

  async function retryProcessing(document: DocumentRecord) {
    setRetryingDocument(document.id)
    setError('')
    try {
      await reprocessDocument(document.id)
      setNotice(`Reprocessing was queued for ${document.title}.`)
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not retry document processing.')
    } finally {
      setRetryingDocument(null)
    }
  }

  async function openRelationships(document: DocumentRecord) {
    setRelationshipDocument(document)
    setLoadingRelationships(true)
    setRelationships([])
    setRelationshipTargetId('')
    setError('')
    try {
      setRelationships(await listDocumentRelationships(document.id))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load document relationships.')
    } finally {
      setLoadingRelationships(false)
    }
  }

  async function addRelationship() {
    if (!relationshipDocument || !relationshipTargetId) return
    setSavingRelationship(true)
    setError('')
    try {
      await createDocumentRelationship(relationshipDocument.id, relationshipTargetId, relationshipType)
      setRelationships(await listDocumentRelationships(relationshipDocument.id))
      setRelationshipTargetId('')
      setNotice('Curated document relationship added.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not add document relationship.')
    } finally {
      setSavingRelationship(false)
    }
  }

  async function removeRelationship(relationship: DocumentRelationship) {
    if (!relationshipDocument) return
    setSavingRelationship(true)
    setError('')
    try {
      await deleteDocumentRelationship(relationshipDocument.id, relationship.id)
      setRelationships((current) => current.filter((item) => item.id !== relationship.id))
      setNotice('Curated document relationship removed.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not remove document relationship.')
    } finally {
      setSavingRelationship(false)
    }
  }

  async function removeDocument(document: DocumentRecord) {
    const confirmed = window.confirm(`Permanently delete “${document.title}” and all its versions? This removes the PDFs, parsed files, and search index entries. Saved answer history entries citing it will also be deleted. The audit event is retained.`)
    if (!confirmed) return
    setDeletingDocument(document.id)
    setError('')
    setNotice('')
    try {
      await deleteDocument(document.id)
      setDocuments((current) => current.filter((item) => item.id !== document.id))
      if (relationshipDocument?.id === document.id) setRelationshipDocument(null)
      setRelationships((current) => current.filter((item) => item.source_document_id !== document.id && item.target_document_id !== document.id))
      window.dispatchEvent(new CustomEvent('sourcebound:document-deleted', { detail: { documentId: document.id } }))
      setNotice(`${document.title} and its stored versions were permanently deleted.`)
      await refreshOrganizationUsage()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete this source.')
    } finally {
      setDeletingDocument(null)
    }
  }

  const visibleDocuments = view === 'Changes'
    ? documents.filter((document) => document.latest_version > 1)
    : documents
  const accessGroupChoices = accessDocument
    ? [...new Set([...accessGroups.map((group) => group.name), ...(accessDocument.access_groups ?? [])])].sort((left, right) => left.localeCompare(right))
    : []

  return (
    <section className="knowledge-page">
      <div className="knowledge-page-heading">
        <div>
          <div className="eyebrow"><span className="eyebrow-dot" />DOCUMENT WORKSPACE</div>
          <h1>{view === 'Changes' ? 'Knowledge changes' : 'Your knowledge base'}</h1>
          <p>{view === 'Changes' ? 'Review document versions as your source material evolves.' : 'Add source documents and follow each file through parsing.'}</p>
        </div>
        <button className="upload-primary" onClick={() => { setVersionTarget(null); inputRef.current?.click() }} disabled={uploading}>
          {uploading ? <LoaderCircle className="spin" size={15} /> : <Upload size={15} />}
          {uploading ? 'Uploading…' : 'Upload PDF'}
        </button>
        <input ref={inputRef} type="file" accept="application/pdf,.pdf" hidden onChange={onFileSelected} />
      </div>

      <div className="local-data-notice"><AlertCircle size={15} /><span>{oidcConfigured ? <><strong>Organization sign-in:</strong> identity and per-document group permissions are enforced by the API.</> : <><strong>Local development:</strong> authentication is disabled. Use only non-sensitive test documents.</>}</span></div>

      {notice && <div className="upload-notice success-notice"><CheckCircle2 size={15} />{notice}</div>}
      {error && <div className="upload-notice error-notice"><AlertCircle size={15} /><span>{error}{error.includes('Could not load') && <small>Start the API in the backend folder, then refresh.</small>}</span><button onClick={() => void refresh()}><RefreshCw size={13} />Retry</button></div>}

      {isWorkspaceAdmin && <section className="access-group-manager">
        <div className="access-group-heading"><div><Users size={15} /><strong>Organization access groups</strong></div><span>Reuse exact identity group names in document policies.</span></div>
        <form className="access-group-create" onSubmit={(event) => { event.preventDefault(); void addAccessGroup() }}>
          <input value={newAccessGroup} onChange={(event) => setNewAccessGroup(event.target.value)} maxLength={255} placeholder="e.g. security-ops" aria-label="New organization access group" />
          <button type="submit" disabled={!newAccessGroup.trim() || savingAccessGroup}>{savingAccessGroup ? 'Saving…' : 'Add group'}</button>
        </form>
        {accessGroups.length === 0 ? <small className="access-group-empty">No groups registered yet. Add a group name that exactly matches an identity-provider claim.</small> : <div className="access-group-list">{accessGroups.map((group) => <div className="access-group-item" key={group.id}><span><strong>{group.name}</strong><small>{group.assigned_document_count} document {group.assigned_document_count === 1 ? 'policy' : 'policies'}</small></span><button type="button" className="row-action row-action-danger" disabled={savingAccessGroup || group.assigned_document_count > 0} title={group.assigned_document_count ? 'Remove this group from document policies first' : 'Delete unused access group'} aria-label={`Delete access group ${group.name}`} onClick={() => void removeAccessGroup(group)}>×</button></div>)}</div>}
      </section>}
      {isWorkspaceAdmin && organizationUsage && <section className="organization-usage" aria-label="Organization storage usage">
        <div><strong>Organization usage</strong><span>{organizationUsage.document_count.toLocaleString()} / {organizationUsage.document_limit.toLocaleString()} documents</span></div>
        <div className="usage-meter" role="progressbar" aria-label="Stored PDF usage" aria-valuemin={0} aria-valuemax={organizationUsage.storage_limit_bytes} aria-valuenow={organizationUsage.storage_bytes}><span style={{ width: `${Math.min(100, organizationUsage.storage_limit_bytes ? organizationUsage.storage_bytes / organizationUsage.storage_limit_bytes * 100 : 0)}%` }} /></div>
        <small>{formatBytes(organizationUsage.storage_bytes)} of {formatBytes(organizationUsage.storage_limit_bytes)} source storage</small>
      </section>}

      <div className="document-toolbar">
        <div className="document-count"><strong>{visibleDocuments.length}</strong> {view === 'Changes' ? 'updated documents' : 'documents'}</div>
        <button onClick={() => void refresh()} className="refresh-button"><RefreshCw size={13} />Refresh status</button>
      </div>

      {view === 'Changes' && (loadingChanges || changeError || changes) && <section className="change-review">
        {loadingChanges ? <p>Comparing parsed source versions…</p> : changeError ? <p className="change-error">{changeError}</p> : changes && <>
          <div className="change-review-head"><div><strong>{changes.document_title}</strong><span>v{changes.from_version} → v{changes.to_version}</span></div><button className="row-action" aria-label="Close version comparison" onClick={() => setChanges(null)}>×</button></div>
          <div className="change-totals"><span className="diff-add">+{changes.additions} added lines</span><span className="diff-remove">−{changes.deletions} removed lines</span></div>
          <pre>{changes.unified_diff || 'No text changes found between the latest parsed versions.'}</pre>
        </>}
      </section>}

      {accessDocument && <section className="access-editor">
        <div><strong>Who can use “{accessDocument.title}”?</strong><button className="row-action" onClick={() => setAccessDocument(null)} aria-label="Close access editor">×</button></div>
        <label className="access-toggle"><input type="checkbox" checked={accessRestricted} onChange={(event) => setAccessRestricted(event.target.checked)} />Restrict access to selected identity groups</label>
        {accessRestricted && <select className="access-groups-select" multiple value={selectedAccessGroups} onChange={(event) => setSelectedAccessGroups(Array.from(event.target.selectedOptions, (option) => option.value))} aria-label="Allowed identity groups">{accessGroupChoices.map((name) => <option key={name} value={name}>{name}{!accessGroups.some((group) => group.name === name) ? ' (legacy policy value)' : ''}</option>)}</select>}
        <small>{accessRestricted ? 'Select one or more exact identity-provider groups. An empty selection blocks non-admin members.' : 'Visible to all members of this organization. Workspace admins retain access.'}</small>
        <button className="save-access-button" onClick={() => void saveAccess()} disabled={savingAccess}>{savingAccess ? 'Saving…' : 'Save access'}</button>
      </section>}

      {relationshipDocument && <section className="access-editor relationship-editor">
        <div><strong>Curated relationships for “{relationshipDocument.title}”</strong><button className="row-action" onClick={() => setRelationshipDocument(null)} aria-label="Close relationships editor">×</button></div>
        <p>These links can expand explicitly relationship-focused questions. Retrieval still checks each document’s access policy.</p>
        {loadingRelationships ? <small>Loading relationships…</small> : relationships.length === 0 ? <small>No curated relationships yet.</small> : <div className="relationship-list">{relationships.map((relationship) => <div className="relationship-row" key={relationship.id}><span><strong>{relationship.source_document_title}</strong> <em>{relationship.relationship_type.replaceAll('_', ' ')}</em> <strong>{relationship.target_document_title}</strong></span>{isWorkspaceAdmin && <button className="row-action" disabled={savingRelationship} onClick={() => void removeRelationship(relationship)} aria-label="Remove document relationship">×</button>}</div>)}</div>}
        {isWorkspaceAdmin && <div className="relationship-create"><select value={relationshipTargetId} onChange={(event) => setRelationshipTargetId(event.target.value)} aria-label="Related document"><option value="">Choose a related document</option>{documents.filter((document) => document.id !== relationshipDocument.id).map((document) => <option key={document.id} value={document.id}>{document.title}</option>)}</select><select value={relationshipType} onChange={(event) => setRelationshipType(event.target.value as DocumentRelationship['relationship_type'])} aria-label="Relationship type"><option value="related_to">Related to</option><option value="implements">Implements</option><option value="supersedes">Supersedes</option><option value="depends_on">Depends on</option></select><button className="save-access-button" onClick={() => void addRelationship()} disabled={!relationshipTargetId || savingRelationship}>{savingRelationship ? 'Saving…' : 'Add relationship'}</button></div>}
      </section>}

      {loading ? <div className="document-empty"><LoaderCircle className="spin" size={19} /><span>Loading documents…</span></div> : visibleDocuments.length === 0 ? (
        <div className="document-empty">
          <div className="empty-file-icon"><FileText size={21} /></div>
          <strong>{view === 'Changes' ? 'No document updates yet' : 'Your knowledge base is empty'}</strong>
          <span>{view === 'Changes' ? 'Upload a newer version of a document to start its version history.' : 'Upload a PDF to create the first source and parse record.'}</span>
          {view === 'Knowledge' && <button onClick={() => { setVersionTarget(null); inputRef.current?.click() }}><Upload size={14} />Choose a PDF</button>}
        </div>
      ) : (
        <div className="document-table-wrap">
          <table className="document-table">
            <thead><tr><th>DOCUMENT</th><th>VERSION</th><th>PROCESSING</th><th>LAST UPDATED</th><th /></tr></thead>
            <tbody>{visibleDocuments.map((document) => (
              <tr key={document.id}>
                <td><div className="document-name-cell"><span className="document-file-icon"><FileText size={15} /></span><span><strong>{document.title}</strong><small>{document.filename} · {document.access_groups === null ? 'All org members' : document.access_groups.length ? `${document.access_groups.length} groups` : 'Admins only'}</small></span></div></td>
                <td><span className="version-pill">v{document.latest_version}</span></td>
                <td><span className={`processing-status ${document.status}`}><i />{statusLabel(document.status)}</span></td>
                <td><span className="date-cell"><Clock3 size={13} />{new Date(document.updated_at).toLocaleString()}</span></td>
                <td className="document-actions">{view === 'Changes' && <button className="diff-button" onClick={async () => { setLoadingChanges(true); setChangeError(''); setChanges(null); try { setChanges(await getDocumentChanges(document.id)) } catch (err) { setChangeError(err instanceof Error ? err.message : 'Could not compare versions.') } finally { setLoadingChanges(false) } }}>Compare</button>}{document.status === 'failed' && <button className="diff-button" disabled={!isWorkspaceAdmin || retryingDocument === document.id} onClick={() => void retryProcessing(document)}>{retryingDocument === document.id ? 'Retrying…' : 'Retry parse'}</button>}<button className="row-action" title="View curated document relationships" aria-label={`View relationships for ${document.title}`} onClick={() => void openRelationships(document)}><Link2 size={14} /></button><button className="row-action" title="Manage document access" aria-label={`Manage access to ${document.title}`} disabled={!isWorkspaceAdmin} onClick={() => { setAccessDocument(document); setAccessRestricted(document.access_groups !== null); setSelectedAccessGroups(document.access_groups ?? []) }}><LockKeyhole size={14} /></button><button className="row-action" title="Upload a new version" aria-label={`Upload a new version of ${document.title}`} onClick={() => { setVersionTarget(document.id); inputRef.current?.click() }}><RefreshCw size={14} /></button>{isWorkspaceAdmin && <button className="row-action row-action-danger" title="Delete document and all versions" aria-label={`Permanently delete ${document.title}`} disabled={deletingDocument === document.id} onClick={() => void removeDocument(document)}>{deletingDocument === document.id ? <LoaderCircle className="spin" size={14} /> : <Trash2 size={14} />}</button>}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}

      <div className="ingestion-note"><span className="note-step">1</span><span><strong>Upload</strong><small>PDF up to 50 MB</small></span><ArrowRight size={14} /><span className="note-step">2</span><span><strong>Parse</strong><small>Docling text and page provenance</small></span><ArrowRight size={14} /><span className="note-step">3</span><span><strong>Index</strong><small>Dense + sparse retrieval</small></span></div>
    </section>
  )
}

\n