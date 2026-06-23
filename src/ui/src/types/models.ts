export interface Source {
  src: string;
  client?: string;
  userId?: string;
  name?: string;
  avatar?: string;
  resolved?: boolean;
  locked?: boolean;
  kind?: string;
  ts?: number;
}

export interface RosterMember {
  userId: string;
  name: string;
  avatar?: string;
  stream?: boolean;
  mute?: boolean;
  deaf?: boolean;
  video?: boolean;
}

export interface TranscriptItem {
  type: "transcript";
  seq: number;
  userId: string;
  name: string;
  avatar?: string;
  text: string;
  isFinal: boolean;
  ts: number;
  client?: string;
  clipId?: string;
}

export interface EventItem {
  type: "event";
  seq: number;
  event: string;
  name: string;
  userId?: string;
  avatar?: string;
  ts: number;
  client?: string;
}

export type PanelItem = TranscriptItem | EventItem;

export interface Panel {
  key: string;
  items: PanelItem[];
  pinned?: boolean;
}

export interface SearchChip {
  op: string;
  value: string;
  label: string;
  userId?: string;
  avatar?: string;
}
