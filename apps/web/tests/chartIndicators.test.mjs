import assert from "node:assert/strict";
import { test } from "node:test";
import { rsi14 } from "../app/chartIndicators.ts";

test("RSI 14 waits for fifteen closes and handles one-sided or flat prices", () => {
  assert.ok(rsi14(Array(14).fill(100)).every(value => value === null));
  assert.equal(rsi14(Array(15).fill(100))[14], 50);
  assert.equal(rsi14(Array.from({ length: 15 }, (_, index) => index))[14], 100);
  assert.equal(rsi14(Array.from({ length: 15 }, (_, index) => 100 - index))[14], 0);
});

test("RSI 14 uses Wilder smoothing after its initial window", () => {
  const closes = [...Array.from({ length: 15 }, (_, index) => 100 - index), 100];
  const result = rsi14(closes);
  assert.equal(result[14], 0);
  assert.ok(Math.abs(result[15] - 51.85185185185185) < 1e-10);
});
