#!/usr/bin/env python3
"""편집샵 매출·재고 Notion 헬퍼 (v2 — 재고이동 원장 체계).

사용:
  notion_sales.py find-product <검색어>
  notion_sales.py add-product <입력.json>
  notion_sales.py add-move <입력.json>
  notion_sales.py restock <product_page_id> <추가수량> [단가]
  notion_sales.py add-sale <입력.json>
  notion_sales.py existing-orders [일수=90]
  notion_sales.py sales-range <YYYY-MM-DD> <YYYY-MM-DD>
  notion_sales.py schedule-upcoming [일수=7]

add-product JSON: {"name","sku","brand","category","season","size","color",
                   "cost","price","qty","vendor_id","status"="판매중"}
                  qty가 있으면 생성 후 사입입고 재고이동을 자동 기록.
add-move JSON:    {"date","product_id","type"(사입입고/거래처반품/폐기/증정/실사조정),
                   "qty"(입고+/출고−),"unit_cost","vendor_id","payment_status","memo"}
                  사입입고+거래처는 payment_status 기본 미지급.
add-sale JSON:    {"title","date","channel","product_id","qty","price",
                   "payment","order_no","status"="완료","memo","input_key",
                   "staff"(담당),"order_id"(주문 헤더 relation)}
                  원가는 상품의 현재 사입가를 자동 스냅샷 (cost로 덮어쓰기 가능).
                  input_key가 있으면 동일 키 존재 시 기록하지 않고 skipped 반환 (멱등).
add-order JSON:   {"order_no","date","customer_id","status"="결제완료",
                   "subtotal","discount"=0,"shipping"=0,"memo"}
                  자사몰 주문 헤더 생성 → 반환된 id를 각 라인 add-sale의 order_id로.
모든 출력은 stdout JSON.
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



HEADERS = {
    'Authorization': f'Bearer {load_token()}',
    'Notion-Version': '2022-06-28',
    'Content-Type': 'application/json',
}
DB = {k: v['id'] for k, v in json.load(open(CONFIG_PATH))['databases'].items()}


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


def query_all(db_id, body=None):
    body = dict(body or {})
    results = []
    while True:
        d = api('POST', f'/databases/{db_id}/query', body)
        results += d['results']
        if not d.get('has_more'):
            return results
        body['start_cursor'] = d['next_cursor']


def rt(text):
    return [{'type': 'text', 'text': {'content': str(text)[:1990]}}]


def plain(prop):
    t = prop['type']
    if t == 'title':
        return ''.join(x['plain_text'] for x in prop['title'])
    if t == 'rich_text':
        return ''.join(x['plain_text'] for x in prop['rich_text'])
    if t == 'number':
        return prop['number']
    if t == 'select':
        return prop['select']['name'] if prop['select'] else None
    if t == 'date':
        return prop['date']['start'] if prop['date'] else None
    if t == 'formula':
        f = prop['formula']
        return f.get('number') if f['type'] == 'number' else f.get('string')
    if t == 'rollup':
        return prop['rollup'].get('number')
    if t == 'relation':
        return [r['id'] for r in prop['relation']]
    return None


def product_row(p):
    pr = p['properties']
    return {
        'id': p['id'],
        'name': plain(pr['상품명']),
        'sku': plain(pr['SKU']),
        'brand': plain(pr['브랜드']),
        'size': plain(pr['사이즈']),
        'color': plain(pr['컬러']),
        'cost': plain(pr['사입가']),
        'price': plain(pr['판매가']),
        'stock': plain(pr['현재고']),
        'stock_status': plain(pr['재고상태']),
        'sale_status': plain(pr['판매상태']),
    }


def cmd_find_product(query):
    rows = query_all(DB['products'], {'filter': {'or': [
        {'property': '상품명', 'title': {'contains': query}},
        {'property': 'SKU', 'rich_text': {'contains': query}},
    ]}})
    print(json.dumps([product_row(p) for p in rows], ensure_ascii=False))


def _create_move(s):
    """재고이동 행 생성. s: date, product_id, type, qty, unit_cost, vendor_id, payment_status, memo, title"""
    mmdd = s['date'][5:7] + s['date'][8:10]
    title = s.get('title') or f"{mmdd} {s['type']} {'+' if s['qty'] >= 0 else ''}{s['qty']}"
    pay = s.get('payment_status') or (
        '미지급' if s['type'] == '사입입고' and s.get('vendor_id') else '해당없음')
    props = {
        '이동 건': {'title': rt(title)},
        '날짜': {'date': {'start': s['date']}},
        '상품': {'relation': [{'id': s['product_id']}]},
        '유형': {'select': {'name': s['type']}},
        '수량': {'number': s['qty']},
        '단가': {'number': s.get('unit_cost', 0) or 0},
        '지급상태': {'select': {'name': pay}},
    }
    if s.get('vendor_id'):
        props['거래처'] = {'relation': [{'id': s['vendor_id']}]}
    if s.get('memo'):
        props['메모'] = {'rich_text': rt(s['memo'])}
    if s.get('staff'):
        props['담당'] = {'select': {'name': s['staff']}}
    if s.get('po_id'):
        props['발주'] = {'relation': [{'id': s['po_id']}]}
    return api('POST', '/pages',
               {'parent': {'database_id': DB['moves']}, 'properties': props})


def cmd_add_move(path):
    page = _create_move(json.load(open(path)))
    print(json.dumps({'id': page['id'], 'url': page['url']}, ensure_ascii=False))


def cmd_add_product(path):
    s = json.load(open(path))
    props = {'상품명': {'title': rt(s['name'])}}
    if s.get('sku'):
        props['SKU'] = {'rich_text': rt(s['sku'])}
    for k, name in {'brand': '브랜드', 'category': '카테고리', 'season': '시즌',
                    'size': '사이즈', 'color': '컬러', 'status': '판매상태'}.items():
        if s.get(k):
            props[name] = {'select': {'name': s[k]}}
    for k, name in {'cost': '사입가', 'price': '판매가'}.items():
        if s.get(k) is not None:
            props[name] = {'number': s[k]}
    if s.get('vendor_id'):
        props['거래처'] = {'relation': [{'id': s['vendor_id']}]}
    props.setdefault('판매상태', {'select': {'name': '판매중'}})
    page = api('POST', '/pages',
               {'parent': {'database_id': DB['products']}, 'properties': props})
    out = {'id': page['id'], 'url': page['url']}
    if s.get('qty'):
        import datetime
        move = _create_move({
            'date': s.get('date') or datetime.date.today().isoformat(),
            'product_id': page['id'], 'type': '사입입고', 'qty': s['qty'],
            'unit_cost': s.get('cost', 0), 'vendor_id': s.get('vendor_id')})
        out['move_id'] = move['id']
    print(json.dumps(out, ensure_ascii=False))


def cmd_restock(page_id, qty, unit_cost=None):
    import datetime
    if unit_cost is None:
        prod = api('GET', f'/pages/{page_id}')
        unit_cost = plain(prod['properties']['사입가']) or 0
        vendor = plain(prod['properties']['거래처'])
    else:
        unit_cost = int(unit_cost)
        vendor = None
    move = _create_move({
        'date': datetime.date.today().isoformat(), 'product_id': page_id,
        'type': '사입입고', 'qty': int(qty), 'unit_cost': unit_cost,
        'vendor_id': vendor[0] if vendor else None})
    print(json.dumps({'move_id': move['id'], 'qty': int(qty), 'unit_cost': unit_cost},
                     ensure_ascii=False))


def cmd_add_sale(path):
    s = json.load(open(path))
    # 멱등키 중복 확인
    if s.get('input_key'):
        dup = query_all(DB['sales'], {'filter': {
            'property': '입력키', 'rich_text': {'equals': s['input_key']}}})
        if dup:
            print(json.dumps({'skipped': True, 'reason': 'duplicate input_key',
                              'existing_id': dup[0]['id']}, ensure_ascii=False))
            return
    # 원가 스냅샷: 명시 cost 없으면 상품의 현재 사입가
    cost = s.get('cost')
    if cost is None:
        prod = api('GET', f'/pages/{s["product_id"]}')
        cost = plain(prod['properties']['사입가']) or 0
    props = {
        '판매 건': {'title': rt(s['title'])},
        '판매일': {'date': {'start': s['date']}},
        '채널': {'select': {'name': s['channel']}},
        '상품': {'relation': [{'id': s['product_id']}]},
        '수량': {'number': s['qty']},
        '실판매가': {'number': s['price']},
        '원가': {'number': cost},
        '상태': {'select': {'name': s.get('status', '완료')}},
    }
    if s.get('payment'):
        props['결제수단'] = {'select': {'name': s['payment']}}
    if s.get('order_no'):
        props['주문번호'] = {'rich_text': rt(s['order_no'])}
    if s.get('input_key'):
        props['입력키'] = {'rich_text': rt(s['input_key'])}
    if s.get('memo'):
        props['메모'] = {'rich_text': rt(s['memo'])}
    if s.get('staff'):
        props['담당'] = {'select': {'name': s['staff']}}
    if s.get('order_id'):
        props['주문'] = {'relation': [{'id': s['order_id']}]}
    page = api('POST', '/pages',
               {'parent': {'database_id': DB['sales']}, 'properties': props})
    print(json.dumps({'id': page['id'], 'url': page['url'], 'cost_snapshot': cost},
                     ensure_ascii=False))


def cmd_add_order(path):
    s = json.load(open(path))
    props = {
        '주문번호': {'title': rt(s['order_no'])},
        '주문일': {'date': {'start': s['date']}},
        '주문상태': {'select': {'name': s.get('status', '결제완료')}},
        '상품합계': {'number': s['subtotal']},
        '할인': {'number': s.get('discount', 0)},
        '배송비': {'number': s.get('shipping', 0)},
    }
    if s.get('customer_id'):
        props['고객'] = {'relation': [{'id': s['customer_id']}]}
    if s.get('memo'):
        props['메모'] = {'rich_text': rt(s['memo'])}
    page = api('POST', '/pages',
               {'parent': {'database_id': DB['orders']}, 'properties': props})
    print(json.dumps({'id': page['id'], 'url': page['url']}, ensure_ascii=False))


def cmd_existing_orders(days='90'):
    import datetime
    since = (datetime.date.today() - datetime.timedelta(days=int(days))).isoformat()
    rows = query_all(DB['sales'], {'filter': {'and': [
        {'property': '판매일', 'date': {'on_or_after': since}},
        {'property': '주문번호', 'rich_text': {'is_not_empty': True}},
    ]}})
    print(json.dumps(sorted({plain(p['properties']['주문번호']) for p in rows}),
                     ensure_ascii=False))


def cmd_sales_range(d_from, d_to):
    rows = query_all(DB['sales'], {'filter': {'and': [
        {'property': '판매일', 'date': {'on_or_after': d_from}},
        {'property': '판매일', 'date': {'on_or_before': d_to}},
    ]}, 'sorts': [{'property': '판매일', 'direction': 'ascending'}]})
    prod_cache = {}
    out = []
    for p in rows:
        pr = p['properties']
        rel = plain(pr['상품'])
        pname = None
        if rel:
            pid = rel[0]
            if pid not in prod_cache:
                pp = api('GET', f'/pages/{pid}')
                prod_cache[pid] = plain(pp['properties']['상품명'])
            pname = prod_cache[pid]
        out.append({
            'date': plain(pr['판매일']),
            'channel': plain(pr['채널']),
            'product': pname,
            'qty': plain(pr['수량']),
            'price': plain(pr['실판매가']),
            'cost': plain(pr['원가']),
            'margin': plain(pr['마진']),
            'payment': plain(pr['결제수단']),
            'status': plain(pr['상태']),
        })
    print(json.dumps(out, ensure_ascii=False))


def cmd_schedule_upcoming(days='7'):
    import datetime
    today = datetime.date.today().isoformat()
    until = (datetime.date.today() + datetime.timedelta(days=int(days))).isoformat()
    rows = query_all(DB['schedule'], {'filter': {'and': [
        {'property': '날짜', 'date': {'on_or_after': today}},
        {'property': '날짜', 'date': {'on_or_before': until}},
    ]}, 'sorts': [{'property': '날짜', 'direction': 'ascending'}]})
    out = [{'date': plain(p['properties']['날짜']),
            'name': plain(p['properties']['일정명']),
            'type': plain(p['properties']['유형']),
            'status': plain(p['properties']['상태'])} for p in rows]
    print(json.dumps(out, ensure_ascii=False))


if __name__ == '__main__':
    cmds = {
        'find-product': cmd_find_product,
        'add-product': cmd_add_product,
        'add-move': cmd_add_move,
        'restock': cmd_restock,
        'add-sale': cmd_add_sale,
        'add-order': cmd_add_order,
        'existing-orders': cmd_existing_orders,
        'sales-range': cmd_sales_range,
        'schedule-upcoming': cmd_schedule_upcoming,
    }
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        sys.exit(__doc__)
    cmds[sys.argv[1]](*sys.argv[2:])
