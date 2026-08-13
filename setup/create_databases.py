#!/usr/bin/env python3
"""select-shop-kit — Notion 워크스페이스 셋업 (DB 13종 + 뷰 + 운영 대시보드).

사용:
  export NOTION_TOKEN=ntn_xxx        # 또는 ~/.claude.json 의 notion MCP 등록 자동 사용
  python3 setup/create_databases.py <parent_page_id> [--staff "이름1,이름2,이름3"]

  parent_page_id : Integration 이 연결된 Notion 페이지 ID (이 페이지 하위에 전부 생성)
  --staff        : 담당 직원 select 옵션 (기본 "대표,스태프1,스태프2")

생성 후 DB ID 맵을 ~/.claude/.comad/select-shop.json 에 저장한다 (SHOP_CONFIG 로 변경 가능).
스킬 4종(meeting/sales/shop-report/closeout)은 이 설정 파일을 읽는다.
"""
import json
import os
import sys
import time
import urllib.request

CONFIG_PATH = os.path.expanduser(os.environ.get('SHOP_CONFIG', '~/.claude/.comad/select-shop.json'))
API = 'https://api.notion.com/v1'


def load_token():
    if os.environ.get('NOTION_TOKEN'):
        return os.environ['NOTION_TOKEN']
    try:
        c = json.load(open(os.path.expanduser('~/.claude.json')))
    except FileNotFoundError:
        sys.exit('NOTION_TOKEN 환경변수를 설정해주세요')
    entry = c.get('mcpServers', {}).get('notion')
    if not entry:
        for proj in c.get('projects', {}).values():
            if 'notion' in proj.get('mcpServers', {}):
                entry = proj['mcpServers']['notion']
                break
    if not entry:
        sys.exit('NOTION_TOKEN 환경변수를 설정해주세요')
    return entry['env']['NOTION_TOKEN']


TOKEN = load_token()


def api(method, path, body=None, version='2022-06-28'):
    req = urllib.request.Request(
        f'{API}{path}',
        data=json.dumps(body).encode() if body else None,
        headers={'Authorization': f'Bearer {TOKEN}', 'Notion-Version': version,
                 'Content-Type': 'application/json'},
        method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        print(f'ERROR {method} {path}: {e.read().decode()[:400]}', file=sys.stderr)
        raise


rt = lambda t: [{'type': 'text', 'text': {'content': t}}]
sel = lambda opts: {'select': {'options': [{'name': n, 'color': c} for n, c in opts]}}
won = {'number': {'format': 'won'}}
num = {'number': {'format': 'number'}}
txt = {'rich_text': {}}


def rel(db_id, dual=True):
    if dual:
        return {'relation': {'database_id': db_id, 'type': 'dual_property', 'dual_property': {}}}
    return {'relation': {'database_id': db_id, 'type': 'single_property', 'single_property': {}}}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        sys.exit(__doc__)
    parent = args[0]
    staff_arg = '대표,스태프1,스태프2'
    for a in sys.argv[1:]:
        if a.startswith('--staff'):
            staff_arg = a.split('=', 1)[1] if '=' in a else sys.argv[sys.argv.index(a) + 1]
    staff_names = [s.strip() for s in staff_arg.split(',')][:3]
    STAFF = list(zip(staff_names, ['blue', 'purple', 'green']))
    print('담당 옵션:', staff_names)

    ids = {}

    def create(key, title, desc, props):
        d = api('POST', '/databases', {
            'parent': {'type': 'page_id', 'page_id': parent},
            'title': rt(title), 'description': rt(desc), 'properties': props})
        ids[key] = d['id']
        print(f'DB {title}: {d["id"]}')
        time.sleep(0.3)
        return d['id']

    def patch(key, props):
        api('PATCH', f'/databases/{ids[key]}', {'properties': props})
        time.sleep(0.2)

    def backrel(key, target_key, new_name, keep=()):
        db = api('GET', f'/databases/{ids[key]}')
        for name, p in db['properties'].items():
            if (p['type'] == 'relation'
                    and p['relation']['database_id'].replace('-', '') == ids[target_key].replace('-', '')
                    and name != new_name and name not in keep):
                patch(key, {name: {'name': new_name}})
                return

    # ── 1~2. 거래처 · 일정 ─────────────────────────────
    create('vendors', '거래처·사입처', '미지급금은 재고이동(지급상태=미지급) 합계 자동 계산 — 직접 수정 금지. 지급 완료 시 해당 재고이동 행의 지급상태를 변경.', {
        '업체명': {'title': {}}, '담당자': txt, '연락처': {'phone_number': {}},
        '결제조건': sel([('선결제', 'green'), ('월말정산', 'orange'), ('기타', 'gray')]),
        '메모': txt})
    create('schedule', '일정', '일정 SoT는 이 DB (Google Calendar는 미러). 회의 액션아이템은 /meeting 이 자동 등록.', {
        '일정명': {'title': {}}, '날짜': {'date': {}},
        '유형': sel([('사입/발주', 'blue'), ('발매드롭', 'red'), ('팝업', 'purple'), ('세일', 'pink'),
                     ('마케팅', 'orange'), ('세무신고', 'brown'), ('회의 액션아이템', 'yellow'), ('기타', 'gray')]),
        '상태': sel([('예정', 'yellow'), ('진행중', 'blue'), ('완료', 'green')]),
        '담당': sel(STAFF), '메모': txt})

    # ── 3~4. 브랜드 · 스타일 ───────────────────────────
    create('brands', '브랜드', '입점 브랜드 마스터. 홀세일율·드롭 주기·입점 상태 관리.', {
        '브랜드명': {'title': {}}, '구분': sel([('국내', 'blue'), ('해외', 'purple')]),
        '홀세일율(%)': {'number': {'format': 'percent'}},
        '주력': sel([('의류', 'green'), ('신발', 'orange'), ('액세서리', 'pink'), ('종합', 'gray')]),
        '공급 거래처': rel(ids['vendors']),
        '입점 상태': sel([('거래중', 'green'), ('협의중', 'yellow'), ('중단', 'red')]),
        '담당': sel(STAFF), '링크': {'url': {}}, '메모': txt})
    backrel('vendors', 'brands', '취급 브랜드')
    create('styles', '스타일(드롭)', '스타일 마스터 — 드롭 단위 상품 기준정보. SKU(사이즈 옵션)는 상품·재고 DB에서 이 스타일에 연결.', {
        '스타일명': {'title': {}}, '스타일코드': txt, '브랜드': rel(ids['brands']),
        '드롭': sel([('SS 메인', 'green'), ('여름 캡슐', 'yellow'), ('FW 프리', 'orange'), ('FW 메인', 'blue'), ('콜라보', 'pink')]),
        '카테고리': sel([('티셔츠', 'green'), ('후디/스웻', 'blue'), ('팬츠', 'yellow'), ('아우터', 'orange'),
                        ('신발', 'red'), ('캡/비니', 'purple'), ('액세서리', 'pink')]),
        '정가': won, '홀세일가': won, '발매일': {'date': {}},
        '상태': sel([('발매예정', 'yellow'), ('판매중', 'green'), ('시즌오프', 'gray'), ('단종', 'red')]),
        '룩북': {'files': {}}})
    backrel('brands', 'styles', '스타일')

    # ── 5. 상품(SKU) ───────────────────────────────────
    create('products', '상품·재고', 'SKU(사이즈 옵션) 단위 재고 원장. 입고수량=재고이동 합계(직접 수정 금지 — 재고이동 DB에 행 추가). 재고상태 자동 계산(재발주점 이하 = 품절임박, 비우면 1) / 판매상태만 직접 변경. 과거 마진은 매출 행의 원가 스냅샷 기준.', {
        '상품명': {'title': {}}, 'SKU': txt, '바코드': txt, '재발주점': num,
        '브랜드': {'select': {'options': []}},
        '카테고리': sel([('아우터', 'blue'), ('상의', 'green'), ('하의', 'yellow'), ('신발', 'orange'), ('가방', 'purple'), ('액세서리', 'pink')]),
        '시즌': {'select': {'options': []}},
        '사이즈': sel([('XS', 'gray'), ('S', 'brown'), ('M', 'blue'), ('L', 'green'), ('XL', 'orange'), ('FREE', 'purple')]),
        '컬러': {'select': {'options': []}},
        '사입가': won, '판매가': won,
        '스타일': rel(ids['styles']), '거래처': rel(ids['vendors'], dual=False),
        '판매상태': sel([('판매예정', 'yellow'), ('판매중', 'green'), ('판매중지', 'red'), ('시즌오프', 'gray')]),
        '사진': {'files': {}}})
    backrel('styles', 'products', 'SKU')

    # ── 6~8. 발주 · 고객 · 주문 ────────────────────────
    create('po', '발주(PO)', '발주 원장. 상태: 발주확정→부분입고→입고완료→정산완료. 입고액은 연결된 재고이동(사입입고) 합계 자동 대사. 미입고 잔액 = 발주총액 − 입고액.', {
        '발주번호': {'title': {}}, '브랜드': rel(ids['brands'], dual=False),
        '거래처': rel(ids['vendors'], dual=False),
        '발주일': {'date': {}}, '입고예정': {'date': {}},
        '상태': sel([('발주확정', 'yellow'), ('부분입고', 'orange'), ('입고완료', 'green'), ('정산완료', 'blue'), ('취소', 'red')]),
        '발주총액': won, '담당': sel(STAFF), '메모': txt})
    create('customers', '고객', 'VIP·단골 관리. 구매횟수·누적구매액은 자사몰 주문 rollup 자동.', {
        '고객명': {'title': {}}, '연락처': {'phone_number': {}},
        '등급': sel([('VIP', 'red'), ('단골', 'orange'), ('일반', 'gray')]),
        '선호 사이즈': sel([('S', 'gray'), ('M', 'blue'), ('L', 'green'), ('XL', 'orange')]),
        '선호 브랜드': rel(ids['brands'], dual=False), '메모': txt})
    create('orders', '주문(자사몰)', '자사몰 주문 헤더. 상품 라인은 매출 대장 행이 이 주문에 연결(1주문 N라인). 결제총액 = 상품합계 − 할인 + 배송비. 라인합계와 상품합계가 다르면 라인 누락 신호.', {
        '주문번호': {'title': {}}, '주문일': {'date': {}}, '고객': rel(ids['customers']),
        '주문상태': sel([('결제완료', 'yellow'), ('배송준비', 'orange'), ('배송중', 'blue'), ('배송완료', 'green'), ('취소', 'red'), ('반품', 'red')]),
        '상품합계': won, '할인': won, '배송비': won, '메모': txt})
    backrel('customers', 'orders', '주문 이력')

    # ── 9~10. 재고이동 · 매출 ──────────────────────────
    create('moves', '재고이동', '비판매 재고 이동 원장 (append-only). 입고=+, 출고=−. 고객 판매/반품은 매출 대장이 담당. 유형: 사입입고(+)/거래처반품(−)/폐기(−)/증정(−)/실사조정(±). 기존 행 수정 금지 — 정정도 새 행으로.', {
        '이동 건': {'title': {}}, '날짜': {'date': {}}, '상품': rel(ids['products']),
        '유형': sel([('사입입고', 'green'), ('거래처반품', 'orange'), ('폐기', 'red'), ('증정', 'purple'), ('실사조정', 'gray')]),
        '수량': num, '단가': won,
        '거래처': rel(ids['vendors']), '발주': rel(ids['po']),
        '지급상태': sel([('미지급', 'red'), ('지급완료', 'green'), ('해당없음', 'gray')]),
        '담당': sel(STAFF), '메모': txt})
    backrel('products', 'moves', '재고이동 내역', keep=('상품',))
    backrel('vendors', 'moves', '사입 이력', keep=('거래처',))
    backrel('po', 'moves', '입고 내역', keep=('발주',))
    create('sales', '매출 대장', '판매 라인 원장 (append-only). 오프라인=단독 행, 자사몰=주문 헤더에 연결된 라인. 반품·환불=음수 행, 교환=반품+판매 2행. 원가=판매 시점 스냅샷. 입력키=멱등키. 담당=판매 처리 직원.', {
        '판매 건': {'title': {}}, '판매일': {'date': {}},
        '채널': sel([('오프라인', 'blue'), ('자사몰', 'purple')]),
        '상품': rel(ids['products']), '주문': rel(ids['orders']),
        '수량': num, '실판매가': won, '원가': won,
        '결제수단': sel([('카드', 'blue'), ('현금', 'green'), ('계좌이체', 'yellow'), ('PG', 'purple')]),
        '주문번호': txt, '입력키': txt,
        '상태': sel([('완료', 'green'), ('교환', 'yellow'), ('반품', 'orange'), ('환불', 'red')]),
        '담당': sel(STAFF), '메모': txt})
    backrel('products', 'sales', '매출 내역', keep=('상품',))
    backrel('orders', 'sales', '라인', keep=('주문',))

    # ── 11~12. 정산 · 회의록 ───────────────────────────
    create('settlements', '정산', 'PG·카드 정산 대사. 차이금액 = 실입금 − (매출총액 − 수수료). 0이 아니면 대사상태를 "차이"로 두고 원인 추적.', {
        '정산 건': {'title': {}}, '주체': sel([('PG', 'purple'), ('카드사', 'blue'), ('기타', 'gray')]),
        '대상기간': {'date': {}}, '매출총액': won, '수수료': won, '실입금액': won,
        '입금일': {'date': {}},
        '대사상태': sel([('대기', 'yellow'), ('일치', 'green'), ('차이', 'red')]),
        '메모': txt})
    create('meetings', '회의록', '회의록 원장. /meeting 스킬이 녹음 전사→요약→액션아이템(→일정 DB)을 자동 생성. 원문 전사는 페이지 하단 토글.', {
        '제목': {'title': {}}, '날짜': {'date': {}}, '참석자': txt,
        '액션아이템': rel(ids['schedule'])})
    backrel('schedule', 'meetings', '출처 회의록')

    # ── rollup · formula ───────────────────────────────
    patch('moves', {'금액': {'formula': {'expression': 'prop("수량") * prop("단가")'}}})
    patch('moves', {'미지급액': {'formula': {'expression': 'prop("지급상태") == "미지급" ? prop("금액") : 0'}}})
    patch('products', {'입고수량': {'rollup': {'relation_property_name': '재고이동 내역', 'rollup_property_name': '수량', 'function': 'sum'}}})
    patch('products', {'판매수량': {'rollup': {'relation_property_name': '매출 내역', 'rollup_property_name': '수량', 'function': 'sum'}}})
    patch('products', {'현재고': {'formula': {'expression': 'prop("입고수량") - prop("판매수량")'}}})
    patch('products', {'재고상태': {'formula': {'expression': 'prop("현재고") <= 0 ? "품절" : (prop("현재고") <= if(empty(prop("재발주점")), 1, prop("재발주점")) ? "품절임박" : "재고있음")'}}})
    patch('vendors', {'미지급금': {'rollup': {'relation_property_name': '사입 이력', 'rollup_property_name': '미지급액', 'function': 'sum'}}})
    patch('po', {'입고액': {'rollup': {'relation_property_name': '입고 내역', 'rollup_property_name': '금액', 'function': 'sum'}}})
    patch('po', {'미입고 잔액': {'formula': {'expression': 'prop("발주총액") - prop("입고액")'}}})
    patch('sales', {'라인금액': {'formula': {'expression': 'prop("실판매가") * prop("수량")'}}})
    patch('sales', {'사입가(참조)': {'rollup': {'relation_property_name': '상품', 'rollup_property_name': '사입가', 'function': 'sum'}}})
    patch('sales', {'마진': {'formula': {'expression': '(prop("실판매가") - prop("원가")) * prop("수량")'}}})
    patch('orders', {'결제총액': {'formula': {'expression': 'prop("상품합계") - prop("할인") + prop("배송비")'}}})
    patch('orders', {'라인합계': {'rollup': {'relation_property_name': '라인', 'rollup_property_name': '라인금액', 'function': 'sum'}}})
    patch('customers', {'구매횟수': {'rollup': {'relation_property_name': '주문 이력', 'rollup_property_name': '주문번호', 'function': 'count'}}})
    patch('customers', {'누적구매액': {'rollup': {'relation_property_name': '주문 이력', 'rollup_property_name': '결제총액', 'function': 'sum'}}})
    patch('settlements', {'차이금액': {'formula': {'expression': 'prop("실입금액") - (prop("매출총액") - prop("수수료"))'}}})
    print('rollup·formula 완료')

    # ── 뷰 + 대시보드 (Views API 2025-09-03) ───────────
    V = '2025-09-03'
    ds, props = {}, {}
    for key, dbid in ids.items():
        d = api('GET', f'/databases/{dbid}', version=V)
        ds[key] = d['data_sources'][0]['id']
        src = api('GET', f'/data_sources/{ds[key]}', version=V)
        props[key] = {name: p['id'] for name, p in src['properties'].items()}

    def view(body):
        v = api('POST', '/views', body, version=V)
        print('view:', v.get('name'))
        time.sleep(0.2)
        return v

    LOW_STOCK = {'and': [
        {'property': '재고상태', 'formula': {'string': {'does_not_equal': '재고있음'}}},
        {'property': '판매상태', 'select': {'equals': '판매중'}}]}
    view({'database_id': ids['sales'], 'data_source_id': ds['sales'], 'name': '최근 7일', 'type': 'table',
          'filter': {'property': '판매일', 'date': {'past_week': {}}},
          'sorts': [{'property': '판매일', 'direction': 'descending'}]})
    view({'database_id': ids['products'], 'data_source_id': ds['products'], 'name': '갤러리', 'type': 'gallery',
          'configuration': {'type': 'gallery', 'cover': {'type': 'property', 'property_id': props['products']['사진']},
                            'cover_size': 'medium', 'cover_aspect': 'cover'}})
    view({'database_id': ids['products'], 'data_source_id': ds['products'], 'name': '품절 임박', 'type': 'table', 'filter': LOW_STOCK})
    view({'database_id': ids['schedule'], 'data_source_id': ds['schedule'], 'name': '캘린더', 'type': 'calendar',
          'configuration': {'type': 'calendar', 'date_property_id': props['schedule']['날짜'], 'view_range': 'month'}})
    view({'database_id': ids['styles'], 'data_source_id': ds['styles'], 'name': '발매 캘린더', 'type': 'calendar',
          'configuration': {'type': 'calendar', 'date_property_id': props['styles']['발매일'], 'view_range': 'month'}})
    view({'database_id': ids['styles'], 'data_source_id': ds['styles'], 'name': '드롭 갤러리', 'type': 'gallery',
          'configuration': {'type': 'gallery', 'cover': {'type': 'property', 'property_id': props['styles']['룩북'],},
                            'cover_size': 'medium', 'cover_aspect': 'cover'}})
    view({'database_id': ids['po'], 'data_source_id': ds['po'], 'name': '진행중', 'type': 'table',
          'filter': {'property': '상태', 'select': {'equals': ['발주확정', '부분입고']}}})
    view({'database_id': ids['orders'], 'data_source_id': ds['orders'], 'name': '처리 보드', 'type': 'board',
          'configuration': {'type': 'board', 'group_by': {'type': 'select', 'property_id': props['orders']['주문상태'], 'sort': {'type': 'manual'}}}})
    view({'database_id': ids['customers'], 'data_source_id': ds['customers'], 'name': '등급 보드', 'type': 'board',
          'configuration': {'type': 'board', 'group_by': {'type': 'select', 'property_id': props['customers']['등급'], 'sort': {'type': 'manual'}}}})
    view({'database_id': ids['settlements'], 'data_source_id': ds['settlements'], 'name': '대사 차이', 'type': 'table',
          'filter': {'property': '대사상태', 'select': {'equals': '차이'}}})
    view({'database_id': ids['moves'], 'data_source_id': ds['moves'], 'name': '미지급', 'type': 'table',
          'filter': {'property': '지급상태', 'select': {'equals': '미지급'}}})
    view({'database_id': ids['meetings'], 'data_source_id': ds['meetings'], 'name': '최신순', 'type': 'list',
          'sorts': [{'property': '날짜', 'direction': 'descending'}]})

    dash = view({'create_database': {'parent': {'type': 'page_id', 'page_id': parent}},
                 'data_source_id': ds['sales'], 'name': '운영 대시보드', 'type': 'dashboard'})
    DASH = dash['id']
    view({'view_id': DASH, 'data_source_id': ds['sales'], 'name': '최근 매출 (7일)', 'type': 'table',
          'filter': {'property': '판매일', 'date': {'past_week': {}}},
          'sorts': [{'property': '판매일', 'direction': 'descending'}], 'placement': {'type': 'new_row'}})
    view({'view_id': DASH, 'data_source_id': ds['schedule'], 'name': '이번 주 일정', 'type': 'table',
          'filter': {'property': '날짜', 'date': {'this_week': {}}},
          'sorts': [{'property': '날짜', 'direction': 'ascending'}], 'placement': {'type': 'existing_row', 'row_index': 0}})
    view({'view_id': DASH, 'data_source_id': ds['products'], 'name': '품절 임박', 'type': 'table',
          'filter': LOW_STOCK, 'placement': {'type': 'new_row'}})
    view({'view_id': DASH, 'data_source_id': ds['meetings'], 'name': '최근 회의록', 'type': 'list',
          'sorts': [{'property': '날짜', 'direction': 'descending'}], 'placement': {'type': 'existing_row', 'row_index': 1}})
    view({'view_id': DASH, 'data_source_id': ds['orders'], 'name': '처리 대기 주문', 'type': 'table',
          'filter': {'property': '주문상태', 'select': {'equals': ['결제완료', '배송준비']}}, 'placement': {'type': 'new_row'}})
    view({'view_id': DASH, 'data_source_id': ds['po'], 'name': '진행중 발주', 'type': 'table',
          'filter': {'property': '상태', 'select': {'equals': ['발주확정', '부분입고']}}, 'placement': {'type': 'existing_row', 'row_index': 2}})

    # ── config 저장 ────────────────────────────────────
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    cfg = {
        'parent_page_id': parent,
        'databases': {k: {'id': v} for k, v in ids.items()},
        'data_sources': ds,
        'dashboard_view_id': DASH,
        'staff': staff_names,
        'channels': ['오프라인', '자사몰'],
    }
    json.dump(cfg, open(CONFIG_PATH, 'w'), ensure_ascii=False, indent=2)
    print(f'\n완료. 설정 저장: {CONFIG_PATH}')
    print('대시보드 블록이 페이지 맨 아래 생성됩니다 — Notion에서 맨 위로 드래그하세요.')


if __name__ == '__main__':
    main()
