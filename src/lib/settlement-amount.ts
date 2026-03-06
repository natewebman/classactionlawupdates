/**
 * Settlement Amount Extraction & Normalization
 *
 * Parses human-readable dollar amounts into numeric values for sorting.
 * Handles: million, billion, thousand, raw dollar values.
 *
 * Examples:
 *   "$4.2 million"   → 4200000
 *   "$1.5 billion"   → 1500000000
 *   "$750 thousand"  → 750000
 *   "$500,000"       → 500000
 *   "$12.5M"         → 12500000
 *   "Up to $50"      → 50
 */

const MULTIPLIERS: Record<string, number> = {
  billion: 1_000_000_000,
  b: 1_000_000_000,
  million: 1_000_000,
  m: 1_000_000,
  thousand: 1_000,
  k: 1_000,
};

/**
 * Parse a settlement amount string to a numeric value.
 * Returns null if the string cannot be parsed.
 */
export function parseSettlementAmount(raw: string | null | undefined): number | null {
  if (!raw) return null;

  const text = raw.toLowerCase().trim();

  // Match a dollar figure: optional $, digits with commas/decimals, optional multiplier
  const match = text.match(
    /\$?\s*([\d,]+(?:\.\d+)?)\s*(billion|million|thousand|b|m|k)?/i
  );
  if (!match) return null;

  // Parse the numeric portion (strip commas)
  const numStr = match[1].replace(/,/g, '');
  const num = parseFloat(numStr);
  if (isNaN(num)) return null;

  // Apply multiplier
  const multiplierKey = match[2]?.toLowerCase();
  const multiplier = multiplierKey ? (MULTIPLIERS[multiplierKey] ?? 1) : 1;

  return num * multiplier;
}

/**
 * Sort articles by settlement amount (descending).
 * Articles without a parseable amount sort to the end.
 */
export function sortBySettlementAmount<T extends { settlement_amount: string | null }>(
  articles: T[]
): T[] {
  return [...articles].sort((a, b) => {
    const amountA = parseSettlementAmount(a.settlement_amount);
    const amountB = parseSettlementAmount(b.settlement_amount);
    // Articles with no amount go to the end
    if (amountA === null && amountB === null) return 0;
    if (amountA === null) return 1;
    if (amountB === null) return -1;
    return amountB - amountA;
  });
}
