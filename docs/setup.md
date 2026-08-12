# 설치 가이드

## 준비물
- [Claude Code](https://claude.com/claude-code) (CLI 또는 데스크톱)
- Notion 계정 (무료 플랜 가능)
- (선택) 회의록 기능: macOS + [mlx-whisper](https://github.com/ml-explore/mlx-examples) — 로컬 전사, 녹음이 외부로 나가지 않음
- (선택) Discord 봇 채널 — 매장에서 폰으로 입력하는 창구

## 1. Notion Integration 만들기
1. https://www.notion.so/my-integrations → New integration → 토큰(`ntn_...`) 복사
2. Notion 에서 운영 허브로 쓸 페이지 1개 생성 (예: "우리샵 운영")
3. 그 페이지 우상단 `···` → 연결 → 방금 만든 Integration 추가

## 2. 데이터베이스 생성 (한 번만)
```bash
export NOTION_TOKEN=ntn_xxx
python3 setup/create_databases.py <parent_page_id> --staff "대표,직원A,직원B"
```
- `parent_page_id`: 페이지 URL 끝의 32자리 ID
- DB 13종 + 뷰 + 운영 대시보드가 자동 생성되고, 설정이 `~/.claude/.comad/select-shop.json` 에 저장됩니다
- 대시보드 블록은 페이지 맨 아래에 생기니 맨 위로 드래그하세요

## 3. 스킬 설치
```bash
./install.sh
```

## 4. Claude Code 에 Notion MCP 연결 (선택 권장)
```bash
claude mcp add -s user notion -e NOTION_TOKEN=ntn_xxx -- npx -y @notionhq/notion-mcp-server
```
스킬 스크립트는 MCP 없이도 REST 로 동작하지만, MCP 를 연결하면 Claude 가 Notion 을 직접 읽고 쓸 수 있습니다.

## 5. (선택) 회의록 전사 환경
```bash
python3 -m venv ~/.claude/tools/whisper-venv
~/.claude/tools/whisper-venv/bin/pip install mlx-whisper
```
첫 실행 시 모델(~1.6GB)이 자동 다운로드됩니다.

## 확인
Claude Code 에서:
- "오늘 오프라인에서 OO티 1장 카드로 팔림, 4.5만" → 매출 기록 + 재고 차감
- 회의 녹음 파일 첨부 + "회의록 정리해줘" → Notion 회의록 + 액션아이템
- "이번 주 매출 어때?" → 채널·브랜드별 리포트
