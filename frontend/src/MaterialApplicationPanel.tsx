import { useEffect, useMemo, useRef, useState } from 'react'
import { ChevronRight, Info, X } from 'lucide-react'
import { api, post } from './api'
import './material-application.css'

type Mode = 'auto' | 'review' | 'exclude'
type Role = 'input' | 'review' | 'unused' | 'not_generated'
type Group = string | { name: string; count?: number }
type Item = {
  category_id: string; number: number; name: string; group: string; record_count: number; artifact_count: number;
  configured_mode: Mode; effective_role: Role; purpose: string; chapter_role: string;
  limitations: string | string[]; record_ids: string[]
}
type Slot = { slot_id: string; label: string; unit: string; value: string | null }
type Data = { project_id: string; section_id: string; config_version: number; groups: Group[]; items: Item[]; slots: Slot[] }
type Issue = string | { code?: string; message?: string }
type Outcome = { status: string; text?: string | string[] | null; used_categories?: string[]; used_records?: unknown[]; issues?: Issue[] }
type Experiment = { category_id: string; status: 'changed' | 'same' | 'blocked' | 'not_consumed' | 'equivalent' | 'not_supported'; reason: string;
  before_text?: string | null; after_text?: string | null; used_records?: unknown[];
  contract?: { schema_version: string; kind: string; fields: Record<string, unknown>; validation: Record<string, unknown>;
    source_record_ids: string[]; replacement_source: string; information_origin?: string; simulation_only?: boolean } | null;
  checks?: Record<string, boolean> | null }
type Step = { rule_id?: string; target?: string; status?: string; value?: string | number | null;
  result?: string | number | null; error?: string; expression?: string; inputs?: Record<string, string | number | null>;
  missing?: string[]; name?: string }
type Fact = { slot_id?: string; label?: string; unit?: string; value?: string | number | null; status?: string; fact_key?: string | null }
type Simulation = { baseline: Outcome; configured: Outcome; experiments: Experiment[];
  trace: Step[]; facts: Fact[]; simulation_only: boolean; experiment_mode?: 'remove' | 'equivalent' }

const modeLabel: Record<Mode, string> = { auto: '自动选用', review: '仅核对', exclude: '不使用' }
const roleLabel: Record<Role, string> = { input: '写作输入', review: '核对', unused: '未使用', not_generated: '未生成' }
const experimentLabel: Record<Experiment['status'], string> = {
  changed: '有变化', same: '无变化', blocked: '受阻', not_consumed: '未消费',
  equivalent: '等价', not_supported: '未适配',
}
const contractKindLabel: Record<string, string> = {
  section_structure: '章节结构', source_locator: '原文位置', slot_schema: '事实槽位', project_rule: '项目规则',
  restricted_claim: '论断边界', evidence_boundary: '证据边界', two_source_conflict: '双来源冲突', section_template: '章节模板',
}
const contractFieldLabel: Record<string, string> = {
  section_id: '章节', title: '标题', level: '层级', page: '页码', start: '起点', end: '终点',
  word_part: 'Word 部件', word_xpath: 'Word 位置', source_kind: '来源类型', unit: '单位', data_type: '类型',
  operator: '运算', inputs: '输入', target: '结果', policy: '论断约束', sentence: '允许表述',
  independently_verified: '独立核实', original_document_available: '完整原件', can_prove_new_project: '能证明本项目',
  status: '状态', same_caliber: '同口径', sources: '来源', evidence_id: '证据', value: '值',
  paragraph_roles: '段落角色', coverage_specified: '覆盖配置', negative_mutation: '负例变更',
  negative_result: '负例结果', baseline_boundary_codes: '原边界', typed_boundary_codes: '替代边界',
}
const checkLabel: Record<string, string> = {
  text_equal: '正文一致', project_values_equal: '项目数值一致', boundaries_equal: '边界一致',
  legacy_refs_reused: '复用历史引用', source_information_equal: '来源信息一致', negative_probe_passed: '负例校验通过',
}
const defaults: Record<string, string> = { N017: '300000', N034: '254016', supplier_name: '新拓设备', N080: '1234.50' }
const inputIds: Record<string, string[]> = { S4: ['N017', 'N034'], 'S7.1': ['supplier_name', 'N080'], 'S5.2': [] }
const inputLabels: Record<string, string> = { N017: '首年需求', N034: '合格能力', supplier_name: '供应商', N080: '设备单价' }
const validated = new Set(['S4', 'S7.1', 'S5.2'])

function settingsFor(data: Data, draft: Record<string, Mode>) {
  return data.items.map((item) => ({ category_id: item.category_id, mode: draft[item.category_id] || item.configured_mode }))
}
function initialModes(data: Data): Record<string, Mode> {
  return Object.fromEntries(data.items.map((item) => [item.category_id, item.configured_mode]))
}
function outcomeText(value: Outcome['text']): string {
  return Array.isArray(value) ? value.filter(Boolean).join('\n') : value || ''
}
function statusText(value: string): string {
  return ({ ready: '可预览', ok: '可预览', blocked: '受阻', unadapted: '未适配',
    unavailable: '不可评估' } as Record<string, string>)[value] || value
}
function recordId(value: unknown): string {
  if (typeof value === 'string') return value
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>
    return String(record.record_id || record.semantic_id || '')
  }
  return ''
}
function factDisplay(fact: Fact): string {
  if (fact.value !== null && fact.value !== undefined) return String(fact.value) + (fact.unit || '')
  if (fact.status === 'UNEVALUABLE') return '不可评估'
  if (fact.status === 'UNBOUND') return '未映射'
  return '未定义'
}
function calculationText(step: Step): string {
  const missing = step.missing || []
  if (missing.length) return `缺失 ${missing.join('、')} · 不可评估`
  if (step.error) return step.error
  const inputs = step.inputs || {}
  let expression = step.expression || ''
  for (const [key, value] of Object.entries(inputs)) {
    expression = expression.replace(new RegExp(`\\b${key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`, 'g'), value === null ? '未定义' : String(value))
  }
  const result = step.result ?? step.value
  return expression ? `${expression} = ${result === null || result === undefined ? '不可评估' : result}`
    : result === null || result === undefined ? '不可评估' : String(result)
}
function contractValue(value: unknown): string {
  if (value === null || value === undefined) return '未定义'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (Array.isArray(value)) return value.map(contractValue).join('、')
  if (typeof value === 'object') return Object.entries(value).map(([key, item]) => `${contractFieldLabel[key] || key}：${contractValue(item)}`).join('；')
  return String(value)
}
function ResultCard({ title, result }: { title: string; result: Outcome }) {
  const text = outcomeText(result.text)
  const records = (result.used_records || []).map(recordId).filter(Boolean)
  return <section className="material-outcome">
    <div><strong>{title}</strong><span className="material-state">{statusText(result.status)}</span></div>
    <p>{text || '未形成候选'}</p>
    {!!result.issues?.length && <details className="material-disclosure"><summary>待核对 {result.issues.length} 项</summary><ul>{result.issues.map((issue, index) => <li key={index}>{typeof issue === 'string' ? issue : issue.message || issue.code || '待核对'}</li>)}</ul></details>}
    {!!records.length && <details className="material-disclosure"><summary>引用 {records.length} 条</summary><ul>{records.map((id, index) => <li key={`${id}-${index}`}>{id}</li>)}</ul></details>}
  </section>
}

export default function MaterialApplicationPanel({ projectId, sectionId, onConfigurationChanged, onRecordClick }: {
  projectId: string; sectionId: string; onConfigurationChanged: () => Promise<void>;
  onRecordClick?: (categoryId: string, recordId: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState<Data | null>(null)
  const [draft, setDraft] = useState<Record<string, Mode>>({})
  const [group, setGroup] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const [detail, setDetail] = useState<Item | null>(null)
  const [inputMode, setInputMode] = useState<'project' | 'example'>('project')
  const [experimentMode, setExperimentMode] = useState<'remove' | 'equivalent'>('remove')
  const [exampleValues, setExampleValues] = useState<Record<string, string>>(defaults)
  const [simulation, setSimulation] = useState<Simulation | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<'save' | 'simulate' | ''>('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const detailRef = useRef<HTMLDivElement>(null)
  const actionRef = useRef<HTMLButtonElement | null>(null)
  const contextVersion = useRef(0)
  const path = '/projects/' + encodeURIComponent(projectId) + '/writing/materials'
  const readPath = path + '?section_id=' + encodeURIComponent(sectionId)

  const applyData = (result: Data) => {
    setData(result); setDraft(initialModes(result)); setSimulation(null); setError('')
    setSelected((current) => current.filter((id) => result.items.some((item) => item.category_id === id)))
    const first = result.groups[0]
    const fallback = first ? typeof first === 'string' ? first : first.name : result.items[0]?.group || ''
    setGroup((current) => result.items.some((item) => item.group === current) ? current : fallback)
  }
  useEffect(() => {
    let active = true
    ++contextVersion.current
    setLoading(true); setData(null); setError(''); setNotice(''); setGroup(''); setSelected([]); setSimulation(null)
    setInputMode('project'); setExperimentMode('remove'); setExampleValues(defaults); setBusy('')
    api<Data>(readPath).then((result) => { if (active) applyData(result) })
      .catch((cause: Error) => { if (active) setError(cause.message) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false; ++contextVersion.current }
  }, [readPath])
  useEffect(() => { if (detail) detailRef.current?.focus() }, [detail])
  const closeDetail = () => { setDetail(null); actionRef.current?.focus() }

  const groups = useMemo(() => data ? [...new Set([
    ...data.groups.map((item) => typeof item === 'string' ? item : item.name),
    ...data.items.map((item) => item.group),
  ])].filter(Boolean) : [], [data])
  const visible = useMemo(() => data?.items.filter((item) => item.group === group)
    .sort((a, b) => a.number - b.number) || [], [data, group])
  const dirty = Boolean(data?.items.some((item) => draft[item.category_id] !== item.configured_mode))
  const canSimulate = validated.has(sectionId)
  const inputs = inputIds[sectionId] || []
  const slotMap = useMemo(() => new Map((data?.slots || []).map((item) => [item.slot_id, item])), [data])

  const save = async () => {
    if (!data || !dirty || busy) return
    const context = contextVersion.current
    setBusy('save'); setError(''); setNotice('')
    let saved = false
    try {
      const updated = await api<Data>(path + '/' + encodeURIComponent(sectionId), {
        method: 'PUT', body: JSON.stringify({ base_version: data.config_version, settings: settingsFor(data, draft) }),
      })
      saved = true
      if (context !== contextVersion.current) return
      applyData(updated)
      await onConfigurationChanged()
      if (context === contextVersion.current) setNotice('配置已保存')
    } catch (cause) { if (context === contextVersion.current) setError(saved ? '配置已保存，写作包需刷新' : (cause as Error).message) }
    finally { if (context === contextVersion.current) setBusy('') }
  }
  const simulate = async () => {
    if (!data || !canSimulate || !selected.length || busy) return
    const context = contextVersion.current
    setBusy('simulate'); setError(''); setSimulation(null)
    const slotValues: Record<string, string | null> = {}
    if (inputMode === 'example') {
      for (const id of inputs) slotValues[id] = (exampleValues[id] || '').trim() === '' ? null : exampleValues[id].trim()
    }
    try {
      const result = await post<Simulation>(path + '/' + encodeURIComponent(sectionId) + '/simulate', {
        settings: settingsFor(data, draft), selected_categories: selected,
        input_mode: inputMode, slot_values: slotValues, experiment_mode: experimentMode,
      })
      if (result.simulation_only !== true) throw new Error('模拟结果状态异常')
      if (context === contextVersion.current) setSimulation(result)
    } catch (cause) { if (context === contextVersion.current) setError((cause as Error).message) }
    finally { if (context === contextVersion.current) setBusy('') }
  }

  return <section className="workspace-card material-panel">
    <div className="material-top"><div><h3>资料应用 <span>{data?.items.length ?? 30} 类</span></h3>{dirty && <small>配置未保存</small>}</div>
      <button type="button" className="subtle-button" aria-expanded={open} onClick={() => setOpen((value) => !value)}>{open ? '收起' : '配置与模拟'} <ChevronRight size={14} className={open ? 'material-open-icon' : ''} /></button></div>
    {open && <>
      {error && <div className="notice error" role="alert">{error}</div>}
      {notice && <div className="notice success" role="status">{notice}</div>}
      {loading ? <div className="material-loading" role="status">正在读取…</div> : !data ?
        <button type="button" className="subtle-button" onClick={() => { setLoading(true); void api<Data>(readPath).then(applyData).catch((cause: Error) => setError(cause.message)).finally(() => setLoading(false)) }}>重试</button> : <>
        <div className="material-groups" role="tablist" aria-label="资料分组">{groups.map((name) => <button type="button" role="tab" aria-selected={group === name} className={group === name ? 'active' : ''} key={name} onClick={() => setGroup(name)}>{name} <span>{data.items.filter((item) => item.group === name).length}</span></button>)}</div>
        <div className="material-list">{visible.map((item) => <div className="material-row" key={item.category_id}>
          <label title="加入本次模拟"><input type="checkbox" aria-label={'模拟 ' + item.name} checked={selected.includes(item.category_id)} disabled={!!busy || (!selected.includes(item.category_id) && selected.length >= 6)} onChange={() => { setSelected((current) => current.includes(item.category_id) ? current.filter((id) => id !== item.category_id) : [...current, item.category_id]); setSimulation(null) }} /></label>
          <div className="material-name"><b>{String(item.number).padStart(2, '0')}</b><strong>{item.name}</strong></div>
          <span className={'material-role ' + (draft[item.category_id] !== item.configured_mode ? 'pending' : item.effective_role)}>{draft[item.category_id] !== item.configured_mode ? '待保存' : roleLabel[item.effective_role] || '未使用'}</span>
          <select aria-label={item.name + '使用方式'} value={draft[item.category_id] || item.configured_mode} disabled={!!busy} onChange={(event) => { setDraft((current) => ({ ...current, [item.category_id]: event.target.value as Mode })); setSimulation(null); setNotice('') }}>
            {(Object.keys(modeLabel) as Mode[]).map((mode) => <option value={mode} key={mode}>{modeLabel[mode]}</option>)}
          </select>
          <button type="button" className="text-button" onClick={(event) => { actionRef.current = event.currentTarget; setDetail(item) }}><Info size={13} />作用</button>
        </div>)}</div>
        <div className="material-actions"><span>{selected.length ? '已选 ' + selected.length + '/6 类' : '选择资料进行模拟'}</span><button type="button" className="subtle-button" disabled={!dirty || !!busy} onClick={() => void save()}>{busy === 'save' ? '保存中…' : '保存配置'}</button></div>
        <div className="material-simulate"><div className="material-simulate-head"><strong>模拟对比</strong>{canSimulate && <div className="material-simulate-switches"><div className="segmented" role="group" aria-label="试验方式"><button type="button" disabled={!!busy} className={experimentMode === 'remove' ? 'active' : ''} onClick={() => { setExperimentMode('remove'); setSimulation(null) }}>移除类别</button><button type="button" disabled={!!busy} className={experimentMode === 'equivalent' ? 'active' : ''} onClick={() => { setExperimentMode('equivalent'); setSimulation(null) }}>等价信息</button></div><div className="segmented" role="group" aria-label="模拟输入"><button type="button" disabled={!!busy} className={inputMode === 'project' ? 'active' : ''} onClick={() => { setInputMode('project'); setSimulation(null) }}>本项目事实</button><button type="button" disabled={!!busy} className={inputMode === 'example' ? 'active' : ''} onClick={() => { setInputMode('example'); setSimulation(null) }}>示例输入</button></div></div>}</div>
          {!canSimulate ? <div className="material-muted">本章尚未验证推演</div> : <>
            {inputMode === 'example' && !!inputs.length && <div className="material-example-fields">{inputs.map((id) => <label key={id}><span>{slotMap.get(id)?.label || inputLabels[id]}{slotMap.get(id)?.unit ? '（' + slotMap.get(id)?.unit + '）' : ''}</span><input type="text" inputMode={id === 'supplier_name' ? 'text' : 'decimal'} aria-label={'示例' + inputLabels[id]} value={exampleValues[id] ?? ''} disabled={!!busy} onChange={(event) => { setExampleValues((current) => ({ ...current, [id]: event.target.value })); setSimulation(null) }} /></label>)}</div>}
            {inputMode === 'example' && <small className="material-example-badge">示例值仅用于本次模拟</small>}
            <button type="button" className="primary-button" disabled={!selected.length || !!busy} onClick={() => void simulate()}>{busy === 'simulate' ? '模拟中…' : '模拟对比'}</button>
          </>}
        </div>
        {simulation && <div className="material-results">
          <div className="material-results-head"><strong>模拟结果</strong><span>未写入项目或报告</span><button type="button" className="text-button" onClick={() => setSimulation(null)}>清除</button></div>
          <div className="material-outcomes"><ResultCard title="默认适用" result={simulation.baseline} /><ResultCard title="当前设置" result={simulation.configured} /></div>
          <div className="material-experiments"><h4>{simulation.experiment_mode === 'equivalent' ? '等价信息试验' : '逐类移除'}</h4>{simulation.experiments.map((item) => <div key={item.category_id} className="material-experiment"><div><strong>{item.category_id} · {data.items.find((entry) => entry.category_id === item.category_id)?.name || ''}</strong><span className="material-state">{experimentLabel[item.status]}</span></div><p>{item.reason}</p>{item.before_text !== item.after_text && (item.before_text || item.after_text) && <div className="material-difference"><span>{simulation.experiment_mode === 'equivalent' ? '原类别' : '移除前'}：{item.before_text || '未形成候选'}</span><span>{simulation.experiment_mode === 'equivalent' ? '等价信息' : '移除后'}：{item.after_text || '未形成候选'}</span></div>}{item.contract && <details className="material-contract"><summary>信息契约与核对</summary><div className="material-contract-body"><div><span>类型</span><strong>{contractKindLabel[item.contract.kind] || item.contract.kind}</strong></div><div><span>来源记录</span><strong>{item.contract.source_record_ids.join('、') || '无'}</strong></div><div><span>实验来源</span><strong>{item.contract.information_origin === 'same_frozen_fictional_corpus' ? '同一虚构历史语料' : item.contract.information_origin || '未记录'}</strong></div><div><span>替代载体</span><strong>{item.contract.replacement_source === 'project_rule_contract' ? '本项目规则契约' : item.contract.replacement_source === 'historical_information_transcribed_for_synthetic_QA' ? '历史信息转写的合成 QA' : item.contract.replacement_source}</strong></div>{Object.entries(item.contract.fields).map(([key, value]) => <div key={key}><span>{contractFieldLabel[key] || key}</span><strong>{contractValue(value)}</strong></div>)}{Object.entries(item.contract.validation).map(([key, value]) => <div key={key}><span>{contractFieldLabel[key] || key}</span><strong>{contractValue(value)}</strong></div>)}{item.checks && Object.entries(item.checks).map(([key, value]) => <div key={key}><span>{checkLabel[key] || key}</span><strong>{key === 'legacy_refs_reused' ? value ? '是 · 需核对' : '否' : value ? '通过' : '未通过'}</strong></div>)}</div></details>}</div>)}</div>
          {!!simulation.facts?.length && <div className="material-facts"><h4>输入事实</h4>{simulation.facts.map((fact, index) => <div key={fact.slot_id || index}><span>{fact.label || fact.slot_id || '事实'}</span><b>{factDisplay(fact)}</b></div>)}</div>}
          {!!simulation.trace?.length && <div className="material-trace"><h4>计算步骤</h4>{simulation.trace.map((step, index) => <div key={index}><span title={step.target || undefined}>{step.name || step.rule_id || '步骤 ' + (index + 1)}</span><b>{calculationText(step)}</b></div>)}</div>}
        </div>}
      </>}
    </>}
    {detail && <div className="dialog-backdrop" onClick={closeDetail}><div ref={detailRef} tabIndex={-1} className="dialog material-purpose" role="dialog" aria-modal="true" aria-label={detail.name + '作用'} onClick={(event) => event.stopPropagation()} onKeyDown={(event) => { if (event.key === 'Escape') closeDetail() }}><div className="dialog-head"><h2>{String(detail.number).padStart(2, '0')} · {detail.name}</h2><button type="button" className="icon-button" aria-label="关闭作用" onClick={closeDetail}><X size={18} /></button></div><dl><div><dt>本章作用</dt><dd>{detail.chapter_role || detail.purpose || '暂无关联'}</dd></div><div><dt>资料用途</dt><dd>{detail.purpose || '暂无记录'}</dd></div><div><dt>限制</dt><dd>{Array.isArray(detail.limitations) ? detail.limitations.join('；') : detail.limitations || '无'}</dd></div><div><dt>关联记录</dt><dd>{detail.record_ids?.length ? <div className="material-records">{detail.record_ids.map((id) => onRecordClick ? <button type="button" key={id} onClick={() => { closeDetail(); onRecordClick(detail.category_id, id) }}>{id}</button> : <span key={id}>{id}</span>)}</div> : '本章无关联记录'}</dd></div></dl></div></div>}
  </section>
}
