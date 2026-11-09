import type { RadiusM } from "@/lib/api";
import type { UpjongSelection } from "@/components/BusinessPicker";

/**
 * "이 결과가 아직 지금 조건의 결과인가"를 한 문자열로 나타낸다.
 *
 * 이 앱에서 반복해서 나온 결함이 하나 있다: 결과는 화면에 남겨 두고 조건만
 * 바꾸면, 머리글은 새 조건을 적는데 숫자는 옛 조건 그대로다(후보지 비교의
 * 반경, 임대조건 분석의 위치). 조건을 이 서명으로 묶어 두면 "바뀌었는가"를
 * 한 곳에서 판단할 수 있고, 조건이 늘어도 여기에 더하기만 하면 된다.
 *
 * 구분자를 넣는 이유: 코드를 그냥 이어 붙이면 ("I2","I212")와 ("I2I2","12")가
 * 같은 서명이 된다.
 */
export function upjongKey(upjong: UpjongSelection | null): string {
  return [
    upjong?.inds_lcls_cd || "",
    upjong?.inds_mcls_cd || "",
    upjong?.inds_scls_cd || "",
    upjong?.business_type || "",
  ].join("|");
}

/** 후보지 비교: 업종 + 반경. */
export function compareInputsKey(upjong: UpjongSelection | null, radius: RadiusM): string {
  return `${radius}|${upjongKey(upjong)}`;
}

/**
 * 위치 하나의 서명.
 *
 * 좌표는 소수점 6자리(약 10cm)까지만 본다. 그 아래 자릿수가 흔들린다고 결과를
 * 버리면 같은 자리인데도 매번 다시 계산하게 된다.
 */
export function pointKey(loc: { lat: number; lng: number } | null | undefined): string {
  if (!loc) return "";
  return `${loc.lat.toFixed(6)},${loc.lng.toFixed(6)}`;
}

/** 지점 하나를 놓고 도는 분석(임대조건 적정성 등): 위치 + 반경 + 업종 + 부가 조건. */
export function siteInputsKey({
  lat,
  lng,
  radiusM,
  upjong,
  extra = [],
}: {
  lat: number | null;
  lng: number | null;
  radiusM: RadiusM;
  upjong: UpjongSelection | null;
  extra?: (string | null | undefined)[];
}): string {
  const coord = lat == null || lng == null ? "" : pointKey({ lat, lng });
  return [coord, radiusM, upjongKey(upjong), ...extra.map((e) => e || "")].join("#");
}
