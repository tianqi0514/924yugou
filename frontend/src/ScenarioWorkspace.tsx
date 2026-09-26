import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowLeft, BookOpen, Check, ChevronDown, Download, FilePlus2, GitCompareArrows, MoreHorizontal, Play, Plus, Save, Sparkles, X } from 'lucide-react'
import { api, post, type Fact, type Project } from './api'
import { EditorPane, type AnalysisRef, type Block, type EditorActions } from './ReportsView'
import AnalysisConfigView, { type AnalysisConfig } from './AnalysisConfigView'
import './scenario-workspace.css'

type SourceRef = { category_id: string; artifact_id: string; record_id: string; semantic_id?: string | null }
type Definition = { key: string; label: string; data_type: string; unit: string; group: string; computed: boolean; input_format?: string; source_ref?: SourceRef | Record<string, unknown> | null; caliber?: string; period?: string }
type Input = { value: string | null; origin: string; source_ref: SourceRef | Record<string, unknown> | null; review_status: string }
type Scenario = { id: string; name: string; revision: number; blueprint_version: string; corpus_id: string | null; corpus_version: string | null; definitions: Definition[]; rules: { id: string; target_key: string; expression: string; version: string; source_ref?: SourceRef }[]; inputs: Record<string, Input> }
type Result = { key: string; label: string; unit: string; value: string | null; status: string; origin: string; source_ref?: SourceRef | null }
type Snapshot = { definitions: Definition[]; inputs: Record<string, Input>; results: Record<string, Result>; trace: { rule_id: string; rule_version: string; expression: string; target: string; inputs: Record<string, string | null>; result: string | null; status: string; missing: string[]; source_ref?: SourceRef }[]; condition: string; issues: { code: string; severity: string; message: string }[]; status: string; corpus_id: string | null; corpus_version: string | null; configuration?: { id: string; version: number; sections: { id: string; title: string; result_keys: string[]; evidence_keys?: string[]; conditions?: { id: string }[] }[] } }
type Run = { id: string; scenario_id: string; scenario_revision: number; status: string; input_sha256: string; snapshot: Snapshot; created_at: string }
type Issue = { code: string; severity: string; message: string; position?: number }
type Report = { id: string; title: string; version: number; content: Block[]; reviewed: boolean; analysis_run_id: string | null; issues: Issue[]; updated_at: string }
type ReportSummary = { id: string; title: string; version: number; updated_at: string; analysis_run_id: string | null }
type Preview = { run_id: string; section_id: string; mode: string; base_version: number; content: Block[]; model_audit: Record<string, unknown> & { evidence?: { key: string; label: string; status: string; reason: string | null }[] }; expires_at: number; preview_token: string; issues: Issue[]; preserved_blocks?: Block[] }
type RefreshAction = { id: string; kind: 'numbers' | 'table' | 'condition' | 'config_condition' | 'judgement' | 'manual_review' | 'manual'; label: string; block_id: string; position: number; section_id: string | null; before: string; after: string | null; selectable: boolean; reason: string | null; result_keys?: string[] }
type RefreshPreview = { report_version: number; run_id: string; actions: RefreshAction[] }
type Panel = 'inputs' | 'results' | 'draft' | 'sources' | 'check' | 'versions' | 'materials' | null

const sections = [{ id: 'S4', label: '产能与交付' }, { id: 'S7.1', label: '设备购置与报价' }, { id: 'S5.2', label: '配电资料分歧' }]
const groups: Record<string, string> = { demand: '需求', capacity: '产能参数', sales: '销售', supplier: '设备与供应商', project: '项目输入' }
const sourceLabel = (origin: string) => origin === 'historical_reference' ? '历史参考' : origin === 'project_fact' ? '项目事实' : origin === 'scenario_assumption' ? '方案假设' : '待补'

function textOf(block: Block): string {
  if (block.type === 'table') return block.children.map((row) => row.children.map((cell) => cell.children.map(textOf).join('')).join(' | ')).join(' / ')
  return block.children.map((child) => child.type === 'fact_ref' ? child.display : child.type === 'a'
    ? child.children.map((leaf) => leaf.text).join('') : child.text).join('')
}

function refsOf(block: Block): AnalysisRef[] {
  if (block.type !== 'table') return block.analysis_refs || []
  return [...(block.analysis_refs || []), ...block.children.flatMap((row) => row.children.flatMap((cell) => cell.children.flatMap((p) => p.analysis_refs || [])))]
}

function shown(value: string | null | undefined, unit = ''): string {
  return value == null ? '未定义' : `${value}${unit}`
}

function percentageInput(field: Definition, scenario: Scenario): boolean {
  return field.input_format === 'percentage' || !!scenario.corpus_id && ['N030', 'N031'].includes(field.key)
}

function inputDisplay(field: Definition, scenario: Scenario, value: string | null | undefined): string {
  if (value == null) return ''
  if (!percentageInput(field, scenario)) return value
  return String(Math.round(Number(value) * 100000000) / 1000000)
}

function inputStored(field: Definition, scenario: Scenario, value: string): string | null {
  if (!value) return null
  if (!percentageInput(field, scenario)) return value
  const percent = Number(value)
  return Number.isFinite(percent) ? String(percent / 100) : value
}

function timeLabel(value: string): string { return new Date(value).toLocaleString('zh-CN', { hour12: false }) }

const pendingKey = (projectId: string, scenarioId: string) => `report-platform-scenario-input:${projectId}:${scenarioId}`
function readPending(projectId: string, scenarioId: string): Record<string, string | null> {
  if (!scenarioId) return {}
  try {
    const stored = JSON.parse(window.sessionStorage.getItem(pendingKey(projectId, scenarioId)) || '{}')
    return stored && typeof stored === 'object' && !Array.isArray(stored) ? stored : {}
  } catch { return {} }
}
function writePending(projectId: string, scenarioId: string, changes: Record<string, string | null>) {
  if (!scenarioId) return
  const key = pendingKey(projectId, scenarioId)
  if (Object.keys(changes).length) window.sessionStorage.setItem(key, JSON.stringify(changes))
  else window.sessionStorage.removeItem(key)
}

export default function ScenarioWorkspace({ project, notify, onOpenFacts, onOpenCorpus, onLegacy }: {
  project: Project; notify: (message: string) => void; onOpenFacts: () => void; onOpenCorpus: () => void; onLegacy: () => void
}) {
  const base = `/projects/${project.id}`
  const analysis = `${base}/analysis`
  const [reports, setReports] = useState<ReportSummary[]>([])
  const [reportId, setReportId] = useState<string | null>(new URLSearchParams(location.search).get('report'))
  const [configOpen, setConfigOpen] = useState(new URLSearchParams(location.search).get('config') === '1')
  const [report, setReport] = useState<Report | null>(null)
  const [content, setContent] = useState<Block[]>([])
  const contentRef = useRef<Block[]>([])
  const [dirty, setDirty] = useState(false)
  const dirtyRef = useRef(false)
  const saving = useRef(false)
  const [saveState, setSaveState] = useState('已保存')
  const [editorKey, setEditorKey] = useState(0)
  const editorActions = useRef<EditorActions | null>(null)
  const [panel, setPanel] = useState<Panel>(null)
  const [moreOpen, setMoreOpen] = useState(false)
  const [error, setError] = useState('')
  const [newTitle, setNewTitle] = useState('')
  const [creating, setCreating] = useState(false)
  const [scenarios, setScenarios] = useState<Scenario[]>([])
  const [configs, setConfigs] = useState<AnalysisConfig[]>([])
  const [configId, setConfigId] = useState('')
  const [scenarioId, setScenarioId] = useState('')
  const scenario = scenarios.find((item) => item.id === scenarioId) || null
  const [scenarioName, setScenarioName] = useState('')
  const [edits, setEdits] = useState<Record<string, string | null>>({})
  const [simulation, setSimulation] = useState<Snapshot | null>(null)
  const [runs, setRuns] = useState<Run[]>([])
  const runsRequest = useRef(0)
  const runRequestKey = useRef<{ revision: string; value: string } | null>(null)
  const [runId, setRunId] = useState('')
  const currentRun = runs.find((item) => item.id === runId && item.scenario_id === scenarioId) || null
  const [selectedResult, setSelectedResult] = useState('')
  const [compareIds, setCompareIds] = useState<string[]>([])
  const [selectedSection, setSelectedSection] = useState('S4')
  const activeRun = runs.find((row) => row.id === report?.analysis_run_id) || null
  const availableSections = activeRun?.snapshot.configuration?.sections.map((item) => ({ id: item.id, label: item.title })) || sections
  const effectiveSection = availableSections.some((item) => item.id === selectedSection) ? selectedSection : availableSections[0]?.id || 'S4'
  const selectedConfigSection = activeRun?.snapshot.configuration?.sections.find((item) => item.id === effectiveSection)
  const modelAvailable = !activeRun?.snapshot.configuration || !!(selectedConfigSection?.conditions?.length && selectedConfigSection?.evidence_keys?.length)
  const [selectedPosition, setSelectedPosition] = useState(0)
  const [draftMode, setDraftMode] = useState<'computed' | 'model'>('computed')
  const [candidate, setCandidate] = useState<Preview | null>(null)
  const [candidateSelected, setCandidateSelected] = useState<string[]>([])
  const [impact, setImpact] = useState<{ proposed_run_id: string; impacts: { position: number; result_key: string; before: string; after: string | null }[] } | null>(null)
  const [refreshPreview, setRefreshPreview] = useState<RefreshPreview | null>(null)
  const [refreshSelected, setRefreshSelected] = useState<string[]>([])
  const [versions, setVersions] = useState<{ version: number; reviewed: boolean; created_at: string; analysis_run_id: string | null }[]>([])
  const [comparison, setComparison] = useState<{ changes: { position: number; before: string; after: string }[] } | null>(null)
  const [sourceDetail, setSourceDetail] = useState<{ summary: string; location?: unknown;
    source_ref?: SourceRef & { corpus_id?: string }; payload?: { status?: string; limitations?: string[] } } | null>(null)
  const [facts, setFacts] = useState<Fact[]>([])
  const [busy, setBusy] = useState(false)

  const loadLists = useCallback(async () => {
    const [reportRows, scenarioRows, factData, configRows] = await Promise.all([
      api<ReportSummary[]>(`${base}/reports`), api<Scenario[]>(`${analysis}/scenarios`),
      api<{ facts: Fact[] }>(`${base}/facts`), api<AnalysisConfig[]>(`${analysis}/configs`),
    ])
    setReports(reportRows); setScenarios(scenarioRows); setFacts(factData.facts); setConfigs(configRows)
    setConfigId((old) => configRows.some((row) => row.id === old && row.status === 'PUBLISHED') ? old : configRows.find((row) => row.status === 'PUBLISHED')?.id || '')
    setScenarioId((previous) => {
      const remembered = window.sessionStorage.getItem(`report-platform-scenario-selected:${project.id}`)
      return scenarioRows.some((row) => row.id === previous) ? previous :
        scenarioRows.find((row) => row.id === remembered)?.id || scenarioRows[0]?.id || ''
    })
  }, [analysis, base, project.id])

  const loadReport = useCallback(async (id: string) => {
    const item = await api<Report>(`${base}/reports/${id}`)
    setReport(item); setContent(item.content); contentRef.current = item.content
    setRefreshPreview(null); setRefreshSelected([])
    setDirty(false); dirtyRef.current = false; setSaveState('已保存'); setEditorKey((key) => key + 1)
  }, [base])

  const loadRuns = useCallback(async (id: string) => {
    const request = ++runsRequest.current
    if (!id) { setRuns([]); return }
    const rows = await api<Run[]>(`${analysis}/scenarios/${id}/runs`)
    if (request !== runsRequest.current) return
    setRuns((old) => [...rows, ...old.filter((row) => (row.id === report?.analysis_run_id || compareIds.includes(row.id)) &&
      !rows.some((current) => current.id === row.id))])
    setRunId((old) => rows.some((row) => row.id === old) ? old : rows[0]?.id || '')
  }, [analysis, report?.analysis_run_id, compareIds])

  useEffect(() => { void loadLists().catch((cause: Error) => setError(cause.message)) }, [loadLists])
  useEffect(() => {
    setEdits(readPending(project.id, scenarioId))
    if (scenarioId) window.sessionStorage.setItem(`report-platform-scenario-selected:${project.id}`, scenarioId)
  }, [project.id, scenarioId])
  useEffect(() => { if (reportId) void loadReport(reportId).catch((cause: Error) => { setReportId(null); setError(cause.message) }); else setReport(null) }, [reportId, loadReport])
  useEffect(() => { void loadRuns(scenarioId).catch((cause: Error) => setError(cause.message)) }, [scenarioId, loadRuns])
  useEffect(() => {
    if (!report?.analysis_run_id || runs.some((row) => row.id === report.analysis_run_id)) return
    void api<Run>(`${analysis}/runs/${report.analysis_run_id}`).then((row) => {
      setRuns((old) => old.some((item) => item.id === row.id) ? old : [row, ...old])
    }).catch((cause: Error) => setError(cause.message))
  }, [analysis, report?.analysis_run_id, runs])
  useEffect(() => {
    const onPop = () => { const query = new URLSearchParams(location.search); setReportId(query.get('report')); setConfigOpen(query.get('config') === '1') }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])
  useEffect(() => {
    const beforeNavigate = (event: Event) => {
      if (dirtyRef.current || saving.current || Object.keys(edits).length) {
        event.preventDefault()
        setError('请先保存正文和方案输入')
      }
    }
    window.addEventListener('report-platform-before-navigate', beforeNavigate)
    return () => window.removeEventListener('report-platform-before-navigate', beforeNavigate)
  }, [edits])

  const openReport = (id: string | null) => {
    if (dirtyRef.current) { setError('正文正在保存，请稍后切换报告'); return }
    const url = new URL(window.location.href)
    if (id) url.searchParams.set('report', id)
    else url.searchParams.delete('report')
    window.history.pushState({}, '', url)
    setReportId(id); setPanel(null); setCandidate(null); setRefreshPreview(null); setRefreshSelected([]); setError('')
  }

  const createReport = async () => {
    if (!newTitle.trim() || busy) return
    setBusy(true); setError('')
    try {
      const item = await post<Report>(`${base}/reports`, { title: newTitle.trim() })
      await loadLists(); setNewTitle(''); setCreating(false); openReport(item.id)
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }

  const createScenario = async (source: 'historical' | 'project' | 'copy' | 'config', copyInputs = false) => {
    if (Object.keys(edits).length) { setError('请先保存或放弃当前输入修改'); return }
    setBusy(true); setError('')
    try {
      const created = await post<Scenario>(`${analysis}/scenarios`, {
        name: scenarioName.trim() || (source === 'copy' ? `${scenario?.name || '方案'} · 副本` : '基准方案'),
        source, ...(source === 'copy' || source === 'config' && copyInputs ? { copy_from: scenarioId } : {}),
        ...(source === 'config' ? { config_id: configId } : {}),
      })
      setScenarios((old) => [created, ...old]); setScenarioId(created.id)
      setScenarioName(''); setEdits({}); setSimulation(null); setRuns([]); setRunId('')
      setPanel('inputs'); notify('方案已创建')
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }

  const openConfig = () => {
    if (dirtyRef.current || Object.keys(edits).length) { setError('请先保存正文和方案输入'); return }
    const url = new URL(window.location.href)
    url.searchParams.set('config', '1')
    window.history.pushState({}, '', url)
    setConfigOpen(true); setPanel(null); setError('')
  }

  const closeConfig = () => {
    const url = new URL(window.location.href)
    url.searchParams.delete('config')
    window.history.pushState({}, '', url)
    setConfigOpen(false)
    void loadLists().catch((cause: Error) => setError(cause.message))
  }

  const switchScenario = (id: string) => {
    if (Object.keys(edits).length) { setError('请先保存或放弃当前输入修改'); return }
    setScenarioId(id); setSimulation(null); setSelectedResult('')
  }

  const discardInputs = () => {
    writePending(project.id, scenarioId, {})
    setEdits({}); setSimulation(null)
  }

  const changeInput = (field: Definition, value: string) => {
    if (!scenario) return
    const stored = inputStored(field, scenario, value)
    setEdits((old) => {
      const next = { ...old }
      if (stored === (scenario.inputs[field.key]?.value ?? null)) delete next[field.key]
      else next[field.key] = stored
      writePending(project.id, scenario.id, next)
      return next
    })
    setSimulation(null)
  }

  const persistScenario = async () => {
    if (!scenario || !Object.keys(edits).length) return
    setBusy(true); setError('')
    try {
      const updated = await api<Scenario>(`${analysis}/scenarios/${scenario.id}`, {
        method: 'PUT', body: JSON.stringify({ base_revision: scenario.revision, changes: edits }),
      })
      setScenarios((old) => old.map((item) => item.id === updated.id ? updated : item))
      writePending(project.id, scenario.id, {})
      setEdits({}); setSimulation(null); notify('方案输入已保存')
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }

  const previewInputs = async () => {
    if (!scenario) return
    setBusy(true); setError('')
    try {
      const response = await post<{ run: Snapshot }>(`${analysis}/scenarios/${scenario.id}/preview`, {
        base_revision: scenario.revision, changes: edits,
      })
      setSimulation(response.run)
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }

  const persistRun = async () => {
    if (!scenario) return
    setBusy(true); setError('')
    let inputsSaved = false
    try {
      let updated = scenario
      if (Object.keys(edits).length) {
        updated = await api<Scenario>(`${analysis}/scenarios/${scenario.id}`, {
          method: 'PUT', body: JSON.stringify({ base_revision: scenario.revision, changes: edits }),
        })
        setScenarios((old) => old.map((item) => item.id === updated.id ? updated : item))
        writePending(project.id, scenario.id, {})
        setEdits({})
        inputsSaved = true
      }
      const revision = `${scenario.id}:${updated.revision}`
      if (runRequestKey.current?.revision !== revision)
        runRequestKey.current = { revision, value: crypto.randomUUID() }
      const saved = await post<Run>(`${analysis}/scenarios/${scenario.id}/runs`, {
        scenario_revision: updated.revision, request_key: runRequestKey.current.value,
      })
      runRequestKey.current = null
      setRuns((old) => [saved, ...old]); setRunId(saved.id)
      setEdits({}); setSimulation(null); setPanel('results')
      notify('推演结果已保存')
    } catch (cause) { setError(`${inputsSaved ? '方案已保存，推演未完成：' : ''}${(cause as Error).message}`) } finally { setBusy(false) }
  }

  const persistBody = useCallback(async () => {
    if (!reportId || !report || !dirtyRef.current || saving.current) return
    saving.current = true; setSaveState('保存中…')
    const captured = contentRef.current
    const baseVersion = report.version
    try {
      const preview = await post<{ preview_token: string }>(`${base}/reports/${reportId}/preview`, {
        content: captured, base_version: baseVersion,
      })
      const response = await api<{ report: Report }>(`${base}/reports/${reportId}`, {
        method: 'PUT', body: JSON.stringify({ content: captured, base_version: baseVersion,
          preview_token: preview.preview_token }),
      })
      setReport(response.report); setRefreshPreview(null); setRefreshSelected([])
      const changedSince = JSON.stringify(contentRef.current) !== JSON.stringify(captured)
      dirtyRef.current = changedSince; setDirty(changedSince)
      setSaveState(changedSince ? '等待保存' : '已保存')
    } catch (cause) { setSaveState('保存失败'); setError((cause as Error).message) }
    finally { saving.current = false }
  }, [base, reportId, report])

  useEffect(() => {
    if (!dirty || saveState === '保存失败') return
    const timer = window.setTimeout(() => void persistBody(), 1200)
    return () => window.clearTimeout(timer)
  }, [dirty, content, persistBody, saveState])
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
        event.preventDefault(); if (!event.isComposing) void persistBody()
      }
      if (event.key === 'Escape') { setPanel(null); setMoreOpen(false) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [persistBody])

  const selectRun = async (id: string) => {
    if (!report || dirtyRef.current) { setError('请先保存正文'); return }
    setBusy(true); setError('')
    try {
      const result = await api<{ proposed_run_id: string; impacts: { position: number; result_key: string; before: string; after: string | null }[] }>(
        `${analysis}/reports/${report.id}/impact/${id}`)
      setImpact(result)
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }

  const confirmRun = async () => {
    if (!report || !impact) return
    setBusy(true); setError('')
    try {
      await post(`${analysis}/reports/${report.id}/select`, {
        run_id: impact.proposed_run_id, base_version: report.version,
      })
      const active = impact.proposed_run_id
      setImpact(null); setRunId(active); await loadReport(report.id); setPanel('check')
      notify('报告已采用该次推演；受影响段落待更新')
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }

  const previewRefresh = async () => {
    if (!report || dirtyRef.current) { setError('请先保存正文'); return }
    setBusy(true); setError(''); setRefreshPreview(null); setRefreshSelected([])
    try {
      const preview = await api<RefreshPreview>(`${analysis}/reports/${report.id}/refresh/preview`)
      setRefreshPreview(preview)
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }

  const applyRefresh = async () => {
    if (!report || !refreshPreview || !refreshSelected.length || dirtyRef.current) return
    setBusy(true); setError('')
    try {
      await post(`${analysis}/reports/${report.id}/refresh/commit`, {
        run_id: refreshPreview.run_id,
        base_version: refreshPreview.report_version,
        operation_ids: refreshSelected,
      })
      const scroll = window.scrollY
      await loadReport(report.id)
      window.requestAnimationFrame(() => window.scrollTo(0, scroll))
      notify('所选位置已更新，请核对剩余问题')
    } catch (cause) {
      setError((cause as Error).message)
      if ((cause as Error).message.includes('重新预览')) { setRefreshPreview(null); setRefreshSelected([]); await loadReport(report.id) }
    } finally { setBusy(false) }
  }

  const locateBlock = (position: number, blockId?: string) => {
    setSelectedPosition(position)
    const block = Array.from(document.querySelectorAll<HTMLElement>('.plate-content [data-block-id]'))
      .find((element) => element.dataset.blockId === blockId)
    block?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }

  const previewDraft = async () => {
    if (!report || !report.analysis_run_id || dirtyRef.current) return
    setBusy(true); setError(''); setCandidate(null)
    try {
      const draft = await post<Preview>(`${analysis}/reports/${report.id}/draft/preview`, {
        run_id: report.analysis_run_id, section_id: effectiveSection, mode: modelAvailable ? draftMode : 'computed',
      })
      setCandidate(draft); setCandidateSelected(draft.content.filter((block) => block.type !== 'h2').map((block) => block.id || ''))
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }

  const acceptDraft = async () => {
    if (!candidate || !report || !candidateSelected.length) return
    setBusy(true); setError('')
    try {
      await post(`${analysis}/reports/${report.id}/draft/commit`, {
        ...candidate, replace_section: report.content.some((block) => block.section_id === candidate.section_id),
        ...(candidateSelected.length < candidate.content.filter((block) => block.type !== 'h2').length
          ? { selected_block_ids: candidateSelected } : {}),
      })
      setCandidate(null); setCandidateSelected([]); await loadReport(report.id); await loadLists(); setPanel(null)
      notify('章节已加入报告')
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }

  const review = async () => {
    if (!report || dirtyRef.current) return
    setBusy(true); setError('')
    try { const updated = await post<Report>(`${base}/reports/${report.id}/review`, {}); setReport(updated); notify('当前版本已核对') }
    catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }

  const openVersions = async () => {
    if (!report) return
    setPanel('versions'); setComparison(null)
    try { setVersions(await api(`${base}/reports/${report.id}/versions`)) }
    catch (cause) { setError((cause as Error).message) }
  }

  const compareVersion = async (version: number) => {
    if (!report) return
    try { setComparison(await api(`${base}/reports/${report.id}/compare?base=${version}`)) }
    catch (cause) { setError((cause as Error).message) }
  }

  const source = async (ref: SourceRef) => {
    setPanel('sources'); setSourceDetail(null); setError('')
    try {
      const detail = project.has_corpus
        ? await api<typeof sourceDetail & { summary: string }>(`${base}/corpus/records/${Number(ref.category_id.slice(4))}/${encodeURIComponent(ref.record_id)}`)
        : await api<typeof sourceDetail & { summary: string }>(`${base}/writing/sources/${Number(ref.category_id.slice(4))}/${encodeURIComponent(ref.record_id)}`)
      if (detail.source_ref?.record_id !== ref.record_id || detail.source_ref?.artifact_id !== ref.artifact_id)
        throw new Error('来源记录版本或身份不一致')
      setSourceDetail(detail)
    } catch (cause) { setError((cause as Error).message) }
  }

  const selectedBlock = selectedPosition > 0 ? content[selectedPosition - 1] : null
  const inputFields = scenario?.definitions.filter((field) => !field.computed) || []
  const inputGroups = [...new Set(inputFields.map((field) => field.group || 'project'))]
  const changeCount = Object.keys(edits).length
  const reportHeadings = report?.content.map((block, index) => ({ block, index })).filter(({ block }) => ['h1', 'h2', 'h3'].includes(block.type)) || []
  const visibleIssues = report?.issues.filter((issue, index, all) =>
    all.findIndex((other) => other.code === issue.code && other.position === issue.position && other.message === issue.message) === index) || []
  const blocking = visibleIssues.filter((issue) => issue.severity === 'block')
  const hasRefreshIssues = report?.issues.some((issue) => ['ANALYSIS_RUN_STALE', 'ANALYSIS_VALUE_STALE', 'ANALYSIS_CONDITION_STALE'].includes(issue.code)) || false
  const scenarioRuns = runs.filter((row) => row.scenario_id === scenarioId)
  const resultList = currentRun ? Object.values(currentRun.snapshot.results).filter((row) => scenario?.definitions.some((field) => field.key === row.key && field.computed) || row.origin === 'calculated')
    : []
  const comparisonRuns = compareIds.map((id) => runs.find((row) => row.id === id)).filter((row): row is Run => !!row)
  const comparisonKeys = [...new Set(comparisonRuns.flatMap((row) => Object.values(row.snapshot.results).filter((result) => row.snapshot.definitions.some((field) => field.key === result.key && field.computed) || result.origin === 'calculated').map((result) => result.key)))]

  if (configOpen) return <AnalysisConfigView projectId={project.id} scenarios={scenarios}
    onClose={closeConfig} onPublished={() => { void loadLists().catch((cause: Error) => setError(cause.message)) }} />

  return <main className={`page scenario-page ${report ? 'scenario-editor-page' : ''}`}>
    <div className="breadcrumb">项目 / {project.name} / {report ? '报告写作' : '报告'}</div>
    {error && <div className="notice error" role="alert"><span>{error}</span><button type="button" onClick={() => setError('')} aria-label="关闭错误"><X size={14} /></button></div>}
    {!report ? <>
      <div className="page-header"><h1>报告</h1><div className="scenario-inline"><button onClick={openConfig}>规则与章节配置</button><button onClick={onLegacy}>{project.has_corpus ? '历史文章整理' : '资料起草'}</button><button className="primary-button" onClick={() => setCreating(true)}><Plus size={15} /> 新建报告</button></div></div>
      <div className="scenario-list-card">{reports.length ? reports.map((item) => <button key={item.id} className="scenario-report-row" onClick={() => openReport(item.id)}><BookOpen size={18} /><span><strong>{item.title}</strong><small>{timeLabel(item.updated_at)}</small></span><em>v{item.version}</em></button>) : <div className="empty">暂无报告</div>}</div>
      {creating && <div className="dialog-backdrop" onClick={() => setCreating(false)}><div className="dialog" role="dialog" aria-modal="true" aria-label="新建报告" onClick={(event) => event.stopPropagation()}><div className="dialog-head"><h2>新建报告</h2><button className="icon-button" onClick={() => setCreating(false)} aria-label="关闭"><X size={18} /></button></div><label className="form-field"><span>报告标题</span><input autoFocus value={newTitle} onChange={(event) => setNewTitle(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void createReport() }} /></label><div className="form-actions"><button onClick={() => setCreating(false)}>取消</button><button className="primary-button" disabled={!newTitle.trim() || busy} onClick={() => void createReport()}>创建</button></div></div></div>}
    </> : <>
      <div className="scenario-topbar"><button className="scenario-back" onClick={() => openReport(null)} aria-label="返回报告列表"><ArrowLeft size={17} /></button><div className="scenario-title"><h1>{report.title}</h1><small>v{report.version} · {saveState}{report.reviewed ? ' · 已核对' : ''}</small></div><div className="scenario-run-badge">{activeRun ? `方案结果 · ${activeRun.scenario_revision} 版` : '未选择推演结果'}</div><div className="scenario-top-actions"><button onClick={() => setPanel(panel === 'inputs' ? null : 'inputs')}>输入数据</button><button onClick={() => setPanel(panel === 'results' ? null : 'results')}>推演结果</button><button className="primary-button" disabled={!report.analysis_run_id || dirty || busy} onClick={() => setPanel('draft')}><Sparkles size={14} /> 生成本章</button><button className="scenario-more" aria-label="更多操作" aria-expanded={moreOpen} onClick={() => setMoreOpen((old) => !old)}><MoreHorizontal size={18} /></button>{moreOpen && <div className="scenario-more-menu"><button onClick={() => { setPanel('sources'); setMoreOpen(false) }}>资料与来源</button><button onClick={() => { setPanel('materials'); setMoreOpen(false) }}>项目资料</button><button onClick={() => { setPanel('check'); setMoreOpen(false) }}>检查与导出</button><button onClick={() => { openConfig(); setMoreOpen(false) }}>规则与章节配置</button><button onClick={() => { void openVersions(); setMoreOpen(false) }}>版本</button><button onClick={() => { setMoreOpen(false); onLegacy() }}>{project.has_corpus ? "历史文章整理" : "资料起草"}</button></div>}</div></div>
      <div className={`scenario-workspace ${panel ? 'with-panel' : ''}`}><aside className="scenario-outline"><div className="scenario-outline-header">章节</div>{reportHeadings.map(({ block, index }) => <button key={block.id || index} className={selectedPosition === index + 1 ? 'active' : ''} onClick={() => locateBlock(index + 1, block.id)}>{textOf(block) || '未命名章节'}</button>)}<button className="scenario-outline-add" onClick={() => setPanel('draft')}><Plus size={13} /> 添加章节</button></aside>
        <section className="scenario-canvas"><div className="scenario-canvas-tools"><span>{selectedBlock?.section_id ? availableSections.find((item) => item.id === selectedBlock.section_id)?.label || '报告正文' : '报告正文'}</span><button onClick={() => void persistBody()} disabled={!dirty || saveState === '保存中…'}><Save size={13} /> 保存</button><button onClick={() => setPanel('sources')} disabled={!selectedBlock}>查看依据</button><button onClick={() => setPanel('check')}>检查 {blocking.length > 0 ? `· ${blocking.length}` : ''}</button></div><div className="scenario-paper"><EditorPane key={`${report.id}-${editorKey}`} initial={content} facts={facts.filter((item) => item.value != null)} actionsRef={editorActions} staleFactKeys={[]} onOpenFact={() => { setPanel('sources') }} onSelectPosition={setSelectedPosition} onChange={(next) => { contentRef.current = next; setContent(next); const changed = JSON.stringify(next) !== JSON.stringify(report.content); dirtyRef.current = changed; setDirty(changed); setSaveState(changed ? '等待保存' : '已保存') }} /></div></section>
        {panel && <aside className="scenario-panel" role="complementary"><div className="scenario-panel-head"><h2>{({ inputs: '输入数据', results: '推演结果', draft: '生成本章', sources: '资料与来源', check: '检查与导出', versions: '版本', materials: '资料应用' })[panel]}</h2><button onClick={() => setPanel(null)} aria-label="关闭面板"><X size={18} /></button></div><div className="scenario-panel-body">
          {panel === 'inputs' && <>
            <div className="scenario-panel-line"><select aria-label="选择方案" value={scenarioId} onChange={(event) => switchScenario(event.target.value)}><option value="">选择方案</option>{scenarios.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select>{scenario && <button onClick={() => void createScenario('copy')} title="复制当前方案"><FilePlus2 size={15} /></button>}</div>
            <details className="scenario-create"><summary>新建方案</summary><input aria-label="方案名称" value={scenarioName} onChange={(event) => setScenarioName(event.target.value)} placeholder="如：工期调整方案" /><div className="scenario-inline">{project.has_corpus && <button onClick={() => void createScenario('historical')} disabled={busy}>采用澄岳历史输入</button>}<button onClick={() => void createScenario('project')} disabled={busy}>从项目事实创建</button></div></details>
            <details className="scenario-create"><summary>按已发布配置创建方案</summary>{configs.some((item) => item.status === 'PUBLISHED') ? <><select aria-label="选择写作配置" value={configId} onChange={(event) => setConfigId(event.target.value)}>{configs.filter((item) => item.status === 'PUBLISHED').map((item) => <option key={item.id} value={item.id}>v{item.version} · {item.name}</option>)}</select><div className="scenario-inline"><button disabled={!configId || busy} onClick={() => void createScenario('config')}>创建空输入方案</button><button disabled={!configId || !scenarioId || busy} onClick={() => void createScenario('config', true)}>沿用当前输入</button></div></> : <button onClick={openConfig}>创建配置</button>}</details>
            {scenario && <>{inputFields.length ? inputGroups.map((group) => { const fields = inputFields.filter((field) => (field.group || 'project') === group); return <section className="scenario-input-group" key={group}><h3>{groups[group] || group}</h3>{fields.map((field) => { const entry = scenario.inputs[field.key]; const value = Object.prototype.hasOwnProperty.call(edits, field.key) ? edits[field.key] : entry?.value; const pending = Object.prototype.hasOwnProperty.call(edits, field.key) && value !== entry?.value; const origin = pending ? (value == null ? "missing" : "scenario_assumption") : entry?.origin || "missing"; return <label key={field.key} className="scenario-input"><span>{field.label}<small>{sourceLabel(origin)}{percentageInput(field, scenario) ? ' · %' : field.unit ? ` · ${field.unit}` : ''}</small></span><input aria-label={field.label} value={inputDisplay(field, scenario, value)} type={field.data_type === 'text' ? 'text' : 'number'} step={field.data_type === 'integer' ? '1' : 'any'} onChange={(event) => changeInput(field, event.target.value)} /></label> })}</section> }) : <div className="empty">本项目尚无输入字段。请先录入项目事实与规则。<button onClick={onOpenFacts}>打开项目事实</button></div>}
              <div className="scenario-panel-actions"><button onClick={discardInputs} disabled={!changeCount}>放弃修改</button><button onClick={() => void persistScenario()} disabled={!changeCount || busy}>保存方案</button><button className="primary-button" onClick={() => void previewInputs()} disabled={busy || !inputFields.length}><Play size={14} /> 预览推演</button></div>
              {simulation && <div className="scenario-preview"><h3>预览结果</h3>{Object.values(simulation.results).filter((result) => scenario.definitions.some((field) => field.key === result.key && field.computed) || result.origin === 'calculated').map((result) => <div key={result.key}><span>{result.label}</span><strong>{shown(result.value, result.unit)}</strong></div>)}<small>{simulation.condition}</small><div className="scenario-panel-actions"><button onClick={() => setSimulation(null)}>取消预览</button><button className="primary-button" onClick={() => void persistRun()} disabled={busy}>保存方案并运行</button></div></div>}
            </>}
          </>}
          {panel === 'results' && <><div className="scenario-panel-line"><select aria-label="查看方案" value={scenarioId} onChange={(event) => switchScenario(event.target.value)}>{scenarios.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select><button onClick={() => setPanel('inputs')}>修改输入</button></div>{scenarioRuns.length ? <><select aria-label="查看推演运行" value={runId} onChange={(event) => { setRunId(event.target.value); setSelectedResult('') }}>{scenarioRuns.map((item) => <option key={item.id} value={item.id}>第 {item.scenario_revision} 版 · {timeLabel(item.created_at)}</option>)}</select><div className="scenario-results-list">{resultList.map((result) => <button key={result.key} onClick={() => setSelectedResult(selectedResult === result.key ? '' : result.key)}><span>{result.label}</span><strong>{shown(result.value, result.unit)}</strong></button>)}</div><div className="scenario-condition">{currentRun?.snapshot.condition}</div>{currentRun?.snapshot.issues.map((issue) => <div key={issue.code} className="scenario-short-note">{issue.message}</div>)}{selectedResult && currentRun && <div className="scenario-result-detail"><h3>{currentRun.snapshot.results[selectedResult]?.label}</h3>{currentRun.snapshot.trace.filter((step) => step.target === selectedResult).map((step) => <div key={step.rule_id}><code>{step.expression}</code><p>{Object.entries(step.inputs).map(([key, value]) => `${currentRun.snapshot.results[key]?.label || key}=${value ?? '未定义'}`).join(' · ')}</p><small>{step.status === 'COMPUTED' ? `结果 ${step.result}` : `缺少 ${step.missing.join('、')}`}</small></div>)}</div>}<div className="scenario-panel-actions"><button onClick={() => setCompareIds((old) => old.includes(runId) ? old.filter((id) => id !== runId) : [...old, runId].slice(-3))}><GitCompareArrows size={14} /> {compareIds.includes(runId) ? '移出比较' : '加入比较'}</button><button className="primary-button" disabled={!currentRun || currentRun.status !== 'COMPUTED' || busy || dirty} onClick={() => void selectRun(runId)}><Check size={14} /> 用于报告</button></div>{comparisonRuns.length >= 2 && <div className="scenario-compare"><h3>方案对比</h3><div className="scenario-compare-header"><span>指标</span>{comparisonRuns.map((row) => <strong key={row.id}>{scenarios.find((item) => item.id === row.scenario_id)?.name || "方案"}</strong>)}</div>{comparisonKeys.map((key) => comparisonRuns.some((row) => row.snapshot.results[key]) ? <div key={key}><span>{comparisonRuns[0].snapshot.results[key]?.label || key}</span>{comparisonRuns.map((row) => <strong key={row.id}>{shown(row.snapshot.results[key]?.value, row.snapshot.results[key]?.unit)}</strong>)}</div> : null)}</div>}</> : <div className="empty">尚无推演结果。<button onClick={() => setPanel('inputs')}>打开输入数据</button></div>}{impact && <div className="scenario-confirm"><h3>采用这次推演</h3><p>{impact.impacts.length ? `将有 ${new Set(impact.impacts.map((row) => row.position)).size} 处正文位置待更新；正文不会自动覆盖。` : '报告将绑定这次推演。'}</p>{impact.impacts.filter((row, index, all) => row.before !== row.after && all.findIndex((other) => other.result_key === row.result_key && other.before === row.before && other.after === row.after) === index).map((row) => <div key={row.result_key}>{row.result_key} · {row.before} → {row.after ?? '不可评估'}</div>)}<div className="scenario-panel-actions"><button onClick={() => setImpact(null)}>取消</button><button className="primary-button" onClick={() => void confirmRun()} disabled={busy}>确认采用</button></div></div>}</>}
          {panel === 'draft' && <><label className="scenario-field">章节<select value={effectiveSection} onChange={(event) => { setSelectedSection(event.target.value); setCandidate(null) }}>{availableSections.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label><div className="scenario-segment"><button className={draftMode === 'computed' ? 'active' : ''} onClick={() => { setDraftMode('computed'); setCandidate(null) }}>确定性内容</button>{modelAvailable && <button className={draftMode === 'model' ? 'active' : ''} onClick={() => { setDraftMode('model'); setCandidate(null) }}>模型起草</button>}</div><div className="scenario-panel-actions"><button className="primary-button" onClick={() => void previewDraft()} disabled={!report.analysis_run_id || busy || dirty}><Sparkles size={14} /> 生成候选</button></div>{candidate && <div className="scenario-candidate"><h3>待确认候选</h3>{candidate.model_audit.evidence?.length ? <div className="scenario-short-note">证据 {candidate.model_audit.evidence.filter((row) => row.status === 'VERIFIED').length}/{candidate.model_audit.evidence.length}{candidate.model_audit.evidence.filter((row) => row.status !== 'VERIFIED').map((row) => <p key={row.key}>{row.label}：{row.reason}</p>)}</div> : null}{candidate.content.filter((block) => block.type !== 'h2').map((block, index) => <label className="scenario-candidate-choice" key={block.id || index}><input type="checkbox" aria-label={`采用候选 ${index + 1}`} checked={candidateSelected.includes(block.id || '')} onChange={(event) => setCandidateSelected((old) => event.target.checked ? [...old, block.id || ''] : old.filter((id) => id !== block.id))} /><span><p>{textOf(block)}</p><small>{refsOf(block).length ? `推演结果 ${[...new Set(refsOf(block).map((ref) => ref.result_key))].join('、')}` : (block as { source_refs?: SourceRef[] }).source_refs?.length ? '历史资料 · 待核对' : '待人工核对'}</small></span></label>)}{report.content.some((block) => block.section_id === effectiveSection) && <details className="scenario-short-note"><summary>查看本章更新范围{candidate.preserved_blocks?.length ? ` · 保留 ${candidate.preserved_blocks.length} 段人工内容` : ''}</summary>{report.content.filter((block) => block.section_id === effectiveSection).map((block, index) => <p key={block.id || index}>{textOf(block)}</p>)}</details>}<div className="scenario-panel-actions"><button onClick={() => setCandidate(null)}>取消候选</button><button className="primary-button" onClick={() => void acceptDraft()} disabled={busy || !candidateSelected.length}>{report.content.some((block) => block.section_id === effectiveSection) ? '更新本章' : '加入报告'}</button></div></div>}</>}
          {panel === 'sources' && <>{selectedBlock ? <><h3>第 {selectedPosition} 段依据</h3><p className="scenario-source-text">{textOf(selectedBlock)}</p>{refsOf(selectedBlock).map((ref, index) => <button className="scenario-source-row" key={`${ref.result_key}-${index}`} onClick={() => { setPanel('results'); setRunId(ref.run_id); setSelectedResult(ref.result_key) }}>推演 · {ref.result_key} = {ref.value}{ref.unit} <ChevronDown size={13} /></button>)}{(selectedBlock.source_refs || []).map((ref, index) => <button className="scenario-source-row" key={index} onClick={() => void source(ref)}>历史资料 · {ref.semantic_id || ref.record_id}</button>)}{activeRun && Object.values(activeRun.snapshot.results).filter((row) => row.value !== null && row.key !== 'demand_gap').slice(0, 12).map((row) => <button className="scenario-source-row" key={row.key} onClick={() => { if (row.value !== null) editorActions.current?.insertResult({ ...row, value: row.value }, activeRun.id) }}>插入 {row.label} · {row.value}{row.unit}</button>)}</> : <div className="empty">选中正文段落后查看依据和插入结果。</div>}{sourceDetail && <div className="scenario-source-detail"><h3>来源记录</h3><p>{sourceDetail.summary}</p>{sourceDetail.payload?.status === "source_asserted_not_independently_verified" && <small>来源陈述，尚未独立核实</small>}{sourceDetail.payload?.limitations?.map((item) => <small key={item}>{item}</small>)}</div>}</>}
          {panel === 'check' && (hasRefreshIssues || refreshPreview) && <section className="scenario-refresh">
            <div className="scenario-material-row"><strong>推演变化</strong>{!refreshPreview && <button onClick={() => void previewRefresh()} disabled={busy || dirty}>处理变化</button>}</div>
            {refreshPreview && <>
              {refreshPreview.actions.length ? refreshPreview.actions.map((action) => <div className="scenario-refresh-action" key={action.id}>
                <div className="scenario-refresh-action-head">
                  {action.selectable
                    ? <label><input type="checkbox" aria-label={`${action.kind === 'manual_review' ? '核对并选择' : '选择'}第 ${action.position} 段${action.label}`} checked={refreshSelected.includes(action.id)} onChange={(event) => setRefreshSelected((old) => event.target.checked ? [...old, action.id] : old.filter((id) => id !== action.id))} /><strong>{action.label}</strong></label>
                    : <strong>{action.label}</strong>}
                  <button type="button" onClick={() => locateBlock(action.position, action.block_id)}>第 {action.position} 段</button>
                </div>
                {action.reason && <small>{action.reason}</small>}
                {action.after && (action.kind === 'manual_review'
                  ? <div className="scenario-refresh-compare"><p><b>原文</b> {action.before}</p><p><b>拟更新</b> {action.after}</p></div>
                  : <details><summary>查看变更</summary><p>原文：{action.before}</p><p>更新：{action.after}</p></details>)}
              </div>) : <div className="empty">没有需要更新的位置</div>}
              <div className="scenario-panel-actions"><button onClick={() => { setRefreshPreview(null); setRefreshSelected([]) }}>取消</button><button className="primary-button" disabled={!refreshSelected.length || busy || dirty} onClick={() => void applyRefresh()}>更新所选 {refreshSelected.length} 处</button></div>
            </>}
          </section>}
          {panel === 'check' && <><div className="scenario-check-state"><strong>{report.reviewed ? '已核对' : '待核对'}</strong><span>{blocking.length ? `${blocking.length} 项待处理` : '无阻断项'}</span></div>{visibleIssues.length ? visibleIssues.map((issue, index) => <button className="scenario-issue" key={`${issue.code}-${index}`} onClick={() => { if (issue.position) { locateBlock(issue.position, report.content[issue.position - 1]?.id); setPanel('sources') } }}><span>{issue.message}</span><small>{issue.severity === 'block' ? '待处理' : '保留事项'}</small></button>) : <div className="empty">暂无问题</div>}<div className="scenario-panel-actions"><button onClick={() => void review()} disabled={!!blocking.filter((issue) => issue.code !== 'UNREVIEWED').length || busy || dirty || report.reviewed}>我已核对</button></div><div className="scenario-delivery"><a href={`/api${base}/reports/${report.id}/export?level=preview`} className={dirty ? 'disabled-link' : ''} aria-disabled={dirty}><Download size={14} /> 预审稿</a><a href={report.reviewed && !blocking.length && !dirty ? `/api${base}/reports/${report.id}/export?level=scenario` : undefined} className={report.reviewed && !blocking.length && !dirty ? 'primary-button' : 'disabled-link'} aria-disabled={!report.reviewed || !!blocking.length || dirty}><Download size={14} /> 情景分析报告</a></div></>}
          {panel === 'versions' && <>{versions.map((version) => <button className="scenario-version" key={version.version} onClick={() => void compareVersion(version.version)}><span>v{version.version} · {timeLabel(version.created_at)}</span><small>{version.reviewed ? '已核对' : '工作稿'}</small></button>)}{comparison && <div className="scenario-diff">{comparison.changes.length ? comparison.changes.map((change) => <div key={change.position}><strong>第 {change.position} 段</strong><p>原文：{change.before || '—'}</p><p>当前：{change.after || '—'}</p></div>) : '正文无变化'}</div>}</>}
          {panel === 'materials' && <><div className="scenario-material-row"><strong>本章资料</strong><button onClick={onOpenCorpus}>查看项目资料</button></div>{report.content.filter((block) => block.section_id === effectiveSection).flatMap((block) => block.source_refs || []).map((ref, index) => <button key={index} className="scenario-source-row" onClick={() => void source(ref)}>{ref.semantic_id || ref.record_id} · 查看作用与原文</button>)}</>}
        </div></aside>}
      </div>
    </>}
  </main>
}
