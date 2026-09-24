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
type Experiment = { category_id: string; status: 'changed' | 'same' | 'blocked' | 'not_consumed'; reason: string;
  before_text?: string | null; after_text?: string | null; used_records?: unknown[] }
type Step = { rule_id?: string; target?: string; status?: string; value?: string | number | null;
  result?: string | number | null; error?: string; expression?: string; inputs?: Record<string, string | number | null>;
  missing?: string[]; name?: string }
type Fact = { slot_id?: string; label?: string; unit?: string; value?: string | number | null; status?: string; fact_key?: string | null }
type Simulation = { baseline: Outcome; configured: Outcome; experiments: Experiment[];
  trace: Step[]; facts: Fact[]; simulation_only: boolean }

const modeLabel: Record<Mode, string> = { auto: '自动选用', review: '仅核对', exclude: '不使用' }
const roleLabel: Record<Role, string> = { input: '写作输入', review: '核对', unused: '未使用', not_generated: '未生成' }
const experimentLabel: Record<Experiment['status'], string> = {
  changed: '有变化', same: '无变化', blocked: '受阻', not_consumed: '未消费',
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

export default function MaterialApplicationPanel({ projectId, sectionId, onConfigurationChanged }: {
  projectId: string; sectionId: string; onConfigurationChanged: () => Promise<void>
}) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState<Data | null>(null)
  const [draft, setDraft] = useState<Record<string, Mode>>({})
  const [group, setGroup] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const [detail, setDetail] = useState<Item | null>(null)
  const [inputMode, setInputMode] = useState<'project' | 'example'>('project')
  const [exampleValues, setExampleValues] = useState<Record<string, string>>(defaults)
  const [simulation, setSimulation] = useState<Simulation | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<'save' | 'simulate' | ''>('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const detailRef = useRef<HTMLDivElement>(null)
  const actionRef = useRef<HTMLButtonElement | null>(null)
  const path = '/projects/' + encodeURIComponent(projectId) + '/writing/materials'
  const readPath = path + '?section_id=' + encodeURIComponent(sectionId)

  const applyData = (result: Data) => {
    setData(result); setDraft(initialModes(result)); setSelected([]); setSimulation(null); setError('')
    const first = result.groups[0]
    setGroup(first ? typeof first === 'string' ? first : first.name : result.items[0]?.group || '')
  }
  useEffect(() => {
    let active = true
    setLoading(true); setData(null); setError(''); setNotice(''); setSelected([]); setSimulation(null)
    api<Data>(readPath).then((result) => { if (active) applyData(result) })
      .catch((cause: Error) => { if (active) setError(cause.message) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
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
    setBusy('save'); setError(''); setNotice('')
    let saved = false
    try {
      await api(path + '/' + encodeURIComponent(sectionId), {
        method: 'PUT', body: JSON.stringify({ base_version: data.config_version, settings: settingsFor(data, draft) }),
      })
      saved = true
      applyData(await api<Data>(readPath))
      await onConfigurationChanged()
      setNotice('配置已保存')
    } catch (cause) { setError(saved ? '配置已保存，刷新失败' : (cause as Error).message) }
    finally { setBusy('') }
  }
  const simulate = async () => {
    if (!data || !canSimulate || !selected.length || busy) return
    setBusy('simulate'); setError(''); setSimulation(null)
    const slotValues: Record<string, string | null> = {}
    if (inputMode === 'example') {
      for (const id of inputs) slotValues[id] = (exampleValues[id] || '').trim() === '' ? null : exampleValues[id].trim()
    }
    try {
      const result = await post<Simulation>(path + '/' + encodeURIComponent(sectionId) + '/simulate', {
        settings: settingsFor(data, draft), selected_categories: selected,
        input_mode: inputMode, slot_values: slotValues,
      })
      if (result.simulation_only !== true) throw new Error('模拟结果状态异常')
      setSimulation(result)
    } catch (cause) { setError((cause as Error).message) }
    finally { setBusy('') }
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
        <div className="material-simulate"><div className="material-simulate-head"><strong>模拟对比</strong>{canSimulate && <div className="segmented" role="group" aria-label="模拟输入"><button type="button" className={inputMode === 'project' ? 'active' : ''} onClick={() => { setInputMode('project'); setSimulation(null) }}>本项目事实</button><button type="button" className={inputMode === 'example' ? 'active' : ''} onClick={() => { setInputMode('example'); setSimulation(null) }}>示例输入</button></div>}</div>
          {!canSimulate ? <div className="material-muted">本章尚未验证推演</div> : <>
            {inputMode === 'example' && !!inputs.length && <div className="material-example-fields">{inputs.map((id) => <label key={id}><span>{slotMap.get(id)?.label || inputLabels[id]}{slotMap.get(id)?.unit ? '（' + slotMap.get(id)?.unit + '）' : ''}</span><input type="text" inputMode={id === 'supplier_name' ? 'text' : 'decimal'} aria-label={'示例' + inputLabels[id]} value={exampleValues[id] ?? ''} disabled={!!busy} onChange={(event) => { setExampleValues((current) => ({ ...current, [id]: event.target.value })); setSimulation(null) }} /></label>)}</div>}
            {inputMode === 'example' && <small className="material-example-badge">示例值仅用于本次模拟</small>}
            <button type="button" className="primary-button" disabled={!selected.length || !!busy} onClick={() => void simulate()}>{busy === 'simulate' ? '模拟中…' : '模拟对比'}</button>
          </>}
        </div>
        {simulation && <div className="material-results">
          <div className="material-results-head"><strong>模拟结果</strong><span>未写入项目或报告</span><button type="button" className="text-button" onClick={() => setSimulation(null)}>清除</button></div>
          <div className="material-outcomes"><ResultCard title="默认适用" result={simulation.baseline} /><ResultCard title="当前设置" result={simulation.configured} /></div>
          <div className="material-experiments"><h4>逐类移除</h4>{simulation.experiments.map((item) => <div key={item.category_id} className="material-experiment"><div><strong>{item.category_id} · {data.items.find((entry) => entry.category_id === item.category_id)?.name || ''}</strong><span className="material-state">{experimentLabel[item.status]}</span></div><p>{item.reason}</p>{item.before_text !== item.after_text && (item.before_text || item.after_text) && <div className="material-difference"><span>移除前：{item.before_text || '未形成候选'}</span><span>移除后：{item.after_text || '未形成候选'}</span></div>}</div>)}</div>
          {!!simulation.facts?.length && <div className="material-facts"><h4>输入事实</h4>{simulation.facts.map((fact, index) => <div key={fact.slot_id || index}><span>{fact.label || fact.slot_id || '事实'}</span><b>{factDisplay(fact)}</b></div>)}</div>}
          {!!simulation.trace?.length && <div className="material-trace"><h4>计算步骤</h4>{simulation.trace.map((step, index) => <div key={index}><span title={step.target || undefined}>{step.name || step.rule_id || '步骤 ' + (index + 1)}</span><b>{calculationText(step)}</b></div>)}</div>}
        </div>}
      </>}
    </>}
    {detail && <div className="dialog-backdrop" onClick={closeDetail}><div ref={detailRef} tabIndex={-1} className="dialog material-purpose" role="dialog" aria-modal="true" aria-label={detail.name + '作用'} onClick={(event) => event.stopPropagation()} onKeyDown={(event) => { if (event.key === 'Escape') closeDetail() }}><div className="dialog-head"><h2>{String(detail.number).padStart(2, '0')} · {detail.name}</h2><button type="button" className="icon-button" aria-label="关闭作用" onClick={closeDetail}><X size={18} /></button></div><dl><div><dt>本章作用</dt><dd>{detail.chapter_role || detail.purpose || '暂无关联'}</dd></div><div><dt>资料用途</dt><dd>{detail.purpose || '暂无记录'}</dd></div><div><dt>限制</dt><dd>{Array.isArray(detail.limitations) ? detail.limitations.join('；') : detail.limitations || '无'}</dd></div><div><dt>关联记录</dt><dd>{detail.record_ids?.length ? detail.record_ids.slice(0, 6).join('、') + (detail.record_ids.length > 6 ? ' 等 ' + detail.record_ids.length + ' 条' : '') : '本章无关联记录'}</dd></div></dl></div></div>}
  </section>
}
