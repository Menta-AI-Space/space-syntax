# 토지가격 × Space Syntax 모델 — 방법론 조사

- **조사일자**: 2026-08-21
- **이 문서는 리서치 노트이며 코드 변경은 포함하지 않는다.**
- 목적: "현재 Integration ↔ 현재 토지가격" 횡단면 관계를 추정하고, 예정 도로가 만들
  Integration delta를 그 관계에 대입해 가격 변화를 예측한다 — 이 설계가 **정당한가**,
  정당하지 않다면 **어떻게 보완하고 어떻게 표기해야 정직한가**를 판정한다.

> **표기 규칙**: 1차 출처(논문 원문·공식 데이터셋 페이지·법령·기관 문서)에서 직접
> 확인한 것만 단정한다. 초록만 본 것은 **"원문 미확인, 초록 기준"**으로 적었고,
> 확인하지 못한 수치는 아예 쓰지 않고 **미확인**으로 남겼다.

---

## 요약 — 권고 결론 (한 문단)

**종속변수는 개별공시지가로 학습하고 토지 실거래로 검증하는 2단 설계가 유일하게
성립하지만, 공시지가를 쓰는 순간 우리가 추정하는 것은 "시장이 얼마 더 낼까"가 아니라
"평가사가 얼마로 매길까"로 바뀐다** — 개별공시지가는 비교표준지 가격에
**토지가격비준표의 가격배율**을 곱해 산정하고, 그 비준표 항목에 **도로접면**과
**도로거리**가 들어 있어([한국부동산원 비준표 열람](https://sct.reb.or.kr/summary/landSummary.do),
[관악구 도로접면구분표](https://www.gwanak.go.kr/site/gwanak/standard/Standard_Road_Pop.do)),
Integration↔공시지가 회귀는 상당 부분 **행정 산식을 되풀이 학습하는 순환**이다.
토지 실거래는 **종속변수가 될 수 없다** — 지번이 법으로 마스킹돼 필지에 붙지 않고,
서울 토지거래의 **94%가 대지(건물 부속토지)**여서 그 금액은 건물+토지 가격이며,
순수토지는 연 약 1.3만 건으로 **전체 필지의 약 1.5%**뿐이다(1.4).
모형은 **로그-선형 헤도닉 + 행정동(또는 격자) 공간고정효과 + 공간 HAC/클러스터
표준오차**로 간다 — SAR/SEM은 쓰지 않는다(Gibbons & Overman 2012는 공간상관을
"표준오차 문제"로 규정하고, Bourassa et al. 2007은 부동산 헤도닉에서 **OLS+서브마켓
더미가 SAR/SEM보다 예측이 좋았다**고 보고한다). Integration은 **반경을 하나씩 넣어
비교**한다 — 유일하게 확보한 반경 실증(Xiao 2013/2016 난징, 반경 10개 동시 투입)에서
**Choice의 부호가 국소 +0.083에서 8km −0.416으로 뒤집혔고**, 저자 스스로 반경 선택이
임의적이고 반경 간 상관이 높아 해석이 어렵다고 인정했다(2.2). counterfactual은
**fitted_after − fitted_before**(잔차 상쇄)로만 계산해야 한다 — 우리가 복제 대상으로
삼은 선례(조정훈·문태헌 2016)가 정확히 이 지점에서 틀려 예측 필지의 4/15가 "도로를
새로 냈는데 지가가 하락"하는 결과를 냈다(2.1). 결과를 **"예상 상승률"로 표기하면 안
되고, "자본화 상한"으로도 표기할 수 없다** — Kuminoff & Pope (2014)는 자본화가 WTP를
**과소**평가한 사례를 보였고, 우리 계수는 상향 편의(미관측 입지질)와 감쇠
편의(Integration은 구성된 측정오차 변수)를 동시에 받아 **편의의 부호조차 사전에 알
수 없다**(2.4). 정직한 라벨은 **"길 구조가 이 정도인 다른 필지들의 현재 지가
수준"(벤치마크) + "인과 추정치가 아니며 개통 전후 검증을 하지 않았다"** 명시다(3.4).
그리고 이 조사의 가장 값 있는 발견: **우리는 횡단면에 갇혀 있지 않다.** 도로망이
OSM(Geofabrik)이라 **2015년 이후 연도별 스냅샷**을 받을 수 있고 공시지가는 전 필지 ×
매년이므로, **필지 고정효과 1차 차분 패널**이 가능하다(2.7) — 문헌이 지지하는 형태는
"예측"이 아니라 **"예측 + 검증"**(Ahlfeldt 2013)이며 이것이 그 검증 경로다.
v1 최소 범위는 **전 서울 도로 세그먼트 Integration 배치 1회 + 필지-접면도로 결합 +
비준표 통제변수 + 로그선형 헤도닉 1개 + 공간블록 검증 + 벤치마크 범위 표기**이며,
착수 전 실측이 필요한 항목은 문서 끝에 목록으로 정리했다. **가장 값싼 첫 실험은
구 하나에서 "비준표 통제변수를 넣어도 Integration이 유의한가"를 확인하는 것이다 —
유의하지 않으면 이 방향을 접어야 한다.**

---

# Part 1 — 종속변수: 개별공시지가 vs 토지 실거래

## 1.1 결론을 먼저

| | 개별공시지가 | 토지 실거래 |
|---|---|---|
| 표본 | 전수 × 매년 | 희소 (Part 1.4) |
| 좌표 결합 | **PNU로 정확 결합** | 지번 지오코딩 |
| 시장가격인가 | ❌ 행정가격 | ✅ |
| Integration과의 관계 | **⚠️ 비준표 순환성** | 순환 없음 |
| v1 역할 | **학습 (main)** | **검증 (holdout)** |

**판정: 개별공시지가로 학습하고, 토지 실거래로 외부 검증한다. 단 화면 문구는 공시지가
기준임을 명시한다.** 근거는 1.2(순환성)와 1.3(현실화율)이 둘 다 공시지가의 한계를
가리키지만, 실거래만으로는 필지 단위 횡단면을 만들 표본이 안 되기 때문이다(1.4).

## 1.2 ⚠️ 가장 중요한 발견 — 공시지가에는 이미 "도로"가 들어 있다 (순환성)

개별공시지가는 시장을 관측해 얻는 값이 아니다. **비교표준지의 가격에
토지가격비준표에서 뽑은 가격배율을 곱해 산정한다.** 비준표는 한국부동산원이 작성해
공식 열람 서비스로 공개한다.

- 공식 열람: <https://sct.reb.or.kr/summary/landSummary.do> (한국부동산원 토지·주택가격비준표)
- 작성단위 정보 데이터셋: <https://www.data.go.kr/data/15120063/fileData.do>
  (제공 한국부동산원, XLS, 갱신 수시, **이용허락범위 제한 없음**, 관리 AX데이터전략부)

그 비준표 항목에 **도로접면**과 **도로거리**가 포함된다. 도로접면은 접면 도로의
**폭과 접면 수**로 12구분되며, 폭 기준은 아래와 같다
(출처: [관악구 도로접면구분표](https://www.gwanak.go.kr/site/gwanak/standard/Standard_Road_Pop.do) — 공식 지자체 페이지):

- 광대로 **25m 이상** / 중로 **12~25m** / 소로 **8~12m** / 세로 **8m 미만**
- 12구분: 광대한면·광대소각·광대세각·중로한면·중로각지·소로한면·소로각지·
  세로(가)·세각(가)·세로(불)·세각(불)·**맹지**

> 비준표 전체 항목이 "22개"이고 그 순서가 ①용도지역 … ⑯도로접면 ⑰도로거리 …
> ㉒묘지라는 서술은 2차 출처(중개업 블로그·카페)에서만 확인됐다 → **항목 수와 순서는
> 미확인.** 다만 **도로접면·도로거리가 비준표 항목에 있다는 사실**은 위 공식
> 열람 서비스와 지자체 구분표로 뒷받침된다.

**이것이 설계에 미치는 영향 — 세 가지, 모두 중대하다.**

1. **적합도가 좋게 나오는 것은 성공이 아니다.** Integration은 도로망 위상에서
   계산되고, 공시지가는 도로 폭·접면 수·도로거리를 곱셈 배율로 이미 반영한다. 두
   변수는 같은 원인(도로 계층)을 공유하므로 R²가 높게 나오는 것은 **시장 발견이
   아니라 산식 재현**일 수 있다. 높은 R²를 근거로 "우리 모델이 지가를 설명한다"고
   말하면 안 된다.
2. **"도로가 나면 공시지가가 얼마 오르나"는 회귀로 추정할 문제가 아니다.** 신설
   도로로 접면 등급이 세로(불) → 소로한면으로 바뀌면 배율이 바뀌고 공시지가가
   바뀐다. 이건 **비준표에서 조회하면 나오는 결정론적 값**이다. 우리 모형이 이걸
   맞히는 것은 의미가 있지만 **차별점은 아니다** — 그리고 조회가 회귀보다 정확하다.
3. **차별점은 오히려 "비준표가 못 잡는 것"에 있다.** 비준표는 접면 도로의 **폭**을
   보지만, Integration은 **그 도로가 도시 전체 망에서 어디에 놓였는지**를 본다. 폭
   8m 세로가 두 개 있어도 하나는 간선으로 이어지고 하나는 막다른 골목이면
   Integration이 다르다. **비준표 항목(도로접면·도로거리)을 통제변수로 넣고 그
   위에서 Integration이 여전히 유의한지**를 보는 것이 이 프로젝트가 물어야 하는
   진짜 질문이며, 동시에 순환성 방어이기도 하다.

→ **모형 사양에 반영**: 도로접면 등급·도로폭·도로거리를 **통제변수로 반드시 포함**
하고, Integration의 계수는 **그것들을 통제한 뒤의 증분 설명력**으로만 해석한다
(Part 3.2).

## 1.3 공시지가는 시장가격이 아니다 — 그리고 그것이 꼭 나쁘지만은 않다

### 산정 방식 (국토교통부 1차 확인)

국토교통부 표준지공시지가 안내(부동산평가과,
<https://www.molit.go.kr/USR/policyTarget/dtl.jsp?idx=530>)에서 확인한 규칙:

- **적정가격** = "통상적인 시장에서 정상적인 거래가 이루어지는 경우 성립될 가능성이
  가장 높다고 인정되는 가격" — 관측된 거래가가 아니라 **평가사가 판정하는 가상 가격**
  이며, 국토교통부장관이 중앙부동산평가위원회 심의를 거쳐 결정한다.
- **✅ 나지상정 평가** — "건물기타의 정착물이 있거나 지상권 등 … 설정되어 있는 경우에는
  그 정착물이 없는 토지의 나지상태를 상정하여 평가합니다."
  **→ 이것은 우리에게 유리한 성질이다.** 우리는 "토지 가격"을 모델링하기로 확정했고,
  공시지가는 정의상 **건물을 제거한 순수 토지 가치**다. 실거래가는 이걸 절대 줄 수 없다
  (1.4의 94% 문제).
- **실제용도 기준평가** — "공부상의 지목에도 불구하고 … 실제지목 및 이용상황을 기준으로".
  즉 기록상 지목은 평가 근거가 아니다(통제변수로 쓸 때 주의).
- **개별공시지가는 독립 감정평가가 아니다** — "표준지공시지가를 기준으로 개별토지의
  토지특성 차이에 따른 **토지가격비준표상의 가격배율**을 곱하여 산정한 후 감정평가업자의
  검증과 부동산평가위원회의 심의 등을 거쳐 결정·공시". → **1.2의 순환성 근거가
  국토부 문서로 확정된다.**
- 법적 성격이 명시적으로 행정용이다 — "행정목적을 위한 지가산정의 기준"이며
  조세·부담금 등 **61개 분야**의 부과 기준.

### 현실화율 (토지)

- **토지(표준지) 시세반영률 65.5%** — 표준주택 53.6% / 공동주택 69%와 구분된다.
  ⚠️ **이 수치는 언론 보도 기준이며 `.go.kr` 1차 페이지에서 문장을 확보하지 못했다**
  (국토부 보도자료 본문이 HWP/PDF·canvas 렌더링). 출처:
  <https://www.newspim.com/news/view/20241218000256>
- 동결의 근거는 1차 확인됐다: 「'25년 부동산 가격 공시를 위한 현실화 계획 수정방안」,
  국무회의 2024-11-19, 국토부 부동산평가과 — "'25년 공시가격 시세반영률은 금년과 같게
  동결" <https://www.molit.go.kr/USR/NEWS/m_71/dtl.jsp?id=95090384>
- 2020년 현실화 계획의 토지 목표는 **2035년까지 90%**였고, 계획 자체가 전면 재검토에
  들어갔으며 정부 브리핑이 "주택과 토지의 현실화율 격차도 벌어지는 등 공시가격의
  공정성이 저해되는 구조"라고 적었다 —
  <https://www.korea.kr/briefing/policyBriefingView.do?newsId=156600849>
- 최근 변동률: 2026년 표준지 +3.35% (<https://www.korea.kr/briefing/pressReleaseView.do?newsId=156735488>),
  2025년 표준지 전국 +2.93% / 서울 +3.92%.

**모델 설계 함의**: 현실화율이 **65.5% 수준에서 동결**돼 있다는 것은, 연도 간
공시지가 변화가 "시장 변화"와 "현실화율 정책 변화"를 섞어 담는다는 뜻이다. 2.7의
패널 설계에서 **연도 고정효과가 필수**인 이유가 여기다 — 전국 공통의 현실화율·변동률
정책 효과를 연도 더미가 흡수한다. 또한 현실화율이 대략 일정하다면 **수준은 틀려도
비율 변화는 보존**되므로, 화면에 **%로만 말하고 원 단위 금액을 말하지 않는 것**이
정직하다(3.4).

### 취득 경로 — 여기서 문제가 생겼다

| 경로 | 상태 |
|---|---|
| data.go.kr **15029071** / **15124014** → 실제로는 **V-World NED API** | ✅ **2026-08-21 실측 완료.** 아래 "실측 결과" 참조. API 유형이 LINK라 data.go.kr이 중계하지 않고 브이월드로 넘긴다 — **키도 브이월드 것**이다(data.go.kr 키가 아니다). 요청주소 `https://api.vworld.kr/ned/data/getIndvdLandPriceAttr` |
| data.go.kr **15004246** 표준지공시지가 | CSV/XML/JSON, **연간**, **제한 없음**, 승인 불필요. **42개 필드** — `토지대장번호(PNU), 지목, 면적, 용도지역1/2, 용도지구1, 이용상황, 주위환경, 지세, 형상, 방위, 도로거리, 도로교통, 철도거리, 하수거리, 전년지가, 공시지가`. 전국 표준지 **60만 필지(2025)** / 공시대상 **3,559만 필지** |
| 서울 열린데이터광장 **OA-1180** 서울시 개별공시지가 정보 | 🔴 **2026-08-18 서비스 종료.** 종료 전에는 FILE + OpenAPI, **공공누리 1유형(상업 OK)**, **1990~2026 37년치 파일**이었다. <https://data.seoul.go.kr/dataList/OA-1180/F/1/datasetView.do> |
| realtyprice.kr (부동산공시가격알리미) | **벌크 다운로드 없음.** 개별지는 일사편리 개별 조회로 넘긴다. 시계열 안내 없음. <https://www.realtyprice.kr/notice/gsindividual/siteLink.htm> |

### ✅ 실측 결과 (2026-08-21) — 취득 경로 확정

브이월드 개발키를 발급해 라이브로 호출했다. 조사 시점의 "미확인" 항목이 전부 풀렸다.
확인 도구는 `backend/tools/probe_land_price_api.py`, 적재는
`backend/tools/ingest_land_prices_api.py`.

| 항목 | 조사 시점 | 실측 |
|---|---|---|
| PNU | 미확인 | ✅ `pnu` 필드로 온다 → `parcel_centroids` 조인 가능, **필지 단위 성립** |
| 표준지/전 필지 | 미확인 | ✅ **전 필지.** 종로구 평동 2024년 378건 중 표준지(`stdLandAt=Y`) 12건, 개별 366건 |
| 시간범위 "-" | 미확인 | ✅ 과거 연도 제공. **하한 2009년** (2008은 totalCount=0) → **2009~2026, 18년치** |
| 벌크 가능성 | 미확인 | ✅ `pnu`에 **법정동코드 10자리**만 넣으면 그 동 전체가 온다 (numOfRows 최대 1000, pageNo 페이징) |
| 필드 목록 | 미확인 | ✅ 12개 필드가 `land_prices` 스키마(012)와 **1:1 대응**. 가격은 `pblntfPclnd`(원/㎡) |
| 트래픽 | 미확인 | 999,999,999 / 일일제한 무제한 |

⚠️ **`domain` 요청변수가 필수다.** 빠지면 `INCORRECT_KEY`("인증키 정보가 올바르지
않습니다")로 거부된다 — 키가 틀린 게 아니라 발급 때 등록한 서비스 URL을 함께
보내야 한다. 서버측 호출이라 도메인 검사에 안 걸릴 줄 알았는데 아니었다.
`.env`의 `VWORLD_API_KEY` + `VWORLD_DOMAIN` 둘 다 필요하다.

**37년치가 18년치로 줄어든 것의 영향은 없다.** 2.7의 필지 고정효과 패널은
연도별 OSM 도로망 스냅샷과 짝지어야 하는데 그쪽이 2015년부터다. 따라서 교집합은
공시지가 하한과 무관하게 **2015~2026 (12년)** 으로 동일하다. 서울시 데이터전략과에
37년치 아카이브를 문의하는 것은 여전히 값이 있지만 **더 이상 선행 조건이 아니다**.

> 🔴 **이 조사에서 가장 나쁜 소식**: 2.7의 패널 설계에 가장 맞는 자산 —
> **서울 개별공시지가 1990~2026년 37년치 파일 아카이브, 공공누리 1유형** — 이
> 조사 **사흘 전(2026-08-18)에 서비스 종료**됐다. 우연히도
> `DEVELOPMENT_INTELLIGENCE_SOURCES.md` 1.2가 기록한 OA-2253(정비사업) 종료와 같은
> 시기다. **서울시 데이터전략과에 이전/제공신청 가능 여부를 사람이 문의하는 것이
> 지금 당장 가장 값 있는 후속 조치다** — 공공누리 1유형이었으므로 우리 용도가 원래
> 허용 범위였다.

### 필지 수

- 전국 **39,632천 필지 / 100,449.4㎢** (지적통계 2024 기준, 국토정보정책관
  공간정보제도과) <https://stat.molit.go.kr/portal/cate/statMetaView.do?hRsId=24>
- 전국 공시대상 토지 **3,559만 필지**(2025)
  <https://www.molit.go.kr/USR/policyData/m_34681/dtl.jsp?id=4807>
- **서울 개별공시지가 대상 861,300 필지** (2025-01-01 기준) — ⚠️ **언론 보도 기준이며
  `.go.kr` 1차 확인 실패** <https://www.intn.co.kr/news/articleView.html?idxno=2043307>
- **서울 필지 수를 1차 출처로 확정하는 경로는 있다**:
  `국토교통부_지적기본통계집계` (**89,081행**, 컬럼 `통계 기준일자, 행정구역, 대장
  구분, 축척, 소유구분, 지목, 지번수, 면적`, CSV, **제한 없음**)에서 서울 `지번수`를
  합산하면 된다. <https://www.data.go.kr/data/15063997/fileData.do>
  → **착수 전 실측 항목**(3.6). KOSIS `DT_MLTM_2300`과 서울 열린데이터광장
  `토지현황`은 **면적(㎡)만** 주고 필지수를 주지 않는다.

## 1.4 토지 실거래 — 표본이 되는가: **아니다** (세 가지 독립적 이유로)

데이터셋: **국토교통부_토지 매매 실거래가 자료**
<https://www.data.go.kr/data/15126466/openapi.do>
REST/XML, 무료, **이용허락범위 제한 없음**, **심의유형 자동승인**(공시지가 API보다
쉽다), 개발계정 1,000 calls/day. 쿼리 키는 법정동코드 5자리 + 계약년월 6자리.
필드에 지번·면적·거래금액·계약년월·용도지역·지목이 있다.

**이유 1 — 지번이 마스킹된다 (치명적).** 데이터셋 명세에 명시:
*"공개내용 중 개인정보보호를 위해 토지의 지번정보는 일부만 제공됩니다."*
→ **PNU를 만들 수 없고, 따라서 `parcel_centroids`에 조인할 수 없고, 따라서 필지
단위 Integration을 붙일 수 없다.** 달성 가능한 최고 해상도는
**법정동 × 용도지역 × 지목**이다. 우리 저장소의 `_build_pnu()`가 마스킹 지번에
`None`을 반환하는 것과 같은 문제다(1.5).

**이유 2 — 표본이 너무 적다.** 서울시기본통계 「토지거래 현황」(1997~2025, 자치구 ×
용도지역 × 지목, 항목 필지수+면적, 출처 서울특별시기본통계)
<https://stat.eseoul.go.kr/statHtml/statHtml.do?orgId=201&tblId=DT_201004_O090014&conn_path=I2>

- **2025년 서울 합계 222,084 필지 / 11,818천㎡**
- 지목별: **대지 208,850 (94.0%)**, 기타 7,431, 임야 2,349, 공장용지 1,738, 전 1,301, 답 415
- 용도지역별: 주거 178,249 / 상업 29,223 / 공업 11,826 / 녹지 317
- 참고(전국 순수토지, e-나라지표 「토지거래동향」, 작성기관 국토교통부)
  <https://www.index.go.kr/unity/potal/main/EachDtlPageDetail.do?idx_cd=1243>:
  2024년 전체토지 1,876,000 필지 / 순수토지 **661,000 필지**
- ⚠️ **서울의 순수토지 절대 건수는 모든 1차 출처에서 미확인**이다. 국토부는 시도별
  순수토지를 **증감률(%)로만** 보도자료 본문에 싣고 절대값 표는 HWP/PDF 첨부에만 둔다.
  한국부동산원 R-ONE의 토지거래현황에는 **순수토지 구분 자체가 없다**.

**이유 3 — 94%가 "토지"가 아니다 (구성 타당도).** e-나라지표가 순수토지를
"건축물 부속토지를 제외한 순수토지"로 정의한다. 즉 아파트 한 채가 팔리면 그 아래
토지가 **토지 거래 1건으로 집계된다.** 서울의 지목별 구성이 **대지 94.0%**라는 것은
서울 헤드라인 222,084건이 압도적으로 **건물 거래에 토지 이름표를 붙인 것**이며,
그 `거래금액`은 **건물+토지 가격**이라는 뜻이다. 이것을 토지가격 종속변수로 쓰면
우리는 지가가 아니라 **건물 감가와 층수를 모델링하게 된다.**

**규모 판정(파생 계산, 명시)**: 서울 2025년 비(非)대지 = 222,084 − 208,850 =
**약 13,234 필지**. 이것이 서울 순수토지의 **상한**이다(일부 대지는 실제 나지이고
일부 비대지에는 구조물이 있으므로 정확치 아님). 861,300 필지 대비
**연 약 1.5% 커버리지**다. 10년을 모아도 약 15%이며, **그 전부가 지번 마스킹으로
필지에 붙지 않는다.**

### 1.4 판정

**토지 실거래는 종속변수가 될 수 없다. 검증용으로만 쓴다** — 그것도 필지 단위가
아니라 **법정동 × 용도지역** 집계 수준에서, **수준이 아니라 순위·방향**만 본다(3.3).
공시지가 잔차가 "실거래가 공시지가를 웃도는 곳"을 맞히는지 보는 용도이며, 이것이
바로 "뜰 자리"가 주장하려는 것과 같은 명제다.

### 종합 판정 (Part 1)

**개별공시지가로 학습, 토지 실거래로 (집계 수준) 검증.** 단 세 가지를 문서와 화면에
동시에 적어야 정직하다: (a) 나지상정이라 **토지 가치로는 오히려 적절**하고(1.3),
(b) 시세반영률 **65.5%**라 **금액이 아니라 %만** 말해야 하며(1.3),
(c) 비준표 순환성 때문에 **높은 R²는 성과가 아니다**(1.2).
그리고 무엇을 모델링하는지 이름을 바꿔야 한다: 우리는 시장가격을 예측하는 것이
아니라 **공시지가와 시장가격의 격차(잔차)에서 신호를 찾는 것**이다.

## 1.5 우리 저장소의 현재 상태 (실측)

- `parcel_centroids(pnu, geom_5179, src, ingested_at)` — 연속지적도(LSMD_CONT_LDREG)
  에서 필지 대표점을 적재하며 **키가 PNU**다
  (`backend/tools/ingest_cadastral.py`). 개별공시지가 표준데이터의 고유번호(PNU)와
  **바로 조인된다** — 이 프로젝트가 공시지가를 쓰기에 유리한 결정적 이유.
- `real_estate_trades` — 현재 적재 대상은 **`commercial`(상업업무용) + `officetel`
  뿐**이다(`ingest_real_estate_trades.py` 기본값 `--types commercial officetel`).
  **토지 매매는 아직 적재되어 있지 않다** → 토지 실거래를 쓰려면 RTMS 토지
  엔드포인트용 ingest를 새로 만들어야 한다. 다만 `land_use`(용도지역)·`jibun`·
  `geo_precision='parcel'` 컬럼이 이미 있어 스키마 확장은 가볍다.
- 즉 **"공시지가로 학습"은 기존 자산으로 거의 바로 되고, "실거래로 검증"은 신규
  ingest 1개가 선행 조건이다.** v1 순서가 여기서 정해진다.

---

# Part 2 — 모델 방법론

## 2.1 직접 선례 정밀 해부 — 조정훈·문태헌 (2016)

**원문 전문 확보 · 텍스트 추출 성공** (PDF 10쪽 전문):
<https://doi.org/10.11108/kagis.2016.19.1.043> /
PDF <https://www.koreascience.or.kr/article/JAKO201612461133081.pdf>
(한국지리정보학회지 19(1):43-52, 2016. 접수 2016-01-07, 게재 2016-03-31)

### 무엇을 어떻게 했나

| 항목 | 내용 | 우리와의 차이 |
|---|---|---|
| 대상지 | **진주시** 7개 행정동 | 서울 아님. 지방 중소도시 |
| 종속변수 | **표준지 공시지가** (원/㎡), **수준** | 개별공시지가 아님. 변화율 아님 |
| 표본 | 표준지 필지. 표 1의 동별 수 119·82·61·120·58·43 (합 약 483) | 표 1은 동 이름 9개 / 숫자 6개로 추출 정렬이 어긋나 **동-표본 대응은 미확인** |
| 독립변수 | Connectivity, Control, Integration **3개뿐** | **통제변수 전무** |
| 공간 표현 | **축선도(axial map)** | 우리는 segment-angular dual graph |
| 반경 | 논문에 **보고되지 않음**. 통합도를 "그 공간에서 다른 **모든** 공간에 접근하기 위해 횡단해야 하는 공간수의 평균"으로 정의 → **전역(Rn)으로 추정, 확정은 미확인** | 우리는 500m~1km **국소** |
| 상관분석 | 동별 상관계수 0.50~0.82, 유의수준 **p<0.1** | 유의수준이 느슨하다 |
| 회귀 | Integration **단일변수 단순회귀**, "설명력 **0.57**" | 계수·절편·함수형태(로그 여부) **미보고 → 미확인** |

핵심 결과는 초록에 알려진 대로 **통합도가 공시지가와 가장 연관성이 높고, 통합도가
높을수록 공시지가가 상승**이다. Connectivity·Control은 동에 따라 유의/비유의가
갈렸고(표 2에 ○/X 혼재), Integration만 계획적 정비지역·자연발생지역 양쪽에서
공통으로 유의했다.

### "신설 도로 시뮬레이션"의 정확한 절차

우리가 복제 대상으로 삼은 부분이므로 그대로 옮긴다.

1. 회귀식을 **학습에 쓰지 않은 다른 지역**(진주 강남동, 주거환경개선사업 2단계
   추진 지역)에 적용한다.
2. **연구진이 임의로 도로 5개를 개설**한다 — "공간구조상 도로개설이 가장 필요하다고
   판단되는 곳"에 연구자 판단으로 그린 가상 도로다.
3. 축선도를 다시 그려 **통합도를 재산정**한다.
4. 각 표준지에 대해 **그 필지와 접한 도로의 통합도**를 회귀식에 대입해 예측 지가를
   얻는다. (필지 자체가 아니라 **접면 도로**가 매개인 점에 주목 — 우리 설계와 같다.)
5. 15개 표준지에 대해 변화율을 표 3으로 제시. 개별 변화율 **-25.8% ~ +71.2%**,
   **평균 +11.8%**.

### ⚠️ 이 논문의 치명적 결함 — 우리가 반드시 피해야 하는 것

표 3을 보면 도로를 **새로 냈는데도** 15개 중 4개 필지의 예측 지가가 **하락**한다
(-25.8%, -20.6%, -17.7%, -8.2%). 도로를 추가하면 통합도는 오르거나 최소한 안 내려가는데
왜 가격이 내려가나? 표 3의 수치를 보면 이유가 분명하다:

> **변화율 = (예측지가 − 실제 현재지가) / 실제 현재지가**

즉 **fitted_after 를 actual_before 와 비교했다.** 이렇게 하면 변화율에
"통합도가 오른 효과"와 "그 필지의 회귀 잔차"가 **섞인다.** 회귀선보다 실제 지가가
높은 필지(잔차 +)는 도로를 내도 하락으로 나오고, 낮은 필지는 과대 상승으로 나온다.
실제로 하락한 필지 4개는 전부 **노선상가**로, 저자들도 "노선상가 지역이고 주도로에
인접해 … 토지이용적 요소가 지나치게 많이 반영되어 기존의 지가가 높게 평가되었기
때문"이라고 설명한다 — **이건 통제변수 누락(omitted variable)이 만든 잔차를 "변화"로
오독한 것이다.**

**→ 우리 설계의 하드 룰**: counterfactual은 반드시
**Δ = f(Integration_after) − f(Integration_before)** 로 계산한다. 같은 필지의 잔차가
양쪽에서 상쇄되므로, 부호가 뒤집히는 이 오류가 구조적으로 불가능해진다. 실제 가격에
곱할 때는 **비율로만** 적용한다(`price_after = price_actual × (1 + Δ상대변화)`).
이 한 줄이 이 문헌 조사에서 얻은 가장 실무적인 소득이다.

### 저자들이 명시한 한계 (원문 결론부)

1. 토지이용 요소를 반영하지 못했다 — 노선상업지역 필지에서 오차가 크다.
2. **표준지 필지 수가 적거나, 지도에 나타나지 않는 골목길이 많은 지역에는 적용
   한계**가 있다. ← 서울 구도심 골목에 그대로 해당한다.
3. 필지 단위 마이크로 수준이 아니라 **지구 단위 매크로 수준**이다. 저자들은 필지별
   정밀 예측을 주장하지 않는다.

**총평**: 방향(Integration→지가, 회귀식에 counterfactual 대입)은 우리와 동일하고
**선례로서 유효하다.** 그러나 표본 483개·통제변수 0개·p<0.1·전역반경·단순회귀이며
변화율 계산이 틀렸다. **복제 대상이 아니라 "출발점 + 오류 목록"으로 써야 한다.**

## 2.2 다른 실증 연구 비교표

| # | 연구 | 대상지 | N | 종속변수(출처) | 지표 · 반경 | 모형 | 설명력 | 통제변수 | 공간자기상관 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 조정훈·문태헌 2016 (원문 확보) | 진주시 7동 | 약 483 표준지 | **표준지 공시지가** 수준 | Integration·Control·Connectivity, **축선·전역(추정)** | 단순회귀 (Integration 단독) | **R²=0.57** | **없음** | **처리 없음** |
| 2 | Abdulla·Ibrahim·Al-Hinkawi 2023, *Buildings* 13:1610 (원문 확보) | Sulaymaniyah, 이라크 | **95** | 시장 **호가** (중개업소 + 현장조사) | Integration·Connectivity + **가로폭**, **축선·전역** | OLS 다중회귀 | **R²=0.766**, adj 0.758, F=99.266 | 없음 (주거/상업 층화만) | **처리 없음** |
| 3 | Abdulla·Ibrahim 2023, *Sustainability* 15:7239 (원문 확보) | Kirkuk, 이라크 | 115(관) + 70(중개) | 시장 **호가** ($/㎡) | Integration·Connectivity, **축선·전역(infinite radius)** | 선형 + 3차 비선형 curve estimator | 최종 **R²=0.606**, F=53.996 | 없음 | **처리 없음** (다중공선성만 언급) |
| 4 | 김혜영·전철민 2012, 한국지리정보학회지 15(2):35-45 (**원문 미확인, 초록 기준**) | **서울 강남구** | 미확인 | **지가** | space syntax를 접근성 변수로, **크기가 다른 grid-cell 다수** | **OLS vs GWR 비교** | 미확인 | 미확인 | **GWR로 공간효과 반영 + MAUP 명시** |
| 5 | **Xiao·Webster·Orford 2016**, *Urban Studies* 53(1):112-131 (**게재본 미확인**, 저자 박사논문 6장 전문 확보) | 난징 10개구, **2005–2010 연 6웨이브** | **2,704** project-year / 593 projects, 불균형 패널 | **분양 예정 평균가 ASP**(원/㎡) log — 거래가 아님 | Integration·Choice × **반경 10개(800m·1200m·2·5·8·10·12·15·20km·∞)** = 20변수 동시 투입, **미터 반경**, 축선 | **수준 + 개체고정효과 패널** (1차 차분 아님) | **FE R²=0.658** (adj 0.509); OLS 0.509, RE 0.844 | 구 더미 9개, 사업 속성, CBD·호수·공원 직선거리 (FE에서는 소거) | **처리 없음**(6장). 같은 논문 4장에서 잔차 Moran's I 0.239·0.267, z=47.514 유의 |
| 6 | Law (2018) UCL 박사논문 / Law (2017) *Cities* 60:166-179 / Chiaradia et al. 2013 ICE *UD&P* 166(3):176-199 | 런던 | 미확인 | 주택가격 | **다중 반경(multi-scale)** | 헤도닉 (Law 논문은 SEM으로 자기상관 처리) | 미확인 | 미확인 | Law: SEM |
| 7 | Doan & Bui (2026) *TR Part A* 212:105134 "Do better street configurations mean higher land prices?" | 하노이(추정) | 미확인 | **지가** | 미확인 | **ML + GeoShapley**(비선형·공간이질성) | 미확인 | 미확인 | 미확인 |

출처 URL:
[#1](https://doi.org/10.11108/kagis.2016.19.1.043) ·
[#2](https://doi.org/10.3390/buildings13071610) ·
[#3](https://doi.org/10.3390/su15097239) ·
[#4](https://scienceon.kisti.re.kr/srch/selectPORSrchArticle.do?cn=JAKO201212656357889&dbt=NART) ·
[#5](https://journals.sagepub.com/doi/abs/10.1177/0042098014560500)
(박사논문 전문 <http://orca.cf.ac.uk/44866/1/2013XiaoYphd.pdf>) ·
[#6](https://discovery.ucl.ac.uk/10041030/) ·
[#7](https://www.sciencedirect.com/science/article/abs/pii/S0965856426002752)

### ⭐ #5 정밀 — 반경 문제와 "변화-대-변화"에 대한 가장 중요한 증거

게재본(*Urban Studies*)은 closed access로 확보하지 못했다. 대신 **동일 연구가
저자의 박사논문 6장으로 실려 있고 그 전문을 확보했다** (Xiao, Y. (2013) *Urban
Morphology and Housing Market*, Cardiff University,
<http://orca.cf.ac.uk/44866/1/2013XiaoYphd.pdf>; 6장 제목 "Identifying the
micro-dynamic effects of urban street configuration on house price volatility using
a panel model", pp.165-208. 2017년 Springer 단행본에도 같은 제목의 장으로 재수록).
**게재본이 사양을 바꿨는지는 미확인.** 아래는 전부 박사논문 기준이다.

**(a) 배경 가정 정정 — 이것은 1차 차분 모형이 아니다.**
Part 2 서두의 기대와 달리 이 논문은 **수준(levels) + 개체고정효과** 패널이다.
저자는 1차 차분을 **검토하고 명시적으로 기각**했다(6.1절): 주택정책이 기간 중 여러 번
바뀌어 미관측 이질성을 절편이 흡수하게 두는 편이 낫고, FE는 오차항 독립 하에서,
1차 차분은 완전 계열상관 하에서 각각 효율적이라는 이유다.
Hausman-Wu χ²=1029.051 (p<2.2e-16)로 FE가 RE보다 선호됐고,
**양방향 FE는 실패했다**(F=1.074, p=0.370) — 저자는 이를 동태 효과가 공간·시간에
걸쳐 **비동기적**이기 때문으로 해석한다.

> ⚠️ **더 중요한 경고**: 저자가 이 연구를 시작한 계기가 **Iacono & Levinson (2011)**
> 이다. 그들은 미니애폴리스-세인트폴 지가에 **문자 그대로 Δ-on-Δ(1차 차분)를
> 적용했고 유의한 미시동태 접근성 효과를 찾지 못했다.** Xiao의 진단은 그들의 접근성
> **측정이 너무 거칠었다**는 것이고, 그의 기여는 더 잘 측정된 접근성 변수를
> 고정효과 패널에 넣은 것이다.
> → **2.7의 1차 차분 설계는 선례에서 한 번 실패한 적이 있다.** 실패 원인이
> 측정이었다면 우리는 유리하다(우리 Integration은 그들보다 정밀하다). 그러나
> "차분하면 잘 될 것"이라고 낙관하면 안 되고, **차분 후에도 신호가 남는지를 먼저
> 확인**해야 한다(3.6).

**(b) 반경 — 이 조사에서 반경에 대해 얻은 유일한 실증 근거.**
저자 축자 인용(6.3.1절):

> *"I specify experimentally the radii of 800m, 1200m, 2km, 5km, 8km, 10km, 12km,
> 15km, 20km and infinite distance as space syntax connectivity zones."*

즉 **반경 10개 × 지표 2개 = 20개 변수를 한 모형에 동시에 투입**했다. 필지 값 부여
규칙은 *"the value of street segment nearest to the observation"* — **우리 설계(접면
세그먼트 값 상속)와 같다**(3.5).

**결과의 부호가 스케일에 따라 뒤집힌다 — 이것이 핵심 발견이다.** FE 모형 유의 계수:

| 변수 | 계수 | t |
|---|---|---|
| CH_800M (Choice 800m) | **+0.083** | 2.138 * |
| CH_5KM | **+0.191** | 2.292 * |
| **CH_8KM** | **−0.416** | −2.293 * |
| CH_N (전역 Choice) | **−0.130** | −3.474 ** |
| **Int_N (전역 Integration)** | **+0.005** | **31.668 *** |
| Int_1200M / Int_10KM / Int_20KM | −0.003 / −0.002 / −0.001 | 전부 음, 유의 |

저자 해석: 국소 Choice는 보행 기반 목적지 밀도(집적)로 **양(+)**, 8km·전역 Choice는
**교통 외부성(소음·매연·불안)으로 음(−)**. 요약 인용: 소규모 가로망 개선은 가치를
더하지만 *"larger scale street network improvement (e.g. freeways, express ways and
bridges) tend to impose negative externalities … which are capitalized in asking
price."* 또 **환상도로 2km 버퍼 안에서는 음, 밖에서는 양**의 동태 관계가 나타난다.

> **→ 우리 설계에 대한 직접 함의**: 반경 500m~1km만 쓰는 것은 **오히려 안전한
> 선택**이다(양의 효과가 나타난 구간). 그러나 **Choice와 Integration의 부호가 같다고
> 가정하면 안 되고**, 반경을 넓힐 때 부호가 뒤집힐 수 있음을 예상해야 한다.

**(c) 저자가 스스로 밝힌 반경 선택의 한계 — 우리가 인용해야 할 문장.**

> *"The process of specifying space syntax radii **seems arbitrary as there is a
> lack of precise definition to distinguish between each radii** … and it is found
> that **different radii could be highly correlated** … **Different researchers
> apply different sets of radii and the results are rarely coherent.**"*

20개 반경-지표 항을 한 회귀에 넣었는데 **VIF나 조건수 진단이 보고되지 않았다**
(게재본 추가 여부 **미확인**). 반경 간 다중공선성이 위에서 본 "부호가 오가는" 패턴의
실제 원인일 가능성이 크다. → **3.2의 "반경을 하나씩 넣어 비교한다"는 권고의 근거.**

**(d) 종속변수의 한계 — 우리와 방향이 반대다.** DV는 거래가가 아니라 개발사업의
**분양 예정 평균가(ASP)**를 상업 부동산 사이트 두 곳(`sofun.com`, `fangjia.com`)에서
매년 10월 시점으로 수집한 것이다. 저자 인정: *"the average selling price is an
estimated price by developer based on expectations, thus it does not represent the
average transaction price. Consequently, the estimated coefficients are not
completely accurate."* 중국은 신축 거래가가 비공개이고 재거래 이력이 거의 없어
거래가 패널 자체를 만들 수 없었다는 설명이다.
→ **우리는 반대로 공시지가라는 전수·매년 시계열을 가질 수 있다**(1.3). 이 점에서
우리 데이터가 이 선례보다 낫다.

**(e) 인과 주장을 하지 않는다.** 표현은 일관되게
*"statistically associated"*, *"correlated"*, *"partial determinants"*다. IV·DiD·
이벤트 스터디·플라시보가 없다. 다만 저자는 예측 용도 자체는 지지한다(6.6절):

> *"This allows, with appropriate modeling tools, for governments and investors to
> predict housing price pattern and movement by observing the levels and changes of
> accessibility across time and space."*

**(f) 국가 간 계수 이전에 대한 경고.** 같은 논문의 카디프(영국) 결과는
**고가↔고연결·저교통량**인데 난징은 **고가↔도심 고교통량·교외 저연결**이었다. 저자
결론: *"people from different countries have different preferences."*
→ **런던·난징 계수를 서울에 그대로 옮겨 쓰면 안 된다. 서울에서 다시 추정해야 한다.**

#2·#3 세부 (원문 PDF에서 직접 추출):

- **#2 Buildings 13:1610** — ANOVA에서 회귀 df=3, 잔차 df=91, 전체 df=94 → **N=95**.
  표준화계수 **Integration 0.438 / Connectivity 0.338 / 가로폭 0.299**, SEE 413.64.
  표본은 "geographical cluster sampling" 후 **고·중·저 가격 3층으로 층화** — 즉
  가격을 보고 표본을 고른 **선택편의**가 있어 R²=0.766을 그대로 믿을 수 없다.
- **#3 Sustainability 15:7239** — 전역 Integration 단독 선형 **R²=0.540**(다른 표에
  0.239도 등장하나 어느 지표인지 정렬 실패 → 미확인), Integration **3차 비선형
  R²=0.702**, Connectivity 3차 R²=0.532, 최종모형 **R²=0.606**. 가격은
  Kirkuk Housing Directorate(행정가격)와 중개업소 호가를 비교해 **㎡당 약 $200
  차이**를 확인한 뒤 중개업소 호가를 채택했다 — 행정가격 대신 시장가격을 고른 것.

**#4가 우리에게 가장 중요한 국내 선례다.** 서울·지가·space syntax·GWR·MAUP를 한
번에 다루며, 초록의 결론이 곧 우리에게 필요한 경고다: *"제안된 접근성 변수는
지가분석에 유의한 결과를 보였으며 GWR이 OLS보다 개선되었고, **공간단위설정에 따라
결과 값이 달라진다**."* 즉 **집계 단위/반경 선택이 결과를 좌우한다**는 것이 2012년에
서울 강남구에서 이미 관측됐다.
원문 PDF(10.7MB)는 확보했으나 **스캔 이미지여서 텍스트 추출에 실패**했다 —
R²·계수·변수 목록은 **미확인**이며, 필요하면 사람이 PDF를 열어 봐야 한다.

### 비교표에서 바로 읽히는 것

1. **반경 설정을 근거를 들어 정한 연구가 이 표에 없다.** #1~#3은 전부 전역(Rn)
   축선 분석이고 반경 논의가 없다. 우리 엔진은 500m~1km 국소를 쓰므로 **선례를 그대로
   따를 수 없고, 반경은 우리가 데이터로 골라야 한다**(#4·#6이 그 방법을 준다).
2. **공간자기상관을 처리한 연구가 #4 하나뿐이다.** #1~#3의 표준오차·유의성은
   신뢰할 수 없다(2.3).
3. **통제변수를 넣은 연구가 거의 없다.** #2가 가로폭을 넣은 정도다. 따라서 보고된
   R²(0.57·0.606·0.766)는 **Integration의 순효과가 아니라 "입지 좋음"의 총합**이다.
   이 숫자들을 우리 목표치로 삼으면 안 된다(2.4).

## 2.3 공간계량 문제 — 표준 처방과, 이 분야가 실제로 한 것

### 정본 레퍼런스

| 주제 | 1차 출처 |
|---|---|
| SAR/SEM 유형학·ML 추정·LM 검정 | Anselin (1988) *Spatial Econometrics: Methods and Models* — <https://link.springer.com/book/10.1007/978-94-015-7799-1> |
| direct/indirect/total impacts | LeSage & Pace (2009) *Introduction to Spatial Econometrics* |
| SDM·모형선택 | Elhorst (2010) *Spatial Economic Analysis* 5(1):9-28 — <https://www.tandfonline.com/doi/abs/10.1080/17421770903541772> |
| robust LM-lag / LM-error | Anselin, Bera, Florax & Yoon (1996) *RSUE* 26(1):77-104 — <https://www.sciencedirect.com/science/article/abs/pii/0166046295021116> |
| GWR / MGWR | Brunsdon et al. (1996) *Geographical Analysis* 28(4):281-298 · Fotheringham et al. (2017) *Annals AAG* 107(6):1247-1265 |
| **공간 고정효과·서브마켓** | **Bourassa, Cantoni & Hoesli (2007) *JREFE* 35:143-160 — <https://link.springer.com/article/10.1007/s11146-007-9036-8>** |
| **Conley 공간 HAC** | **Conley (1999) *J. Econometrics* 92(1):1-45 — <https://www.sciencedirect.com/science/article/abs/pii/S0304407698000840>** |
| Moran's I / LISA | Moran (1950) *Biometrika* 37:17-23 · Anselin (1995) *Geographical Analysis* 27(2):93-115 |

### 이 조사가 권고를 바꾼 두 가지 근거

**(1) 부동산 헤도닉에서는 "고정효과가 공간모형을 이겼다"는 실증이 있다.**
Bourassa, Cantoni & Hoesli (2007)는 오클랜드 데이터에서 **OLS + 서브마켓 더미가
geostatistical·SAR/SEM보다 예측 정확도 개선이 더 컸고 구현도 훨씬 단순**했다고
보고한다. → 우리가 3.2에서 "동 고정효과부터"를 권고하는 실증 근거.

**(2) Gibbons & Overman (2012)의 처방이 우리 사례를 정확히 지목한다.**
원문 전문 확보 <https://personal.lse.ac.uk/gibbons/Papers/Mostly%20Pointless%20Spatial%20Econometrics.pdf>
(공개판 <https://eprints.lse.ac.uk/33559/1/sercdp0061.pdf>). 3.2에 인용한 세 논지
외에, **그들이 대신 하라고 제시한 목록의 두 번째 항목이 우리 문제 그 자체다**:

> *"changes over time in the connections between places or agents may allow
> identification"* — 예시로 **학군 경계 변경과 교통망 변경**을 든다.

즉 이 논문은 우리 설계를 비판만 하는 것이 아니라 **2.7(도로망 변화를 이용한 식별)이
옳은 방향임을 지지한다.** 나머지 처방: 축약형 SLX, 설계된 IV(미국 고속도로가
동서/남북 격자로 계획된 것을 예로 든다), spatial differencing / DiD / RD.
핵심 원칙 인용: *"The goal is more modest — to find 'control' and 'treatment' places
that are comparable along the dimensions that influence the outcome being studied."*

**반대 진영도 적어 둔다.** LeSage & Pace (2014) "The Biggest Myth in Spatial
Econometrics", *Econometrics* 2(4):217-249 <https://www.mdpi.com/2225-1146/2/4/217>는
"추정이 W 설정에 민감하다"는 통념에 이론적 근거가 약하다고 반박한다 — 단, 모형이
제대로 특정되고 추론이 **참인 편미분(impacts)** 에 기초할 때. 실무 함의: **만약
SAR 계열을 쓴다면 계수가 아니라 direct/indirect/total impacts로 보고해야 한다.**

### Moran's I의 올바른 위치

Anselin et al. (1996)에 따르면 **OLS 잔차의 Moran's I는 "의존성이 있다"만 말하고
lag인지 error인지 구분하지 못한다.** 따라서 Moran's I는 **스크리닝 도구**이고, 모형
선택은 robust LM-lag / LM-error가 한다. 사후에 공간모형(또는 고정효과 모형) 잔차에
다시 걸어 잔여 군집이 사라졌는지 확인하고, **특정 구역에 LISA 클러스터가 남으면
서브마켓 고정효과 누락 신호**로 읽는다.

⚠️ Xiao의 카디프 사례(2.2 #5의 같은 논문 4장)가 경고한다: **space syntax 변수를
넣은 뒤에도 잔차 Moran's I가 0.239·0.267이고 z=47.514로 유의**했다. 즉
**Integration을 넣는 것으로 공간자기상관이 해결되지 않는다.**

### 서울 사례와, Integration 특유의 문제

- **서울 공간 헤도닉의 표준 사례가 있다**: Kim, C.W., Phipps, T.T. & Anselin, L.
  (2003) "Measuring the benefits of air quality improvement: a spatial hedonic
  approach", *JEEM* 45(1):24-39
  <https://www.sciencedirect.com/science/article/abs/pii/S009506960200013X> —
  **서울 대도시권**, 진단검정으로 spatial lag 모형을 선택하고 거기서 **개선의 한계
  가치**를 계산한다. Anselin 본인의 응용이므로 절차의 표준으로 인용할 수 있다.
- ⚠️ **Integration은 "관측된 변수"가 아니라 그래프에서 계산된 파생 변수다.**
  Anselin & Lozano-Gracia (2008) "Errors in variables and spatial effects in
  hedonic house price models of ambient air quality", *Empirical Economics*
  <https://link.springer.com/article/10.1007/s00181-007-0152-3>가 다루는 문제가
  **우리에게 직접 걸린다** — 설명변수 자체가 추정·보간된 값일 때의 measurement
  error. 반경 선택·경계효과·OSM 디지타이징 차이가 모두 여기 들어온다.
- GWR로 갈 경우: Wheeler & Tiefelsdorf (2005) *J. Geogr. Syst.* 7:161-187
  <https://link.springer.com/article/10.1007/s10109-005-0155-6> — **기저 변수가
  무상관이어도 GWR 국소계수는 collinear해진다.** Integration처럼 강한 공간추세를
  가진 변수에 직접 걸리는 경고이므로, GWR을 주 모형으로 쓰지 않을 이유가 하나 더 늘었다.

## 2.4 Omitted variable bias — 크기는 알려져 있고, 우리 경우 **부호조차 미정**이다

### 편의 크기의 실증 (1차 출처)

| 연구 | 횡단면/OLS | 준실험/IV | 함의 |
|---|---|---|---|
| **Black (1999)** *QJE* 114(2):577-599 <https://academic.oup.com/qje/article-abstract/114/2/577/1844232> | 통상 헤도닉 | 학군 경계 고정효과: 성적 5%↑ → WTP **약 2.5%** | **횡단면이 약 2배 과대** |
| **Bayer, Ferreira & McMillan (2007)** *JPE* 115(4) <https://www.nber.org/system/files/working_papers/w13236/w13236.pdf> | 선행 약 2~4% | **1% 미만** | **횡단면이 수 배 과대**. 사회인구 변수 WTP의 상당 부분이 미관측 근린질과의 상관 |
| **Debrezion, Pels & Rietveld (2007)** *JREFE* 35(2):161-180 <https://ideas.repec.org/a/kap/jrefec/v35y2007i2p161-180.html> | 역세권 프리미엄 주거 **4.2%**, 상업 **16.4%**; 250m 근접당 주거 2.3%↑ | — | **고속도로 등 다른 접근성 변수를 넣으면 철도역 효과가 줄어든다** |
| **Rennert (2022)** *TR A* 163:165-180 <https://www.sciencedirect.com/science/article/pii/S0965856422001677> | 200+ 사례 메타 | — | 효과 범위 **−7.4%p ~ +9.6%p — 부호가 뒤집힌다** |
| **Baum-Snow (2007)** *QJE* 122(2):775-805 <https://matthewturner.org/ec1410/readings/BaumSnow_QJE_2007.pdf> | ray당 중심도시 인구 **−약 6%** | 1947년 주간고속도로 계획 IV: **−약 18%** | **IV가 3배** — 저자는 OLS 감쇠의 원인을 **측정오차**로 해석 |
| Mohammad et al. (2013) *TR A* 50:158-170 | 23연구/102추정치 | — | 이질성 원인에 **도로 접근성**과 **방법론적 특성**이 포함. 유럽·동아시아 > 북미 |

Debrezion의 결과가 우리에게 가장 직접적이다: **Integration 하나만 넣으면 그 계수는
"도로 구조"가 아니라 "모든 접근성 + 그와 상관된 미관측 입지질"의 합**이 된다.
→ 3.1-B의 통제변수(역 거리·역 승하차량·상권·생활인구)를 **반드시** 넣어야 하는
실증적 이유.

### ⭐ 우리 경우의 비대칭 — 이 조사의 핵심 발견

편의의 방향이 문헌에서 **두 갈래로 갈린다.**

- **헤도닉 근접 프리미엄 계열** (우리와 더 가깝다): 횡단면이 **과대추정**되는 것이
  지배적. 경계 고정효과·반복매매·DiD로 가면 **대략 절반 이하**(Black, Bayer).
- **인프라 물량 → 성과 계열**: 처리변수 **측정오차** 때문에 OLS가 **과소추정**되고
  IV가 2~3배(Baum-Snow, Duranton & Turner).

**Space Syntax Integration은 양쪽 성격을 동시에 갖는다.** 미관측 입지 품질과 강하게
상관된 접근성 변수(→ 상향 편의)이면서, 그래프 구축 방식·반경 선택·경계효과·OSM
디지타이징에 민감한 **구성된 측정오차 변수**(→ 감쇠 편의)다(2.3의 Anselin &
Lozano-Gracia). **따라서 우리의 횡단면 계수는 편의의 부호조차 사전에 특정할 수 없다.**

→ 이것이 화면 표기를 "상한"이나 "하한" 어느 쪽으로도 부르면 안 되는 이유다(2.5, 3.4).
단일 숫자를 인용해야 한다면 헤도닉 맥락에서 가장 방어 가능한 것은
**Black(1999)의 "약 2배"**다.

### ⚠️ 접근성 개선이 지가를 **떨어뜨릴** 수도 있다

이것은 "뜰 자리" 로직에 대한 직접적 반례이므로 명시해 둔다.

- **Faber (2014)** *ReStud* 81(3):1046-1070 <https://www.ben-faber.com/China2012.pdf>
  — 중국 국가간선도로망. "무관한 단위(inconsequential units)" + **최소비용경로
  신장트리 IV**. 결과: **연결이 비대상 주변부의 공업생산·GDP 성장률을 감소**시켰다.
- **Chandra & Thompson (2000)** *RSUE* 30(4):457-490
  <https://www.sciencedirect.com/science/article/abs/pii/S0166046200000405>
  — 고속도로 통과 카운티는 24년 후 활동 **6~8%↑**, 그러나 **인접 카운티 총소득
  1~3%↓**.
- Xiao(2016) 난징의 **CH_8KM = −0.416**(2.2 #5)도 같은 현상의 도시 내부 버전이다.

→ **"길 구조가 좋아지면 오른다"는 단조 가정을 코드에 넣으면 안 된다.** 어떤 필지는
인접 중심지에 흡수될 수 있다. 부호를 모형이 정하게 하고, 부호가 음으로 나오는
케이스를 억지로 뒤집지 않는다.

### 표준 해법과 핸드북

- 개관 정본: **Redding & Turner (2015)** "Transportation Costs and the Spatial
  Organization of Economic Activity", *Handbook of Regional and Urban Economics*
  vol.5 ch.20:1339-1398 <https://www.princeton.edu/~reddings/papers/handbooktransport.pdf>
  — 인프라 배치가 무작위가 아닌 것이 근본 문제이며, 문헌을 **계획 노선(planned
  route) IV**와 **역사 노선(historical route) IV** 두 계열로 정리한다.
- IV 실례: Duranton & Turner (2011) *AER* 101(6):2616-2652
  <https://matthewturner.org/papers/published/Duranton_Turner_AER_2011.pdf> —
  1947년 계획 연장 / 1898년 철도 노선 / 1835-1850 탐험 원정 노선. 공통 논리는
  "현재 수요를 경유하지 않고 역사·지형을 통해서만 현재 도로 물량에 영향".
- 개통 전후 DiD: Gibbons & Machin (2005) *JUE* 57(1):148-169
  <https://cep.lse.ac.uk/pubs/download/dp0611.pdf> — 저자들이 스스로
  **"횡단면 가치평가에 내재한 편의를 피하기 위한"** 설계라고 규정.
- 한국: "Transport infrastructure investment, accessibility change and firm
  productivity: Evidence from the Seoul region", *J. Transport Geography* (2021)
  <https://www.sciencedirect.com/science/article/abs/pii/S0966692321002350> —
  **1차 차분 + IV**로 시간불변 미관측 요소와 표적화된 교통정책·역인과를 다룬다고
  명시. 서울권에서 내생성을 정면으로 다룬 드문 사례(IV의 구체적 정체 **미확인**).

> **문헌 공백 — 그리고 이 프로젝트가 서 있는 자리**: Space Syntax × 지가 문헌 중
> **누락변수편의·내생성을 정면으로 다룬 논문을 찾지 못했다.** 반대로 경제학 문헌은
> 식별은 엄격하지만 처리변수를 "역 거리"나 "도로 물량"으로 단순화한다.
> **정밀한 처리변수(Integration) × 엄격한 식별(패널·DiD)의 조합이 비어 있고,
> 2.7이 정확히 그 자리다.**

## 2.5 counterfactual 대입의 타당성 — 이 설계의 핵심 쟁점

### 결론부터: 문헌은 이 용법을 **명시적으로 경고**한다

**Mann & Levinson (2026)** "Access for appraisal: a systematic review of estimating
transport benefits via real-estate uplift", *Transport Reviews*, DOI
10.1080/01441647.2026.2627191 (오픈액세스) <https://doi.org/10.1080/01441647.2026.2627191>

14개 실증연구를 검토한 결과: **ex post(사후) 평가는 대부분 DiD로 인과식별하지만,
ex ante(사전) 연구는 "cross-sectional models with limited efforts to address
endogeneity"에 의존한다.** → **우리 설계는 이 문헌 분류에서 가장 약한 범주에 들어간다.**
같은 리뷰가 uplift가 direct user benefits를 **초과**하는 보고들이 있어 이중계상
위험을 지적하고, 응용연구들이 **공급측 반응**을 무시한다고 비판한다.

### Rosen 계보 — 2단계 식별 문제

- **Rosen (1974)** "Hedonic Prices and Implicit Markets", *JPE* 82(1):34-55 — 1단계
  가격함수의 도함수 = implicit price = 한계 WTP.
- **Brown & Rosen (1982)** *Econometrica* 50(3):765-768 — 2단계가 함수형태 가정만으로
  식별된다는 비판.
- **Epple (1987)** *JPE* 95(1):59-80 · **Bartik (1987)** *JPE* 95(1):81-88 — 가격함수가
  비선형이면 속성의 implicit price가 소비량과 체계적으로 함께 변한다 → 2단계에
  내생성이 있고 공급측 관측특성은 유효한 IV가 아니다.
  요약 출처: Bishop & Timmins, NBER WP 17611 <https://www.nber.org/papers/w17611>
- 이후 통설: 헤도닉 균형모형 식별에는 **복수 시장 데이터 또는 준실험 설계가 필요**하다.

### capitalization ≠ WTP — 그리고 "상한"이라는 라벨은 틀렸다

**Kuminoff & Pope (2014)** "Do 'Capitalization Effects' for Public Goods Reveal the
Public's Willingness to Pay?", *International Economic Review* 55(4):1227-1250
<https://onlinelibrary.wiley.com/doi/10.1111/iere.12088>

초록: *"The model predicts that trading between heterogeneous buyers and sellers
will drive a wedge between these 'capitalization effects' and welfare changes."*
5개 대도시권 boundary discontinuity 적용 결과 — **capitalization effect가 학부모의
WTP를 최대 75%까지 과소평가**했다.

> ⚠️ **이 문서 초안의 오류를 여기서 정정한다.** "자본화 상한(upper bound)"이라는
> 라벨은 **쓸 수 없다.** Kuminoff & Pope가 보인 것은 자본화가 WTP를 **과소**평가한
> 사례이고, 방향은 상황에 따라 달라진다. 자본화가 WTP의 상한이라는 보장은 없다.

**Banzhaf (2021)** "Difference-in-Differences Hedonics", *JPE* 129(8):2385-2414
<https://www.journals.uchicago.edu/doi/10.1086/714442> (WP <https://mpra.ub.uni-muenchen.de/101194/>)
— 준실험 헤도닉은 **"ex post 가격함수 위의 이동"**을 식별하며, baseline 조건부로
얻는 **"average direct unmediated effect"**는 후생의 **하한(lower bound)**이다.
→ **준실험 추정치조차 후생이 아니라 후생의 하한이다. 횡단면 계수는 그 하한 자격조차
없다.**

### ⭐ 이 분야의 공식 best-practice 문서가 우리 질문에 직접 답한다

**Bishop, Kuminoff, Banzhaf, Boyle, von Gravenitz, Pope, Smith, Timmins (2020)**
"Best Practices for Using Hedonic Property Value Models to Measure Willingness to
Pay for Environmental Quality", *Review of Environmental Economics and Policy*
14(2):260-281 <https://www.journals.uchicago.edu/doi/full/10.1093/reep/reaa001>

확인된 권고 (우리 설계에 하나씩 대응한다):

1. 초록: *"The starting point is a research design that identifies a source of
   exogenous variation in an amenity… **Under ideal conditions**, the derivative of
   this price function can be interpreted as indicating the amenity's implicit
   price."* → **외생적 변동원이 출발점**이고, 해석은 "ideal conditions" 조건부다.
2. 외생변동 없는 횡단면: *"unobserved amenities are likely to be correlated with the
   amenity of interest"* → 상당한 편향에 취약.
3. **권고**: 학군·우편번호·센서스 트랙 같은 **공간 더미로 미관측 어메니티의 가격효과를
   흡수**하라. 그리고 **bias–variance trade-off**를 인지하라 — 근린을 작게 잡으면
   편향↓·분산↑. → 3.2의 "동 고정효과, 필요하면 1km 격자"가 바로 이 권고다.
4. **비한계적(non-marginal) 변화**: 한계 WTP × 변화량의 곱셈 외삽은 **"변화가 충분히
   작다고 믿어질 때만"** 유효하다. 그 외에는 상·하한 bounding 등을 쓰라.
   → **우리 counterfactual이 정확히 이 곱셈이다.** 예정 도로가 만드는 ΔIntegration이
   작지 않으면 이 외삽은 무효다. → 3.6의 실측 항목 10번.
5. **일반균형 경고**: 정책 때문에 가격함수 자체가 이동할 수 있고,
   *"large changes in price functions that are not specifically modeled may
   undermine the estimation of MWTP."*
6. **보고 기준**: *"It is important to report the robustness of welfare conclusions
   to these and other modeling choices."* + 추정치가 **한계적 변화용인지 비한계적
   변화용인지 명시**하라.

관련: **Kuminoff, Smith & Timmins (2013)** *JEL* 51(4):1007-1062
<https://www.aeaweb.org/articles?id=10.1257%2Fjel.51.4.1007> — 가구가 부와 선호에
따라 근린을 **sorting**하고 그 집계가 어메니티 공급 자체를 바꾼다. **횡단면 구배는
이 sorting 균형의 산물**이다. 큰 변화의 후생평가에 GE 접근이 필요하다는 정본은
Sieg, Smith, Banzhaf & Walsh (2004) *IER* 45(4):1047-1077 (초록 수준 확인).

### 균형 구배 vs 처리효과 — 정리표

| | 횡단면 지가–Integration 구배 | 신설 도로의 처리효과 |
|---|---|---|
| 무엇인가 | 수십 년의 sorting·용도지역·개발이 누적된 **장기 균형 가격함수의 도함수** | 그 균형에서 벗어난 **한 시점 충격에 대한 반응** |
| 식별 조건 | 미관측 입지질과 Integration 무상관 (거의 확실히 위반) | 개통의 외생성 + 가격함수 안정성 |
| 후생 해석 | 없음 (Kuminoff & Pope 2014) | 후생의 **하한** (Banzhaf 2021) |
| 비한계 변화 | 곱셈 외삽 무효 (Bishop et al. 2020) | GE 재균형 필요 (Sieg et al. 2004) |

### 생태학적 오류 / MAUP — 동 단위 적합 → 필지 적용

- **Robinson (1950)** "Ecological Correlations and the Behavior of Individuals",
  *American Sociological Review* 15(3):351-357
  <https://www.stats.uwo.ca/faculty/aim/2015/9938/articles/Robinson1950AmericanSociologicalReview.pdf>
  — 州 단위에서는 이민율↑ → 문해율↑였으나 개인 단위에서는 **반대 부호**. 집계
  상관을 개인 상관의 대체물로 쓸 수 없다는 원전.
- **Openshaw (1983)** *The Modifiable Areal Unit Problem*, CATMOG 38
  <https://www.uio.no/studier/emner/sv/iss/SGO9010/openshaw1983.pdf> ·
  Openshaw & Taylor (1979) MAUP 명명 원전 · Openshaw (1984) *EPA* 16(1):17-31
- **헤도닉에 직접 적용된 1차 문헌**: "The modifiable areal unit problem in hedonic
  house-price models", *Urban Geography* 37(2), 2016
  <https://www.tandfonline.com/doi/abs/10.1080/02723638.2015.1057397> — 확인된 결론:
  *"When geographically aggregated data are included in hedonic models, the
  resulting coefficients are biased by the spatial scale and spatial configuration
  of variable measurement."* 유의한 scale·zoning 효과를 실증했고, 집계 스케일 변화에
  **표준 모형의 적합도 변화가 크고 공간모형은 작다**고 보고. (저자명 **미확인** — 403)

→ **우리에게 직접 적용**: 기존 "뜰 자리"는 **424개 행정동 집계**에서 구조·가격
백분위를 비교한다(3.0). 그 결과를 필지에 그대로 옮겨 말하면 생태학적 오류다.
그리고 김혜영·전철민(2012)이 서울 강남구에서 **격자 크기에 따라 결과가 달라진다**고
보고한 것이 MAUP의 국내·서울·지가 실증이다(2.2 #4).

### ⚠️ SAR 계열에서는 counterfactual 예측 자체가 자명하지 않다

**Goulard, Laurent & Thomas-Agnan (2017)** "About predictions in spatial
autoregressive models: optimal and almost optimal strategies", *Spatial Economic
Analysis* 12(2-3):304-325
<https://www.tandfonline.com/doi/abs/10.1080/17421772.2017.1300679>
— ρ 때문에 x 하나를 바꾸면 예측이 **시스템 전체에 걸린다.**
`(I − ρW)⁻¹(Xβ + WXθ)`를 다시 풀어야 하며, 대상 필지만 국소적으로 계산하면
spillover를 놓친다. → SAR을 쓰지 않는(3.2) 또 하나의 실무적 이유. 우리 엔진은
반경 내 국소 계산이 기본이므로 SAR과 궁합이 나쁘다.

### 있어야 할 선례 — 예측 + 검증의 2단 구조

**Ahlfeldt (2013)** "If we build it, will they pay? Predicting property price
effects of transport innovations", *Environment and Planning A* 45(8):1977-1994
<https://journals.sagepub.com/doi/abs/10.1068/a45429> (<https://ideas.repec.org/p/ehl/lserod/33595.html>)

**우리 과제와 사실상 동일한 논문이다.** 중력형 접근성 모형으로 대런던 신설역의
가격효과를 **사전 예측**하고, 그 예측을 **준실험적 가격분석으로 검증**한다. 효과
크기와 공간범위 모두에 상당한 이질성이 있으나 예측력은 양호하다고 보고.

> **→ 문헌이 지지하는 형태는 "예측"이 아니라 "예측 + 검증"이다. 검증 없는 예측은
> 이 분야에 선례가 없다.** 우리의 검증 경로는 2.7(과거 OSM 스냅샷 × 연도별 공시지가
> 백테스트)이며, 그것이 v2의 최우선 항목이 되는 이유가 여기다.

### 그래서 어떻게 표기해야 하는가 (판정)

| 쓰면 안 되는 표현 | 왜 |
|---|---|
| ❌ "예상 가격상승 X%" | 인과 처리효과라는 주장. 외생변동 없음 (Bishop et al. 2020) |
| ❌ "자본화 **상한**" | Kuminoff & Pope (2014)가 자본화의 WTP **과소**평가 사례를 보임. 상한 보장 없음 |
| ❌ "AI가 예측한 미래 지가" | 우리 모형에 시간 축이 없다 |

| 권고 표현 | 근거 |
|---|---|
| ✅ **"장기 균형 지가 구배가 함의하는 값"** | 횡단면 구배는 sorting 균형의 산물 (Kuminoff/Smith/Timmins 2013) |
| ✅ **"길 구조가 이 정도인 다른 필지들의 현재 지가 수준"** (벤치마크) | 인과 주장 없이 관측 사실만 진술 |
| ✅ **"인과 추정치가 아니며 개통 전후 검증을 하지 않았다"** 명시 | Bishop et al. (2020)의 robustness 보고 요구 |
| ✅ **한계적/비한계적 구분 명시** | Bishop et al. (2020) 명시 권고 |

가장 정직한 문장 형태:

> **"길 구조가 이 정도인 서울 내 다른 필지들의 현재 공시지가는 평균적으로 X~Y%
> 높습니다. 이 도로가 실제로 개통되면 그렇게 된다는 인과적 예측이 아닙니다 —
> 그 예측에는 과거 개통 사례의 개통 전후 비교가 필요하고 이 분석에는 포함되지
> 않았습니다."**

불확실성 폭의 근거로 **Rennert (2022)의 −7.4%p ~ +9.6%p**(2.4)를 함께 제시하는 것이
정직하다.

## 2.6 모델 선택 — 선형 헤도닉 vs GBM

### 판정: **counterfactual 대입에 트리 앙상블을 쓰는 것은 도구 선택 오류다.**

### 외삽 불가는 사실이며, 공식 문서로 확인된다

- **scikit-learn 공식 문서, Decision Trees "Disadvantages"**
  <https://scikit-learn.org/stable/modules/tree.html> — 축자 인용:
  *"Predictions of decision trees are neither smooth nor continuous, but piecewise
  constant approximations as seen in the above figure. **Therefore, they are not
  good at extrapolation.**"*
  → CART 기반 앙상블(RF·GBM·XGBoost·LightGBM·CatBoost) 전체에 적용되는 메커니즘
  근거. 마지막 분할점 밖은 그 리프의 **상수값**이 그대로 나온다.
- **LightGBM 공식 문서, `linear_tree`**
  <https://lightgbm.readthedocs.io/en/latest/Parameters.html> — 축자 인용:
  *"tree splits are chosen in the usual way, but **the model at each leaf is linear
  instead of constant**"*, *"the first tree has constant leaf values"*.
  → **기본 동작이 상수 리프임을 공식 문서가 확인**한다. (문서에 "extrapolation"이라는
  단어 자체는 없다 → **미확인**.) 대안이 존재하지만 대가가 있다: 메모리 급증,
  `regression_l1` 미지원, `monotone_constraints`가 분할점에만 적용되고 리프 선형모형에는
  미적용.
- **Malistov & Trushin (2019)** "Gradient Boosted Trees with Extrapolation",
  *ICMLA 2019*, IEEE <https://ieeexplore.ieee.org/abstract/document/8999249/> —
  peer-reviewed 확인: *"Gradient boosted decision tree algorithms only make it
  possible to interpolate data. Therefore, the prediction quality degrades if one
  of the features… lies outside the boundaries of training data set."*
- **Zhang, Nettleton & Zhu (2019)** "Regression-Enhanced Random Forests",
  arXiv:1904.10416 <https://arxiv.org/abs/1904.10416> — *"Random forests may fail
  in extrapolation problems where predictions are required at points out of the
  domain of the training dataset."* 해법으로 penalized 선형회귀 + RF 하이브리드 제안.
- Hengl et al. (2018) *PeerJ* 6:e5518 <https://peerj.com/articles/5518/> —
  *"Random Forest can lead to completely nonsensical predictions if applied to
  extrapolation domains."* (본문 내 위치 **미확인**)

**우리 설계에 대한 직접 함의 — 두 가지, 둘 다 치명적이다.**

1. 계획도로의 counterfactual Integration이 학습 데이터 최댓값을 넘으면 GBM 예측은
   **그 최댓값 구간의 값에서 정확히 멈춘다.** 예측 Δ가 0이 된다.
2. **범위 안에 있어도** 예측이 계단함수이므로 "Integration을 0.1 올리면 얼마"라는
   한계효과가 **국소적으로 0이거나 점프한다.** 우리 제품의 핵심 출력이 바로 이
   한계효과이므로, 이건 정확도 문제가 아니라 **기능이 성립하지 않는 문제**다.

### AVM/부동산 ML — 성능은 좋고, 우리 용도에는 안 맞는다

- **Gorjian (2025)** "Integrating Machine Learning and Hedonic Regression for
  Housing Price Prediction: A Systematic International Review", MPRA 125676
  <https://mpra.ub.uni-muenchen.de/125676/> — 23개 연구 중 **14개에서 Random
  Forest 채택, 5개에서 "exceptional"**. 확인된 문장: *"Despite their lack of
  precision, hedonic regression models provided critical explanatory insights into
  critical variables."* → **정확도는 ML, 설명력·정책추론은 헤도닉**이라는 분업 결론.
  구체 R²/MAPE는 초록에 없음 → **미확인**.
- **Mullainathan & Spiess (2017)** "Machine Learning: An Applied Econometric
  Approach", *JEP* 31(2):87-106
  <https://www.aeaweb.org/articles?id=10.1257%2Fjep.31.2.87> — 확인된 핵심 문장:
  *"Machine learning revolves around the problem of prediction, while many economic
  applications revolve around parameter estimation."* → ML은 ŷ에 강하고 β̂에 약하다는
  정본 진술. 우리가 필요한 것은 β̂다.
- **Tepe (2024)** "A random forests-based hedonic price model accounting for spatial
  autocorrelation", *J. Geogr. Syst.* 26:511-540
  <https://link.springer.com/article/10.1007/s10109-024-00449-w> —
  **우리 설계와 가장 가까운 논문**(단면 지가 + 공간피처 + RF + K-fold/spatial-block/
  spatial-LOO 3종 CV 비교, 잔차 Moran's I 검정). **세 CV별 수치는 유료로 미확인 —
  원문 확보 최우선 권장**(3.6).

### 그래서 무엇을 쓰나 — 부분선형 / DML 하이브리드

문헌상 **"해석 가능 + 외삽 가능 + 편향 통제"** 세 조건을 동시에 만족하는 유일한 구조:
**관심 변수(Integration)는 선형(또는 저차 스플라인) 계수로 유지하고, 나머지 통제변수를
ML에 맡긴다.**

- 정본: **Chernozhukov, Chetverikov, Demirer, Duflo, Hansen, Newey, Robins (2018)**
  "Double/debiased machine learning for treatment and structural parameters",
  *The Econometrics Journal* 21(1):C1-C68
  <https://academic.oup.com/ectj/article/21/1/C1/5056401> (arXiv:1608.00060)
  — **Neyman-orthogonal score + cross-fitting**으로 nuisance 함수의 정규화 편향·
  과적합을 제거. 부분선형모형 `Y = θD + g(X) + ε`에서 θ에 대해 √N-consistent.
  원조는 Robinson (1988) *Econometrica*의 partialling-out.
- 구현: DoubleML (Python/R) <https://docs.doubleml.org/stable/literature/literature.html>
- 헤도닉 응용 사례: <https://arxiv.org/pdf/2403.14385>
- 공간 하이브리드: **Sigrist (2022)** "Gaussian Process Boosting", *JMLR* 23
  <https://www.jmlr.org/papers/v23/20-322.html> (구현 GPBoost
  <https://github.com/fabsig/GPBoost>) — 공간 상관은 Gaussian process가, 비선형·
  교호작용은 tree-boosting이 담당.

**v1 권고는 여전히 단순 로그선형이다**(3.2). DML/GPBoost는 v1에서 얻을 것보다 복잡도가
크다. 다만 **"정확도를 올리려고 GBM으로 갈아타자"는 제안이 나올 때 이 절을 근거로
거절**해야 한다 — 갈아타는 순간 제품의 핵심 기능이 사라진다.

## 2.7 ★ 우리는 횡단면에 갇혀 있지 않다 — OSM 시계열 × 공시지가 전수 패널

이 조사에서 나온 **가장 값 있는 발견**이다. 배경에 적힌 "이벤트 기반 학습은 포기했다"는
판단은 **지하철 개통 이벤트(25~30개)** 기준으로는 옳다. 그러나 그것은 *이벤트를 센* 것이고,
**필지 × 연도 패널**을 센 것이 아니다. 우리는 다음 두 가지를 **이미 갖거나 값싸게 얻을 수 있다.**

**(1) 종속변수의 시계열 — 개별공시지가는 전 필지 × 매년이다.**
이벤트가 30개인 것과 관측치가 (필지 수 × 연도 수)인 것은 전혀 다른 이야기다. 도로 신설이
드물어도, 도로망 위상은 **주변에 무엇이 생기든 매년 조금씩 변한다** — 그리고 그 변화가
필지별 Integration을 조금씩 움직인다. 필요한 것은 "큰 이벤트"가 아니라 **변화의 분산**이다.

**(2) 독립변수의 시계열 — 우리 도로망은 OSM이고, OSM은 과거 스냅샷이 공개돼 있다.**

- `roads_clean`은 OSM 기반이다: `graph_builder.py`가 `osm_id`를 쓰고 주석이 OSM way의
  디지타이징 특성을 다루며, `docs/FEATURES_DATA_PLAN.md`가 출처를 **Geofabrik**으로 적는다.
- Geofabrik 한국 추출본 페이지(<https://download.geofabrik.de/asia/south-korea.html>)에
  **2015년부터 2026년 8월까지 날짜가 찍힌 과거 스냅샷**이 디렉터리로 공개돼 있다
  (최근 연도는 월 단위). 포맷 `.osm.pbf`/`.shp.zip`/`.gpkg.zip`, **라이선스 ODbL 1.0**.
  (개인정보가 포함된 완전이력 `.osh.pbf`는 OSM 기여자 로그인이 필요한 내부 서버에만
  있지만, **우리는 날짜별 스냅샷만으로 충분하다** — 완전이력은 필요 없다.)

**→ 따라서 이 설계가 가능하다:**

> 연도 t의 OSM 스냅샷으로 세그먼트별 Integration을 계산하고, 같은 해 개별공시지가를 붙여
> **필지 고정효과 + 연도 고정효과를 넣은 1차 차분(within) 회귀**를 돌린다:
> **Δlog(지가)_{i,t} = β · ΔIntegration_{seg(i),t} + 연도효과 + ε**

이것이 왜 결정적인가:

1. **Part 2.4의 omitted variable bias가 구조적으로 제거된다.** 시간에 불변인 입지 품질
   (지형·용도지역·역세권·상업 집적의 기저 수준)은 차분에서 전부 소거된다. 남는 것은
   "같은 필지 안에서 도로 구조가 변한 만큼 가격이 변했나"다.
2. **Part 2.5의 "횡단면 계수를 변화 예측에 쓴다"는 정당성 문제가 사라진다.** 우리가
   추정하는 것 자체가 이미 **변화-대-변화 계수**이기 때문이다. 이것은
   Xiao·Webster·Orford(2016)가 난징에서 2005–2010 구성 변화 대 가격 변화로 한 것과
   같은 식별 전략인데(비교표 #5), **차이는 그들은 5년 2시점이었고 우리는 매년 전수 필지를
   쓸 수 있다는 점**이다.
3. **횡단면 모형을 폐기하지 않아도 된다.** 횡단면 계수와 패널 계수를 **둘 다 추정해
   나란히 보고**하면, 그 격차 자체가 "선반영·자본화가 얼마나 들어가 있는지"의
   경험적 추정이 된다 — 최근 커밋 방향("뜰 자리 — 길 구조 좋은데 아직 가격에 안 반영된
   동")이 정확히 원하는 숫자다.

**⚠️ 두 가지 나쁜 소식을 함께 적어 둔다.**

**(가) 종속변수 시계열 확보가 방금 어려워졌다.** 이 설계에 가장 맞는 자산이었던
서울 열린데이터광장 개별공시지가 **1990~2026년 37년치 파일(공공누리 1유형)**이
**2026-08-18 서비스 종료**됐다(1.3). data.go.kr의 대체 API는 **시간범위가 "-"로
표기**돼 있고 **운영단계 심의승인**이 필요하다. **사람이 서울시에 문의하는 것이
이 설계의 선행 조건이다.**

**(나) 이 식별 전략은 선례에서 한 번 실패했다.** Iacono & Levinson (2011)이
미니애폴리스-세인트폴 지가에 문자 그대로 1차 차분을 적용했고 **유의한 접근성 효과를
찾지 못했다**. Xiao(2013)의 진단은 그들의 접근성 **측정이 거칠었다**는 것이고, 그가
1차 차분 대신 고정효과 수준 모형을 고른 이유도 여기에 있다(2.2 (a)).
→ **"차분하면 잘 될 것"이라고 낙관하면 안 된다.** 차분 후에도 신호가 남는지를
소규모로 먼저 확인하는 것이 순서다(3.6).

**⚠️ 이 설계의 최대 함정 — 반드시 통제해야 한다.**

**OSM에서 도로가 "생기는" 것은 도로가 건설된 것이 아니라 누군가 그것을 그린 것일 수
있다.** 서울의 OSM 커버리지는 2015년 이후 크게 개선됐으므로, 초기 연도의
ΔIntegration은 **실제 건설이 아니라 매핑 개선**을 상당 부분 반영한다. 이것을 걸러내지
않으면 "도로가 늘면 지가가 오른다"가 아니라 "OSM 기여자가 많은 동네가 비싸다"를
추정하게 된다. 완화책:

- **도시계획시설(도로) 결정 데이터로 교차 검증** — `docs/DEVELOPMENT_INTELLIGENCE_SOURCES.md`
  1.1의 서울시 OA-21134(공공누리 1유형). 신규 세그먼트가 계획도로 결정과 대응하는지 확인.
- **간선급으로 한정** — 폭이 큰 도로는 OSM 초기부터 완비돼 있어 매핑 잡음이 작다.
- **매핑 잡음 진단**: 연도별 서울 총 세그먼트 수·총 연장 추이를 먼저 그려 보고, 급증
  구간(매핑 캠페인)이 있는 연도는 제외한다. ← **착수 전 실측 항목**(3.6).

**라이선스 주의**: OSM은 **ODbL 1.0**이다. 파생 데이터베이스를 배포할 때 share-alike
의무가 발생할 수 있다. 화면에 분석 결과만 보여주는 것과 데이터베이스를 배포하는 것은
다르며, **어디까지가 허용인지는 이 조사에서 확인하지 않았다 → 미확인.** 사람이 확인할 항목.

---

# Part 3 — 이 프로젝트에서 무엇을 어떻게 만들 것인가

## 3.0 우리 엔진의 제약 (실측 기준)

권고를 쓰기 전에 코드에서 확인한 사실을 못 박아 둔다.

- `backend/app/services/space_syntax.py` `compute_metrics(G, segments, modes=(WALK, DRIVE))`
  — **segment-angular dual graph**에서 `choice`(NACH)·`integration`(NAIN)·
  `mean_depth`·`connectivity`를 계산하고, **walk/drive 서브그래프를 따로** 돈다
  (모드마다 all-pairs shortest-path 1회 → 모드를 하나로 줄이면 대략 절반).
- `spatial_query.fetch_roads_in_radius(lat, lng, radius_m)` — `roads_clean`
  (EPSG:5179)에서 반경 내 세그먼트를 받아오며, 연결성 보존용 버퍼를 더 가져온다.
- **성능이 설계를 결정한다**: `docs/PERF_AREA_SEARCH.md`의 라이브 실측에 따르면
  반경 800m area-search가 **고정비 약 8.5초**이고 **같은 입력을 세 번 보내도 전혀
  빨라지지 않는다**(매 요청마다 도로망을 새로 받아 그래프를 만들고 적분을 다시 돈다).
  → **필지별 온라인 계산은 불가능하다.** 5초 룰과 정면 충돌한다.
- 기존 "뜰 자리"(`backend/tools/compute_undervalued_areas.py`)는 **회귀가 아니다**:
  424개 행정동 중심점에서 500m 도보 반경으로 `(choice_walk+integration_walk)/2`
  평균을 구조 점수로 쓰고, 가격은 동 폴리곤 내 24개월 실거래 ㎡당가 중앙값
  (`MIN_TRADE_SAMPLE = 3`)이며, **두 값의 백분위 차이**를 갭 점수로 저장한다.
  → 이 문서가 권고하는 모델은 그 휴리스틱을 **계수 있는 회귀로 승격**하는 일이다.

**여기서 v1 아키텍처가 강제된다.**

> 필지마다 국소 반경을 돌리지 않는다. **전 서울 도로 세그먼트에 대해 Integration을
> 배치로 한 번 계산해 테이블에 저장하고, 각 필지는 자기 접면 도로 세그먼트의 값을
> 물려받는다.** — 조정훈·문태헌(2016)이 "표준지와 접한 도로의 통합도"를 매개로 쓴
> 것과 정확히 같은 구조이며, 우연이 아니라 이 문제의 유일하게 값싼 해법이다.

## 3.1 변수 목록

### 종속변수

- **주(학습)**: `log(개별공시지가 원/㎡)`. 로그를 쓰는 이유는 (a) 지가 분포가 강하게
  우편향이고 (b) 계수를 **"Integration 1단위당 몇 %"**로 읽어야 화면에 "상승률"로
  옮길 수 있기 때문이다. 조정훈·문태헌(2016)은 원 단위 선형이었고 그래서 예측값이
  음의 변화까지 갔다.
- **보(검증)**: 토지 실거래 ㎡당가. 학습에 쓰지 않고 **외부 검증에만** 쓴다(3.3).
  현재 `real_estate_trades`에 토지가 없으므로 신규 ingest가 선행 조건(1.5).

### 핵심 독립변수 (우리 엔진 산출물)

`compute_metrics`가 세그먼트별로 주는 값을 **반경별로 여러 개** 넣는다. 반경을 하나로
미리 고르지 않는 것이 이 조사의 권고다 — 선례 중 근거를 들어 반경을 정한 것이 없고
(2.2), 국내 선례(김혜영·전철민 2012)의 결론이 하필 **"공간단위설정에 따라 결과 값이
달라진다"**이기 때문이다. 데이터가 고르게 한다.

- `integration_walk_r500`, `integration_walk_r800`, `integration_walk_r1000` (NAIN)
- `choice_walk_r500 … r1000` (NACH)
- `integration_drive_*`, `choice_drive_*` — walk/drive는 `compute_metrics`가 이미 분리해
  주며, 토지가격에는 **drive(차량 접근)가 더 중요할 가능성**이 있다(비준표가 도로 폭을
  보는 것과 같은 논리). 반드시 둘 다 넣어 비교한다.
- `connectivity`, `mean_depth` (같은 함수가 함께 반환)

### 통제변수 — 1.2의 순환성 방어가 최우선

**A. 비준표가 이미 보는 것 (반드시 넣는다. 넣지 않으면 Integration 계수가 도로접면
배율을 대신 흡수한다.)**

| 변수 | 만드는 법 | 출처/근거 |
|---|---|---|
| 도로접면 등급 (12구분 또는 폭 구간) | 필지 최근접 `roads_clean` 세그먼트의 폭·접면 수 → 광대로 25m↑/중로 12–25m/소로 8–12m/세로 8m↓로 매핑 | [관악구 도로접면구분표](https://www.gwanak.go.kr/site/gwanak/standard/Standard_Road_Pop.do) |
| 도로거리 | `ST_Distance(필지, 최근접 도로)` | PostGIS |
| **용도지역** | 국토부 용도지역지구도 WFS 배치 적재 → 필지 공간조인 | <https://www.data.go.kr/data/15058773/openapi.do> — 전국, **공공누리 1유형(상업 OK)**, 무료, 운영단계 **자동승인**, 기준 2025-07-11 |
| 지목·면적 | 개별공시지가 표준데이터 / 연속지적도 속성 | 1.3 |

> 용도지역은 **가장 중요한 단일 통제변수**다. 상업지역이 간선도로변에 지정되므로,
> 넣지 않으면 "Integration이 높으면 비싸다"의 대부분이 실제로는 "상업지역이 비싸다"다.

**B. 입지 품질 (omitted variable 완화)**

| 변수 | 기존 테이블 |
|---|---|
| 최근접 지하철역 거리 + 역 승하차량 | `subway_ridership`, `planet_osm_point` |
| 생활인구(주중/주말, 주야) | `living_pop_grid`, `living_pop_slot` |
| 상권 매출·점포 밀도 | `golmok_sales`, `trdar_sales`, `trdar_stores`, `sbiz_stores` |
| 상권 경계 내부 여부 | `trdar_zone`, `trdar_area`, `commercial_districts` |
| 인구·종사자(1km 격자) | `sgis_grid_cells`, `sgis_grid_stats` |
| 학교 거리 | `schools` |
| 병원 공급 | `hira_hospitals`, `hira_hospital_subjects` |

**C. 고정효과**

- **행정동 고정효과** (`sgis_dong_boundary`, 424개) — 또는 더 촘촘히 **1km 격자
  고정효과**(`sgis_grid_cells`). 이것이 3.2의 핵심이다.
- **연도 고정효과** — 공시지가 시계열을 쓰는 순간 필수.

### 넣지 말아야 할 것

- **`real_estate_trades` 기반 가격 변수** — 종속변수와 같은 것을 우변에 넣는 누수.
  기존 "뜰 자리"가 가격 백분위를 쓰는 것은 갭 계산이라 괜찮지만, 회귀에서는 금지.
- **`undervalued_area_scores`** — Integration에서 파생된 값이라 순환.

## 3.2 모형 사양

### 권고: 로그-선형 헤도닉 + 고정효과 + 공간 강건 표준오차. SAR/SEM은 쓰지 않는다.

```
log(지가_i) = β·Integration_(seg(i), r) + γ·(비준표 통제: 접면등급·도로거리·용도지역·지목·면적)
              + δ·(입지 통제) + 동(또는 격자) 고정효과 + 연도 고정효과 + ε_i
```

표준오차는 **공간 클러스터(동 단위) 또는 Conley 공간 HAC**로 낸다.

**SAR/SEM/SDM을 권고하지 않는 근거 (1차 출처 확인).**
Gibbons & Overman, "Mostly Pointless Spatial Econometrics?", *Journal of Regional
Science* 52(2):172-191 (2012). 원문 PDF 전문 확보:
<https://personal.lse.ac.uk/gibbons/Papers/Mostly%20Pointless%20Spatial%20Econometrics.pdf>
(공개판 SERC DP 61: <https://eprints.lse.ac.uk/33559/1/sercdp0061.pdf>)

원문에서 직접 확인한 논지 세 가지:

1. **오차의 공간상관은 편향 문제가 아니라 표준오차 문제다.** 원문:
   *"…is irrelevant (aside from the implications for standard errors)."*
   → 그래서 우리는 계수 추정을 OLS/고정효과로 하고, **공간상관은 표준오차에서만**
   처리한다. 이게 SAR/SEM보다 단순하면서 더 방어 가능하다.
2. **공간시차 모형을 내생성의 "빠른 해결책"으로 쓰는 것을 정면 비판한다.** 원문 결론:
   *"…estimates spatial lag models as a quick fix for endogeneity issues, or that
   blindly applies spatial econometric models by default without serious consideration
   of whether this is necessary given the research question in hand… we urge those
   considering embarking down this route to think again."*
3. **저자들의 대안 권고**: 식별을 중심에 두는 실험주의 전략, 그것이 불가능하면
   **축약형(reduced form)** — 원문: *"…then we advocate a reduced form approach paying
   particular attention to the problems raised by endogeneity of the x_i"*, 그리고
   *"…argue for estimation of reduced form SLX models in x_i and spatial lags of x_i."*

→ 실무 번역: **우리 x(Integration)의 내생성에 집중하고, y의 공간시차를 추정하려 하지
않는다.** 내생성 대응은 (a) 3.1-A의 비준표 통제, (b) 동/격자 고정효과, (c) 궁극적으로
**2.7의 필지 내 1차 차분 패널**이다. Manski의 reflection problem(원문 §4에서 논의)
때문에 이웃 결과의 인과효과와 이웃 특성의 인과효과는 축약형에서 **분리 식별되지
않는다** — 그러니 시도하지 않는 것이 옳다.

**GWR은 보조로만.** 김혜영·전철민(2012)이 서울 강남구에서 GWR이 OLS보다 개선된다고
보고했으므로(초록 기준) 계수의 **공간적 비정상성 진단**에는 쓸 값이 있다. 다만 GWR은
지역별 계수를 주므로 counterfactual 대입 시 "어느 지역 계수를 쓸 것인가"가 새 문제로
생긴다. **v1은 전역 계수 + 고정효과로 가고, GWR은 "이 계수가 강남과 강북에서 같은가"를
점검하는 진단 도구로만 쓴다.**

### 반경은 데이터가 고른다

반경 후보 {500, 800, 1000}m × 모드 {walk, drive} × 지표 {integration, choice}를 넣고,
**공간블록 교차검증 성능(3.3)으로 고른다.** 정보기준(AIC/BIC)만으로 고르면 공간자기
상관 때문에 과대적합 쪽으로 끌린다. 다중공선성이 심할 것이므로(반경이 겹친다) 전부
동시에 넣지 말고 **반경 하나씩 넣은 모형들을 비교**하거나 주성분/정규화를 쓴다.
Kirkuk 논문이 다중공선성을 자기 한계로 언급한 지점이 정확히 여기다(2.2 #3).

## 3.3 검증 설계

**랜덤 분할은 금지다.** 토지가격은 공간자기상관이 극단적으로 강하므로, 랜덤 k-fold는
같은 골목의 옆 필지를 train과 test에 나눠 넣어 **성능을 크게 과대평가**한다. 필지 단위
데이터에서는 "이웃을 외웠는가"와 "구조를 배웠는가"를 구분하지 못한다.

권고하는 3단 검증:

1. **공간 블록 CV (필수)** — 격자(예: 2km) 또는 행정동 단위로 블록을 만들어
   블록째로 fold를 나눈다. 블록 경계에 **버퍼**를 둬서 인접 관측이 train/test에 동시에
   들어가지 않게 한다. 이것이 주 성능 지표다.
   → 랜덤 CV 성능과 공간블록 CV 성능을 **둘 다 보고**하라. 그 격차 자체가 "이 모형이
   얼마나 이웃을 외웠는지"의 측정치다. 격차가 크면 화면 신뢰도 문구를 낮춰야 한다.
2. **구(區) 단위 holdout** — 특정 구 전체를 빼고 학습해 그 구를 예측. "학습에 없던
   동네에도 통하는가"를 본다. 서울 25개 구로 leave-one-gu-out.
3. **실거래 외부 검증** — 공시지가로 학습한 모형이 **토지 실거래 ㎡당가의 순위**를
   맞히는지 본다. 수준(level)은 못 맞힌다(현실화율 때문). **순위상관(Spearman)과
   변화 방향**만 본다. 이것이 1.2의 순환성에 대한 유일한 실질적 방어다 —
   "행정 산식을 외운 것"이 아니라 "시장을 설명하는 것"임을 보이는 자리.

### 1차 출처 — 격차가 얼마나 큰가

랜덤 CV와 공간 CV의 격차는 추정치가 아니라 실측된 값이다.

- **Ploton et al. (2020)** "Spatial validation reveals poor predictive performance of
  large-scale ecological mapping models", *Nature Communications* 11:4540
  <https://pmc.ncbi.nlm.nih.gov/articles/PMC7486894>

  | 검증 방식 | R² | RMSPE |
  |---|---|---|
  | 랜덤 K-fold | **0.53** | 56.5 (19%) |
  | 공간 44-fold | **0.14** | 77.5 (26%) |
  | Buffered LOO (>100km) | 사실상 **0** | — |

- **Meyer et al. (2018)** *Environmental Modelling & Software* 101:1-9
  <https://www.sciencedirect.com/science/article/abs/pii/S1364815217310976> —
  랜덤 k-fold R² **0.90 / 0.92** vs Leave-Location-Out CV R² **0.24 / 0.49**.
  결론이 우리에게 특히 중요하다: **목표에 맞는 검증을 쓰지 않으면 성능 수치만이
  아니라 변수 선택 자체가 과최적화된다.** → 반경·지표 선택을 랜덤 CV로 하면
  **고른 반경이 틀린다**(3.2).
- **Roberts et al. (2017)** *Ecography* 40(8):913-929
  <https://nsojournals.onlinelibrary.wiley.com/doi/10.1111/ecog.02881> — 축자 권고:
  *"We recommend that block cross-validation be used wherever dependence structures
  exist in a dataset, **even if no correlation structure is visible in the fitted
  model residuals, or if the fitted models account for such correlations**."*
  그리고 목적 프레이밍: 블록 CV가 랜덤 CV보다 적절한 것은
  *"if the goal is predicting to new data or predictor space, or for selecting
  causal predictors"*. **블록 크기는 잔차 공간자기상관 range(경험적 베리오그램)를
  하한으로** 잡는다. 저자들 자신의 반대 논점도 있다 — 블록을 나누면 훈련 데이터의
  예측변수 범위가 잘려 **의도치 않은 외삽을 유발하고 보간 오차를 과대추정**할 수 있다.
- **반대 진영도 적어 둔다**: **Wadoux, Heuvelink, de Bruin & Brus (2021)**
  "Spatial cross-validation is not the right way to evaluate map accuracy",
  *Ecological Modelling* 457:109692
  <https://alexandrewadoux.github.io/assets/pdf/Wadoux_et_al_2021.pdf> — 수치실험에서
  **표준(비공간) CV의 편향이 공간 CV의 편향보다 작았다**고 보고하며, 지도 정확도의
  불편 추정은 **확률표집 + 설계기반 추론**에서 나온다고 주장한다. (구체적 편향 수치는
  PDF 추출 실패 → **미확인**.)
  → **실무적 타협**: 공간 CV 수치를 "정확도"라고 부르지 않고
  **"새 위치로 외삽할 때 기대성능의 보수적 하한"**으로 라벨링한다.
- **kNNDM** — Linnenbrink, Milà, Ludwig & Meyer (2024) *GMD* 17(15):5897-5912
  <https://gmd.copernicus.org/articles/17/5897/2024/> (오픈액세스). 우리에게 가장
  중요한 문장: 표본이 예측영역에 **랜덤/규칙적으로 분포하면 군집성 검정 후 kNNDM은
  그냥 랜덤 k-fold를 반환한다** — "random and regular samples에는 그게 적절한
  선택"이기 때문. **서울 필지는 조밀·균일하므로 우리 경우 격차가 생태학 사례보다
  작을 가능성이 실제로 있다.** 계산량도 실용적이다(4,000점에서 NNDM LOO 4.8일 →
  kNNDM 1.2분). 군집 데이터에서는 k=2보다 **k=4 또는 6**이 좋았다.
  선행: Milà et al. (2022) NNDM LOO *MEE* 13(6):1304-1316 ·
  Le Rest et al. (2014) *GEB* 23(7):811-820 (buffered LOO의 원조 —
  자기상관이 있으면 AIC 기반 변수 선택이 **과도하게 복잡한 모형**을 고른다)
- **부동산 도메인의 실측은 아직 얇다.** 우리 설계와 가장 가까운 Tepe (2024)
  *J. Geogr. Syst.* 26:511-540이 정확히 3종 CV를 비교하지만 **수치가 유료로
  미확인**이다(2.6). 반대 방향 사례로, hex 격자 블록 홀드아웃을 부동산 조망 모형에
  적용했을 때 격차가 **소폭**이었다는 보고가 있다(R² 0.758→0.748, 0.649→0.643;
  arXiv:2606.15198 <https://arxiv.org/abs/2606.15198>).
  → **격차가 작을 수도 있다. 그러나 작다는 것은 측정해서 보여야 하는 것이고, 그것이
  공간 CV를 돌려 볼 이유다.**

### ⭐ AOA — "이 필지에 대해 모형이 발언할 자격이 있는가"

**Meyer & Pebesma (2021)** "Predicting into unknown space? Estimating the area of
applicability of spatial prediction models", *MEE* 12(9):1620-1633
<https://besjournals.onlinelibrary.wiley.com/doi/abs/10.1111/2041-210X.13650>
(구현 <https://github.com/HannaMeyer/MEE_AOA>, R `CAST::aoa`)

예측변수 공간의 거리 기반 **dissimilarity index (DI)**를 변수 중요도로 가중해 계산하고,
임계값을 훈련 데이터 내부 DI 분포에서 유도한다. 검증 결과 **AOA 내부의 예측오차는 CV
오차와 일치하고 AOA 밖에서는 크게 벌어진다.** 저자 권고는 지도를 낼 때 불확실성 지표와
**AOA를 함께 공개**하라는 것이다.

> **이것이 "뜰 자리"에 정확히 맞는 도구다.** "길 구조는 좋은데 가격에 아직 반영되지
> 않은 곳"을 찍는 일은 **본질적으로 훈련 분포에서 벗어난 지점을 찍는 일**이다. AOA는
> 그 위험을 필지 단위로 계량해 UI에 표시할 수 있게 해 준다 — 모형이 말할 자격이 없는
> 필지는 숫자를 내지 않고 "판단 불가"로 표시한다.

### 권장 조합

1. **베리오그램으로 잔차 공간자기상관 range를 먼저 추정** → 블록 크기·버퍼 반경 결정.
2. **한 개가 아니라 네 개 수치를 병기한다** — 랜덤 k-fold(보간 상한) / 행정동
   GroupKFold(전이성) / 공간 블록·buffered LOO(외삽 하한) / kNNDM.
   **세 숫자가 벌어지는 폭 자체가 결과물이다** (Ploton의 0.53 vs 0.14가 그 폭).
3. **AOA/DI를 필지 단위로 계산**해 발언권 없는 필지를 UI에서 구분(3.4).
4. 구현: Python은 scikit-learn `GroupKFold`/`LeaveOneGroupOut`(동 id를 `groups`로)
   <https://scikit-learn.org/stable/modules/cross_validation.html#cross-validation-iterators-for-grouped-data>
   로 대부분 되고, 이 분야 전용 도구는 사실상 R에 있다 — `blockCV`
   <https://github.com/rvalavi/blockCV>, `CAST` <https://hannameyer.github.io/CAST/>,
   `mlr3spatiotempcv`.

**counterfactual 자체의 검증 (2.7이 열어 주는 것)**: 과거 OSM 스냅샷으로
"2019년 시점에서 2020년 도로 신설을 예측"하고 실제 2020년 공시지가 변화와 비교하는
**백테스트**가 가능하다. 2.5가 확인한 대로 **문헌이 지지하는 형태는 "예측"이 아니라
"예측 + 검증"(Ahlfeldt 2013)**이므로, 이 백테스트가 있으면 화면에 신뢰구간을 근거를
갖고 표시할 수 있고 없으면 표시할 수 없다.

## 3.4 화면 표기 — 무엇이 정직한가

### 하지 말아야 하는 표기

- ❌ "이 필지는 **12.4% 상승**합니다" — 점 추정, 인과 주장, 시점 불명. 셋 다 틀렸다.
- ❌ "AI가 예측한 **미래 지가**" — 우리 모형은 미래를 보지 않는다. 균형 관계를 본다.
- ❌ R²를 "정확도"로 제시 — 1.2의 순환성 때문에 R²는 부풀려져 있다.

### 권고 표기

1. **범위로만.** 점 추정 금지. 모형의 예측구간 + 반경/모드 사양에 따른 변동을 합쳐
   범위로 낸다. 이 프로젝트에 이미 같은 방향의 결정이 있다 —
   `docs/GROWTHFACTOR_GAP.md`의 "P2 — 예측을 범위로: 점이 아니라 밴드(P25~P75)".
2. **이름을 정확히 — 그리고 "상한"이라고 부르지 않는다.** 2.5에서 확인한 대로
   Kuminoff & Pope (2014)가 자본화가 WTP를 **과소**평가한 사례를 보였으므로
   "자본화 상한"이라는 라벨은 근거가 없다. 또 2.4에서 확인한 대로 우리 계수는
   상향 편의(미관측 입지질)와 감쇠 편의(측정오차)를 동시에 받아 **편의의 부호조차
   사전에 알 수 없다.** 따라서 상한·하한 어느 쪽으로도 부르면 안 된다.
   권고 라벨은 **"장기 균형 지가 구배가 함의하는 값"**이고, 사용자용 문구로는
   **"길 구조가 이 정도인 다른 필지들의 현재 지가 수준"(벤치마크)**이다.
   "언제"를 말하지 않는 것이 핵심이다 — 우리 모형에는 시간 축이 없다(2.5).
   Bishop et al. (2020)의 보고 기준에 따라 **한계적 변화용 추정치인지 비한계적
   변화용인지**도 함께 밝힌다.
   불확실성 폭의 근거로 Rennert (2022)의 **−7.4%p ~ +9.6%p**(2.4)를 병기하는 것이
   정직하다 — 이 분야 200여 사례에서 **부호까지 뒤집힌다**는 사실을 숨기지 않는다.
3. **분해해서 보여준다.** 사용자가 검증할 수 있어야 한다:
   Integration 현재값 → 예정 도로 반영 후 값 → Δ → 계수 → 범위. GrowthFactor 방식의
   "TRACED TO SOURCE"이며 이미 이 저장소의 방향이다(`GROWTHFACTOR_GAP.md`).
4. **선반영을 분리해 표기.** "이미 가격에 들어와 있는 정도"와 "아직 안 들어온 정도"를
   나눠 보여준다. `DEVELOPMENT_INTELLIGENCE_SOURCES.md` 2.4가 이것을 이 프로젝트의
   빈칸으로 지목했고, 2.7의 횡단면 계수 대 패널 계수 격차가 그 추정치를 준다.
5. **면책 문구 — 국내 선례가 있다.** 부동산플래닛 AI 추정가 안내
   (<https://www.bdsplanet.com/etc/assumeReadme.ytp>)의 문구를 1차 확인했다:
   *"부동산플래닛 AI시세는 감정평가금액이 아니며 감정평가 및 금융거래의 기초자료로
   사용할 수 없습니다"*, *"가격의 정확성이 보증되지 않습니다"*, 그리고 거래 전
   *"부동산 중개인 또는 감정평가사 등 전문가"* 상담 권고. 정확도도
   *"오차범위 20% 이내에서 약 70%의 정확도"*로 **범위와 적중률로** 제시한다.
   → **최소한 이 수준의 문구는 우리도 필요하다**(3.8의 법적 미확인 항목과 연결).
   그리고 정확도를 "오차범위 X% 이내 Y%" 형식으로 내는 관행이 국내에 이미 있으므로,
   우리도 R² 대신 이 형식을 쓰는 것이 사용자에게 정직하고 비교 가능하다.
6. **공시지가 기준임을 명시.** "공시지가 기준 추정이며 실거래가와 다릅니다."
   1.2·1.3을 사용자에게 숨기면 안 된다.

## 3.5 v1 최소 범위

작동하는 최소 버전은 **모델이 아니라 파이프라인**이다. 아래 순서를 권고한다.

**v1 (여기까지가 "작동하는 최소")**

1. **개별공시지가 배치 적재** — PNU 키로 `parcel_land_price(pnu, year, price_per_sqm)`.
   `parcel_centroids`가 이미 PNU 키라 조인이 공짜다(1.5).
2. **전 서울 도로 세그먼트 Integration 배치 1회** — 필지별 온라인 계산은 5초 룰과
   충돌한다(3.0). 세그먼트별 값을 테이블에 저장하고, 필지는 **최근접/접면 세그먼트의
   값을 물려받는다**. 반경은 v1에서 하나(walk 500m — 기존 "뜰 자리"와 같은 값)만
   먼저 만들어 파이프라인을 세운다.
3. **용도지역 + 도로접면 등급 + 도로거리 통제변수 생성** — 1.2의 순환성 방어. 이걸
   빼면 v1의 계수는 해석 불가이므로 **v2로 미룰 수 없다**.
4. **로그-선형 헤도닉 1개 + 동 고정효과 + 공간 클러스터 표준오차.** 모델 1개다.
5. **공간블록 CV + 랜덤 CV 둘 다 보고.** 격차를 기록한다.
6. **기존 `simulation.py`에 연결** — 이미 가상 도로를 splice해 before/after
   Integration delta를 계산한다. 그 delta를 계수에 넣어
   **Δ = f(after) − f(before)** 로 계산한다(2.1의 오류 회피).
7. **범위 + 자본화 상한 표기 + 면책 문구**(3.4).

**v2**

- 반경·모드 다중화(500/800/1000 × walk/drive)와 공간블록 CV로 반경 선택
- 토지 실거래 ingest → 외부 순위 검증(3.3-3)
- **2.7 패널** — OSM 과거 스냅샷 × 공시지가 연도별 1차 차분. 여기서 처음으로
  "변화-대-변화" 계수가 나오고, 그때 비로소 화면 문구를 "자본화 상한"에서
  조금 더 강하게 쓸 수 있다.
- GWR로 계수 비정상성 진단

**v1에서 명시적으로 하지 않을 것**

- SAR/SEM/SDM (3.2의 근거)
- GBM/LightGBM (2.6) — v1에서 tree 모델을 쓰면 counterfactual 대입이 곧바로 막힌다
- 개별 필지 단위 정밀 예측 주장 — 조정훈·문태헌(2016)조차 "지구 단위 매크로"라고
  선을 그었다. v1은 **동/블록 수준**으로 말하고 필지는 참고로 표시한다.

## 3.6 착수 전 반드시 실측해야 할 항목

전부 **지금 미확인**이며, 이 중 하나라도 어긋나면 설계가 바뀐다.

**데이터**

1. **`parcel_centroids`에 서울 필지가 실제로 몇 건 들어 있는가.** 연속지적도 적재가
   서울 전역인지 일부인지 확인 (`SELECT count(*) FROM parcel_centroids`). 개별공시지가
   조인률이 여기서 결정된다.
2. **개별공시지가 표준데이터의 실제 필드에 PNU(고유번호)와 기준연도가 있는가**, 그리고
   **몇 개 연도를 받을 수 있는가.** 2.7의 패널 설계가 여기에 전부 달렸다.
3. **`roads_clean`의 서울 세그먼트 수.** 전 서울 세그먼트 Integration 배치의 실행
   시간이 여기서 나온다. 반경 800m 1회가 고정비 8.5초였다는 실측(3.0)을 감안하면
   세그먼트 수 × 반경 수가 배치 예산을 정한다. **감당 못 하면 격자 샘플링으로
   전략을 바꿔야 한다.**
4. **`roads_clean`에 도로 폭(width) 속성이 있는가.** 없으면 도로접면 등급을 만들 수
   없고(3.1-A), OSM `highway` 등급으로 대리해야 한다 — 그러면 비준표 통제가 약해진다.
5. **RTMS 토지 매매 엔드포인트의 실제 필드와 서울 연간 건수.**
6. **실거래의 `geo_precision='parcel'` 달성 비율.** RTMS가 지번을 마스킹하면
   `_build_pnu`가 `None`을 반환한다(`ingest_real_estate_trades.py`) — 필지 단위 검증에
   쓸 수 있는 표본이 실제로 얼마인지 세야 한다.
7. **OSM 연도별 서울 세그먼트 수·총 연장 추이** — 2.7의 매핑 잡음 진단. 급증 연도를
   찾아 제외 구간을 정한다.

**모형**

8. **비준표 통제변수를 넣었을 때 Integration이 여전히 유의한가.** 이 프로젝트 전체가
   이 한 번의 회귀에 걸려 있다. **유의하지 않으면 이 방향은 접어야 한다** — 그리고
   그것을 먼저 확인하는 것이 가장 값싼 실험이다.
   → v1을 만들기 전에 **동 하나 또는 구 하나**에서 이 회귀를 먼저 돌려 볼 것.
9. **랜덤 CV와 공간블록 CV의 성능 격차.** 크면 화면 문구를 더 보수적으로 써야 한다.
10. **Integration 분포에서 예정 도로가 만드는 Δ가 어디에 놓이는가.** 학습 분포 안이면
    선형 모형의 외삽 부담이 작다. 분포 밖으로 크게 나가면 어떤 모형도 신뢰할 수
    없으므로 그 케이스는 **"예측 불가"로 표시**해야 한다.

11. **1차 차분 후에도 신호가 남는가.** 2.7의 함정 (나) — Iacono & Levinson (2011)이
    같은 설계로 실패했다. 구 하나 × 3~4개 연도로 먼저 확인한다.
12. **OSM 연도별 스냅샷에서 계산한 Integration이 현재 `roads_clean` 값과 정합하는가.**
    같은 파이프라인(`graph_builder` → `compute_metrics`)을 과거 스냅샷에 돌렸을 때
    현재 값이 재현되는지 확인. 재현되지 않으면 시계열 비교가 무의미하다.

**사람이 처리해야 할 것 (코드로 안 되는 것)**

13. 🔴 **서울시 데이터전략과에 개별공시지가 데이터 문의** — OA-1180(1990~2026,
    공공누리 1유형)이 2026-08-18 종료됐다. 이전 여부·제공신청 가능 여부를 확인한다.
    **이 문서에서 가장 급한 후속 조치다.** (`DEVELOPMENT_INTELLIGENCE_SOURCES.md`
    1.2가 기록한 OA-2253 종료와 같은 시기이므로 함께 문의할 수 있다.)
14. **개별공시지가 API 운영단계 심의승인 신청** (data.go.kr 15029071/15124014).
15. 감정평가법상 예측 지가 표시의 적법성(3.8).
16. OSM **ODbL 1.0**의 share-alike가 우리 파생 DB·화면 표시에 미치는 범위(2.7).

---

## 3.7 이 조사에서 원문을 확보하지 못한 것 (정직하게 남긴다)

우선순위 순. 확보하면 이 문서의 해당 절을 갱신해야 한다.

1. **Tepe (2024)** *J. Geogr. Syst.* 26:511-540 — **우리 설계와 가장 가까운 논문**
   (단면 지가 + 공간피처 + 트리모형 + 3종 CV 비교). 세 CV별 R²/MSE가 유료로 미확인.
   이 수치가 3.3의 "격차가 얼마나 될지"를 부동산 도메인에서 직접 알려 준다.
2. **Xiao·Webster·Orford (2016) 게재본** (*Urban Studies*) — closed access, OA 사본
   없음. 박사논문 6장으로 대체했으나 **게재본이 사양을 바꿨는지 미확인**(특히 공간
   진단·VIF 추가 여부).
3. **김혜영·전철민 (2012)** 한국지리정보학회지 15(2):35-45 — 원문 PDF(10.7MB)를
   확보했으나 **스캔 이미지로 텍스트 추출 실패**. R²·계수·변수 목록 미확인.
   **국내·서울·지가·space syntax·GWR·MAUP를 한 번에 다룬 유일한 선례이므로 사람이
   PDF를 열어 보는 값이 크다.**
4. **Doan & Bui (2026)** *TR Part A* 212:105134 — 제목이 우리 질문 그 자체
   ("Do better street configurations mean higher land prices?"), **지가**를 종속변수로
   ML+GeoShapley를 쓴다. closed access로 초록조차 권위 있는 출처에서 확보 실패.
5. **Bishop et al. (2020)** REEP 14(2):260-281 본문 전체 — best-practice 축자 인용.
6. **Roberts et al. (2017)** *Ecography* 권고 박스 축자 표현 (Wiley 403).
7. **Iacono & Levinson (2011)** — 2.7의 실패 선례. 서지사항까지만 확인했고 **원문
   미확인**.
8. **감정평가 및 감정평가사에 관한 법률** 조문 (제2조 정의, 무자격 감정평가 금지 및
   벌칙) — law.go.kr·LBOX 모두 본문 확보 실패(3.8).
9. 서울 필지 수의 `.go.kr` 1차 확정 (1.3) · 토지(표준지) 현실화율 65.5%의 `.go.kr`
   축자 문장 (1.3) · 서울 순수토지 절대 건수 (1.4).

## 3.8 별도 확인이 필요한 법적 쟁점 (미확인)

상업 서비스가 필지별 **예측 지가**를 숫자로 표시하는 것이 **감정평가 및 감정평가사에
관한 법률**의 "감정평가업"(타인의 의뢰에 따라 보수를 받고 토지등의 감정평가를 업으로
행하는 것)에 저촉되는지 여부는 **1차 확인에 실패했다 → 미확인.**
국가법령정보센터(<https://law.go.kr>)와 LBOX가 조사 환경에서 본문을 반환하지 않았다
(JS 렌더링 / 403). 관련 조문(제2조 정의, 무자격 감정평가 금지 및 벌칙 조항)의 정확한
번호와 문구를 확인하지 못했으므로 **어떤 단정도 하지 않는다.**

이것은 코드로 해결할 문제가 아니라 **사람이 확인할 문제**다. 최소한 화면 문구를
"감정평가가 아니며 참고용 추정치"로 명시하는 것은 어느 경우에도 필요하다(3.4).
