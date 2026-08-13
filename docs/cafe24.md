# 카페24 자사몰 연동 (준비 단계)

`skills/sales/scripts/cafe24_sync.py` 가 카페24 주문을 Notion 주문/매출 원장으로 끌어옵니다.

> **상태: 골격 (실계정 미검증).** 필드 매핑은 카페24 Admin API 문서 기준 초안입니다.
> 실제 몰 페이로드로 `parse-fixture` 대조를 마치기 전에는 `--apply` 를 크론에 물리지 마세요.

## 1. 카페24 개발자센터 앱 등록

1. [developers.cafe24.com](https://developers.cafe24.com) → 앱 만들기 (Private 앱이면 충분)
2. 권한 스코프: **mall.read_order** (주문 조회)
3. Redirect URI 등록 (로컬 테스트면 `https://localhost/callback` 같은 값도 가능 — 코드만 복사하면 됨)
4. 발급받은 `client_id` / `client_secret` 확보

## 2. 설정 파일에 cafe24 섹션 추가

`~/.claude/.comad/select-shop.json` (또는 `SHOP_CONFIG` 경로):

```json
"cafe24": {
  "mall_id": "yourmall",
  "client_id": "...",
  "client_secret": "...",
  "redirect_uri": "https://localhost/callback"
}
```

## 3. OAuth 승인 (최초 1회)

```bash
python3 skills/sales/scripts/cafe24_sync.py auth-url    # URL 출력 → 브라우저에서 승인
python3 skills/sales/scripts/cafe24_sync.py exchange <redirect 로 받은 code>
```

이후 access 토큰(2시간)은 refresh 토큰(2주)으로 자동 갱신됩니다.
**주의: refresh 토큰은 2주 안에 한 번은 pull 이 돌아야 유지됩니다** — 크론 연결 전 수동 운용 중이면 만료될 수 있습니다.

## 4. 동기화

```bash
python3 skills/sales/scripts/cafe24_sync.py pull 7            # dry-run — 매핑 결과만 출력
python3 skills/sales/scripts/cafe24_sync.py pull 7 --apply    # Notion 실기록 (실측 검증 후에만)
```

동작 규율:

- **멱등** — 매출 라인 입력키 `cafe24:<주문번호>:<품목번호>`. 같은 기간을 두 번 돌려도 이중 기록 없음
- **상품 매칭은 정확 일치만** — 카페24 자체상품코드 ↔ 상품 DB SKU. 매칭 0건/복수 건은 `unmatched` 로 보고만 하고 기록하지 않습니다 (추측 금지 — Claude 세션이나 사람이 확정)
- **취소·환불은 자동 반영 안 함** — `cancel_candidates` 로 보고. 반품은 원장 규율대로 음수 행 append 를 사람이/Claude 가 확정
- **0건 = 성공 아님** — 무주문인지 토큰/권한 문제인지 warning 으로 구분 요구

## 5. 실계정 검증 체크리스트 (활성화 전 필수)

1. 실제 몰에서 주문 목록 1페이지를 받아 `parse-fixture` 로 매핑 대조 (fixtures/ 교체)
2. `actual_order_amount` 하위 필드명·부호(할인 음수 여부) 실측 확인
3. `order_status` 실제 코드값 확인 → `CANCELED` 집합 갱신 (문서상 N10/C40 계열 코드 체계)
4. 테스트 주문 1건 `--apply` → Notion 주문 헤더+라인·원가 스냅샷·멱등 재실행 확인
5. 통과 후에만 크론 연결 (예: 30분 간격 `pull 3 --apply`)
