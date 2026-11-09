import { test } from "node:test";
import * as assert from "node:assert/strict";

import { langFromSearch } from "../lib/lang.ts";

test("langFromSearch reads ?lang=ko|en and ignores anything else", () => {
  assert.equal(langFromSearch("?lang=ko"), "ko");
  assert.equal(langFromSearch("?tab=x&lang=en"), "en");
  assert.equal(langFromSearch(""), null);
  assert.equal(langFromSearch("?lang=jp"), null);
});
