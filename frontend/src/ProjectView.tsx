import { useCallback, useEffect, useMemo, useState } from 'react'
import { ArrowRight, Calculator, Check, ChevronRight, Clock3, FilePlus2, Plus, Search, X } from 'lucide-react'
import { api, post, type Fact, type FactChange, type Preview, type Project, type Rule, type Trace } from './api'
import './project-view.css'

type FactForm = { key: string; label: string; data_type: string; unit: string; caliber: string; as_of: string; source: string }
type EvidenceStatus = { fact_key: string; fact_revision: number; status: 'UNVERIFIED' | 'SOURCE_LOCATOR_REVIEWED'; document_id: string | null; source_refs: string[] }
type EvidenceDocument = { id: string; filename: string; pages: number }
type EvidenceSegment = { ref: string; page: number; text: string; locator?: string }
const emptyFact: FactForm = { key: '', label: '', data_type: 'decimal', unit: '', caliber: '', as_of: '', source: '' }
const newFact = (): FactForm => ({ ...emptyFact, key: `fact_${window.crypto.randomUUID().replaceAll('-', '').slice(0, 12)}` })
const statusText: Record<string, string> = { UNDEFINED: '未定义', UNEVALUABLE: '不可评估', PROVIDED: '已提供', COMPUTED: '已计算' }
const displayValue = (value: string | null, status?: string | null) => value ?? (statusText[status || ''] || '—')

function FactStatus({ status }: { status: string }) {
  return <span className={`status status-${status === 'COMPUTED' || status === 'PROVIDED' ? 'success' : 'warn'}`}>{statusText[status] || status}</span>
}

function Modal({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  useEffect(() => {
    const dismiss = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', dismiss)
    return () => window.removeEventListener('keydown', dismiss)
  }, [onClose])
  return <div className="drawer-backdrop" onClick={onClose}><aside className="drawer" role="dialog" aria-modal="true" aria-label={title} onClick={(event) => event.stopPropagation()}><div className="drawer-header"><h2>{title}</h2><button className="icon-button" onClick={onClose} aria-label="关闭"><X size={20} /></button></div><div className="drawer-content">{children}</div></aside></div>
}

function FieldInput({ label, value, onChange, placeholder, type = 'text' }: { label: string; value: string; onChange: (value: string) => void; placeholder?: string; type?: string }) {
  return <label className="form-field"><span>{label}</span><input value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} type={type} /></label>
}

export default function ProjectView({ project, section, onProjectChange, notify, onOpenDocuments, onOpenRules }: { project: Project | null; section: 'facts' | 'rules'; onProjectChange: (project: Project) => void; notify: (message: string) => void; onOpenDocuments: () => void; onOpenRules: () => void }) {
  const [facts, setFacts] = useState<Fact[]>([])
  const [rules, setRules] = useState<Rule[]>([])
  const [trace, setTrace] = useState<Trace[]>([])
  const [version, setVersion] = useState(0)
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [dialog, setDialog] = useState<'fact' | 'rule' | 'edit' | 'history' | 'evidence' | null>(null)
  const [factForm, setFactForm] = useState<FactForm>(emptyFact)
  const [ruleForm, setRuleForm] = useState({ name: '', target_key: '', expression: '' })
  const [current, setCurrent] = useState<Fact | null>(null)
  const [change, setChange] = useState<FactChange>({ fact_key: '', value: null, source: '', caliber: '', as_of: '', reason: '人工修改' })
  const [undefinedValue, setUndefinedValue] = useState(false)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [history, setHistory] = useState<Record<string, any>[]>([])
  const [evidenceStatus, setEvidenceStatus] = useState<EvidenceStatus | null>(null)
  const [evidenceDocuments, setEvidenceDocuments] = useState<EvidenceDocument[]>([])
  const [evidenceDocumentId, setEvidenceDocumentId] = useState('')
  const [evidencePage, setEvidencePage] = useState(1)
  const [evidenceSegments, setEvidenceSegments] = useState<EvidenceSegment[]>([])
  const [evidenceRefs, setEvidenceRefs] = useState<string[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    if (!project) return
    try {
      const [factResult, ruleResult] = await Promise.all([
        api<{ project_version: number; facts: Fact[] }>(`/projects/${project.id}/facts`),
        api<{ rules: Rule[]; trace: Trace[] }>(`/projects/${project.id}/rules`),
      ])
      setFacts(factResult.facts); setRules(ruleResult.rules); setTrace(ruleResult.trace); setVersion(factResult.project_version); setError('')
      if (project.version !== factResult.project_version) onProjectChange({ ...project, version: factResult.project_version })
    } catch (cause) { setError((cause as Error).message) }
  }, [project, onProjectChange])

  useEffect(() => { void load() }, [load])
  const filtered = useMemo(() => facts.filter((fact) =>
    `${fact.key} ${fact.label} ${fact.unit} ${fact.status}`.toLowerCase().includes(query.toLowerCase()) &&
    (statusFilter === 'all' || (statusFilter === 'needs-source' ? fact.status === 'PROVIDED' && fact.evidence_status !== 'SOURCE_LOCATOR_REVIEWED' : fact.status === statusFilter))), [facts, query, statusFilter])

  const createFact = async (openEditor: boolean) => {
    if (!project) return
    setBusy(true); setError('')
    try {
      const created = await post<Fact>(`/projects/${project.id}/facts`, factForm)
      setFactForm(emptyFact); await load(); if (openEditor) editFact(created); else setDialog(null); notify('事实已创建')
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }
  const createRule = async () => {
    if (!project) return
    setBusy(true); setError('')
    try {
      await post(`/projects/${project.id}/rules`, ruleForm)
      setDialog(null); setRuleForm({ name: '', target_key: '', expression: '' }); await load(); notify('规则已保存并重新计算')
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }
  const editFact = (fact: Fact) => {
    setCurrent(fact); setChange({ fact_key: fact.key, value: fact.value, source: fact.source, caliber: fact.caliber, as_of: fact.as_of, reason: '人工修改' }); setUndefinedValue(false); setPreview(null); setError(''); setDialog('edit')
  }
  const showHistory = async (fact: Fact) => {
    if (!project) return
    setCurrent(fact); setDialog('history'); setError('')
    try { setHistory(await api<Record<string, any>[]>(`/projects/${project.id}/revisions?fact_key=${encodeURIComponent(fact.key)}`)) }
    catch (cause) { setError((cause as Error).message) }
  }
  const openEvidence = async (fact: Fact) => {
    if (!project) return
    setCurrent(fact); setDialog('evidence'); setError(''); setEvidenceSegments([]); setEvidenceStatus(null); setEvidenceDocuments([])
    try {
      const [status, documents] = await Promise.all([
        api<EvidenceStatus>(`/projects/${project.id}/facts/${encodeURIComponent(fact.key)}/evidence`),
        api<EvidenceDocument[]>(`/projects/${project.id}/documents`),
      ])
      setEvidenceStatus(status); setEvidenceDocuments(documents)
      setEvidenceDocumentId(status.document_id || documents[0]?.id || '')
      setEvidenceRefs(status.source_refs || [])
      const boundPage = /^p(\d+)-/.exec(status.source_refs?.[0] || '')
      setEvidencePage(boundPage ? Number(boundPage[1]) : 1)
    } catch (cause) { setError((cause as Error).message) }
  }
  useEffect(() => {
    if (!project || dialog !== 'evidence' || !evidenceDocumentId) return
    let active = true
    api<{ segments: EvidenceSegment[] }>(`/projects/${project.id}/documents/${evidenceDocumentId}?page=${evidencePage}`)
      .then((detail) => { if (active) { setEvidenceSegments(detail.segments); setError('') } })
      .catch((cause: Error) => { if (active) { setEvidenceSegments([]); setError(cause.message) } })
    return () => { active = false }
  }, [project, dialog, evidenceDocumentId, evidencePage])
  const bindEvidence = async () => {
    if (!project || !current || !evidenceDocumentId || !evidenceRefs.length) return
    setBusy(true); setError('')
    try {
      const status = await post<EvidenceStatus>(`/projects/${project.id}/facts/${encodeURIComponent(current.key)}/evidence/bind`, {
        document_id: evidenceDocumentId, source_refs: evidenceRefs,
      })
      setEvidenceStatus(status); await load(); notify('原文位置已绑定')
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }
  const previewChange = async () => {
    if (!project) return
    setBusy(true); setError('')
    try { setPreview(await post<Preview>(`/projects/${project.id}/changes/preview`, { ...change, value: undefinedValue ? null : change.value })) }
    catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }
  const commitChange = async () => {
    if (!project || !preview) return
    setBusy(true); setError('')
    try {
      await post(`/projects/${project.id}/changes/commit`, { ...change, value: undefinedValue ? null : change.value, base_version: preview.base_version, preview_token: preview.preview_token })
      const affected = preview.report_impacts.length
      setDialog(null); setPreview(null); await load(); notify(affected ? `变更已提交；${affected} 处报告段落需重新核对` : '变更已提交，修订记录已保存')
    } catch (cause) { setError((cause as Error).message); setPreview(null) } finally { setBusy(false) }
  }

  if (!project) return <main className="page"><div className="breadcrumb">项目工作台</div><div className="page-header"><h1>{section === 'facts' ? '项目事实' : '规则计算'}</h1></div><div className="empty-state"><FilePlus2 size={42} /><h2>选择或新建项目</h2></div></main>

  return <main className="page">
    <div className="breadcrumb">项目 / {project.name} / {section === 'facts' ? '事实台账' : '规则计算'}</div>
    <div className="page-header"><h1>{section === 'facts' ? '项目事实' : '规则计算'}</h1>{project.has_corpus ? <span className="status status-neutral">只读</span> : <button className="primary-button" onClick={() => { setError(''); if (section === 'facts') { setFactForm(newFact()); setDialog('fact') } else { setRuleForm({ name: '', target_key: '', expression: '' }); setDialog('rule') } }}><Plus size={16} />{section === 'facts' ? '新增事实' : '新增规则'}</button>}</div>
    {error && !dialog && <div className="notice error">{error}</div>}
    <div className="project-summary"><span>{facts.length} 项事实</span><span>{facts.filter((fact) => fact.status === 'UNDEFINED' || fact.status === 'UNEVALUABLE').length} 项待补</span><span>v{version}</span></div>
    {section === 'facts' ? <section className="workspace-card"><div className="workspace-toolbar"><h2>事实台账</h2><div className="fact-list-tools"><select aria-label="筛选事实状态" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="all">全部状态</option><option value="UNDEFINED">未定义</option><option value="UNEVALUABLE">不可评估</option><option value="PROVIDED">已提供</option><option value="COMPUTED">已计算</option><option value="needs-source">来源待核对</option></select><div className="search-input"><Search size={16} /><input aria-label="搜索事实" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索名称或单位" /></div></div></div><div className="table-wrap"><table><thead><tr><th>事实</th><th>值</th><th>状态</th><th>操作</th></tr></thead><tbody>{filtered.map((fact) => { const computed = rules.some((rule) => rule.target_key === fact.key); return <tr key={fact.id}><td><strong>{fact.label}</strong></td><td className="fact-value">{fact.value === null ? '—' : fact.value}{fact.value !== null && fact.unit ? ` ${fact.unit}` : ''}</td><td><FactStatus status={fact.status} />{fact.status === 'PROVIDED' && <small className="fact-source-state">{fact.evidence_status === 'SOURCE_LOCATOR_REVIEWED' ? '原文位置已核对' : '来源待核对'}</small>}</td><td><div className="inline-actions">{computed ? <button onClick={onOpenRules}>规则</button> : !project.has_corpus && <><button onClick={() => editFact(fact)}>编辑</button>{fact.status === 'PROVIDED' && <button onClick={() => void openEvidence(fact)}>证据</button>}</>}<button onClick={() => showHistory(fact)}>历史</button></div></td></tr> })}</tbody></table>{filtered.length === 0 && <div className="empty">{facts.length === 0 ? '暂无事实，点击右上角新增' : '没有匹配事实'}</div>}</div></section> : <section className="workspace-card"><div className="workspace-toolbar"><h2>规则与结果</h2></div>{rules.length === 0 ? <div className="empty">暂无规则</div> : <div className="rule-list">{rules.map((rule) => { const item = trace.find((entry) => entry.rule_id === rule.id); return <div className="rule-card" key={rule.id}><div className="rule-card-head"><span className="rule-symbol"><Calculator size={18} /></span><div><strong>{rule.name}</strong><small>{rule.target_key} ← {rule.deps.join('、')}</small></div><FactStatus status={item?.status || 'UNDEFINED'} /></div><div className="expression">{rule.expression}</div><div className="rule-result">{item?.status === 'UNEVALUABLE' ? <>缺少输入：{item.missing?.join('、')}</> : <>当前结果：<b>{item?.result ?? '—'}</b></>}</div></div> })}</div>}</section>}

    {dialog === 'fact' && <Modal title="新增事实" onClose={() => setDialog(null)}><div className="form-stack">
      <FieldInput label="名称" value={factForm.label} onChange={(value) => setFactForm({ ...factForm, label: value })} placeholder="首年需求" />
      <label className="form-field"><span>类型</span><select value={factForm.data_type} onChange={(event) => setFactForm({ ...factForm, data_type: event.target.value })}>{[['decimal', '小数'], ['integer', '整数'], ['boolean', '布尔'], ['date', '日期'], ['text', '文本'], ['enum', '选项']].map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <FieldInput label="单位" value={factForm.unit} onChange={(value) => setFactForm({ ...factForm, unit: value })} placeholder="套" />
      <details className="project-more"><summary>更多属性</summary><FieldInput label="字段 key" value={factForm.key} onChange={(value) => setFactForm({ ...factForm, key: value })} /><FieldInput label="口径" value={factForm.caliber} onChange={(value) => setFactForm({ ...factForm, caliber: value })} /><FieldInput label="时点" value={factForm.as_of} onChange={(value) => setFactForm({ ...factForm, as_of: value })} placeholder="YYYY-MM-DD" /><FieldInput label="来源" value={factForm.source} onChange={(value) => setFactForm({ ...factForm, source: value })} /></details>
      {error && <div className="notice error">{error}</div>}<div className="form-actions"><button onClick={() => setDialog(null)}>取消</button><button disabled={!factForm.key || !factForm.label || busy} onClick={() => void createFact(false)}>仅创建</button><button className="primary-button" disabled={!factForm.key || !factForm.label || busy} onClick={() => void createFact(true)}>创建并录入值</button></div>
    </div></Modal>}
    {dialog === 'rule' && <Modal title="新增规则" onClose={() => setDialog(null)}><div className="form-stack">
      <FieldInput label="名称" value={ruleForm.name} onChange={(value) => setRuleForm({ ...ruleForm, name: value })} placeholder="首年计划销售量" />
      <label className="form-field"><span>结果事实</span><select value={ruleForm.target_key} onChange={(event) => setRuleForm({ ...ruleForm, target_key: event.target.value })}><option value="">选择事实</option>{facts.filter((fact) => !rules.some((rule) => rule.target_key === fact.key) && ['decimal', 'integer', 'boolean'].includes(fact.data_type)).map((fact) => <option key={fact.key} value={fact.key}>{fact.label} · {fact.key}</option>)}</select></label>
      <FieldInput label="表达式" value={ruleForm.expression} onChange={(value) => setRuleForm({ ...ruleForm, expression: value })} placeholder="min(first_year_demand, qualified_capacity)" />
      {error && <div className="notice error">{error}</div>}<div className="form-actions"><button onClick={() => setDialog(null)}>取消</button><button className="primary-button" disabled={!ruleForm.name || !ruleForm.target_key || !ruleForm.expression || busy} onClick={createRule}>保存规则</button></div>
    </div></Modal>}
    {dialog === 'edit' && current && <Modal title={`录入 · ${current.label}`} onClose={() => setDialog(null)}><div className="form-stack">
      <div className="project-fact-meta">{current.key} · {current.unit || '无单位'} · r{current.revision} · {statusText[current.status] || current.status}</div>
      <label className="check-line"><input type="checkbox" checked={undefinedValue} onChange={(event) => { setUndefinedValue(event.target.checked); setPreview(null) }} />设为未定义</label>
      {!undefinedValue && <FieldInput label="值" value={change.value === null ? '' : String(change.value)} onChange={(value) => { setChange({ ...change, value }); setPreview(null) }} placeholder={current.data_type === 'boolean' ? 'true / false' : '输入数值或文字'} type={current.data_type === 'date' ? 'date' : 'text'} />}
      <FieldInput label="来源" value={change.source} onChange={(value) => { setChange({ ...change, source: value }); setPreview(null) }} placeholder="本项目资料" />
      <details className="project-more"><summary>更多属性</summary><FieldInput label="口径" value={change.caliber || ''} onChange={(value) => { setChange({ ...change, caliber: value }); setPreview(null) }} /><FieldInput label="时点" value={change.as_of || ''} onChange={(value) => { setChange({ ...change, as_of: value }); setPreview(null) }} placeholder="YYYY-MM-DD" /><FieldInput label="修改原因" value={change.reason} onChange={(value) => { setChange({ ...change, reason: value }); setPreview(null) }} /></details>
      {error && <div className="notice error">{error}</div>}
      {preview && <div className="preview-box"><div className="surface-heading"><strong>影响预览</strong><span>v{preview.base_version}</span></div>{preview.changes.length === 0 ? <div className="empty">没有变更</div> : preview.changes.map((item) => <div className="diff-row" key={item.key}><div><strong>{item.label}</strong></div><span>{displayValue(item.before.value, item.before.status)}</span><ArrowRight size={16} /><b>{displayValue(item.after.value, item.after.status)}</b></div>)}{preview.report_impacts.length > 0 && <div className="report-impact-preview"><strong>{preview.report_impacts.length} 处报告引用需更新</strong>{preview.report_impacts.map((impact, index) => <div key={`${impact.report_id}-${impact.position}-${impact.fact_key}-${index}`}><small>{impact.report_title} · v{impact.report_version} · 第 {impact.position} 段</small><p>{impact.text}</p><span>{displayValue(impact.before.value, impact.before.status)} → {displayValue(impact.after.value, impact.after.status)}</span></div>)}</div>}{preview.trace.length > 0 && <details><summary>计算过程 · {preview.trace.length} 步</summary>{preview.trace.map((step, i) => <div className="trace-step" key={i}>{step.expression} → {step.status === 'UNEVALUABLE' ? `缺少 ${step.missing?.join('、')}` : step.result}</div>)}</details>}</div>}
      <div className="form-actions"><button onClick={() => setDialog(null)}>取消</button>{preview ? <button className="primary-button" disabled={busy || preview.changes.length === 0} onClick={commitChange}><Check size={16} />确认提交</button> : <button className="primary-button" disabled={busy || (!undefinedValue && String(change.value ?? '').trim() === '')} onClick={previewChange}>预览影响 <ArrowRight size={16} /></button>}</div>
    </div></Modal>}
    {dialog === 'evidence' && current && <Modal title={`核对原文 · ${current.label}`} onClose={() => setDialog(null)}>
      <div className="form-stack evidence-bind">
        <div className="notice">当前事实 {current.value ?? '未定义'}{current.unit} · r{current.revision} · {evidenceStatus?.status === 'SOURCE_LOCATOR_REVIEWED' ? '原文位置已核对' : '来源待核对'}</div>
        {error && <div className="notice error">{error}</div>}
        {!evidenceDocuments.length ? <div className="empty"><button type="button" className="text-button" onClick={() => { setDialog(null); onOpenDocuments() }}>上传原件</button></div> : <>
          <label className="form-field"><span>本项目原件</span><select aria-label="证据原件" value={evidenceDocumentId} onChange={(event) => { setEvidenceDocumentId(event.target.value); setEvidencePage(1); setEvidenceRefs([]) }}>{evidenceDocuments.map((document) => <option value={document.id} key={document.id}>{document.filename}</option>)}</select></label>
          <div className="evidence-page-row"><label className="form-field"><span>页码</span><input aria-label="证据页码" type="number" min={1} max={evidenceDocuments.find((document) => document.id === evidenceDocumentId)?.pages || 1} value={evidencePage} onChange={(event) => { const next = Number(event.target.value); const maximum = evidenceDocuments.find((document) => document.id === evidenceDocumentId)?.pages || 1; if (Number.isInteger(next) && next >= 1 && next <= maximum) setEvidencePage(next) }} /></label><small>已选 {evidenceRefs.length}/3 处</small><a className="text-button" target="_blank" rel="noreferrer" href={`/api/projects/${project.id}/documents/${evidenceDocumentId}/original`}>打开原件</a></div>
          <div className="evidence-segments">{evidenceSegments.length ? evidenceSegments.map((segment) => <label key={segment.ref}>
            <input type="checkbox" checked={evidenceRefs.includes(segment.ref)} disabled={!evidenceRefs.includes(segment.ref) && evidenceRefs.length >= 3} onChange={(event) => setEvidenceRefs((old) => event.target.checked ? [...old, segment.ref] : old.filter((ref) => ref !== segment.ref))} />
            <span><b>{segment.ref}</b><small>{segment.locator || `第 ${segment.page} 页`}</small><p>{segment.text}</p></span>
          </label>) : <div className="empty">本页暂无可读取的文字片段。</div>}</div>
          {evidenceRefs.length > 0 && <div className="evidence-selected"><strong>已选原文位置</strong><span>{evidenceRefs.join('、')}</span></div>}
          <div className="form-actions"><button type="button" onClick={() => setDialog(null)}>取消</button><button className="primary-button" disabled={busy || evidenceRefs.length === 0} onClick={() => void bindEvidence()}><Check size={14} /> 核对片段并绑定</button></div>
        </>}
      </div>
    </Modal>}
    {dialog === 'history' && current && <Modal title={`${current.label} · 修订记录`} onClose={() => setDialog(null)}>{error && <div className="notice error">{error}</div>}{history.length === 0 ? <div className="empty">尚无值变更记录</div> : <div className="timeline revisions">{history.map((item) => <div key={item.id}><i /><div><strong>项目 v{item.project_version} · {item.reason}</strong><small><Clock3 size={13} /> {new Date(item.created_at).toLocaleString('zh-CN')}</small><p>{item.before?.value ?? '未定义'} <ChevronRight size={14} /> {item.after?.value ?? '未定义'}</p></div></div>)}</div>}</Modal>}
  </main>
}
