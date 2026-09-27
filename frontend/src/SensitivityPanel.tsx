import { useState } from 'react'
import { post } from './api'

type Field = { key: string; label: string; unit: string; data_type: string; computed: boolean }
type Scenario = { id: string; revision: number; definitions: Field[] }
type Trial = { id: string; input_value: string | null; status: string; condition: string;
  results: Record<string, { label: string; value: string | null; unit: string } | null> }
type Response = { input: { key: string; label: string; unit: string }; result_keys: string[]; runs: Trial[] }

export default function SensitivityPanel({ projectId, scenario, onRuns, onSelect }: {
  projectId: string; scenario: Scenario; onRuns: () => Promise<void>; onSelect: (id: string) => void
}) {
  const inputs = scenario.definitions.filter((row) => !row.computed && ['integer', 'decimal'].includes(row.data_type))
  const outputs = scenario.definitions.filter((row) => row.computed)
  const [inputKey, setInputKey] = useState(inputs[0]?.key || '')
  const [resultKey, setResultKey] = useState(outputs[0]?.key || '')
  const [values, setValues] = useState('')
  const [response, setResponse] = useState<Response | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  if (!inputs.length || !outputs.length) return null
  const run = async () => {
    const parsed = values.split(/[，,\n]/).map((part) => part.trim()).filter(Boolean)
    if (parsed.length < 2 || parsed.length > 8) { setError('请输入 2 至 8 个不同的试算值'); return }
    setBusy(true); setError(''); setResponse(null)
    try {
      const result = await post<Response>(`/projects/${projectId}/analysis/scenarios/${scenario.id}/sensitivity`, {
        base_revision: scenario.revision, input_key: inputKey,
        values: parsed.map((value) => value === '未定义' ? null : value), result_keys: [resultKey],
      })
      setResponse(result)
      await onRuns()
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }
  return <details className="scenario-sensitivity"><summary>敏感性试算</summary>
    <div className="scenario-sensitivity-controls"><label>输入<select aria-label="试算输入" value={inputKey} onChange={(event) => { setInputKey(event.target.value); setResponse(null) }}>{inputs.map((row) => <option value={row.key} key={row.key}>{row.label} · {row.unit}</option>)}</select></label>
      <label>结果<select aria-label="试算结果" value={resultKey} onChange={(event) => { setResultKey(event.target.value); setResponse(null) }}>{outputs.map((row) => <option value={row.key} key={row.key}>{row.label} · {row.unit}</option>)}</select></label>
      <label>试算值<input aria-label="试算值" value={values} onChange={(event) => setValues(event.target.value)} placeholder="300000，200000，0" /></label>
      <button type="button" disabled={busy} onClick={() => void run()}>{busy ? '计算中…' : '试算'}</button></div>
    {error && <small role="alert" className="scenario-sensitivity-error">{error}</small>}
    {response && <div className="scenario-sensitivity-rows">{response.runs.map((row) => <button type="button" key={row.id} onClick={() => onSelect(row.id)}>
      <span>{row.input_value ?? '未定义'}{response.input.unit}</span>
      <strong>{row.results[resultKey]?.value ?? '不可评估'}{row.results[resultKey]?.value == null ? '' : row.results[resultKey]?.unit}</strong>
      <small>{row.condition}</small></button>)}</div>}
  </details>
}
