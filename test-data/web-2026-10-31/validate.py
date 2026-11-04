"""Offline collection QA; never connects to the app or an external model."""
import csv
import hashlib
import json
import os
import re
import sys
import tempfile
import unicodedata
import zipfile
from pathlib import Path
from pypdf import PdfReader
from PIL import Image
from collect import ROOT, TextOnly, normalized, write_json

def norm(text):
    return normalized(text).replace("​","").replace("­","").replace("`","").replace("*","")

def main():
    manifest=json.loads((ROOT/"manifest.json").read_text(encoding="utf-8"))
    sources={s["id"]:s for s in manifest["sources"]}
    qa=[json.loads(line) for line in (ROOT/"labels"/"qa.jsonl").read_text(encoding="utf-8").splitlines() if line]
    checks=[json.loads(line) for line in (ROOT/"labels"/"grounding.jsonl").read_text(encoding="utf-8").splitlines() if line]
    ocr=json.loads((ROOT/"labels"/"ocr_cases.json").read_text(encoding="utf-8"))
    reports=json.loads((ROOT/"labels"/"report_cases.json").read_text(encoding="utf-8"))
    errors=[];texts={};pages={};parsing=[]
    for sid,source in sources.items():
        path=ROOT/source["path"];data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=source["sha256"]:errors.append(sid+": checksum mismatch")
        if path.suffix==".pdf":
            pages[sid]=[p.extract_text() or "" for p in PdfReader(path).pages]
            text="\n".join(pages[sid])
        elif path.suffix==".html":
            parser=TextOnly();parser.feed(data.decode("utf-8-sig"));text=parser.result()
        else:text=data.decode("utf-8-sig")
        if not text.strip():errors.append(sid+": empty text")
        texts[sid]=text
    if len(qa)!=32 or len({q["id"] for q in qa})!=len(qa):errors.append("Invalid QA count or IDs")
    evidence_results=[]
    for question in qa:
        if question["answerable"] and (not question["source_ids"] or not question["evidence"]):
            errors.append(question["id"]+": missing source evidence")
        if not question["answerable"] and (question["source_ids"] or question["evidence"]):
            errors.append(question["id"]+": negative case unexpectedly has gold answer evidence")
        for evidence in question["evidence"]:
            sid=evidence["source_id"]
            if sid not in sources:
                errors.append(question["id"]+": unknown source");continue
            text=texts[sid]
            if "physical_page" in evidence:
                index=evidence["physical_page"]-1
                if index<0 or index>=len(pages.get(sid,[])):
                    errors.append(question["id"]+": invalid physical page");continue
                text=pages[sid][index]
            found=norm(evidence["quote"]) in norm(text)
            evidence_results.append({"question":question["id"],"source":sid,"quote":evidence["quote"],"physical_page":evidence.get("physical_page"),"found":found})
            if not found:errors.append(question["id"]+"/"+sid+": evidence quote not found: "+evidence["quote"])
    for item in checks:
        if item["expected_verdict"] not in {"supported","contradicted","insufficient"}:errors.append(item["id"]+": invalid verdict")
        if any(sid not in sources for sid in item["source_ids"]):errors.append(item["id"]+": unknown grounding source")
    for item in reports:
        if any(sid not in sources for sid in item["source_ids"]):errors.append(item["id"]+": unknown report source")
    for item in ocr:
        if not (ROOT/item["input"]).is_file():errors.append(item["id"]+": missing OCR input")
    # Test the exact production parsers, but migration/settings must use a new
    # temporary DB, not the user's live data. No app.main import or background jobs.
    with tempfile.TemporaryDirectory(prefix="rag-web-corpus-qa-") as temp:
        os.environ["RAG_DATA_DIR"]=temp
        sys.path.insert(0,str(ROOT.parents[1]/"backend"))
        from app.rag import parse_local
        for sid,source in sources.items():
            path=ROOT/source["path"]
            units=parse_local(path,path.suffix[1:])
            size=sum(len(unit["text"]) for unit in units)
            if not units or not size:errors.append(sid+": project parser extracted no text")
            parsing.append({"source":sid,"units":len(units),"text_chars":size})
    fixtures=manifest["optional_fixtures"]
    for number in [1,2]:
        path=ROOT/"scenarios"/"ocr"/f"S01_DPR_page{number:02d}.png"
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width,height=image.size
            if width<1000 or height<1000:errors.append(path.name+": insufficient raster resolution")
        item={"path":path.relative_to(ROOT).as_posix(),"role":"pdf_page_raster","parent_source":"S01","physical_page":number,"printed_page":6768+number,"dpi":130,"width":width,"height":height,"license":"CC-BY-4.0","source_url":sources["S01"]["page_url"],"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"include_in_baseline":False,"changes":"original PDF page rendered to PNG without content edits; no OCR/model call"}
        fixtures=[f for f in fixtures if f.get("path")!=item["path"]]+[item]
    duplicate=next(f for f in fixtures if f["role"]=="exact_duplicate")
    if (ROOT/duplicate["path"]).read_bytes()!=(ROOT/sources["S03"]["path"]).read_bytes():errors.append("Duplicate is not byte-identical")
    manifest["optional_fixtures"]=fixtures
    manifest["qa_count"]=len(qa);manifest["grounding_count"]=len(checks)
    write_json(ROOT/"manifest.json",manifest)
    with (ROOT/"labels"/"results_template.csv").open("w",encoding="utf-8-sig",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=["question_id","mode","answerable","expected_sources","retrieved_sources","retrieval_hit","answer_supported","refusal_correct","citation_location_correct","latency_ms","notes"])
        writer.writeheader()
        for question in qa:
            for mode in ["vector","keyword","hybrid"]:
                writer.writerow({"question_id":question["id"],"mode":mode,"answerable":question["answerable"],"expected_sources":";".join(question["source_ids"])})
    report={"source_count":len(sources),"qa_count":len(qa),"answerable":sum(q["answerable"] for q in qa),"unanswerable":sum(not q["answerable"] for q in qa),"grounding_cases":len(checks),"ocr_cases":len(ocr),"report_cases":len(reports),"evidence_quotes":len(evidence_results),"verified_quotes":sum(item["found"] for item in evidence_results),"project_parser":parsing,"errors":errors,"llm_calls":0,"live_database_modified":False,"notes":"Quote presence and parser validation only; gold answer semantics are curator annotations, not a measured model score. Raster images visually reviewed separately."}
    write_json(ROOT/"validation"/"evidence_check.json",evidence_results)
    write_json(ROOT/"validation"/"collection_check.json",report)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if errors:raise SystemExit(1)

if __name__=="__main__":main()
