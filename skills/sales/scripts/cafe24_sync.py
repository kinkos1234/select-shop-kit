#!/usr/bin/env python3
"""카페24 자사몰 주문 → Notion 주문/매출 동기화 어댑터 (골격 — 실계정 미검증).

★ 상태: 필드 매핑은 카페24 Admin API 문서 기준 초안이다. 실제 몰 페이로드로
  검증하기 전까지 --apply 를 크론에 물리지 말 것. 파서는 순수 함수로 분리돼
  있어 실측 페이로드가 오면 fixtures/ 를 교체하고 매핑만 고치면 된다.
  (실측 전 파서 확정 금지 — SOOP 브릿지와 같은 규율)

사용:
  cafe24_sync.py setup                             # 대화형 설정 (mall_id·앱 키 입력 → 저장)
  cafe24_sync.py auth-url                          # OAuth 승인 URL 출력
  cafe24_sync.py exchange <code>                   # 승인 코드 → 토큰 교환·저장
  cafe24_sync.py doctor [days=7]                   # 연동 점검 — 실주문 1페이지 수집 + 매핑 리포트
                                                   #   (개인정보 마스킹된 파일 2개 생성 → 개발자에게 전달)
  cafe24_sync.py pull [days=7]                     # 주문 조회 → 매핑 결과 dry-run 출력
  cafe24_sync.py pull [days] --apply               # Notion 에 실기록 (멱등, 실측 검증 후에만)
  cafe24_sync.py parse-fixture <payload.json>      # 모의 페이로드 파싱 검증 (네트워크 없음)

설정: select-shop.json 의 "cafe24" 섹션
  {"mall_id","client_id","client_secret","redirect_uri",
   "refresh_token","access_token","expires_at"}   ← exchange 가 토큰을 채운다

동기화 규율 (sync-integrity):
- 매출 라인 멱등키 = cafe24:<order_id>:<item_no> — 재실행해도 이중 기록 없음
- 상품 매칭은 custom_product_code(자체상품코드) ↔ 상품 DB SKU 의 **정확 일치만**.
  0건/2건+ 매칭은 추측하지 않고 unmatched 로 보고 → 사람/Claude 가 처리
- 취소·환불은 기존 행 수정이 아니라 반품 음수 행 append 후보로 보고만 한다
  (자동 append 는 실계정 검증 후 활성화)
- 빈 결과 반복은 성공이 아니라 점검 신호로 출력에 명시
"""
import datetime
import json
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import notion_sales as ns

CONFIG_PATH = os.path.expanduser(os.environ.get('SHOP_CONFIG', '~/.claude/.comad/select-shop.json'))
SCOPE = 'mall.read_order'


def cfg():
    c = json.load(open(CONFIG_PATH))
    if 'cafe24' not in c:
        sys.exit('select-shop.json 에 cafe24 섹션이 없습니다 (mall_id/client_id/client_secret/redirect_uri)')
    return c


def save_cfg(c):
    json.dump(c, open(CONFIG_PATH, 'w'), ensure_ascii=False, indent=2)


def base(c):
    return f"https://{c['cafe24']['mall_id']}.cafe24api.com"


# ── OAuth ───────────────────────────────────────────────


def cmd_auth_url():
    c = cfg()['cafe24']
    q = urllib.parse.urlencode({
        'response_type': 'code', 'client_id': c['client_id'],
        'redirect_uri': c['redirect_uri'], 'scope': SCOPE, 'state': 'select-shop-kit'})
    print(f"https://{c['mall_id']}.cafe24api.com/api/v2/oauth/authorize?{q}")
    print('\n브라우저에서 위 URL 승인 → redirect URL 의 ?code=... 값을 exchange 로 넘기세요.')


def _token_request(c, body):
    import base64
    cc = c['cafe24']
    basic = base64.b64encode(f"{cc['client_id']}:{cc['client_secret']}".encode()).decode()
    req = urllib.request.Request(
        f'{base(c)}/api/v2/oauth/token',
        data=urllib.parse.urlencode(body).encode(),
        headers={'Authorization': f'Basic {basic}',
                 'Content-Type': 'application/x-www-form-urlencoded'})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def cmd_exchange(code):
    c = cfg()
    d = _token_request(c, {'grant_type': 'authorization_code', 'code': code,
                           'redirect_uri': c['cafe24']['redirect_uri']})
    c['cafe24'].update({'access_token': d['access_token'],
                        'refresh_token': d['refresh_token'],
                        'expires_at': d.get('expires_at', '')})
    save_cfg(c)
    print('토큰 저장 완료 (access + refresh)')


def access_token(c):
    """만료 임박이면 refresh. 카페24 access 2시간 / refresh 2주."""
    cc = c['cafe24']
    exp = cc.get('expires_at', '')
    if exp:
        try:
            if datetime.datetime.fromisoformat(exp.replace('Z', '+00:00')) > \
               datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=10):
                return cc['access_token']
        except ValueError:
            pass
    d = _token_request(c, {'grant_type': 'refresh_token', 'refresh_token': cc['refresh_token']})
    cc.update({'access_token': d['access_token'], 'refresh_token': d['refresh_token'],
               'expires_at': d.get('expires_at', '')})
    save_cfg(c)
    return cc['access_token']


# ── 주문 조회 & 매핑 (파서는 순수 함수) ─────────────────


def fetch_orders(c, days):
    """주문 목록 + 품목 embed. ★ 실계정 미검증 — 파라미터·응답 형태는 문서 기준."""
    tok = access_token(c)
    end = datetime.date.today()
    start = end - datetime.timedelta(days=days)
    out, offset = [], 0
    while True:
        q = urllib.parse.urlencode({
            'start_date': start.isoformat(), 'end_date': end.isoformat(),
            'embed': 'items', 'limit': 100, 'offset': offset})
        req = urllib.request.Request(
            f'{base(c)}/api/v2/admin/orders?{q}',
            headers={'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json'})
        with urllib.request.urlopen(req) as r:
            batch = json.load(r).get('orders', [])
        out += batch
        if len(batch) < 100:
            return out
        offset += 100


CANCELED = {'canceled', 'canceling', 'returned', 'refunded'}


def map_order(o):
    """카페24 주문 1건 → {header, lines[], canceled_items[]}. 순수 함수 — fixture 테스트 대상.
    ★ 필드명은 문서 기준 초안: order_id, order_date, actual_order_amount.*, items[].*"""
    amt = o.get('actual_order_amount', {}) or {}
    header = {
        'order_no': o['order_id'],
        'date': str(o.get('order_date', ''))[:10],
        'subtotal': float(amt.get('order_price_amount', 0) or 0),
        'discount': abs(float(amt.get('coupon_discount_price', 0) or 0)),
        'shipping': float(amt.get('shipping_fee', 0) or 0),
    }
    lines, canceled = [], []
    for it in o.get('items', []) or []:
        row = {
            'item_no': it.get('item_no'),
            'input_key': f"cafe24:{o['order_id']}:{it.get('item_no')}",
            'sku': (it.get('custom_product_code') or '').strip(),
            'product_name': it.get('product_name', ''),
            'option': it.get('option_value', ''),
            'qty': int(it.get('quantity', 0) or 0),
            'price': float(it.get('product_price', 0) or 0),
            'status': it.get('order_status', ''),
        }
        (canceled if str(row['status']).lower() in CANCELED else lines).append(row)
    return {'header': header, 'lines': lines, 'canceled_items': canceled}


def match_products(lines):
    """SKU 정확 일치만 매칭. 0건/복수 매칭은 unmatched — 추측 금지."""
    matched, unmatched = [], []
    for row in lines:
        cands = []
        if row['sku']:
            found = ns.query_all(ns.DB['products'], {'filter': {
                'property': 'SKU', 'rich_text': {'equals': row['sku']}}})
            cands = [{'id': p['id'], 'name': ns.plain(p['properties']['상품명'])} for p in found]
        if len(cands) == 1:
            matched.append({**row, 'product_id': cands[0]['id'], 'product_name_notion': cands[0]['name']})
        else:
            unmatched.append({**row, 'match_candidates': len(cands)})
    return matched, unmatched


def cmd_pull(days='7', *flags):
    apply_mode = '--apply' in flags
    c = cfg()
    orders = fetch_orders(c, int(days))
    report = run_sync(orders, apply_mode)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def run_sync(orders, apply_mode):
    known = set()
    if orders:
        known = {k for k in json.loads(
            run_capture('existing-orders', '60')) if k}
    report = {'orders_fetched': len(orders), 'created': [], 'skipped_known': [],
              'unmatched': [], 'cancel_candidates': [], 'applied': apply_mode}
    if not orders:
        report['warning'] = '0건 — 정상 무주문인지, 토큰/권한/기간 문제인지 확인 필요 (빈 결과는 성공 신호가 아니다)'
        return report
    for o in orders:
        m = map_order(o)
        if m['canceled_items']:
            report['cancel_candidates'].append({
                'order_no': m['header']['order_no'],
                'items': m['canceled_items'],
                'note': '반품 음수 행 append 후보 — 자동 반영은 실계정 검증 후'})
        if m['header']['order_no'] in known:
            report['skipped_known'].append(m['header']['order_no'])
            continue
        matched, unmatched = match_products(m['lines'])
        report['unmatched'] += [{**u, 'order_no': m['header']['order_no']} for u in unmatched]
        entry = {'header': m['header'], 'lines_matched': len(matched), 'lines_unmatched': len(unmatched)}
        if apply_mode and matched:
            import tempfile
            with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
                json.dump({**m['header'], 'status': '결제완료'}, f, ensure_ascii=False)
                hp = f.name
            order_id = json.loads(run_capture('add-order', hp))['id']
            for row in matched:
                with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
                    json.dump({
                        'title': f"{m['header']['date'][5:7]}{m['header']['date'][8:10]} 자사몰 {row['product_name_notion']}",
                        'date': m['header']['date'], 'channel': '자사몰',
                        'product_id': row['product_id'], 'qty': row['qty'],
                        'price': row['price'], 'order_no': m['header']['order_no'],
                        'input_key': row['input_key'], 'order_id': order_id,
                    }, f, ensure_ascii=False)
                    lp = f.name
                json.loads(run_capture('add-sale', lp))
            entry['notion_order_id'] = order_id
        report['created'].append(entry)
    return report


def run_capture(cmd, *args):
    """notion_sales.py 서브커맨드를 프로세스로 실행해 stdout 캡처 (print 기반 헬퍼 재사용)."""
    import subprocess
    r = subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'notion_sales.py'),
         cmd, *args], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f'notion_sales {cmd} 실패: {r.stderr[:300]}')
    return r.stdout


def cmd_setup():
    """대화형 설정 — 의뢰인이 개발자센터에서 받은 값을 그대로 붙여넣는다."""
    try:
        c = json.load(open(CONFIG_PATH))
    except FileNotFoundError:
        sys.exit(f'설정 파일이 없습니다: {CONFIG_PATH}\n먼저 setup/create_databases.py 로 킷을 설치해주세요.')
    print('카페24 개발자센터(developers.cafe24.com)에서 앱을 만들고 아래 값을 입력하세요.')
    print('권한 스코프는 mall.read_order 하나면 됩니다.\n')
    cur = c.get('cafe24', {})
    def ask(label, key, default=''):
        d = cur.get(key) or default
        v = input(f'{label}{f" [{d}]" if d else ""}: ').strip()
        return v or d
    cc = {
        'mall_id': ask('몰 ID (https://<이것>.cafe24.com)', 'mall_id'),
        'client_id': ask('클라이언트 ID', 'client_id'),
        'client_secret': ask('클라이언트 시크릿', 'client_secret'),
        'redirect_uri': ask('Redirect URI (개발자센터에 등록한 값)', 'redirect_uri', 'https://localhost/callback'),
    }
    if not all(cc.values()):
        sys.exit('네 값 모두 필요합니다. 다시 실행해주세요.')
    c['cafe24'] = {**cur, **cc}
    save_cfg(c)
    print(f'\n저장 완료 → {CONFIG_PATH}')
    print('다음 단계: python3 cafe24_sync.py auth-url')


MASK_KEYS = ('name', 'phone', 'cellphone', 'email', 'address', 'zipcode',
             'member_id', 'buyer', 'receiver', 'ip', 'personal')
# 상품 계열 키는 마스킹 예외 — 매핑 검증(상품명·SKU 대조)에 필요하다
PRESERVE_KEYS = ('product', 'item', 'option', 'brand', 'sku', 'shop')


def mask_pii(obj):
    """고객 개인정보로 보이는 값을 마스킹 — 구조(키)와 상품 정보는 보존해 매핑 검증엔 지장 없게."""
    if isinstance(obj, dict):
        def one(k, v):
            kl = k.lower()
            if isinstance(v, (dict, list)) or any(w in kl for w in PRESERVE_KEYS):
                return mask_pii(v)
            return '◼︎masked' if any(m in kl for m in MASK_KEYS) else mask_pii(v)
        return {k: one(k, v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask_pii(x) for x in obj]
    return obj


def friendly_http(e):
    code = getattr(e, 'code', None)
    hints = {401: '토큰이 만료됐거나 승인이 안 됐습니다 — auth-url 부터 다시 진행해주세요.',
             403: '앱 권한(mall.read_order)이 없습니다 — 개발자센터에서 스코프를 확인해주세요.',
             404: '몰 ID 가 틀렸을 수 있습니다 — setup 으로 다시 확인해주세요.',
             429: '요청이 너무 잦습니다 — 잠시 후 다시 시도해주세요.'}
    body = ''
    try:
        body = e.read().decode()[:300]
    except Exception:
        pass
    return f'카페24 API 오류 {code}: {hints.get(code, "")}\n{body}'


def cmd_doctor(days='7'):
    """연동 점검 — 실주문 1페이지를 받아 (1) PII 마스킹 원문 (2) 매핑 dry-run 리포트를 파일로.
    의뢰인은 이 두 파일만 개발자에게 보내면 된다. Notion 에는 아무것도 쓰지 않는다."""
    import datetime as dt
    import urllib.error
    c = cfg()
    print('1/3 토큰 확인 중...')
    try:
        access_token(c)
    except urllib.error.HTTPError as e:
        sys.exit(friendly_http(e))
    except KeyError:
        sys.exit('토큰이 없습니다 — auth-url → exchange 를 먼저 진행해주세요.')
    print('   OK')
    print(f'2/3 최근 {days}일 주문 수집 중...')
    try:
        orders = fetch_orders(c, int(days))
    except urllib.error.HTTPError as e:
        sys.exit(friendly_http(e))
    print(f'   {len(orders)}건 수신')
    stamp = dt.date.today().strftime('%Y%m%d')
    raw_path = f'cafe24-raw-{stamp}.json'
    json.dump({'orders': mask_pii(orders[:20])}, open(raw_path, 'w'), ensure_ascii=False, indent=2)
    print('3/3 매핑 리포트 생성 중...')
    report = run_sync(orders, apply_mode=False)
    report['note'] = '점검 전용 — Notion 에는 아무것도 기록하지 않았습니다.'
    rep_path = f'cafe24-doctor-report-{stamp}.json'
    json.dump(report, open(rep_path, 'w'), ensure_ascii=False, indent=2)
    print(f"""
점검 완료. 아래 두 파일을 개발자에게 보내주세요 (고객 개인정보는 마스킹돼 있습니다):
  1) {raw_path}  — 주문 원문 구조 (매핑 확정용, 최대 20건)
  2) {rep_path}  — 매핑 결과 리포트
주문이 0건이면 기간을 늘려 다시: python3 cafe24_sync.py doctor 30""")


def cmd_parse_fixture(path):
    """네트워크 없이 파서만 검증 — 실측 페이로드 확보 시 이 fixture 를 교체한다."""
    data = json.load(open(path))
    orders = data.get('orders', data if isinstance(data, list) else [data])
    out = [map_order(o) for o in orders]
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    cmds = {'setup': cmd_setup, 'auth-url': cmd_auth_url, 'exchange': cmd_exchange,
            'doctor': cmd_doctor, 'pull': cmd_pull, 'parse-fixture': cmd_parse_fixture}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        sys.exit(__doc__)
    cmds[sys.argv[1]](*sys.argv[2:])
