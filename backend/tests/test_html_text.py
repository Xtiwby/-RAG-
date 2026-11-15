import pytest

from app import html_text, rag


def units(html):
    return html_text.parse_bytes(html.encode('utf-8'))


def test_headings_become_sections_and_scripts_are_dropped():
    parsed = units('<h1>第一章</h1><p>网页<strong>正文</strong></p>'
                   '<script>ignore_script()</script><style>.x{color:red}</style>')
    assert parsed[0]['section'] == '第一章'
    joined = ' '.join(item['text'] for item in parsed)
    assert '网页正文' in joined
    assert 'ignore_script' not in joined and 'color:red' not in joined


def test_tables_lists_and_image_alt_text_survive():
    parsed = units('<h2>召回指标</h2><table><tr><td>召回率</td><td>0.8</td></tr></table>'
                   '<ul><li>条目一</li></ul><img alt="架构图说明" src="a.png">')
    texts = [item['text'] for item in parsed]
    assert '召回指标' in texts
    assert '召回率 | 0.8' in texts
    assert '条目一' in texts
    assert any('架构图说明' in text for text in texts)
    assert all(item['section'] == '召回指标' for item in parsed)


def test_declared_charset_entities_and_no_units_falls_back_to_title():
    raw = '<meta charset="gb18030"><h3>编码</h3><p>中文&nbsp;测试 &amp; 版本</p>'.encode('gb18030')
    parsed = html_text.parse_bytes(raw)
    assert '中文 测试 & 版本' in [item['text'] for item in parsed]

    titled = html_text.parse_bytes('<html><head><title>只有标题</title></head><body></body></html>'.encode())
    assert titled == [{'page': None, 'section': '正文', 'text': '只有标题'}]


def test_truncation_caps_total_characters():
    parsed = html_text.parse_bytes(('<p>' + '字' * (html_text.MAX_CHARS + 5000) + '</p>').encode())
    assert 0 < sum(len(item['text']) for item in parsed) <= html_text.MAX_CHARS
    assert parsed[0]['text'].startswith('字')


def test_html_ingestion_keeps_text_but_not_script(tmp_path):
    path = tmp_path / 'page.html'
    path.write_bytes('<h2>验收</h2><p>网页正文</p><script>ignore_script()</script>'.encode())
    parsed = rag.parse_local(path, 'html')
    joined = ' '.join(item['text'] for item in parsed)
    assert '网页正文' in joined and 'ignore_script' not in joined
    assert parsed[0]['section'] == '验收'


def test_json_payload_is_flattened_into_readable_lines(tmp_path):
    path = tmp_path / 'payload.json'
    path.write_text('{"message":"接口记录","rows":[{"title":"第一条"}]}', encoding='utf-8')
    parsed = rag.parse_local(path, 'json')
    joined = '\n'.join(item['text'] for item in parsed)
    assert 'message: 接口记录' in joined
    assert 'rows[1].title: 第一条' in joined
    assert all(item['section'] == 'JSON 数据' for item in parsed)


def test_broken_json_is_reported_as_bad_content(tmp_path):
    path = tmp_path / 'broken.json'
    path.write_text('{not json', encoding='utf-8')
    with pytest.raises(ValueError, match='JSON'):
        rag.parse_local(path, 'json')
