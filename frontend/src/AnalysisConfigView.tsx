import { useCallback, useEffect, useState } from 'react'
import { ArrowLeft, Check, Plus, Save, Trash2 } from 'lucide-react'
import { api, post } from './api'
import './analysis-config.css'

export type AnalysisConfig = {
  id: string; project_id: string; version: number; revision: number; status: 'DRAFT' | 'PUBLISHED';
  name: string; checksum: string; definitions: ConfigField[]; rules: ConfigRule[];
  sections: ConfigSection[]; published_at: string | null
}
type ConfigField = { key: string; label: string; data_type: 'integer' | 'decimal' | 'text' | 'boolean'; unit: string; group: string; computed: boolean; input_format?: string }
type ConfigRule = { id: string; name: string; target_key: string; expression: string }
type ConfigSection = { id: string; title: string; result_keys: string[] }
type TestResult = { status: string; snapshot: { results: Record<string, { label: string; value: string | null; unit: string }>; trace: { rule_id: string; expression: string; result: string | null; status: string }[] }; test_token: string | null }
type ScenarioOption = { id: string; name: string }

const emptyField = (): ConfigField => ({ key: '', label: '', data_type: 'integer', unit: '', group: '项目输入', computed: false })
const emptyRule = (): ConfigRule => ({ id: '', name: '', target_key: '', expression: '' })
const emptySection = (): ConfigSection => ({ id: '', title: '', result_keys: [] })

export default function AnalysisConfigView({ projectId, scenarios, onClose, onPublished }: {
  projectId: string; scenarios: ScenarioOption[]; onClose: () => void; onPublished: () => void
}) {
  const base = `/projects/${projectId}/analysis/configs`
  const [configs, setConfigs] = useState<AnalysisConfig[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [editing, setEditing] = useState<AnalysisConfig | null>(null)
  const [tab, setTab] = useState<'fields' | 'rules' | 'sections' | 'test'>('fields')
  const [createOpen, setCreateOpen] = useState(false)
  const [newName, setNewName] = useState('')
  const [source, setSource] = useState('blank')
  const [sampleInputs, setSampleInputs] = useState<Record<string, string>>({})
  const [tested, setTested] = useState<TestResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const selected = configs.find((item) => item.id === selectedId) || null
  const changed = !!selected && !!editing && JSON.stringify([editing.name, editing.definitions, editing.rules, editing.sections]) !==
    JSON.stringify([selected.name, selected.definitions, selected.rules, selected.sections])

  const load = useCallback(async (preferred?: string) => {
    const rows = await api<AnalysisConfig[]>(base)
    setConfigs(rows)
    const id = preferred || rows.find((item) => item.status === 'DRAFT')?.id || rows[0]?.id || ''
    setSelectedId(id)
    setEditing(rows.find((item) => item.id === id) || null)
  }, [base])
  useEffect(() => { void load().catch((cause: Error) => setError(cause.message)) }, [load])

  const choose = (item: AnalysisConfig) => {
    if (changed) { setError('请先保存或放弃草稿修改'); return }
    setSelectedId(item.id); setEditing(item); setTested(null); setError(''); setTab('fields')
  }
  const edit = (next: AnalysisConfig) => { setEditing(next); setTested(null); setError('') }
  const patchField = (index: number, key: keyof ConfigField, value: string | boolean) => {
    if (!editing) return
    edit({ ...editing, definitions: editing.definitions.map((row, at) => at === index ? { ...row, [key]: value } : row) })
  }
  const patchRule = (index: number, key: keyof ConfigRule, value: string) => {
    if (!editing) return
    edit({ ...editing, rules: editing.rules.map((row, at) => at === index ? { ...row, [key]: value } : row) })
  }
  const patchSection = (index: number, row: ConfigSection) => {
    if (!editing) return
    edit({ ...editing, sections: editing.sections.map((old, at) => at === index ? row : old) })
  }

  const create = async () => {
    if (!newName.trim() || busy || changed) return
    setBusy(true); setError('')
    try {
      const data = { name: newName.trim(),
        ...(source.startsWith('config:') ? { from_config_id: source.slice(7) } : {}),
        ...(source.startsWith('scenario:') ? { from_scenario_id: source.slice(9) } : {}) }
      const created = await post<AnalysisConfig>(base, data)
      await load(created.id); setCreateOpen(false); setNewName(''); setTab('fields')
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }
  const save = async () => {
    if (!selected || !editing || !changed || busy) return
    setBusy(true); setError('')
    try {
      const saved = await api<AnalysisConfig>(`${base}/${selected.id}`, { method: 'PUT', body: JSON.stringify({
        revision: selected.revision, name: editing.name, definitions: editing.definitions,
        rules: editing.rules, sections: editing.sections,
      }) })
      setConfigs((old) => old.map((item) => item.id === saved.id ? saved : item))
      setEditing(saved); setTested(null)
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }
  const test = async () => {
    if (!selected || busy || changed) return
    setBusy(true); setError(''); setTested(null)
    try {
      const result = await post<TestResult>(`${base}/${selected.id}/test`, {
        revision: selected.revision,
        sample_inputs: Object.fromEntries(selected.definitions.filter((field) => !field.computed)
          .map((field) => [field.key, sampleInputs[field.key]?.trim() || null])),
      })
      setTested(result)
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }
  const publish = async () => {
    if (!selected || !tested?.test_token || busy || changed) return
    setBusy(true); setError('')
    try {
      const published = await post<AnalysisConfig>(`${base}/${selected.id}/publish`, {
        revision: selected.revision, test_token: tested.test_token,
      })
      await load(published.id); setTested(null); onPublished()
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }
  const leave = () => {
    if (changed) { setError('请先保存或放弃草稿修改'); return }
    onClose()
  }
  const writable = selected?.status === 'DRAFT'
  const inputs = editing?.definitions.filter((field) => !field.computed) || []
  const numericFields = editing?.definitions.filter((field) => ['integer', 'decimal'].includes(field.data_type)) || []
  const computedFields = editing?.definitions.filter((field) => field.computed) || []

  return <main className="page analysis-config-page">
    <div className="breadcrumb">项目 / 推演与写作配置</div>
    <div className="analysis-config-top"><button onClick={leave} aria-label="返回报告"><ArrowLeft size={17} /></button><div><h1>推演与写作配置</h1><small>{selected ? `v${selected.version} · ${selected.status === 'DRAFT' ? '草稿' : '已发布'}` : '暂无配置'}</small></div><button className="primary-button" onClick={() => setCreateOpen(true)} disabled={configs.some((item) => item.status === 'DRAFT') || changed}><Plus size={14} /> 新建版本</button></div>
    {error && <div className="notice error" role="alert">{error}<button onClick={() => setError('')}>关闭</button></div>}
    {createOpen && <div className="analysis-config-create"><input aria-label="配置名称" placeholder="配置名称" value={newName} onChange={(event) => setNewName(event.target.value)} /><select aria-label="配置来源" value={source} onChange={(event) => setSource(event.target.value)}><option value="blank">空白配置</option>{configs.filter((item) => item.status === 'PUBLISHED').map((item) => <option key={item.id} value={`config:${item.id}`}>复制配置 v{item.version} · {item.name}</option>)}{scenarios.map((item) => <option key={item.id} value={`scenario:${item.id}`}>复制方案结构 · {item.name}</option>)}</select><button onClick={() => setCreateOpen(false)}>取消</button><button className="primary-button" disabled={!newName.trim() || busy} onClick={() => void create()}>创建</button></div>}
    <div className="analysis-config-layout"><aside className="analysis-config-list">{configs.map((item) => <button key={item.id} className={selectedId === item.id ? 'active' : ''} onClick={() => choose(item)}><strong>v{item.version} · {item.name}</strong><small>{item.status === 'DRAFT' ? '草稿' : '已发布'}</small></button>)}{!configs.length && <div className="empty">暂无配置。点击“新建版本”开始。</div>}</aside>
      {editing && <section className="analysis-config-editor"><div className="analysis-config-editor-head"><input aria-label="当前配置名称" value={editing.name} disabled={!writable} onChange={(event) => edit({ ...editing, name: event.target.value })} /><div>{writable && <><button disabled={!changed || busy} onClick={() => { setEditing(selected); setTested(null) }}>放弃修改</button><button disabled={!changed || busy} onClick={() => void save()}><Save size={13} /> 保存草稿</button></>}</div></div>
        <nav className="analysis-config-tabs" aria-label="配置内容">{([['fields', '字段'], ['rules', '规则'], ['sections', '章节'], ['test', '试算与发布']] as const).map(([id, label]) => <button key={id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>{label}</button>)}</nav>
        {tab === 'fields' && <div className="analysis-config-rows"><div className="analysis-config-grid-head"><span>标识</span><span>名称</span><span>类型</span><span>单位</span><span>分组</span><span>用途</span><span></span></div>{editing.definitions.map((field, index) => <div className="analysis-config-field" key={index}><input aria-label={`字段 ${index + 1} 标识`} placeholder="如 demand" value={field.key} disabled={!writable} onChange={(event) => patchField(index, 'key', event.target.value)} /><input aria-label={`字段 ${index + 1} 名称`} placeholder="中文名称" value={field.label} disabled={!writable} onChange={(event) => patchField(index, 'label', event.target.value)} /><select aria-label={`字段 ${index + 1} 类型`} value={field.data_type} disabled={!writable} onChange={(event) => patchField(index, 'data_type', event.target.value)}><option value="integer">整数</option><option value="decimal">小数</option><option value="text">文本</option><option value="boolean">是/否</option></select><input aria-label={`字段 ${index + 1} 单位`} placeholder="如 套" value={field.unit} disabled={!writable} onChange={(event) => patchField(index, 'unit', event.target.value)} /><input aria-label={`字段 ${index + 1} 分组`} placeholder="如：需求" value={field.group} disabled={!writable} onChange={(event) => patchField(index, 'group', event.target.value)} /><select aria-label={`字段 ${index + 1} 用途`} value={field.computed ? 'computed' : 'input'} disabled={!writable} onChange={(event) => patchField(index, 'computed', event.target.value === 'computed')}><option value="input">输入</option><option value="computed">计算</option></select>{writable && <button aria-label={`删除字段 ${index + 1}`} onClick={() => edit({ ...editing, definitions: editing.definitions.filter((_, at) => at !== index) })}><Trash2 size={14} /></button>}</div>)}{writable && <button className="analysis-config-add" onClick={() => edit({ ...editing, definitions: [...editing.definitions, emptyField()] })}><Plus size={13} /> 添加字段</button>}</div>}
        {tab === 'rules' && <div className="analysis-config-rows">{editing.rules.map((rule, index) => <div className="analysis-config-rule" key={index}><input aria-label={`规则 ${index + 1} 标识`} placeholder="规则 ID" value={rule.id} disabled={!writable} onChange={(event) => patchRule(index, 'id', event.target.value)} /><input aria-label={`规则 ${index + 1} 名称`} placeholder="规则名称" value={rule.name} disabled={!writable} onChange={(event) => patchRule(index, 'name', event.target.value)} /><select aria-label={`规则 ${index + 1} 目标`} value={rule.target_key} disabled={!writable} onChange={(event) => patchRule(index, 'target_key', event.target.value)}><option value="">结果字段</option>{computedFields.map((field) => <option key={field.key} value={field.key}>{field.label} · {field.key}</option>)}</select><input className="analysis-config-expression" aria-label={`规则 ${index + 1} 表达式`} placeholder="如 min(demand, capacity)" value={rule.expression} disabled={!writable} onChange={(event) => patchRule(index, 'expression', event.target.value)} />{writable && <button aria-label={`删除规则 ${index + 1}`} onClick={() => edit({ ...editing, rules: editing.rules.filter((_, at) => at !== index) })}><Trash2 size={14} /></button>}</div>)}{writable && <button className="analysis-config-add" onClick={() => edit({ ...editing, rules: [...editing.rules, emptyRule()] })}><Plus size={13} /> 添加规则</button>}</div>}
        {tab === 'sections' && <div className="analysis-config-rows">{editing.sections.map((section, index) => <div className="analysis-config-section" key={index}><div className="analysis-config-section-head"><input aria-label={`章节 ${index + 1} 标识`} placeholder="章节 ID" value={section.id} disabled={!writable} onChange={(event) => patchSection(index, { ...section, id: event.target.value })} /><input aria-label={`章节 ${index + 1} 标题`} placeholder="章节标题" value={section.title} disabled={!writable} onChange={(event) => patchSection(index, { ...section, title: event.target.value })} />{writable && <button aria-label={`删除章节 ${index + 1}`} onClick={() => edit({ ...editing, sections: editing.sections.filter((_, at) => at !== index) })}><Trash2 size={14} /></button>}</div><div className="analysis-config-key-list">{numericFields.map((field) => <label key={field.key}><input type="checkbox" disabled={!writable} checked={section.result_keys.includes(field.key)} onChange={(event) => patchSection(index, { ...section, result_keys: event.target.checked ? [...section.result_keys, field.key] : section.result_keys.filter((key) => key !== field.key) })} />{field.label} <small>{field.key}</small></label>)}</div></div>)}{writable && <button className="analysis-config-add" onClick={() => edit({ ...editing, sections: [...editing.sections, emptySection()] })}><Plus size={13} /> 添加章节</button>}</div>}
        {tab === 'test' && <div className="analysis-config-test"><div className="analysis-config-samples">{inputs.map((field) => <label key={field.key}><span>{field.label}<small>{field.unit}</small></span><input aria-label={`试算 ${field.label}`} value={sampleInputs[field.key] || ''} disabled={!writable} onChange={(event) => { setSampleInputs((old) => ({ ...old, [field.key]: event.target.value })); setTested(null) }} /></label>)}</div>{writable && <div className="analysis-config-actions"><button disabled={busy || changed} onClick={() => void test()}>试算</button><button className="primary-button" disabled={!tested?.test_token || busy || changed} onClick={() => void publish()}><Check size={14} /> 发布 v{editing.version}</button></div>}{changed && <small>请先保存草稿</small>}{tested && <div className="analysis-config-results"><strong>{tested.status === 'COMPUTED' ? '试算通过' : '输入不足，暂不可发布'}</strong>{editing.definitions.filter((field) => field.computed).map((field) => <div key={field.key}><span>{field.label}</span><b>{tested.snapshot.results[field.key]?.value ?? '不可评估'}{field.unit}</b></div>)}<details><summary>查看计算过程</summary>{tested.snapshot.trace.map((step) => <p key={step.rule_id}>{step.expression} → {step.result ?? step.status}</p>)}</details></div>}</div>}
      </section>}
    </div>
  </main>
}
