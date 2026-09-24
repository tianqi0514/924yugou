import { useCallback, useEffect, useMemo, useState } from 'react'
import { ReactFlow, Background, Controls, MarkerType, Position, type Edge, type Node } from '@xyflow/react'
import dagre from 'dagre'
import { ArrowRight, Focus, GitBranch, List, Search, X } from 'lucide-react'
import { api, corpusPath, type GraphData, type GraphEdge, type GraphEntity } from './api'
import '@xyflow/react/dist/style.css'

const kinds: Record<string, { background: string; border: string; color: string }> = {
  事实: { background: '#eaf1ff', border: '#9ebaff', color: '#2551ad' },
  规则: { background: '#eaf7f3', border: '#9cdcc9', color: '#116b55' },
  论断: { background: '#fff4e7', border: '#f2c78e', color: '#92521e' },
  证据: { background: '#f3edff', border: '#c9b3f2', color: '#66439d' },
  正文: { background: '#f4f6f9', border: '#cfd6e2', color: '#3c4b62' },
  章节: { background: '#e9f7fb', border: '#a2d8e7', color: '#17677d' },
  冲突: { background: '#fff0f0', border: '#efb0b0', color: '#af3333' },
  缺口: { background: '#fff5ed', border: '#f4c39c', color: '#a55f2a' },
}

function shortestPath(edges: GraphEdge[], start: string, end: string): { nodes: Set<string>; edges: Set<string> } {
  const queue = [start]
  const previous = new Map<string, { id: string; edge: string }>()
  const visited = new Set([start])
  while (queue.length) {
    const current = queue.shift()!
    if (current === end) break
    for (const edge of edges) {
      if (edge.source !== current && edge.target !== current) continue
      const next = edge.source === current ? edge.target : edge.source
      if (!visited.has(next)) {
        visited.add(next)
        previous.set(next, { id: current, edge: edge.id })
        queue.push(next)
      }
    }
  }
  const nodes = new Set<string>(), pathEdges = new Set<string>()
  if (!visited.has(end)) return { nodes, edges: pathEdges }
  for (let current = end; current !== start;) {
    nodes.add(current)
    const step = previous.get(current)!
    pathEdges.add(step.edge)
    current = step.id
  }
  nodes.add(start)
  return { nodes, edges: pathEdges }
}

function layout(data: GraphData, highlighted: { nodes: Set<string>; edges: Set<string> }): { nodes: Node[]; edges: Edge[] } {
  const graph = new dagre.graphlib.Graph()
  graph.setGraph({ rankdir: 'LR', ranksep: 100, nodesep: 34, marginx: 28, marginy: 28 })
  graph.setDefaultEdgeLabel(() => ({}))
  for (const node of data.nodes) graph.setNode(node.id, { width: 174, height: 60 })
  for (const edge of data.edges) graph.setEdge(edge.source, edge.target)
  dagre.layout(graph)
  const selected = highlighted.edges.size > 0
  return {
    nodes: data.nodes.map((entity) => {
      const point = graph.node(entity.id)
      const palette = kinds[entity.kind] || kinds.正文
      return {
        id: entity.id,
        position: { x: point.x - 87, y: point.y - 30 },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        data: { label: <div className="graph-node"><b>{entity.id}</b><span>{entity.label}</span></div> },
        style: { width: 174, minHeight: 60, background: palette.background, border: `1px solid ${palette.border}`, color: palette.color, borderRadius: 10, opacity: selected && !highlighted.nodes.has(entity.id) ? 0.32 : 1, boxShadow: entity.id === data.focus ? '0 0 0 3px #d9e6ff' : undefined },
      }
    }),
    edges: data.edges.map((relation) => ({
      id: relation.id, source: relation.source, target: relation.target, label: relation.type,
      markerEnd: { type: MarkerType.ArrowClosed, color: highlighted.edges.has(relation.id) ? '#2563eb' : '#a4afbf' },
      style: { stroke: highlighted.edges.has(relation.id) ? '#2563eb' : '#b7c2d1', strokeWidth: highlighted.edges.has(relation.id) ? 2.7 : 1.2,
        strokeDasharray: relation.derivation === 'derived' ? '4 3' : undefined,
        opacity: selected && !highlighted.edges.has(relation.id) ? 0.22 : 1 },
      labelStyle: { fontSize: 10, fill: '#75839a' },
      labelBgStyle: { fill: '#fff', fillOpacity: 0.88 },
      type: 'smoothstep',
    })),
  }
}

export default function GraphPanel({ projectId, initialFocus = 'N034', onEntity }: { projectId: string; initialFocus?: string; onEntity?: (entity: GraphEntity) => void }) {
  const [focus, setFocus] = useState(initialFocus)
  const [query, setQuery] = useState(initialFocus)
  const [depth, setDepth] = useState(1)
  const [edgeType, setEdgeType] = useState('')
  const [nodeKind, setNodeKind] = useState('')
  const [direction, setDirection] = useState('both')
  const [matches, setMatches] = useState<GraphEntity[]>([])
  const [data, setData] = useState<GraphData | null>(null)
  const [view, setView] = useState<'graph' | 'list'>('graph')
  const [selected, setSelected] = useState<GraphEntity | null>(null)
  const [error, setError] = useState('')

  useEffect(() => { setFocus(initialFocus); setQuery(initialFocus); setSelected(null) }, [initialFocus])
  useEffect(() => {
    let active = true
    const params = new URLSearchParams({ focus, depth: String(depth), direction })
    if (edgeType) params.set('edge_types', edgeType)
    if (nodeKind) params.set('node_kinds', nodeKind)
    api<GraphData>(corpusPath(projectId, `/graph?${params}`)).then((result) => { if (active) { setData(result); setError('') } }).catch((cause: Error) => { if (active) setError(cause.message) })
    return () => { active = false }
  }, [projectId, focus, depth, edgeType, nodeKind, direction])

  useEffect(() => {
    if (!query.trim() || query === focus) { setMatches([]); return }
    let active = true
    api<GraphEntity[]>(corpusPath(projectId, `/graph/search?q=${encodeURIComponent(query.trim())}`)).then((result) => { if (active) setMatches(result) }).catch(() => { if (active) setMatches([]) })
    return () => { active = false }
  }, [projectId, query, focus])
  const locate = () => { setFocus(matches[0]?.id || query.trim()); setMatches([]) }

  const highlighted = useMemo(() => selected && data ? shortestPath(data.edges, focus, selected.id) : { nodes: new Set<string>(), edges: new Set<string>() }, [selected, data, focus])
  const elements = useMemo(() => data ? layout(data, highlighted) : { nodes: [], edges: [] }, [data, highlighted])
  const handleNode = useCallback((_event: React.MouseEvent, node: Node) => {
    const entity = data?.nodes.find((item) => item.id === node.id) || null
    setSelected(entity)
  }, [data])

  return <div className="graph-panel">
    <div className="graph-toolbar">
      <div className="graph-search"><Search size={15} /><input aria-label="搜索图谱 ID 或名称" value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') locate() }} placeholder="搜索 ID 或名称" /><button className="icon-button" onClick={locate} title="定位"><ArrowRight size={16} /></button>{matches.length > 0 && <div className="graph-suggestions">{matches.map((item) => <button key={item.id} onClick={() => { setFocus(item.id); setQuery(item.id); setMatches([]) }}><b>{item.id}</b><span>{item.label}</span><small>{item.kind}</small></button>)}</div>}</div>
      <select aria-label="邻居跳数" value={depth} onChange={(event) => setDepth(Number(event.target.value))}><option value={1}>1 跳邻居</option><option value={2}>2 跳邻居</option></select>
      <select aria-label="关系类型" value={edgeType} onChange={(event) => setEdgeType(event.target.value)}><option value="">全部关系</option>{(data?.edge_type_options || []).map((type) => <option key={type} value={type}>{type}</option>)}</select>
      <select aria-label="对象类型" value={nodeKind} onChange={(event) => setNodeKind(event.target.value)}><option value="">全部对象</option>{(data?.node_kind_options || []).map((kind) => <option key={kind} value={kind}>{kind}</option>)}</select>
      <select aria-label="关系方向" value={direction} onChange={(event) => setDirection(event.target.value)}><option value="both">双向邻居</option><option value="out">沿边向下</option><option value="in">沿边向上</option></select>
      <div className="segmented"><button className={view === 'graph' ? 'active' : ''} onClick={() => setView('graph')}><GitBranch size={15} /> 图谱</button><button className={view === 'list' ? 'active' : ''} onClick={() => setView('list')}><List size={15} /> 列表</button></div>
    </div>
    {error ? <div className="notice error">{error}</div> : !data ? <div className="empty">正在加载图谱…</div> : <>
      <div className="graph-stats"><span>{data.nodes.length} 个对象 · {data.edges.length} 条关系</span><span>原始 {data.raw_relation_count.toLocaleString()} · 派生 {data.derived_edge_count}</span>{data.truncated && <span className="text-warning">已限量</span>}</div>
      {view === 'graph' ? <div className="graph-canvas"><ReactFlow nodes={elements.nodes} edges={elements.edges} fitView fitViewOptions={{ padding: 0.16 }} onNodeClick={handleNode} nodesDraggable={false} minZoom={0.15} maxZoom={1.8}><Background color="#e8edf5" gap={20} /><Controls /></ReactFlow></div> : <div className="graph-edge-list">{data.edges.length ? data.edges.map((edge) => <button key={edge.id} className="edge-row" onClick={() => { const entity = data.nodes.find((item) => item.id === edge.target); if (entity) setSelected(entity) }}><span>{edge.source}</span><small>{edge.type} →</small><span>{edge.target}</span><em>{edge.derivation === 'derived' ? '字段派生' : '原始关系'} · {edge.origin}</em></button>) : <div className="empty">当前筛选没有关系</div>}</div>}
      {selected && <div className="graph-selection"><div><strong>{selected.id}</strong><span className="pill">{selected.kind}</span><span>{selected.label}</span></div><div className="inline-actions"><button onClick={() => { setFocus(selected.id); setQuery(selected.id); setSelected(null) }}><Focus size={15} /> 以此为中心</button>{onEntity && <button onClick={() => onEntity(selected)}>查看详情</button>}<button className="icon-button" onClick={() => setSelected(null)} aria-label="关闭选择"><X size={16} /></button></div></div>}
      <div className="graph-legend">{Object.entries(kinds).map(([kind, palette]) => <span key={kind}><i style={{ background: palette.background, borderColor: palette.border }} />{kind}</span>)}</div>
    </>}
  </div>
}
