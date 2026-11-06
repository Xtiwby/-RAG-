"""Bounded, offline OOXML template inspection. No Office, macros or external fetches."""
import hashlib
import io
import asyncio
import json
import re
import secrets
import zipfile
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree as ET
from fastapi import Depends,File,HTTPException,UploadFile,Request
from fastapi.responses import FileResponse
from pydantic import Field
from . import blueprints,settings,store as s,object_storage

W='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
PLACEHOLDER=re.compile(r'\{\{\s*([^{}\n]{1,100}?)\s*\}\}|\$\{\s*([^{}\n]{1,100}?)\s*\}|<<\s*([^<>\n]{1,100}?)\s*>>')
MAX_FILE=20*1024*1024

def parse_docx(data):
    warnings=[];blocks=[];fields={};styles={}
    def warn(message):
        if message not in warnings:warnings.append(message)
    def xml(z,name):
        raw=z.read(name)
        scan=raw.replace(b'\x00',b'').upper()
        if b'<!DOCTYPE' in scan or b'<!ENTITY' in scan:
            raise ValueError('不接受包含 DTD 或实体声明的 XML')
        return ET.fromstring(raw)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            entries=z.infolist()
            if len(entries)>2000 or sum(e.file_size for e in entries)>64*1024*1024 or len({e.filename for e in entries})!=len(entries):
                raise ValueError('DOCX 解压大小、条目数量超限或存在重复条目')
            if any(e.flag_bits&1 for e in entries):raise ValueError('不支持加密 DOCX')
            root=xml(z,'word/document.xml')
            body=root.find(W+'body')
            if body is None:raise ValueError('未找到受支持的 Word 正文结构')
            if 'word/styles.xml' in z.namelist():
                for el in xml(z,'word/styles.xml').findall(W+'style'):
                    styles[el.get(W+'styleId')]=el
            def level(p):
                prop=p.find(W+'pPr');direct=prop.find(W+'outlineLvl') if prop is not None else None
                if direct is not None:
                    n=int(direct.get(W+'val','9'));return n+1 if 0<=n<=8 else 0
                sty=prop.find(W+'pStyle') if prop is not None else None
                sid=sty.get(W+'val','') if sty is not None else '';seen=set()
                while sid and sid not in seen:
                    seen.add(sid);el=styles.get(sid)
                    if el is None:return 0
                    outline=el.find(W+'pPr/'+W+'outlineLvl')
                    if outline is not None:
                        n=int(outline.get(W+'val','9'));return n+1 if 0<=n<=8 else 0
                    name=el.find(W+'name');label=name.get(W+'val','') if name is not None else sid
                    match=re.fullmatch(r'(?:heading|标题)\s*([1-9])',label,re.I)
                    if match:return int(match[1])
                    parent=el.find(W+'basedOn');sid=parent.get(W+'val','') if parent is not None else ''
                if sid in seen and sid:warn('样式继承存在循环，相关段落需要手动校正标题级别')
                return 0
            def add_field(key,bid,syntax,start=None,end=None):
                key=key.strip()
                if not key:return
                if len(fields)>=1000 and key not in fields:raise ValueError('模板占位符超过1000项')
                if key not in fields:fields[key]={'key':key,'occurrences':[]}
                occurrence={'block_id':bid,'syntax':syntax}
                if start is not None:occurrence.update(start=start,end=end)
                fields[key]['occurrences'].append(occurrence)
            def para(p,part,table=None):
                pieces=[];controls=[];stack=[]
                def offset():return sum(len(v) for v in pieces)
                def merge_key(instr):
                    match=re.search(r'MERGEFIELD\s+(?:"([^"]+)"|([^\s\\]+))',instr,re.I)
                    return (match[1] or match[2]) if match else None
                def extract(node):
                    if node.tag==W+'del':return
                    if node.tag==W+'fldSimple':
                        start=offset()
                        for child in node:extract(child)
                        key=merge_key(node.get(W+'instr',''))
                        if key:controls.append((key,'MERGEFIELD',start,offset()))
                        return
                    if node.tag==W+'sdt':
                        start=offset();content=node.find(W+'sdtContent')
                        if content is not None:
                            for child in content:extract(child)
                        tag=node.find(W+'sdtPr/'+W+'tag');alias=node.find(W+'sdtPr/'+W+'alias')
                        key=tag.get(W+'val') if tag is not None else alias.get(W+'val') if alias is not None else None
                        if key:controls.append((key,'内容控件',start,offset()))
                        return
                    if node.tag==W+'fldChar':
                        action=node.get(W+'fldCharType')
                        if action=='begin':stack.append({'instr':'','start':offset()})
                        elif action=='separate' and stack:stack[-1]['start']=offset()
                        elif action=='end' and stack:
                            value=stack.pop();key=merge_key(value['instr'])
                            if key:controls.append((key,'MERGEFIELD',value['start'],offset()))
                        return
                    if node.tag==W+'instrText' and stack:stack[-1]['instr']+=node.text or ''
                    if node.tag==W+'t':pieces.append(node.text or '')
                    elif node.tag==W+'tab':pieces.append('\t')
                    elif node.tag in (W+'br',W+'cr'):pieces.append('\n')
                    for child in node:extract(child)
                extract(p);text=''.join(pieces)
                bid='b'+str(len(blocks)+1);lvl=level(p)
                segments=[];last=0
                for match in PLACEHOLDER.finditer(text):
                    if match.start()>last:segments.append({'type':'fixed','text':text[last:match.start()]})
                    segments.append({'type':'placeholder','key':next(v for v in match.groups() if v is not None),'text':match.group()});last=match.end()
                if last<len(text):segments.append({'type':'fixed','text':text[last:]})
                block={'id':bid,'type':'heading' if lvl and part=='body' and not table else 'paragraph','level':lvl,'text':text,'segments':segments,'part':part,'table':table}
                blocks.append(block)
                for match in PLACEHOLDER.finditer(text):add_field(next(v for v in match.groups() if v is not None),bid,match.group())
                instructions=' '.join(n.text or '' for n in p.iter(W+'instrText'))
                instructions+=' '+' '.join(n.get(W+'instr','') for n in p.iter(W+'fldSimple'))
                for key,syntax,start,end in controls:add_field(key,bid,syntax,start,end)
                if instructions.strip() and not re.search(r'MERGEFIELD',instructions,re.I):warn('存在 Word 动态域（如目录或页码），仅提取缓存文字，不执行域指令')
                if p.find('.//'+W+'drawing') is not None or p.find('.//'+W+'pict') is not None:warn('存在图片、图形或文本框；版面、图中文字不作为可靠解析结果，请人工检查原文件')
                return bid
            tables=[];cell_count=0
            def walk(parent,part='body',table=None):
                nonlocal cell_count
                ids=[]
                for node in parent:
                    if len(blocks)>=3000:raise ValueError('模板超过3000个段落，请拆分后导入')
                    if node.tag==W+'p':ids.append(para(node,part,table))
                    elif node.tag==W+'tbl':
                        if len(tables)>=100:raise ValueError('模板超过100个表格')
                        tid='t'+str(len(tables)+1);record={'id':tid,'part':part,'parent_table':table,'grid_columns':len(node.findall(W+'tblGrid/'+W+'gridCol')),'rows':[]};tables.append(record)
                        for ri,row in enumerate(node.findall(W+'tr')):
                            cells=[];column=0
                            for ci,cell in enumerate(row.findall(W+'tc')):
                                cell_count+=1
                                if cell_count>5000:raise ValueError('模板超过5000个表格单元格')
                                prop=cell.find(W+'tcPr');span=prop.find(W+'gridSpan') if prop is not None else None;merge=prop.find(W+'vMerge') if prop is not None else None
                                width=int(span.get(W+'val','1')) if span is not None else 1
                                if not 1<=width<=100:raise ValueError('表格合并列数无效或超限')
                                cellids=walk(cell,part,tid)
                                cells.append({'row':ri,'column':column,'column_span':width,'vertical_merge':merge.get(W+'val','continue') if merge is not None else '', 'block_ids':cellids})
                                column+=width
                            record['rows'].append({'index':ri,'header':row.find(W+'trPr/'+W+'tblHeader') is not None,'cells':cells})
                        ids.append(tid)
                    elif node.tag==W+'sdt':
                        start=len(blocks);content=node.find(W+'sdtContent')
                        if content is not None:ids.extend(walk(content,part,table))
                        tag=node.find(W+'sdtPr/'+W+'tag');alias=node.find(W+'sdtPr/'+W+'alias')
                        key=tag.get(W+'val') if tag is not None else alias.get(W+'val') if alias is not None else None
                        if key:
                            for b in blocks[start:]:add_field(key,b['id'],'内容控件')
                        warn('内容控件按标签和缓存内容解析，绑定、重复及条件逻辑不执行')
                    elif node.tag in (W+'ins',W+'del',W+'customXml'):
                        warn('存在修订或自定义 XML，请先核对已接受/删除内容；已删除段落不作为正文')
                        if node.tag!=W+'del':ids.extend(walk(node,part,table))
                    elif node.tag not in (W+'sectPr',W+'tcPr'):warn('存在未完整支持的正文结构，需对照原文件人工核查')
                return ids
            body_order=walk(body)
            for name in sorted(z.namelist()):
                if re.fullmatch(r'word/(header|footer)\d+\.xml',name):
                    walk(xml(z,name),name.split('/')[-1])
                    warn('页眉页脚文字已单独提取，页布局与特殊域不自动重建')
                if name.endswith('.rels'):
                    for rel in xml(z,name):
                        if rel.get('TargetMode')=='External':warn('包含外部关系，仅记录警告，不访问链接或外部内容')
                if any(t in name for t in ('vbaProject','embeddings/','activeX/')):warn('存在宏或嵌入对象，不执行，不解析其内容')
            # Run-level content controls are inside a paragraph, not block-level.
            for node in root.iter(W+'sdt'):
                tag=node.find(W+'sdtPr/'+W+'tag');alias=node.find(W+'sdtPr/'+W+'alias')
                key=tag.get(W+'val') if tag is not None else alias.get(W+'val') if alias is not None else None
                if key and key not in fields:
                    add_field(key,'inline-control','内容控件');warn('行内内容控件已识别，需核对字段映射')
            if not any(b['type']=='heading' for b in blocks):warn('未检测到 Word 标题样式或大纲级别，请手动指定正文段落的标题级别')
            if not fields:warn('未检测到受支持的占位符（{{字段}}、${字段}、<<字段>>、MERGEFIELD或内容控件）')
            if root.find('.//'+W+'del') is not None:warn('文档含删除修订，已删除文字不作为正文；请对照原文件核对修订')
            if root.find('.//'+W+'altChunk') is not None:warn('外部导入块不解析、不执行，请先在Word中转换为普通正文')
            return {'schema_version':3,'body_order':body_order,'blocks':blocks,'tables':tables,'fields':list(fields.values()),'warnings':warnings}
    except (zipfile.BadZipFile,KeyError,ET.ParseError,ValueError,RuntimeError,NotImplementedError,EOFError,OSError) as e:
        raise HTTPException(422,'无法解析 DOCX 模板：'+str(e)[:160]) from None

class HeadingCorrection(settings.Strict):
    block_id:str
    level:int=Field(ge=0,le=9)  # 0 means fixed body text.

class Mapping(settings.Strict):
    key:str=Field(min_length=1,max_length=100)
    target:Literal['section','report_title','knowledge_base','generated_at','period','user_input','literal','ignore']
    section_block_id:str=''
    value:str=Field(default='',max_length=2000)
    required:bool=False
    input_type:Literal['text','date','number']='text'

class SaveImport(settings.Strict):
    name:str=Field(min_length=1,max_length=100)
    description:str=Field(default='',max_length=1000)
    purpose:str=Field(default='',max_length=2000)
    version:str=Field(default='v1.0',min_length=1,max_length=40)
    knowledge_base_ids:list[int]=Field(default_factory=list,max_length=100)
    headings:list[HeadingCorrection]=Field(default_factory=list,max_length=3000)
    mappings:list[Mapping]=Field(default_factory=list,max_length=1000)
    parent_id:int|None=None

def allowed(template,user,include_deleted=False):
    if template.get('lifecycle')=='deleted' and (not include_deleted or not (user['system_admin'] or 'templates.manage' in user['permissions'])):return False
    if template.get('lifecycle','published')!='published' and not user['system_admin'] and 'templates.manage' not in user['permissions']:return False
    ids=json.loads(template.get('knowledge_base_ids') or '[]')
    if not ids or user['system_admin']:return True
    owned={r['kb_id'] for r in s.rows('SELECT kb_id FROM kb_grants WHERE user_id=?',(user['id'],))}
    return bool(owned.intersection(ids))

def ensure_template_scope(template,kid,user):
    ids=json.loads(template.get('knowledge_base_ids') or '[]')
    if ids and kid not in ids:raise HTTPException(403,'该模板不适用于所选知识库')
    if not allowed(template,user):raise HTTPException(403,'无权使用此模板')

def install(app,auth,require,check_kb):
    def admin(user):
        require(user,'templates.manage')
        if not user['system_admin']:raise HTTPException(403,'仅系统管理员可导入和校正 DOCX 模板')
    def imported(iid,user):
        admin(user);row=s.one('SELECT * FROM template_imports WHERE id=?',(iid,))
        if not row:raise HTTPException(404,'导入记录不存在')
        return row

    @app.post('/api/templates/import-docx',status_code=201)
    async def upload(file:UploadFile=File(...),user=Depends(auth)):
        admin(user)
        if not (file.filename or '').lower().endswith('.docx'):raise HTTPException(422,'报告模板只接受 .docx，不支持 .doc 或 .docm')
        data=await file.read(MAX_FILE+1)
        if len(data)>MAX_FILE:raise HTTPException(413,'模板不能超过20MB')
        structure=parse_docx(data)
        sha=hashlib.sha256(data).hexdigest()
        key=object_storage.object_key('templates',sha[:16],'import','docx')
        stored=await asyncio.to_thread(object_storage.save_bytes,data,key,sha)
        try:
            iid=s.execute('INSERT INTO template_imports(user_id,filename,path,sha256,structure,created_at,storage_backend,storage_bucket,object_key,object_version_id) VALUES(?,?,?,?,?,?,?,?,?,?)',
                          (user['id'],Path(file.filename).name,stored['path'],sha,json.dumps(structure,ensure_ascii=False),s.now(),stored['storage_backend'],stored['storage_bucket'],stored['object_key'],stored['object_version_id']))
        except Exception:
            if stored['storage_backend']=='s3':
                try:object_storage.remove_uncommitted(stored['object_key'],stored['object_version_id'])
                except Exception:pass
            else:
                Path(stored['path']).unlink(missing_ok=True)
            raise
        s.audit(user,'解析DOCX模板','报告模板',object_id=iid)
        return {'import_id':iid,'filename':Path(file.filename).name,'sha256':hashlib.sha256(data).hexdigest(),'structure':structure}

    @app.get('/api/templates/imports/{iid}')
    def get_import(iid:int,user=Depends(auth)):
        row=imported(iid,user)
        return {'import_id':iid,'filename':row['filename'],'sha256':row['sha256'],'structure':json.loads(row['structure'])}

    @app.post('/api/templates/imports/{iid}/save',status_code=201)
    def save(iid:int,body:SaveImport,user=Depends(auth)):
        row=imported(iid,user);structure=json.loads(row['structure'])
        for kid in body.knowledge_base_ids:check_kb(user,kid)
        paragraphs={b['id']:b for b in structure['blocks'] if b['part']=='body' and not b['table']}
        seen=set()
        for correction in body.headings:
            if correction.block_id not in paragraphs or correction.block_id in seen:raise HTTPException(422,'标题校正含重复或不存在的正文段落')
            seen.add(correction.block_id);block=paragraphs[correction.block_id]
            block.update(level=correction.level,type='heading' if correction.level else 'paragraph')
        heads=[b for b in paragraphs.values() if b['type']=='heading' and b['text'].strip()]
        if not 1<=len(heads)<=20:raise HTTPException(422,'请校正为1至20个有效标题，每个标题对应一个报告章节')
        headids={h['id'] for h in heads};known={f['key'] for f in structure['fields']}
        if {m.key for m in body.mappings}!=known or len(body.mappings)!=len(known):raise HTTPException(422,'每个占位符必须恰好配置一个映射（可选忽略）')
        for m in body.mappings:
            if m.target=='section' and m.section_block_id not in headids:raise HTTPException(422,'章节字段必须映射到有效标题')
        if not body.name.strip():raise HTTPException(422,'模板名称不能为空')
        specs=[]
        for h in heads:
            if len(h['text'].strip())>100:raise HTTPException(422,'标题超过100字，请先在原文件中精简再导入')
            keys=[m.key for m in body.mappings if m.target=='section' and m.section_block_id==h['id']]
            specs.append(blueprints.ReportSection(title=h['text'].strip()[:100],level=h['level'],block_id=h['id'],instructions=('围绕下列模板字段生成有证据的正文：'+ '、'.join(keys))[:2000] if keys else '只依据知识库证据撰写本节。'))
        plan=blueprints.ReportBlueprint(name=body.name,description=body.description,purpose=body.purpose,sections=specs)
        with s.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if body.parent_id:
                parent=c.execute('SELECT * FROM report_templates WHERE id=?',(body.parent_id,)).fetchone()
                if not parent or parent['import_id']!=iid:raise HTTPException(409,'父模板版本与导入文件不匹配')
                if parent['lifecycle']=='deleted':raise HTTPException(409,'父模板已删除，请先从回收站恢复')
            if c.execute('SELECT id FROM report_templates WHERE import_id=? AND version=?',(iid,body.version)).fetchone():raise HTTPException(409,'此导入文件已有同名版本，请填写新的版本号')
            tid=c.execute('''INSERT INTO report_templates(name,description,sections,version,created_at,blueprint,import_id,parent_id,structure,field_mappings,knowledge_base_ids)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(body.name,body.description,json.dumps([h.title for h in specs],ensure_ascii=False),body.version,s.now(),plan.model_dump_json(),iid,body.parent_id,json.dumps(structure,ensure_ascii=False),json.dumps([m.model_dump() for m in body.mappings],ensure_ascii=False),json.dumps(sorted(set(body.knowledge_base_ids))))).lastrowid
        s.audit(user,'保存模板映射版本','报告模板',body.version,object_id=tid)
        return {'id':tid,'version':body.version}

    @app.get('/api/templates/{tid}/structure')
    def inspect(tid:int,user=Depends(auth)):
        require(user,'reports.generate');row=s.one('SELECT * FROM report_templates WHERE id=?',(tid,))
        if not row:raise HTTPException(404,'模板不存在')
        if not allowed(row,user):raise HTTPException(403,'无权访问此模板')
        return {k:json.loads(row[k]) for k in ('structure','field_mappings','knowledge_base_ids')}|{k:row[k] for k in ('id','name','description','version','import_id','parent_id')}|{'blueprint':blueprints.from_template(row).model_dump()}

    @app.get('/api/templates/{tid}/original')
    def original(tid:int,request:Request,user=Depends(auth)):
        require(user,'reports.generate');row=s.one('SELECT * FROM report_templates WHERE id=?',(tid,))
        if not row:raise HTTPException(404,'模板不存在')
        if not allowed(row,user):raise HTTPException(403,'无权访问此模板')
        source=s.one('SELECT * FROM template_imports WHERE id=?',(row['import_id'],))
        if not source or not object_storage.exists(source):raise HTTPException(404,'此模板无可用原文件')
        return object_storage.original_response(source,source['filename'],'application/vnd.openxmlformats-officedocument.wordprocessingml.document',request.headers.get('range'))
