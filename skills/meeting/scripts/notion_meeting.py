#!/usr/bin/env python3
"""회의록 JSON → Notion 회의록 페이지 + 일정 DB 액션아이템 생성.

사용: python3 notion_meeting.py <입력.json>
출력: {"meeting_url": ..., "meeting_id": ..., "action_item_ids": [...]} (stdout JSON)
"""
import json
import sys
import urllib.request

import os

CONFIG_PATH = os.path.expanduser(os.environ.get('SHOP_CONFIG', '~/.claude/.comad/select-shop.json'))
CLAUDE_JSON = os.path.expanduser('~/.claude.json')
API = 'https://api.notion.com/v1'


def load_token():
    if os.environ.get('NOTION_TOKEN'):
        return os.environ['NOTION_TOKEN']
    try:
        c = json.load(open(CLAUDE_JSON))
    except FileNotFoundError:
        sys.exit('NOTION_TOKEN 환경변수 또는 ~/.claude.json 의 notion MCP 등록이 필요합니다')
    entry = c.get('mcpServers', {}).get('notion')
    if not entry:
        for proj in c.get('projects', {}).values():
            if 'notion' in proj.get('mcpServers', {}):
                entry = proj['mcpServers']['notion']
                break
    if not entry:
        sys.exit('NOTION_TOKEN 환경변수 또는 notion MCP 등록을 찾지 못했습니다')
    return entry['env']['NOTION_TOKEN']



TOKEN = load_token()
HEADERS = {
    'Authorization': f'Bearer {TOKEN}',
    'Notion-Version': '2022-06-28',
    'Content-Type': 'application/json',
}


def api(method, path, body=None):
    req = urllib.request.Request(
        f'{API}{path}',
        data=json.dumps(body).encode() if body else None,
        headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f'Notion API {method} {path} 실패: {e.read().decode()[:500]}')


def rt(text):
    return [{'type': 'text', 'text': {'content': text[:1990]}}]


def para(text):
    return {'type': 'paragraph', 'paragraph': {'rich_text': rt(text)}}


def bullet(text):
    return {'type': 'bulleted_list_item',
            'bulleted_list_item': {'rich_text': rt(text)}}


def heading(text):
    return {'type': 'heading_2', 'heading_2': {'rich_text': rt(text)}}


def chunks(text, n=1900):
    return [text[i:i + n] for i in range(0, len(text), n)] or ['']


def main():
    spec = json.load(open(sys.argv[1]))
    cfg = json.load(open(CONFIG_PATH))
    db = {k: v['id'] for k, v in cfg['databases'].items()}

    # 1) 일정 DB에 액션아이템 행 생성
    action_ids = []
    for item in spec.get('action_items', []):
        props = {
            '일정명': {'title': rt(item['name'])},
            '유형': {'select': {'name': '회의 액션아이템'}},
            '상태': {'select': {'name': '예정'}},
        }
        if item.get('owner'):
            props['담당'] = {'select': {'name': item['owner']}}
        if item.get('due'):
            props['날짜'] = {'date': {'start': item['due']}}
        page = api('POST', '/pages',
                   {'parent': {'database_id': db['schedule']}, 'properties': props})
        action_ids.append(page['id'])

    # 2) 회의록 페이지 본문 블록
    blocks = [heading('요약')]
    blocks += [para(p) for p in spec.get('summary', [])]
    if spec.get('decisions'):
        blocks.append(heading('결정사항'))
        blocks += [bullet(d) for d in spec['decisions']]
    if spec.get('action_items'):
        blocks.append(heading('액션아이템'))
        for item in spec['action_items']:
            tail = ' / '.join(x for x in [item.get('owner'), item.get('due')] if x)
            blocks.append(bullet(item['name'] + (f' — {tail}' if tail else '')))

    transcript = ''
    if spec.get('transcript_path'):
        transcript = open(spec['transcript_path']).read().strip()

    props = {
        '제목': {'title': rt(spec['title'])},
        '날짜': {'date': {'start': spec['date']}},
    }
    if spec.get('attendees'):
        props['참석자'] = {'rich_text': rt(spec['attendees'])}
    if action_ids:
        props['액션아이템'] = {'relation': [{'id': i} for i in action_ids]}

    meeting = api('POST', '/pages', {
        'parent': {'database_id': db['meetings']},
        'properties': props,
        'children': blocks[:100],
    })

    # 3) 원문 전사 토글 (100블록 배치 제한 대응: 토글 생성 후 children append)
    if transcript:
        toggle = api('PATCH', f'/blocks/{meeting["id"]}/children', {
            'children': [{'type': 'toggle',
                          'toggle': {'rich_text': rt('원문 전사')}}]})
        toggle_id = toggle['results'][0]['id']
        paras = [para(c) for c in chunks(transcript)]
        for i in range(0, len(paras), 90):
            api('PATCH', f'/blocks/{toggle_id}/children',
                {'children': paras[i:i + 90]})

    print(json.dumps({
        'meeting_url': meeting['url'],
        'meeting_id': meeting['id'],
        'action_item_ids': action_ids,
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
