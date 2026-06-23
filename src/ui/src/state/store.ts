import { signal } from "@preact/signals";
import type { AppConfig } from "../types/config";
import type { Panel, RosterMember, Source } from "../types/models";

export const config = signal<AppConfig | null>(null);
export const panels = signal<Record<string, Panel>>({});
export const rosters = signal<Record<string, RosterMember[]>>({});
export const speakingNow = signal<Record<string, string[]>>({});
export const sources = signal<Record<string, Source>>({});
export const keywords = signal<string[]>([]);
export const searchChips = signal<unknown[]>([]);
