/** Wilder's 14-bar RSI on observed closes; no trading signal is implied. */
export function rsi14(closes: number[]): Array<number | null> {
  const values: Array<number | null> = Array(closes.length).fill(null);
  if (closes.length < 15) return values;

  let gain = 0;
  let loss = 0;
  for (let index = 1; index <= 14; index++) {
    const change = closes[index] - closes[index - 1];
    gain += Math.max(0, change);
    loss += Math.max(0, -change);
  }
  gain /= 14;
  loss /= 14;
  const read = () => gain === 0 && loss === 0 ? 50 : loss === 0 ? 100 :
    gain === 0 ? 0 : 100 - 100 / (1 + gain / loss);
  values[14] = read();

  for (let index = 15; index < closes.length; index++) {
    const change = closes[index] - closes[index - 1];
    gain = (gain * 13 + Math.max(0, change)) / 14;
    loss = (loss * 13 + Math.max(0, -change)) / 14;
    values[index] = read();
  }
  return values;
}
