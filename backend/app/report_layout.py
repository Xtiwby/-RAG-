"""One semantic document tree for screen preview, DOCX and PDF (no raw HTML)."""
import copy
import io
import json
import re
import zipfile
from xml.sax.saxutils import escape
from . import blueprints,template_docx,report_export

def decode(value,default):
    return json.loads(value) if isinstance(value,str) and value else value or default

def nodes(report,sections,kb_name='',draft=True,diagnostics=False):
    plan=blueprints.ReportBlueprint.model_validate(decode(report.get('blueprint'),{}))
    template=decode(report.get('template_snapshot'),{})
    structure=decode(template.get('structure'),{})
    mappings=decode(template.get('field_mappings'),[])
    inputs=decode(report.get('field_values'),{})
    sources=[];source_ids={};texts=[]
    for section in sections:
        cites=decode(section.get('citations'),[]);numbers={}
        used={int(n) for n in re.findall(r'\[(\d+)\]',section['content'])}
        for i,c in enumerate(cites,1):
            if i not in used:continue
            identity=(c.get('document_id'),c.get('version'),c.get('chunk_id'))
            if identity not in source_ids:source_ids[identity]=len(sources)+1;sources.append(c)
            numbers[i]=source_ids[identity]
        texts.append(re.sub(r'\[(\d+)\]',lambda m:'['+str(numbers.get(int(m[1]),m[1]))+']',section['content']))
    heads=[b for b in structure.get('blocks',[]) if b['type']=='heading']
    section_by_block={spec.block_id or (heads[i]['id'] if i<len(heads) else ''):i for i,spec in enumerate(plan.sections)}
    def field(mapping):
        target=mapping['target']
        if target=='section':
            i=section_by_block.get(mapping.get('section_block_id'))
            return texts[i] if i is not None and i<len(texts) else '【待补充章节】'
        return {'report_title':report['title'],'knowledge_base':kb_name,'generated_at':report['created_at'],
                'period':('至'.join(v for v in (report.get('period_start'),report.get('period_end')) if v) or '未限定'),
                'literal':mapping.get('value',''),'user_input':inputs.get(mapping['key'],''),'ignore':''}.get(target,'')
    values={m['key']:field(m) for m in mappings}
    def section_text(i):
        prefix={'analysis':'分析（基于来源的推断，不等同于已发生事实）：\n','recommendation':'建议（不表示已经实施）：\n'}.get(plan.sections[i].kind,'')
        return prefix+(texts[i] if i<len(texts) else '【待生成章节】')
    for m in mappings:
        if m['target']=='section':
            i=section_by_block.get(m.get('section_block_id'))
            if i is not None and i<len(texts):values[m['key']]=section_text(i)
    blockmap={b['id']:b for b in structure.get('blocks',[])};tables={t['id']:t for t in structure.get('tables',[])}
    def block_text(b):
        replacements=[(match.start(),match.end(),values.get(next(v for v in match.groups() if v is not None).strip(),'【未映射字段】')) for match in template_docx.PLACEHOLDER.finditer(b['text'])]
        controls=[f for f in structure.get('fields',[]) if any(o['block_id']==b['id'] and o['syntax'] in ('MERGEFIELD','内容控件') for o in f['occurrences'])]
        for f in controls:
            for occ in f['occurrences']:
                if occ['block_id']==b['id'] and 'start' in occ:replacements.append((occ['start'],occ['end'],values.get(f['key'],'【未映射字段】')))
        text=b['text'];right=len(text)+1
        for start,end,value in sorted(replacements,reverse=True):
            if end<=right:text=text[:start]+value+text[end:];right=start
        if controls and not replacements:text=' '.join(values.get(f['key'],'【未映射字段】') for f in controls)
        return text
    def paragraph(text,level=0):return {'type':'heading' if level else 'paragraph','level':level,'text':text}
    def render_table(tid,depth=0):
        if depth>5:return paragraph('【嵌套表格超过5级，请对照原件核对】')
        result={'type':'table','rows':[]}
        for row in tables[tid]['rows']:
            cells=[]
            for cell in row['cells']:
                content=[]
                for bid in cell['block_ids']:
                    if bid in blockmap:content.append(paragraph(block_text(blockmap[bid])))
                    elif bid in tables:content.append(render_table(bid,depth+1))
                cells.append({'span':cell['column_span'],'merge':cell['vertical_merge'],'nodes':content})
            result['rows'].append({'header':row.get('header',False),'cells':cells})
        return result
    result=[paragraph(report['title'],1)]
    if draft:result.append(paragraph('草稿：仅供编辑和审阅，不代表已定稿。未核验或未解决内容请勿作为正式结论。'))
    from . import report_grounding
    if report_grounding.generation_mode(report)=='synthesis':
        result.append(paragraph('生成模式：基于证据综合生成。正文由Kimi结合RAG资料撰写，可含明确标注的分析、方案及建议，未执行逐句事实核验，请人工审阅。'))
    else:
        result.append(paragraph('生成模式：严格事实证据。生成正文执行逐句证据核验，仍需人工审阅。'))
    risks=[]
    for i,section in enumerate(sections):
        spec=plan.sections[i] if i<len(plan.sections) else None
        verification=report_grounding.current(section)
        supported=verification.get('state')=='verified' or (verification.get('state')=='partial' and verification.get('publishable'))
        if section['content'].strip() and not supported and (not spec or spec.kind not in ('fixed','container')):
            risks.append(section['title'])
    if risks:
        result.append(paragraph('证据状态说明：'+ '、'.join(risks)+'尚未获证据核验通过，相关内容不代表已核实事实。'+('人工审阅后可决定是否定稿。' if draft else '本正式版本由人工审阅决定保留，定稿不代表模型核验通过。')))
    if report.get('period_start') or report.get('period_end'):result.append(paragraph('报告业务时间范围：'+values.get('报告期间',' 至 '.join(v for v in (report.get('period_start'),report.get('period_end')) if v))))
    if not structure.get('blocks'):
        for m in mappings:
            if m['target'] not in ('section','ignore'):result.append(paragraph(m['key']+'：'+values[m['key']]))
    if structure.get('blocks'):
        order=structure.get('body_order')
        if order is None:
            order=[];seen=set()
            for b in structure['blocks']:
                if b['part']!='body':continue
                tid=b.get('table')
                while tid and tables.get(tid,{}).get('parent_table'):tid=tables[tid]['parent_table']
                key=tid or b['id']
                if key not in seen:seen.add(key);order.append(key)
        mapped={m.get('section_block_id') for m in mappings if m['target']=='section'}
        inserted=set()
        for bid in order:
            if bid in tables:result.append(render_table(bid));continue
            b=blockmap.get(bid)
            if not b:continue
            i=section_by_block.get(bid)
            if i is not None:
                result.append(paragraph(plan.sections[i].title,min(6,plan.sections[i].level+1)))
                if bid not in mapped and i<len(texts):result.extend(paragraph(t) for t in section_text(i).splitlines());inserted.add(i)
            else:result.append(paragraph(block_text(b),min(6,b.get('level',0)+1) if b['type']=='heading' else 0))
        # A manually added section with no source position is appended, not silently dropped.
        for i,spec in enumerate(plan.sections):
            bid=spec.block_id or (heads[i]['id'] if i<len(heads) else '')
            if not bid or bid not in order:
                result.append(paragraph(spec.title,min(6,spec.level+1)));result.extend(paragraph(t) for t in section_text(i).splitlines())
    else:
        for i,spec in enumerate(plan.sections):
            result.append(paragraph(spec.title,min(6,spec.level+1)))
            if i<len(texts):result.extend(paragraph(t) for t in section_text(i).splitlines())
    # A reference list is mandatory; include_sources only controls full evidence snippets.
    if sources:
        result.append(paragraph('参考来源',2))
        for i,c in enumerate(sources,1):
            result.append(paragraph(f"[{i}] {c.get('source',c.get('name','资料'))}；知识库 {c.get('knowledge_base_name',kb_name)}；文档#{c.get('document_id')}；片段#{c.get('chunk_id')}；相关度 {c.get('relevance','—')}"))
            if plan.format.include_sources:result.append(paragraph(c.get('text',c.get('snippet',''))))
    if diagnostics:
        from .report_grounding import label
        result.append(paragraph('质量核验附录',2))
        for section in sections:result.append(paragraph(section['title']+'：'+label(section)))
    return result

def markdown(tree):
    def text(node):
        if node['type']=='table':return '\n'.join(' | '.join(' '.join(text(n) for n in c['nodes']) for c in r['cells']) for r in node['rows'])
        return ('#'*node.get('level',0)+' ' if node['type']=='heading' else '')+node['text']
    return '\n\n'.join(text(n) for n in tree)

def docx(tree,format):
    w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    def paragraph(text,level=0):
        style='Title' if level==1 else 'Heading1' if level==2 else 'Heading2' if level else 'Normal'
        return '<w:p><w:pPr><w:pStyle w:val="'+style+'"/>'+ (f'<w:outlineLvl w:val="{min(8,level-2)}"/>' if level>1 else '')+'</w:pPr>'+''.join('<w:r><w:t xml:space="preserve">'+escape(line)+'</w:t></w:r>'+('<w:r><w:br/></w:r>' if i<len(text.split('\n'))-1 else '') for i,line in enumerate(text.split('\n')))+'</w:p>'
    def render(node):
        if node['type']!='table':return paragraph(node['text'],node.get('level',0))
        rows=[]
        for row in node['rows']:
            cells=[]
            for cell in row['cells']:
                merge=('<w:vMerge w:val="restart"/>' if cell['merge']=='restart' else '<w:vMerge/>' if cell['merge'] else '')
                cells.append('<w:tc><w:tcPr>'+f'<w:gridSpan w:val="{cell["span"]}"/>'+merge+'<w:vAlign w:val="center"/></w:tcPr>'+(''.join(render(n) for n in cell['nodes']) or '<w:p/>')+'</w:tc>')
            rows.append('<w:tr>'+('<w:trPr><w:tblHeader/></w:trPr>' if row['header'] else '')+''.join(cells)+'</w:tr>')
        borders=''.join(f'<w:{side} w:val="single" w:sz="4" w:color="D9D9D9"/>' for side in ('top','left','bottom','right','insideH','insideV'))
        return '<w:tbl><w:tblPr><w:tblW w:w="5000" w:type="pct"/><w:tblBorders>'+borders+'</w:tblBorders><w:tblCellMar><w:top w:w="100" w:type="dxa"/><w:left w:w="100" w:type="dxa"/><w:bottom w:w="100" w:type="dxa"/><w:right w:w="100" w:type="dxa"/></w:tblCellMar></w:tblPr>'+''.join(rows)+'</w:tbl>'
    base=report_export.docx('',format);out=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(base)) as z,zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as target:
        for name in z.namelist():
            data=z.read(name)
            if name=='word/document.xml':
                original=data.decode();section=original[original.index('<w:sectPr>'):original.index('</w:sectPr>')+len('</w:sectPr>')]
                content=''
                for i,node in enumerate(tree):
                    content+=render(node)
                    if i==0 and format.cover:content+='<w:p><w:r><w:br w:type="page"/></w:r></w:p>'
                data=(f'<w:document xmlns:w="{w}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body>'+content+section+'</w:body></w:document>').encode()
            target.writestr(name,data)
    return out.getvalue()

def pdf(tree,format):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,PageBreak
    from reportlab.lib.pagesizes import A4,LETTER,landscape
    font='STSong-Light'
    if font not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(UnicodeCIDFont(font))
    page=A4 if format.paper=='A4' else LETTER
    if format.orientation=='landscape':page=landscape(page)
    cm=72/2.54;out=io.BytesIO();width=page[0]-(format.margin_left_cm+format.margin_right_cm)*cm
    normal=ParagraphStyle('Report',fontName=font,fontSize=format.font_size_pt,leading=format.font_size_pt*format.line_spacing,wordWrap='CJK',spaceAfter=6)
    def render(node,available=width):
        if node['type']!='table':
            style=normal
            if node['type']=='heading':style=ParagraphStyle('Heading',parent=normal,fontSize=20 if node['level']==1 else max(format.font_size_pt,format.heading_size_pt-node['level']+2),spaceBefore=10,spaceAfter=8,keepWithNext=True)
            return Paragraph(escape(node['text']).replace('\n','<br/>') or ' ',style)
        columns=max((sum(c['span'] for c in r['cells']) for r in node['rows']),default=1);data=[];commands=[];vertical={}
        for ri,row in enumerate(node['rows']):
            values=[];col=0
            for c in row['cells']:
                values.append([render(n,available*c['span']/columns) for n in c['nodes']]);values.extend(['']*(c['span']-1))
                if c['span']>1:commands.append(('SPAN',(col,ri),(col+c['span']-1,ri)))
                if c['merge']=='restart':vertical[col]=(col,ri,c['span'])
                elif c['merge']=='continue' and col in vertical:
                    start=vertical[col]
                    # Vertical spans are finalized when their chain ends.
                    vertical[col]=(start[0],start[1],c['span'],ri)
                elif col in vertical:
                    start=vertical.pop(col)
                    if len(start)>3:commands.append(('SPAN',(start[0],start[1]),(start[0]+start[2]-1,start[3])))
                col+=c['span']
            values+=['']*(columns-len(values));data.append(values)
            if row['header']:commands.append(('BACKGROUND',(0,ri),(-1,ri),colors.HexColor('#eef3f6')))
        for start in vertical.values():
            if len(start)>3:commands.append(('SPAN',(start[0],start[1]),(start[0]+start[2]-1,start[3])))
        table=Table(data or [['']],colWidths=[available/columns]*columns,repeatRows=1 if node['rows'] and node['rows'][0]['header'] else 0,hAlign='LEFT',splitInRow=1)
        table.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.5,colors.HexColor('#D9D9D9')),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]+commands));return table
    story=[]
    for i,node in enumerate(tree):
        story.append(render(node));story.append(Spacer(1,5))
        if i==0 and format.cover:story.append(PageBreak())
    def footer(canvas,document):
        if format.page_numbers:
            canvas.setFont(font,10);canvas.drawCentredString(page[0]/2,24,str(document.page))
    SimpleDocTemplate(out,pagesize=page,leftMargin=format.margin_left_cm*cm,rightMargin=format.margin_right_cm*cm,topMargin=format.margin_top_cm*cm,bottomMargin=format.margin_bottom_cm*cm).build(story,onFirstPage=footer,onLaterPages=footer)
    return out.getvalue()
