import { useEffect, useState } from 'react'
import { api, post } from './api'
import './issue-panel.css'

type Source = { id: string; label: string; statement: string; excerpt?: string; document_id: string; source_refs: string[]; source_type: string }
type Issue = { id: string; kind: 'claim' | 'conflict' | 'gap'; title: string; statement: string; status: 'OPEN' | 'LIMITED' | 'RESOLVED'; revision: number; decision: string; support: Source[]; opposing: Source[]; impacts: { position: number; report_id: string; block_id: string }[]; independent_verification: string }
type Evidence = { id: string; label: string; statement: string; document_id: string; source_refs: string[] }

const kindLabel = { claim: '论断', conflict: '冲突', gap: '缺口' }
const statusLabel = { OPEN: '待处理', LIMITED: '保留', RESOLVED: '已裁决' }

export default function IssuePanel({ projectId, reportId, sectionId, focusIssueId, onChanged, onLocate, onOpenDocument }: {
  projectId: string; reportId: string; sectionId: string; onChanged: () => Promise<void>;
  focusIssueId?: string | null;
  onLocate: (position: number) => void; onOpenDocument: (id: string, page: number, ref: string) => void
}) {
  const base = `/projects/${projectId}`
  const [items, setItems] = useState<Issue[]>([])
  const [evidence, setEvidence] = useState<Evidence[]>([])
  const [selected, setSelected] = useState<Issue | null>(null)
  const [creating, setCreating] = useState(false)
  const [kind, setKind] = useState<'claim' | 'conflict' | 'gap'>('gap')
  const [title, setTitle] = useState('')
  const [statement, setStatement] = useState('')
  const [support, setSupport] = useState('')
  const [opposing, setOpposing] = useState('')
  const [decision, setDecision] = useState('')
  const [resolutionEvidence, setResolutionEvidence] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const load = async () => {
    const [problems, sources] = await Promise.all([
      api<{ items: Issue[] }>(`${base}/issues?section_id=${encodeURIComponent(sectionId)}`),
      api<{ items: Evidence[] }>(`${base}/evidence`),
    ])
    setItems(problems.items); setEvidence(sources.items)
    setSelected((old) => problems.items.find((row) => row.id === old?.id) || null)
  }
  useEffect(() => { let active = true; void Promise.all([
    api<{ items: Issue[] }>(`${base}/issues?section_id=${encodeURIComponent(sectionId)}`),
    api<{ items: Evidence[] }>(`${base}/evidence`),
  ]).then(([problems, sources]) => { if (active) { setItems(problems.items); setEvidence(sources.items) } })
    .catch((cause: Error) => { if (active) setError(cause.message) }); return () => { active = false }
  }, [base, sectionId])
  useEffect(() => {
    if (!focusIssueId) return
    const target = items.find((item) => item.id === focusIssueId)
    if (target) { setSelected(target); setCreating(false) }
  }, [focusIssueId, items])

  const create = async () => {
    if (!title.trim() || !statement.trim() || !sectionId || busy) return
    setBusy(true); setError('')
    try {
      await post(`${base}/issues`, { kind, title: title.trim(), statement: statement.trim(),
        section_ids: [sectionId], support_evidence_ids: support ? [support] : [],
        opposing_evidence_ids: opposing ? [opposing] : [] })
      setCreating(false); setTitle(''); setStatement(''); setSupport(''); setOpposing('')
      await load(); await onChanged()
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }
  const resolve = async () => {
    if (!selected || !decision.trim() || !resolutionEvidence || busy) return
    setBusy(true); setError('')
    try {
      await post(`${base}/issues/${selected.id}/decision`, { revision: selected.revision,
        status: 'RESOLVED', decision: decision.trim(), resolution_evidence_id: resolutionEvidence })
      setDecision(''); setResolutionEvidence(''); await load(); await onChanged()
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }
  const sourceButton = (row: Source) => <button className="issue-source" key={row.id} onClick={() => {
    const ref = row.source_refs[0] || ''
    const page = Number(/^p(\d+)-/.exec(ref)?.[1] || 1)
    onOpenDocument(row.document_id, page, ref)
  }}><strong>{row.label}</strong><small>{(row.excerpt || row.statement).slice(0, 180)}{row.source_type === 'secondary' ? ' · 二手材料' : ''}</small></button>

  return <section className="issue-panel">
    <div className="issue-panel-head"><strong>本章问题 {items.filter((row) => row.status !== 'RESOLVED').length}</strong><button onClick={() => { setCreating((old) => !old); setSelected(null) }}>记录问题</button></div>
    {error && <div className="notice error" role="alert">{error}</div>}
    {items.map((item) => <button className="issue-row" key={item.id} onClick={() => { setSelected(item); setCreating(false) }}><span>{item.title}</span><small>{kindLabel[item.kind]} · {statusLabel[item.status]}</small></button>)}
    {creating && <div className="issue-form"><label>类型<select aria-label="问题类型" value={kind} onChange={(event) => setKind(event.target.value as typeof kind)}><option value="gap">缺口</option><option value="conflict">冲突</option><option value="claim">论断</option></select></label><label>标题<input aria-label="问题标题" value={title} onChange={(event) => setTitle(event.target.value)} /></label><label>内容<textarea aria-label="问题内容" value={statement} onChange={(event) => setStatement(event.target.value)} rows={3} /></label>{kind !== 'gap' && <label>支持来源<select aria-label="支持来源" value={support} onChange={(event) => setSupport(event.target.value)}><option value="">选择证据</option>{evidence.map((row) => <option key={row.id} value={row.id}>{row.label}</option>)}</select></label>}{kind === 'conflict' && <label>相反来源<select aria-label="相反来源" value={opposing} onChange={(event) => setOpposing(event.target.value)}><option value="">选择证据</option>{evidence.filter((row) => row.id !== support).map((row) => <option key={row.id} value={row.id}>{row.label}</option>)}</select></label>}<div className="scenario-panel-actions"><button onClick={() => setCreating(false)}>取消</button><button className="primary-button" onClick={() => void create()} disabled={busy || !title.trim() || !statement.trim() || kind === 'claim' && !support || kind === 'conflict' && (!support || !opposing)}>保存问题</button></div></div>}
    {selected && <div className="issue-detail"><div className="issue-panel-head"><strong>{selected.title}</strong><button onClick={() => setSelected(null)}>关闭</button></div><p>{selected.statement}</p>{selected.independent_verification === "not_recorded" && <small>独立核实未记录</small>}{selected.support.length > 0 && <><small>支持来源</small>{selected.support.map(sourceButton)}</>}{selected.opposing.length > 0 && <><small>相反来源</small>{selected.opposing.map(sourceButton)}</>}{selected.decision && <p>处理记录：{selected.decision}</p>}{selected.impacts.filter((row) => row.report_id === reportId).map((row) => <button className="issue-row" key={row.block_id} onClick={() => onLocate(row.position)}>定位正文 · 第 {row.position} 段</button>)}{selected.status !== 'RESOLVED' && <details><summary>提交裁决</summary><textarea aria-label="裁决依据" value={decision} onChange={(event) => setDecision(event.target.value)} rows={3} /><select aria-label="新增裁决证据" value={resolutionEvidence} onChange={(event) => setResolutionEvidence(event.target.value)}><option value="">选择双方之外的新证据</option>{evidence.filter((row) => ![...selected.support, ...selected.opposing].some((used) => used.id === row.id)).map((row) => <option key={row.id} value={row.id}>{row.label}</option>)}</select><button className="primary-button" onClick={() => void resolve()} disabled={busy || !decision.trim() || !resolutionEvidence}>确认裁决</button></details>}</div>}
  </section>
}
