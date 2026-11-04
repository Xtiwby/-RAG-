"""Create a separate synthetic acceptance set, never ingest labels/answers.

20 fictional manuals x 4 answerable questions + 20 unsupported questions.
Correlated variants are not 100 independent facts or a real-world benchmark.
Refuses to overwrite an existing locked dataset.
"""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]/'test-data/quality-v010-synthetic'

def build():
    if (ROOT/'manifest.json').exists():
        print('Locked set already exists; use validation, not regeneration.');return
    (ROOT/'corpus').mkdir(parents=True,exist_ok=True)
    (ROOT/'labels').mkdir(exist_ok=True)
    cases=[];files=[]
    for i in range(1,21):
        code=f'NX{i:02d}';sid=f'N{i:02d}';english=i%2==0
        alpha=f'{(70+i)/100:.2f}';minimum=-10-i;retention=20+i
        source=(f'# Synthetic acceptance manual {code}\n\nThis is fictional test material, not a deployment or performance record of this project.\n\n'
            f'{code} controls enclosure humidity using passive ventilation, not an electric fan.\n\n'
            f'For {code}, alpha={alpha}; retention_days={retention}. Retention is measured in days, not hours.\n\n'
            f'The minimum operating temperature of {code} is {minimum} degrees Celsius.\n\n'
            f'The maintenance guide for {code} says to disconnect power before opening the enclosure. It does not authorize opening a powered enclosure.\n\n'
            'The manual contains no project QPS, customer count, medical outcome, purchase price or measured deployment gains.\n') if english else (
            f'# 合成验收手册 {code}\n\n以下为虚构验收场景，不是本项目真实部署或性能记录。\n\n'
            f'{code}使用被动通风控制机壳湿度，不使用电动风扇。\n\n'
            f'{code}的参数为alpha={alpha}，retention_days={retention}。保留期限以天为单位，不是小时。\n\n'
            f'{code}最低工作温度为{minimum}摄氏度。\n\n'
            f'{code}维护指南要求先断电再打开机壳，不允许带电开壳。\n\n'
            '本手册未提供项目QPS、客户人数、医疗效果、采购价格或实际部署收益。\n')
        path=ROOT/'corpus'/f'{sid}_{code}.md';path.write_text(source,encoding='utf-8')
        files.append({'source_id':sid,'path':path.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        samples=[
            (f'{code}的alpha参数和保留期限分别是多少，期限单位是什么？',[alpha,str(retention)],'参数与单位'),
            (f'What is the lowest operating temperature of {code}?',[str(minimum)],'中英检索'),
            (f'{code}能否在不断电的情况下打开机壳，操作顺序是什么？',[], '否定与操作条件'),
            (f'Explain how {code} regulates enclosure humidity and whether it uses an electric fan.',[], '跨语言与复合要点')]
        for j,(question,numbers,category) in enumerate(samples,1):
            facts=[f'alpha={alpha}',f'{retention}天/days'] if j==1 else [f'{minimum}摄氏度/Celsius'] if j==2 else ['先断电再开壳/disconnect power before opening','不允许带电开壳/not while powered'] if j==3 else ['被动通风/passive ventilation','不使用电动风扇/no electric fan']
            cases.append({'id':f'{code}-A{j}','source_ids':[sid],'question':question,'answerable':True,
                'category':category,'required_facts':facts,'required_numbers':numbers,'group':code,
                'expected_behavior':'回答全部关键点并引用手册；含义判分需人工复核，不以词项命中代替正确性'})
        cases.append({'id':f'{code}-U1','source_ids':[],'scope_source_ids':[sid],
            'question':f'{code}已经在本项目中实测达到多少QPS？请给出确切数值。','answerable':False,
            'category':'无项目实测证据','required_facts':[],'required_numbers':[],'group':code,
            'expected_behavior':'明确说明缺少项目实测证据，不编造QPS数值'})
    labels=ROOT/'labels'/'questions.jsonl'
    labels.write_text('\n'.join(json.dumps(c,ensure_ascii=False) for c in cases)+'\n',encoding='utf-8')
    manifest={'version':'quality-synthetic-v1','locked':True,'cases':len(cases),'answerable':80,'unanswerable':20,
        'independent_source_groups':20,'synthetic':True,'used_to_tune':False,
        'limitations':'同源问题相关；开发者编写，不是第三方盲测。没有扫描PDF、真实表格或图像，不能替代多模态验收。',
        'corpus':files,'labels_path':labels.relative_to(ROOT).as_posix(),'labels_sha256':hashlib.sha256(labels.read_bytes()).hexdigest()}
    (ROOT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Locked',len(cases),'questions / 20 fictional manuals; labels excluded from corpus.')

if __name__=='__main__':build()
