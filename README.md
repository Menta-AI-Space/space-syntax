# Space Syntax Web Analyzer — Korea

Web application for analyzing **Choice** and **Integration** (Space Syntax metrics) on Korean road networks within a 500 m or 1 km radius of any address.

## Architecture

```
User → Kakao Maps Frontend → FastAPI Backend → PostGIS (OSM data)
                                    ↓
                              NetworkX analysis
```

- **Data:** MVP uses a 서대문구 extract (`data/osm/seoul-small.osm.pbf`); full Korea import is optional
- **Database:** PostGIS (PostgreSQL + PostGIS extension)
- **Backend:** Python + FastAPI + NetworkX
- **Frontend:** Kakao Maps JavaScript API + Polylines
- **Geocoding:** Kakao Local REST API

## Prerequisites

| Tool | Purpose |
|------|---------|
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) | PostGIS + backend containers |
| [osm2pgsql](https://osm2pgsql.org/doc/install.html) | One-time PBF → PostGIS import |
| [Kakao Developers](https://developers.kakao.com/) account | Geocoding + map tiles |

### Install osm2pgsql (Windows)

### Install osm2pgsql + osmium (pick one)

**Option A — Docker only (recommended on Windows, no Chocolatey):**

Local install not required. Scripts auto-use Docker when `osmium` / `osm2pgsql` are missing.

```powershell
docker compose up -d db
.\scripts\prepare_sample_osm.ps1
.\scripts\import_osm.ps1
```

**Option B — Chocolatey:**

```powershell
choco install osm2pgsql osmium-tool
```

## Quick Start

### 1. Clone / open project

```powershell
cd C:\dev\space-syntax
```

### 2. Configure environment

```powershell
copy .env.example .env
```

Edit `.env`:

- `KAKAO_REST_KEY` — REST API key (Local API enabled)
- `OSM_PBF_PATH` — MVP sample PBF, 서대문구 bbox (default: `data/osm/seoul-small.osm.pbf`)
- `OSM_PBF_FULL_PATH` — full/regional PBF for sample extraction or full import

Edit `frontend/index.html` — replace `YOUR_KAKAO_JS_KEY` with your **JavaScript** key.

### 3. Kakao Developer setup

1. Go to [Kakao Developers](https://developers.kakao.com/) → create an application
2. **Local API** → enable → copy REST API key → `KAKAO_REST_KEY` in `.env`
3. **Kakao Map (JavaScript)** → enable → copy JavaScript key → `frontend/index.html`
4. **Platform → Web** → register site domain: `http://localhost:8000`

### 4. Start PostGIS

```powershell
docker compose up -d db
```

Wait until healthy:

```powershell
docker compose ps
```

### 5. Import OSM data (MVP — a few minutes)

Download a full/regional PBF once (Geofabrik `south-korea-latest.osm.pbf`), set `OSM_PBF_FULL_PATH` in `.env`, start PostGIS, then:

```powershell
docker compose up -d db
.\scripts\prepare_sample_osm.ps1   # extracts 서대문구 bbox → data/osm/seoul-small.osm.pbf
.\scripts\import_osm.ps1           # imports the small sample (Docker fallback if needed)
```

`import_osm.ps1` reads `OSM_PBF_PATH` from `.env` (default: `data/osm/seoul-small.osm.pbf`).

For **full South Korea** import later (30–90+ min):

```powershell
.\scripts\import_osm_full.ps1
```

Both scripts build the **`roads_clean`** service table with EPSG:5179 indexes from `scripts/init_db.sql`.

### 5.1 Import SGIS grid stats (optional — radius-scoped demand, a few minutes)

Population/household/worker radius aggregation reads from PostGIS
(`sgis_grid_cells` / `sgis_grid_stats`), not from a local folder. Merge a SGIS
census CSV + boundary shapefile bundle into one folder (see
`backend/app/services/sgis_grid.py` docstring for layout), then load it once:

```powershell
cd backend
.venv\Scripts\python.exe -m scripts.import_sgis_grid --data-dir "C:\path\to\sgis-grid"
```

Re-run after a fresh SGIS download — the script truncates and reloads both
tables. Without this, radius demand falls back to a density-based estimate.

### 6. Start the application

```powershell
docker compose up --build
```

Open http://localhost:8000

### 6.1 Warm the persistent Space Syntax cache

`/api/analyze` stores completed baseline results in PostgreSQL and reuses them for
matching center/radius/mode requests. Road-change simulation also reuses the
cached baseline and computes only the modified network. Apply the migration
explicitly in managed environments (the API also creates the table lazily):

```powershell
psql $env:DATABASE_URL -f scripts/migrations/003_analysis_cache.sql
```

Warm selected Seoul grid points through the running API. Start coarse or use a
small bbox/`--limit` first because every cold point executes DepthmapX once:

```powershell
cd backend
python -m tools.precompute_analysis_cache --step-m 500 --radii 500 1000 --workers 4 --dry-run
python -m tools.precompute_analysis_cache --step-m 500 --radii 500 1000 --workers 4
```

Set `ANALYSIS_CACHE_NETWORK_VERSION` to a new value whenever `roads_clean` is
rebuilt. New requests then use a clean namespace while old cache rows remain
available for rollback or later pruning.

### 7. Run analysis

1. Enter a Korean address in 서대문구 (e.g. `서울 서대문구 연세로` or `신촌역`)
2. Select radius: **500 m** or **1 km**
3. Click **Analyze**
4. Toggle **Choice** / **Integration** to recolor road segments
5. View summary table by road type (highway tag)

## Local development (without Docker backend)

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# From project root, with PostGIS running:
$env:DATABASE_URL = "postgresql://spacesyntax:spacesyntax@localhost:5432/spacesyntax"
uvicorn app.main:app --reload --port 8000
```

Mount frontend manually or symlink — Docker setup serves `frontend/` at `/`.

## API

### `POST /api/analyze`

```json
{
  "address": "서울특별시 종로구 세종로 1",
  "radius_m": 500
}
```

Response includes GeoJSON segments with `choice` and `integration` properties, plus `summary_by_highway`.

### `POST /api/site-score` (입지 진단 — MVP 1)

Click a point on the map → side-panel site diagnosis with sub-scores, expanded **배후 수요** (age/gender/housing/industry/Seoul rank), and natural-language explanation.

```json
{
  "lat": 37.5585,
  "lng": 126.9368,
  "radius_m": 500,
  "inds_mcls_cd": "I212",
  "inds_scls_cd": "I21201"
}
```

진단 업종은 사이드 패널 상단에서 선택합니다. SBIZ 공식 업종 분류(중·소분류)를 `GET /api/upjong/tree`로 제공하며, 경쟁 집계는 **업종 코드** 기준으로 매칭합니다. 레거시 `business_type: "cafe"` 프리셋도 호환됩니다.

| Sub-score / block | Source |
|-------------------|--------|
| 도로 구조 (상위%) | Own space-syntax engine (walk mode) |
| 대중교통 접근 | Kakao Local category search (SW8 지하철역) |
| 배후 수요 (기본) | SGIS 행정동 인구·종사자·가구 |
| 배후 수요 (상세) | SGIS `pplsummary` (연령), `mfratiosummary` (성별), `housesummary` (주거), `company`+산업분류 (종사) |
| 서울 분위 | SGIS 서울 424동 캐시 대비 상위 n% |
| 아파트 시세 proxy | RTMS AptTrade 매매 ㎡당 중앙값 (임대료 아님) |
| 연령 적합도 | Rule-based demographic fit per business archetype |
| 경쟁 강도 | SBIZ 반경 내 동일 업종(코드) 점포 |

**Limitations (shown in UI):** demand stats are **행정동 (census)**, not a true 500 m catchment; apartment values are **sale transaction medians**, not rent quotes.

The explanation uses the OpenAI API (`OPENAI_API_KEY`) when configured, otherwise a rule-based Korean template. Missing data sources degrade gracefully (warnings + weight renormalization).

### `GET /api/health`

Health check endpoint.

### Real estate APIs (backend services)

The pp.services.real_estate module fetches **data.go.kr** RTMS trades (commercial, apartment, officetel). Covered by unit tests; no HTTP route in this branch.

| Service | API | Property type |
|---------|-----|---------------|
| RTMSDataSvcNrgTrade | commercial RTMS | commercial |
| RTMSDataSvcAptTrade | apartment RTMS | partment |
| RTMSDataSvcOffiTrade | officetel RTMS | officetel |
| SBIZ storeListInRadius | SBIZ stores in radius | analyze commercial block |

### Commercial district metrics (상권 분석)

When you run **Analyze**, the backend also queries [소상공인시장진흥공단 상가(상권)정보](https://www.data.go.kr/data/15012005/openapi.do) (`storeListInRadius`) at the same center/radius and returns a commercial block:

| Metric | Method |
|--------|--------|
| **업종밀집도** | Store counts per middle/small upjong + stores/km² |
| **업종다양성** | Shannon entropy on middle-upjong distribution (normalized 0–1) |
| **공실추정** | MVP heuristic: building occupancy vs assumed 2.5 retail units/building |

Uses the same `DATA_GO_API_KEY`; activate **storeListInRadius** (and related endpoints) on data.go.kr. API failures appear in `warnings` without blocking space-syntax analysis.

## Data Model

| Table | Role |
|-------|------|
| `planet_osm_line` | Raw OSM import (osm2pgsql) |
| `roads_clean` | Service-layer roads with `geom` (4326), `geom_5179` (meters), `length_m` |
| `analysis_cache` | Versioned persistent `/api/analyze` baseline GeoJSON and metrics cache |

Spatial queries use **EPSG:5179** for accurate 500 m / 1 km radius filters.

## Metrics

| Metric | Implementation |
|--------|----------------|
| **Choice** | Edge betweenness centrality (`edge_betweenness_centrality`) |
| **Integration** | Node closeness → averaged to each edge `(u+v)/2` |

Edge weights = road segment length in meters (precomputed in `roads_clean`). Metrics are computed on the largest connected component if the subgraph is fragmented.

## Kakao API Security

- **REST key** → `.env` only, used by FastAPI backend (never expose in frontend)
- **JavaScript key** → `frontend/index.html` (expected for Kakao Maps; restrict allowed domains in Kakao Developer console)
- Register `http://localhost:8000` before testing locally

## Project Structure

```
space-syntax/
├── docker-compose.yml
├── .env.example
├── data/osm/               # Local sample extracts (gitignored *.pbf)
├── scripts/
│   ├── prepare_sample_osm.ps1  # Extract 서대문구 bbox from full PBF
│   ├── import_osm.ps1          # MVP import (small sample)
│   ├── import_osm_full.ps1     # Full/regional import
│   └── init_db.sql             # Post-import indexes
├── backend/
│   └── app/
│       ├── main.py
│       ├── routers/analyze.py
│       └── services/       # geocoding, spatial query, graph, metrics
└── frontend/
    ├── index.html
    ├── css/style.css
    └── js/                 # app, map, table
```

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Kakao map blank | Register `http://localhost:8000` in Kakao app platform settings |
| Geocoding 401 | Check `KAKAO_REST_KEY` in `.env` |
| No roads found | Ensure OSM import completed; try a denser urban address |
| Import slow/fails | Increase Docker memory; reduce `-C` cache in import script |
| Too many segments | Use 500 m radius or increase `MAX_SEGMENTS` in config |

## License

MIT — OSM data © OpenStreetMap contributors (ODbL)
