import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, ArrowRight, Check, ExternalLink, Link2, RefreshCw, ShieldCheck } from 'lucide-react'
import { api, post, rawUrl, type Fact, type FactChange, type Project } from './api'

type Slot = { id: string; label: string; data_type: string; unit: string; suggested_key: string; chunk_id: string; origin?: string }
type Source = { chunk_id: string; title: string; section: string; page: number; span: number[]; original_text: string; skeleton: string; mentions: { mention_id: string; slot: string; format: Record<string, unknown> }[]; reuse_note: string }
type Issue = { chunk_id: string; code: string; message: string; severity: 'block' | 'review' | 'info'; slot_id?: string }
type Block = { id: string; chunk_id: string; title: string; text: string; references: { slot_id: string; fact_key: string; display: string; mention_id: string | null }[] }
type Draft = { blocks: Block[]; issues: Issue[]; blocked: boolean }
type WritingData = { project_id: string; project_version: number; source_project_id: string; source_corpus_id: string; slots: Slot[]; bindings: Record<string, string>; facts: Fact[]; rule_targets: string[]; sources: Source[]; draft: Draft }
type WritingPreview = { base_version: number; preview_token: string; changes: { key: string; label: string; before: { value: string | null }; after: { value: string | null } }[]; before: Draft; after: Draft; affected: { chunk_id: string; before: string; after: string; issues_changed: boolean }[]; change: FactChange }

const ordered = (value: Record<string, string>) => JSON.stringify(Object.entries(value).filter(([, key]) => key).sort(([a], [b]) => a.localeCompare(b)))
const shown = (fact: Fact | undefined) => fact?.value === null || fact?.value === undefined ? '未定义' : `${fact.value}${fact.unit || ''}`

function IssueList({ issues }: { issues: Issue[] }) {
  if (!issues.length) return <div className="writing-clear"><ShieldCheck size={16} /> 无问题</div>
  return <div className="writing-issues">{issues.map((issue, index) => <div key={`${issue.code}-${issue.slot_id || index}`} className={`writing-issue issue-${issue.severity}`}><AlertTriangle size={15} /><div><strong>{issue.chunk_id} · {issue.severity === 'block' ? '阻断' : issue.severity === 'review' ? '需复核' : '提示'}</strong><span>{issue.message}</span></div></div>)}</div>
}

function DraftCards({ draft, sources, sourceProjectId }: { draft: Draft; sources: Source[]; sourceProjectId: string }) {
  return <div className="writing-drafts">{draft.blocks.map((block) => { const source = sources.find((item) => item.chunk_id === block.chunk_id); const issues = draft.issues.filter((item) => item.chunk_id === block.chunk_id); return <section className="writing-draft surface" key={block.id}><div className="surface-heading"><div><strong>{block.chunk_id} · {block.title}</strong><span>{source?.section} · 原文第 {source?.page} 页</span></div><span className={`status ${issues.some((item) => item.severity === 'block') ? 'status-warn' : issues.some((item) => item.severity === 'review') ? 'status-warn' : 'status-success'}`}>{issues.some((item) => item.severity === 'block') ? '待补事实' : issues.some((item) => item.severity === 'review') ? '待复核' : '可核对'}</span></div><div className="writing-prose"><p>{block.text}</p></div><div className="writing-refs"><strong>事实引用</strong>{block.references.length ? block.references.map((ref, index) => <span className="chip" key={`${ref.fact_key}-${index}`} title={ref.mention_id || '按来源规则补充的输入'}>{ref.fact_key} → {ref.display}{ref.mention_id ? ` · ${ref.mention_id}` : ''}</span>) : <small>待映射项目事实</small>}</div><IssueList issues={issues} />{source && <details className="writing-source"><summary>原文与骨架</summary><div><small>历史原文 · 字符 {source.span.join('–')}</small><p>{source.original_text}</p><small>抽取骨架</small><p className="mono">{source.skeleton}</p><a className="text-button" href={rawUrl(sourceProjectId, 'source/normalized.md')} target="_blank" rel="noreferrer">打开规范化原文 <ExternalLink size={13} /></a><a className="text-button" href={rawUrl(sourceProjectId, 'source/original.docx')} target="_blank" rel="noreferrer">打开 Word 原件 <ExternalLink size={13} /></a></div></details>}</section> })}</div>
}

export default function WritingView({ project, onProjectChange, notify, onChooseReference }: { project: Project; onProjectChange: (project: Project) => void; notify: (message: string) => void; onChooseReference: () => void }) {
  const [data, setData] = useState<WritingData | null>(null)
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const [selectedKey, setSelectedKey] = useState('')
  const [value, setValue] = useState('')
  const [source, setSource] = useState('')
  const [preview, setPreview] = useState<WritingPreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    const result = await api<WritingData>(`/projects/${project.id}/writing`)
    setData(result)
    const suggested = Object.fromEntries(result.slots.filter((slot) => result.facts.some((fact) => fact.key === slot.suggested_key)).map((slot) => [slot.id, slot.suggested_key]))
    setMapping(Object.keys(result.bindings).length ? result.bindings : suggested)
    setSelectedKey((previous) => result.facts.some((fact) => fact.key === previous && !result.rule_targets.includes(fact.key)) ? previous : result.facts.find((fact) => !result.rule_targets.includes(fact.key))?.key || '')
    setError('')
  }, [project.id])
  useEffect(() => { void refresh().catch((cause: Error) => setError(cause.message)) }, [refresh])

  const editable = useMemo(() => data?.facts.filter((fact) => !data.rule_targets.includes(fact.key)) || [], [data])
  const selected = editable.find((fact) => fact.key === selectedKey)
  useEffect(() => { setValue(selected?.value ?? ''); setPreview(null) }, [selected?.key, selected?.value])
  const mappingDirty = data ? ordered(mapping) !== ordered(data.bindings) : false

  const setup = async () => {
    setBusy(true); setError('')
    try { const result = await post<{ project_version: number }>(`/projects/${project.id}/writing/setup`, {}); onProjectChange({ ...project, version: result.project_version }); await refresh(); notify('空白字段已创建') }
    catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }
  const saveMapping = async () => {
    setBusy(true); setError('')
    try { const result = await api<{ project_version: number }>(`/projects/${project.id}/writing/bindings`, { method: 'PUT', body: JSON.stringify({ bindings: mapping }) }); onProjectChange({ ...project, version: result.project_version }); await refresh(); setPreview(null); notify('项目事实映射已保存') }
    catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }
  const previewChange = async () => {
    if (!selected) return
    setBusy(true); setError('')
    const change: FactChange = { fact_key: selected.key, value: value.trim() === '' ? null : value.trim(), source: source.trim(), reason: '章节验证输入' }
    try { const result = await post<Omit<WritingPreview, 'change'>>(`/projects/${project.id}/writing/preview`, change); setPreview({ ...result, change }) }
    catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }
  const commit = async () => {
    if (!preview) return
    setBusy(true); setError('')
    try { const result = await post<{ project_version: number }>(`/projects/${project.id}/changes/commit`, { ...preview.change, base_version: preview.base_version, preview_token: preview.preview_token }); onProjectChange({ ...project, version: result.project_version }); setPreview(null); await refresh(); notify('事实变更已提交') }
    catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }

  return <main className="page"><div className="breadcrumb">项目 / {project.name} / 事实映射</div><div className="page-header"><div><h1>案例验证</h1></div><span className="status status-neutral">项目 v{data?.project_version ?? project.version}</span></div>
    {error && <div className="notice error">{error}{error.includes('只读参考语料') && <button type="button" className="text-button" onClick={onChooseReference}>选择参考语料 <ArrowRight size={13} /></button>}</div>}
    {!data ? <div className="empty">正在读取…</div> : <>
      <section className="workspace-card writing-section"><div className="workspace-toolbar"><h2>1 · 字段映射</h2>{data.facts.length === 0 && <button className="primary-button" disabled={busy} onClick={setup}>建立空白字段</button>}</div>{data.facts.length === 0 ? <div className="empty">暂无事实</div> : <><div className="writing-mapping">{data.slots.map((slot) => { const options = data.facts.filter((fact) => fact.unit === slot.unit && (slot.data_type === 'text' ? ['text', 'enum'].includes(fact.data_type) : ['integer', 'decimal'].includes(fact.data_type))); return <label className="writing-map-row" key={slot.id}><span><b>{slot.label}</b><small>{slot.id} · {slot.unit || '文本'}</small>{slot.origin && <em>{slot.origin}</em>}</span><select aria-label={`映射 ${slot.label}`} value={mapping[slot.id] || ''} onChange={(event) => { setMapping((previous) => ({ ...previous, [slot.id]: event.target.value })); setPreview(null) }}><option value="">暂不映射</option>{options.map((fact) => <option value={fact.key} key={fact.key}>{fact.label} · {fact.key}</option>)}</select><span className="writing-current">{shown(options.find((fact) => fact.key === mapping[slot.id]))}</span></label> })}</div><div className="writing-action-line"><small>{mappingDirty ? '未保存' : '已保存'}</small><button className="primary-button" disabled={!mappingDirty || busy} onClick={saveMapping}><Link2 size={15} /> 保存映射</button></div></>}</section>

      <section className="workspace-card writing-section"><div className="workspace-toolbar"><h2>2 · 事实变更</h2><button className="subtle-button" onClick={() => void refresh()} disabled={busy}><RefreshCw size={14} /> 刷新</button></div><div className="writing-input-grid"><label className="form-field"><span>选择输入事实</span><select aria-label="选择验证事实" value={selectedKey} onChange={(event) => setSelectedKey(event.target.value)}><option value="">选择事实</option>{editable.map((fact) => <option value={fact.key} key={fact.key}>{fact.label} · 当前 {shown(fact)}</option>)}</select></label><label className="form-field"><span>新值</span><input aria-label="新值" value={value} onChange={(event) => { setValue(event.target.value); setPreview(null) }} placeholder="留空表示未定义；0 是有效值" /></label><label className="form-field"><span>来源</span><input aria-label="来源" value={source} onChange={(event) => { setSource(event.target.value); setPreview(null) }} placeholder="本项目资料" /></label></div><div className="writing-action-line"><small>{selected ? `${selected.unit || '无单位'} · ${shown(selected)}` : '选择事实'}</small><button className="primary-button" disabled={!selected || mappingDirty || busy || (value.trim() !== '' && !source.trim())} onClick={previewChange}>预览影响 <ArrowRight size={15} /></button></div>{preview && <div className="writing-preview"><div className="surface-heading"><strong>变更预览 · 基于项目 v{preview.base_version}</strong><span>{preview.affected.length} 处内容变化 · {preview.changes.length} 项事实变化</span></div><div className="writing-impact">{preview.affected.length ? preview.affected.map((item) => <div key={item.chunk_id}><b>{item.chunk_id}</b><span>{item.before}</span><ArrowRight size={14} /><strong>{item.after}{item.issues_changed && item.before === item.after ? '（待处理项已变化）' : ''}</strong></div>) : <p>候选未变化</p>}</div><DraftCards draft={preview.after} sources={data.sources} sourceProjectId={data.source_project_id} /><div className="writing-action-line"><div className="toolbar"><button className="subtle-button" onClick={() => setPreview(null)}>取消预览</button><button className="primary-button" disabled={!preview.changes.length || busy} onClick={commit}><Check size={15} /> 确认提交</button></div></div></div>}</section>

      <section className="writing-section"><div className="section-heading"><h2>章节候选</h2><span className={`status ${data.draft.blocked ? 'status-warn' : 'status-success'}`}>{data.draft.blocked ? '仍有阻断项' : '可继续人工复核'}</span></div><DraftCards draft={data.draft} sources={data.sources} sourceProjectId={data.source_project_id} /></section>
    </>}
  </main>
}
