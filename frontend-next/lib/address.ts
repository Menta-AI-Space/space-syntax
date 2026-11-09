import { loadKakaoMaps } from "@/lib/kakao";
import { searchDongs } from "@/lib/api";

export interface AddressSearchItem {
  address: string;
  geocodeAddress: string;
  placeName?: string;
  lat: number;
  lng: number;
  source: "place" | "address" | "dong";
}

function formatPlaceSearchLabel(place: { place_name?: string; road_address_name?: string; address_name?: string }) {
  const name = place.place_name?.trim();
  const addr = place.road_address_name?.trim() || place.address_name?.trim();
  if (name && addr) return `${name} · ${addr}`;
  return name || addr || "";
}

function placeToAddressItem(place: { place_name?: string; road_address_name?: string; address_name?: string; x: string; y: string }): AddressSearchItem {
  const road = place.road_address_name?.trim();
  const jibun = place.address_name?.trim();
  const geocodeAddress = road || jibun || place.place_name?.trim() || "";
  return { address: formatPlaceSearchLabel(place) || geocodeAddress, geocodeAddress, placeName: place.place_name?.trim() || "", lat: parseFloat(place.y), lng: parseFloat(place.x), source: "place" };
}

function searchGeocoderAddresses(query: string, limit: number): Promise<AddressSearchItem[]> {
  return new Promise((resolve) => {
    const geocoder = new window.kakao.maps.services.Geocoder();
    geocoder.addressSearch(query, (result: Array<{ road_address?: { address_name?: string }; address?: { address_name?: string }; address_name?: string; x: string; y: string }>, status: string) => {
      if (status !== window.kakao.maps.services.Status.OK || !result?.length) { resolve([]); return; }
      resolve(result.slice(0, limit).map((doc) => {
        const road = doc.road_address?.address_name;
        const jibun = doc.address?.address_name;
        const address = road || jibun || doc.address_name || query;
        return { address, geocodeAddress: address, lat: parseFloat(doc.y), lng: parseFloat(doc.x), source: "address" as const };
      }));
    }, { page: 1, size: Math.min(Math.max(limit, 1), 30) });
  });
}

function searchPlacesKeyword(query: string, limit: number): Promise<AddressSearchItem[]> {
  return new Promise((resolve) => {
    const places = new window.kakao.maps.services.Places();
    places.keywordSearch(query, (result: Array<{ place_name?: string; road_address_name?: string; address_name?: string; x: string; y: string }>, status: string) => {
      if (status !== window.kakao.maps.services.Status.OK || !result?.length) { resolve([]); return; }
      resolve(result.slice(0, limit).map((place) => placeToAddressItem(place)));
    }, { size: Math.min(Math.max(limit, 1), 15) });
  });
}

function isDuplicate(item: AddressSearchItem, seen: { coords: Set<string>; addrs: Set<string> }) {
  const coordKey = `${Number(item.lat).toFixed(5)},${Number(item.lng).toFixed(5)}`;
  const addrKey = String(item.address || "").replace(/\s+/g, " ").trim().toLowerCase();
  if (seen.coords.has(coordKey) || (addrKey && seen.addrs.has(addrKey))) return true;
  seen.coords.add(coordKey);
  if (addrKey) seen.addrs.add(addrKey);
  return false;
}

// Ported from `reverseGeocodeLatLng` in frontend/js/api.js.
export async function reverseGeocodeLatLng(lat: number, lng: number): Promise<string | null> {
  await loadKakaoMaps();
  if (!window.kakao?.maps?.services?.Geocoder) return null;
  return new Promise((resolve) => {
    const geocoder = new window.kakao.maps.services.Geocoder();
    geocoder.coord2Address(lng, lat, (result: Array<{ road_address?: { address_name?: string }; address?: { address_name?: string } }>, status: string) => {
      if (status === window.kakao.maps.services.Status.OK && result?.[0]) {
        const road = result[0].road_address?.address_name;
        const jibun = result[0].address?.address_name;
        resolve(road || jibun || null);
        return;
      }
      resolve(null);
    });
  });
}

export async function searchKoreanAddresses(
  query: string,
  limit = 10,
  opts: { includeDongs?: boolean } = {},
): Promise<AddressSearchItem[]> {
  await loadKakaoMaps();
  const fetchLimit = Math.min(Math.max(limit, 1), 15);
  const [dongResults, placeResults, geocodeResults] = await Promise.all([
    opts.includeDongs ? searchDongs(query, 5) : Promise.resolve([]),
    searchPlacesKeyword(query, fetchLimit),
    searchGeocoderAddresses(query, fetchLimit),
  ]);
  // 동네를 물었으면 행정동을 맨 위에 — '연남동'이 가게보다 먼저 뜨게.
  const dongItems: AddressSearchItem[] = dongResults.map((d) => ({
    address: d.name, geocodeAddress: d.name, lat: d.lat, lng: d.lng, source: "dong" as const,
  }));
  const seen = { coords: new Set<string>(), addrs: new Set<string>() };
  const merged: AddressSearchItem[] = [];
  for (const item of [...dongItems, ...placeResults, ...geocodeResults]) {
    if (!item.address || !Number.isFinite(item.lat) || !Number.isFinite(item.lng)) continue;
    if (isDuplicate(item, seen)) continue;
    merged.push(item);
    if (merged.length >= limit) break;
  }
  return merged;
}
