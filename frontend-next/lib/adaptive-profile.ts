export type AdaptiveIndustryId = "restaurant" | "cafe" | "beauty" | "clinic" | "academy" | "convenience";

export interface UpjongProfileHint {
  business_type?: string | null;
  inds_mcls_cd?: string | null;
  label?: string | null;
  breadcrumb?: string | null;
}

const BUSINESS_TYPE_MAP: Record<string, AdaptiveIndustryId> = {
  restaurant: "restaurant",
  cafe: "cafe",
  convenience: "convenience",
  clinic: "clinic",
  academy: "academy",
  beauty: "beauty",
};

export function profileIdForUpjong(upjong: UpjongProfileHint | null): AdaptiveIndustryId {
  if (!upjong) return "cafe";
  const direct = upjong.business_type ? BUSINESS_TYPE_MAP[upjong.business_type] : undefined;
  if (direct) return direct;

  const label = `${upjong.label || ""} ${upjong.breadcrumb || ""}`.toLowerCase();
  if (/카페|커피|제과|디저트/.test(label)) return "cafe";
  if (/미용|헤어|바버/.test(label)) return "beauty";
  if (/병원|의원|피부과|치과/.test(label)) return "clinic";
  if (/학원|교육/.test(label)) return "academy";
  if (/편의점|소매/.test(label)) return "convenience";

  const middle = upjong.inds_mcls_cd || "";
  if (["S207", "S208"].includes(middle)) return "beauty";
  if (middle.startsWith("Q")) return "clinic";
  if (middle.startsWith("P")) return "academy";
  if (middle.startsWith("G")) return "convenience";
  if (middle === "I212") return "cafe";
  return "restaurant";
}
