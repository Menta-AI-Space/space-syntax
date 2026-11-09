#!/usr/bin/env bash
# 자율 러너 컨테이너 시작점.
#
#   자격 확인 → 저장소 clone/갱신 → 프론트 의존성 → 러너 루프
#
# 저장소를 런타임에 받는 이유는 Dockerfile.agent에 적어 뒀다. 여기서 중요한 건
# **자격증명을 원격 URL에 박지 않는 것**이다. `gh auth setup-git`은 credential
# helper를 설정할 뿐이라 토큰이 .git/config에 남지 않는다. URL에 박아 두면
# 로그·에러 메시지·`git remote -v` 어디로든 새어 나간다.

set -euo pipefail

REPO_SLUG="${REPO_SLUG:-space-syntax/space-syntax}"
WORKSPACE="${WORKSPACE:-/workspace}"
CHECKOUT="${WORKSPACE}/repo"

fail() { echo "[entrypoint] 실패: $1" >&2; exit 1; }
step() { echo "[entrypoint] $1"; }

step "시작"

# --- 자격 확인 — 없으면 조용히 헛도는 대신 바로 멈춘다 -----------------------

[ -n "${GH_TOKEN:-}" ] || fail "GH_TOKEN이 없다. Railway Variables에 넣어야 한다 (Contents·Issues 쓰기)."

if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  # 인증 우선순위상 API 키가 구독 토큰보다 먼저 쓰인다 = 과금된다.
  # 추가 비용을 쓰지 않기로 했으므로(CLAUDE.md) 여기서 세운다.
  fail "ANTHROPIC_API_KEY가 설정돼 있다. 이대로 두면 구독이 아니라 API로 과금된다. 변수를 지워라."
fi

[ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] || fail "CLAUDE_CODE_OAUTH_TOKEN이 없다. 로컬에서 'claude setup-token'으로 발급해 Railway Variables에 넣어야 한다."

# 값 자리에 설명문을 붙여넣는 실수가 실제로 났다 — 컨테이너는 40번 넘게 조용히
# 실패하고서야 원인이 드러났다. 토큰처럼 안 생긴 값은 시작 전에 막는다.
case "$CLAUDE_CODE_OAUTH_TOKEN" in
  *" "*|"("*|"<"*)
    fail "CLAUDE_CODE_OAUTH_TOKEN 값이 토큰이 아니다(공백이나 괄호로 시작). 설명문 대신 'claude setup-token'이 출력한 문자열을 그대로 넣어야 한다." ;;
esac
case "$GH_TOKEN" in
  *" "*|"("*|"<"*)
    fail "GH_TOKEN 값이 토큰이 아니다. GitHub에서 발급한 github_pat_... 문자열을 그대로 넣어야 한다." ;;
esac

# --- git 자격 — 토큰을 URL에 박지 않는다 -------------------------------------

# `gh auth login --with-token`을 부르지 않는다. GH_TOKEN이 환경에 있으면 gh는
# 그걸 그대로 쓰며, 그 상태에서 login을 부르면 "먼저 환경변수를 지우라"며 0이
# 아닌 코드로 끝난다 — set -e와 만나 컨테이너가 조용히 재시작을 반복했다.
#
# git 인증도 gh에 맡기지 않고 helper를 직접 건다. helper는 실행 시점에 환경변수를
# 읽으므로 토큰이 .git/config에도 원격 URL에도 남지 않는다.
git config --global credential.helper   '!f() { echo username=x-access-token; echo "password=${GH_TOKEN}"; }; f'

git config --global user.name "${GIT_USER_NAME:-space-syntax-agent}"
git config --global user.email "${GIT_USER_EMAIL:-agent@users.noreply.github.com}"
git config --global --add safe.directory "$CHECKOUT"
# 러너가 rebase pull을 하므로 기본 전략을 못 박는다. 안 박으면 pull에서 멈춘다.
git config --global pull.rebase true

# --- 저장소 ------------------------------------------------------------------

if [ -d "$CHECKOUT/.git" ]; then
  step "기존 체크아웃 갱신"
  git -C "$CHECKOUT" fetch origin main
  # 재시작 시점에 남아 있던 중간 편집은 버린다. 진행 중이던 작업은 러너가
  # task-state.json을 보고 pending으로 되돌려 처음부터 다시 한다 — 반쯤 고친
  # 코드를 안고 시작하는 것보다 낫다.
  git -C "$CHECKOUT" reset --hard origin/main
  git -C "$CHECKOUT" clean -fd
else
  step "저장소 clone"
  git clone --depth 50 "https://github.com/${REPO_SLUG}.git" "$CHECKOUT"
fi

cd "$CHECKOUT"

# --- 프론트 의존성 — 검증에 필요하다 ------------------------------------------

if [ ! -d frontend-next/node_modules ]; then
  step "프론트 의존성 설치"
  (cd frontend-next && npm ci --no-audit --no-fund)
fi

# --- claude 자가진단 — 실패 원인을 부팅 로그에 남긴다 -------------------------
#
# 작업이 실패해도 러너 로그만으로는 "왜 못 했는지"가 안 보이는 경우가 있었다.
# 여기서 한 번 시험 호출해 두면, 인증·네트워크·CLI 문제를 작업과 무관하게
# 부팅 시점에 바로 알 수 있다. 실패해도 러너는 그대로 띄운다 — 진단이 목적이지
# 여기서 세우는 게 목적이 아니다.

step "claude 자가진단"
if selftest="$(claude -p 'OK 두 글자만 답하라' --output-format json 2>&1)"; then
  step "claude 정상: $(echo "$selftest" | head -c 200)"
else
  echo "[entrypoint] claude 자가진단 실패: $(echo "$selftest" | head -c 800)" >&2
fi

# --- 텔레그램 봇 — 러너와 별도 프로세스 ---------------------------------------
#
# 봇은 지시를 받아 inbox 파일에 적기만 하고, 작업으로 만드는 일은 러너가 한다
# (상태 파일의 작성자를 하나로 유지). 토큰이 없으면 봇 없이 러너만 돈다 —
# 텔레그램을 안 쓰는 환경에서 부팅이 막히면 안 된다.

if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
  step "텔레그램 봇 시작"
  # 죽으면 다시 띄운다. 봇이 조용히 죽으면 지시를 보내도 아무 답이 없는데,
  # 러너는 멀쩡히 돌고 있어서 사람은 무엇이 잘못됐는지 알 방법이 없다.
  # 5초 간격은 즉시 재시도로 로그를 채우는 것을 막는다.
  ( while true; do
      python backend/tools/agent/telegram_bot.py || echo "[entrypoint] 봇이 죽었다 — 5초 뒤 재시작" >&2
      sleep 5
    done ) &
else
  step "TELEGRAM_BOT_TOKEN 없음 — 봇 없이 러너만 돈다"
fi

step "러너 시작 (구독 인증, API 과금 없음)"
exec python backend/tools/agent/runner.py "$@"
