import type { RosterMember } from "./models";

export interface RelayStatusMessage {
  type: "status";
  active?: number;
  clients?: Record<string, { active?: number }>;
}

export interface RelayTranscriptMessage {
  type: "transcript";
  src?: string;
  client?: string;
  userId: string;
  name: string;
  avatar?: string;
  text: string;
  isFinal: boolean;
  ts?: number;
  clipId?: string;
  locked?: boolean;
}

export interface RelayEventMessage {
  type: "event";
  client?: string;
  userId?: string;
  name: string;
  avatar?: string;
  event: string;
  ts?: number;
}

export interface RelayRenameMessage {
  type: "rename";
  src?: string;
  client?: string;
  userId: string;
  name: string;
  avatar?: string;
  locked?: boolean;
}

export interface RelayRosterMessage {
  type: "roster";
  client?: string;
  members: RosterMember[];
}

export interface RelaySpeakingMessage {
  type: "speaking";
  client?: string;
  userId: string;
  on: boolean;
}

export interface RelaySelfIdentityMessage {
  type: "selfIdentity";
  client?: string;
  name?: string;
  avatar?: string;
  userId?: string;
}

export interface RelayClipMessage {
  type: "clip";
  clipId: string;
  wav: string | number[] | null;
}

export interface RelayKeepaliveMessage {
  type: "keepalive";
  client?: string;
  userId: string;
}

export type RelayInboundMessage =
  | RelayStatusMessage
  | RelayTranscriptMessage
  | RelayEventMessage
  | RelayRenameMessage
  | RelayRosterMessage
  | RelaySpeakingMessage
  | RelaySelfIdentityMessage
  | RelayClipMessage
  | RelayKeepaliveMessage;

export type RelayOutboundMessage =
  | { type: "setKeywords"; keywords: string[] }
  | { type: "setConfig"; config: Record<string, unknown> }
  | { type: "reinjectOverlay" }
  | { type: "getClip"; clipId: string }
  | { type: "assign"; src: string; userId?: string; name?: string; avatar?: string; locked?: boolean };
