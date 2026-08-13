#!/usr/bin/env python3
"""편집샵 자동 브리핑 — 크론이 Discord 로 push 하는 텍스트를 생성한다.

사용:
  shop_brief.py brief      아침 브리핑 (오늘 일정·발주 진행·재고 경고·미지급·정산 차이·어제 매출)
  shop_brief.py closeout   저녁 마감 (오늘 매출·재고 이상·내일 일정·미기록 확인 질문)

출력은 그대로 채팅/알림 채널에 붙일 수 있는 plain text. 조회 전용 — 어떤 DB 도 쓰지 않는다.
헬퍼는 sales 스킬의 notion_sales.py 를 재사용 (NOTION_TOKEN / SHOP_CONFIG env 동일 지원).

cron 연결 예 (매일 08:47 브리핑을 원하는 채널로):
  47 8 * * *  python3 ~/.claude/skills/shop-report/scripts/shop_brief.py brief | <your-notifier>
"""
import datetime
import os
import sys

# 설치 위치(~/.claude/skills)와 레포 체크아웃 양쪽에서 동작
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'sales', 'scripts'))
sys.path.insert(0, os.path.expanduser('~/.claude/skills/sales/scripts'))
import notion_sales as ns

WEEKDAY = ['월', '화', '수', '목', '금', '토', '일']


def won(n):
    return f'{int(n):,}원' if n else '0원'


def day_label(d):
    return f'{d.isoformat()} ({WEEKDAY[d.weekday()]})'


def schedule_on(d_from, d_to):
    rows = ns.query_all(ns.DB['schedule'], {'filter': {'and': [
        {'property': '날짜', 'date': {'on_or_after': d_from.isoformat()}},
        {'property': '날짜', 'date': {'on_or_before': d_to.isoformat()}},
    ]}, 'sorts': [{'property': '날짜', 'direction': 'ascending'}]})
    out = []
    for p in rows:
        pr = p['properties']
        if ns.plain(pr['상태']) == '완료':
            continue
        staff = ns.plain(pr['담당'])
        out.append({'date': ns.plain(pr['날짜']), 'name': ns.plain(pr['일정명']),
                    'type': ns.plain(pr['유형']), 'staff': staff})
    return out


def open_pos(today):
    rows = ns.query_all(ns.DB['po'], {'filter': {'or': [
        {'property': '상태', 'select': {'equals': '발주확정'}},
        {'property': '상태', 'select': {'equals': '부분입고'}},
    ]}})
    out = []
    for p in rows:
        pr = p['properties']
        eta = ns.plain(pr['입고예정'])
        late = 0
        if eta:
            try:
                late = (today - datetime.date.fromisoformat(eta[:10])).days
            except ValueError:
                late = 0
        out.append({'no': ns.plain(pr['발주번호']), 'status': ns.plain(pr['상태']),
                    'remain': ns.plain(pr['미입고 잔액']) or 0, 'eta': eta, 'late': late})
    return out


def stock_alerts():
    """판매중인데 품절/품절임박인 SKU + 음수 재고(기록 누락 신호)."""
    rows = ns.query_all(ns.DB['products'], {'filter': {'and': [
        {'property': '판매상태', 'select': {'equals': '판매중'}},
        {'property': '재고상태', 'formula': {'string': {'does_not_equal': '재고있음'}}},
    ]}})
    low, negative = [], []
    for p in rows:
        pr = p['properties']
        # 상품명은 SKU(옵션) 단위라 사이즈·컬러가 이미 이름에 들어 있다 — 별도 표기 안 함
        row = {'name': ns.plain(pr['상품명']),
               'stock': ns.plain(pr['현재고']) or 0, 'status': ns.plain(pr['재고상태'])}
        (negative if row['stock'] < 0 else low).append(row)
    return low, negative


def unpaid_vendors():
    rows = ns.query_all(ns.DB['vendors'], {'filter': {
        'property': '미지급금', 'rollup': {'number': {'greater_than': 0}}}})
    return [{'name': ns.plain(p['properties']['업체명']),
             'amount': ns.plain(p['properties']['미지급금'])} for p in rows]


def settlement_diffs():
    rows = ns.query_all(ns.DB['settlements'], {'filter': {
        'property': '대사상태', 'select': {'equals': '차이'}}})
    return [{'name': ns.plain(p['properties']['정산 건']),
             'diff': ns.plain(p['properties']['차이금액']) or 0} for p in rows]


def sales_summary(d):
    """하루 매출 요약 — 라인금액·마진 formula 합산, 채널별 집계."""
    rows = ns.query_all(ns.DB['sales'], {'filter': {'and': [
        {'property': '판매일', 'date': {'on_or_after': d.isoformat()}},
        {'property': '판매일', 'date': {'on_or_before': d.isoformat()}},
    ]}})
    total = margin = 0
    channels = {}
    for p in rows:
        pr = p['properties']
        amt = ns.plain(pr['라인금액']) or 0
        total += amt
        margin += ns.plain(pr['마진']) or 0
        ch = ns.plain(pr['채널']) or '기타'
        c = channels.setdefault(ch, {'count': 0, 'amount': 0})
        c['count'] += 1
        c['amount'] += amt
    return {'count': len(rows), 'total': total, 'margin': margin, 'channels': channels}


def fmt_sales(s):
    if s['count'] == 0:
        return '기록 없음'
    ch = ' · '.join(f"{k} {v['count']}건 {won(v['amount'])}" for k, v in s['channels'].items())
    return f"{s['count']}건 / {won(s['total'])} (마진 {won(s['margin'])}) — {ch}"


def brief():
    today = datetime.date.today()
    L = [f'[아침 브리핑] {day_label(today)}', '']

    sch = schedule_on(today, today)
    L.append('오늘 일정')
    L += [f"- {s['name']} ({s['type']}{' · ' + s['staff'] if s['staff'] else ''})"
          for s in sch] or ['- 없음']

    pos = open_pos(today)
    if pos:
        L += ['', f'발주 진행 {len(pos)}건']
        for p in sorted(pos, key=lambda x: -x['late']):
            eta = f"입고예정 {p['eta'][:10]}" if p['eta'] else '입고예정 미정'
            if p['late'] > 0:
                eta += f" — {p['late']}일 지연"
            L.append(f"- {p['no']} [{p['status']}] 미입고 {won(p['remain'])} · {eta}")

    low, negative = stock_alerts()
    if low or negative:
        L += ['', '재고 경고']
        for r in negative:
            L.append(f"- {r['name']} 현재고 {r['stock']} — 음수, 기록 누락 확인 필요")
        soldout = [r for r in low if r['status'] == '품절']
        lowstock = [r for r in low if r['status'] == '품절임박']
        if lowstock:
            L.append(f"- 품절임박 {len(lowstock)}종: "
                     + ', '.join(r['name'] for r in lowstock[:5])
                     + (' 외' if len(lowstock) > 5 else ''))
        if soldout:
            L.append(f"- 품절(판매중 유지) {len(soldout)}종: "
                     + ', '.join(r['name'] for r in soldout[:5])
                     + (' 외' if len(soldout) > 5 else ''))

    unpaid = unpaid_vendors()
    if unpaid:
        L += ['', '미지급 (거래처)']
        L += [f"- {v['name']} {won(v['amount'])}" for v in unpaid]

    diffs = settlement_diffs()
    if diffs:
        L += ['', '정산 차이 (원인 추적 필요)']
        L += [f"- {d['name']}: {'+' if d['diff'] > 0 else ''}{int(d['diff']):,}원" for d in diffs]

    y = today - datetime.timedelta(days=1)
    L += ['', f'어제 매출: {fmt_sales(sales_summary(y))}']
    return '\n'.join(L)


def closeout():
    today = datetime.date.today()
    tomorrow = today + datetime.timedelta(days=1)
    L = [f'[마감] {day_label(today)}', '']

    s = sales_summary(today)
    if s['count'] == 0:
        L.append('오늘 기록된 매출이 없습니다 — 실제로 판매가 없었는지, 기록을 안 한 것인지 확인해주세요.')
    else:
        L.append(f'오늘 매출: {fmt_sales(s)}')

    _, negative = stock_alerts()
    if negative:
        L += ['', '재고 이상 (음수 = 기록 누락 신호)']
        L += [f"- {r['name']} 현재고 {r['stock']}" for r in negative]

    sch = schedule_on(tomorrow, tomorrow)
    L += ['', '내일 일정']
    L += [f"- {x['name']} ({x['type']}{' · ' + x['staff'] if x['staff'] else ''})"
          for x in sch] or ['- 없음']

    L += ['', '오늘 기록 안 된 판매·반품·입고가 있으면 이 채널에 한 줄로 남겨주세요 — 바로 기록합니다.']
    return '\n'.join(L)


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else ''
    if mode == 'brief':
        print(brief())
    elif mode == 'closeout':
        print(closeout())
    else:
        sys.exit(__doc__)
