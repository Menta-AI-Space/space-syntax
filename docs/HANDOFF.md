# 인수인계 — 필지 단위 갭 계산 (2026-08-22 작성 · 8/26에 뜰 자리로 전환)

> ⚠️ **2026-08-26 이후 이 문서의 「저평가」는 제품 결론이 아니다.**
> 제품은 **B(뜰 자리 — 같은 계층 대비 초과상승 확률)** 단일 결론으로 간다. 저평가 계산은
> 유지하되 갭 지표는 **뜰 자리 모델의 입력 feature**로만 쓴다(반증 불가 + 감정평가법상
> 위험). 근거는 `docs/DECISIONS_LAND_MODEL.md` §10.
> 아래 본문에서 「저평가·고평가」가 **사용자 화면의 결론**으로 나오는 대목은 전부
> 「뜰 자리(오를 확률)」로 읽는다. 화면에 "싸다"로 나가면 안 되고 "오를 확률이 높다"로
> 나가야 한다. 내부 계산·통제 방법을 설명하는 대목은 그대로 유효하다.


새로 붙는 작업자(사람이든 다른 도구든)가 **대화 기록 없이** 이어받을 수 있게
정리한 문서다. 코드와 커밋 메시지에 이미 있는 내용은 반복하지 않고, **어디에
무엇이 있는지 · 왜 그렇게 정했는지 · 지금 막힌 곳이 어디인지**만 적는다.

배경 조사는 `LAND_VALUE_MODEL_RESEARCH.md`(1,400줄)가 정본이다. 그 문서의
"미확인" 항목 상당수가 2026-08-21~22 실측으로 풀렸고, 결과는 그 문서의
"실측 결과" 절에 반영돼 있다.

---

## 1. 지금 어디까지 왔나

### 확정된 방향

**분석 대상은 토지 가격이다** (소유자 결정 2026-08-21). 종속변수는 개별공시지가.
실거래는 지번 마스킹으로 PNU를 만들 수 없어 종속변수가 못 된다 — 집계 수준
검증에만 쓴다.

**저평가 판정은 2단이다.** 단위를 섞으면 안 된다:

| 단계 | 단위 | 답하는 질문 | 상태 |
|---|---|---|---|
| ① 탐색 | 행정동 424개 | 어느 동네를 볼까 | ✅ 계산·API·화면 완료 |
| ② 판정 | 필지 (PNU) | 그 안 어느 땅이 싼가 | 🟡 코드 완료, **실행 전** |

동은 답이 아니라 후보 구역이다. 동 중심점 500m 반경 평균은 같은 동 안 대로변과
이면도로의 차이를 지우는데, **그 차이가 공간구문론이 파는 신호다.** 화면에서
"저평가된 동을 찾았다"고 말하지 않는 이유다.

### 코드 위치

| 무엇 | 어디 |
|---|---|
| 동 단위 계산 | `backend/app/services/undervalued_areas.py` |
| **필지 단위 계산** | `backend/app/services/parcel_undervalued.py` |
| 용도지역 명칭 정규화 | `backend/app/services/land_use_zones.py` |
| 1단계 배치 — 세그먼트 구조 | `backend/tools/compute_segment_structure.py` |
| 2단계 배치 — 필지 판정 | `backend/tools/compute_parcel_undervalued.py` |
| 공시지가 적재 (CSV) | `backend/tools/ingest_land_prices.py` |
| 공시지가 적재 (API) | `backend/tools/ingest_land_prices_api.py` |
| 토지특성 적재 (API) | `backend/tools/ingest_parcel_characteristics.py` |
| API 확인용 프로브 | `backend/tools/probe_land_price_api.py` |
| 스키마 | `scripts/migrations/013_*.sql`, `014_*.sql` |
| 첫 화면 목업 | `design/*.dc.html` + `design/canvas.json` |
| 발표 덱 빌드 | `design/ppt/` (README 참고) |

---

## 2. 데이터 조달 — 전부 확정됐다

**하나의 V-World 키로 다 된다.** `.env`의 `VWORLD_API_KEY` + `VWORLD_DOMAIN`.

⚠️ **`domain` 요청변수가 없으면 `INCORRECT_KEY`로 거부된다.** 키가 틀린 게 아니라
발급 때 등록한 서비스 URL을 함께 보내야 한다. 서버측 호출이라 도메인 검사에 안
걸릴 줄 알았는데 아니었다 — 이것 때문에 진단을 한 바퀴 돌았다.

| 소스 | 엔드포인트 / 파일 | 주는 것 | 연도 |
|---|---|---|---|
| 개별공시지가 | `api.vworld.kr/ned/data/getIndvdLandPriceAttr` | PNU · 공시지가(원/㎡) · 표준지여부 · 특수지구분 | **2009~2026** |
| 토지특성 | `api.vworld.kr/ned/data/getLandCharacteristics` | **용도지역** · 도로접면 · 지목 · 면적 · 지세 · 형상 | **2015~2026** |

**용도지역 폴리곤을 따로 받을 필요가 없다.** 원래 국토부 용도지역지구도 WFS를
적재해 필지에 공간조인하려 했는데(`013`의 `land_use_zones`), 토지특성 API가
같은 것을 PNU로 묶어 주므로 그 경로가 통째로 사라졌다. `land_use_zones` 테이블은
지우지 않고 미사용으로 둔다.

두 API 모두 `pnu`에 **법정동코드 10자리**만 넣으면 그 동 전체가 온다
(`numOfRows` 최대 1000 + `pageNo` 페이징). 트래픽 999,999,999 / 일일 무제한.

**한 필지가 여러 행으로 온다** — 값은 같고 `stdrMt`가 `"01"`/`"1"`로만 다르다.
두 적재 스크립트 모두 `(pnu, base_year)`로 접는다.

### 이미 적재된 것 (DB 랩탑)

- 개별공시지가 CSV **887,699행 / base_year=2026** 적재 완료
- `parcel_centroids`와 PNU 조인 **99.86%** (886,452/887,699)
- 표준지 31,463개(3.5%) — 전 필지 데이터임이 확인된다
- ⚠️ CSV 파일 하나가 **한 해분만** 담는다. 패널을 만들려면 연도별 파일을 각각
  받거나 API 경로(`ingest_land_prices_api.py --years 2015..2026`)를 쓴다.

### 아직 안 된 것

- **토지특성 적재** — 용도지역이 없으면 계층을 못 나누고, 계층을 못 나누면
  주거지역 필지가 통째로 저평가로 뜬다. 이게 없으면 2단계가 성립하지 않는다.
- **세그먼트 구조 계산** — 한 번도 안 돌렸다. 아래 3절 참고.
- **필지 판정 배치** — 위 둘이 선행이다.

---

## 3. 다음에 할 일 (순서대로)

적재가 끝난 로컬 DB(PostGIS, 전체 11.3GB)가 있는 랩탑에서만 된다. "9.2GB"는
틀린 숫자였다 — 실측 근거와 내역은 CLAUDE.md "로컬 백엔드 실행이 안 되면" 절에
있다. 도로망 자체는 무겁지 않다(`roads_clean` 92MB).

```bash
git pull
psql -h localhost -p 5433 -U spacesyntax -d spacesyntax -f scripts/migrations/013_parcel_undervalued.sql
psql -h localhost -p 5433 -U spacesyntax -d spacesyntax -f scripts/migrations/014_parcel_characteristics.sql
```

```bash
# 토지특성 — 한 동으로 먼저 확인
cd backend && .venv/Scripts/python -m tools.ingest_parcel_characteristics --ldong 1111017700 --years 2026 --dry-run
# 통과하면 서울 전체 (약 470 법정동, 수십 분)
cd backend && .venv/Scripts/python -m tools.ingest_parcel_characteristics --years 2026
```

```bash
# 세그먼트 구조 — 반드시 --limit로 먼저 (한 번도 안 돌린 코드다)
cd backend && .venv/Scripts/python -m tools.compute_segment_structure --limit 5 --dry-run
```

**여기가 가장 위험한 단계다.** 서울을 800m 타일 약 950개로 나눠 각각 NetworkX
매개중심성을 돌린다. 타일당 5~15초면 전체 **2~4시간**. 첫 실행은 거의 항상
무언가 걸리므로 `--limit 5`로 확인한 뒤 전체를 건다.

```bash
# 구 하나만 — 타일 수십 개, 십수 분. 발표가 급하면 이쪽.
cd backend && .venv/Scripts/python -m tools.compute_segment_structure --sgg 11440
# 또는 서울 전체 — 타일 약 950개, 2~4시간
cd backend && .venv/Scripts/python -m tools.compute_segment_structure
```

```bash
# 필지 판정 — 한 구로 먼저
cd backend && .venv/Scripts/python -m tools.compute_parcel_undervalued --sgg 11440 --dry-run
```

> **발표가 급하면 서울 전체를 기다리지 말 것.** 한 구만 나와도 "마포구 N필지를
> 계산해 상위 20개를 뽑았다"가 "전체 준비 중"보다 훨씬 강하다 — 데모가 아니라
> 진짜 숫자이기 때문이다.

---

## 4. 열려 있는 결정 (사람이 정해야 함)

1. **원본 5번 슬라이드의 "미래가치를 예측합니다"** — 근거가 없다. 준공일 데이터가
   없어 개통 전후 검증을 못 했고 연구 문서가 인과 주장을 금지한다. "구조 변화량을
   계산합니다"가 방어 가능한 표현이다. 원본 슬라이드라 임의로 고치지 않았다.
2. **첫 화면 예시 3줄을 그대로 둘지** — 탭(저평가 부지·도면 분석)과 예시가
   기능 두 개를 중복해서 보여준다. 처음 온 사람에겐 예시가, 재방문자에겐 탭이
   맞아 역할이 다르지만, 화면이 반복돼 보이는 것도 사실이다.
3. **필지 결과 화면** — 동을 클릭해 그 안 필지로 내려가는 2단계 UI가 아직 없다.
   `UndervaluedPanel.tsx`에 "준비 중"으로 적어 두었다.

---

## 5. 조심할 것 — 반복해서 물린 함정들

- **용도지역을 통제하지 않으면 결과가 틀린다.** 상업지역은 애초에 간선도로변에
  지정되고 용적률이 높아 그 자체로 비싸다. 섞어 비교하면 "구조 좋음"과 "값 비쌈"의
  공통 원인이 전부 구조의 몫으로 계산되어 **주거지역이 통째로 저평가로 뜬다.**
  `test_parcel_undervalued.py`에 이걸 직접 검증하는 테스트가 있다.
- **공시지가는 시세가 아니다.** 나지상정 평가액이고 시세반영률이 100%가 아니다.
  결과는 "시세 대비 싸다"가 아니라 "같은 조건 필지들의 공시지가 수준 대비 낮다"이며
  **금액이 아니라 %로만** 말한다.
- **순환성** — 토지가격비준표 22개 항목에 도로접면·도로거리가 들어 있다. 구조와
  공시지가의 상관 일부는 행정 산식의 재현이다. **높은 R²는 성과가 아니다.**
  단 도로접면은 실측 결측이 63%라 이 필드만으로는 통제가 약하다 — PostGIS로
  최근접 도로 폭을 재는 경로를 병행해야 한다.
- **raw Choice는 타일 간 비교가 불가능하다.** `nx.betweenness_centrality`를
  `normalized=False`로 부르므로 값이 그래프 크기에 비례한다. 반드시 타일 내
  백분위(`structure_pct_in_tile`)를 쓴다.
- **테스트가 통과해도 적재는 죽을 수 있다.** 공시지가 CSV 적재가 파서 테스트 8개를
  전부 통과하면서도 첫 배치에서 항상 죽고 있었다 — 테스트가 SQL을 건드리지
  않았기 때문이다. 적재 스크립트는 **실제 파일로 한 번 돌려 봐야** 검증된 것이다.

---

## 6. 비밀값

`.env`(gitignore)에 있다. 커밋·대화·로그에 값을 남기지 않는다.

- `VWORLD_API_KEY` / `VWORLD_DOMAIN` — 개별공시지가·토지특성
- `DATA_GO_API_KEY` — RTMS 실거래, 소상공인 점포
- `KAKAO_REST_KEY` — 지오코딩 (이 랩탑은 아직 예시값)

httpx가 INFO 로그에 요청 URL을 통째로 찍는다 — 키가 거기 들어 있다. 적재
스크립트는 `logging.getLogger("httpx").setLevel(WARNING)`으로 막아 두었다.
새 스크립트를 쓸 때도 같이 넣을 것.
