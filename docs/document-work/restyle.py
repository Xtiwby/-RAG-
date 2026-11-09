from pathlib import Path
from copy import deepcopy
from zipfile import ZipFile, ZIP_DEFLATED
from lxml import etree as E
from docx import Document
import hashlib, json

ROOT=Path('E:/RAG/-RAG-')
OUT=ROOT/'docs/deliverables-v2'
SRC=Path('F:/北京理工大学/OneDrive/2026-2027上学期/卓越工程综合训练')
W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
NS={'w':W}
def q(t):return '{'+W+'}'+t
def el(t,**attrs):
 x=E.Element(q(t))
 for k,v in attrs.items():x.set(q(k),str(v))
 return x
def text(x):return ''.join(x.xpath('.//w:t/text()',namespaces=NS))
def prop(x,t):
 y=x.find(q(t))
 if y is None:y=el(t);x.insert(0,y)
 return y
def replace_text(p,value):
 ts=list(p.iter(q('t')))
 if ts:
  ts[0].text=value
  for t in ts[1:]:t.text=''
def para(t,pr=None,rpr=None):
 p=el('p')
 if pr is not None:p.append(deepcopy(pr))
 r=el('r')
 if rpr is not None:r.append(deepcopy(rpr))
 tx=el('t');tx.text=t;r.append(tx);p.append(r);return p
def three_lines(tbl,available):
 pr=prop(tbl,'tblPr')
 for tag in ['tblBorders','tblStyle','shd']:
  for x in pr.findall(q(tag)):pr.remove(x)
 borders=el('tblBorders')
 for edge in ['top','left','bottom','right','insideH','insideV']:
  borders.append(el(edge,val='single' if edge in ['top','bottom'] else 'nil',sz='12' if edge in ['top','bottom'] else '0',color='000000'))
 pr.append(borders)
 width=prop(pr,'tblW');width.set(q('w'),str(available));width.set(q('type'),'dxa')
 jc=prop(pr,'jc');jc.set(q('val'),'center')
 mar=prop(pr,'tblCellMar')
 for side in ['top','bottom','left','right']:
  x=prop(mar,side);x.set(q('w'),'70' if side in ['top','bottom'] else '90');x.set(q('type'),'dxa')
 rows=tbl.findall(q('tr'))
 for ri,row in enumerate(rows):
  for x in row.findall(q('tblPrEx')):row.remove(x)
  trpr=prop(row,'trPr')
  for x in trpr.findall(q('trHeight')):trpr.remove(x)
  if trpr.find(q('cantSplit')) is None:trpr.append(el('cantSplit'))
  for cell in row.findall(q('tc')):
   tcpr=prop(cell,'tcPr')
   for tag in ['tcBorders','shd']:
    for x in tcpr.findall(q(tag)):tcpr.remove(x)
   cb=el('tcBorders')
   for edge in ['top','left','bottom','right']:
    cb.append(el(edge,val='single' if ri==0 and edge=='bottom' else 'nil',sz='6' if ri==0 and edge=='bottom' else '0',color='000000'))
   # Top and bottom rules are owned by table borders, not overridden by cells.
   for edge in ['top','bottom']:
    x=cb.find(q(edge))
    if edge=='top' and ri==0:cb.remove(x)
    if edge=='bottom' and ri==len(rows)-1:cb.remove(x)
   tcpr.append(cb)
   va=prop(tcpr,'vAlign');va.set(q('val'),'center')
 # Scale the actual column grid and every matching cell width to page text width.
 grid=tbl.find(q('tblGrid'))
 if grid is not None:
  cols=grid.findall(q('gridCol'));old=[int(c.get(q('w'))) for c in cols]
  widths=[round(v*available/sum(old)) for v in old];widths[-1]+=available-sum(widths)
  for c,v in zip(cols,widths):c.set(q('w'),str(v))
  for row in rows:
   cells=row.findall(q('tc'))
   if len(cells)==len(widths):
    for c,v in zip(cells,widths):
     w=prop(prop(c,'tcPr'),'tcW');w.set(q('w'),str(v));w.set(q('type'),'dxa')

reports=[]
for target in sorted(OUT.glob('*.docx')):
 kind=target.stem.split('_')[1]
 reference=SRC/('基于RAG的知识库管理系统_'+('软件项目计划' if kind=='软件项目计划' else '软件需求规格说明书')+'_V1.0.docx')
 with ZipFile(reference) as z:parts={n:z.read(n) for n in z.namelist()}
 source_hash=hashlib.sha256(reference.read_bytes()).hexdigest()
 root=E.fromstring(parts['word/document.xml']);body=root.find(q('body'))
 source_doc=Document(reference)
 normal=next(p for p in source_doc.paragraphs if p.text.startswith('本文档用于'))
 normal_pr=deepcopy(normal._p.pPr);normal_rpr=deepcopy(normal.runs[0]._r.rPr)
 heads={n:next(p for p in source_doc.paragraphs if p.style.name=='Heading '+str(n) and p.text.strip()) for n in [1,2]}
 # Keep the source cover and its section break, headers, footers, styles and relationships.
 children=list(body);tocpos=next(i for i,x in enumerate(children) if x.tag==q('sdt'))
 cover=[deepcopy(x) for x in children[:tocpos]]
 sectbreak=next(deepcopy(p._p) for p in source_doc.paragraphs if p._p.xpath('./w:pPr/w:sectPr'))
 finalsect=deepcopy(body.find(q('sectPr')))
 for node in list(body):body.remove(node)
 for node in cover:
  if node.tag==q('p'):
   if text(node) in ['软件需求规格说明书','软件项目计划']:replace_text(node,kind)
   if text(node)=='二〇二六年十月':replace_text(node,'二〇二六年十一月')
  body.append(node)
 # New table of contents is an actual Word field, with current headings as cached text.
 pg=el('p');r=el('r');r.append(el('br',type='page'));pg.append(r);body.append(pg)
 title=deepcopy(heads[1]._p);replace_text(title,'目录');body.append(title)
 with ZipFile(target) as z:old=E.fromstring(z.read('word/document.xml'))
 oldbody=old.find(q('body'));nodes=list(oldbody)
 start=next(i for i,x in enumerate(nodes) if text(x)=='文档版本与使用约定')
 newnodes=[];heading_list=[]
 for node in nodes[start:]:
  if node.tag==q('sectPr'):continue
  x=deepcopy(node)
  if x.tag==q('p'):
   sty=x.find('w:pPr/w:pStyle',NS);sid=sty.get(q('val')) if sty is not None else ''
   content=text(x)
   if sid.startswith('Heading') and content:
    level=1 if sid=='Heading1' else 2
    pr=deepcopy(heads[level]._p.pPr)
    # Existing explicit numbers are authoritative, disable inherited auto-numbering.
    num=prop(pr,'numPr');num.clear();num.append(el('numId',val='0'))
    if x.find(q('pPr')) is not None:x.remove(x.find(q('pPr')))
    x.insert(0,pr)
    for r in x.findall(q('r')):
     rp=r.find(q('rPr'))
     if rp is not None:r.remove(rp)
    if kind=='功能规格说明书' and content.startswith('3 F-'):
     number=int(content.split('F-')[1][:2])+2;content=str(number)+content[1:];replace_text(x,content)
    heading_list.append((content,level))
   elif content:
    if x.find(q('pPr')) is not None:x.remove(x.find(q('pPr')))
    x.insert(0,deepcopy(normal_pr))
    for r in x.findall(q('r')):
     rp=r.find(q('rPr'))
     if rp is not None:r.remove(rp)
     r.insert(0,deepcopy(normal_rpr))
  elif x.tag==q('tbl'):
   for pp in x.xpath('.//w:p',namespaces=NS):
    pr=prop(pp,'pPr')
    for st in pr.findall(q('pStyle')):pr.remove(st)
    for rr in pp.findall(q('r')):
     rp=rr.find(q('rPr'))
     if rp is not None:rr.remove(rp)
     rr.insert(0,deepcopy(normal_rpr))
  newnodes.append(x)
 toc=el('p');rr=el('r');rr.append(el('fldChar',fldCharType='begin'));toc.append(rr)
 rr=el('r');tx=el('instrText');tx.text=' TOC \\o "1-2" \\h \\z \\u ';rr.append(tx);toc.append(rr)
 rr=el('r');rr.append(el('fldChar',fldCharType='separate'));toc.append(rr);body.append(toc)
 for txt,level in heading_list:
  pp=para(txt);pr=prop(pp,'pPr');pr.append(el('ind',left='0' if level==1 else '420'));body.append(pp)
 pp=el('p');rr=el('r');rr.append(el('fldChar',fldCharType='end'));pp.append(rr);body.append(pp)
 body.append(sectbreak)
 # Keep the revised introductory statement within the document description.
 for i,x in enumerate(newnodes):
  body.append(x)
  if i==0:
   body.append(para('版本 V2.0　修订日期 2026年11月5日　代码基线 0.10.0',normal_pr,normal_rpr))
   body.append(para('本版围绕文件知识库、证据检索、知识问答和模板报告构建可演示闭环。后续迭代重点为质量修复、接口联调和可复现验收；功能是否通过以冻结版本的测试记录为准。',normal_pr,normal_rpr))
 body.append(finalsect)
 pgsize=finalsect.find(q('pgSz'));margin=finalsect.find(q('pgMar'))
 available=int(pgsize.get(q('w')))-int(margin.get(q('left')))-int(margin.get(q('right')))
 for tbl in body.findall(q('tbl')):three_lines(tbl,available)
 parts['word/document.xml']=E.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)
 settings=E.fromstring(parts['word/settings.xml']);uf=prop(settings,'updateFields');uf.set(q('val'),'true');parts['word/settings.xml']=E.tostring(settings,xml_declaration=True,encoding='UTF-8',standalone=True)
 dest=target.with_name(target.stem+'_原格式三线表.docx')
 with ZipFile(dest,'w',ZIP_DEFLATED) as z:
  for n,b in parts.items():z.writestr(n,b)
 assert hashlib.sha256(reference.read_bytes()).hexdigest()==source_hash
 with ZipFile(dest) as z:
  assert z.testzip() is None
  preserved=[n for n in parts if n not in ['word/document.xml','word/settings.xml']]
  with ZipFile(reference) as original:assert all(z.read(n)==original.read(n) for n in preserved)
 reports.append({'output':str(dest),'reference':str(reference),'sha256':source_hash,'table_count':len(body.findall(q('tbl'))),'preserved_parts':len(preserved)})
 print(dest.name,'tables',reports[-1]['table_count'],'preserved package parts',len(preserved))
(ROOT/'docs/document-work/artifact.md').write_text('# Reference format contract\n\n'+json.dumps(reports,ensure_ascii=False,indent=2)+'\n\nRetain source cover, metadata table, A4 page size 11906 by 16838 twips, margins top/bottom 1440 and left/right 1800, section numbering, original styles, headers and footers. Clone body paragraph and heading properties and actual run font from source. Replace source body with revised V2 content. All tables are explicitly changed to three horizontal rules without vertical or internal body lines or shading. TOC field updated on opening in Word; cached entries contain current headings without guessed page numbers. All package parts except document.xml and settings.xml preserved byte for byte. Rendering unavailable: bundled LibreOffice missing and no Word COM registered.\n',encoding='utf-8')
