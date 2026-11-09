# 인프라 — 배포·DB·비밀값·로컬 실행

CLAUDE.md의 "배포 흐름"·"로컬 실행" 항목의 상세 근거다.

## Railway 계정 구조 (2026-09-10 정리 후)

| 무엇 | 어디 |
| --- | --- |
| 라이브 백엔드 | `space-syntax` 프로젝트 → `backend` 서비스 |
| 운영 DB | `space-syntax` 프로젝트 → `postgres` 서비스 |
| 자율 러너 | `space-syntax-agent` 프로젝트 → `space-syntax-agent` 서비스 |
| 학습용 DB | `space-syntax-agent` 프로젝트 → `experiment-postgres` 서비스 |
| 외부 접속 | TCP 프록시 이미 열림 (`railway tcp-proxy list`) |
| DB 사양 | PostGIS 3.4.3 / PostgreSQL 16.4, DB·유저 이름 `spacesyntax` |

### 결정 (2026-10-23 최종) — 랩미팅(10/27) 뒤 사용자 작업공간으로 Transfer

**지금 상태**: 프로젝트는 Railway 작업공간 **"Chae Yeon Kim's Projects"**(Pro, 주인
`cmdysdntqh08@gmail.com`)에 있다. **결제 카드는 이미 사용자 카드**이고, 사용자 계정
`neverforg77@gmail.com`은 그 작업공간의 **Admin**이다. 배포·변수·로그를 사용자 계정으로 다 한다.
GitHub 자동 배포 토큰이 죽어 있는 동안은 CLI로 직접 배포한다(`SETUP_LAPTOP.md` 증상표).

**실제 비용**(Usage 화면): 작업공간 전체 월 $20~29 (8월 $24.51, 9월 $28.79). 10/16~22 엿새
동안 space-syntax $1.24, AroundU $2.65, seller-copilot $1.21, space-syntax-agent $0.45 —
**우리 서비스는 월 약 $6**.

**결정**: 사용자가 직접 결제·관리하고 채연을 초대해 같이 쓴다. **랩미팅 전에는 옮기지 않는다**
(시연 직전 이전은 라이브가 멈출 위험). 랩미팅 뒤 순서:
1. 사용자 작업공간(summerforg's Projects)에 Pro 결제 — DB 볼륨 20GB라 Hobby(5GB) 불가
2. 프로젝트별 **Settings → Transfer Project** → 사용자 작업공간. 사용자가 양쪽 Admin이라 직접
   가능. 서비스·볼륨·변수·`*.up.railway.app` 주소가 그대로 따라온다(Railway 직원 답변 기준)
3. 사용자 작업공간에 채연 초대
4. **채연 작업공간의 결제 정리**(카드 제거·해지 또는 채연 카드로) — 안 하면 Pro $20이 같은 카드로 두 번
5. 보안 사고 대응(비밀값 교체) + 사용자 작업공간 기준으로 GitHub `RAILWAY_TOKEN` 새로 발급

버린 안: ① 10/22 아침 「사용자 계정에 새로 만들기」 — DB를 직접 복사해야 하고 주소가 바뀐다.
Transfer가 같은 결과를 훨씬 싸게 낸다. ② 10/22 오후 「옮기지 않기」(비용만 보면 가장 쌈) —
사용자가 자기 작업공간으로 관리하길 원했다. ③ 지금 토큰부터 새로 만들기 — 옮긴 뒤 다시 만들어야 한다.

**보안 사고 뒤 OpenAI 사용 확인 (2026-10-23, 사용자 화면으로 확인)**: 지난 7일(10/17~23) 모든 프로젝트
합계 $0.20, 요청 20건, 사용자 왕종미 한 명뿐. 10/22 하루 $0.16(그날 LLM 질의창 시험). 10월 누계 $2.08 /
월 한도 $100. 도용 흔적 없음 → `OPENAI_API_KEY` 교체는 랩미팅 뒤 Railway 이전 때 함께 한다.
권고: 월 한도를 $20으로 낮춘다(키가 새도 피해가 $20에서 멈춘다). 랩미팅 당일 LLM 답 한 번 31초 실측.

같은 날 배포가 `Unauthorized`·`paused`로 막힌 건 이 계정 구조 때문이 아니라 **Railway 보안
사고**(전체 토큰 무효화·빌드 중단, `SETUP_LAPTOP.md` 증상표)였다. 보안 공지에 따라 Railway에
저장된 비밀값 교체가 남아 있다.

2026-09-10 전에는 라이브가 `empathetic-courage`라는 랜덤 이름의 프로젝트에 있었고
`space-syntax`라는 이름의 프로젝트는 러너 전용이었다. 지금은 이름이 내용과 맞는다.
옛 이름이 적힌 문서·스크립트를 보면 그건 낡은 것이다.

### 함정 — 서비스 이름과 내부 도메인은 다르다

**`RAILWAY_PRIVATE_DOMAIN`은 서비스를 만들 때의 이름으로 고정되고, 이름을 바꿔도
따라오지 않는다.** 그래서 지금 이렇다:

| 서비스 이름 | 내부 도메인 |
| --- | --- |
| `backend` | `space-syntax.railway.internal` |
| `postgres` | `vigilant-radiance.railway.internal` |

`DATABASE_URL`을 서비스 이름에 맞춰 `postgres.railway.internal`로 "고치면" 라이브가
죽는다. 2026-09-10에 실제로 그렇게 해서 6분간 DB 엔드포인트가 전부 500이었다:

```
psycopg2.OperationalError: could not translate host name
"postgres.railway.internal" to address: Name or service not known
```

`/api/health`는 DB를 안 건드려서 그동안에도 200이었다. **헬스체크만으로 배포를
검증하지 말 것** — `/api/undervalued-areas`처럼 DB를 읽는 엔드포인트를 같이 친다.

### 나머지 함정

- 라이브를 확인하는 기준은 이름이 아니라 `RAILWAY_PUBLIC_DOMAIN`이다. 그게
  `space-syntax-production.up.railway.app`인 서비스가 라이브다. 서비스 이름을 바꿔도
  공개 도메인은 유지된다 (내부 도메인과 달리 이쪽은 안전하다).
- `railway ssh -s <백엔드>`는 컨테이너에 닿지 않고 railway.new 메타 응답으로 폴백한다.
  환경변수는 `railway variables -s <서비스> --json`으로 본다. **`--json` 없는 표
  형식은 비밀값을 화면에 그대로 뿌린다** — 이걸로 `OPENAI_API_KEY`를 노출해 재발급해야
  했다.
- `railway` CLI에는 rename 명령이 없다. 이름 변경은 대시보드나 GraphQL API
  (`projectUpdate`·`serviceUpdate`)로만 된다. `railway config`(IaC)는 `pull`이 실제
  설정을 못 가져오고 빈 placeholder를 뱉으니 라이브 프로젝트에 `apply`하지 말 것.

## 비밀값

- Railway·Firebase는 chaeyeon573 계정으로 배포된다 — GitHub Actions 시크릿을 통해서다
  (개인 로그인을 공유하지 않는다). `RAILWAY_TOKEN`·`FIREBASE_TOKEN`은 저장소
  Settings → Secrets.
- 두 랩탑 다 배포할 수 있다. **기본 경로는 push다** — Railway 로그인조차 필요 없다.
  로컬 CLI(`railway logs`·`railway up`)가 필요하면 그 랩탑에서 `railway login` 한 번.
- `railway up`을 손으로 올리기 전엔 반드시 `git pull` — 옛 로컬로 올리면 Actions가
  배포한 최신 커밋을 조용히 덮는다 (2026-07-27 실사고).
- **2026-09-10 ~ 09-18 backend 배포 공백 (실사고).** 9/10 서비스 이름을
  `space-syntax → backend`로 바꿨는데 `.github/workflows/deploy.yml`의
  `railway up --service space-syntax`를 안 고쳐서 8일간 backend 커밋 10개가 라이브에
  가지 않았다. GitHub Actions는 초록이었다 — `railway up --detach`는 던지고 끝나므로
  대상 서비스가 없어도 워크플로가 실패하지 않는다. **서비스 이름을 바꾸면 deploy.yml도
  같이 바꾼다.** 반영 확인은 `railway deployment list -s backend --json`의 마지막
  시각으로 한다 (`/api/health`는 옛 컨테이너도 ok를 준다).
- 손으로 올릴 땐 **레포 루트에서** `railway up --detach -s backend -p <프로젝트ID> -e production`.
  `backend/` 안에서 올리면 서비스의 root directory(/backend)가 아카이브에 없어 빌드가
  죽는다 (deploy.yml 주석, 2026-07-19 실측).
- `.env`, `frontend-next/.env.local`은 gitignore다. 필요하면 소유자에게 따로 받고,
  대화창이나 커밋에 실제 키 값을 적지 않는다.

## 로컬 백엔드가 안 돌 때

백엔드는 적재된 PostGIS가 필요해서 다른 랩탑에서 바로 못 돌릴 수 있다. 그럴 땐
`frontend-next/.env.local`에
`BACKEND_API_BASE_URL=https://space-syntax-production.up.railway.app`만 넣으면 라이브
백엔드에 붙어 프론트 작업을 그대로 할 수 있다.

## DB 용량 — "도로망 9.2GB"는 틀렸다 (2026-08-22 실측)

| 대상 | 용량 |
| --- | --- |
| `roads_clean` (Space Syntax 입력, 203,983 세그먼트) | 92 MB |
| `planet_osm_*` 합계 (OSM 원본) | 704 MB |
| 로컬 DB 전체 | 11.3 GB |

11GB의 대부분은 도로가 아니라 `living_pop_hourly`(2.3GB)·`sgis_grid_cells`(1.9GB)·
`golmok_stores`+`golmok_sales`(2.4GB)다. 9.2GB는 OSM 원본 pbf 크기이거나 과거 DB 전체
크기로 보인다.

토지가격 분석에 필요한 최소 집합은 `land_prices` + `parcel_centroids` + `roads_clean`
= 약 456MB이고 작은 클라우드 Postgres에 그대로 들어간다. **새 데이터를 어디에 둘지
정할 때 "도로망이 무겁다"를 근거로 쓰지 말 것.**

## 공시지가 확보 경로 — V-World 다운로드 CSV (2026-08-22 확정)

서울 열린데이터광장 OA-1180이 2026-08-18 종료된 뒤의 대체 경로: **V-World 공간정보
다운로드 `dsId=6`(개별공시지가정보, 테이블 AL_D151)**. 적재는
`backend/tools/ingest_land_prices.py`, 스키마는 `scripts/migrations/012_land_prices.sql`.

- **개별**공시지가(전 필지)다. 표준지가 아니다 — 서울 887,699행 중 표준지는
  31,463개(3.5%)뿐이다. 선행연구(강창덕 2021)가 7,701필지만 쓴 건 표준지 표본이었기
  때문으로 보이니, 우리 표본은 100배 이상 크다.
- `고유번호`가 PNU 19자리라 `parcel_centroids`와 그냥 조인된다 — 실측 조인율 99.86%.
- **파일 하나가 한 해분만 담는다.** 패널을 만들려면 연도별 파일을 각각 받아야 한다.
- 다운로드에 V-World 무료 로그인이 필요하다 (인증 없이 호출하면 Content-Length 0).
  사람이 받아 경로를 주거나 세션 쿠키를 `--fetch --cookie`로 넘긴다.
- 새 파일은 적재 전에 `--inspect`를 먼저 돌린다 (DB 불필요) — 헤더·인코딩·유효 행 수.
- 2026년분은 로컬·운영 DB 양쪽 적재 완료 (각 887,699행).

## 로컬 → 운영 DB 테이블 이전 (2026-08-30 실행)

**왜 했나.** 로컬(65개)과 운영(52개)을 대조했더니 로컬에만 있는 테이블이 13개였고,
전부 토지가치 모델의 파생 산출물이었다. 라이브 코드는 그 테이블을 참조하는데 DB에
없으니 **에러 대신 빈 값을 200으로** 돌려주고 있었다 — `/api/map-parcels` →
`{"parcels":[]}`, `/api/undervalued-areas` → `{"areas":[]}`. 조용히 죽어 있었다.

**무엇을 옮겼나.** `cv_folds`를 뺀 12개 (789MB 덤프):

| 테이블 | 행 수 |
| --- | --- |
| `parcel_characteristics` | 1,312,505 |
| `parcel_undervalued_scores` | 669,610 |
| `parcel_panel` | 476,786 |
| `parcel_neighbor_prices` | 473,140 |
| `road_segment_structure` | 170,707 |
| `parcel_polygons` | 54,925 |
| `parcel_accessibility` | 54,396 |
| `redevelopment_zones` | 3,209 |
| `subway_ridership` | 529 |
| `undervalued_area_scores` | 426 |
| `franchise_brands` · `land_use_zones` | 0 (스키마만) |

결과: 운영 52 → **64개 테이블**, 9.7GB → 10GB. 볼륨은 20GB라 여유가 있다.

**절차** (다음에 또 옮길 때 그대로 쓴다):

```bash
# 1. 운영 DSN 조립 — 내부 DATABASE_URL 은 railway.internal 이라 밖에서 안 붙는다
railway variables -p space-syntax -s postgres -e production --json

# 2. 덤프. pg_dump 17 이 PG16 이 모르는 SET transaction_timeout 을 넣으므로 걸러낸다
pg_dump --no-owner --no-privileges --no-tablespaces -t public.<표> ... "$LOCAL"   | grep -v '^SET transaction_timeout' > dump.sql

# 3. 넣기 전에 덤프에 DROP/TRUNCATE/DELETE 가 없는지 확인한다
grep -nE '^(DROP|TRUNCATE|DELETE)' dump.sql

# 4. 한 트랜잭션으로 적용 — 실패하면 전부 롤백된다
psql -v ON_ERROR_STOP=1 --single-transaction -d "$PROD" -f dump.sql

# 5. 로컬과 행 수를 대조하고, 라이브 엔드포인트가 값을 뱉는지 확인한다
```

**검증 결과.** 12개 전부 행 수 일치(불일치 0). `/api/undervalued-areas`가 실제 동·
점수를 반환하고, `/api/map-parcels`도 강남·금천 좌표에서 필지를 반환한다.

⚠️ **`parcel_polygons`는 서울 전역이 아니다** — 강남구(11680) 34,566 / 금천구(11545)
19,830 / 영등포구(11560) 529 뿐이다. 다른 구 좌표로 때리고 "비었다"고 판단하지 말 것.
처음에 시청 좌표(126.978, 37.5665)로 확인하다 헛짚었다.

### `cv_folds`는 옮기지 않는다 — 만든다

2.4GB / 640만 행이지만 **`land_prices` + `parcel_centroids`에서 계산되는 결과**이고,
그 둘은 이미 운영에 있다. 배정은 `sha256(salt|block) % n_folds`로 완전히 결정론적이라
(`cv_split.py` — "파이썬 `hash()`는 실행마다 바뀐다. 절대 쓰지 않는다") 같은 입력·같은
인자면 같은 표가 나온다.

단, `compute_cv_folds.py`가 `--salt`·`--spatial-folds`·`--horizons`·`--years`·`--sgg`·
`--spatial-mode`를 전부 CLI로 받는다. **로컬 표가 기본값으로 만들어졌다는 보장이
없으므로** 순서는: ① 운영에서 `--dry-run` ② 로컬 `cv_folds`와 행 수·`fold_id` 분포
대조 ③ 같으면 적재, 다르면 덤프로 옮긴다. 아직 안 했다.
