from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_ROOT.parent

# Anchored to this file, not the working directory. These used to be the
# relative paths ".env"/"../.env"/"../../.env", which only resolved when the
# process happened to be started from backend/ — `uvicorn --app-dir backend`
# from the repo root found no .env at all and silently fell back to the
# defaults below, pointing DATABASE_URL at port 5432 (a stale, near-empty
# copy) instead of the real 5433 instance. Every analysis then 503'd on a
# missing roads_clean table. Order is unchanged: later files win.
_ENV_FILES = (
    _BACKEND_ROOT / ".env",
    _REPO_ROOT / ".env",
    _REPO_ROOT.parent / ".env",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql://spacesyntax:spacesyntax@localhost:5432/spacesyntax"
    # Pooled PostGIS connections per process. Comfortably above anyio's default
    # 40-thread run_in_threadpool limiter plus the handful of event-loop-side
    # queries, so a request never queues on the pool in practice, and well under
    # Postgres' default max_connections=100 for a couple of uvicorn workers.
    db_pool_max_connections: int = 48
    kakao_rest_key: str = ""
    data_go_api_key: str = ""
    vworld_api_key: str = ""
    vworld_domain: str = ""
    sgis_consumer_key: str = ""
    sgis_consumer_secret: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-5.4-mini"
    # 서사(사용자에게 보이는 해석 문장) 전용 모델. 파싱은 위 openai_model을
    # 그대로 쓴다 — 의도 파싱은 mini로 충분하고 응답 5초 예산의 임계 경로에
    # 있지만, 서사는 분석과 분리돼 나중에 도착하므로 품질 좋은 모델을 쓸
    # 여유가 있다. A/B 실측(2026-08-08, 신촌역 카페 동일 질문): mini는 결론이
    # 하나 마나 한 말("신중해야")이었고 gpt-5.4는 판단("될 자리와 안 될 자리
    # 차이가 큰 입지")과 근거를 집은 행동 제안을 냈다. gpt-5.6은 품질이 비슷
    # 한데 1.5배 느렸다(평균 9.7s vs 6.6s). 비우면 openai_model로 폴백.
    openai_narrative_model: str = "gpt-5.4"
    # 2026-11-06: AI 상담(도구 루프·스트리밍)에 쓰는 모델. 소유자 지적 — "그냥 GPT에
    # 물어보는 것보다 답을 못한다, 정해진 틀로만 답한다." 상담은 이 모델이 대화를
    # 이끌고 엔진을 도구로 부른다. gpt-6.1-sol은 Chat Completions에서 함수 호출을
    # 지원하지 않아 Responses API로 부른다(llm_client.AgentConversation).
    # 비우면 openai_narrative_model로 폴백. 비교용으로 gpt-6.1-terra 등으로 바꿀 수 있다.
    openai_agent_model: str = "gpt-6.1-sol"
    # 상담 모델의 추론 강도. 6.1 Sol은 none을 받지 않는다(low/medium/high).
    # 빈 문자열이면 보내지 않는다(추론 모델이 아닌 경우).
    openai_agent_reasoning_effort: str = "low"
    # LLM 제공자 — 나중에 로컬 LLM으로 옮길 때 여기만 바꾼다.
    # "openai": 현재 기본값 | "local": OpenAI 호환 로컬 서버 | "off": 서술 비활성
    # (off여도 모든 기능은 규칙 기반 문구로 동작한다. 숫자는 LLM과 무관하다.)
    # 도구를 루프로 돌리는 상담 경로(services/agent_loop.py). 모델이 도구를 직접
    # 고르고, 결과를 보고 한 번 더 고칠 수 있다. 켜면 응답이 느려지는 대신 답이
    # 깊어진다. 기본 꺼짐 — 라이브에서 한 번 확인한 뒤 켠다(로컬 OPENAI 키가
    # 만료돼 있어 개발기에서 end-to-end 확인이 안 된다).
    agent_loop_enabled: bool = False
    llm_provider: str = "openai"
    llm_base_url: str = ""   # 예: http://localhost:11434/v1 (Ollama)
    llm_model: str = ""      # 예: qwen2.5:14b-instruct
    llm_api_key: str = ""    # 로컬 서버가 키를 요구할 때만
    # Hard 422 guard for road fetches. Delivery-territory analysis fetches a
    # 4500m radius, which is ~20.5k segments in dense Seoul (Sinchon); the
    # STRtree-optimized build_graph handles 21k in ~23s, so 25k gives headroom
    # without letting pathological fetches (8km bands in dense areas) through.
    # Interactive paths are unaffected: analyze/site-score cap at
    # interactive_max_segments long before this.
    max_segments: int = 25_000
    interactive_max_segments: int = 1000
    # Road-network resolution control for wide competition/isochrone fetches.
    # Above this fetch radius, drop to arterial roads only. A 4~8km drive reach
    # is decided by arterials; residential/service alleys add ~78% more segments
    # (Hongdae 8km: 31.5k drivable vs 5.8k arterial) with negligible accuracy
    # gain — and it is exactly those alleys that tip dense-Seoul fetches past
    # max_segments into a 422. Small analyze/simulate/site-score fetches stay
    # well under this, so their full-resolution networks are untouched.
    arterial_only_radius_threshold_m: int = 2000
    # OSM highway classes treated as arterial (through-traffic carrying), incl.
    # their _link ramps. Everything else (residential, service, unclassified,
    # living_street, …) is an alley and gets dropped past the threshold.
    arterial_highway_types: str = (
        "motorway,trunk,primary,secondary,tertiary,"
        "motorway_link,trunk_link,primary_link,secondary_link,tertiary_link"
    )
    # Vehicular (drive/moto) reach is approximated as a Euclidean disc of
    # radius speed×minutes×factor instead of a per-request network Dijkstra:
    # multi-km graphs cannot be built inside the 5s budget, and the product
    # decision is that coarse coverage is enough for these bands. 0.75 because
    # on an urban grid the straight-line distance covered by a network path is
    # typically ~0.7–0.8× the path length (pure grid worst case ≈ 1/√2 ≈ 0.71).
    network_detour_factor: float = 0.75
    betweenness_sample_size: int = 500
    depthmapx_enabled: bool = False
    depthmapx_command: str = ""
    depthmapx_timeout_seconds: int = 180
    # Strict mode: when DepthmapX is enabled but fails, return an error instead
    # of silently serving Python-approximation values (MVP policy).
    depthmapx_strict: bool = False
    analyze_compute_timeout_seconds: int = 120
    analysis_cache_enabled: bool = True
    analysis_cache_network_version: str = "roads-clean-v2-seoul-full"
    analysis_cache_version: str = "angular-analysis-v1"
    analysis_cache_max_entries: int = 10_000
    data_seoul_go_api_key: str = ""
    opinet_api_key: str = ""

    # --- API access control ---
    # Shared secret required in the X-API-Key header. Empty (the default) leaves
    # auth disabled so local dev and existing tests keep working untouched; set
    # it in the deployed environment to close the API off.
    api_access_token: str = ""
    # Comma-separated CORS allowlist. Replaces the previous "*", which is also
    # invalid to combine with allow_credentials=True per the CORS spec.
    cors_allow_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    # Per-client sliding-window rate limits (requests per minute).
    # 0 disables the limit.
    rate_limit_per_minute: int = 60
    # /api/chat fans out to OpenAI on every call, so it gets its own tighter
    # budget on top of the general limit.
    chat_rate_limit_per_minute: int = 6
    # Off by default: nothing in front of this API (see docker-compose.yml —
    # no nginx/CDN/LB) can be trusted to have set X-Forwarded-For itself, so a
    # caller could otherwise mint a fresh rate-limit bucket per request just by
    # sending a different value. Only flip this on once a real reverse proxy
    # that overwrites (not appends to) the header sits in front of the app.
    trust_forwarded_for: bool = False

    # --- Observability ---
    # Root log level. INFO surfaces the per-request completion lines and the
    # analyze step timings; drop to WARNING if the volume ever becomes a problem.
    log_level: str = "INFO"
    # Requests slower than this are logged at WARNING instead of INFO. Defaults
    # to the project's hard requirement — every analysis must return within 5s —
    # so SLA breaches are greppable rather than buried in the INFO stream.
    # 0 disables the escalation.
    slow_request_ms: int = 5000

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def arterial_highway_list(self) -> list[str]:
        return [h.strip() for h in self.arterial_highway_types.split(",") if h.strip()]


settings = Settings()
