import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowDown, ArrowLeft, ArrowUp, BookOpen, Check, ChevronDown, Download, GitCompareArrows, MoreHorizontal, Pencil, Play, Plus, Save, Sparkles, X } from 'lucide-react'
import { api, post, type Fact, type Project } from './api'
import { EditorPane, type AnalysisRef, type Block, type EditorActions } from './ReportsView'
import AnalysisConfigView, { type AnalysisConfig } from './AnalysisConfigView'
import IssuePanel from './IssuePanel'
import SensitivityPanel from './SensitivityPanel'
import './scenario-workspace.css'

type SourceRef = { category_id: string; artifact_id: string; record_id: string; semantic_id?: string | null }
type Definition = { key: string; label: string; data_type: string; unit: string; group: string; computed: boolean; input_format?: string; source_ref?: SourceRef | Record<string, unknown> | null; caliber?: string; period?: string }
type Input = { value: string | null; origin: string; source_ref: SourceRef | Record<string, unknown> | null; review_status: string }
type Scenario = { id: string; name: string; revision: number; blueprint_version: string; corpus_id: string | null; corpus_version: string | null; definitions: Definition[]; rules: { id: string; target_key: string; expression: string; version: string; source_ref?: SourceRef }[]; inputs: Record<string, Input> }
type Result = { key: string; label: string; unit: string; value: string | null; status: string; origin: string; source_ref?: SourceRef | null }
type Snapshot = { definitions: Definition[]; inputs: Record<string, Input>; results: Record<string, Result>; trace: { rule_id: string; rule_version: string; expression: string; target: string; inputs: Record<string, string | null>; result: string | null; status: string; missing: string[]; source_ref?: SourceRef }[]; condition: string; issues: { code: string; severity: string; message: string }[]; status: string; corpus_id: string | null; corpus_version: string | null; configuration?: { id: string; version: number; sections: { id: string; title: string; kind?: 'narrative' | 'calculation' | 'mixed'; result_keys: string[]; evidence_keys?: string[]; conditions?: { id: string }[] }[] } }
type Run = { id: string; scenario_id: string; scenario_revision: number; status: string; input_sha256: string; snapshot: Snapshot; created_at: string }
type Issue = { code: string; severity: string; message: string; position?: number }
type Report = { id: string; title: string; version: number; content: Block[]; reviewed: boolean; analysis_run_id: string | null; report_type: string; template_version: string; template_snapshot: { label?: string; sections?: { id: string; title: string; kind: string }[] }; issues: Issue[]; updated_at: string }
type ReportSummary = { id: string; title: string; version: number; updated_at: string; analysis_run_id: string | null; status: '工作稿' | '待核对' | '已核对'; has_scenario_assumption?: boolean }
type ReportType = { id: string; label: string; stage: string; version: string; sections: { id: string; title: string; kind: string }[] }
type ChapterStatus = { section_id: string; heading_id: string; title: string; kind: string; status: string; missing_fact_keys: string[]; config_id: string | null }
type ExportEntry = { id: string; report_version: number; level: 'preview' | 'scenario' | 'formal'; analysis_run_id: string | null; sha256: string; created_at: string }
type WorkTask = { id: string; report_id: string; kind: 'model_draft' | 'export'; status: string; stage: string;
  attempt: number; result: { candidate_id?: string; export_id?: string }; error: string | null }
type RunComparison = { rows: { key: string; comparable: boolean; reason: string | null }[] }
type PendingReport = { baseVersion: number; content: Block[]; savedAt: number }
type ChapterDialog = { action: 'add' | 'rename'; headingId?: string; title: string }
type CandidateEvidence = { key: string; label: string; status: string; reason: string | null; evidence_id?: string | null; document_id: string | null; source_refs: string[] }
type ProjectEvidenceItem = { id: string; label: string; statement: string; excerpt: string; document_id: string; document_sha256: string; document_filename: string; source_refs: string[]; fact_key: string | null; fact_revision: number | null; source_type: 'original' | 'secondary'; page: number | null; original_url: string }
type FactProposal = { id: string; report_id: string; report_version: number; block_id: string; fact_key: string; proposed_value: string; source: string; reason: string; status: string }
type FactProposalPreview = { base_version: number; preview_token: string; changes: { key: string; before: { value: string | null }; after: { value: string | null } }[]; report_impacts: { position: number; text: string }[] }
type ChapterMaterial = { category_id: string; name: string; group: string; mode: string; status: string; record_count: number; used_records: { block_id: string; record_id: string; artifact_id: string; use?: string }[]; reason: string }
type ChapterPack = { report_version: number; section_id: string; config_id: string | null; corpus_id: string | null; corpus_version: string | null; policy_revision: number; facts: { key: string; label: string; revision: number; status: string }[]; project_evidence: { id: string; label: string; fact_key: string | null; source_type: string }[]; groups: { name: string; numbers: number[] }[]; categories: ChapterMaterial[] }
type Preview = { candidate_id?: string; run_id?: string; config_id?: string; section_id: string; mode: string; base_version: number; content: Block[]; model_audit: Record<string, unknown> & { evidence?: CandidateEvidence[] }; expires_at: number; preview_token: string; issues: Issue[]; preserved_blocks?: Block[] }
type RefreshAction = { id: string; kind: 'numbers' | 'table' | 'condition' | 'config_condition' | 'judgement' | 'manual_review' | 'rebind' | 'detach' | 'manual'; label: string; block_id: string; position: number; section_id: string | null; before: string; after: string | null; selectable: boolean; reason: string | null; result_keys?: string[]; reference_changes?: { key: string; before: string; after: string | null }[] }
type RefreshPreview = { report_version: number; run_id: string; actions: RefreshAction[] }
type Panel = 'inputs' | 'results' | 'draft' | 'sources' | 'check' | 'versions' | 'materials' | null
type ScenarioCreateSource = 'project' | 'historical' | 'copy' | 'config_facts' | 'config' | 'config_copy'

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

function candidateBody(block: Block) {
  if (block.type !== 'table') return <p>{textOf(block)}</p>
  return <div className="scenario-candidate-table-wrap"><table className="scenario-candidate-table"><tbody>{block.children.map((row, rowIndex) =>
    <tr key={row.id || rowIndex}>{row.children.map((cell, cellIndex) => cell.type === 'th'
      ? <th key={cell.id || cellIndex}>{cell.children.map(textOf).join('')}</th>
      : <td key={cell.id || cellIndex}>{cell.children.map(textOf).join('')}</td>)}</tr>)}</tbody></table></div>
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
const reportPendingKey = (projectId: string, reportId: string) => `report-platform-report-draft:${projectId}:${reportId}`
function readReportPending(projectId: string, reportId: string): PendingReport | null {
  try {
    const value = JSON.parse(window.sessionStorage.getItem(reportPendingKey(projectId, reportId)) || 'null')
    return value && Number.isInteger(value.baseVersion) && Array.isArray(value.content) ? value as PendingReport : null
  } catch { return null }
}
function writeReportPending(projectId: string, reportId: string, pending: PendingReport | null): boolean {
  try {
    if (pending) window.sessionStorage.setItem(reportPendingKey(projectId, reportId), JSON.stringify(pending))
    else window.sessionStorage.removeItem(reportPendingKey(projectId, reportId))
    return true
  } catch { return false }
}
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

export default function ScenarioWorkspace({ project, notify, onOpenFacts, onOpenCorpus, onOpenDocument, onLegacy }: {
  project: Project; notify: (message: string) => void; onOpenFacts: () => void; onOpenCorpus: () => void; onOpenDocument: (id: string, page: number, segment: string) => void; onLegacy: () => void
}) {
  const base = `/projects/${project.id}`
  const analysis = `${base}/analysis`
  const [reports, setReports] = useState<ReportSummary[]>([])
  const [reportQuery, setReportQuery] = useState('')
  const [reportFilter, setReportFilter] = useState('all')
  const [reportId, setReportId] = useState<string | null>(new URLSearchParams(location.search).get('report'))
  const [configOpen, setConfigOpen] = useState(new URLSearchParams(location.search).get('config') === '1')
  const [report, setReport] = useState<Report | null>(null)
  const [chapterStatuses, setChapterStatuses] = useState<ChapterStatus[]>([])
  const [content, setContent] = useState<Block[]>([])
  const contentRef = useRef<Block[]>([])
  const [dirty, setDirty] = useState(false)
  const dirtyRef = useRef(false)
  const saving = useRef(false)
  const [saveState, setSaveState] = useState('已保存')
  const [editorKey, setEditorKey] = useState(0)
  const [recovery, setRecovery] = useState<PendingReport | null>(null)
  const editorActions = useRef<EditorActions | null>(null)
  const [panel, setPanel] = useState<Panel>(null)
  const [moreOpen, setMoreOpen] = useState(false)
  const [error, setError] = useState('')
  const [newTitle, setNewTitle] = useState('')
  const [newReportType, setNewReportType] = useState('custom')
  const [reportTypes, setReportTypes] = useState<ReportType[]>([])
  const [creating, setCreating] = useState(false)
  const [scenarios, setScenarios] = useState<Scenario[]>([])
  const [configs, setConfigs] = useState<AnalysisConfig[]>([])
  const [configId, setConfigId] = useState('')
  const [scenarioId, setScenarioId] = useState('')
  const scenario = scenarios.find((item) => item.id === scenarioId) || null
  const [scenarioName, setScenarioName] = useState('')
  const [scenarioCreateOpen, setScenarioCreateOpen] = useState(false)
  const [scenarioCreateSource, setScenarioCreateSource] = useState<ScenarioCreateSource>('project')
  const [edits, setEdits] = useState<Record<string, string | null>>({})
  const [simulation, setSimulation] = useState<Snapshot | null>(null)
  const [runs, setRuns] = useState<Run[]>([])
  const runsRequest = useRef(0)
  const runRequestKey = useRef<{ revision: string; value: string } | null>(null)
  const [runId, setRunId] = useState('')
  const currentRun = runs.find((item) => item.id === runId && item.scenario_id === scenarioId) || null
  const [selectedResult, setSelectedResult] = useState('')
  const [compareIds, setCompareIds] = useState<string[]>([])
  const [runComparison, setRunComparison] = useState<RunComparison | null>(null)
  const [selectedSection, setSelectedSection] = useState('S4')
  const [selectedHeadingId, setSelectedHeadingId] = useState<string | null>(null)
  const [chapterDialog, setChapterDialog] = useState<ChapterDialog | null>(null)
  const activeRun = runs.find((row) => row.id === report?.analysis_run_id) || null
  const writingConfig = configs.find((item) => item.id === activeRun?.snapshot.configuration?.id) || configs.find((item) => item.id === configId && item.status === 'PUBLISHED')
  const availableSections = writingConfig?.sections.map((item) => ({ id: item.id, label: item.title })) || activeRun?.snapshot.configuration?.sections.map((item) => ({ id: item.id, label: item.title })) || sections
  const selectedHeading = selectedHeadingId ? report?.content.find((block) => block.id === selectedHeadingId && block.type === 'h2') : null
  const effectiveSection = selectedHeadingId
    ? selectedHeading && availableSections.some((item) => item.id === selectedHeading.section_id) ? selectedHeading.section_id || '' : ''
    : availableSections.some((item) => item.id === selectedSection) ? selectedSection : availableSections[0]?.id || ''
  const selectedConfigSection = writingConfig?.sections.find((item) => item.id === effectiveSection) || activeRun?.snapshot.configuration?.sections.find((item) => item.id === effectiveSection)
  const narrativeSection = selectedConfigSection?.kind === 'narrative'
  const draftReady = narrativeSection ? !!writingConfig?.id : !!report?.analysis_run_id
  const modelAvailable = narrativeSection || !activeRun?.snapshot.configuration || !!(selectedConfigSection?.conditions?.length && selectedConfigSection?.evidence_keys?.length)
  const [selectedPosition, setSelectedPosition] = useState(0)
  const [draftMode, setDraftMode] = useState<'computed' | 'model'>('computed')
  const [candidate, setCandidate] = useState<Preview | null>(null)
  const [pendingCandidates, setPendingCandidates] = useState<{ candidate_id: string; section_id: string; mode: string; status: string }[]>([])
  const [candidateSelected, setCandidateSelected] = useState<string[]>([])
  const [evidencePreview, setEvidencePreview] = useState<{ key: string; documentId: string; ref: string; page: number; text: string } | null>(null)
  const [projectEvidence, setProjectEvidence] = useState<ProjectEvidenceItem[]>([])
  const [chapterPack, setChapterPack] = useState<ChapterPack | null>(null)
  const [materialDetail, setMaterialDetail] = useState<ChapterMaterial | null>(null)
  const [materialMode, setMaterialMode] = useState('auto')
  const [evidenceSelection, setEvidenceSelection] = useState<string[]>([])
  const [impact, setImpact] = useState<{ proposed_run_id: string; unchanged_references: number; impacts: { position: number; result_key: string; before: string; after: string | null }[] } | null>(null)
  const [refreshPreview, setRefreshPreview] = useState<RefreshPreview | null>(null)
  const [refreshSelected, setRefreshSelected] = useState<string[]>([])
  const [versions, setVersions] = useState<{ version: number; reviewed: boolean; created_at: string; analysis_run_id: string | null }[]>([])
  const [comparison, setComparison] = useState<{ changes: { position: number; before: string; after: string }[] } | null>(null)
  const [sourceDetail, setSourceDetail] = useState<{ summary: string; location?: unknown;
    source_ref?: SourceRef & { corpus_id?: string }; payload?: { status?: string; limitations?: string[] } } | null>(null)
  const [facts, setFacts] = useState<Fact[]>([])
  const [proposal, setProposal] = useState<FactProposal | null>(null)
  const [proposalPreview, setProposalPreview] = useState<FactProposalPreview | null>(null)
  const [proposalFactKey, setProposalFactKey] = useState('')
  const [proposalValue, setProposalValue] = useState('')
  const [proposalSource, setProposalSource] = useState('')
  const [proposalReason, setProposalReason] = useState('正文修改建议')
  const [busy, setBusy] = useState(false)
  const [exports, setExports] = useState<ExportEntry[]>([])
  const [exportsOpen, setExportsOpen] = useState(false)
  const [activeTask, setActiveTask] = useState<WorkTask | null>(null)

  const loadLists = useCallback(async () => {
    const [reportRows, scenarioRows, factData, configRows, typeRows] = await Promise.all([
      api<ReportSummary[]>(`${base}/reports`), api<Scenario[]>(`${analysis}/scenarios`),
      api<{ facts: Fact[] }>(`${base}/facts`), api<AnalysisConfig[]>(`${analysis}/configs`), api<ReportType[]>('/report-types'),
    ])
    setReports(reportRows); setScenarios(scenarioRows); setFacts(factData.facts); setConfigs(configRows); setReportTypes(typeRows)
    setConfigId((old) => configRows.some((row) => row.id === old && row.status === 'PUBLISHED') ? old : configRows.find((row) => row.status === 'PUBLISHED')?.id || '')
    setScenarioId((previous) => {
      const remembered = window.sessionStorage.getItem(`report-platform-scenario-selected:${project.id}`)
      return scenarioRows.some((row) => row.id === previous) ? previous :
        scenarioRows.find((row) => row.id === remembered)?.id || scenarioRows[0]?.id || ''
    })
  }, [analysis, base, project.id])

  const loadReport = useCallback(async (id: string) => {
    const [item, chapterData, proposals] = await Promise.all([
      api<Report>(`${base}/reports/${id}`),
      api<{ chapters: ChapterStatus[] }>(`${base}/reports/${id}/chapters`),
      api<FactProposal[]>(`${base}/reports/${id}/fact-proposals`),
    ])
    setReport(item); setContent(item.content); contentRef.current = item.content
    setProposal(proposals.find((entry) => entry.status === 'PENDING') || null); setProposalPreview(null)
    setChapterStatuses(chapterData.chapters)
    const pending = readReportPending(project.id, id)
    if (pending && JSON.stringify(pending.content) !== JSON.stringify(item.content)) setRecovery(pending)
    else { setRecovery(null); if (pending) writeReportPending(project.id, id, null) }
    setRefreshPreview(null); setRefreshSelected([])
    setDirty(false); dirtyRef.current = false; setSaveState('已保存'); setEditorKey((key) => key + 1)
  }, [base, project.id])

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
  useEffect(() => { setCompareIds([]); setRunComparison(null) }, [project.id])
  useEffect(() => {
    setEdits(readPending(project.id, scenarioId))
    if (scenarioId) window.sessionStorage.setItem(`report-platform-scenario-selected:${project.id}`, scenarioId)
  }, [project.id, scenarioId])
  useEffect(() => { if (reportId) void loadReport(reportId).catch((cause: Error) => { setReportId(null); setError(cause.message) }); else { setReport(null); setChapterStatuses([]) } }, [reportId, loadReport])
  useEffect(() => { void loadRuns(scenarioId).catch((cause: Error) => setError(cause.message)) }, [scenarioId, loadRuns])
  useEffect(() => {
    if (!reportId) { setActiveTask(null); return }
    let active = true
    void api<WorkTask[]>(`${base}/tasks?report_id=${reportId}`).then((items) => {
      if (active) setActiveTask(items.find((item) => item.status !== 'CANCELLED') || null)
    }).catch((cause: Error) => { if (active) setError(cause.message) })
    return () => { active = false }
  }, [base, reportId])
  useEffect(() => {
    if (!activeTask || !['PENDING', 'RUNNING'].includes(activeTask.status)) return
    let active = true
    const timer = window.setInterval(() => {
      void api<WorkTask>(`${base}/tasks/${activeTask.id}`).then((next) => {
        if (!active) return
        setActiveTask(next)
        if (next.status === 'COMPLETED' && next.kind === 'model_draft' && next.result.candidate_id && reportId === next.report_id) {
          void api<Preview>(`${analysis}/reports/${reportId}/draft/candidates/${next.result.candidate_id}`).then((draft) => {
            if (active) { setCandidate(draft); setCandidateSelected(draft.content.filter((block) => block.type !== 'h2').map((block) => block.id || '')) }
          }).catch((cause: Error) => { if (active) setError(cause.message) })
        }
        if (next.status === 'FAILED' && next.error) setError(next.error)
      }).catch((cause: Error) => { if (active) setError(cause.message) })
    }, 1000)
    return () => { active = false; window.clearInterval(timer) }
  }, [activeTask?.id, activeTask?.status, analysis, base, reportId])
  useEffect(() => {
    if (compareIds.length < 2) { setRunComparison(null); return }
    setRunComparison(null)
    let active = true
    void post<RunComparison>(`${analysis}/runs/compare`, compareIds).then((value) => {
      if (active) setRunComparison(value)
    }).catch((cause: Error) => { if (active) { setRunComparison(null); setError(cause.message) } })
    return () => { active = false }
  }, [analysis, compareIds])
  useEffect(() => {
    if (panel !== 'draft' || !narrativeSection) return
    let active = true
    void api<{ items: ProjectEvidenceItem[] }>(`${base}/evidence`).then((data) => {
      if (active) setProjectEvidence(data.items)
    }).catch((cause: Error) => { if (active) setError(cause.message) })
    return () => { active = false }
  }, [base, panel, narrativeSection])
  useEffect(() => {
    if (panel !== 'draft' || !report || !effectiveSection) return
    let active = true
    void api<{ candidate_id: string; section_id: string; mode: string; status: string }[]>(
      `${analysis}/reports/${report.id}/draft/candidates?section_id=${encodeURIComponent(effectiveSection)}`)
      .then((rows) => { if (active) setPendingCandidates(rows) })
      .catch((cause: Error) => { if (active) setError(cause.message) })
    return () => { active = false }
  }, [analysis, panel, report?.id, report?.version, effectiveSection])
  useEffect(() => {
    if (panel !== 'materials' || !report || !effectiveSection) return
    let active = true
    setChapterPack(null)
    void api<ChapterPack>(`${base}/reports/${report.id}/materials?section_id=${encodeURIComponent(effectiveSection)}`)
      .then((value) => { if (active) setChapterPack(value) })
      .catch((cause: Error) => { if (active) setError(cause.message) })
    return () => { active = false }
  }, [base, panel, report?.id, report?.version, effectiveSection])
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
    setReportId(id); setPanel(null); setCandidate(null); setMaterialDetail(null); setChapterPack(null); setRefreshPreview(null); setRefreshSelected([]); setError('')
    setSelectedHeadingId(null); setSelectedPosition(0); setChapterDialog(null)
    setExports([]); setExportsOpen(false)
    setRecovery(null)
    if (!id) void loadLists().catch((cause: Error) => setError(cause.message))
  }

  const createReport = async () => {
    if (!newTitle.trim() || busy) return
    setBusy(true); setError('')
    try {
      const item = await post<Report>(`${base}/reports`, { title: newTitle.trim(), report_type: newReportType })
      await loadLists(); setNewTitle(''); setCreating(false); openReport(item.id)
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }

  const createScenario = async (source: 'historical' | 'project' | 'copy' | 'config' | 'config_facts', copyInputs = false) => {
    if (Object.keys(edits).length) { setError('请先保存或放弃当前输入修改'); return }
    setBusy(true); setError('')
    try {
      const created = await post<Scenario>(`${analysis}/scenarios`, {
        name: scenarioName.trim() || (source === 'copy' ? `${scenario?.name || '方案'} · 副本` : '基准方案'),
        source: source === 'config_facts' ? 'config' : source,
        ...(source === 'copy' || source === 'config' && copyInputs ? { copy_from: scenarioId } : {}),
        ...(source === 'config' || source === 'config_facts' ? { config_id: configId } : {}),
        ...(source === 'config_facts' ? { use_project_facts: true } : {}),
      })
      setScenarios((old) => [created, ...old]); setScenarioId(created.id)
      setScenarioName(''); setScenarioCreateOpen(false); setEdits({}); setSimulation(null); setRuns([]); setRunId('')
      setPanel('inputs'); notify(source === 'config_facts'
        ? `方案已创建，填入 ${Object.values(created.inputs).filter((row) => row.origin === 'project_fact').length} 项项目事实`
        : '方案已创建')
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }

  const openScenarioCreate = () => {
    setScenarioName('')
    setScenarioCreateSource(configs.some((item) => item.status === 'PUBLISHED') ? 'config_facts' : scenario ? 'copy' : project.has_corpus ? 'historical' : 'project')
    setScenarioCreateOpen(true)
    setError('')
  }

  const createSelectedScenario = () => {
    if (scenarioCreateSource === 'config_copy') void createScenario('config', true)
    else void createScenario(scenarioCreateSource)
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
    setScenarioCreateOpen(false)
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
      writeReportPending(project.id, reportId, changedSince
        ? { baseVersion: response.report.version, content: contentRef.current, savedAt: Date.now() } : null)
    } catch (cause) { setSaveState('保存失败'); setError((cause as Error).message) }
    finally { saving.current = false }
  }, [base, reportId, report, project.id])

  const closePanel = useCallback(() => {
    setScenarioCreateOpen(false)
    setPanel(null)
    window.requestAnimationFrame(() => editorActions.current?.focus())
  }, [])

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
      if (event.key === 'Escape' && !event.defaultPrevented) {
        if (chapterDialog) setChapterDialog(null)
        else if (panel) closePanel()
        setMoreOpen(false)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [persistBody, panel, chapterDialog, closePanel])

  const selectRun = async (id: string) => {
    if (!report || dirtyRef.current) { setError('请先保存正文'); return }
    setBusy(true); setError('')
    try {
      const result = await api<{ proposed_run_id: string; unchanged_references: number; impacts: { position: number; result_key: string; before: string; after: string | null }[] }>(
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
      setImpact(null); setRunId(active); await loadReport(report.id); setPanel(impact.impacts.length ? 'check' : null)
      notify(impact.impacts.length ? '报告已采用该次推演；受影响段落待更新' : '报告已采用该次推演')
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

  const reviewSelectedSource = async () => {
    if (!report || !selectedBlock?.id || dirtyRef.current) { setError('请先保存正文'); return }
    setBusy(true); setError('')
    try {
      await post(`/projects/${project.id}/reports/${report.id}/blocks/${selectedBlock.id}/source-review`,
        { base_version: report.version })
      await loadReport(report.id)
      notify('本段来源复核已记录；报告仍需整体核对')
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }

  const chooseRefresh = (action: RefreshAction, checked: boolean) => {
    setRefreshSelected((old) => {
      if (!checked) return old.filter((id) => id !== action.id)
      const conflicting = refreshPreview?.actions.filter((item) => item.block_id === action.block_id &&
        !(item.kind === 'numbers' && action.kind === 'condition' ||
          item.kind === 'condition' && action.kind === 'numbers')).map((item) => item.id) || []
      return [...old.filter((id) => !conflicting.includes(id)), action.id]
    })
  }

  const createFactProposal = async () => {
    const factKey = proposalFactKey || (selectedBlock?.fact_keys || []).find((key) => facts.some((fact) => fact.key === key && ['integer', 'decimal'].includes(fact.data_type)))
    if (!report || !selectedBlock?.id || !factKey || !proposalValue.trim() || dirty) return
    setBusy(true); setError('')
    try {
      const created = await post<FactProposal>(`${base}/reports/${report.id}/fact-proposals`, {
        block_id: selectedBlock.id, fact_key: factKey, proposed_value: proposalValue.trim(),
        source: proposalSource.trim(), reason: proposalReason.trim(),
      })
      setProposal(created); setProposalPreview(null); notify('修订提案已保存，项目事实尚未改变')
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }

  const previewFactProposal = async () => {
    if (!report || !proposal) return
    setBusy(true); setError('')
    try { setProposalPreview(await post<FactProposalPreview>(
      `${base}/reports/${report.id}/fact-proposals/${proposal.id}/preview`, {})) }
    catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }

  const applyFactProposal = async () => {
    if (!report || !proposal || !proposalPreview) return
    setBusy(true); setError('')
    try {
      await post(`${base}/changes/commit`, { fact_key: proposal.fact_key, value: proposal.proposed_value,
        source: proposal.source, reason: proposal.reason, base_version: proposalPreview.base_version,
        preview_token: proposalPreview.preview_token, proposal_id: proposal.id })
      await Promise.all([loadLists(), loadReport(report.id)])
      setPanel('check'); notify('项目事实已修订；正文引用需要重新核对')
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }

  const rejectFactProposal = async () => {
    if (!report || !proposal) return
    setBusy(true); setError('')
    try {
      await post(`${base}/reports/${report.id}/fact-proposals/${proposal.id}/reject`, {})
      setProposal(null); setProposalPreview(null); notify('提案已拒用，项目事实未改变')
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }

  const selectPosition = (position: number) => {
    setSelectedPosition(position)
    setProposalPreview(null); setProposalFactKey(''); setProposalValue(''); setProposalSource('')
    const heading = contentRef.current.slice(0, position).reverse().find((block) => block.type === 'h2')
    setSelectedHeadingId(heading?.id || null)
    if (heading) setSelectedSection(heading.section_id || '')
  }

  const locateBlock = (position: number, blockId?: string) => {
    selectPosition(position)
    const targetId = blockId || contentRef.current[position - 1]?.id
    const block = Array.from(document.querySelectorAll<HTMLElement>('.plate-content [data-block-id]'))
      .find((element) => element.dataset.blockId === targetId)
    block?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    if (targetId) editorActions.current?.focusBlock(targetId)
  }

  const changeChapter = async (action: 'add' | 'rename' | 'move', headingId?: string, title?: string, direction?: 'up' | 'down') => {
    if (!report || busy) return
    if (dirtyRef.current || saving.current) { setError('请先保存正文'); return }
    setBusy(true); setError('')
    try {
      const changed = await post<{ report: Report; heading_id: string }>(`${base}/reports/${report.id}/sections/change`, {
        action, base_version: report.version, ...(headingId ? { heading_id: headingId } : {}),
        ...(title !== undefined ? { title: title.trim() } : {}), ...(direction ? { direction } : {}),
      })
      await loadReport(report.id)
      setChapterDialog(null); setCandidate(null)
      const index = changed.report.content.findIndex((block) => block.id === changed.heading_id)
      if (index >= 0) {
        selectPosition(index + 1)
        window.requestAnimationFrame(() => {
          document.querySelector<HTMLElement>(`.plate-content [data-block-id="${changed.heading_id}"]`)?.scrollIntoView({ block: 'center' })
          editorActions.current?.focusBlock(changed.heading_id)
        })
      }
      notify(action === 'add' ? '章节已添加' : action === 'rename' ? '章节已重命名' : '章节顺序已调整')
    } catch (cause) {
      setError((cause as Error).message)
      if ((cause as Error).message.includes('版本')) await loadReport(report.id)
    } finally { setBusy(false) }
  }

  const previewDraft = async () => {
    if (!report || !draftReady || !effectiveSection || dirtyRef.current) return
    setBusy(true); setError(''); setEvidencePreview(null)
    try {
      if (candidate?.candidate_id) {
        await post(`${analysis}/reports/${report.id}/draft/candidates/${candidate.candidate_id}/decline`, {})
        setPendingCandidates((old) => old.filter((row) => row.candidate_id !== candidate.candidate_id))
      }
      setCandidate(null)
      const mode = narrativeSection && draftMode === 'computed' ? 'excerpt' : modelAvailable ? draftMode : 'computed'
      if (mode === 'model') {
        const task = await post<WorkTask>(`${base}/tasks`, {
          kind: 'model_draft', report_id: report.id, request_key: crypto.randomUUID(),
          section_id: effectiveSection,
          ...(narrativeSection ? { config_id: writingConfig?.id, evidence_ids: evidenceSelection }
            : { run_id: report.analysis_run_id }),
        })
        setActiveTask(task)
        return
      }
      const draft = await post<Preview>(`${analysis}/reports/${report.id}/draft/preview`, {
        ...(narrativeSection ? { config_id: writingConfig?.id } : { run_id: report.analysis_run_id }),
        ...(narrativeSection ? { evidence_ids: evidenceSelection } : {}),
        section_id: effectiveSection, mode,
      })
      setCandidate(draft); setCandidateSelected(draft.content.filter((block) => block.type !== 'h2').map((block) => block.id || ''))
      setPendingCandidates((old) => [...old.filter((row) => row.candidate_id !== draft.candidate_id),
        ...(draft.candidate_id ? [{ candidate_id: draft.candidate_id, section_id: draft.section_id,
          mode: draft.mode, status: 'READY' }] : [])])
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }

  const declineDraft = async () => {
    if (!candidate || !report || busy) return
    setBusy(true); setError('')
    try {
      if (candidate.candidate_id) await post(`${analysis}/reports/${report.id}/draft/candidates/${candidate.candidate_id}/decline`, {})
      setCandidate(null); setEvidencePreview(null)
      if (activeTask?.kind === 'model_draft') setActiveTask(null)
      setPendingCandidates((old) => old.filter((row) => row.candidate_id !== candidate.candidate_id))
      notify('候选已取消')
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }

  const resumeDraft = async (id: string) => {
    if (!report || busy) return
    setBusy(true); setError('')
    try {
      const restored = await api<Preview>(`${analysis}/reports/${report.id}/draft/candidates/${id}`)
      setCandidate(restored)
      setCandidateSelected(restored.content.filter((block) => block.type !== 'h2').map((block) => block.id || ''))
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }

  const cancelTask = async () => {
    if (!activeTask) return
    try { setActiveTask(await post<WorkTask>(`${base}/tasks/${activeTask.id}/cancel`, {})) }
    catch (cause) { setError((cause as Error).message) }
  }

  const retryTask = async () => {
    if (!activeTask) return
    try { setActiveTask(await post<WorkTask>(`${base}/tasks/${activeTask.id}/retry`, {})) }
    catch (cause) { setError((cause as Error).message) }
  }

  const inspectEvidence = async (row: CandidateEvidence) => {
    const ref = row.source_refs[0]
    if (!row.document_id || !ref) return
    const page = Number(/^p(\d+)-/.exec(ref)?.[1] || 1)
    setError(''); setEvidencePreview(null)
    try {
      const detail = await api<{ segments: { ref: string; text: string }[] }>(
        `${base}/documents/${row.document_id}?page=${page}`)
      const matching = detail.segments.filter((segment) => row.source_refs.includes(segment.ref))
      if (!matching.length) throw new Error('原文位置已变化，请在项目事实中重新核对')
      setEvidencePreview({ key: row.key, documentId: row.document_id, ref, page,
        text: matching.map((segment) => segment.text).join('\n') })
    } catch (cause) { setError((cause as Error).message) }
  }

  const inspectFactSource = async (key: string) => {
    setError(''); setEvidencePreview(null)
    try {
      const detail = await api<{ kind: string; document_id?: string; source_ref?: string; page?: number; excerpt?: string }>(
        `${base}/facts/${encodeURIComponent(key)}/source`)
      if (detail.kind !== 'document' || !detail.document_id || !detail.source_ref || !detail.excerpt) {
        throw new Error('本项目原文位置尚未核对')
      }
      setEvidencePreview({ key, documentId: detail.document_id, ref: detail.source_ref,
        page: detail.page || 1, text: detail.excerpt })
    } catch (cause) { setError((cause as Error).message) }
  }

  const inspectProjectEvidence = async (id: string) => {
    setError(''); setEvidencePreview(null)
    try {
      const item = await api<ProjectEvidenceItem>(`${base}/evidence/${id}`)
      setEvidencePreview({ key: item.fact_key || item.id, documentId: item.document_id,
        ref: item.source_refs[0], page: item.page || 1, text: item.excerpt })
    } catch (cause) { setError((cause as Error).message) }
  }

  const saveMaterialMode = async () => {
    if (!report || !chapterPack || !materialDetail || busy) return
    setBusy(true); setError('')
    try {
      const next = await api<ChapterPack>(`${base}/reports/${report.id}/materials`, {
        method: 'PUT', body: JSON.stringify({ section_id: effectiveSection,
          base_revision: chapterPack.policy_revision,
          settings: chapterPack.categories.map((item) => ({ category_id: item.category_id,
            mode: item.category_id === materialDetail.category_id ? materialMode : item.mode })) }),
      })
      setChapterPack(next); setMaterialDetail(null); setCandidate(null)
      notify('资料策略已保存')
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
      setCandidate(null); setCandidateSelected([]); setEvidencePreview(null); await loadReport(report.id); await loadLists(); setPanel(null)
      if (activeTask?.kind === 'model_draft') setActiveTask(null)
      notify('章节已加入报告')
    } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }

  const review = async () => {
    if (!report || dirtyRef.current) return
    setBusy(true); setError('')
    try { const updated = await post<Report>(`${base}/reports/${report.id}/review`, {}); setReport(updated); await loadLists(); notify('当前版本已核对') }
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

  const toggleExports = async () => {
    if (exportsOpen) { setExportsOpen(false); return }
    if (!report) return
    try { setExports(await api<ExportEntry[]>(`${base}/reports/${report.id}/exports`)); setExportsOpen(true) }
    catch (cause) { setError((cause as Error).message) }
  }

  const downloadExport = async (level?: 'preview' | 'scenario', exportId?: string) => {
    if (!report || busy || (!exportId && dirtyRef.current)) return
    setBusy(true); setError('')
    try {
      if (!exportId) {
        const task = await post<WorkTask>(`${base}/tasks`, {
          kind: 'export', report_id: report.id, request_key: crypto.randomUUID(), level,
        })
        setActiveTask(task)
        return
      }
      const url = `${base}/reports/${report.id}/exports/${exportId}`
      const response = await fetch(`/api${url}`)
      if (!response.ok) {
        const detail = await response.json().catch(() => ({})) as { detail?: string }
        throw new Error(detail.detail || `下载失败（${response.status}）`)
      }
      const filename = response.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)?.[1] || 'report.zip'
      const objectUrl = URL.createObjectURL(await response.blob())
      const anchor = document.createElement('a')
      anchor.href = objectUrl; anchor.download = filename
      document.body.append(anchor); anchor.click(); anchor.remove()
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 10_000)
      const refreshed = await api<ExportEntry[]>(`${base}/reports/${report.id}/exports`).catch(() => null)
      if (refreshed) setExports(refreshed)
      if (activeTask?.kind === 'export') setActiveTask(null)
    } catch (cause) { setError((cause as Error).message) }
    finally { setBusy(false) }
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

  const changeBody = (next: Block[]) => {
    if (!report || recovery) return
    contentRef.current = next; setContent(next)
    const changed = JSON.stringify(next) !== JSON.stringify(report.content)
    dirtyRef.current = changed; setDirty(changed); setSaveState(changed ? '等待保存' : '已保存')
    if (!writeReportPending(project.id, report.id, changed
      ? { baseVersion: report.version, content: next, savedAt: Date.now() } : null)) {
      setError('本地恢复副本保存失败，请立即保存正文')
    }
  }

  const restoreBody = () => {
    if (!recovery || !report) return
    contentRef.current = recovery.content; setContent(recovery.content)
    dirtyRef.current = true; setDirty(true); setSaveState('等待保存')
    setRecovery(null); setEditorKey((key) => key + 1)
  }

  const discardRecovery = () => {
    if (report) writeReportPending(project.id, report.id, null)
    setRecovery(null)
  }

  const copyRecovery = async () => {
    if (!recovery) return
    try { await navigator.clipboard.writeText(recovery.content.map(textOf).join('\n')); notify('本地文字已复制') }
    catch { setError('复制失败，请选择恢复本地稿后再复制') }
  }

  const selectedBlock = selectedPosition > 0 ? content[selectedPosition - 1] : null
  const taskControls = activeTask && <div className="scenario-task-status" role="status"><span>{activeTask.kind === 'model_draft' ? '模型起草' : '导出'} · {({ PENDING: '排队中', RUNNING: '处理中', COMPLETED: '已完成', FAILED: '失败', UNCERTAIN: '待重试', CANCELLED: '已取消' } as Record<string, string>)[activeTask.status] || activeTask.status}</span>
    {activeTask.error && <small>{activeTask.error}</small>}
    <div>{['PENDING', 'RUNNING'].includes(activeTask.status) && <button onClick={() => void cancelTask()}>取消任务</button>}
      {['FAILED', 'UNCERTAIN', 'CANCELLED'].includes(activeTask.status) && <button onClick={() => void retryTask()}>重试</button>}
      {activeTask.status === 'COMPLETED' && activeTask.kind === 'model_draft' && activeTask.result.candidate_id && <button onClick={() => void resumeDraft(activeTask.result.candidate_id!)}>查看候选</button>}
      {activeTask.status === 'COMPLETED' && activeTask.kind === 'export' && activeTask.result.export_id && <button onClick={() => void downloadExport(undefined, activeTask.result.export_id)}>下载文件</button>}</div></div>
  const inputFields = scenario?.definitions.filter((field) => !field.computed) || []
  const inputGroups = [...new Set(inputFields.map((field) => field.group || 'project'))]
  const changeCount = Object.keys(edits).length
  const reportHeadings = report?.content.map((block, index) => ({ block, index })).filter(({ block }) => block.type === 'h2') || []
  const visibleIssues = report?.issues.filter((issue, index, all) =>
    all.findIndex((other) => other.code === issue.code && other.position === issue.position && other.message === issue.message) === index) || []
  const blocking = visibleIssues.filter((issue) => issue.severity === 'block')
  const hasRefreshIssues = report?.issues.some((issue) => ['ANALYSIS_RUN_STALE', 'ANALYSIS_VALUE_STALE', 'ANALYSIS_CONDITION_STALE'].includes(issue.code)) || false
  const scenarioRuns = runs.filter((row) => row.scenario_id === scenarioId)
  const resultList = currentRun ? Object.values(currentRun.snapshot.results).filter((row) => scenario?.definitions.some((field) => field.key === row.key && field.computed) || row.origin === 'calculated')
    : []
  const comparisonRuns = compareIds.map((id) => runs.find((row) => row.id === id)).filter((row): row is Run => !!row)
  const comparisonKeys = [...new Set(comparisonRuns.flatMap((row) => Object.values(row.snapshot.results).filter((result) => row.snapshot.definitions.some((field) => field.key === result.key && field.computed) || result.origin === 'calculated').map((result) => result.key)))].filter((key) => runComparison?.rows.find((row) => row.key === key)?.comparable)
  const chapterEvidence = projectEvidence.filter((item) => selectedConfigSection?.evidence_keys?.includes(item.fact_key || '') &&
    facts.some((fact) => fact.key === item.fact_key && fact.revision === item.fact_revision))

  if (configOpen) return <AnalysisConfigView projectId={project.id} scenarios={scenarios}
    onClose={closeConfig} onPublished={() => { void loadLists().catch((cause: Error) => setError(cause.message)) }} />

  return <main className={`page scenario-page ${report ? 'scenario-editor-page' : ''}`}>
    <div className="breadcrumb">项目 / {project.name} / {report ? '报告写作' : '报告'}</div>
    {error && <div className="notice error" role="alert"><span>{error}</span><button type="button" onClick={() => setError('')} aria-label="关闭错误"><X size={14} /></button></div>}
    {!report ? <>
      <div className="page-header"><h1>报告</h1><div className="scenario-inline"><button onClick={openConfig}>规则与章节配置</button><button onClick={onLegacy}>{project.has_corpus ? '历史文章整理' : '资料起草'}</button><button className="primary-button" onClick={() => setCreating(true)}><Plus size={15} /> 新建报告</button></div></div>
      {reports.length > 0 && <div className="scenario-list-tools"><input aria-label="搜索报告" value={reportQuery} onChange={(event) => setReportQuery(event.target.value)} placeholder="搜索报告标题" /><select aria-label="筛选报告状态" value={reportFilter} onChange={(event) => setReportFilter(event.target.value)}><option value="all">全部状态</option><option value="工作稿">工作稿</option><option value="待核对">待核对</option><option value="已核对">已核对</option></select></div>}
      <div className="scenario-list-card">{reports.length ? reports.filter((item) => item.title.toLocaleLowerCase().includes(reportQuery.trim().toLocaleLowerCase()) && (reportFilter === 'all' || item.status === reportFilter)).map((item) => <button key={item.id} className="scenario-report-row" onClick={() => openReport(item.id)}><BookOpen size={18} /><span><strong>{item.title}</strong><small>{timeLabel(item.updated_at)} · {item.status}{item.has_scenario_assumption ? ' · 方案假设' : ''}</small></span><em>v{item.version}</em></button>) : <div className="empty">暂无报告，点击右上角新建</div>}{reports.length > 0 && !reports.some((item) => item.title.toLocaleLowerCase().includes(reportQuery.trim().toLocaleLowerCase()) && (reportFilter === 'all' || item.status === reportFilter)) && <div className="empty">没有匹配报告</div>}</div>
      {creating && <div className="dialog-backdrop" onClick={() => setCreating(false)}><div className="dialog" role="dialog" aria-modal="true" aria-label="新建报告" onClick={(event) => event.stopPropagation()}><div className="dialog-head"><h2>新建报告</h2><button className="icon-button" onClick={() => setCreating(false)} aria-label="关闭"><X size={18} /></button></div><label className="form-field"><span>报告标题</span><input autoFocus value={newTitle} onChange={(event) => setNewTitle(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void createReport() }} /></label><label className="form-field"><span>报告类型</span><select aria-label="报告类型" value={newReportType} onChange={(event) => setNewReportType(event.target.value)}>{reportTypes.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label><div className="form-actions"><button onClick={() => setCreating(false)}>取消</button><button className="primary-button" disabled={!newTitle.trim() || busy} onClick={() => void createReport()}>创建</button></div></div></div>}
    </> : <>
      <div className="scenario-topbar"><button className="scenario-back" onClick={() => openReport(null)} aria-label="返回报告列表"><ArrowLeft size={17} /></button><div className="scenario-title"><h1>{report.title}</h1><small>v{report.version} · {saveState}{report.reviewed ? ' · 已核对' : ''}{report.issues.some((issue) => issue.code === 'CHAPTER_SCENARIO_ASSUMPTION') ? ' · 方案假设' : ''}</small></div><div className="scenario-run-badge" title={activeRun ? '当前报告采用的方案运行' : '计算章节需选择推演运行'}>{activeRun ? `${scenarios.find((item) => item.id === activeRun.scenario_id)?.name || '方案'} · 运行 ${activeRun.scenario_revision}` : '文字章节可直接起草'}</div><div className="scenario-top-actions"><button onClick={() => setPanel(panel === 'inputs' ? null : 'inputs')}>输入数据</button><button onClick={() => setPanel(panel === 'results' ? null : 'results')}>推演结果</button><button className="primary-button" disabled={!draftReady || dirty || busy} onClick={() => setPanel('draft')}><Sparkles size={14} /> 生成本章</button><button className="scenario-more" aria-label="更多操作" aria-expanded={moreOpen} onClick={() => setMoreOpen((old) => !old)}><MoreHorizontal size={18} /></button>{moreOpen && <div className="scenario-more-menu"><button onClick={() => { setPanel('sources'); setMoreOpen(false) }}>资料与来源</button><button onClick={() => { setPanel('materials'); setMoreOpen(false) }}>项目资料</button><button onClick={() => { setPanel('check'); setMoreOpen(false) }}>检查与导出</button><button onClick={() => { openConfig(); setMoreOpen(false) }}>规则与章节配置</button><button onClick={() => { void openVersions(); setMoreOpen(false) }}>版本</button><button onClick={() => { setMoreOpen(false); onLegacy() }}>{project.has_corpus ? "历史文章整理" : "资料起草"}</button></div>}</div></div>
      {activeTask && ['PENDING', 'RUNNING', 'FAILED', 'UNCERTAIN', 'COMPLETED'].includes(activeTask.status) && <button className="scenario-task-shortcut" onClick={() => setPanel(activeTask.kind === 'model_draft' ? 'draft' : 'check')}>{activeTask.kind === 'model_draft' ? '模型起草' : '导出'} · {activeTask.status === 'COMPLETED' ? '已完成' : activeTask.status === 'RUNNING' ? '处理中' : activeTask.status === 'PENDING' ? '排队中' : '待处理'}</button>}
      <div className={`scenario-workspace ${panel ? 'with-panel' : ''}`}><aside className="scenario-outline"><div className="scenario-outline-header">章节</div>{reportHeadings.map(({ block, index }, chapterIndex) => { const state = chapterStatuses.find((item) => item.heading_id === block.id); return <div key={block.id || index} className={`scenario-outline-row ${selectedHeadingId === block.id ? 'active' : ''}`}><button className="scenario-outline-title" onClick={() => locateBlock(index + 1, block.id)} title={state?.missing_fact_keys.length ? `缺少：${state.missing_fact_keys.join('、')}` : textOf(block)}>{textOf(block) || '未命名章节'}</button>{state && <small className="scenario-chapter-status">{state.status}</small>}<div className="scenario-outline-controls"><button aria-label={`重命名章节 ${textOf(block)}`} title="重命名" onClick={() => setChapterDialog({ action: 'rename', headingId: block.id, title: textOf(block) })}><Pencil size={12} /></button><button aria-label={`上移章节 ${textOf(block)}`} title="上移" disabled={chapterIndex === 0 || busy} onClick={() => void changeChapter('move', block.id, undefined, 'up')}><ArrowUp size={12} /></button><button aria-label={`下移章节 ${textOf(block)}`} title="下移" disabled={chapterIndex === reportHeadings.length - 1 || busy} onClick={() => void changeChapter('move', block.id, undefined, 'down')}><ArrowDown size={12} /></button></div></div> })}<button className="scenario-outline-add" onClick={() => setChapterDialog({ action: 'add', title: '' })}><Plus size={13} /> 添加章节</button></aside>
        <section className="scenario-canvas"><div className="scenario-canvas-tools"><span>{selectedBlock?.section_id ? availableSections.find((item) => item.id === selectedBlock.section_id)?.label || '报告正文' : '报告正文'}</span><button onClick={() => void persistBody()} disabled={!dirty || saveState === '保存中…'}><Save size={13} /> 保存</button><button onClick={() => setPanel('sources')} disabled={!selectedBlock}>查看依据</button><button onClick={() => setPanel('check')}>检查 {blocking.length > 0 ? `· ${blocking.length}` : ''}</button></div><div className="scenario-paper"><EditorPane key={`${report.id}-${editorKey}`} initial={content} facts={facts.filter((item) => item.value != null)} actionsRef={editorActions} staleFactKeys={[]} onOpenFact={() => { setPanel('sources') }} onSelectPosition={selectPosition} onChange={changeBody} /></div></section>
        {panel && <aside className="scenario-panel" role="complementary"><div className="scenario-panel-head"><h2>{({ inputs: '输入数据', results: '推演结果', draft: '生成本章', sources: '资料与来源', check: '检查与导出', versions: '版本', materials: '资料应用' })[panel]}</h2><button onClick={closePanel} aria-label="关闭面板"><X size={18} /></button></div><div className="scenario-panel-body">
          {panel === 'inputs' && <>
            <div className="scenario-panel-line"><select aria-label="选择方案" value={scenarioId} onChange={(event) => switchScenario(event.target.value)}><option value="">选择方案</option>{scenarios.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select><button type="button" onClick={openScenarioCreate}><Plus size={14} /> 新建</button></div>
            {scenarioCreateOpen && <div className="scenario-create-card">
              <label className="scenario-field">方案名称<input aria-label="方案名称" value={scenarioName} onChange={(event) => setScenarioName(event.target.value)} placeholder={scenarioCreateSource === 'copy' ? `${scenario?.name || '方案'} · 副本` : '基准方案'} /></label>
              <label className="scenario-field">输入来源<select aria-label="方案输入来源" value={scenarioCreateSource} onChange={(event) => setScenarioCreateSource(event.target.value as ScenarioCreateSource)}>
                <option value="project">本项目事实</option>{scenario && <option value="copy">复制当前方案</option>}{project.has_corpus && <option value="historical">澄岳历史输入副本</option>}
                {configs.some((item) => item.status === 'PUBLISHED') && <><option value="config_facts">已发布配置 · 填入项目事实</option><option value="config">已发布配置 · 空输入</option>{scenario && <option value="config_copy">已发布配置 · 沿用当前输入</option>}</>}
              </select></label>
              {scenarioCreateSource.startsWith('config') && <label className="scenario-field">写作配置<select aria-label="选择写作配置" value={configId} onChange={(event) => setConfigId(event.target.value)}>{configs.filter((item) => item.status === 'PUBLISHED').map((item) => <option key={item.id} value={item.id}>v{item.version} · {item.name}</option>)}</select></label>}
              <div className="scenario-panel-actions"><button type="button" onClick={() => setScenarioCreateOpen(false)}>取消</button><button type="button" className="primary-button" disabled={busy || scenarioCreateSource.startsWith('config') && !configId} onClick={createSelectedScenario}>{busy ? '创建中…' : '创建方案'}</button></div>
            </div>}
            {scenario && <>{inputFields.length ? inputGroups.map((group) => { const fields = inputFields.filter((field) => (field.group || 'project') === group); return <section className="scenario-input-group" key={group}><h3>{groups[group] || group}</h3>{fields.map((field) => { const entry = scenario.inputs[field.key]; const value = Object.prototype.hasOwnProperty.call(edits, field.key) ? edits[field.key] : entry?.value; const pending = Object.prototype.hasOwnProperty.call(edits, field.key) && value !== entry?.value; const origin = pending ? (value == null ? "missing" : "scenario_assumption") : entry?.origin || "missing"; return <label key={field.key} className="scenario-input"><span>{field.label}<small>{sourceLabel(origin)}{percentageInput(field, scenario) ? ' · %' : field.unit ? ` · ${field.unit}` : ''}</small></span><input aria-label={field.label} value={inputDisplay(field, scenario, value)} type={field.data_type === 'text' ? 'text' : 'number'} step={field.data_type === 'integer' ? '1' : 'any'} onChange={(event) => changeInput(field, event.target.value)} /></label> })}</section> }) : <div className="empty">本项目尚无输入字段。请先录入项目事实与规则。<button onClick={onOpenFacts}>打开项目事实</button></div>}
              <div className="scenario-panel-actions"><button onClick={discardInputs} disabled={!changeCount}>放弃修改</button><button onClick={() => void persistScenario()} disabled={!changeCount || busy}>保存方案</button><button className="primary-button" onClick={() => void previewInputs()} disabled={busy || !inputFields.length}><Play size={14} /> 预览推演</button></div>
              {simulation && <div className="scenario-preview"><h3>预览结果</h3>{Object.values(simulation.results).filter((result) => scenario.definitions.some((field) => field.key === result.key && field.computed) || result.origin === 'calculated').map((result) => <div key={result.key}><span>{result.label}</span><strong>{shown(result.value, result.unit)}</strong></div>)}<small>{simulation.condition}</small><div className="scenario-panel-actions"><button onClick={() => setSimulation(null)}>取消预览</button><button className="primary-button" onClick={() => void persistRun()} disabled={busy}>保存方案并运行</button></div></div>}
            </>}
          </>}
          {panel === 'results' && <><div className="scenario-panel-line"><select aria-label="查看方案" value={scenarioId} onChange={(event) => switchScenario(event.target.value)}>{scenarios.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select><button onClick={() => setPanel('inputs')}>修改输入</button></div>{scenarioRuns.length ? <><select aria-label="查看推演运行" value={runId} onChange={(event) => { setRunId(event.target.value); setSelectedResult('') }}>{scenarioRuns.map((item) => <option key={item.id} value={item.id}>第 {item.scenario_revision} 版 · {timeLabel(item.created_at)}</option>)}</select><div className="scenario-results-list">{resultList.map((result) => <button key={result.key} onClick={() => setSelectedResult(selectedResult === result.key ? '' : result.key)}><span>{result.label}</span><strong>{shown(result.value, result.unit)}</strong></button>)}</div><div className="scenario-condition">{currentRun?.snapshot.condition}</div>{currentRun?.snapshot.issues.map((issue) => <div key={issue.code} className="scenario-short-note">{issue.message}</div>)}{selectedResult && currentRun && <div className="scenario-result-detail"><h3>{currentRun.snapshot.results[selectedResult]?.label}</h3>{currentRun.snapshot.trace.filter((step) => step.target === selectedResult).map((step) => <div key={step.rule_id}><code>{step.expression}</code><p>{Object.entries(step.inputs).map(([key, value]) => `${currentRun.snapshot.results[key]?.label || key}=${value ?? '未定义'}`).join(' · ')}</p><small>{step.status === 'COMPUTED' ? `结果 ${step.result}` : `缺少 ${step.missing.join('、')}`}</small></div>)}</div>}<div className="scenario-panel-actions"><button onClick={() => setCompareIds((old) => old.includes(runId) ? old.filter((id) => id !== runId) : [...old, runId].slice(-3))}><GitCompareArrows size={14} /> {compareIds.includes(runId) ? '移出比较' : '加入比较'}</button><button className="primary-button" disabled={!currentRun || currentRun.status !== 'COMPUTED' || busy || dirty} onClick={() => void selectRun(runId)}><Check size={14} /> 用于报告</button></div>{comparisonRuns.length >= 2 && <div className="scenario-compare"><h3>方案对比</h3><div className="scenario-compare-header"><span>指标</span>{comparisonRuns.map((row) => <strong key={row.id}>{scenarios.find((item) => item.id === row.scenario_id)?.name || "方案"}</strong>)}</div>{comparisonKeys.map((key) => comparisonRuns.some((row) => row.snapshot.results[key]) ? <div key={key}><span>{comparisonRuns[0].snapshot.results[key]?.label || key}</span>{comparisonRuns.map((row) => <strong key={row.id}>{shown(row.snapshot.results[key]?.value, row.snapshot.results[key]?.unit)}</strong>)}</div> : null)}</div>}</> : <div className="empty">尚无推演结果。<button onClick={() => setPanel('inputs')}>打开输入数据</button></div>}{impact && <div className="scenario-confirm"><h3>采用这次推演</h3><p>{impact.impacts.length ? `将有 ${new Set(impact.impacts.map((row) => row.position)).size} 处正文位置待更新；正文不会自动覆盖。` : '报告将绑定这次推演。'}{impact.unchanged_references > 0 ? ` 另有 ${impact.unchanged_references} 处未变化引用直接沿用。` : ''}</p>{impact.impacts.filter((row, index, all) => row.before !== row.after && all.findIndex((other) => other.result_key === row.result_key && other.before === row.before && other.after === row.after) === index).map((row) => <div key={row.result_key}>{row.result_key} · {row.before} → {row.after ?? '不可评估'}</div>)}<div className="scenario-panel-actions"><button onClick={() => setImpact(null)}>取消</button><button className="primary-button" onClick={() => void confirmRun()} disabled={busy}>确认采用</button></div></div>}</>}
          {panel === 'draft' && <>
            {activeTask?.kind === 'model_draft' && taskControls}
            {!candidate && pendingCandidates.filter((row) => row.status === 'READY' && row.section_id === effectiveSection).map((row) => <button className="scenario-source-row" key={row.candidate_id} onClick={() => void resumeDraft(row.candidate_id)}>继续上次候选 · {row.mode === 'model' ? '模型起草' : row.mode === 'excerpt' ? '原文摘录' : '计算内容'}</button>)}
            <label className="scenario-field">章节<select aria-label="生成章节" value={effectiveSection} onChange={(event) => { const chosen = event.target.value; setSelectedSection(chosen); setSelectedHeadingId(report.content.find((block) => block.type === 'h2' && block.section_id === chosen)?.id || null); setCandidate(null); setEvidencePreview(null); setEvidenceSelection([]) }}>{!effectiveSection && <option value="">当前章节无生成配置</option>}{availableSections.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
            {activeRun && <div className="scenario-draft-run"><span>写作依据</span><strong>{scenarios.find((item) => item.id === activeRun.scenario_id)?.name || '当前方案'} · 运行 {activeRun.scenario_revision}</strong></div>}
            {narrativeSection && <details className="scenario-evidence-list"><summary>项目证据{evidenceSelection.length ? ` · 已选 ${evidenceSelection.length}` : ''}</summary>{chapterEvidence.length ? chapterEvidence.map((item) => <label key={item.id} className="scenario-evidence-choice"><input type="checkbox" aria-label={`选用证据 ${item.label} ${item.document_filename}`} checked={evidenceSelection.includes(item.id)} onChange={(event) => { setCandidate(null); setEvidenceSelection((old) => event.target.checked ? [...old, item.id] : old.filter((id) => id !== item.id)) }} /><span><strong>{item.label}</strong><small>{item.document_filename} · {item.source_refs.join('、')}{item.source_type === 'secondary' ? ' · 二手材料' : ''}</small></span><button type="button" onClick={() => void inspectProjectEvidence(item.id)}>查看</button></label>) : <div className="empty">本章暂无独立证据，可使用已核对的项目事实原文。</div>}</details>}
            {narrativeSection && evidencePreview && !candidate && <div className="scenario-evidence-preview"><div><strong>项目原文 · {evidencePreview.ref}</strong><button type="button" aria-label="关闭原文预览" onClick={() => setEvidencePreview(null)}><X size={14} /></button></div><p>{evidencePreview.text}</p><button type="button" onClick={() => onOpenDocument(evidencePreview.documentId, evidencePreview.page, evidencePreview.ref)}>在项目文件中定位</button></div>}
            {!!selectedConfigSection?.result_keys.length && activeRun && <details className="scenario-draft-basis"><summary>查看本章数据 · {selectedConfigSection.result_keys.length} 项</summary>{selectedConfigSection.result_keys.map((key) => { const row = activeRun.snapshot.results[key]; return row && <div key={key}><span>{row.label}</span><strong>{shown(row.value, row.unit)}</strong></div> })}</details>}
            <div className="scenario-segment"><button className={draftMode === 'computed' ? 'active' : ''} onClick={() => { setDraftMode('computed'); setCandidate(null) }}>{narrativeSection ? '原文摘录' : '确定性内容'}</button>{modelAvailable && <button className={draftMode === 'model' ? 'active' : ''} onClick={() => { setDraftMode('model'); setCandidate(null) }}>模型起草</button>}</div>
            <div className="scenario-panel-actions"><button className="primary-button" onClick={() => void previewDraft()} disabled={!draftReady || !effectiveSection || busy || dirty || (draftMode === 'model' && activeTask?.kind === 'model_draft' && ['PENDING', 'RUNNING'].includes(activeTask.status))}><Sparkles size={14} /> {busy ? '生成中…' : '生成候选'}</button></div>
            {candidate && <div className="scenario-candidate"><h3>待确认候选</h3>
              {candidate.model_audit.evidence?.length ? <div className={`scenario-short-note ${candidate.model_audit.evidence.every((row) => row.status === 'VERIFIED') ? 'scenario-evidence-ok' : ''}`}>原文位置 {candidate.model_audit.evidence.filter((row) => row.status === 'VERIFIED').length}/{candidate.model_audit.evidence.length}{candidate.model_audit.evidence.filter((row) => row.status !== 'VERIFIED').map((row) => <p key={row.key}>{row.label}：{row.reason}</p>)}</div> : null}
              {!!candidate.model_audit.evidence?.length && <details className="scenario-evidence-list"><summary>查看原文位置</summary>{candidate.model_audit.evidence.map((row) => <button type="button" key={row.key} disabled={!row.document_id || !row.source_refs.length} onClick={() => void inspectEvidence(row)}><span>{row.label}</span><small>{row.status === 'VERIFIED' ? row.source_refs.join('、') : row.reason || '待核对'}</small></button>)}</details>}
              {evidencePreview && <div className="scenario-evidence-preview"><div><strong>原文 · {evidencePreview.ref}</strong><button type="button" aria-label="关闭原文预览" onClick={() => setEvidencePreview(null)}><X size={14} /></button></div><p>{evidencePreview.text}</p><button type="button" onClick={() => onOpenDocument(evidencePreview.documentId, evidencePreview.page, evidencePreview.ref)}>在项目文件中定位</button></div>}
              {candidate.content.filter((block) => block.type !== 'h2').map((block, index) => { const keys = [...new Set(refsOf(block).map((ref) => ref.result_key))]; return <div className="scenario-candidate-choice" data-origin={block.origin || 'guided'} key={block.id || index}>
                <label><input type="checkbox" aria-label={`采用候选 ${index + 1}`} checked={candidateSelected.includes(block.id || '')} onChange={(event) => setCandidateSelected((old) => event.target.checked ? [...old, block.id || ''] : old.filter((id) => id !== block.id))} /><span>{block.origin === 'model' ? '模型候选' : block.type === 'table' ? '结果表' : narrativeSection ? '原文摘录' : '计算内容'}</span></label>
                {candidateBody(block)}
                {!!keys.length && <details className="scenario-candidate-basis"><summary>查看依据 · {keys.length} 项</summary>{keys.map((key) => { const row = activeRun?.snapshot.results[key]; return row && <button type="button" key={key} onClick={() => { setPanel('results'); setRunId(activeRun?.id || ''); setSelectedResult(key) }}><span>{row.label}</span><strong>{shown(row.value, row.unit)}</strong></button> })}{candidate.model_audit.evidence?.filter((row) => keys.includes(row.key)).map((row) => <small key={row.key}>{row.label} · {row.status === 'VERIFIED' ? '原文位置已核对' : row.reason || '待核对'}</small>)}</details>}
                {!keys.length && (block.project_evidence_refs?.length ? <details className="scenario-candidate-basis"><summary>查看项目证据 · {block.project_evidence_refs.length} 项</summary>{block.project_evidence_refs.map((ref) => <button type="button" key={ref.evidence_id} onClick={() => void inspectProjectEvidence(ref.evidence_id)}>{projectEvidence.find((item) => item.id === ref.evidence_id)?.label || '项目证据'}</button>)}</details> : block.fact_keys?.length ? <details className="scenario-candidate-basis"><summary>查看项目原文 · {block.fact_keys.length} 项</summary>{block.fact_keys.map((key) => <button type="button" key={key} onClick={() => void inspectFactSource(key)}>{facts.find((fact) => fact.key === key)?.label || key}</button>)}</details> : block.source_refs?.length ? <small>历史资料 · 待核对</small> : <small>待人工核对</small>)}
              </div> })}
              {report.content.some((block) => block.section_id === effectiveSection) && <details className="scenario-short-note"><summary>查看本章更新范围{candidate.preserved_blocks?.length ? ` · 保留 ${candidate.preserved_blocks.length} 段人工内容` : ''}</summary>{report.content.filter((block) => block.section_id === effectiveSection).map((block, index) => <p key={block.id || index}>{textOf(block)}</p>)}</details>}
              <div className="scenario-panel-actions"><button onClick={() => void declineDraft()} disabled={busy}>取消候选</button><button className="primary-button" onClick={() => void acceptDraft()} disabled={busy || !candidateSelected.length}>{report.content.some((block) => block.section_id === effectiveSection) ? '更新本章' : '加入报告'}</button></div>
            </div>}
          </>}
          {panel === 'sources' && <>{selectedBlock ? <><h3>第 {selectedPosition} 段依据</h3><p className="scenario-source-text">{textOf(selectedBlock)}</p>{selectedBlock.source_review_required && <div className="notice warn">正文已修改，需核对本段依据。<button type="button" disabled={dirty || busy} onClick={() => void reviewSelectedSource()}>核对本段来源</button></div>}{refsOf(selectedBlock).map((ref, index) => <button className="scenario-source-row" key={`${ref.result_key}-${index}`} onClick={() => { setPanel('results'); setRunId(ref.run_id); setSelectedResult(ref.result_key) }}>推演 · {ref.result_key} = {ref.value}{ref.unit} <ChevronDown size={13} /></button>)}{(selectedBlock.project_evidence_refs || []).map((ref) => <button className="scenario-source-row" key={ref.evidence_id} onClick={() => void inspectProjectEvidence(ref.evidence_id)}>项目证据 · {ref.evidence_id.slice(0, 8)}</button>)}{(selectedBlock.fact_keys || []).map((key) => <button className="scenario-source-row" key={key} onClick={() => void inspectFactSource(key)}>项目原文 · {facts.find((fact) => fact.key === key)?.label || key}</button>)}{(selectedBlock.source_refs || []).map((ref, index) => <button className="scenario-source-row" key={index} onClick={() => void source(ref)}>历史资料 · {ref.semantic_id || ref.record_id}</button>)}{activeRun && Object.values(activeRun.snapshot.results).filter((row) => row.value !== null && row.key !== 'demand_gap').slice(0, 12).map((row) => <button className="scenario-source-row" key={row.key} onClick={() => { if (row.value !== null) editorActions.current?.insertResult({ ...row, value: row.value }, activeRun.id) }}>插入 {row.label} · {row.value}{row.unit}</button>)}</> : <div className="empty">选中正文段落后查看依据和插入结果。</div>}{evidencePreview && <div className="scenario-evidence-preview"><div><strong>项目原文 · {evidencePreview.ref}</strong><button type="button" aria-label="关闭原文预览" onClick={() => setEvidencePreview(null)}><X size={14} /></button></div><p>{evidencePreview.text}</p><button type="button" onClick={() => onOpenDocument(evidencePreview.documentId, evidencePreview.page, evidencePreview.ref)}>在项目文件中定位</button></div>}{sourceDetail && <div className="scenario-source-detail"><h3>来源记录</h3><p>{sourceDetail.summary}</p>{sourceDetail.payload?.status === "source_asserted_not_independently_verified" && <small>来源陈述，尚未独立核实</small>}{sourceDetail.payload?.limitations?.map((item) => <small key={item}>{item}</small>)}</div>}</>}
          {panel === 'sources' && selectedBlock?.id && !project.has_corpus && (selectedBlock.fact_keys || []).some((key) => facts.some((fact) => fact.key === key && ['integer', 'decimal'].includes(fact.data_type))) &&
            <details className="scenario-create"><summary>提出项目事实修订</summary>
              {proposal?.block_id === selectedBlock.id ? <>
                <div className="scenario-short-note">{facts.find((fact) => fact.key === proposal.fact_key)?.label || proposal.fact_key}：{facts.find((fact) => fact.key === proposal.fact_key)?.value ?? '未定义'} → {proposal.proposed_value}</div>
                {!proposalPreview ? <div className="scenario-panel-actions"><button disabled={busy} onClick={() => void rejectFactProposal()}>拒用</button><button className="primary-button" disabled={busy || dirty} onClick={() => void previewFactProposal()}>预览影响</button></div> : <>
                  <div className="scenario-short-note">影响事实 {proposalPreview.changes.length} 项、正文位置 {proposalPreview.report_impacts.length} 处</div>
                  {proposalPreview.changes.map((item) => <div className="scenario-short-note" key={item.key}>{item.key} · {item.before.value ?? '未定义'} → {item.after.value ?? '未定义'}</div>)}
                  <div className="scenario-panel-actions"><button onClick={() => setProposalPreview(null)}>取消预览</button><button className="primary-button" disabled={busy} onClick={() => void applyFactProposal()}>确认修改事实</button></div>
                </>}
              </> : <>
                <select aria-label="提案事实" value={proposalFactKey || (selectedBlock.fact_keys || []).find((key) => facts.some((fact) => fact.key === key && ['integer', 'decimal'].includes(fact.data_type))) || ''} onChange={(event) => setProposalFactKey(event.target.value)}>
                  {(selectedBlock.fact_keys || []).filter((key) => facts.some((fact) => fact.key === key && ['integer', 'decimal'].includes(fact.data_type))).map((key) => <option key={key} value={key}>{facts.find((fact) => fact.key === key)?.label || key}</option>)}
                </select>
                <input aria-label="提议的新数值" type="number" value={proposalValue} onChange={(event) => setProposalValue(event.target.value)} placeholder="新数值" />
                <input aria-label="新数值来源" value={proposalSource} onChange={(event) => setProposalSource(event.target.value)} placeholder="来源，可留空" />
                <input aria-label="修订理由" value={proposalReason} onChange={(event) => setProposalReason(event.target.value)} placeholder="修订理由" />
                <div className="scenario-panel-actions"><button className="primary-button" disabled={busy || dirty || !proposalValue.trim() || !proposalReason.trim()} onClick={() => void createFactProposal()}>保存提案</button></div>
              </>}
            </details>}
          {panel === 'results' && scenario && <SensitivityPanel key={scenario.id} projectId={project.id} scenario={scenario}
            onRuns={() => loadRuns(scenario.id)} onSelect={(id) => { setRunId(id); setSelectedResult('') }} />}
          {panel === 'results' && runComparison?.rows.some((row) => !row.comparable) &&
            <details className="scenario-short-note"><summary>不可比指标 {runComparison.rows.filter((row) => !row.comparable).length} 项</summary>
              {runComparison.rows.filter((row) => !row.comparable).map((row) => <p key={row.key}>{row.key} · {row.reason}</p>)}</details>}
          {panel === 'check' && (hasRefreshIssues || refreshPreview) && <section className="scenario-refresh">
            <div className="scenario-material-row"><strong>推演变化</strong>{!refreshPreview && <button onClick={() => void previewRefresh()} disabled={busy || dirty}>处理变化</button>}</div>
            {refreshPreview && <>
              {refreshPreview.actions.length ? refreshPreview.actions.map((action) => <div className="scenario-refresh-action" key={action.id}>
                <div className="scenario-refresh-action-head">
                  {action.selectable
                    ? <label><input type="checkbox" aria-label={`${action.kind === 'manual_review' ? '核对并选择' : '选择'}第 ${action.position} 段${action.label}`} checked={refreshSelected.includes(action.id)} onChange={(event) => chooseRefresh(action, event.target.checked)} /><strong>{action.label}</strong></label>
                    : <strong>{action.label}</strong>}
                  <button type="button" onClick={() => { locateBlock(action.position, action.block_id); if (!action.selectable) setPanel(null) }}>{action.selectable ? `第 ${action.position} 段` : '编辑正文'}</button>
                </div>
                {action.reason && <small>{action.reason}</small>}
                {!!action.reference_changes?.length && <div className="scenario-refresh-refs">{action.reference_changes.map((change, index) => <div key={`${change.key}-${index}`}><span>{activeRun?.snapshot.definitions.find((field) => field.key === change.key)?.label || change.key}</span><strong>{change.before} → {change.after || '解除引用'}</strong></div>)}</div>}
                {action.after && !['rebind', 'detach'].includes(action.kind) && (action.kind === 'manual_review'
                  ? <div className="scenario-refresh-compare"><p><b>原文</b> {action.before}</p><p><b>拟更新</b> {action.after}</p></div>
                  : <details><summary>查看变更</summary><p>原文：{action.before}</p><p>更新：{action.after}</p></details>)}
              </div>) : <div className="empty">没有需要更新的位置</div>}
              <div className="scenario-panel-actions"><button onClick={() => { setRefreshPreview(null); setRefreshSelected([]) }}>取消</button><button className="primary-button" disabled={!refreshSelected.length || busy || dirty} onClick={() => void applyRefresh()}>更新所选 {refreshSelected.length} 处</button></div>
            </>}
          </section>}
          {panel === 'check' && <>
            {activeTask?.kind === 'export' && taskControls}
            <div className="scenario-check-state"><strong>{report.reviewed ? '已核对' : '待核对'}</strong><span>{blocking.length ? `${blocking.length} 项待处理` : '无阻断项'}</span></div>
            {visibleIssues.length ? visibleIssues.map((issue, index) => <button className="scenario-issue" key={`${issue.code}-${index}`} onClick={() => { if (issue.position) { locateBlock(issue.position, report.content[issue.position - 1]?.id); setPanel('sources') } }}><span>{issue.message}</span><small>{issue.severity === 'block' ? '待处理' : '保留事项'}</small></button>) : <div className="empty">暂无问题</div>}
            <IssuePanel projectId={project.id} reportId={report.id} sectionId={effectiveSection}
              onChanged={() => loadReport(report.id)} onLocate={(position) => locateBlock(position)}
              onOpenDocument={onOpenDocument} />
            <div className="scenario-panel-actions"><button onClick={() => void review()} disabled={!!blocking.filter((issue) => issue.code !== 'UNREVIEWED').length || busy || dirty || report.reviewed}>我已核对</button></div>
            <div className="scenario-delivery"><button onClick={() => void downloadExport('preview')} disabled={busy || dirty}><Download size={14} /> 预审稿</button><button className="primary-button" disabled={!report.reviewed || !!blocking.length || dirty || busy} onClick={() => void downloadExport('scenario')}><Download size={14} /> 情景分析报告</button></div>
            <div className="scenario-export-history"><button className="scenario-export-toggle" aria-expanded={exportsOpen} onClick={() => void toggleExports()}>历史交付 <ChevronDown size={13} /></button>{exportsOpen && (exports.length ? exports.map((entry) => <button className="scenario-export-row" key={entry.id} title={entry.sha256} onClick={() => void downloadExport(undefined, entry.id)} disabled={busy}><span>v{entry.report_version} · {entry.level === 'preview' ? '预审稿' : entry.level === 'scenario' ? '情景分析' : '正式稿'}</span><small>{timeLabel(entry.created_at)} · 下载</small></button>) : <div className="empty">暂无交付记录</div>)}</div>
          </>}
          {panel === 'versions' && <>{versions.map((version) => <button className="scenario-version" key={version.version} onClick={() => void compareVersion(version.version)}><span>v{version.version} · {timeLabel(version.created_at)}</span><small>{version.reviewed ? '已核对' : '工作稿'}</small></button>)}{comparison && <div className="scenario-diff">{comparison.changes.length ? comparison.changes.map((change) => <div key={change.position}><strong>第 {change.position} 段</strong><p>原文：{change.before || '—'}</p><p>当前：{change.after || '—'}</p></div>) : '正文无变化'}</div>}</>}
          {panel === 'materials' && <><div className="scenario-material-row"><strong>本章资料</strong><button onClick={onOpenCorpus}>项目资料</button></div>{chapterPack ? <><div className="scenario-material-summary"><span>项目事实 {chapterPack.facts.length}</span><span>项目证据 {chapterPack.project_evidence.length}</span><span>历史引用 {chapterPack.categories.reduce((sum, row) => sum + row.used_records.length, 0)}</span></div>{chapterPack.project_evidence.length > 0 && <details className="scenario-material-group"><summary>项目证据 · {chapterPack.project_evidence.length}</summary>{chapterPack.project_evidence.map((item) => <button className="scenario-source-row" key={item.id} onClick={() => void inspectProjectEvidence(item.id)}>{item.label}{item.source_type === 'secondary' ? ' · 二手' : ''}</button>)}</details>}{evidencePreview && <div className="scenario-evidence-preview"><div><strong>项目原文</strong><button aria-label="关闭原文预览" onClick={() => setEvidencePreview(null)}><X size={14} /></button></div><p>{evidencePreview.text}</p><button onClick={() => onOpenDocument(evidencePreview.documentId, evidencePreview.page, evidencePreview.ref)}>定位原文</button></div>}{chapterPack.groups.map((group) => <details className="scenario-material-group" key={group.name}><summary>{group.name}</summary>{chapterPack.categories.filter((item) => item.group === group.name).map((item) => <div className="scenario-material-category" key={item.category_id}><span>{item.name}</span><small>{item.status}</small><button onClick={() => { setMaterialDetail(item); setMaterialMode(item.mode) }}>作用</button></div>)}</details>)}</> : <div className="empty">正在读取本章资料…</div>}</>}
        </div></aside>}
      {materialDetail && <div className="dialog-backdrop" onClick={() => setMaterialDetail(null)}><div className="dialog scenario-material-dialog" role="dialog" aria-modal="true" aria-label={`${materialDetail.name}的作用`} onClick={(event) => event.stopPropagation()}><div className="dialog-head"><h2>{materialDetail.name}</h2><button className="icon-button" aria-label="关闭" onClick={() => setMaterialDetail(null)}><X size={18} /></button></div><p><strong>{materialDetail.status}</strong> · {materialDetail.reason}</p>{materialDetail.used_records.length ? materialDetail.used_records.map((item, index) => <button className="scenario-source-row" key={`${item.record_id}-${index}`} onClick={() => { setMaterialDetail(null); const target = report.content.findIndex((block) => block.id === item.block_id); if (target >= 0) locateBlock(target + 1, item.block_id) }}>{item.record_id} · 定位正文</button>) : <small>本章没有采用记录</small>}{chapterPack?.corpus_id && !['CAT-11', 'CAT-30'].includes(materialDetail.category_id) && <><label className="form-field"><span>本章策略</span><select aria-label="本章策略" value={materialMode} onChange={(event) => setMaterialMode(event.target.value)}><option value="auto">参与起草</option><option value="review">仅供核对</option><option value="exclude">不使用</option></select></label><div className="form-actions"><button onClick={() => setMaterialDetail(null)}>取消</button><button className="primary-button" onClick={() => void saveMaterialMode()} disabled={busy || materialMode === materialDetail.mode}>保存策略</button></div></>}</div></div>}
      </div>
      {chapterDialog && <div className="dialog-backdrop" onClick={() => setChapterDialog(null)}><div className="dialog" role="dialog" aria-modal="true" aria-label={chapterDialog.action === 'add' ? '添加章节' : '重命名章节'} onClick={(event) => event.stopPropagation()}><div className="dialog-head"><h2>{chapterDialog.action === 'add' ? '添加章节' : '重命名章节'}</h2><button className="icon-button" onClick={() => setChapterDialog(null)} aria-label="关闭"><X size={18} /></button></div><label className="form-field"><span>章节标题</span><input autoFocus maxLength={80} value={chapterDialog.title} onChange={(event) => setChapterDialog({ ...chapterDialog, title: event.target.value })} onKeyDown={(event) => { if (event.key === 'Enter' && !event.nativeEvent.isComposing && chapterDialog.title.trim()) void changeChapter(chapterDialog.action, chapterDialog.headingId, chapterDialog.title) }} /></label><div className="form-actions"><button onClick={() => setChapterDialog(null)}>取消</button><button className="primary-button" disabled={!chapterDialog.title.trim() || busy} onClick={() => void changeChapter(chapterDialog.action, chapterDialog.headingId, chapterDialog.title)}>{chapterDialog.action === 'add' ? '添加' : '保存'}</button></div></div></div>}
      {recovery && <div className="dialog-backdrop"><div className="dialog" role="dialog" aria-modal="true" aria-label="恢复未保存正文"><div className="dialog-head"><h2>发现未保存正文</h2></div>{recovery.baseVersion !== report.version && <p className="scenario-recovery-note">服务器已更新至 v{report.version}。恢复本地稿并保存会生成新版本；服务器原版可在“版本”中查看。</p>}<div className="form-actions"><button onClick={discardRecovery}>使用服务器版本</button>{recovery.baseVersion !== report.version && <button onClick={() => void copyRecovery()}>复制本地文字</button>}<button className="primary-button" onClick={restoreBody}>恢复本地稿</button></div></div></div>}
    </>}
  </main>
}
