"""Package only this public dataset; exclude caches and all project/runtime data."""
import hashlib
import json
import re
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent
REPORT=json.loads((ROOT/"validation"/"collection_check.json").read_text(encoding="utf-8"))
if REPORT["errors"]:raise SystemExit("Validation errors must be fixed before packaging")
MANIFEST=json.loads((ROOT/"manifest.json").read_text(encoding="utf-8"))
for source in MANIFEST["sources"]:
    if hashlib.sha256((ROOT/source["path"]).read_bytes()).hexdigest()!=source["sha256"]:
        raise SystemExit("Source checksum changed: "+source["id"])
TEXT={".md",".txt",".json",".jsonl",".csv",".html",".py"}
files=[]
for path in ROOT.rglob("*"):
    if not path.is_file() or "__pycache__" in path.parts:continue
    if path.suffix not in TEXT|{".pdf",".png"}:raise SystemExit("Unexpected artifact type: "+path.name)
    if path.name.startswith(".env") or path.suffix in {".db",".sqlite",".sqlite3"}:raise SystemExit("Forbidden runtime data")
    if path.suffix in TEXT and re.search(r"sk-[A-Za-z0-9]{20,}",path.read_text(encoding="utf-8-sig")):
        raise SystemExit("Potential secret pattern in dataset: "+path.name)
    files.append(path)
full=ROOT.parent/"RAG网络测试集_2026-10-31.zip"
lite=ROOT.parent/"RAG入库资料_不含答案_2026-10-31.zip"
for path in [full,lite]:
    if path.exists():raise SystemExit("Archive already exists; choose a new version instead of overwriting: "+path.name)
with zipfile.ZipFile(full,"x",compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
    for path in sorted(files):archive.write(path,ROOT.name+"/"+path.relative_to(ROOT).as_posix())
with zipfile.ZipFile(lite,"x",compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
    for path in sorted(files):
        rel=path.relative_to(ROOT)
        if rel.parts[0] in {"corpus","licenses"} or path.name in {"manifest.json","IMPORT_README.md"}:
            if path.name=="manifest.json":
                copy={k:v for k,v in MANIFEST.items() if k not in {"optional_fixtures","qa_count","grounding_count"}}
                copy["package"]="corpus_only; no answer labels, optional fixtures or validation payloads"
                archive.writestr("入库资料/manifest.json",json.dumps(copy,ensure_ascii=False,indent=2))
            else:archive.write(path,"入库资料/"+rel.as_posix())
for path in [full,lite]:
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:raise SystemExit("Archive CRC check failed")
        print(json.dumps({"path":str(path),"bytes":path.stat().st_size,"entries":len(archive.namelist()),"sha256":hashlib.sha256(path.read_bytes()).hexdigest()},ensure_ascii=False))
