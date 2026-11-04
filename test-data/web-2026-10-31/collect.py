"""Collect a small, attributed corpus; no app requests, secrets, uploads or LLM calls.

Run with the project's backend Python. Authored material is in corpus/ and labels/;
this script only downloads, normalizes, hashes and packages research data.
"""
import csv
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "corpus"
RAW = ROOT / "references" / "raw"
LICENSES = ROOT / "licenses"
ALLOWED = {"raw.githubusercontent.com", "www.sqlite.org", "sqlite.org", "aclanthology.org", "fastapi.tiangolo.com", "cn.vuejs.org", "cdn.jsdelivr.net"}
SOURCES = [
    {"id":"S01","title":"Dense Passage Retrieval for Open-Domain Question Answering","file":"S01_DPR_2020.pdf","page_url":"https://aclanthology.org/2020.emnlp-main.550/","url":"https://aclanthology.org/2020.emnlp-main.550.pdf","license":"CC-BY-4.0","license_url":"https://aclanthology.org/faq/copyright/","authors":"Vladimir Karpukhin, Barlas Oguz, Sewon Min, Patrick Lewis, Ledell Wu, Sergey Edunov, Danqi Chen, Wen-tau Yih; ACL 2020","kind":"original_pdf","group":"retrieval","language":"en"},
    {"id":"S02","title":"SQLite FTS5 Extension","file":"S02_SQLite_FTS5_BM25.html","page_url":"https://www.sqlite.org/fts5.html","url":"https://www.sqlite.org/fts5.html","license":"Public-Domain","license_url":"https://www.sqlite.org/copyright.html","authors":"SQLite authors","kind":"cleaned_html","group":"retrieval","language":"en"},
    {"id":"S03","title":"FastAPI 请求文件","file":"S03_FastAPI_请求文件.md","page_url":"https://fastapi.tiangolo.com/zh/tutorial/request-files/","url":"https://raw.githubusercontent.com/fastapi/fastapi/master/docs/zh/docs/tutorial/request-files.md","license":"MIT","license_url":"https://github.com/fastapi/fastapi/blob/master/LICENSE","authors":"Sebastián Ramírez and FastAPI contributors","kind":"cleaned_markdown","group":"backend","language":"zh"},
    {"id":"S04","title":"FastAPI OAuth2、密码哈希与JWT","file":"S04_FastAPI_JWT.md","page_url":"https://fastapi.tiangolo.com/zh/tutorial/security/oauth2-jwt/","url":"https://raw.githubusercontent.com/fastapi/fastapi/master/docs/zh/docs/tutorial/security/oauth2-jwt.md","license":"MIT","license_url":"https://github.com/fastapi/fastapi/blob/master/LICENSE","authors":"Sebastián Ramírez and FastAPI contributors","kind":"cleaned_markdown","group":"backend","language":"zh"},
    {"id":"S05","title":"Vue 3 简介","file":"S05_Vue3_简介.md","page_url":"https://cn.vuejs.org/guide/introduction","url":"https://raw.githubusercontent.com/vuejs-translations/docs-zh-cn/main/src/guide/introduction.md","license":"CC-BY-4.0","license_url":"https://github.com/vuejs-translations/docs-zh-cn/blob/main/LICENSE","authors":"Yuxi (Evan) You and Vue documentation contributors; Chinese translation contributors","kind":"cleaned_markdown","group":"frontend","language":"zh"},
    {"id":"S06","title":"Vue 3 响应式基础","file":"S06_Vue3_响应式基础.md","page_url":"https://cn.vuejs.org/guide/essentials/reactivity-fundamentals","url":"https://raw.githubusercontent.com/vuejs-translations/docs-zh-cn/main/src/guide/essentials/reactivity-fundamentals.md","license":"CC-BY-4.0","license_url":"https://github.com/vuejs-translations/docs-zh-cn/blob/main/LICENSE","authors":"Yuxi (Evan) You and Vue documentation contributors; Chinese translation contributors","kind":"cleaned_markdown","group":"frontend","language":"zh"},
    {"id":"S07","title":"RRF排名融合（中文摘编）","file":"S07_RRF_中文摘编.md","page_url":"https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion","license":"short_paraphrase_only","authors":"Elastic documentation; Chinese notes by dataset curator","kind":"curated_summary","group":"retrieval","language":"zh"},
    {"id":"S08","title":"RAG论文摘要（中文摘编）","file":"S08_RAG_中文摘编.md","page_url":"https://arxiv.org/abs/2005.11401","license":"short_paraphrase_only","authors":"Patrick Lewis et al.; Chinese notes by dataset curator","kind":"curated_summary","group":"retrieval","language":"zh"},
    {"id":"S09","title":"TF-IDF特征表示（中文摘编）","file":"S09_TFIDF_中文摘编.txt","page_url":"https://scikit-learn.org/stable/modules/generated/sklearn.feature_extraction.text.TfidfVectorizer.html","license":"short_paraphrase_only","authors":"scikit-learn documentation; Chinese notes by dataset curator","kind":"curated_summary","group":"retrieval","language":"zh"},
    {"id":"S10","title":"PDF文本提取与OCR（中文摘编）","file":"S10_PDF与OCR_中文摘编.md","page_url":"https://pypdf.readthedocs.io/en/stable/user/extract-text.html","license":"short_paraphrase_only","authors":"pypdf documentation; Chinese notes by dataset curator","kind":"curated_summary","group":"backend","language":"zh"},
]
LICENSE_URLS = {
    "FastAPI_MIT.txt":"https://raw.githubusercontent.com/fastapi/fastapi/master/LICENSE",
    "Vue_docs_CC_BY_4.0.txt":"https://raw.githubusercontent.com/vuejs-translations/docs-zh-cn/main/LICENSE",
    "SQLite_public_domain.html":"https://www.sqlite.org/copyright.html",
    "ACL_copyright_policy.html":"https://aclanthology.org/faq/copyright/",
}

def utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def sha(data):
    return hashlib.sha256(data).hexdigest()

def write_json(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

def fetch(url, name, fallback=None):
    from urllib.parse import urlparse
    if urlparse(url).scheme!="https" or urlparse(url).hostname not in ALLOWED:
        raise ValueError("URL is outside the collection allowlist")
    target=RAW/name
    info=target.with_suffix(target.suffix+".json")
    if target.exists() and info.exists():
        meta=json.loads(info.read_text(encoding="utf-8"))
        data=target.read_bytes()
        if sha(data)!=meta["sha256"]:
            raise ValueError("Cached download checksum mismatch: "+name)
        return data,meta
    urls=[url]+([fallback] if fallback else [])
    for attempt,current_url in enumerate(urls):
        try:
            if urlparse(current_url).hostname not in ALLOWED:raise ValueError("Unexpected fallback host")
            req=urllib.request.Request(current_url,headers={"User-Agent":"RAG-course-public-corpus/1.0"})
            with urllib.request.urlopen(req,timeout=20) as response:
                final=response.geturl()
                if urlparse(final).hostname not in ALLOWED:
                    raise ValueError("Unexpected redirect host")
                data=response.read(20*1024*1024+1)
                if len(data)>20*1024*1024:
                    raise ValueError("Download larger than 20MB")
                meta={"url":url,"final_url":final,"used_fallback":current_url!=url,"retrieved_at":utc(),"sha256":sha(data),"bytes":len(data),"content_type":response.headers.get("Content-Type"),"etag":response.headers.get("ETag"),"last_modified":response.headers.get("Last-Modified")}
            target.write_bytes(data)
            write_json(info,meta)
            return data,meta
        except Exception:
            if attempt==len(urls)-1:
                raise
            print("Retrying via alternate public source:",name,flush=True)

class TextOnly(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts=[];self.skip=0
    def handle_starttag(self,tag,attrs):
        if tag in {"script","style","noscript"}: self.skip+=1
        if not self.skip and tag in {"p","div","h1","h2","h3","h4","li","pre","tr","br"}: self.parts.append("\n\n")
    def handle_endtag(self,tag):
        if tag in {"script","style","noscript"} and self.skip: self.skip-=1
        if not self.skip and tag in {"p","div","h1","h2","h3","h4","li","pre","tr"}: self.parts.append("\n\n")
    def handle_data(self,data):
        if not self.skip:self.parts.append(data)
    def result(self):
        return re.sub(r"\n[ \t]*\n(?:\s*\n)+","\n\n","".join(self.parts)).strip()

class ArticleOnly(TextOnly):
    def __init__(self):
        super().__init__();self.active=False;self.capture_tag=None;self.capture_depth=0
    def handle_starttag(self,tag,attrs):
        values=dict(attrs)
        if not self.active and (tag=="article" or any(c in values.get("class","").split() for c in ["vp-doc","vt-doc"])):
            self.active=True;self.capture_tag=tag;self.capture_depth=0
        if not self.active:return
        if tag==self.capture_tag:self.capture_depth+=1
        if tag in {"pre","figure"}:self.skip+=1
        super().handle_starttag(tag,attrs)
        if not self.skip and re.fullmatch(r"h[1-6]",tag):self.parts.append("#"*int(tag[1])+" ")
    def handle_endtag(self,tag):
        if not self.active:return
        super().handle_endtag(tag)
        if tag in {"pre","figure"} and self.skip:self.skip-=1
        if tag==self.capture_tag:
            self.capture_depth-=1
            if self.capture_depth==0:self.active=False
    def handle_data(self,data):
        if self.active:super().handle_data(data)

def prose_markdown(text):
    # These docs refer to separate runnable examples. Keep prose, not incomplete
    # code or executable snippets. Raw originals are retained for verification.
    text=re.sub(r"\A---\s*\n.*?\n---\s*\n","",text,flags=re.S)
    text=re.sub(r"^\s*\x60\x60\x60[^\n]*\n.*?^\s*\x60\x60\x60\s*$","",text,flags=re.M|re.S)
    text=re.sub(r"<script\b[^>]*>.*?</script>","",text,flags=re.S|re.I)
    text=re.sub(r"^.*\{\*.*\*\}.*$","",text,flags=re.M)
    text=re.sub(r"!\[[^\]]*\]\([^)]*\)","",text)
    text=re.sub(r"\{\s*#[^}]+\}","",text)
    text=re.sub(r"^\s*:::[^\n]*$","",text,flags=re.M)
    text=re.sub(r"^\s*///[^\n]*$","",text,flags=re.M)
    text=re.sub(r"<style\b[^>]*>.*?</style>","",text,flags=re.S|re.I)
    inline=[]
    def keep(match):
        inline.append(match.group(0));return "@@INLINE_"+str(len(inline)-1)+"@@"
    text=re.sub(r"\x60[^\x60\n]+\x60",keep,text)
    text=re.sub(r"<[^>]+>","",text)
    for index,value in enumerate(inline):text=text.replace("@@INLINE_"+str(index)+"@@",value)
    return re.sub(r"\n{3,}","\n\n",html.unescape(text)).strip()

def normalized(text):
    return re.sub(r"\s+","",unicodedata.normalize("NFKC",text)).lower()

def main():
    for folder in [CORPUS,RAW,LICENSES,ROOT/"labels",ROOT/"validation"]:
        folder.mkdir(parents=True,exist_ok=True)
    # Parallel public downloads prevent one slow raw-file endpoint from holding
    # every other source. The SQLite and PDF caches are checksum-verified.
    pending=[s for s in SOURCES if s.get("url")]
    def download(s):
        return s["id"],fetch(s["url"],s["id"]+Path(s["file"]).suffix,s["page_url"] if s["kind"]=="cleaned_markdown" else None)
    with ThreadPoolExecutor(max_workers=4) as pool:
        downloads=dict(pool.map(download,pending))
    records=[]
    for source in SOURCES:
        record=dict(source);path=CORPUS/source["file"]
        if source.get("url"):
            raw,meta=downloads[source["id"]]
            record["download"]=meta
            if source["kind"]=="original_pdf":
                if not raw.startswith(b"%PDF-"):raise ValueError("Not a PDF")
                path.write_bytes(raw);record["changes"]="none; original PDF bytes"
            else:
                text=raw.decode("utf-8-sig")
                header=f'# {source["id"]}: {source["title"]}\n\n来源：{source["page_url"]}\n作者：{source["authors"]}\n许可：{source["license"]}（见licenses目录）\n采集：{meta["retrieved_at"]}\n\n外部技术资料，不代表本项目实际实现、配置或性能。\n\n'
                if source["kind"]=="cleaned_html":
                    parser=TextOnly();parser.feed(text)
                    blocks=parser.result().split("\n\n")
                    text="<!doctype html><html lang='en'><meta charset='utf-8'><title>SQLite FTS5</title><body>"+''.join("<p>"+html.escape(p)+"</p>\n\n" for p in [header]+blocks)+"</body></html>"
                    record["changes"]="HTML normalized to inert text paragraphs; scripts, styles, images and interactive elements removed"
                else:
                    if "text/html" in (meta["content_type"] or ""):
                        parser=ArticleOnly();parser.feed(text);text=parser.result()
                        if len(text)<200:raise ValueError("Could not locate article body: "+source["id"])
                    else:text=prose_markdown(text)
                    text=header+text+"\n"
                    record["changes"]="source prose retained; code fences, external code includes, source header script blocks and images omitted; attribution header added"
                path.write_text(text,encoding="utf-8")
        else:
            record["verified_at"]="2026-10-31"
            record["changes"]="short Chinese paraphrase manually checked against the primary page; no original full text redistributed"
        data=path.read_bytes()
        record.update(path="corpus/"+path.name,bytes=len(data),sha256=sha(data),version="web-2026-10-31",include_in_baseline=True)
        records.append(record)
        print(source["id"],path.name,len(data),"bytes",flush=True)
    def license_download(item):
        name,url=item
        fallback=url.replace("https://raw.githubusercontent.com/fastapi/fastapi/master/","https://cdn.jsdelivr.net/gh/fastapi/fastapi@master/").replace("https://raw.githubusercontent.com/vuejs-translations/docs-zh-cn/main/","https://cdn.jsdelivr.net/gh/vuejs-translations/docs-zh-cn@main/")
        raw,meta=fetch(url,"license_"+name,fallback if fallback!=url else None)
        (LICENSES/name).write_bytes(raw)
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(license_download,LICENSE_URLS.items()))
    from pypdf import PdfReader
    paper=PdfReader(CORPUS/SOURCES[0]["file"])
    records[0]["pdf_pages"]=len(paper.pages)
    records[0]["printed_pages"]="6769-6781"
    pages=[]
    for i,page in enumerate(paper.pages,1):
        text=page.extract_text() or ""
        pages.append({"physical_page":i,"printed_page":6768+i,"text_chars":len(text),"text":text})
    write_json(ROOT/"validation"/"S01_pdf_page_text.json",pages)
    duplicate=ROOT/"scenarios"/"duplicate"/SOURCES[2]["file"]
    duplicate.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(CORPUS/SOURCES[2]["file"],duplicate)
    write_json(ROOT/"manifest.json",{"dataset_id":"web-2026-10-31","created_at":utc(),"sources":records,"optional_fixtures":[{"path":str(duplicate.relative_to(ROOT)).replace("\\","/"),"role":"exact_duplicate","parent_source":"S03","sha256":sha(duplicate.read_bytes()),"include_in_baseline":False}],"warning":"Do not ingest labels, references, licenses or validation. Summaries are not original documents; external examples are not project implementation facts."})
    with (ROOT/"sources.csv").open("w",encoding="utf-8-sig",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=["id","title","path","page_url","license","kind","language","group","bytes","sha256"])
        writer.writeheader();writer.writerows({k:r.get(k,"") for k in writer.fieldnames} for r in records)
    print("Collected",len(records),"sources; PDF",len(pages),"physical pages; one exact duplicate fixture")

if __name__=="__main__":main()
