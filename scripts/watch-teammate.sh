#!/usr/bin/env bash
# 팀원이 origin/main에 올린 새 커밋을 감시한다 (2인 협업 — 둘 다 main에 직접 push).
#
# 세션 시작 훅이 Claude에게 이 스크립트를 Monitor로 띄우라고 지시한다.
# stdout 한 줄 = 알림 한 건이므로, 알릴 것이 없으면 아무것도 출력하지 않는다.
#
# fetch만 하고 작업 트리는 건드리지 않는다 — 자동 pull은 작업 중인 파일을
# 덮어쓸 수 있어서 일부러 하지 않는다. 알림을 받으면 사람이 판단해서 pull한다.
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR" || exit 1

INTERVAL_SEC="${WATCH_INTERVAL_SEC:-120}"

# 내가 방금 올린 커밋까지 알리면 시끄럽다 — 작성자 이름으로 걸러낸다.
ME="$(git config user.name || echo '')"

# 새 커밋 목록을 출력한다 (내 커밋 제외). 없으면 아무것도 출력하지 않는다.
report_new_commits() {
  local from="$1" to="$2"
  local lines
  lines="$(git log --format='새 커밋 — %an: %s' "$from..$to" 2>/dev/null || true)"
  [ -z "$lines" ] && return 0
  if [ -n "$ME" ]; then
    # grep이 전부 걸러내 exit 1을 내도 감시가 죽으면 안 된다.
    printf '%s\n' "$lines" | grep -v "^새 커밋 — ${ME}:" || true
  else
    printf '%s\n' "$lines"
  fi
}

prev="$(git rev-parse origin/main 2>/dev/null || echo '')"

while true; do
  sleep "$INTERVAL_SEC"

  # 네트워크가 잠깐 끊겨도 감시가 죽으면 안 된다.
  git fetch --quiet origin main 2>/dev/null || true
  cur="$(git rev-parse origin/main 2>/dev/null || echo "$prev")"

  if [ -n "$cur" ] && [ "$cur" != "$prev" ]; then
    # 첫 실행에서 prev를 못 읽었으면 기준점만 잡고 넘어간다(과거 커밋 도배 방지).
    [ -n "$prev" ] && report_new_commits "$prev" "$cur"
    prev="$cur"
  fi
done
