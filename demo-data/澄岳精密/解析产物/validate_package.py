#!/usr/bin/env python3
"""Validate the native-format corpus package (PyYAML and thrift required).

Usage: python validate_package.py [package_directory]
Checks data and round-tripping, not semantic recall or project approval. The Parquet is schema-only.
"""
from __future__ import annotations
import ast
import csv
import io
import struct
import zipfile
import datetime
import hashlib
import json
import re
import sys
from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR, ROUND_CEILING, localcontext
from pathlib import Path
from typing import Any
import yaml
from thrift.Thrift import TType
from thrift.protocol.TCompactProtocol import TCompactProtocol
from thrift.transport.TTransport import TMemoryBuffer

class NotEvaluable(Exception):
    pass

def evaluate(expression: str, values: dict[str, Any]) -> Any:
    def visit(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return node.value
            if isinstance(node.value, (int, float)):
                return Decimal(str(node.value))
            raise ValueError('Unsupported constant')
        if isinstance(node, ast.Name):
            value = values.get(node.id)
            if value is None:
                raise NotEvaluable(node.id)
            return value
        if isinstance(node, ast.BinOp):
            a, b = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add): return a + b
            if isinstance(node.op, ast.Sub): return a - b
            if isinstance(node.op, ast.Mult): return a * b
            if isinstance(node.op, ast.Div):
                if b == 0: raise NotEvaluable('division_by_zero')
                return a / b
        if isinstance(node, ast.UnaryOp):
            value = visit(node.operand)
            if isinstance(node.op, ast.USub): return -value
            if isinstance(node.op, ast.UAdd): return value
            if isinstance(node.op, ast.Not): return not value
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            args = [visit(arg) for arg in node.args]
            if node.func.id == 'min' and args: return min(args)
            if node.func.id == 'max' and args: return max(args)
            if node.func.id == 'floor' and len(args) == 1:
                return args[0].to_integral_value(rounding=ROUND_FLOOR)
            if node.func.id == 'ceil' and len(args) == 1:
                return args[0].to_integral_value(rounding=ROUND_CEILING)
        if isinstance(node, ast.Compare):
            values_ = [visit(node.left)] + [visit(n) for n in node.comparators]
            for index, op in enumerate(node.ops):
                a, b = values_[index:index + 2]
                if isinstance(op, ast.GtE): result = a >= b
                elif isinstance(op, ast.LtE): result = a <= b
                elif isinstance(op, ast.Gt): result = a > b
                elif isinstance(op, ast.Lt): result = a < b
                elif isinstance(op, ast.Eq): result = a == b
                elif isinstance(op, ast.NotEq): result = a != b
                else: raise ValueError('Unsupported comparison')
                if not result: return False
            return True
        if isinstance(node, ast.BoolOp):
            results = [bool(visit(n)) for n in node.values]
            if isinstance(node.op, ast.And): return all(results)
            if isinstance(node.op, ast.Or): return any(results)
        raise ValueError(f'Unsupported expression: {type(node).__name__}')
    return visit(ast.parse(expression, mode='eval'))

def format_slot(slot: dict[str, Any], value: Any) -> str:
    fmt = slot['format']
    kind = fmt['kind']
    if kind == 'number':
        number = Decimal(str(value)) * Decimal(fmt.get('multiplier', '1'))
        places = fmt['decimals']
        number = number.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
        return format(number, (',' if fmt['thousands'] else '') + f'.{places}f') + fmt.get('suffix', '')
    if kind == 'boolean_text': return fmt['true'] if value else fmt['false']
    if kind == 'date':
        day = datetime.date.fromisoformat(value)
        # Non-zero-padded date directives are not portable to Windows strftime.
        pattern = fmt['pattern'].replace('%-m', '__MONTH__').replace('%-d', '__DAY__')
        return day.strftime(pattern).replace('__MONTH__', str(day.month)).replace('__DAY__', str(day.day))
    if kind == 'period':
        a, b = value.split('/')
        return datetime.date.fromisoformat(a).strftime('%Y.%m') + '—' + datetime.date.fromisoformat(b).strftime('%Y.%m')
    if kind == 'chinese_integer':
        return {0:'零',1:'一',2:'两',3:'三',4:'四',5:'五',6:'六',7:'七',8:'八',9:'九',10:'十'}.get(int(value), str(value))
    if kind == 'text_transform': return str(value).removeprefix(fmt['remove_prefix'])
    if kind == 'text_layout':
        word = fmt['insert_line_break_after']
        return str(value).replace(word, word + '\n', 1)
    return str(value)

def check(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)

def read_thrift_value(proto, kind):
    if kind == TType.STRUCT:
        result={}; proto.readStructBegin()
        while True:
            _, field_type, field_id=proto.readFieldBegin()
            if field_type == TType.STOP: break
            result[field_id]=read_thrift_value(proto, field_type)
            proto.readFieldEnd()
        proto.readStructEnd(); return result
    if kind == TType.LIST:
        element_type, count=proto.readListBegin()
        check(0 <= count <= 100000, 'Invalid Parquet list count')
        values=[read_thrift_value(proto,element_type) for _ in range(count)]
        proto.readListEnd(); return values
    if kind == TType.I32: return proto.readI32()
    if kind == TType.I64: return proto.readI64()
    if kind == TType.STRING: return proto.readString()
    raise ValueError(f'Unsupported schema-only Parquet field type: {kind}')

def validate_empty_parquet(file: Path, chunk_ids: list[str]) -> dict:
    raw=file.read_bytes()
    check(len(raw)>=12 and raw[:4]==b'PAR1' and raw[-4:]==b'PAR1','Invalid Parquet magic')
    length=struct.unpack('<I',raw[-8:-4])[0]
    check(length==len(raw)-12,'Unexpected data in schema-only Parquet')
    proto=TCompactProtocol(TMemoryBuffer(raw[4:-8]))
    metadata=read_thrift_value(proto,TType.STRUCT)
    check(metadata[1]==1 and metadata[3]==0 and metadata[4]==[],'Parquet must contain zero rows/groups')
    schema=metadata[2]
    def walk(i):
        element=schema[i]
        if 5 in element:
            check(1 not in element,'Group cannot have physical type')
            j=i+1
            for _ in range(element[5]): j=walk(j)
            return j
        check(1 in element and 3 in element,'Invalid primitive schema element')
        return i+1
    check(walk(0)==len(schema),'Invalid flattened Parquet schema tree')
    names=[e[4] for e in schema]
    check('embedding' in names and 'chunk_id' in names and 'status' in names,'Required columns missing')
    extra={row[1]:row[2] for row in metadata[5]}
    check(extra['status']=='not_generated' and extra['vector_count']=='0','False vector generation status')
    check(json.loads(extra['expected_chunk_ids_json'])==chunk_ids,'Pending embedding chunk IDs changed')
    # A full Parquet engine is optional; footer/tree validation always runs.
    engine='not_installed; checked_Thrift_footer_and_schema_tree'
    try:
        import pyarrow.parquet as pq
    except ImportError:
        pass
    else:
        table=pq.read_table(file)
        check(table.num_rows==0,'Unexpected embedding rows')
        engine='pyarrow_read_table_pass'
    return {'row_count':0,'vector_count':0,'status':'not_generated','schema_tree_verified':True,'engine_check':engine}

def main(directory: Path) -> dict:
    directory=directory.resolve()
    def load(name: str) -> Any:
        p=directory/name
        if p.suffix == '.yaml': return yaml.safe_load(p.read_text(encoding='utf-8'))
        if p.suffix == '.json': return json.loads(p.read_text(encoding='utf-8'))
        if p.suffix == '.jsonl':
            lines=p.read_text(encoding='utf-8').splitlines()
            check(all(lines), f'Empty JSONL record: {name}')
            result=[json.loads(line) for line in lines]
            check(all(isinstance(row,dict) for row in result),f'JSONL records must be objects: {name}')
            return result
        raise ValueError('Unknown structured file format')
    manifest=load('manifest.yaml')
    check(manifest['artifact_category_count']==30 and len(manifest['files'])==30,'Expected 30 categories')
    check([f['no'] for f in manifest['files']]==list(range(1,31)),'Artifact numbering differs')
    covered=set()
    for entry in manifest['files']:
        members=entry.get('files',[entry])
        check(len(members)==entry['physical_file_count'],f'Count mismatch: {entry["path"]}')
        for item in members:
            p=directory/item['path']
            check(p.is_file(),f'Missing file: {item["path"]}')
            check(directory in p.resolve().parents,'Path escapes package')
            check(item['path'] not in covered,'Repeated artifact file')
            covered.add(item['path'])
            if item.get('sha256'):
                check(hashlib.sha256(p.read_bytes()).hexdigest()==item['sha256'],f'Hash mismatch: {item["path"]}')
            if p.suffix in {'.yaml','.json','.jsonl'}: load(item['path'])
    check(len(covered)==manifest['physical_artifact_file_count'],'Physical artifact count mismatch')
    sums=directory/'SHA256SUMS'
    if sums.exists():
        for line in sums.read_text('utf-8').splitlines():
            digest,name=line.split('  ',1)
            check(hashlib.sha256((directory/name).read_bytes()).hexdigest()==digest,f'Checksum mismatch: {name}')
    provenance=load('provenance.yaml')
    original=directory/'source/original.docx'
    check(hashlib.sha256(original.read_bytes()).hexdigest()==provenance['source_sha256'],'Original DOCX changed')
    check(original.stat().st_size==provenance['source_byte_size'],'Original byte count changed')
    with zipfile.ZipFile(original) as archive:
        check(archive.testzip() is None,'Corrupt source DOCX')
        check('word/document.xml' in archive.namelist(),'Not a Word OOXML document')
    raw=(directory/'source/normalized.md').read_bytes()
    text=raw.decode('utf-8')
    digest=hashlib.sha256(raw).hexdigest()
    check('sha256:'+digest==manifest['normalized_fingerprint'],'Normalized Markdown changed')
    chunks=load('chunks/chunks.jsonl')
    check(''.join(c['text']+'\n\n' for c in chunks)==text,'Normalized text reconstruction failed')
    chunk_map={c['id']:c for c in chunks}
    check(len(chunk_map)==len(chunks),'Duplicate chunk IDs')
    for c in chunks:
        a,b=c['span'];check(text[a:b]==c['text'],f'Wrong span: {c["id"]}')
    offsets=load('source/offset_map.json')
    check(offsets['normalized_sha256']==digest,'Offset file hash mismatch')
    for entry in offsets['entries']:
        a,b=entry['normalized_span']
        check(text[a:b]==chunk_map[entry['chunk_id']]['text'],'Offset map block differs')
    node_list=load('graph/nodes.yaml');nodes={n['id']:n for n in node_list}
    check(len(nodes)==len(node_list),'Duplicate nodes')
    values={}
    for n in node_list:
        v=n['value']
        values[n['id']]=Decimal(n.get('decimal_value',str(v))) if n['kind']=='quantity' and v is not None else v
    rules=load('graph/rules.yaml');target_to_rule={r['target']:r['id'] for r in rules}
    check(len(target_to_rule)==len(rules),'More than one rule for a target')
    pending,done=list(rules),set()
    with localcontext() as context:
        context.prec=50
        while pending:
            progressed=False
            for rule in list(pending):
                if any(d in target_to_rule and target_to_rule[d] not in done for d in rule['deps']): continue
                check(all(d in nodes for d in rule['deps']),'Missing dependency node')
                try:
                    if any(values.get(d) is None for d in rule['deps']): raise NotEvaluable('missing_dep')
                    guard=rule['guard'].get('additional_expr')
                    if guard and not evaluate(guard,values): raise NotEvaluable('guard_not_satisfied')
                    result=evaluate(rule['expr'],values)
                except NotEvaluable:
                    result=None
                check(result==values[rule['target']],f'Baseline differs: {rule["id"]}')
                values[rule['target']]=result
                done.add(rule['id']);pending.remove(rule);progressed=True
            check(progressed,'Unresolved dependency cycle')
    mentions=0;mention_ids=set();mention_slots={}
    for c in load('chunks/skeletons.jsonl'):
        result=c['skeleton_md']
        check(not re.search(r'\d',re.sub(r'\{\{node:N\d+\}\}','',result)),f'Unmasked digit: {c["id"]}')
        for slot in reversed(c['node_slots']):
            a,b=slot['skeleton_span']
            check(result[a:b]=='{{node:'+slot['slot']+'}}','Invalid slot')
            result=result[:a]+format_slot(slot,values[slot['slot']])+result[b:]
            mentions+=1;mention_ids.add(slot['mention_id']);mention_slots[slot['mention_id']]=slot
        check(result==chunk_map[c['id']]['text'],f'Skeleton round-trip differs: {c["id"]}')
    node_index=load('index/node_index.json');index_mentions=set()
    check(set(node_index)==set(nodes),'Node index membership mismatch')
    for node_id,entry in node_index.items():
        for mention in entry['mentions']:
            a,b=mention['char_span']
            check(mention['node_id']==node_id or node_id in mention_slots[mention['id']].get('also_supports_node_ids',[]),'Wrong mention node or undeclared shared mention')
            check(text[a:b]==mention['surface'],f'Mention text differs: {mention["id"]}')
            check(mention['chunk_id'] in chunk_map,'Unknown mention chunk')
            index_mentions.add(mention['id'])
    check(mention_ids==index_mentions,'Index and skeleton mention IDs differ')
    evidence=load('evidence/evidence.yaml');excerpt_count=0
    for e in evidence:
        check((directory/e['source_document']).is_file(),'Evidence source missing')
        for x in e['excerpts']:
            xraw=(directory/x['path']).read_bytes()
            a,b=x['span']
            check(text[a:b]==xraw.decode('utf-8'),f'Excerpt differs: {x["id"]}')
            check(hashlib.sha256(xraw).hexdigest()==x['source_text_sha256'],'Excerpt hash mismatch')
            excerpt_count+=1
    for a in provenance['artifact_metadata']['source/assets/']['items']:
        content=(directory/a['path']).read_bytes()
        check(hashlib.sha256(content).hexdigest()==a['csv_sha256'],'CSV hash mismatch')
        rows=list(csv.reader(io.StringIO(content.decode('utf-8'),newline='')))
        check(len(rows)==a['row_count'] and all(len(row)==a['column_count'] for row in rows),'CSV matrix changed')
        check(a['chunk_id'] in chunk_map,'CSV source chunk not found')
    graph=load('graph/graph.yaml')
    for name in ['nodes','rules','relations','claims','invariants']:
        check(graph[name]==load('graph/'+name+'.yaml'),f'Merged graph differs: {name}')
    check(graph['sections']==load('outline.yaml')['sections'],'Merged outline differs')
    ids=set(nodes)|set(chunk_map)|{r['id'] for r in rules}
    for key in ['sections','claims','evidence_nodes','conflicts','gaps','invariants']:ids.update(row['id'] for row in graph[key])
    for edge in graph['relations']:
        check(edge['from_id'] in ids and edge['to_id'] in ids,f'Dangling relation: {edge["id"]}')
    for e in graph['evidence_nodes']:
        name,ref=e['detail_ref'].split('#',1)
        check((directory/name).is_file() and ref in {x['id'] for x in evidence},'Evidence pointer not rewritten')
    templates=list((directory/'style/section_templates').glob('*.yaml'))
    sections={s['id'] for s in graph['sections']}
    for p in templates:
        obj=load(p.relative_to(directory).as_posix())
        check(obj['section_id'] in sections,'Unknown template section')
        for item in obj['paragraph_order']:
            name,ref=item['skeleton_ref'].split('#',1)
            check(name=='chunks/skeletons.jsonl' and ref in chunk_map,'Old skeleton pointer remained')
    parquet=validate_empty_parquet(directory/'chunks/embeddings.parquet',[c['id'] for c in chunks])
    report={'status':'validated','artifact_categories':30,'artifact_files':len(covered),
      'nodes':len(nodes),'rules_replayed':len(done),'chunks_restored':len(chunks),'mentions_restored':mentions,
      'excerpt_spans_verified':excerpt_count,'table_csv_files':len(list((directory/'source/assets').glob('*.csv'))),
      'section_template_files':len(templates),'original_docx_hash_verified':True,'normalized_text_unchanged':True,
      'offsets_and_indexes_verified':True,'merged_graph_matches':True,'file_references_verified':True,
      'parquet':parquet,'note':'Mechanical checks only; no independent semantic gold set or project approval.'}
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return report

if __name__=='__main__':
    try:
        main(Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).resolve().parent)
    except (ValueError,KeyError,OSError,json.JSONDecodeError,yaml.YAMLError) as exc:
        print(f'VALIDATION FAILED: {exc}',file=sys.stderr)
        sys.exit(1)
