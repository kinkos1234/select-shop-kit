#!/bin/bash
# select-shop-kit 스킬 설치 — ~/.claude/skills/ 에 복사 (이름 충돌 시 중단)
set -e
DEST="$HOME/.claude/skills"
mkdir -p "$DEST"
for s in meeting sales shop-report closeout; do
  if [ -e "$DEST/$s" ]; then
    echo "SKIP: $DEST/$s 이미 존재 — 충돌 확인 후 수동 설치하세요"
  else
    cp -r "$(dirname "$0")/skills/$s" "$DEST/$s"
    echo "installed: $s"
  fi
done
echo ""
echo "다음 단계:"
echo "  1) Notion Integration 토큰 발급 → export NOTION_TOKEN=ntn_..."
echo "  2) python3 setup/create_databases.py <parent_page_id> --staff \"이름1,이름2,이름3\""
echo "  3) Claude Code 에서 /meeting, /sales, /shop-report, /closeout 사용"
