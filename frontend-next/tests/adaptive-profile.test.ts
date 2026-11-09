import { test } from "node:test";
import * as assert from "node:assert/strict";

import { profileIdForUpjong } from "../lib/adaptive-profile.ts";

test("maps quick preset business types to adaptive industry profiles", () => {
  assert.equal(profileIdForUpjong({ business_type: "cafe" }), "cafe");
  assert.equal(profileIdForUpjong({ business_type: "restaurant" }), "restaurant");
  assert.equal(profileIdForUpjong({ business_type: "clinic" }), "clinic");
});

test("maps official middle category codes when custom search is used", () => {
  assert.equal(profileIdForUpjong({ business_type: "custom", inds_mcls_cd: "S207" }), "beauty");
  assert.equal(profileIdForUpjong({ business_type: "custom", inds_mcls_cd: "P105" }), "academy");
  assert.equal(profileIdForUpjong({ business_type: "custom", inds_mcls_cd: "G204" }), "convenience");
});

test("falls back to restaurant for unknown food codes and cafe label clues", () => {
  assert.equal(profileIdForUpjong({ business_type: "custom", inds_mcls_cd: "I204" }), "restaurant");
  assert.equal(profileIdForUpjong({ business_type: "custom", label: "디저트 카페" }), "cafe");
});
