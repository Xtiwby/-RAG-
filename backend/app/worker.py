"""Run durable jobs in a dedicated process: python -m app.worker."""
import asyncio
import os

os.environ['RAG_WORKER_PROCESS']='1'
from . import main  # Register task handlers and authorization checks.
from . import jobs

async def serve():
    if os.getenv('RAG_WORKER_MODE')!='external':
        raise RuntimeError('独立工作进程需要 RAG_WORKER_MODE=external')
    await jobs.start()
    await jobs.supervisor

if __name__ == '__main__':
    asyncio.run(serve())
