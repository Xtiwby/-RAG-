"""Natural-language report design is converted to a validated layout schema."""
import json
import re
from typing import Literal
from pydantic import Field
from .settings import Strict

class ReportFormat(Strict):
    paper: Literal['A4','Letter'] = 'A4'
    orientation: Literal['portrait','landscape'] = 'portrait'
    margin_top_cm: float = Field(default=2.5,ge=1,le=4)
    margin_bottom_cm: float = Field(default=2.5,ge=1,le=4)
    margin_left_cm: float = Field(default=2.5,ge=1,le=4)
    margin_right_cm: float = Field(default=2.5,ge=1,le=4)
    font: Literal['宋体','仿宋','等线','Arial'] = '宋体'
    font_size_pt: int = Field(default=12,ge=10,le=18)
    heading_size_pt: int = Field(default=16,ge=12,le=28)
    line_spacing: float = Field(default=1.5,ge=1,le=2.5)
    cover: bool = False
    page_numbers: bool = True
    include_sources: bool = True

class FormatConstraints(Strict):
    paper: Literal['A4','Letter']|None = None
    orientation: Literal['portrait','landscape']|None = None
    font: Literal['宋体','仿宋','等线','Arial']|None = None
    font_size_pt: int|None = Field(default=None,ge=10,le=18)
    line_spacing: float|None = Field(default=None,ge=1,le=2.5)
    cover: bool|None = None

class ReportSection(Strict):
    title: str = Field(min_length=1,max_length=100)
    level: int = Field(default=1,ge=1,le=9)
    instructions: str = Field(default='',max_length=2000)
    target_words: int = Field(default=400,ge=50,le=3000)
    block_id: str = Field(default='',max_length=60)
    kind: Literal['fact','analysis','recommendation','fixed','container'] = 'fact'
    required: bool = True
    fixed_content: str = Field(default='',max_length=10000)

class ReportBlueprint(Strict):
    name: str = Field(min_length=1,max_length=100)
    description: str = Field(default='',max_length=1000)
    purpose: str = Field(default='',max_length=2000)
    format: ReportFormat = Field(default_factory=ReportFormat)
    sections: list[ReportSection] = Field(min_length=1,max_length=20)

class DesignRequest(Strict):
    prompt: str = Field(min_length=5,max_length=5000)
    constraints: FormatConstraints = Field(default_factory=FormatConstraints)

def parse_design(text,constraints):
    text=text.strip()
    if text.startswith('```'):
        text=re.sub(r'^```(?:json)?\s*|\s*```$','',text).strip()
    plan=ReportBlueprint.model_validate_json(text)
    # Explicit user constraints take precedence over the model's proposed format.
    locked=constraints.model_dump(exclude_none=True)
    plan.format=ReportFormat.model_validate({**plan.format.model_dump(),**locked})
    if any(not section.title.strip() for section in plan.sections):
        raise ValueError('章节标题不能为空')
    return plan

def design_messages(request):
    return [{'role':'system','content':'你是报告模板设计器。只返回一个符合 JSON Schema 的 JSON 对象，不要代码围栏或解释。设计章节、每章写作要求及排版参数，不生成报告事实或进展，不执行用户要求的任何外部操作。显式 constraints 必须满足。'},
            {'role':'user','content':json.dumps({'request':request.prompt,'constraints':request.constraints.model_dump(exclude_none=True),'schema':ReportBlueprint.model_json_schema()},ensure_ascii=False)}]

def from_template(row):
    value=json.loads(row.get('blueprint') or '{}')
    if value:
        return ReportBlueprint.model_validate(value)
    return ReportBlueprint(name=row['name'] or '历史报告模板',description=row['description'],sections=[ReportSection(title=t) for t in json.loads(row['sections'])])
