import { useEffect, useState } from 'react'
import { api, post } from './api'
import { ruleExpressionForDisplay } from './rule-display'

type Source = { verified: boolean; location: { document_id: string; filename: string; ref: string; page: number } | null }
type FactOption = { key: string; label: string; data_type: string; value: string | null; unit: string; revision: number; status: string; source: Source }
type Options = { section_id: string; title: string; template_kind: string;
  current_config: { id: string; version: number; kind: string; evidence_keys: string[]; result_keys: string[] } | null;
  facts: FactOption[] }
type Preview = { preview_digest: string; preview_token: string; expires_at: number;
  results: { key: string; label: string; value: string | null; unit: string }[];
  trace: { rule_id: string; expression: string; inputs: Record<string, string | null>; result: string | null }[];
  sources: Record<string, Source> }
type Published = { id: string; version: number; sections: { kind: string }[] }

export default function ChapterMappingPanel({ projectId, reportId, sectionId, onOpenDocument, onOpenFacts, onConfigured }: {
  projectId: string; reportId: string; sectionId: string;
  onOpenDocument: (documentId: string, page: number, ref: string) => void;
  onOpenFacts: (key?: string, action?: 'new' | 'edit' | 'source', label?: string) => void;
  onConfigured: (config: Published) => void | Promise<void>
}) {
  const base = `/projects/${projectId}/reports/${reportId}/chapter-mapping/${sectionId}`
  const [options, setOptions] = useState<Options | null>(null)
  const [mode, setMode] = useState<'narrative' | 'calculation'>('narrative')
  const [keys, setKeys] = useState<string[]>([])
  const [results, setResults] = useState<string[]>([])
  const [query, setQuery] = useState('')
  const [preview, setPreview] = useState<Preview | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    void api<Options>(base).then((value) => {
      if (!active) return
      setOptions(value)
      setMode(value.current_config?.kind === 'narrative' ? 'narrative'
        : value.current_config?.kind === 'calculation' ? 'calculation'
          : value.template_kind === 'narrative' ? 'narrative' : 'calculation')
      setKeys([...new Set([...(value.current_config?.evidence_keys || []),
        ...(value.current_config?.result_keys || [])])])
      setResults(value.current_config?.result_keys || [])
    }).catch((cause: Error) => { if (active) setError(cause.message) })
    return () => { active = false }
  }, [base])

  const choose = (key: string, checked: boolean) => {
    setPreview(null); setError('')
    setKeys((old) => checked ? [...old, key] : old.filter((item) => item !== key))
    if (!checked) setResults((old) => old.filter((item) => item !== key))
  }
  const selection = { mode, fact_keys: keys, result_keys: mode === 'calculation' ? results : [] }
  const makePreview = async () => {
    setBusy(true); setError(''); setPreview(null)
    try { setPreview(await post<Preview>(`${base}/preview`, selection)) }
    catch (cause) { setError((cause as Error).message) }
    finally { setBusy(false) }
  }
  const commit = async () => {
    if (!preview) return
    setBusy(true); setError('')
    try {
      const created = await post<Published>(`${base}/commit`, { ...selection,
        preview_digest: preview.preview_digest, preview_token: preview.preview_token,
        expires_at: preview.expires_at })
      await onConfigured(created)
    } catch (cause) { setError((cause as Error).message); setPreview(null) }
    finally { setBusy(false) }
  }
  const visible = options?.facts.filter((fact) => `${fact.label} ${fact.key}`.toLowerCase().includes(query.toLowerCase())) || []
  const factLabels = Object.fromEntries((options?.facts || []).map((fact) => [fact.key, fact.label]))
  const hasText = options?.facts.some((fact) => keys.includes(fact.key) && fact.data_type === 'text') || false

  return <div className="chapter-mapping">
    {options?.current_config && <div className="scenario-short-note">当前要求 v{options.current_config.version}</div>}
    <div className="scenario-panel-line"><select aria-label="章节写作方式" value={mode} onChange={(event) => {
      setMode(event.target.value as 'narrative' | 'calculation'); setPreview(null); setResults([])
    }}><option value="narrative">原文文字</option><option value="calculation">数值推演</option></select></div>
    <input aria-label="搜索项目事实" placeholder="搜索项目事实" value={query} onChange={(event) => setQuery(event.target.value)} />
    <div className="chapter-mapping-facts">{visible.map((fact) => {
      const selectable = fact.value !== null && fact.source.verified &&
        ['integer', 'decimal', 'text', 'boolean'].includes(fact.data_type)
      return <div className="chapter-mapping-fact" key={fact.key}>
        <label><input type="checkbox" aria-label={`用于本章 ${fact.label}`} checked={keys.includes(fact.key)}
          disabled={!selectable} onChange={(event) => choose(fact.key, event.target.checked)} />
          <span><strong>{fact.label}</strong><small>{fact.value ?? '未定义'}{fact.unit} · {selectable ? fact.status === 'COMPUTED' ? '由项目规则计算' : '原文已定位' : '待核对'}</small></span></label>
        {mode === 'calculation' && keys.includes(fact.key) && ['integer', 'decimal'].includes(fact.data_type) &&
          <label className="chapter-mapping-result"><input type="checkbox" aria-label={`展示指标 ${fact.label}`}
            checked={results.includes(fact.key)} onChange={(event) => {
              setResults((old) => event.target.checked ? [...old, fact.key] : old.filter((key) => key !== fact.key)); setPreview(null)
            }} />指标</label>}
        {fact.source.location ? <button type="button" onClick={() => onOpenDocument(
          fact.source.location!.document_id, fact.source.location!.page, fact.source.location!.ref)}>原文</button>
          : !selectable ? <button type="button" onClick={() => onOpenFacts(fact.key, fact.value === null ? 'edit' : 'source')}>{fact.value === null ? '填写' : '核对'}</button> : null}
      </div>
    })}{!options?.facts.length && <div className="empty">暂无项目事实。<button type="button" onClick={() => onOpenFacts()}>录入事实</button></div>}
    {!!options?.facts.length && !visible.length && <div className="empty">没有匹配的项目事实</div>}</div>
    {error && <div className="notice error" role="alert">{error}</div>}
    {mode === 'narrative' && keys.length > 0 && !hasText && <small>请选一条完整的文字事实</small>}
    {preview ? <div className="scenario-preview"><strong>本章要求预览</strong><div>{keys.length} 项事实 · {mode === 'narrative' ? '原文文字' : `${results.length} 项指标`}</div>
      {preview.results.map((row) => <div key={row.key}><span>{row.label}</span><strong>{row.value ?? '不可评估'}{row.unit}</strong></div>)}
      {!!preview.trace.length && <details><summary>查看计算依据</summary>{preview.trace.map((step) =>
        <p key={step.rule_id}>{ruleExpressionForDisplay(step.expression, factLabels)} · {Object.entries(step.inputs).map(([key, value]) => `${factLabels[key] || key}=${value ?? '未定义'}`).join('、')} → {step.result ?? '不可评估'}</p>)}</details>}
      <div className="scenario-panel-actions"><button type="button" disabled={busy} onClick={() => setPreview(null)}>返回修改</button><button type="button" className="primary-button" disabled={busy} onClick={() => void commit()}>确认本章要求</button></div>
    </div> : <div className="scenario-panel-actions"><button type="button" className="primary-button" disabled={busy || !keys.length || mode === 'narrative' && (keys.length > 3 || !hasText) || mode === 'calculation' && !results.length} onClick={() => void makePreview()}>预览要求</button></div>}
  </div>
}
