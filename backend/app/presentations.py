"""Text-only PPTX and PowerPoint 97–2003 PPT ingestion. Never executes macros or OLE objects.

Binary records follow MS-PPT 2.1.2 (latest persist directory), not a whole-file text scan:
https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-ppt/1fc22d56-28f9-4818-bd45-67c2bf721ccf
"""
import posixpath
import struct
import zipfile
from xml.etree import ElementTree as ET

LIMIT=100*1024*1024
FREE=0xffffffff
END=0xfffffffe

def _u32(data,offset):
    if offset<0 or offset+4>len(data):
        raise ValueError('PPT 文件结构截断')
    return struct.unpack_from('<I',data,offset)[0]

class CompoundFile:
    """Bounded CFB FAT/miniFAT reader, supporting v3 and v4, including DIFAT chains."""
    def __init__(self,path):
        self.data=path.read_bytes()
        if len(self.data)<512 or len(self.data)>LIMIT or self.data[:8]!=bytes.fromhex('D0CF11E0A1B11AE1'):
            raise ValueError('不是有效的 PPT 二进制文件')
        major=struct.unpack_from('<H',self.data,26)[0]
        shift=struct.unpack_from('<H',self.data,30)[0]
        if (major,shift) not in ((3,9),(4,12)) or self.data[28:30]!=b'\xfe\xff' or self.data[32:34]!=b'\x06\x00':
            raise ValueError('不支持或损坏的 PPT 复合文件头')
        self.sector_size=1<<shift
        self.sectors=len(self.data)//self.sector_size-1
        difat=list(struct.unpack_from('<109I',self.data,76))
        next_sector=_u32(self.data,68);seen=set()
        for _ in range(_u32(self.data,72)):
            if next_sector in seen:
                raise ValueError('PPT DIFAT 链循环')
            seen.add(next_sector)
            values=struct.unpack('<'+'I'*(self.sector_size//4),self.sector(next_sector))
            difat.extend(values[:-1]);next_sector=values[-1]
            if len(seen)>self.sectors:
                raise ValueError('PPT DIFAT 超出文件边界')
        fat_ids=[sid for sid in difat if sid!=FREE]
        fat_count=_u32(self.data,44)
        if fat_count>self.sectors or len(fat_ids)<fat_count:
            raise ValueError('PPT FAT 表损坏')
        self.fat=[]
        for sid in fat_ids[:fat_count]:
            self.fat.extend(struct.unpack('<'+'I'*(self.sector_size//4),self.sector(sid)))
        directory=self.chain(_u32(self.data,48),self.fat)
        self.entries={};root=None
        for offset in range(0,len(directory)-127,128):
            entry=directory[offset:offset+128]
            length=struct.unpack_from('<H',entry,64)[0]
            if entry[66] not in (2,5) or length<2 or length>64 or length%2:
                continue
            name=entry[:length-2].decode('utf-16-le')
            size=struct.unpack_from('<Q',entry,120)[0] if major==4 else _u32(entry,120)
            value=(_u32(entry,116),size)
            if entry[66]==5:
                root=value
            else:
                self.entries[name]=value
        if root is None:
            raise ValueError('PPT 缺少根存储')
        self.mini_stream=self.chain(root[0],self.fat,root[1]) if root[1] else b''
        mini_count=_u32(self.data,64)
        mini=self.chain(_u32(self.data,60),self.fat,mini_count*self.sector_size) if mini_count else b''
        self.mini_fat=list(struct.unpack('<'+'I'*(len(mini)//4),mini))
        self.cutoff=_u32(self.data,56)
        if self.cutoff!=4096:
            raise ValueError('PPT mini-stream 阈值损坏')

    def sector(self,sid):
        if sid>=self.sectors:
            raise ValueError('PPT 扇区越界')
        return self.data[(sid+1)*self.sector_size:(sid+2)*self.sector_size]

    def chain(self,sid,fat,size=None,mini=False):
        if size is not None and (size<0 or size>LIMIT):
            raise ValueError('PPT 数据流过大')
        chunks=[];seen=set();unit=64 if mini else self.sector_size
        while sid!=END:
            if sid in seen or sid>=len(fat) or len(seen)*unit>=LIMIT:
                raise ValueError('PPT 数据流链循环或越界')
            seen.add(sid)
            chunk=self.mini_stream[sid*64:(sid+1)*64] if mini else self.sector(sid)
            if len(chunk)!=unit:
                raise ValueError('PPT mini-stream 截断')
            chunks.append(chunk);sid=fat[sid]
        result=b''.join(chunks)
        if size is not None and len(result)<size:
            raise ValueError('PPT 数据流长度不足')
        return result if size is None else result[:size]

    def stream(self,name):
        if name not in self.entries:
            raise ValueError('PPT 缺少必要数据流：'+name)
        sid,size=self.entries[name]
        if not size:
            return b''
        return self.chain(sid,self.mini_fat if size<self.cutoff else self.fat,size,mini=size<self.cutoff)

def _record(data,offset,limit=None):
    limit=len(data) if limit is None else limit
    if offset<0 or offset+8>limit:
        raise ValueError('PPT 记录头截断')
    flags,kind,length=struct.unpack_from('<HHI',data,offset)
    end=offset+8+length
    if end>limit:
        raise ValueError('PPT 记录长度超出容器')
    return flags,kind,offset+8,end

def _children(data,start,end):
    count=0
    while start<end:
        flags,kind,body,next_offset=_record(data,start,end)
        yield flags,kind,body,next_offset
        start=next_offset;count+=1
        if count>100000:
            raise ValueError('PPT 记录数量超出限制')

def _text(data,start,end,depth=0):
    if depth>32:
        raise ValueError('PPT 容器嵌套过深')
    result=[]
    for flags,kind,body,stop in _children(data,start,end):
        if kind in (0x0fa0,0x0fa8):
            # TextBytesAtom is compressed Unicode (zero high bytes), not an ANSI code page.
            text=data[body:stop].decode('utf-16-le' if kind==0x0fa0 else 'latin1')
            result.extend(line.strip() for line in text.replace('\x0b','\n').replace('\r','\n').split('\n') if line.strip())
        elif flags&15==15 or kind==0xf00d:  # OfficeArtClientTextbox contains PPT records too.
            result.extend(_text(data,body,stop,depth+1))
    return result

def _ppt(path,progress=None):
    ole=CompoundFile(path)
    current=ole.stream('Current User');data=ole.stream('PowerPoint Document')
    if _u32(current,12)==0xf3d1c4df:
        raise ValueError('加密 PPT 不能解析，请先解除密码保护')
    if _record(current,0)[1]!=0x0ff6:
        raise ValueError('PPT Current User 结构错误')
    offset=_u32(current,16);seen=set();persist={};doc_id=None
    while True:
        if offset in seen or len(seen)>10000:
            raise ValueError('PPT 用户编辑链循环')
        seen.add(offset)
        _,kind,body,end=_record(data,offset)
        if kind!=0x0ff5 or end-body<28:
            raise ValueError('PPT 用户编辑记录无效')
        if end-body>28 and _u32(data,body+28):
            raise ValueError('不支持加密 PPT')
        if doc_id is None:
            doc_id=_u32(data,body+16)
        previous=_u32(data,body+8);directory=_u32(data,body+12)
        _,kind,pos,stop=_record(data,directory)
        if kind!=0x1772:
            raise ValueError('PPT 持久对象目录无效')
        while pos<stop:
            value=_u32(data,pos);pos+=4
            start=value&0xfffff;count=value>>20
            if not count or pos+count*4>stop:
                raise ValueError('PPT 持久对象目录截断')
            for index in range(count):
                persist.setdefault(start+index,_u32(data,pos+index*4))
            pos+=count*4
        if previous==0:
            break
        offset=previous
    if doc_id not in persist:
        raise ValueError('PPT 无法定位当前演示文稿')
    _,kind,start,end=_record(data,persist[doc_id])
    if kind!=0x03e8:
        raise ValueError('PPT 文档容器无效')
    slides=[]
    for flags,kind,body,stop in _children(data,start,end):
        if kind!=0x0ff0 or flags>>4!=0:
            continue
        for _,child,content,child_end in _children(data,body,stop):
            if child==0x03f3:
                slides.append([_u32(data,content),[]])
            elif child in (0x0fa0,0x0fa8) and slides:
                slides[-1][1].extend(_text(data,content-8,child_end))
    if not slides or len(slides)>10000:
        raise ValueError('PPT 没有有效幻灯片列表')
    units=[]
    for page,(pid,outline) in enumerate(slides,1):
        if pid not in persist:
            raise ValueError('PPT 幻灯片引用缺失')
        _,kind,body,end=_record(data,persist[pid])
        if kind!=0x03ee:
            raise ValueError('PPT 幻灯片容器无效')
        inline=_text(data,body,end)
        lines=list(dict.fromkeys([*outline,*inline]))
        units.append({'page':page,'section':f'幻灯片 {page}'+(' · '+lines[0][:100] if lines else ''),'text':'\n'.join(lines)})
        if progress:
            progress(page,len(slides))
    return units

def _pptx(path,progress=None):
    with zipfile.ZipFile(path) as package:
        if sum(entry.file_size for entry in package.infolist())>LIMIT:
            raise ValueError('PPTX 解压内容过大')
        def xml(name):
            if name not in package.namelist() or package.getinfo(name).file_size>16*1024*1024:
                raise ValueError('PPTX 缺少必要XML或XML过大：'+name)
            return ET.fromstring(package.read(name))
        root=xml('ppt/presentation.xml')
        rels={rel.attrib['Id']:rel for rel in xml('ppt/_rels/presentation.xml.rels')}
        slides=[node for node in root.iter() if node.tag.rsplit('}',1)[-1]=='sldId']
        if not slides or len(slides)>10000:
            raise ValueError('PPTX 没有有效幻灯片')
        units=[]
        for page,slide in enumerate(slides,1):
            rid=next((value for key,value in slide.attrib.items() if key.endswith('}id')),None)
            rel=rels.get(rid)
            if rel is None or rel.attrib.get('TargetMode')=='External' or not rel.attrib.get('Type','').endswith('/slide'):
                raise ValueError('PPTX 幻灯片关系无效或为外部链接')
            target=rel.attrib['Target']
            name=posixpath.normpath(target.lstrip('/') if target.startswith('/') else 'ppt/'+target)
            if not name.startswith('ppt/') or '..' in name.split('/'):
                raise ValueError('PPTX 幻灯片路径无效')
            lines=[]
            for paragraph in xml(name).iter():
                if paragraph.tag.rsplit('}',1)[-1]!='p':
                    continue
                text=''.join(node.text or '' for node in paragraph.iter() if node.tag.rsplit('}',1)[-1]=='t')
                if text.strip():
                    lines.append(text.strip())
            units.append({'page':page,'section':f'幻灯片 {page}'+(' · '+lines[0][:100] if lines else ''),'text':'\n'.join(lines)})
            if progress:
                progress(page,len(slides))
        return units

def extract(path,suffix,progress=None):
    try:
        return _ppt(path,progress) if suffix=='ppt' else _pptx(path,progress)
    except ValueError:
        raise
    except (zipfile.BadZipFile,KeyError,ET.ParseError,UnicodeError,struct.error):
        raise ValueError('PPT/PPTX 文件损坏或格式不匹配，请重新保存后上传') from None
