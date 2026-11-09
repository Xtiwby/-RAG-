"""Produce a DOCX whose page and paragraph properties follow the validated report template."""
import io
import re
import zipfile
from xml.sax.saxutils import escape, quoteattr
from .blueprints import ReportFormat

def docx(text,format:ReportFormat,*,images=None):
    pictures={}

    def paragraph(line,style='Normal',align=None,keep_next=False):
        parts=[]
        for part in re.split(r'(\*\*[^*]+\*\*)',line):
            bold=part.startswith('**') and part.endswith('**')
            parts.append('<w:r>'+('<w:rPr><w:b/></w:rPr>' if bold else '')+'<w:t xml:space="preserve">'+escape(part[2:-2] if bold else part)+'</w:t></w:r>')
        return '<w:p><w:pPr><w:pStyle w:val="'+style+'"/>'+('<w:keepNext/>' if keep_next else '')+('<w:jc w:val="'+align+'"/>' if align else '')+'</w:pPr>'+''.join(parts)+'</w:p>'
    paragraphs=[]
    title_seen=False
    for index,line in enumerate(text.splitlines()):
        if image:=(images or {}).get(index):
            number=pictures.setdefault(id(image),(len(pictures)+1,image))[0]
            scale=min(5650000/image.width,6900000/image.height)
            width,height=round(image.width*scale),round(image.height*scale)
            name=quoteattr(image.label)
            paragraphs.append(f'''<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:drawing>
              <wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">
                <wp:extent cx="{width}" cy="{height}"/><wp:docPr id="{index+1}" name={name} descr={name}/>
                <a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
                  <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">
                    <pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">
                      <pic:nvPicPr><pic:cNvPr id="0" name={name}/><pic:cNvPicPr/></pic:nvPicPr>
                      <pic:blipFill><a:blip r:embed="rIdImage{number}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>
                      <pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{width}" cy="{height}"/></a:xfrm>
                        <a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>
                    </pic:pic>
                  </a:graphicData>
                </a:graphic>
              </wp:inline></w:drawing></w:r></w:p>''')
            continue
        match=re.match(r'^(#{1,6})\s+(.+)',line)
        if match:
            level=len(match[1]);title=match[2]
            if level==1 and not title_seen:
                title_seen=True
                paragraphs.append(paragraph(title,'Title','center'))
                if format.cover:
                    paragraphs.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
            else:
                paragraphs.append(paragraph(title,'Heading1' if level==2 else 'Heading2'))
        else:
            paragraphs.append(paragraph(line,keep_next=index+1 in (images or {})))
    width,height=(11906,16838) if format.paper=='A4' else (12240,15840)
    if format.orientation=='landscape':
        width,height=height,width
    margins={key:round(getattr(format,'margin_'+key+'_cm')*1440/2.54) for key in ('top','bottom','left','right')}
    section=f'<w:sectPr><w:pgSz w:w="{width}" w:h="{height}" w:orient="{format.orientation}"/><w:pgMar '+ ' '.join(f'w:{k}="{v}"' for k,v in margins.items())+' w:header="708" w:footer="708"/>'
    if format.page_numbers:
        section+='<w:footerReference w:type="default" r:id="rIdFooter"/>'
    section+='</w:sectPr>'
    document='<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body>'+''.join(paragraphs)+section+'</w:body></w:document>'
    font=escape(format.font)
    styles=f'''<?xml version="1.0" encoding="UTF-8"?><w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
    <w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:eastAsia="{font}"/><w:sz w:val="{format.font_size_pt*2}"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="100" w:line="{round(format.line_spacing*240)}" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
    <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
    <w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:rPr><w:b/><w:sz w:val="44"/></w:rPr></w:style>
    <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="Heading 1"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="{format.heading_size_pt*2}"/></w:rPr></w:style>
    <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="Heading 2"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:sz w:val="{max(format.font_size_pt,format.heading_size_pt-2)*2}"/></w:rPr></w:style></w:styles>'''
    types='''<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'''
    relationships='<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    if pictures:
        types+='<Default Extension="png" ContentType="image/png"/>'
        for number,_ in pictures.values():
            relationships+=f'<Relationship Id="rIdImage{number}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/citation-{number}.png"/>'
    if format.page_numbers:
        types+='<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
        relationships+='<Relationship Id="rIdFooter" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>'
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml',types+'</Types>')
        z.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr('word/document.xml',document)
        z.writestr('word/styles.xml',styles)
        z.writestr('word/_rels/document.xml.rels',relationships+'</Relationships>')
        for number,image in pictures.values():
            z.writestr(f'word/media/citation-{number}.png',image.data)
        if format.page_numbers:
            z.writestr('word/footer1.xml','<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:fldSimple w:instr="PAGE"><w:r><w:t>1</w:t></w:r></w:fldSimple></w:p></w:ftr>')
    return buffer.getvalue()
