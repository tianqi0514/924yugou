export type CorpusArtifact = {
  artifact_id: string; path: string; name: string; category_number?: number | null;
  purpose?: string; writing_use?: string; boundary?: string; purpose_source?: string;
  byte_size?: number; record_count?: number; sha256?: string; media_type?: string
}
export type Category = {
  number: number; category_id?: string; name: string; group: string; format: string; stage: string | number;
  count: number; record_count?: number; content_status: string; files: string[];
  artifacts?: CorpusArtifact[];
  description: string; actual_content: string; boundary: string
}
export type CategoryPage = { category: Category; records: Record<string, unknown>[]; context: Record<string, unknown> | null; total: number; page: number; size: number; filter_options: Record<string, string[]> }
export type CorpusSummary = {
  corpus_id: string; version: string; status: string; review_status: string; embedding_status: string;
  counts: Record<string, number>; groups: { name: string; numbers: number[]; count: number }[];
  integrity: { total_files: number; listed_hashes: number; valid: boolean; errors: string[] }
  auxiliary_assets?: CorpusArtifact[]
}
export type Project = { id: string; name: string; version: number; created_at?: string; has_corpus: boolean; is_builtin: boolean }
export type Fact = { id: string; key: string; label: string; data_type: string; value: string | null; status: string; unit: string; caliber: string; as_of: string; source: string; revision: number; updated_at: string | null }
export type Rule = { id: string; name: string; target_key: string; expression: string; deps: string[] }
export type Trace = { rule_id?: string; name?: string; target?: string; expression?: string; deps?: string[]; status: string; missing?: string[]; result?: string | null; inputs?: Record<string, string | null>; reason?: string }
export type FactChange = { fact_key: string; value: string | null; source: string; caliber?: string; as_of?: string; reason: string }
export type Change = { key: string; label: string; unit: string; before: Record<string, string | null>; after: Record<string, string | null> }
export type ReportImpact = { report_id: string; report_title: string; report_version: number; position: number; text: string; fact_key: string; before: Record<string, string | null>; after: Record<string, string | null> }
export type Preview = { base_version: number; preview_token: string; changes: Change[]; trace: Trace[]; report_impacts: ReportImpact[]; expires_at: number }
export type GraphData = { focus: string; nodes: GraphEntity[]; edges: GraphEdge[]; raw_relation_count: number; derived_edge_count: number; edge_type_counts: Record<string, number>; edge_type_options: string[]; node_kind_options: string[]; truncated: boolean }
export type GraphEntity = { id: string; label: string; kind: string; source: string }
export type GraphEdge = { id: string; source: string; target: string; type: string; origin: string; derivation?: 'raw' | 'derived' }

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!response.ok) {
    let message = `请求失败（${response.status}）`
    try { const body = await response.json(); message = body.detail || message } catch { /* 无结构化错误时保留状态码 */ }
    throw new Error(message)
  }
  return response.json() as Promise<T>
}

export const post = <T,>(path: string, body: unknown) => api<T>(path, { method: 'POST', body: JSON.stringify(body) })

export function corpusPath(projectId: string, path: string): string {
  return `/projects/${encodeURIComponent(projectId)}/corpus${path}`
}

export function rawUrl(projectId: string, path: string): string {
  return `/api${corpusPath(projectId, '/raw')}?path=${encodeURIComponent(path)}`
}
