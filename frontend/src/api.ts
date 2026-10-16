export type DocumentRecord = {
  id: string
  title: string
  filename: string
  latest_version: number
  access_groups: string[] | null
  status: 'queued' | 'processing' | 'indexing' | 'ready' | 'failed'
  created_at: string
  updated_at: string
}

export type DocumentVersionRecord = {
  id: string
  version: number
  status: DocumentRecord['status']
  sha256: string
  byte_size: number
  page_count: number | null
  parser: string | null
  error: string | null
  created_at: string
  processed_at: string | null
}

export type Citation = {
  chunk_id: string
  document_id: string
  document_title: string
  filename: string
  version_id: string
  version_number: number
  page_numbers: number[]
  text: string
  score: number
}

export type ConflictFinding = {
  conflict_id: string
  summary: string
  citation_chunk_ids: string[]
  disposition: 'unreviewed' | 'confirmed' | 'dismissed'
  review_note: string | null
  reviewed_at: string | null
}

export type AskResult = {
  answer: string
  citations: Citation[]
  conflicts: ConflictFinding[]
  abstained: boolean
  trace_id: string
  observability_trace_id?: string | null
  model_name: string
  embedding_model: string
  reranker_model: string | null
  retrieval_version: string
  retrieval_scope: 'latest' | 'latest_plus_history' | 'latest_plus_related' | 'latest_plus_history_and_related'
  prompt_version: string
  history_enabled: boolean
  history_saved: boolean
}

export type AnswerHistoryItem = Omit<AskResult, 'history_saved' | 'history_enabled'> & {
  question: string
  created_at: string
  redacted: boolean
}

export type AnswerHistory = { enabled: boolean; retention_days: number; traces: AnswerHistoryItem[] }

export type AuditEvent = {
  id: string
  actor_subject: string
  action: string
  resource_type: string
  resource_id: string
  details: Record<string, unknown>
  created_at: string
}

export type DocumentChanges = {
  document_id: string
  document_title: string
  from_version: number
  to_version: number
  additions: number
  deletions: number
  unified_diff: string
}

export type DocumentRelationship = {
  id: string
  source_document_id: string
  source_document_title: string
  target_document_id: string
  target_document_title: string
  relationship_type: 'related_to' | 'implements' | 'supersedes' | 'depends_on'
  created_at: string
}

export type AccessGroup = {
  id: string
  name: string
  assigned_document_count: number
  created_at: string
}

export type OrganizationUsage = {
  document_count: number
  document_limit: number
  storage_bytes: number
  storage_limit_bytes: number
  updated_at: string
}

const apiBase = (import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000').replace(/\/$/, '')
let accessTokenProvider: (() => Promise<string | undefined>) | null = null

export function setAccessTokenProvider(provider: (() => Promise<string | undefined>) | null) {
  accessTokenProvider = provider
}

async function requestHeaders(headers?: HeadersInit): Promise<Headers> {
  const result = new Headers(headers)
  const token = await accessTokenProvider?.()
  if (token) result.set('Authorization', `Bearer ${token}`)
  return result
}

export async function listDocuments(): Promise<DocumentRecord[]> {
  const response = await fetch(`${apiBase}/api/v1/documents`, { headers: await requestHeaders() })
  if (!response.ok) throw new Error(`Could not load documents (${response.status}).`)
  return response.json() as Promise<DocumentRecord[]>
}

export async function askKnowledgeBase(question: string): Promise<AskResult> {
  const response = await fetch(`${apiBase}/api/v1/ask`, {
    method: 'POST',
    headers: await requestHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ question }),
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null
    throw new Error(body?.detail ?? `Could not get an answer (${response.status}).`)
  }
  return response.json() as Promise<AskResult>
}

export async function listAnswerHistory(): Promise<AnswerHistory> {
  const response = await fetch(`${apiBase}/api/v1/history`, { headers: await requestHeaders() })
  if (!response.ok) throw new Error(`Could not load answer history (${response.status}).`)
  return response.json() as Promise<AnswerHistory>
}

export async function deleteAnswerHistory(traceId: string): Promise<void> {
  const response = await fetch(`${apiBase}/api/v1/history/${encodeURIComponent(traceId)}`, {
    method: 'DELETE', headers: await requestHeaders(),
  })
  if (!response.ok) throw new Error(`Could not delete answer history (${response.status}).`)
}

export async function reviewConflict(
  traceId: string,
  conflictId: string,
  disposition: 'confirmed' | 'dismissed',
  reviewNote?: string,
): Promise<ConflictFinding> {
  const response = await fetch(`${apiBase}/api/v1/history/${encodeURIComponent(traceId)}/conflicts/${encodeURIComponent(conflictId)}`, {
    method: 'PUT',
    headers: await requestHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ disposition, review_note: reviewNote?.trim() || null }),
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null
    throw new Error(body?.detail ?? `Could not record conflict review (${response.status}).`)
  }
  return response.json() as Promise<ConflictFinding>
}

export async function clearAnswerHistory(): Promise<void> {
  const response = await fetch(`${apiBase}/api/v1/history`, { method: 'DELETE', headers: await requestHeaders() })
  if (!response.ok) throw new Error(`Could not clear answer history (${response.status}).`)
}

export async function listAuditEvents(limit = 100): Promise<AuditEvent[]> {
  const response = await fetch(`${apiBase}/api/v1/audit-events?limit=${limit}`, { headers: await requestHeaders() })
  if (!response.ok) throw new Error(`Could not load audit events (${response.status}).`)
  return response.json() as Promise<AuditEvent[]>
}

export async function getDocumentChanges(documentId: string): Promise<DocumentChanges> {
  const response = await fetch(`${apiBase}/api/v1/documents/${documentId}/changes`, { headers: await requestHeaders() })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null
    throw new Error(body?.detail ?? `Could not load version changes (${response.status}).`)
  }
  return response.json() as Promise<DocumentChanges>
}

export async function listDocumentRelationships(documentId: string): Promise<DocumentRelationship[]> {
  const response = await fetch(`${apiBase}/api/v1/documents/${encodeURIComponent(documentId)}/relationships`, { headers: await requestHeaders() })
  if (!response.ok) throw new Error(`Could not load document relationships (${response.status}).`)
  return response.json() as Promise<DocumentRelationship[]>
}

export async function createDocumentRelationship(
  documentId: string,
  targetDocumentId: string,
  relationshipType: DocumentRelationship['relationship_type'],
): Promise<DocumentRelationship> {
  const response = await fetch(`${apiBase}/api/v1/documents/${encodeURIComponent(documentId)}/relationships`, {
    method: 'POST',
    headers: await requestHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ target_document_id: targetDocumentId, relationship_type: relationshipType }),
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null
    throw new Error(body?.detail ?? `Could not add document relationship (${response.status}).`)
  }
  return response.json() as Promise<DocumentRelationship>
}

export async function deleteDocumentRelationship(documentId: string, relationshipId: string): Promise<void> {
  const response = await fetch(`${apiBase}/api/v1/documents/${encodeURIComponent(documentId)}/relationships/${encodeURIComponent(relationshipId)}`, {
    method: 'DELETE', headers: await requestHeaders(),
  })
  if (!response.ok) throw new Error(`Could not remove document relationship (${response.status}).`)
}

export async function listAccessGroups(): Promise<AccessGroup[]> {
  const response = await fetch(`${apiBase}/api/v1/groups`, { headers: await requestHeaders() })
  if (!response.ok) throw new Error(`Could not load access groups (${response.status}).`)
  return response.json() as Promise<AccessGroup[]>
}

export async function getOrganizationUsage(): Promise<OrganizationUsage> {
  const response = await fetch(`${apiBase}/api/v1/organization/usage`, { headers: await requestHeaders() })
  if (!response.ok) throw new Error(`Could not load organization usage (${response.status}).`)
  return response.json() as Promise<OrganizationUsage>
}

export async function createAccessGroup(name: string): Promise<AccessGroup> {
  const response = await fetch(`${apiBase}/api/v1/groups`, {
    method: 'POST',
    headers: await requestHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ name }),
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null
    throw new Error(body?.detail ?? `Could not create access group (${response.status}).`)
  }
  return response.json() as Promise<AccessGroup>
}

export async function deleteAccessGroup(groupId: string): Promise<void> {
  const response = await fetch(`${apiBase}/api/v1/groups/${encodeURIComponent(groupId)}`, {
    method: 'DELETE', headers: await requestHeaders(),
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null
    throw new Error(body?.detail ?? `Could not delete access group (${response.status}).`)
  }
}

export async function updateDocumentAccess(documentId: string, accessGroups: string[] | null): Promise<DocumentRecord> {
  const response = await fetch(`${apiBase}/api/v1/documents/${documentId}/access`, {
    method: 'PUT',
    headers: await requestHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ access_groups: accessGroups }),
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null
    throw new Error(body?.detail ?? `Could not update document access (${response.status}).`)
  }
  return response.json() as Promise<DocumentRecord>
}

export async function deleteDocument(documentId: string): Promise<void> {
  const response = await fetch(`${apiBase}/api/v1/documents/${encodeURIComponent(documentId)}`, {
    method: 'DELETE', headers: await requestHeaders(),
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null
    throw new Error(body?.detail ?? `Could not delete document (${response.status}).`)
  }
}

export async function uploadDocument(file: File): Promise<DocumentRecord> {
  return sendDocumentFile(`${apiBase}/api/v1/documents`, file)
}

export async function uploadDocumentVersion(documentId: string, file: File): Promise<void> {
  await sendDocumentFile(`${apiBase}/api/v1/documents/${documentId}/versions`, file)
}

export async function reprocessDocument(documentId: string): Promise<DocumentVersionRecord> {
  const response = await fetch(`${apiBase}/api/v1/documents/${encodeURIComponent(documentId)}/reprocess`, {
    method: 'POST', headers: await requestHeaders(),
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null
    throw new Error(body?.detail ?? `Could not retry document processing (${response.status}).`)
  }
  return response.json() as Promise<DocumentVersionRecord>
}

async function sendDocumentFile(url: string, file: File): Promise<DocumentRecord> {
  const form = new FormData()
  form.append('file', file)
  const response = await fetch(url, { method: 'POST', body: form, headers: await requestHeaders() })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null
    throw new Error(body?.detail ?? `Could not upload document (${response.status}).`)
  }
  return response.json() as Promise<DocumentRecord>
}
