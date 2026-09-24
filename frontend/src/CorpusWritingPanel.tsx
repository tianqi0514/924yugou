import { useCallback, useEffect, useMemo, useState } from 'react'
import { ArrowRight, Check, ExternalLink, FileSearch, Sparkles, X } from 'lucide-react'
import { api, post, rawUrl, type Project } from './api'

export type CorpusSourceRef = {
  corpus_id: string; corpus_version: string; category_id: string; artifact_id: string;
  record_id: string; semantic_id?: string | null
}
type ReportType = 'feasibility' | 'accident_investigation' | 'other'
type Reference = { selected: boolean; corpus_id: string; corpus_version: string; source_project_id: string;
  target_report_type?: ReportType | null; compatible?: boolean }
type Section = { id: string; title: string; level: number; reusable: boolean; chunk_count: number;
  guided_available?: boolean; model_available?: boolean; availability_reason?: string }
type PackageItem = {
  item_id: string; category_id: string; category_number: number; artifact_id: string;
  record_id: string; semantic_id?: string | null; role: string;
  decision: 'selectable' | 'review_only' | 'excluded'; reason: string; location: unknown; summary: string
}
type PackageSlot = { slot_id: string; label: string; unit: string; fact_key: string | null; value: string | null; status: string }
type MappingData = { slots: { id: string; label: string; data_type: string; unit: string }[];
  bindings: Record<string, string>; facts: { key: string; label: string; data_type: string; unit: string; value: string | null }[] }
type PackageIssue = { code: string; severity: string; message: string; item_ids: string[] }
type WritingPackage = {
  section: { id: string; title: string; level: number }; project_id: string; project_version: number;
  corpus_id: string; corpus_version: string; items: PackageItem[]; slots: PackageSlot[];
  issues: PackageIssue[]; required_item_ids?: string[];
  facts: { key: string; label: string; value: string | null; status: string; unit: string; revision: number }[]
}
type CandidateParagraph = { type: string; children: unknown[]; fact_keys?: string[]; source_refs?: CorpusSourceRef[];
  project_rule_refs?: { rule_id: string; expression: string; target_key: string; deps: string[] }[] }
type Candidate = {
  section_id: string; paragraphs: CandidateParagraph[]; issues: PackageIssue[]; used_items: string[];
  rejected_items: { item_id: string; reason: string }[]; base_version: number; project_version: number;
  preview_token: string; expires_at: number
}
type SourceDetail = { source_ref: CorpusSourceRef; payload: Record<string, unknown> | null; location: unknown; summary: string }
type SourceImpact = { report_id: string; report_title: string; report_version: number; block_id: string;
  section_id: string; position: number; text: string }

const roleLabels: Record<string, string> = {
  outline: '章节结构', template: '章节模板', historical_text: '历史原文', skeleton: '写作骨架',
  historical_node: '历史语义节点', rule: '计算规则', claim: '论断', historical_evidence: '历史证据',
  historical_excerpt: '历史摘录', conflict: '冲突', gap: '缺口', style: '写作样式',
}
const decisionLabels = { selectable: '可选用', review_only: '仅供核对', excluded: '不适用' }

function versionLabel(value: string): string { return /^v/i.test(value) ? value : `v${value}` }
export function locationText(value: unknown): string {
  if (typeof value === 'string') return value
  if (typeof value === 'number') return String(value)
  if (Array.isArray(value)) return value.map(locationText).filter(Boolean).join('、')
  if (!value || typeof value !== 'object') return '未记录'
  const location = value as Record<string, unknown>
  const pieces = [location.section || location.section_id, location.chunk_id,
    typeof location.page === 'number' ? `第 ${location.page} 页` : null,
    Array.isArray(location.span) ? `字符 ${location.span.join('–')}` : null].filter(Boolean)
  return pieces.length ? pieces.join(' · ') : Object.entries(location).filter(([, item]) => ['string', 'number'].includes(typeof item))
    .slice(0, 4).map(([key, item]) => `${key}: ${item}`).join(' · ') || '未记录'
}
export function sourceLocation(source: SourceDetail | null, fallback: unknown): unknown {
  const payload = source?.payload
  const linked = payload?.link && typeof payload.link === 'object' ? payload.link as Record<string, unknown> : null
  return payload?.register_location ||
    (Array.isArray(payload?.excerpt_locations) ? payload.excerpt_locations[0] : null) ||
    (typeof payload?.page === 'number' ? { chunk_id: typeof payload.id === 'string' && /^C\d+$/.test(payload.id) ? payload.id : null,
      page: payload.page, span: payload.span } : null) ||
    (linked && typeof linked.page === 'number' ? { page: linked.page, span: linked.span } : null) ||
    source?.location || fallback
}
export function wordLocator(value: unknown): string | null {
  if (!value || typeof value !== 'object') return null
  const locator = value as Record<string, unknown>
  return typeof locator.xpath === 'string' ? `${locator.part || 'Word'} · ${locator.xpath}` : null
}
export function originalWordLocator(source: SourceDetail): string | null {
  const payload = source.payload
  const linked = payload?.link && typeof payload.link === 'object' ? payload.link as Record<string, unknown> : null
  const main = payload?.source_locator || (payload?.register_location as Record<string, unknown> | undefined)?.source_locator ||
    linked?.source_locator || source.location
  return wordLocator(main)
}

function paragraphText(value: unknown): string {
  if (!value || typeof value !== 'object') return ''
  const node = value as { text?: unknown; display?: unknown; children?: unknown }
  if (typeof node.text === 'string') return node.text
  if (typeof node.display === 'string') return node.display
  return Array.isArray(node.children) ? node.children.map(paragraphText).join('') : ''
}

export function primarySourceText(payload: Record<string, unknown> | null): string | null {
  if (!payload) return null
  for (const key of ['text', 'excerpt', 'content', 'body', 'quote', 'statement']) {
    const value = payload[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return null
}

function relationIds(value: unknown): string[] {
  const found = new Set<string>()
  const walk = (node: unknown) => {
    if (typeof node === 'string') {
      for (const id of node.match(/\b(?:N\d+|EV-[\w-]+|EX-[\w-]+|C\d+|X\d+|L\d+)\b/g) || []) found.add(id)
    } else if (Array.isArray(node)) node.forEach(walk)
    else if (node && typeof node === 'object') Object.values(node).forEach(walk)
  }
  walk(value)
  return [...found]
}
function relatedGroups(payload: Record<string, unknown> | null): { label: string; ids: string[] }[] {
  if (!payload) return []
  return [
    { label: '支撑节点', ids: relationIds(payload.supports) },
    { label: '应引证据', ids: relationIds(payload.must_cite) },
    { label: '冲突节点', ids: relationIds(payload.nodes) },
    { label: '冲突证据', ids: relationIds(payload.evidence) },
    { label: '证据摘录', ids: relationIds(payload.excerpt_ids) },
    { label: '原文切块', ids: relationIds(payload.source_location || payload.source_locations || payload.excerpt_locations).filter((id) => id.startsWith('C')) },
  ].filter((group) => group.ids.length)
}
function relatedCategory(id: string): number | null {
  if (/^N\d+$/.test(id)) return 12
  if (/^EV-/.test(id)) return 18
  if (/^EX-/.test(id)) return 19
  if (/^C\d+$/.test(id)) return 9
  if (/^X\d+$/.test(id)) return 23
  if (/^L\d+$/.test(id)) return 15
  return null
}

export default function CorpusWritingPanel({ project, reportId, dirty, onCommitted, onEditFacts, onJumpToReport }: {
  project: Project; reportId: string; dirty: boolean; onCommitted: () => Promise<void>; onEditFacts: () => void;
  onJumpToReport: (reportId: string, position: number) => void
}) {
  const [reference, setReference] = useState<Reference | null>(null)
  const [reportType, setReportType] = useState<ReportType>('feasibility')
  const [sections, setSections] = useState<Section[]>([])
  const [sectionId, setSectionId] = useState('')
  const [sectionSearch, setSectionSearch] = useState('')
  const [itemFilter, setItemFilter] = useState<PackageItem['decision']>('selectable')
  const [itemSearch, setItemSearch] = useState('')
  const [writingPackage, setWritingPackage] = useState<WritingPackage | null>(null)
  const [approved, setApproved] = useState<string[]>([])
  const [candidate, setCandidate] = useState<Candidate | null>(null)
  const [mode, setMode] = useState<'guided' | 'model'>('guided')
  const [detail, setDetail] = useState<{ item: PackageItem; source: SourceDetail | null; impacts: SourceImpact[]; loadError?: string } | null>(null)
  const [mappingData, setMappingData] = useState<MappingData | null>(null)
  const [mappingDraft, setMappingDraft] = useState<Record<string, string>>({})
  const [mappingOpen, setMappingOpen] = useState(false)
  const [mappingBusy, setMappingBusy] = useState(false)
  const [mappingError, setMappingError] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const refreshReference = useCallback(async () => {
    const result = await api<Reference>(`/projects/${project.id}/writing/reference`)
    setReference(result)
    if (result.target_report_type) setReportType(result.target_report_type)
    if (result.selected) {
      const available = await api<{ sections: Section[] }>(`/projects/${project.id}/writing/sections`)
      setSections(available.sections)
    } else {
      setSections([]); setSectionId(''); setWritingPackage(null)
    }
  }, [project.id])
  useEffect(() => { void refreshReference().catch((cause: Error) => setError(cause.message)) }, [refreshReference])
  const refreshPackage = useCallback(async () => {
    if (!sectionId || !reference?.selected) { setWritingPackage(null); return }
    const result = await api<WritingPackage>(`/projects/${project.id}/writing/packages/${encodeURIComponent(sectionId)}`)
    setWritingPackage(result)
    setApproved([])
    setCandidate(null)
    setItemFilter('selectable')
    setItemSearch('')
  }, [project.id, reference?.selected, sectionId])
  useEffect(() => { void refreshPackage().catch((cause: Error) => setError(cause.message)) }, [refreshPackage])
  const shownSections = useMemo(() => sections.filter((section) => section.id === sectionId || `${section.id} ${section.title}`.toLowerCase().includes(sectionSearch.trim().toLowerCase())), [sections, sectionId, sectionSearch])
  const selectedSection = sections.find((section) => section.id === sectionId)
  const canDraft = mode === 'model' ? selectedSection?.model_available : selectedSection?.guided_available
  const selectable = writingPackage?.items.filter((item) => item.decision === 'selectable') || []
  const reviewOnly = writingPackage?.items.filter((item) => item.decision === 'review_only') || []
  const excluded = writingPackage?.items.filter((item) => item.decision === 'excluded') || []
  const requiredIds = writingPackage?.required_item_ids || []
  const missingRequired = requiredIds.filter((id) => !approved.includes(id))
  const shownItems = writingPackage?.items.filter((item) => item.decision === itemFilter &&
    `${item.semantic_id || ''} ${item.record_id} ${item.summary} ${item.reason}`.toLowerCase().includes(itemSearch.trim().toLowerCase())) || []
  const activeMappingSlots = mappingData?.slots.filter((slot) => writingPackage?.slots.some((item) => item.slot_id === slot.id)) || []
  const normalizedMapping = (value: Record<string, string>) => JSON.stringify(Object.entries(value).filter(([, key]) => key).sort(([a], [b]) => a.localeCompare(b)))
  const mappingChanged = mappingData ? normalizedMapping(mappingDraft) !== normalizedMapping(mappingData.bindings) : false

  const openMapping = async () => {
    setMappingOpen(true); setMappingBusy(true); setMappingError('')
    try {
      const result = await api<MappingData>(`/projects/${project.id}/writing`)
      setMappingData(result)
      setMappingDraft(result.bindings)
    } catch (cause) { setMappingError((cause as Error).message) } finally { setMappingBusy(false) }
  }
  const saveMapping = async () => {
    if (!mappingChanged) return
    setMappingBusy(true); setMappingError('')
    try {
      await api(`/projects/${project.id}/writing/bindings`, { method: 'PUT', body: JSON.stringify({ bindings: mappingDraft }) })
      setMappingOpen(false)
      setMappingData(null)
      await refreshPackage()
    } catch (cause) { setMappingError((cause as Error).message) } finally { setMappingBusy(false) }
  }

  const bindReference = async () => {
    if (!reference) return
    setBusy(true); setError('')
    try {
      await api(`/projects/${project.id}/writing/reference`, { method: 'PUT', body: JSON.stringify({
        corpus_id: reference.corpus_id, corpus_version: reference.corpus_version,
        target_report_type: reportType, accepted: true,
      }) })
      await refreshReference()
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }
  const openItem = async (item: PackageItem) => {
    setDetail({ item, source: null, impacts: [] }); setError('')
    try {
      const source = await api<SourceDetail>(`/projects/${project.id}/writing/sources/${item.category_number}/${encodeURIComponent(item.record_id)}`)
      setDetail((current) => current?.item.item_id === item.item_id ? { item, source, impacts: [] } : current)
      try {
        const impact = await api<{ impacts: SourceImpact[] }>(`/projects/${project.id}/writing/impact?record_id=${encodeURIComponent(source.source_ref.record_id)}`)
        setDetail((current) => current?.item.item_id === item.item_id ? { item, source, impacts: impact.impacts } : current)
      } catch (cause) {
        setDetail((current) => current?.item.item_id === item.item_id ? { item, source, impacts: [], loadError: `引用位置未能读取：${(cause as Error).message}` } : current)
      }
    } catch (cause) { setDetail((current) => current?.item.item_id === item.item_id ? { ...current, loadError: (cause as Error).message } : current) }
  }
  const openRelated = (id: string) => {
    const existing = writingPackage?.items.find((item) => item.semantic_id === id || item.record_id === id)
    if (existing) { void openItem(existing); return }
    const category = relatedCategory(id)
    if (!category) { setDetail((current) => current ? { ...current, loadError: `${id} 未能定位到入库类别` } : current); return }
    void openItem({ item_id: `related-${category}-${id}`, category_id: `CAT-${String(category).padStart(2, '0')}`,
      category_number: category, artifact_id: '', record_id: id, semantic_id: id, role: 'historical_text',
      decision: 'review_only', reason: '由当前记录的显式关联定位，仅供核对', location: null, summary: `关联记录 ${id}` })
  }
  const preview = async () => {
    if (!writingPackage || !approved.length || dirty || !canDraft) return
    setBusy(true); setError(''); setCandidate(null)
    try {
      setCandidate(await post<Candidate>(`/projects/${project.id}/reports/${reportId}/writing/preview`, {
        section_id: writingPackage.section.id, approved_item_ids: approved, mode,
      }))
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }
  const commit = async () => {
    if (!candidate || dirty) return
    setBusy(true); setError('')
    try {
      await post(`/projects/${project.id}/reports/${reportId}/writing/commit`, candidate)
      setCandidate(null)
      await onCommitted()
    } catch (cause) { setCandidate(null); setError(`${(cause as Error).message}；请重新预览`) } finally { setBusy(false) }
  }
  const toggle = (id: string) => {
    setApproved((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id])
    setCandidate(null)
  }

  return <section className="workspace-card corpus-writing-panel">
    <div className="workspace-toolbar"><h2>章节起草</h2></div>
    {error && <div className="notice error" role="alert">{error}</div>}
    {!reference ? <div className="empty">正在读取参考语料…</div> : !reference.selected ? <div className="corpus-writing-empty"><strong>澄岳精密 · {versionLabel(reference.corpus_version)}</strong><label className="form-field"><span>本项目报告类型</span><select aria-label="本项目报告类型" value={reportType} onChange={(event) => setReportType(event.target.value as ReportType)}><option value="feasibility">项目可行性报告</option><option value="accident_investigation">事故调查报告</option><option value="other">其他专业报告</option></select></label><button type="button" className="primary-button" disabled={busy} onClick={() => void bindReference()}>选择为只读参考</button></div> : <>
      <div className="corpus-writing-reference"><span>参考语料</span><strong>{reference.corpus_id.startsWith('cy_tray') ? '澄岳精密' : reference.corpus_id} · {versionLabel(reference.corpus_version)}</strong><span className="status status-neutral">只读</span></div>
      <div className="corpus-writing-selector"><label className="form-field"><span>查找章节</span><input value={sectionSearch} onChange={(event) => setSectionSearch(event.target.value)} placeholder="章节编号或名称" /></label><label className="form-field"><span>目标章节</span><select aria-label="选择语料章节" value={sectionId} onChange={(event) => { setSectionId(event.target.value); setMode('guided'); setWritingPackage(null); setCandidate(null) }}><option value="">选择章节</option>{shownSections.map((section) => <option key={section.id} value={section.id}>{section.id} · {section.title}{!section.guided_available ? ' · 仅核对' : ''}</option>)}</select></label></div>
      {sectionId && !writingPackage && <div className="empty">正在整理章节写作包…</div>}
      {writingPackage && <>
        <div className="corpus-writing-summary"><strong>{writingPackage.section.id} · {writingPackage.section.title}</strong></div>
        {!selectedSection?.guided_available && <div className="notice warn">{selectedSection?.availability_reason || '当前章节只能查看来源并人工核对。'}</div>}
        {writingPackage.slots.length > 0 && <div className="corpus-writing-slots"><div className="surface-heading"><strong>本项目事实</strong><div className="inline-actions"><button type="button" className="text-button" onClick={() => void openMapping()}>映射事实</button><button type="button" className="text-button" onClick={onEditFacts}>项目事实 <ArrowRight size={13} /></button></div></div><div>{writingPackage.slots.map((slot) => <span key={slot.slot_id} className={`corpus-writing-slot ${slot.value === null ? 'missing' : ''}`}><b>{slot.label}</b><small>{!slot.fact_key ? '未映射' : slot.value === null ? '未定义' : `${slot.value}${slot.unit || ''}`}</small></span>)}</div></div>}
        {writingPackage.issues.length > 0 && <div className="corpus-writing-issues">{writingPackage.issues.map((issue, index) => <div key={`${issue.code}-${index}`} className={`notice ${issue.severity === 'block' ? 'warn' : ''}`}><span>{issue.message}</span></div>)}</div>}
        <div className="corpus-writing-items"><div className="corpus-writing-list-toolbar"><strong>参考记录 · 已选 {approved.length}{requiredIds.length > 0 ? ` / 必选 ${requiredIds.length}` : ''}</strong><div className="segmented" role="group" aria-label="写作包筛选"><button type="button" className={itemFilter === 'selectable' ? 'active' : ''} onClick={() => setItemFilter('selectable')}>可选用 {selectable.length}</button><button type="button" className={itemFilter === 'review_only' ? 'active' : ''} onClick={() => setItemFilter('review_only')}>仅核对 {reviewOnly.length}</button><button type="button" className={itemFilter === 'excluded' ? 'active' : ''} onClick={() => setItemFilter('excluded')}>不适用 {excluded.length}</button></div><input aria-label="搜索写作包记录" value={itemSearch} onChange={(event) => setItemSearch(event.target.value)} placeholder="搜索 ID 或内容" /></div>{shownItems.length === 0 && <div className="empty">没有记录</div>}{shownItems.map((item) => <div className={`corpus-writing-item decision-${item.decision}`} key={item.item_id}><label><input type="checkbox" checked={approved.includes(item.item_id)} disabled={item.decision !== 'selectable' || busy} onChange={() => toggle(item.item_id)} /><span><b>{roleLabels[item.role] || item.role} · {item.semantic_id || item.record_id}{requiredIds.includes(item.item_id) ? ' · 必选' : ''}</b><small>{item.summary}</small>{item.decision !== 'selectable' && item.reason && <em>{item.reason}</em>}</span></label><button type="button" className="text-button" onClick={() => void openItem(item)}>来源</button></div>)}</div>
        <div className="corpus-writing-actions"><label className="form-field"><span>起草方式</span><select aria-label="起草方式" value={mode} onChange={(event) => { setMode(event.target.value as 'guided' | 'model'); setCandidate(null) }}><option value="guided">按事实与规则组织</option><option value="model" disabled={!selectedSection?.model_available}>使用已配置写作模型</option></select></label><button type="button" className="primary-button" disabled={busy || dirty || approved.length === 0 || missingRequired.length > 0 || !canDraft} onClick={() => void preview()}><Sparkles size={14} /> 预览章节候选</button></div>
        {missingRequired.length > 0 && <div className="notice">必选：{writingPackage.items.filter((item) => missingRequired.includes(item.item_id)).map((item) => item.semantic_id || item.record_id).join('、')}</div>}
        {dirty && <div className="notice warn">请先保存正文</div>}
        {candidate && <div className="corpus-writing-candidate"><div className="surface-heading"><strong>章节候选 · {candidate.section_id}</strong><span>{candidate.paragraphs.length} 段</span></div>{candidate.paragraphs.map((paragraph, index) => <div className="candidate-paragraph" key={index}><small>第 {index + 1} 段</small><p>{paragraphText(paragraph)}</p><small>{paragraph.fact_keys?.length ? `项目事实 ${paragraph.fact_keys.join('、')} · ` : ''}{paragraph.source_refs?.map((ref) => `${ref.category_id}/${ref.semantic_id || ref.record_id}`).join('、') || '无历史引用'}</small></div>)}{candidate.issues.length > 0 && <div className="corpus-writing-issues">{candidate.issues.map((issue, index) => <div className="notice warn" key={index}>{issue.message}</div>)}</div>}{candidate.rejected_items.length > 0 && <details className="candidate-rejections"><summary>未采用 {candidate.rejected_items.length} 项</summary>{candidate.rejected_items.map((item) => <div key={item.item_id}>{writingPackage.items.find((record) => record.item_id === item.item_id)?.semantic_id || item.item_id} · {item.reason}</div>)}</details>}{candidate.issues.some((issue) => issue.code === 'POWER_CONFLICT_UNRESOLVED') && <div className="notice warn">冲突未解决，仅可作为预审稿</div>}<div className="inline-actions"><button type="button" onClick={() => setCandidate(null)}>取消候选</button><button type="button" className="primary-button" disabled={busy || dirty || candidate.issues.some((issue) => issue.severity === 'block' && issue.code !== 'POWER_CONFLICT_UNRESOLVED')} onClick={() => void commit()}><Check size={14} /> 加入报告</button></div></div>}
      </>}
    </>}
    {mappingOpen && <div className="drawer-backdrop" onClick={() => setMappingOpen(false)}><aside className="drawer" role="dialog" aria-modal="true" aria-label="映射项目事实" onClick={(event) => event.stopPropagation()}><div className="drawer-header"><h2>映射项目事实</h2><button type="button" className="icon-button" aria-label="关闭映射" onClick={() => setMappingOpen(false)}><X size={19} /></button></div><div className="drawer-content"><div className="mapping-rows">{activeMappingSlots.map((slot) => { const options = mappingData?.facts.filter((fact) => fact.unit === slot.unit && (slot.data_type === 'text' ? ['text', 'enum'].includes(fact.data_type) : ['integer', 'decimal'].includes(fact.data_type))) || []; return <label className="form-field" key={slot.id}><span>{slot.label} · {slot.id}</span><select aria-label={`映射 ${slot.label}`} value={mappingDraft[slot.id] || ''} onChange={(event) => setMappingDraft((old) => ({ ...old, [slot.id]: event.target.value }))}><option value="">暂不映射</option>{options.map((fact) => <option key={fact.key} value={fact.key}>{fact.label} · {fact.value ?? '未定义'}</option>)}</select></label> })}</div>{mappingBusy && !mappingData && <div className="empty">正在读取…</div>}{mappingError && <div className="notice error" role="alert">{mappingError}</div>}<div className="form-actions"><button type="button" onClick={() => setMappingOpen(false)}>取消</button><button type="button" className="primary-button" disabled={!mappingChanged || mappingBusy} onClick={() => void saveMapping()}>{mappingBusy ? '保存中…' : '保存映射'}</button></div></div></aside></div>}
    {detail && <div className="drawer-backdrop" onClick={() => setDetail(null)}>
      <aside className="drawer" onClick={(event) => event.stopPropagation()}>
        <div className="drawer-header"><h2>资料来源 · {detail.item.semantic_id || detail.item.record_id}</h2>
          <button type="button" className="icon-button" aria-label="关闭来源" onClick={() => setDetail(null)}><X size={20} /></button></div>
        <div className="drawer-content corpus-writing-source">
          <div className="status status-neutral">历史参考 · 待本项目核对</div>
          <p>{detail.source?.summary || detail.item.summary}</p>
          <div className="field-grid">
            <div className="field"><span>记录</span><strong>{detail.source?.source_ref.category_id || detail.item.category_id} · {detail.source?.source_ref.record_id || detail.item.record_id}</strong></div>
            <div className="field"><span>原文位置</span><strong>{locationText(sourceLocation(detail.source, detail.item.location))}</strong></div>
            <div className="field"><span>用途</span><strong>{decisionLabels[detail.item.decision]}</strong></div>
          </div>
          {detail.item.reason && <div className="notice">{detail.item.reason}</div>}
          {detail.loadError && <div className="notice error">{detail.loadError}</div>}
          {detail.source ? <>
            {primarySourceText(detail.source.payload) && primarySourceText(detail.source.payload) !== detail.source.summary && <blockquote className="source-excerpt corpus-source-text">{primarySourceText(detail.source.payload)}</blockquote>}
            {detail.source.payload?.original_document_available === false && <div className="notice warn">完整原件缺失</div>}
            {Array.isArray(detail.source.payload?.limitations) && <div className="corpus-writing-limitations"><strong>证据限制</strong>{detail.source.payload.limitations.map((item, index) => <span key={index}>{String(item)}</span>)}</div>}
            {originalWordLocator(detail.source) && <div className="notice">Word 原文：{originalWordLocator(detail.source)}</div>}
            {Array.isArray(detail.source.payload?.excerpt_locations) && detail.source.payload.excerpt_locations.length > 0 && <div className="notice">首条摘录：{locationText(detail.source.payload.excerpt_locations[0])}{wordLocator((detail.source.payload.excerpt_locations[0] as Record<string, unknown>)?.source_locator) ? ` · Word ${wordLocator((detail.source.payload.excerpt_locations[0] as Record<string, unknown>)?.source_locator)}` : ''}</div>}
            {relatedGroups(detail.source.payload).map((group) => <div className="corpus-writing-relations" key={group.label}>
              <strong>{group.label}</strong><div>{group.ids.map((id) => <button type="button" className="source-button" key={id}
                onClick={() => openRelated(id)}>{id} <ArrowRight size={12} /></button>)}</div>
            </div>)}
            <div className="corpus-writing-impacts"><h3>已引用于</h3>
              {detail.impacts.length ? detail.impacts.map((impact) => <button type="button" key={`${impact.report_id}-${impact.block_id}`}
                onClick={() => { onJumpToReport(impact.report_id, impact.position); setDetail(null) }}>
                <b>{impact.report_title} · v{impact.report_version} · 第 {impact.position} 段</b><span>{impact.text}</span>
              </button>) : <small>当前项目报告尚未引用此记录。</small>}
            </div>
            {reference?.source_project_id && <a className="text-button" target="_blank" rel="noreferrer"
              href={rawUrl(reference.source_project_id, detail.source.source_ref.artifact_id)}>打开入库原件 <ExternalLink size={14} /></a>}
          </> : !detail.loadError && <div className="empty"><FileSearch size={16} /> 正在读取记录…</div>}
        </div>
      </aside>
    </div>}
  </section>
}
