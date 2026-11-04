"""Produce a DOCX whose page and paragraph properties follow the validated report template."""
import io
import re
import zipfile
from xml.sax.saxutils import escape
from .blueprints import ReportFormat

def docx(text,format:ReportFormat):
    def paragraph(line,style='Normal',align=None):
        parts=[]
        for part in re.split(r'(\*\*[^*]+\*\*)',line):
            bold=part.startswith('**') and part.endswith('**')
            parts.append('<w:r>'+('<w:rPr><w:b/></w:rPr>' if bold else '')+'<w:t xml:space="preserve">'+escape(part[2:-2] if bold else part)+'</w:t></w:r>')
        return '<w:p><w:pPr><w:pStyle w:val="'+style+'"/>'+('<w:jc w:val="'+align+'"/>' if align else '')+'</w:pPr>'+''.join(parts)+'</w:p>'
    paragraphs=[]
    title_seen=False
    for line in text.splitlines():
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
            paragraphs.append(paragraph(line))
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
        if format.page_numbers:
            z.writestr('word/footer1.xml','<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:fldSimple w:instr="PAGE"><w:r><w:t>1</w:t></w:r></w:fldSimple></w:p></w:ftr>')
    return buffer.getvalue()
