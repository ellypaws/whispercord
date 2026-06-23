const cache = new Map<string, RegExp>();

export function keywordRegex(keyword: string): RegExp {
  const normalized = keyword.toLowerCase();
  const cached = cache.get(normalized);
  if (cached) return cached;
  const escaped = normalized.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const regex = new RegExp(`(?<!\\w)${escaped}(?!\\w)`, "gi");
  cache.set(normalized, regex);
  return regex;
}

export function matchKeyword(text: string, keywords: string[]): string | null {
  if (!text) return null;
  for (const raw of keywords) {
    const keyword = raw.toLowerCase();
    if (!keyword) continue;
    const regex = keywordRegex(keyword);
    regex.lastIndex = 0;
    if (regex.test(text)) return keyword;
  }
  return null;
}
