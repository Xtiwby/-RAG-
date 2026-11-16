"""Compare the QA SSE path with a repeatable provider simulation and isolated corpus."""
import argparse
import asyncio
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parent
PARAMETERS = [
    'NX01的参数为alpha=0.71，retention_days=21。',
    '保留期限以天为单位，不是小时。',
]
TEMPERATURE = ['NX01最低工作温度为-11摄氏度。']
HUMIDITY = ['NX01使用被动通风控制机壳湿度，不使用电动风扇。']
MAINTENANCE = ['NX01维护指南要求先断电再打开机壳，不允许带电开壳。']
MISSING = 'NX01已经在本项目中实测达到多少QPS？请给出确切数值。'
OVERVIEW = '请概括NX01的参数、保留期限、最低温度、湿度控制和开壳维护条件。'
CASES = {
    'NX01-A1': ('NX01的alpha参数和保留期限分别是多少，期限单位是什么？', PARAMETERS),
    'NX01-A2': ('What is the lowest operating temperature of NX01?', TEMPERATURE),
    'NX01-A3': ('NX01能否在不断电的情况下打开机壳，操作顺序是什么？', MAINTENANCE),
    'NX01-A4': ('Explain how NX01 regulates enclosure humidity and whether it uses an electric fan.', HUMIDITY),
    'NX01-M1': (OVERVIEW, PARAMETERS + TEMPERATURE + HUMIDITY + MAINTENANCE),
    'NX01-U1': (MISSING, []),
}


class CaptureSSE:
    def __init__(self, app, records):
        self.app = app
        self.records = records

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['path'] != '/api/qa/stream':
            await self.app(scope, receive, send)
            return
        started = perf_counter()

        async def capture(message):
            if message['type'] == 'http.response.body':
                body = message.get('body', b'').decode('utf-8')
                for line in body.splitlines():
                    if line.startswith('data: '):
                        self.records.append(((perf_counter() - started) * 1000, json.loads(line[6:])))
            await send(message)

        await self.app(scope, receive, capture)


class SimulatedSSE(httpx.AsyncByteStream):
    def __init__(self, parts):
        self.parts = parts

    async def __aiter__(self):
        for part in self.parts:
            await asyncio.sleep(.012)
            yield ('data: ' + json.dumps({'choices': [{'delta': {'content': part}}]},
                                         ensure_ascii=False) + '\n\n').encode()
        yield b'data: [DONE]\n\n'


def question_key(question):
    return unicodedata.normalize('NFKC', question)


class Provider:
    def __init__(self):
        self.client_count = 0
        self.calls = []
        self.answer_keys = {question_key(question): facts for question, facts in CASES.values()}
        self.client_type = httpx.AsyncClient

    def new_client(self, **kwargs):
        self.client_count += 1
        return self.client_type(transport=httpx.MockTransport(self.respond), **kwargs)

    async def respond(self, request):
        data = json.loads(json.loads(request.content)['messages'][-1]['content'])
        await asyncio.sleep(.040)  # The same fixed round trip on every provider call.
        if 'retrieval_queries_schema' in data:
            self.calls.append('query_rewrite')
            question = data['question']
            translations = {
                CASES['NX01-A2'][0]: 'NX01最低工作温度是多少？',
                CASES['NX01-A4'][0]: 'NX01怎样用通风控制机壳湿度，是否使用电动风扇？',
            }
            parts = [{'chinese': 'NX01的参数和期限是多少？', 'english': 'NX01 retention parameter'},
                     {'chinese': 'NX01的工作温度、通风和维护要求是什么？', 'english': 'NX01 temperature ventilation maintenance'}]
            output = {'chinese': translations.get(question, question),
                      'english': question if question in translations else 'NX01 device manual',
                      'subquestions': parts if question == question_key(OVERVIEW) else []}
            parts = [json.dumps(output, ensure_ascii=False)]
        elif 'verification_schema' in data:
            self.calls.append('grounding_judge')
            chunks = {item['reference']: item['text'] for item in data['evidence_json']}
            verdicts = []
            for claim in data['claims']:
                sentence = re.sub(r'\[\d+\]', '', claim['text']).strip()
                quotes = [{'reference': ref, 'quote': sentence} for ref in claim['references']
                          if sentence in chunks[ref]]
                verdicts.append({'claim_id': claim['claim_id'],
                                 'verdict': 'supported' if len(quotes) == len(claim['references']) else 'insufficient',
                                 'quotes': quotes})
            parts = [json.dumps({'verdicts': verdicts}, ensure_ascii=False)]
        elif 'coverage_schema' in data:
            self.calls.append('coverage_review')
            parts = [json.dumps({'items': [{'requirement': '问题要点',
                'status': 'covered' if data['accepted_answer'] else 'insufficient'}]}, ensure_ascii=False)]
        else:
            self.calls.append('chat')
            facts = self.answer_keys[data['question']]
            parts = []
            for fact in facts:
                reference = next((item['reference'] for item in data['evidence_json']
                                  if fact in item['text']), None)
                if reference is not None:
                    parts.append(f'{fact}[{reference}]\n')
            if not parts:
                parts = ['资料显示实测 QPS 为 999。[1]\n']
        return httpx.Response(200, stream=SimulatedSSE(parts))


def validate(client, headers, events, facts, evidence_dir):
    first, last = events[0][1], events[-1][1]
    assert first['type'] == 'meta' and last['type'] == 'done', [event['type'] for _, event in events]
    answer = ''.join(event['content'] for _, event in events if event['type'] == 'token')
    refs = [int(n) for n in re.findall(r'\[(\d+)\]', answer)]
    citations = first['citations']
    if facts:
        assert all(fact in answer for fact in facts), f'Expected {facts}, got {answer}'
        assert len(refs) == len(facts) and all(0 < n <= len(citations) for n in refs), answer
    else:
        assert '证据不足' in answer and '999' not in answer and not refs, answer
    for number in set(refs):
        cite = citations[number - 1]
        response = client.get(f"/api/documents/{cite['document_id']}?compact=true&chunk_id={cite['chunk_id']}",
                              headers=headers)
        assert response.status_code == 200
        original = client.get(f"/api/documents/{cite['document_id']}/original", headers=headers)
        assert original.status_code == 200 and original.content == (evidence_dir / cite['name']).read_bytes()
        row = response.json()
        assert row['version'] == cite['version'] and any(
            chunk['id'] == cite['chunk_id'] and chunk['text'] == cite['text'] for chunk in row['chunks'])
    response = client.get(f"/api/conversations/{first['conversation_id']}", headers=headers)
    assert response.status_code == 200
    saved = response.json()['messages'][-1]
    assert saved['content'] == answer and saved['citations'] == citations and saved['status'] == 'complete'
    assert len([check for check in saved['verification'] if check['status'] == 'supported']) == len(facts)
    return {'answer': answer, 'ranked_chunks': [c['chunk_id'] for c in citations],
            'cited_chunks': [citations[n - 1]['chunk_id'] for n in refs]}


def run(args, directory):
    os.environ.update(RAG_DATA_DIR=directory, RAG_DATABASE_URL='', RAG_FILE_STORAGE='local',
                      RAG_WORKER_MODE='embedded', RAG_WORKER_PROCESS='0',
                      KIMI_API_KEY='benchmark-local-simulation', KIMI_BASE_URL='https://provider.invalid/v1',
                      EMBEDDING_BASE_URL='', EMBEDDING_MODEL='')
    sys.path.insert(0, str(args.backend_dir))
    from app import ingestion, local_models, rag, retrieval_planner, settings, store
    from app.main import app

    captured, retrieval = [], []
    app.add_middleware(CaptureSSE, records=captured)
    provider = Provider()
    retrieve = rag.retrieve

    async def timed_retrieve(*args, **kwargs):
        start = perf_counter()
        result = await retrieve(*args, **kwargs)
        retrieval.append((perf_counter() - start) * 1000)
        return result

    with TestClient(app) as client, patch.object(rag, 'retrieve', timed_retrieve), \
            patch.object(rag.httpx, 'AsyncClient', provider.new_client):
        login = client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin123'})
        login.raise_for_status()
        headers = {'Authorization': 'Bearer ' + login.json()['token']}
        kb = client.post('/api/knowledge-bases', headers=headers, json={'name': 'QA 基准手册'})
        kb.raise_for_status()
        kid = kb.json()['id']
        config = settings.read()['settings']
        config['retrieval'].update(mode='hybrid', top_k=12, cross_language=True)
        config['answer_quality'].update(enabled=True, repair=True)
        if args.model_dir:
            local_models.MODEL_ROOT = args.model_dir
            config['embedding'].update(provider='local', model='BAAI/bge-m3', device=args.device)
            config['reranking'].update(provider='local', device=args.device)
        for path in sorted(args.corpus.glob('*.md')):
            original = store.UPLOADS / path.name
            original.write_bytes(path.read_bytes())
            did = ingestion.create_document(kid, path.name, 'md', 'text', str(original))
            rag.index_units(did, rag.parse_local(original, 'md'))
        if not store.one('SELECT id FROM chunks LIMIT 1'):
            raise RuntimeError('Benchmark corpus contains no indexable chunks')
        settings.save(settings.RuntimeSettings.model_validate(config), 0, login.json()['user'])
        rows = []
        for run_index in range(args.repeats + 1):
            for case_id, (question, facts) in CASES.items():
                captured.clear()
                retrieval.clear()
                retrieval_planner._cache.clear()
                provider.client_count = 0
                provider.calls.clear()
                previous_call = store.one('SELECT COALESCE(MAX(id),0) AS id FROM model_calls')['id']
                start = perf_counter()
                response = client.post('/api/qa/stream', headers=headers,
                                       json={'query': question, 'knowledge_base_ids': [kid]})
                elapsed = (perf_counter() - start) * 1000
                response.raise_for_status()
                first_token = next((ms for ms, event in captured if event['type'] == 'token'), None)
                if first_token is None:
                    raise RuntimeError(f'{case_id}: SSE did not deliver a token. {response.text[-1000:]}')
                model_calls = store.rows('SELECT kind,latency_ms FROM model_calls WHERE id>? ORDER BY id',
                                         (previous_call,))
                quality = validate(client, headers, captured, facts, args.corpus)
                if run_index:
                    rows.append({'case': case_id, 'question': question, 'repeat': run_index,
                                 'retrieval_ms': round(retrieval[0], 1), 'first_token_ms': round(first_token, 1),
                                 'total_ms': round(elapsed, 1), 'judge_calls': provider.calls.count('grounding_judge'),
                                 'chat_clients': provider.client_count, 'model_calls': model_calls, **quality})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend-dir', type=Path, default=ROOT)
    parser.add_argument('--corpus', type=Path, default=ROOT.parent / 'test-data/quality-v010-synthetic/corpus')
    parser.add_argument('--model-dir', type=Path, help='Use offline BGE and reranker weights instead of TF-IDF')
    parser.add_argument('--device', choices=['cpu', 'cuda', 'auto'], default='auto')
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    with TemporaryDirectory(prefix='rag-qa-benchmark-') as directory:
        rows = run(args, directory)
    keys = ['retrieval_ms', 'first_token_ms', 'total_ms', 'judge_calls', 'chat_clients']
    summary = {case: {key: median(row[key] for row in rows if row['case'] == case) for key in keys}
               for case in CASES}
    output = {'profile': 'local-bge' if args.model_dir else 'tfidf', 'provider': 'simulated',
              'warmup_repeats': 1, 'summary': summary, 'runs': rows}
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

