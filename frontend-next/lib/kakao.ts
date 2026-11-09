const KAKAO_SDK_URL = "https://dapi.kakao.com/v2/maps/sdk.js";
const LOAD_TIMEOUT_MS = 15_000;

function getKakaoKey(): string {
  const key = process.env.NEXT_PUBLIC_KAKAO_JS_KEY;
  if (!key || key === "your_kakao_javascript_key_here") {
    throw new Error("NEXT_PUBLIC_KAKAO_JS_KEY가 설정되지 않았습니다. frontend-next/.env.local 파일을 확인하세요.");
  }
  return key;
}

function waitForKakaoMaps(): Promise<void> {
  return new Promise((resolve, reject) => {
    if (window.kakao?.maps) {
      window.kakao.maps.load(() => resolve());
      return;
    }
    const started = Date.now();
    const timer = window.setInterval(() => {
      if (window.kakao?.maps) {
        window.clearInterval(timer);
        window.kakao.maps.load(() => resolve());
        return;
      }
      if (Date.now() - started > LOAD_TIMEOUT_MS) {
        window.clearInterval(timer);
        // 화면(상담 미니 지도 등)이 err.message를 그대로 띄운다 — 개발용 말은 콘솔에만
        // (2026-11-07 UX 자동 개선, 느린 회선에서 「Kakao Maps SDK 로드 시간이…」가 떴다).
        console.error("Kakao Maps SDK 로드 시간이 초과되었습니다.");
        reject(new Error("지도를 불러오는 데 시간이 너무 걸립니다 — 인터넷 연결을 확인해 주세요."));
      }
    }, 50);
  });
}

let loadPromise: Promise<void> | null = null;

export function loadKakaoMaps(): Promise<void> {
  if (loadPromise) return loadPromise;
  const attempt: Promise<void> = new Promise((resolve, reject) => {
    if (window.kakao?.maps) {
      window.kakao.maps.load(() => resolve());
      return;
    }
    const existing = document.querySelector<HTMLScriptElement>('script[data-kakao-sdk="true"]');
    if (existing) {
      existing.addEventListener("load", () => waitForKakaoMaps().then(resolve).catch(reject));
      existing.addEventListener("error", () => reject(new Error("Kakao Maps SDK 스크립트를 불러오지 못했습니다.")));
      return;
    }
    let key: string;
    try { key = getKakaoKey(); } catch (err) { reject(err); return; }
    const script = document.createElement("script");
    script.dataset.kakaoSdk = "true";
    script.src = `${KAKAO_SDK_URL}?appkey=${encodeURIComponent(key)}&libraries=services&autoload=false`;
    script.async = true;
    script.onload = () => waitForKakaoMaps().then(resolve).catch(reject);
    // 개발용 안내(키·도메인 설정)는 콘솔에만 남기고, 화면에 뜨는 문장은 사용자 말로 한다 —
    // AI 상담 미니 지도 등이 err.message를 그대로 띄웠다 (2026-11-07 UX 자동 개선).
    script.onerror = () => {
      console.error("Kakao Maps SDK를 불러오지 못했습니다. NEXT_PUBLIC_KAKAO_JS_KEY와 도메인 설정을 확인하세요.");
      reject(new Error("지도를 불러오지 못했습니다 — 인터넷 연결을 확인해 주세요."));
    };
    document.head.appendChild(script);
  });
  // 한 번 실패하면(휴대폰 회선 끊김 등) 실패한 약속과 스크립트가 남아, 다시 불러도 같은
  // 실패만 돌려받거나 끝나지 않는 load를 기다렸다 — 실패하면 둘 다 치워 다시 부를 수 있게
  // 한다 (2026-11-07 UX 자동 개선).
  const pending: Promise<void> = attempt.catch((err: unknown) => {
    if (loadPromise === pending) loadPromise = null;
    if (!window.kakao?.maps) document.querySelector('script[data-kakao-sdk="true"]')?.remove();
    throw err;
  });
  loadPromise = pending;
  return pending;
}

export function isKakaoMapsReady(): boolean {
  return typeof window !== "undefined" && typeof window.kakao !== "undefined" && Boolean(window.kakao.maps);
}
