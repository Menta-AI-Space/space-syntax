#!/usr/bin/env bash
# 새 랩탑에서 이 저장소를 작업 가능한 상태로 만든다.
#
#   bash scripts/setup-laptop.sh              # 점검 + 없는 것만 채운다
#   bash scripts/setup-laptop.sh --force-db   # 이미 있는 DATABASE_URL 도 운영 DB로 덮는다
#
# 하는 일:
#   1. gh / railway / node / python 로그인·설치 상태를 점검한다
#   2. Railway 운영 DB 자격증명을 받아 backend/.env 의 DATABASE_URL 을 채운다
#   3. frontend-next/.env.local 을 만든다 (없는 키는 자리만 남긴다)
#   4. 운영 DB에 실제로 붙는지 확인한다
#
# 비밀값은 화면에 절대 찍지 않는다. 쓰는 파일은 전부 gitignore 대상이다.
set -uo pipefail
cd "$(dirname "$0")/.."

# Windows 콘솔(cp1252)에서 한글 출력이 죽지 않게
export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1

FORCE_DB=0
[ "${1:-}" = "--force-db" ] && FORCE_DB=1

# 2026-09-10 이름 정리: 프로젝트 empathetic-courage → space-syntax,
# DB 서비스 vigilant-radiance → postgres. 아래 둘은 CLI 인자라 새 이름을 쓴다.
# 주의: 내부 도메인은 따라오지 않아서 여전히 vigilant-radiance.railway.internal 이다.
RAILWAY_PROJECT=space-syntax
DB_SERVICE=postgres
RAILWAY_ENV=production

ok()   { printf '  \033[32mOK\033[0m   %s\n' "$1"; }
warn() { printf '  \033[33m..\033[0m   %s\n' "$1"; }
bad()  { printf '  \033[31mNO\033[0m   %s\n' "$1"; }

PY=""
for c in backend/.venv/Scripts/python.exe backend/.venv/bin/python python3 python; do
  if command -v "$c" >/dev/null 2>&1 || [ -x "$c" ]; then PY="$c"; break; fi
done

echo
echo "1) 도구·로그인 점검"

if gh auth status >/dev/null 2>&1; then
  ok "gh — $(gh api user --jq .login 2>/dev/null)"
else
  bad "gh 로그인 안 됨 →  gh auth login"
fi

if railway whoami >/dev/null 2>&1; then
  ok "railway — 로그인됨"
  RAILWAY_READY=1
else
  bad "railway 로그인 안 됨 →  railway login   (계정: cmdysdntqh08@gmail.com)"
  RAILWAY_READY=0
fi

command -v node >/dev/null 2>&1 && ok "node $(node -v)" || bad "node 없음 → https://nodejs.org (v20+)"
[ -n "$PY" ] && ok "python — $PY" || bad "python 없음"

echo
echo "2) 운영 DB 자격증명 → backend/.env"

if [ "$RAILWAY_READY" = "1" ] && [ -n "$PY" ]; then
  TMPJSON="$(mktemp)"
  if railway variables -p "$RAILWAY_PROJECT" -s "$DB_SERVICE" -e "$RAILWAY_ENV" --json > "$TMPJSON" 2>/dev/null && [ -s "$TMPJSON" ]; then
    RVJSON="$TMPJSON" FORCE_DB="$FORCE_DB" "$PY" - <<'PY'
import json, os, re, urllib.parse
v = json.load(open(os.environ["RVJSON"]))
dsn = "postgresql://{u}:{p}@{h}:{port}/{db}".format(
    u=v["POSTGRES_USER"],
    p=urllib.parse.quote_plus(v["POSTGRES_PASSWORD"]),
    h=v["RAILWAY_TCP_PROXY_DOMAIN"],
    port=v["RAILWAY_TCP_PROXY_PORT"],
    db=v["POSTGRES_DB"],
)
path = os.path.join("backend", ".env")
lines = open(path, encoding="utf-8").read().splitlines() if os.path.exists(path) else []

existing = ""
for line in lines:
    m = re.match(r"\s*DATABASE_URL\s*=\s*(.*)", line)
    if m:
        existing = m.group(1).strip()
if existing and os.environ.get("FORCE_DB") != "1":
    print("  [33m..[0m   backend/.env 에 DATABASE_URL 이 이미 있다 — 건드리지 않는다")
    print("       운영 DB로 바꾸려면:  bash scripts/setup-laptop.sh --force-db")
    raise SystemExit(0)

out, seen = [], False
for line in lines:
    if re.match(r"\s*DATABASE_URL\s*=", line):
        out.append("DATABASE_URL=" + dsn); seen = True
    else:
        out.append(line)
if not seen:
    out.append("DATABASE_URL=" + dsn)
open(path, "w", encoding="utf-8").write("\n".join(out) + "\n")
print("  \033[32mOK\033[0m   backend/.env 의 DATABASE_URL 을 운영 DB(%s)로 맞췄다" % v["RAILWAY_TCP_PROXY_DOMAIN"])
PY
  else
    bad "railway variables 실패 — 프로젝트 접근 권한을 확인하라"
  fi
  rm -f "$TMPJSON"
else
  warn "railway 로그인 또는 python 이 없어 건너뛴다"
fi

echo
echo "3) frontend-next/.env.local"

FE=frontend-next/.env.local
if [ -f "$FE" ]; then
  ok "이미 있음 (건드리지 않는다)"
else
  cat > "$FE" <<'ENV'
BACKEND_API_BASE_URL=https://space-syntax-production.up.railway.app
NEXT_PUBLIC_API_BASE_URL=https://space-syntax-production.up.railway.app
NEXT_PUBLIC_KAKAO_JS_KEY=
ENV
  ok "만들었다 — 라이브 백엔드를 보게 해뒀다"
  warn "NEXT_PUBLIC_KAKAO_JS_KEY 가 비어 있다. 지도를 띄우려면 채워라 (docs/SETUP_LAPTOP.md)"
fi

echo
echo "4) 운영 DB 접속 확인"

if [ -n "$PY" ]; then
  "$PY" - <<'PY'
import os, re, sys
path = os.path.join("backend", ".env")
dsn = None
if os.path.exists(path):
    for line in open(path, encoding="utf-8"):
        m = re.match(r"\s*DATABASE_URL\s*=\s*(.+)", line)
        if m: dsn = m.group(1).strip()
if not dsn:
    print("  \033[33m..\033[0m   DATABASE_URL 이 없어 건너뛴다"); sys.exit(0)
try:
    import psycopg2
except ImportError:
    print("  \033[33m..\033[0m   psycopg2 가 없다 →  cd backend && python -m venv .venv && .venv/Scripts/pip install -r requirements.txt -r requirements-dev.txt"); sys.exit(0)
try:
    cur = psycopg2.connect(dsn, connect_timeout=20).cursor()
    cur.execute("select postgis_version()")
    print("  \033[32mOK\033[0m   붙었다 — PostGIS %s" % cur.fetchone()[0].split()[0])
    for t in ("land_prices", "parcel_centroids", "roads_clean"):
        cur.execute("select count(*) from " + t)
        print("       %-18s %s행" % (t, format(cur.fetchone()[0], ",")))
except Exception as e:
    print("  \033[31mNO\033[0m   접속 실패: %s" % str(e).strip()[:160])
PY
else
  warn "python 이 없어 건너뛴다"
fi

echo
echo "막힌 항목이 있으면 docs/SETUP_LAPTOP.md 를 봐라."
echo
