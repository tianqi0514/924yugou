import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { NodeIdPlugin } from '@platejs/core'
import type { Value } from 'platejs'
import { BlockquotePlugin, BoldPlugin, H1Plugin, H2Plugin, H3Plugin, ItalicPlugin, StrikethroughPlugin, UnderlinePlugin } from '@platejs/basic-nodes/react'
import { useComboboxInput } from '@platejs/combobox/react'
import { LinkPlugin } from '@platejs/link/react'
import { upsertLink, validateUrl } from '@platejs/link'
import { ListPlugin } from '@platejs/list/react'
import { toggleList } from '@platejs/list'
import { SlashInputPlugin, SlashPlugin } from '@platejs/slash-command/react'
import { TableCellHeaderPlugin, TableCellPlugin, TablePlugin, TableRowPlugin } from '@platejs/table/react'
import { deleteColumn, deleteRow, insertTable, insertTableColumn, insertTableRow } from '@platejs/table'
import { Plate, PlateContent, PlateElement, createPlatePlugin, useEditorRef, useEditorSelector, usePlateEditor, type PlateElementProps } from 'platejs/react'
import { Check, Download, Plus, RefreshCw, Sparkles, X } from 'lucide-react'
import { api, post, rawUrl, type Fact, type Project } from './api'
import CorpusWritingPanel, { locationText, originalWordLocator, primarySourceText, sourceLocation, wordLocator, type CorpusSourceRef } from './CorpusWritingPanel'
import { refreshedFactDisplay } from './fact-display'
import './report-editor.css'

type TextLeaf = { type?: undefined; text: string; bold?: true; italic?: true; underline?: true; strikethrough?: true }
type FactRef = { type: 'fact_ref'; fact_key: string; display: string; children: [{ text: '' }] }
type Link = { type: 'a'; url: string; target?: '_blank'; children: TextLeaf[] }
type Inline = TextLeaf | FactRef | Link
type ProjectRuleRef = { rule_id: string; expression: string; target_key: string; deps: string[];
  input_fact_revisions: { fact_key: string; revision: number; value: string | null }[]; target_fact_revision: number }
type TextBlock = { type: 'p' | 'h1' | 'h2' | 'h3' | 'blockquote'; children: Inline[]; id?: string; fact_keys?: string[]; origin?: string; section_id?: string; source_refs?: CorpusSourceRef[]; project_rule_refs?: ProjectRuleRef[]; listStyleType?: 'disc' | 'decimal'; indent?: number; listStart?: number }
type TableCell = { type: 'td' | 'th'; children: TextBlock[]; id?: string }
type TableRow = { type: 'tr'; children: TableCell[]; id?: string }
type TableBlock = { type: 'table'; children: TableRow[]; id?: string; fact_keys?: string[]; origin?: string; section_id?: string; source_refs?: CorpusSourceRef[] }
type Block = TextBlock | TableBlock
type EditorActions = { insertFact: (fact: Fact) => void }
type Issue = { code: string; message: string; severity: string; fact_key?: string }
type FactImpact = { position: number; text: string; fact_key: string; before: { value: string | null; revision: number } | null; after: { value: string | null; revision: number } | null }
type FactSource = {
  kind: 'document' | 'manual' | 'missing' | 'rule'; fact: Fact; description: string;
  filename?: string; locator?: string; excerpt: string | null; original_url: string | null;
  review_status?: 'SOURCE_LOCATOR_REVIEWED' | 'UNVERIFIED' | 'DERIVED_FROM_REVIEWED_INPUTS' | 'DERIVED_INPUTS_UNVERIFIED';
  rule?: { id: string; name: string; target_key: string; expression: string; deps: string[] };
  input_facts?: { key: string; label: string; value: string | null; unit: string; revision: number; review_status: string; source_url: string }[];
}
type Report = { id: string; project_id: string; title: string; version: number; content: Block[]; reviewed: boolean; issues: Issue[]; fact_impacts: FactImpact[]; facts: Fact[]; updated_at: string }
type ReportSummary = { id: string; title: string; version: number; updated_at: string }
type ReportPreview = { base_version: number; preview_token: string; changes: { position: number; before: string; after: string; fact_keys: string[] }[]; fact_keys_affected: string[] }
type SectionPreview = { title: string; fact_keys: string[]; paragraphs: TextBlock[]; base_version: number; project_version: number; preview_token: string; expires_at: number }
type VersionEntry = { version: number; reviewed: boolean; created_at: string }
type Comparison = { base_version: number; current_version: number; changes: ReportPreview['changes'] }
type CorpusSourceDetail = { source_ref: CorpusSourceRef; summary: string; location: unknown; payload: Record<string, unknown> | null }
type CorpusSourceImpact = { report_id: string; report_title: string; report_version: number; block_id: string;
  section_id: string; position: number; text: string }
type CorpusSourceView = CorpusSourceDetail & { source_project_id: string; impacts: CorpusSourceImpact[] }

function CorpusSourceDrawer({ source, onClose, onJump }: { source: CorpusSourceView; onClose: () => void;
  onJump: (reportId: string, position: number) => void }) {
  const payload = source.payload
  const excerpt = Array.isArray(payload?.excerpt_locations) ? payload.excerpt_locations[0] : null
  const excerptWord = excerpt && typeof excerpt === 'object' ? wordLocator((excerpt as Record<string, unknown>).source_locator) : null
  return <div className="drawer-backdrop" onClick={onClose}><aside className="drawer" onClick={(event) => event.stopPropagation()}>
    <div className="drawer-header"><h2>历史参考 · {source.source_ref.semantic_id || source.source_ref.record_id}</h2><button className="icon-button" aria-label="关闭语料来源" onClick={onClose}><X size={20} /></button></div>
    <div className="drawer-content corpus-writing-source"><span className="status status-neutral">待本项目核对</span>
      <p>{source.summary}</p>{primarySourceText(payload) && primarySourceText(payload) !== source.summary && <blockquote className="source-excerpt corpus-source-text">{primarySourceText(payload)}</blockquote>}<div className="field-grid"><div className="field"><span>记录</span><strong>{source.source_ref.category_id} · {source.source_ref.record_id}</strong></div>
        <div className="field"><span>版本</span><strong>{source.source_ref.corpus_version}</strong></div>
        <div className="field"><span>原文位置</span><strong>{locationText(sourceLocation(source, null))}</strong></div></div>
      {payload?.original_document_available === false && <div className="notice warn">完整原件缺失</div>}
      {Array.isArray(payload?.limitations) && <div className="corpus-writing-limitations"><strong>证据限制</strong>{payload.limitations.map((item, index) => <span key={index}>{String(item)}</span>)}</div>}
      {originalWordLocator(source) && <div className="notice">Word 原文：{originalWordLocator(source)}</div>}
      {excerpt && <div className="notice">首条摘录：{locationText(excerpt)}{excerptWord ? ` · Word ${excerptWord}` : ''}</div>}
      <div className="corpus-writing-impacts"><h3>已引用于</h3>{source.impacts.length ? source.impacts.map((impact) => <button type="button" key={`${impact.report_id}-${impact.block_id}`} onClick={() => { onJump(impact.report_id, impact.position); onClose() }}><b>{impact.report_title} · v{impact.report_version} · 第 {impact.position} 段</b><span>{impact.text}</span></button>) : <small>当前项目报告尚未引用此记录。</small>}</div>
      <a className="text-button" href={rawUrl(source.source_project_id, source.source_ref.artifact_id)} target="_blank" rel="noreferrer">打开入库原件</a>
    </div>
  </aside></div>
}

function FactSourceDrawer({ source, onClose, onOpenFact }: { source: FactSource; onClose: () => void;
  onOpenFact: (key: string) => void }) {
  const statusText = source.review_status === 'SOURCE_LOCATOR_REVIEWED' ? '本项目原文位置已核对（当前修订）'
    : source.review_status === 'DERIVED_FROM_REVIEWED_INPUTS' ? '依据已核对的输入事实计算'
      : source.review_status === 'DERIVED_INPUTS_UNVERIFIED' ? '计算输入的原文位置仍待核对'
        : '来源待核对'
  return <div className="drawer-backdrop" onClick={onClose}><aside className="drawer" onClick={(event) => event.stopPropagation()}>
    <div className="drawer-header"><h2>事实来源 · {source.fact.label}</h2><button className="icon-button" aria-label="关闭来源" onClick={onClose}><X size={20} /></button></div>
    <div className="drawer-content"><div className="notice">{source.fact.key} · {source.fact.value ?? '未定义'}{source.fact.unit} · r{source.fact.revision}</div>
      <div className={`notice ${source.review_status === 'SOURCE_LOCATOR_REVIEWED' || source.review_status === 'DERIVED_FROM_REVIEWED_INPUTS' ? '' : 'warn'}`}>{statusText}</div>
      {source.kind === 'rule' ? <div className="fact-rule-source">
        <strong>{source.rule?.name || '项目计算规则'}</strong>
        <code>{source.rule?.expression || source.description}</code>
        <small>{source.rule?.id || '规则 ID 未返回'} · 目标 {source.rule?.target_key || source.fact.key}</small>
        <h3>上游事实</h3>
        {source.input_facts?.length ? source.input_facts.map((input) => <button type="button" key={input.key} onClick={() => onOpenFact(input.key)}>
          <span><b>{input.label} · {input.value ?? '未定义'}{input.unit}</b><small>{input.key} · r{input.revision} · {input.review_status === 'SOURCE_LOCATOR_REVIEWED' ? '原文位置已核对' : '来源待核对'}</small></span>
          <span>查看来源 →</span>
        </button>) : <div className="notice warn">上游事实缺失</div>}
      </div> : <>
        <p className="source-meta">{source.kind === 'document' ? `${source.filename || '本项目原件'} · ${source.locator || '原文定位未返回'}` : source.description}</p>
        {source.excerpt ? <blockquote className="source-excerpt">{source.excerpt}</blockquote>
          : <div className="notice warn">{source.kind === 'manual' ? '无原文摘录' : '原文定位不可用'}</div>}
        {source.original_url && <a className="primary-button" href={source.original_url} target="_blank" rel="noreferrer">打开原件位置</a>}
      </>}
    </div>
  </aside></div>
}

function HeadingOne(props: PlateElementProps) { return <PlateElement as="h1" {...props} /> }
function HeadingTwo(props: PlateElementProps) { return <PlateElement as="h2" {...props} /> }
function HeadingThree(props: PlateElementProps) { return <PlateElement as="h3" {...props} /> }
function QuoteBlock(props: PlateElementProps) { return <PlateElement as="blockquote" {...props} /> }
function TableElement(props: PlateElementProps) { return <PlateElement as="table" className="report-table" {...props}><tbody>{props.children}</tbody></PlateElement> }
function TableRowElement(props: PlateElementProps) { return <PlateElement as="tr" {...props} /> }
function TableCellElement(props: PlateElementProps) { return <PlateElement as="td" {...props} /> }
function TableHeaderElement(props: PlateElementProps) { return <PlateElement as="th" {...props} /> }
function LinkElement(props: PlateElementProps) {
  const node = props.element as unknown as Link
  return <PlateElement as="a" className="report-link" {...props} {...({ href: node.url, target: '_blank', rel: 'noopener noreferrer' } as Record<string, string>)} />
}
const FactReferenceContext = createContext<{ open: (key: string) => void; stale: Set<string> }>({ open: () => {}, stale: new Set() })
const LinkEditorContext = createContext<{ open: () => void }>({ open: () => {} })
function FactReferenceElement(props: PlateElementProps) {
  const node = props.element as unknown as FactRef
  const { open, stale } = useContext(FactReferenceContext)
  return <PlateElement as="span" {...props} className={`report-fact-token${stale.has(node.fact_key) ? ' stale' : ''}`}>
    <span role="button" tabIndex={0} contentEditable={false} title={`查看 ${node.fact_key} 的来源`}
      onMouseDown={(event) => event.preventDefault()} onClick={() => open(node.fact_key)}
      onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(node.fact_key) } }}>{node.display}</span>{props.children}
  </PlateElement>
}
const FactReferencePlugin = createPlatePlugin({ key: 'fact_ref', node: { isElement: true, isInline: true, isVoid: true } }).withComponent(FactReferenceElement)

function TableTools() {
  const editor = useEditorRef()
  const dimensions = useEditorSelector((current) => {
    const top = current.selection?.anchor.path[0]
    const table = (top === undefined ? null : current.children[top]) as Block | null
    if (!table || table.type !== 'table') return null
    return `${table.children.length}:${table.children[0]?.children.length || 0}`
  }, [])
  if (!dimensions) return null
  const [rows, columns] = dimensions.split(':').map(Number)
  return <><span />
    <button type="button" disabled={rows >= 100} onMouseDown={(event) => event.preventDefault()} onClick={() => insertTableRow(editor)}>加行</button>
    <button type="button" disabled={columns >= 8} onMouseDown={(event) => event.preventDefault()} onClick={() => insertTableColumn(editor)}>加列</button>
    <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => deleteRow(editor)}>删行</button>
    <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => deleteColumn(editor)}>删列</button>
  </>
}

const slashCommands = [
  { id: 'p', label: '正文段落', hint: 'p / text' },
  { id: 'h1', label: '一级标题', hint: 'h1 / 标题 1' },
  { id: 'h2', label: '二级标题', hint: 'h2 / 标题 2' },
  { id: 'h3', label: '三级标题', hint: 'h3 / 标题 3' },
  { id: 'blockquote', label: '引用块', hint: 'quote / 引用' },
  { id: 'bullet', label: '项目符号列表', hint: 'bullet / 列表' },
  { id: 'number', label: '编号列表', hint: 'number / 列表' },
  { id: 'table', label: '插入 2×2 表格', hint: 'table / 表格' },
  { id: 'link', label: '插入链接', hint: 'link / 网址' },
] as const

function hasTransientSlash(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(hasTransientSlash)
  if (!value || typeof value !== 'object') return false
  const node = value as { type?: string; children?: unknown }
  return node.type === 'slash_input' || hasTransientSlash(node.children)
}

function normalizedInline(value: Inline[]): Inline[] {
  return value.map((node) => {
    if (node.type === 'fact_ref') return { type: 'fact_ref', fact_key: node.fact_key, display: node.display, children: [{ text: '' }] }
    if (node.type === 'a') return { type: 'a', url: node.url, ...(node.target === '_blank' ? { target: '_blank' as const } : {}), children: normalizedInline(node.children) as TextLeaf[] }
    return { text: node.text, ...(node.bold ? { bold: true as const } : {}), ...(node.italic ? { italic: true as const } : {}),
      ...(node.underline ? { underline: true as const } : {}), ...(node.strikethrough ? { strikethrough: true as const } : {}) }
  })
}

function normalizedTextBlock(block: TextBlock): TextBlock {
  return { type: block.type, children: normalizedInline(block.children), ...(block.id ? { id: block.id } : {}),
    ...(block.fact_keys?.length ? { fact_keys: block.fact_keys } : {}), ...(block.origin ? { origin: block.origin } : {}),
    ...(block.section_id ? { section_id: block.section_id } : {}), ...(block.source_refs?.length ? { source_refs: block.source_refs } : {}),
    ...(block.project_rule_refs?.length ? { project_rule_refs: block.project_rule_refs } : {}),
    ...(block.listStyleType ? { listStyleType: block.listStyleType } : {}), ...(block.indent ? { indent: block.indent } : {}),
    ...(block.listStart ? { listStart: block.listStart } : {}) }
}

function normalizedBlocks(value: Block[]): Block[] {
  return value.map((block) => block.type !== 'table' ? normalizedTextBlock(block) : {
    type: 'table', ...(block.id ? { id: block.id } : {}), ...(block.fact_keys?.length ? { fact_keys: block.fact_keys } : {}),
    ...(block.origin ? { origin: block.origin } : {}), ...(block.section_id ? { section_id: block.section_id } : {}),
    ...(block.source_refs?.length ? { source_refs: block.source_refs } : {}),
    children: block.children.map((row) => ({ type: 'tr', ...(row.id ? { id: row.id } : {}), children: row.children.map((cell) => ({
      type: cell.type, ...(cell.id ? { id: cell.id } : {}), children: cell.children.map(normalizedTextBlock),
    })) })),
  })
}

function SlashInputElement(props: PlateElementProps) {
  const editor = useEditorRef()
  const linkEditor = useContext(LinkEditorContext)
  const inputRef = useRef<HTMLInputElement>(null)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const { props: inputBehavior, removeInput } = useComboboxInput({ ref: inputRef })
  const options = slashCommands.filter((item) => `${item.label} ${item.hint}`.toLowerCase().includes(query.trim().toLowerCase()))
  const choose = (id: typeof slashCommands[number]['id']) => {
    const inputPath = editor.api.findPath(props.element)
    const blockPath = inputPath?.slice(0, -1)
    removeInput(false)
    if (id === 'bullet' || id === 'number') toggleList(editor, { listStyleType: id === 'bullet' ? 'disc' : 'decimal' })
    else if (id === 'table') insertTable(editor, { rowCount: 2, colCount: 2, header: true }, { select: true })
    else if (id === 'link') linkEditor.open()
    else if (blockPath) editor.tf.setNodes({ type: id }, { at: blockPath })
    editor.tf.focus()
  }
  return <PlateElement as="span" {...props}>
    <span className="slash-anchor" contentEditable={false}>
      <input ref={inputRef} aria-label="斜杠命令搜索" className="slash-query" value={query} onChange={(event) => { setQuery(event.target.value); setActive(0) }}
        onBlur={inputBehavior.onBlur} onKeyDown={(event) => {
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); setActive((index) => options.length ? (index + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length : 0); return }
          if (event.key === 'Enter' && options.length) { event.preventDefault(); choose(options[Math.min(active, options.length - 1)].id); return }
          if (event.key === 'Escape') { event.preventDefault(); removeInput(true); return }
          inputBehavior.onKeyDown(event)
        }} placeholder="搜索命令…" />
      <div className="slash-menu" role="listbox" aria-label="插入内容">
        {options.length ? options.map((item, index) => <button key={item.id} type="button" role="option" aria-selected={index === active}
          className={index === active ? 'active' : ''} onMouseDown={(event) => event.preventDefault()} onClick={() => choose(item.id)}>
          <span>{item.label}</span><small>{item.hint}</small></button>) : <div className="slash-empty">没有匹配的命令</div>}
      </div>
    </span>{props.children}
  </PlateElement>
}

function EditorPane({ initial, onChange, onSelectPosition, actionsRef, onOpenFact, staleFactKeys, facts }: {
  initial: Block[]; onChange: (value: Block[]) => void; onSelectPosition: (position: number) => void;
  actionsRef: { current: EditorActions | null }; onOpenFact: (key: string) => void; staleFactKeys: string[]; facts: Fact[]
}) {
  const editor = usePlateEditor({
    plugins: [NodeIdPlugin.configure({ options: { initialValueIds: 'always' } }), BoldPlugin, ItalicPlugin, UnderlinePlugin, StrikethroughPlugin,
      H1Plugin.withComponent(HeadingOne), H2Plugin.withComponent(HeadingTwo), H3Plugin.withComponent(HeadingThree), BlockquotePlugin.withComponent(QuoteBlock),
      ListPlugin, LinkPlugin.withComponent(LinkElement), TablePlugin.withComponent(TableElement).configure({ plugins: [
        TableRowPlugin.withComponent(TableRowElement), TableCellPlugin.withComponent(TableCellElement), TableCellHeaderPlugin.withComponent(TableHeaderElement),
      ] }), FactReferencePlugin, SlashPlugin, SlashInputPlugin.withComponent(SlashInputElement)],
    value: initial as Value,
  })
  const [linkOpen, setLinkOpen] = useState(false)
  const [factMenuOpen, setFactMenuOpen] = useState(false)
  const [linkUrl, setLinkUrl] = useState('')
  const [linkError, setLinkError] = useState('')
  const savedLinkSelection = useRef(editor.selection)
  const openLink = () => { savedLinkSelection.current = editor.selection; setLinkUrl(''); setLinkError(''); setLinkOpen(true) }
  const commitLink = () => {
    const url = linkUrl.trim()
    if (!/^https?:\/\//i.test(url) || !validateUrl(editor, url)) { setLinkError('请输入有效的 http 或 https 地址'); return }
    if (savedLinkSelection.current) editor.tf.select(savedLinkSelection.current)
    upsertLink(editor, { url })
    editor.tf.focus()
    setLinkOpen(false)
    setLinkError('')
  }
  const knownBlockIds = useRef(new Set((editor.children as Block[]).map((block) => block.id).filter((id): id is string => !!id)))
  const previousBlocks = useRef(normalizedBlocks(initial))
  const repairingSplitIds = useRef(false)
  const clearedInheritedKeys = useRef(new Set<string>())
  const clearedInheritedSources = useRef(new Set<string>())
  const reportChange = (value: Value) => {
    if (repairingSplitIds.current || hasTransientSlash(value)) return
    const next = normalizedBlocks(value as Block[])
    const before = previousBlocks.current
    const beforeIds = new Set(before.map((block) => block.id).filter((id): id is string => !!id))
    let activeSection: string | undefined
    for (const block of next) {
      if (block.section_id) activeSection = block.section_id
      else if (['h1', 'h2', 'h3'].includes(block.type)) activeSection = undefined
      else if (activeSection) block.section_id = activeSection
      if (!block.id) continue
      if (!knownBlockIds.current.has(block.id)) {
        // A split or pasted paragraph must not silently inherit its source or model review status.
        if (block.source_refs?.length || (block.type !== 'table' && block.project_rule_refs?.length) || block.origin === 'model' || block.origin === 'guided') clearedInheritedSources.current.add(block.id)
        if (block.fact_keys?.length && !block.fact_keys.some((key) => hasFactToken(block, key))) clearedInheritedKeys.current.add(block.id)
      }
      if (clearedInheritedSources.current.has(block.id)) {
        delete block.source_refs
        if (block.type !== 'table') delete block.project_rule_refs
        if (block.origin === 'model' || block.origin === 'guided') block.origin = 'manual'
      }
      if (clearedInheritedKeys.current.has(block.id)) delete block.fact_keys
      knownBlockIds.current.add(block.id)
    }
    // Plate may keep the original block ID on an empty half of a split.
    // Preserve provenance only when the other, newly created half contains
    // the exact same visible text and fact tokens as the original block.
    for (const old of before) {
      if (!old.id || !blockText(old).trim()) continue
      const emptyIndex = next.findIndex((block) => block.id === old.id && !blockText(block).trim())
      if (emptyIndex < 0) continue
      const moved = next[emptyIndex + 1]
      if (!moved?.id || beforeIds.has(moved.id) || moved.type !== old.type || blockText(moved) !== blockText(old)) continue
      const newId = moved.id
      repairingSplitIds.current = true
      try {
        editor.tf.setNodes({ id: newId, fact_keys: undefined, source_refs: undefined,
          project_rule_refs: undefined, origin: undefined }, { at: [emptyIndex] })
        editor.tf.setNodes({ id: old.id, fact_keys: old.fact_keys, source_refs: old.source_refs,
          project_rule_refs: old.type === 'table' ? undefined : old.project_rule_refs,
          origin: old.origin, section_id: old.section_id }, { at: [emptyIndex + 1] })
      } finally { repairingSplitIds.current = false }
      next[emptyIndex].id = newId
      moved.id = old.id
      if (old.fact_keys?.length) moved.fact_keys = [...old.fact_keys]
      if (old.source_refs?.length) moved.source_refs = [...old.source_refs]
      if (old.origin) moved.origin = old.origin
      if (old.section_id) moved.section_id = old.section_id
      if (old.type !== 'table' && moved.type !== 'table' && old.project_rule_refs?.length) moved.project_rule_refs = [...old.project_rule_refs]
      clearedInheritedKeys.current.delete(old.id)
      clearedInheritedSources.current.delete(old.id)
    }
    for (const block of next) {
      if (blockText(block).trim()) continue
      delete block.fact_keys
      delete block.source_refs
      delete block.origin
      if (block.type !== 'table') delete block.project_rule_refs
    }
    previousBlocks.current = next
    onChange(next)
  }
  useEffect(() => {
    actionsRef.current = { insertFact: (fact) => {
      if (!editor.selection) editor.tf.select(editor.api.end([editor.children.length - 1]))
      const topIndex = editor.selection?.anchor.path[0]
      if (topIndex === undefined) return
      const block = editor.children[topIndex] as Block
      const keys = block.fact_keys || []
      if (!keys.includes(fact.key)) editor.tf.setNodes({ fact_keys: [...keys, fact.key] }, { at: [topIndex] })
      editor.tf.insertNodes({ type: 'fact_ref', fact_key: fact.key, display: `${fact.label} ${fact.value}${fact.unit}`, children: [{ text: '' }] })
      editor.tf.focus()
    } }
    return () => { actionsRef.current = null }
  }, [actionsRef, editor])
  const selectedBlock = (event?: { target: EventTarget | null }) => {
    const target = event?.target
    const blockId = target instanceof HTMLElement ? target.closest('[data-block-id]')?.getAttribute('data-block-id') : null
    if (blockId) {
      const index = (editor.children as Block[]).findIndex((block) => block.id === blockId)
      if (index >= 0) { onSelectPosition(index + 1); return }
    }
    const path = editor.selection?.anchor.path
    if (!path) return
    onSelectPosition(path[0] + 1)
  }
  return <FactReferenceContext.Provider value={{ open: onOpenFact, stale: new Set(staleFactKeys) }}><LinkEditorContext.Provider value={{ open: openLink }}><Plate editor={editor} onChange={({ value }) => reportChange(value)}>
    <div className="plate-toolbar">
      <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => editor.tf.h1.toggle()}>标题 1</button>
      <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => editor.tf.h2.toggle()}>标题 2</button>
      <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => editor.tf.h3.toggle()}>标题 3</button><span />
      <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => toggleList(editor, { listStyleType: 'disc' })}>项目符号</button>
      <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => toggleList(editor, { listStyleType: 'decimal' })}>编号</button>
      <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => insertTable(editor, { rowCount: 2, colCount: 2, header: true }, { select: true })}>表格</button>
      <TableTools />
      <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={openLink}>链接</button>
      <div className="plate-fact-menu-wrap"><button type="button" aria-expanded={factMenuOpen} onMouseDown={(event) => event.preventDefault()} onClick={() => setFactMenuOpen((open) => !open)}>事实</button>
        {factMenuOpen && <div className="plate-fact-menu" role="menu" aria-label="插入事实引用">{facts.length ? facts.map((fact) => <button type="button" role="menuitem" key={fact.key} onMouseDown={(event) => event.preventDefault()} onClick={() => { actionsRef.current?.insertFact(fact); setFactMenuOpen(false) }}>{fact.label} · {fact.value}{fact.unit}</button>) : <span>暂无可用事实</span>}</div>}</div><span />
      <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => editor.tf.bold.toggle()}><b>B</b></button>
      <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => editor.tf.italic.toggle()}><i>I</i></button>
      <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => editor.tf.underline.toggle()}><u>U</u></button>
      <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => editor.tf.strikethrough.toggle()}><s>S</s></button><span />
      <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => editor.undo()}>撤销</button>
      <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => editor.redo()}>重做</button>
      {linkOpen && <form className="report-link-form" onSubmit={(event) => { event.preventDefault(); commitLink() }}>
        <input autoFocus aria-label="链接地址" value={linkUrl} onChange={(event) => { setLinkUrl(event.target.value); setLinkError('') }} placeholder="https://example.com" />
        <button type="submit">确定链接</button><button type="button" onClick={() => { setLinkOpen(false); setLinkError('') }}>取消链接</button>
        {linkError && <small role="alert">{linkError}</small>}
      </form>}
    </div>
    <PlateContent className="plate-content" placeholder="开始撰写报告正文…" onKeyUp={selectedBlock} onClick={selectedBlock} />
  </Plate></LinkEditorContext.Provider></FactReferenceContext.Provider>
}

function sourceLink(projectId: string, fact: Fact): string | null {
  const match = /^document:([a-f0-9-]+)#(p(\d+)-(?:s\d+|t\d+-r\d+)|d-[\w-]+)/.exec(fact.source)
  if (!match) return null
  return `/api/projects/${projectId}/documents/${match[1]}/original${match[3] ? `#page=${match[3]}` : ''}`
}

function inlineText(children: Inline[]): string { return children.map((leaf) => leaf.type === 'fact_ref' ? leaf.display : leaf.type === 'a' ? inlineText(leaf.children) : leaf.text).join('') }
function blockText(block: Block): string { return block.type === 'table' ? block.children.map((row) => row.children.map((cell) => cell.children.map((paragraph) => inlineText(paragraph.children)).join(' ')).join(' | ')).join(' / ') : inlineText(block.children) }
function RuleProvenance({ block }: { block: Block }) {
  if (block.type === 'table' || !block.project_rule_refs?.length) return null
  return <div className="report-rule-refs"><strong>本项目规则计算</strong>{block.project_rule_refs.map((rule) => <div key={rule.rule_id}>
    <b>{rule.rule_id} · {rule.target_key}</b><span className="mono">{rule.expression}</span>
    <small>输入 {rule.input_fact_revisions.map((fact) => `${fact.fact_key} r${fact.revision}=${fact.value ?? '未定义'}`).join('、')} · 结果 r{rule.target_fact_revision}</small>
  </div>)}</div>
}
function hasFactToken(block: Block, key: string): boolean {
  const includes = (children: Inline[]) => children.some((node) => node.type === 'fact_ref' && node.fact_key === key)
  return block.type === 'table' ? block.children.some((row) => row.children.some((cell) => cell.children.some((paragraph) => includes(paragraph.children)))) : includes(block.children)
}
function updateFactToken(block: Block, key: string, before: string | null, fact: Fact): Block | null {
  let invalid = false
  let found = false
  const update = (children: Inline[]): Inline[] => children.map((node) => {
    if (node.type !== 'fact_ref' || node.fact_key !== key) return node
    found = true
    const display = refreshedFactDisplay(node.display, before, fact.value, fact.data_type, fact.unit)
    if (display === null) { invalid = true; return node }
    return { ...node, display }
  })
  const result: Block = block.type !== 'table' ? { ...block, children: update(block.children) }
    : { ...block, children: block.children.map((row) => ({ ...row, children: row.children.map((cell) => ({
      ...cell, children: cell.children.map((paragraph) => ({ ...paragraph, children: update(paragraph.children) })),
    })) })) }
  return invalid || !found ? null : result
}

export default function ReportsView({ project, notify, onEditFacts, onOpenDocuments, onOpenProjectFacts }: {
  project: Project; notify: (message: string) => void; onEditFacts: () => void;
  onOpenDocuments: () => void; onOpenProjectFacts: () => void
}) {
  const [reports, setReports] = useState<ReportSummary[]>([])
  const [reportId, setReportId] = useState('')
  const [report, setReport] = useState<Report | null>(null)
  const [facts, setFacts] = useState<Fact[]>([])
  const [content, setContent] = useState<Block[]>([])
  const [selectedPosition, setSelectedPosition] = useState<number | null>(null)
  const [source, setSource] = useState<FactSource | null>(null)
  const [corpusSource, setCorpusSource] = useState<CorpusSourceView | null>(null)
  const [editorKey, setEditorKey] = useState(0)
  const editorActions = useRef<EditorActions | null>(null)
  const candidateRequest = useRef(0)
  const pendingJump = useRef<{ reportId: string; position: number } | null>(null)
  const [title, setTitle] = useState('')
  const [sectionTitle, setSectionTitle] = useState('项目说明')
  const [selectedKeys, setSelectedKeys] = useState<string[]>([])
  const [preview, setPreview] = useState<ReportPreview | null>(null)
  const [sectionPreview, setSectionPreview] = useState<SectionPreview | null>(null)
  const [versions, setVersions] = useState<VersionEntry[]>([])
  const [compareBase, setCompareBase] = useState(0)
  const [comparison, setComparison] = useState<Comparison | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const dirty = !!report && JSON.stringify(normalizedBlocks(content)) !== JSON.stringify(normalizedBlocks(report.content))
  const usable = useMemo(() => facts.filter((fact) => fact.value !== null && ['PROVIDED', 'COMPUTED'].includes(fact.status)), [facts])
  const loadReports = useCallback(async () => {
    const items = await api<ReportSummary[]>(`/projects/${project.id}/reports`)
    setReports(items)
    setReportId((old) => {
      const saved = window.localStorage.getItem(`report-platform-report:${project.id}`)
      return items.some((item) => item.id === old) ? old : items.some((item) => item.id === saved) ? saved! : items[0]?.id || ''
    })
  }, [project.id])
  const loadReport = useCallback(async () => {
    candidateRequest.current += 1
    if (!reportId) { setReport(null); return }
    const item = await api<Report>(`/projects/${project.id}/reports/${reportId}`)
    setReport(item); setContent(item.content); setPreview(null); setSectionPreview(null); setSelectedPosition(null); setEditorKey((key) => key + 1)
    const history = await api<VersionEntry[]>(`/projects/${project.id}/reports/${reportId}/versions`)
    setVersions(history); setCompareBase(history[history.length - 1]?.version ?? 0); setComparison(null)
  }, [project.id, reportId])
  useEffect(() => { void loadReports().catch((cause: Error) => setError(cause.message)); void api<{ facts: Fact[] }>(`/projects/${project.id}/facts`).then((result) => setFacts(result.facts)).catch((cause: Error) => setError(cause.message)) }, [project.id, loadReports])
  useEffect(() => { void loadReport().catch((cause: Error) => setError(cause.message)) }, [loadReport])
  useEffect(() => {
    if (!report || pendingJump.current?.reportId !== report.id) return
    const position = pendingJump.current.position
    pendingJump.current = null
    setSelectedPosition(position)
    window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
      const blocks = document.querySelectorAll('.plate-content > [data-slate-node="element"]')
      blocks[position - 1]?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }))
  }, [report, editorKey])
  const jumpToReport = (targetReportId: string, position: number) => {
    if (dirty && !window.confirm('当前 Plate 修改尚未保存，确定切换定位？')) return
    pendingJump.current = { reportId: targetReportId, position }
    if (targetReportId === reportId) {
      setSelectedPosition(position)
      window.requestAnimationFrame(() => {
        document.querySelectorAll('.plate-content > [data-slate-node="element"]')[position - 1]
          ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      })
      pendingJump.current = null
    } else setReportId(targetReportId)
  }
  const create = async () => {
    if (!title.trim()) return
    setBusy(true); setError('')
    try { const created = await post<Report>(`/projects/${project.id}/reports`, { title: title.trim() }); await loadReports(); setReportId(created.id); window.localStorage.setItem(`report-platform-report:${project.id}`, created.id); setTitle(''); notify('报告已创建，可开始写作') }
    catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }
  const previewSave = async () => {
    if (!report) return
    setBusy(true); setError('')
    try { setPreview(await post<ReportPreview>(`/projects/${project.id}/reports/${report.id}/preview`, { content, base_version: report.version })) }
    catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }
  const save = async () => {
    if (!report || !preview) return
    setBusy(true); setError('')
    try {
      const result = await api<{ report: Report }>(`/projects/${project.id}/reports/${report.id}`, {
        method: 'PUT', body: JSON.stringify({ content, base_version: preview.base_version, preview_token: preview.preview_token }),
      })
      await loadReport(); await loadReports()
      if (result.report.fact_impacts.length) setError(`仍有 ${result.report.fact_impacts.length} 处事实引用过时，请核对文内数字和来源后重试`)
      else notify('报告新版本已保存，导出前请核对正文')
    }
    catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }
  const bindFact = (position: number, key: string) => {
    const current = content[position - 1]
    if (current?.fact_keys?.includes(key) && hasFactToken(current, key)) { setError('请先删除文内事实引用，再解除该事实绑定'); return }
    setContent((previous) => previous.map((block, index) => {
      if (index !== position - 1) return block
      const keys = block.fact_keys || []
      return { ...block, fact_keys: keys.includes(key) ? keys.filter((item) => item !== key) : [...keys, key] }
    }))
    candidateRequest.current += 1
    setPreview(null); setSectionPreview(null); setEditorKey((key) => key + 1)
  }
  const refreshFactToken = (position: number, key: string) => {
    const fact = report?.facts.find((item) => item.key === key)
    const impact = report?.fact_impacts.find((item) => item.position === position && item.fact_key === key)
    const current = content[position - 1]
    if (!fact || fact.value === null || !impact || !current) return
    const updated = updateFactToken(current, key, impact.before?.value ?? null, fact)
    if (!updated) { setError(`第 ${position} 段 ${fact.label} 的显示格式无法安全换算，请在 Plate 手工修改并核对`); return }
    setContent((previous) => previous.map((block, index) => index === position - 1 ? updated : block))
    candidateRequest.current += 1
    setEditorKey((value) => value + 1)
    setPreview(null); setSectionPreview(null)
    setError('')
  }
  const openSource = async (factKey: string) => {
    setError('')
    try { setSource(await api<FactSource>(`/projects/${project.id}/facts/${encodeURIComponent(factKey)}/source`)) }
    catch (cause) { setError((cause as Error).message) }
  }
  const openCorpusSource = async (ref: CorpusSourceRef) => {
    const categoryNumber = Number(ref.category_id.match(/\d+$/)?.[0])
    if (!categoryNumber) { setError('语料类别编号缺失，无法定位来源'); return }
    setError('')
    try {
      const [detail, reference] = await Promise.all([
        api<CorpusSourceDetail>(`/projects/${project.id}/writing/sources/${categoryNumber}/${encodeURIComponent(ref.record_id)}`),
        api<{ source_project_id: string }>(`/projects/${project.id}/writing/reference`),
      ])
      const impact = await api<{ impacts: CorpusSourceImpact[] }>(`/projects/${project.id}/writing/impact?record_id=${encodeURIComponent(detail.source_ref.record_id)}`)
      setCorpusSource({ ...detail, source_project_id: reference.source_project_id, impacts: impact.impacts })
    } catch (cause) { setError((cause as Error).message) }
  }
  const generate = async () => {
    if (!report || !selectedKeys.length || dirty) return
    const requestId = ++candidateRequest.current
    setBusy(true); setError(''); setSectionPreview(null)
    try {
      const candidate = await post<SectionPreview>(`/projects/${project.id}/reports/${report.id}/generate/preview`, { title: sectionTitle.trim(), fact_keys: selectedKeys })
      if (candidateRequest.current === requestId) setSectionPreview(candidate)
    }
    catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }
  const confirmGenerate = async () => {
    if (!report || !sectionPreview || dirty) return
    setBusy(true); setError('')
    try {
      await post(`/projects/${project.id}/reports/${report.id}/generate/commit`, sectionPreview)
      await loadReport(); await loadReports(); notify('章节已加入报告'); window.requestAnimationFrame(() => document.getElementById('report-editor')?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
    } catch (cause) { setSectionPreview(null); setError(`${(cause as Error).message}；请重新生成候选`) }
    finally { setBusy(false) }
  }
  const review = async () => {
    if (!report) return
    setBusy(true); setError('')
    try { await post(`/projects/${project.id}/reports/${report.id}/review`, {}); await loadReport(); notify('人工核对已记录；当前版本可检查导出') }
    catch (cause) { setError((cause as Error).message) } finally { setBusy(false) }
  }
  const compare = async () => {
    if (!report) return
    setError('')
    try { setComparison(await api<Comparison>(`/projects/${project.id}/reports/${report.id}/compare?base=${compareBase}`)) }
    catch (cause) { setError((cause as Error).message) }
  }
  const blockingIssues = report?.issues.filter((issue) => issue.severity === 'block') || []
  const reviewIssues = report?.issues.filter((issue) => issue.severity !== 'block') || []
  const scrollTo = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  return <main className="page"><div className="breadcrumb">项目 / {project.name} / 报告写作</div><div className="page-header"><h1>报告写作</h1>{report && <span className="status status-neutral">v{report.version} · {dirty ? '未保存' : report.reviewed ? '已核对' : '待核对'}</span>}</div>
    {error && <div className="notice error">{error}</div>}
    <div className="reports-layout"><aside className="workspace-card report-list"><h2>本项目报告</h2>{reports.map((item) => <button className={item.id === reportId ? 'active' : ''} key={item.id} onClick={() => { if (dirty && !window.confirm('当前修改尚未保存，确定切换报告？')) return; candidateRequest.current += 1; setSectionPreview(null); setReportId(item.id); window.localStorage.setItem(`report-platform-report:${project.id}`, item.id) }}><strong>{item.title}</strong><small>v{item.version}</small></button>)}<label className="form-field"><span>新报告名称</span><input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="如：项目情况报告" /></label><button className="primary-button" disabled={!title.trim() || busy} onClick={() => void create()}><Plus size={14} /> 创建报告</button></aside>
      <div className="reports-main">{report ? <>
        <nav className="report-flow-nav" aria-label="写作步骤">
          <button type="button" onClick={() => scrollTo('report-drafting')}>章节起草</button>
          <button type="button" onClick={() => scrollTo('report-editor')}>编辑正文</button>
          <button type="button" onClick={() => scrollTo('report-review')}>核对导出</button>
        </nav>
        <div id="report-drafting">
          <CorpusWritingPanel project={project} reportId={report.id} dirty={dirty} onEditFacts={onEditFacts} onJumpToReport={jumpToReport} onCommitted={async () => { await loadReport(); await loadReports(); notify('章节已加入报告'); window.requestAnimationFrame(() => scrollTo('report-editor')) }} />
        </div>
        <details className="workspace-card report-optional-draft">
          <summary>仅用本项目事实起草</summary>
          <label className="form-field"><span>章节标题</span><input value={sectionTitle} disabled={busy} onChange={(event) => { candidateRequest.current += 1; setSectionTitle(event.target.value); setSectionPreview(null) }} /></label>
          <div className="report-facts">{usable.length ? usable.map((fact) => <div key={fact.key}>
            <label><input type="checkbox" disabled={busy} checked={selectedKeys.includes(fact.key)} onChange={(event) => { candidateRequest.current += 1; setSelectedKeys((old) => event.target.checked ? [...old, fact.key] : old.filter((key) => key !== fact.key)); setSectionPreview(null) }} /><span><b>{fact.label}</b> · {fact.value}{fact.unit}</span></label>
          </div>) : <button type="button" className="text-button" onClick={onOpenProjectFacts}>录入项目事实 <Plus size={13} /></button>}</div>
          <div className="report-save-line"><button className="primary-button" disabled={busy || dirty || !sectionTitle.trim() || !selectedKeys.length} onClick={() => void generate()}><Sparkles size={14} /> {sectionPreview ? '重新生成候选' : '预览候选'}</button></div>
          {sectionPreview && <div className="report-preview"><strong>待确认候选 · {sectionPreview.title}</strong>
            {sectionPreview.paragraphs.map((paragraph, index) => <div key={index}><small>第 {index + 1} 段 · 关联 {paragraph.fact_keys?.join('、') || '无事实'}</small><p>{blockText(paragraph)}</p></div>)}
            <div className="inline-actions"><button type="button" onClick={() => setSectionPreview(null)}>取消候选</button><button type="button" className="primary-button" disabled={busy || dirty} onClick={() => void confirmGenerate()}><Check size={14} /> 加入报告</button></div>
          </div>}
        </details>
        <div id="report-editor" className="workspace-card"><div className="workspace-toolbar"><h2>{report.title}</h2><button className="subtle-button" onClick={() => { if (dirty && !window.confirm('放弃未保存修改并重新加载？')) return; void loadReport() }}><RefreshCw size={14} /> 刷新</button></div>
          {report.fact_impacts.length > 0 && <div className="report-impact-preview"><strong>过时引用 · {report.fact_impacts.length} 处</strong>{report.fact_impacts.map((impact, index) => <div key={`${impact.position}-${impact.fact_key}-${index}`}><small>第 {impact.position} 段 · {facts.find((fact) => fact.key === impact.fact_key)?.label || impact.fact_key}</small><span>{impact.before?.value ?? '未定义'} → {impact.after?.value ?? '未定义'}</span>{impact.after?.value !== null && impact.after && content[impact.position - 1] && hasFactToken(content[impact.position - 1], impact.fact_key) && <button className="subtle-button" type="button" onClick={() => refreshFactToken(impact.position, impact.fact_key)}>更新文内引用</button>}</div>)}</div>}
          <EditorPane key={`${report.id}-${editorKey}`} initial={content} actionsRef={editorActions} facts={usable} onOpenFact={(key) => void openSource(key)} staleFactKeys={report.fact_impacts.map((impact) => impact.fact_key)} onSelectPosition={setSelectedPosition} onChange={(next) => { candidateRequest.current += 1; setContent(next); setPreview(null); setSectionPreview(null) }} />
          {selectedPosition && content[selectedPosition - 1] && <div className="paragraph-binding"><div className="surface-heading"><strong>第 {selectedPosition} 段依据</strong></div>
            {(content[selectedPosition - 1].fact_keys || []).map((key) => <button type="button" className="source-button" key={key} onClick={() => void openSource(key)}>{facts.find((fact) => fact.key === key)?.label || key} · 来源</button>)}
            <RuleProvenance block={content[selectedPosition - 1]} />
            {(content[selectedPosition - 1].source_refs || []).length > 0 && <div className="report-corpus-refs"><small>历史参考 · 待核对</small>{(content[selectedPosition - 1].source_refs || []).map((ref, index) => <button type="button" className="source-button" key={`${ref.category_id}-${ref.record_id}-${index}`} onClick={() => void openCorpusSource(ref)}>{ref.category_id} · {ref.semantic_id || ref.record_id}</button>)}</div>}
            <details className="report-bind-menu"><summary>绑定项目事实</summary><div className="binding-choices">{usable.map((fact) => <button type="button" key={fact.key} className={(content[selectedPosition - 1].fact_keys || []).includes(fact.key) ? 'bound' : ''} onClick={() => bindFact(selectedPosition, fact.key)}>{fact.label} · {fact.value}{fact.unit}</button>)}</div></details>
          </div>}
          <div className="report-save-line"><span>{dirty ? '有未保存修改' : '当前正文已保存'}</span><button className="primary-button" disabled={(!dirty && report.fact_impacts.length === 0) || busy} onClick={() => void previewSave()}>{dirty ? '预览改动' : '预览引用核对'}</button></div>
          {preview && <div className="report-preview"><strong>保存预览 · {preview.changes.length} 处变化</strong>{preview.changes.length === 0 && report.fact_impacts.length > 0 && <div className="notice">正文未变 · 核对当前引用</div>}{preview.changes.map((change) => <div key={change.position}><small>第 {change.position} 段</small>{!change.before && !change.after ? <p>空段落或来源调整</p> : <><p>原文：{change.before || '（空）'}</p><p>提议：{change.after || '（删除）'}{change.before === change.after ? '（格式或来源变化）' : ''}</p></>}</div>)}<div className="inline-actions"><button onClick={() => setPreview(null)}>取消</button><button className="primary-button" disabled={busy} onClick={() => void save()}><Check size={14} /> 确认保存</button></div></div>}
        </div>
        <details className="workspace-card report-history"><summary>版本差异</summary><div className="toolbar"><select aria-label="比较历史版本" value={compareBase} onChange={(event) => setCompareBase(Number(event.target.value))}>{versions.map((version) => <option key={version.version} value={version.version}>v{version.version}{version.reviewed ? ' · 已核对' : ''}</option>)}</select><button className="subtle-button" onClick={() => void compare()}>与当前版本比较</button></div>{comparison && <div className="report-preview"><strong>v{comparison.base_version} → v{comparison.current_version} · {comparison.changes.length} 处变化</strong>{comparison.changes.length ? comparison.changes.map((change) => <div key={change.position}><small>第 {change.position} 段</small><p>旧版：{change.before || '（空）'}</p><p>当前：{change.after || '（删除）'}</p></div>) : <p>正文无变化</p>}</div>}</details>
        <div id="report-review" className="workspace-card"><div className="workspace-toolbar"><h2>核对与交付</h2></div>
          <div className="report-check-summary"><span className={`status ${blockingIssues.length ? 'status-warn' : 'status-neutral'}`}>{blockingIssues.length ? `正式导出阻断 ${blockingIssues.length}` : report.reviewed ? '正式导出可用' : '待人工核对'}</span>{reviewIssues.length > 0 && <span className="status status-neutral">保留 {reviewIssues.length}</span>}{dirty && <span className="status status-neutral">未保存</span>}{report.fact_impacts.length > 0 && <button type="button" className="text-button" onClick={() => scrollTo('report-editor')}>修正过时引用</button>}</div>
          {report.issues.length > 0 && <details className="report-issues"><summary>查看问题 · {report.issues.length}</summary>{report.issues.map((issue, index) => <div key={index} className={`notice ${issue.severity === 'block' ? 'warn' : ''}`}>{issue.severity === 'block' ? '阻断' : '保留'} · {issue.message}</div>)}</details>}
          {report.facts.length > 0 && <details className="report-bound-facts"><summary>引用事实 · {report.facts.length}</summary>{report.facts.map((fact) => <div key={fact.key}><strong>{fact.label} · {fact.value}{fact.unit}</strong><button className="source-button" type="button" onClick={() => void openSource(fact.key)}>来源</button>{sourceLink(project.id, fact) && <a href={sourceLink(project.id, fact)!} target="_blank" rel="noreferrer">打开原件</a>}</div>)}</details>}
          <div className="report-save-line report-delivery"><div className="inline-actions"><button type="button" onClick={onOpenDocuments}>项目文件</button><button type="button" onClick={onOpenProjectFacts}>项目事实</button></div><div className="inline-actions">{report.reviewed ? <span className="status status-neutral">已人工核对</span> : <button className="subtle-button" disabled={dirty || busy || blockingIssues.some((issue) => issue.code !== 'UNREVIEWED')} onClick={() => void review()}><Check size={14} /> 我已核对</button>}<a className={`subtle-button ${dirty ? 'disabled-link' : ''}`} aria-disabled={dirty} href={dirty ? undefined : `/api/projects/${project.id}/reports/${report.id}/export?level=preview`}><Download size={14} /> 导出预审稿</a><a className={`primary-button ${dirty || blockingIssues.length || !report.reviewed ? 'disabled-link' : ''}`} aria-disabled={dirty || blockingIssues.length > 0 || !report.reviewed} title={blockingIssues.length ? `正式导出阻断 ${blockingIssues.length} 项` : dirty ? '请先保存正文' : !report.reviewed ? '请先人工核对' : ''} href={dirty || blockingIssues.length || !report.reviewed ? undefined : `/api/projects/${project.id}/reports/${report.id}/export`}><Download size={14} /> 正式导出</a></div></div>
        </div>
      </> : null}</div></div>
    {corpusSource && <CorpusSourceDrawer source={corpusSource} onClose={() => setCorpusSource(null)} onJump={jumpToReport} />}
    {source && <FactSourceDrawer source={source} onClose={() => setSource(null)} onOpenFact={(key) => void openSource(key)} />}
  </main>
}
