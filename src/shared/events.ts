export const EVENT_LABEL: Record<string, string> = {
  joined: "joined the channel",
  left: "left the channel",
  muted: "muted",
  unmuted: "unmuted",
  deafened: "deafened",
  undeafened: "undeafened",
  video_on: "turned camera on",
  video_off: "turned camera off",
  stream_on: "started streaming",
  stream_off: "stopped streaming",
};

export const DESKTOP_EVENT_ICON: Record<string, [string, string]> = {
  joined: ["log-in", "#23a55a"],
  left: ["log-out", "#f23f43"],
  muted: ["mic-off", "#f23f43"],
  unmuted: ["mic", "#23a55a"],
  deafened: ["headphones-off", "#f23f43"],
  undeafened: ["headphones", "#23a55a"],
  video_on: ["video", "#5865f2"],
  video_off: ["video-off", "#949ba4"],
  stream_on: ["screen-share", "#5865f2"],
  stream_off: ["screen-share-off", "#949ba4"],
};

export const OVERLAY_EVENT: Record<string, [string, string, string]> = {
  joined: ["log-in", "joined", "#43b581"],
  left: ["log-out", "left", "#f04747"],
  muted: ["mic-off", "muted", "#f04747"],
  unmuted: ["mic", "unmuted", "#43b581"],
  deafened: ["headphones-off", "deafened", "#f04747"],
  undeafened: ["headphones", "undeafened", "#43b581"],
  video_on: ["video", "turned camera on", "#5865f2"],
  video_off: ["video-off", "turned camera off", "#b5bac1"],
  stream_on: ["screen-share", "started streaming", "#5865f2"],
  stream_off: ["screen-share-off", "stopped streaming", "#b5bac1"],
};
