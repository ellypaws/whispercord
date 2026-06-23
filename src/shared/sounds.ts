export const SOUND_EVENT_RE = /[\[(*♪][^\][()*♪]*[\])*♪]/g;

export function isSoundEvent(text: string): boolean {
  return SOUND_EVENT_RE.test(text);
}
