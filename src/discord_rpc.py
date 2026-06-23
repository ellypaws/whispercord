"""Discord local RPC over the named pipe - self-detection with no CDP / no webpack / no
client restart. The handshake's READY dispatch returns the logged-in user for free (no app
approval needed), which is the robust way to know "who am I" on each running client.

Roster/voice reads (GET_SELECTED_VOICE_CHANNEL, VOICE_STATE_*) are gated behind the
rpc.voice.read scope (app approval), so those are NOT used here; self is the free, reliable win.

Framing: <op u32 LE><len u32 LE><utf8 json>.  op: 0=HANDSHAKE 1=FRAME 2=CLOSE 3=PING 4=PONG.
Each running Discord client owns one pipe: \\.\pipe\discord-ipc-{0,1,2,...}.
"""
import struct, json, time

# Any registered application id is accepted for the handshake; we only read the READY user,
# never authorize, so this need not be "our" app. (A user may swap in their own client_id.)
DEFAULT_CLIENT_ID = "207646673902501888"
PIPE_FMT = r"\\.\pipe\discord-ipc-%d"

OP_HANDSHAKE, OP_FRAME, OP_CLOSE, OP_PING, OP_PONG = 0, 1, 2, 3, 4


class RpcPipe:
    """One connection to a single discord-ipc pipe."""
    def __init__(self, index, client_id=DEFAULT_CLIENT_ID, timeout=2.0):
        self.index = index
        self.client_id = client_id
        self.path = PIPE_FMT % index
        self._f = open(self.path, "r+b", buffering=0)   # raises if the pipe doesn't exist
        self._f_timeout = timeout
        self.user = None

    def _send(self, op, payload):
        data = json.dumps(payload).encode("utf-8")
        self._f.write(struct.pack("<II", op, len(data)) + data)
        self._f.flush()

    def _recv(self):
        hdr = self._f.read(8)
        if not hdr or len(hdr) < 8:
            return None, None
        op, ln = struct.unpack("<II", hdr)
        body = self._f.read(ln) if ln else b""
        try:
            return op, json.loads(body.decode("utf-8"))
        except Exception:
            return op, None

    def handshake(self):
        """Send HANDSHAKE, read the READY dispatch, capture the self user. Returns the user dict."""
        self._send(OP_HANDSHAKE, {"v": 1, "client_id": self.client_id})
        op, msg = self._recv()
        if isinstance(msg, dict) and msg.get("evt") == "READY":
            self.user = ((msg.get("data") or {}).get("user")) or None
        return self.user

    def close(self):
        try:
            self._send(OP_CLOSE, {})
        except Exception:
            pass
        try:
            self._f.close()
        except Exception:
            pass


def discover_self(max_pipes=10, client_id=DEFAULT_CLIENT_ID):
    """Handshake every reachable discord-ipc pipe and return a list of self users:
    [{pipe, id, username, global_name, avatar}], one per running Discord client.
    De-duplicated by user id is left to the caller (same account may be on several clients)."""
    out = []
    for i in range(max_pipes):
        try:
            p = RpcPipe(i, client_id=client_id)
        except Exception:
            continue                                    # no such pipe -> no client at this index
        try:
            u = p.handshake()
            if u:
                out.append({"pipe": i, "id": u.get("id"), "username": u.get("username"),
                            "global_name": u.get("global_name"), "avatar": u.get("avatar")})
        except Exception:
            pass
        finally:
            p.close()
    return out


def self_ids(max_pipes=10, client_id=DEFAULT_CLIENT_ID):
    """Just the set of my own user ids across all running clients (for own-voice handling)."""
    return {s["id"] for s in discover_self(max_pipes, client_id) if s.get("id")}


if __name__ == "__main__":
    t0 = time.time()
    found = discover_self()
    print("discovered %d self identit%s in %.0fms:" % (len(found), "y" if len(found) == 1 else "ies",
                                                       (time.time() - t0) * 1000))
    for s in found:
        print("  pipe %d: id=%s username=%s global_name=%s" % (s["pipe"], s["id"], s["username"], s["global_name"]))
