export const DEFAULT_AVATAR = "https://cdn.discordapp.com/embed/avatars/0.png";
export const DEFAULT_GRAY_AVATAR = "https://cdn.discordapp.com/embed/avatars/1.png";
const UNKNOWN_MARKERS = ["🦊","🐢","🦉","🦋","🐙","🦔","🦫","🐝","🦎","🐳","🦜","🐊","🦒","🦓","🦩","🦦","🐺","🦡","🐿️","🦃","🦚","🐌","🐠","🦂"];

export function colorFor(id: string): string {
  let hash = 0;
  for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
  return `hsl(${hash % 360} 65% 72%)`;
}

export function unknownLabel(src: string): string {
  return `Unknown ${String(src).slice(-5)}`;
}

export function markerFor(src: string): string {
  let hash = 0;
  for (let i = 0; i < String(src).length; i++) hash = (hash * 31 + String(src).charCodeAt(i)) >>> 0;
  return UNKNOWN_MARKERS[hash % UNKNOWN_MARKERS.length];
}

export function markerAvatar(src: string): string {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"><rect width="40" height="40" rx="20" fill="#3a3c43"/><text x="50%" y="52%" dominant-baseline="central" text-anchor="middle" font-size="22">${markerFor(src)}</text></svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

export interface SpeakerDisplaySource {
  userId: string;
  name?: string;
  avatar?: string;
  resolved?: boolean;
  locked?: boolean;
}

export function speakerDisplay(source: SpeakerDisplaySource) {
  if (source.resolved === false) {
    return { name: unknownLabel(source.userId), avatar: markerAvatar(source.userId), locked: !!source.locked };
  }
  return { name: source.name || "unknown", avatar: source.avatar || markerAvatar(source.userId), locked: !!source.locked };
}
