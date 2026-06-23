export const DEFAULT_AVATAR = "https://cdn.discordapp.com/embed/avatars/0.png";

export function colorFor(id: string): string {
  let hash = 0;
  for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
  return `hsl(${hash % 360} 65% 72%)`;
}

export function unknownLabel(src: string): string {
  return `Unknown ${String(src).slice(-5)}`;
}
