"""Optional live smoke test: sends only synthetic evidence, never prints credentials."""
import asyncio
import sys
from app.main import rag

async def main():
    evidence=[{'name':'连通性测试','section':'测试','page':None,'version':'v1',
               'text':'测试知识库的名称是知源。'}]
    text=''
    try:
        async for token in rag.llm_stream(rag.evidence_prompt('测试知识库叫什么？请只用一句话回答并引用。',evidence),max_tokens=256):
            text+=token
        if not rag.valid_citations(text,1):
            print('MODEL_CHECK_FAILED: response did not contain valid evidence citations')
            return 1
        print('MODEL_CHECK_OK: real streaming response received; citation [1] verified')
        return 0
    except Exception as error:
        detail=getattr(error,'detail',None) or (str(error) if isinstance(error,RuntimeError) else type(error).__name__)
        print('MODEL_CHECK_FAILED: '+detail)
        return 1

if __name__=='__main__':
    sys.exit(asyncio.run(main()))
