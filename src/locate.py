"""Runtime locator for the per-user audio function in discord_voice.node.

Replaces the hardcoded RVA so the hook survives Discord updates / runs on other
machines. Strategy (no debug symbols needed):

  1. The function embeds the assertion string "ChannelReceive::GetAudioFrameWithInfo".
  2. Find that string in the image, then byte-scan .text for a RIP-relative
     `lea reg, [rip+disp32]` whose target is the string -> an instruction *inside*
     the function.
  3. x64 PEs carry an exception table (.pdata / RUNTIME_FUNCTION) describing every
     function's [Begin, End) range. The range containing that instruction gives the
     function's start RVA.

Pure byte-scan + pefile; capstone not required. Falls back to a known RVA.
"""
import glob, os
import pefile

VOICE_STR = b"ChannelReceive::GetAudioFrameWithInfo"
FALLBACK_RVA = 0x4481d0  # build 1.0.1199

# Native SSRC<->userId binder: discord::voice::Connection::ConnectUser(std::string userId,
# uint32_t audioSsrc, ...) and its DisconnectUser(const std::string&) twin. MSVC embeds each
# function's __FUNCSIG__ at its own entry, so the marker xref lands inside the function itself.
CONNECT_FUNCSIG = b"void __cdecl discord::voice::Connection::ConnectUser"
DISCONNECT_FUNCSIG = b"void __cdecl discord::voice::Connection::DisconnectUser"

# Connection factory: discord::voice::Connection::Create(threadloop, std::string, BridgeConnectionOptions, ...).
# The BridgeConnectionOptions carries the connection CONTEXT label ("default"/"voice" for the main
# voice channel, "stream"/"golive" for a watched Go Live). Hooking Create lets us tag each
# Connection* with its kind at creation - the authoritative mic-vs-screenshare signal.
CREATE_FUNCSIG = b"static std::shared_ptr<Connection> __cdecl discord::voice::Connection::Create"

# Native event sources (no CDP): remote speaking + self mute/deaf, pushed JS->native.
SPEAKING_FUNCSIG = b"void __cdecl discord::voice::Connection::SetRemoteUserSpeaking"
SELFMUTE_FUNCSIG = b"void __cdecl VoiceConnectionWrapper::SetSelfMute"
SELFDEAF_FUNCSIG = b"void __cdecl VoiceConnectionWrapper::SetSelfDeafen"

# %LOCALAPPDATA%\DiscordPTB\app-*\modules\discord_voice-1\discord_voice\discord_voice.node
NODE_GLOB = os.path.join(
    os.environ.get("LOCALAPPDATA", ""),
    "Discord*", "app-*", "modules", "discord_voice-1", "discord_voice", "discord_voice.node",
)


def find_voice_node(prefer_running_build=None):
    """Return the newest discord_voice.node path (or the one matching a build string)."""
    cands = sorted(glob.glob(NODE_GLOB))
    if not cands:
        return None
    if prefer_running_build:
        for c in cands:
            if prefer_running_build in c:
                return c
    # newest app-* wins (lexicographic works for zero-padded build numbers here)
    return cands[-1]


def _find_string_rva(pe, needle):
    for sec in pe.sections:
        data = sec.get_data()
        i = data.find(needle)
        if i != -1:
            return sec.VirtualAddress + i
    return None


def _exec_sections(pe):
    IMAGE_SCN_MEM_EXECUTE = 0x20000000
    out = []
    for sec in pe.sections:
        if sec.Characteristics & IMAGE_SCN_MEM_EXECUTE:
            out.append((sec.VirtualAddress, sec.get_data()))
    return out


def _scan_lea_refs(pe, target_rva):
    """Yield RVAs of `lea r64, [rip+disp32]` instructions pointing at target_rva."""
    hits = []
    for base_rva, data in _exec_sections(pe):
        n = len(data)
        for i in range(n - 7):
            b0 = data[i]
            # REX.W prefix (0x48-0x4F) + opcode 0x8D (LEA) + ModRM RIP-relative (mod=00, rm=101)
            if 0x48 <= b0 <= 0x4F and data[i + 1] == 0x8D and (data[i + 2] & 0xC7) == 0x05:
                disp = int.from_bytes(data[i + 3:i + 7], "little", signed=True)
                insn_rva = base_rva + i
                if insn_rva + 7 + disp == target_rva:
                    hits.append(insn_rva)
    return hits


def _func_start_for_rva(pe, rva):
    """Map an RVA to the start of its function via the .pdata exception table."""
    try:
        pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXCEPTION"]])
        entries = pe.DIRECTORY_ENTRY_EXCEPTION
    except Exception:
        return None
    best = None
    for e in entries:
        beg = e.struct.BeginAddress
        end = e.struct.EndAddress
        if beg <= rva < end:
            # innermost (smallest) enclosing range wins for chained unwind info
            if best is None or (end - beg) < (best[1] - best[0]):
                best = (beg, end)
    return best[0] if best else None


def locate_rva(node_path=None, prefer_running_build=None, verbose=False):
    """Return (rva, node_path). Falls back to FALLBACK_RVA if the scan fails."""
    path = node_path or find_voice_node(prefer_running_build)
    if not path or not os.path.exists(path):
        if verbose:
            print("[locate] discord_voice.node not found; using fallback RVA")
        return FALLBACK_RVA, path
    try:
        pe = pefile.PE(path, fast_load=True)
        str_rva = _find_string_rva(pe, VOICE_STR)
        if str_rva is None:
            if verbose:
                print("[locate] marker string not found; fallback")
            return FALLBACK_RVA, path
        refs = _scan_lea_refs(pe, str_rva)
        for ref in refs:
            fn = _func_start_for_rva(pe, ref)
            if fn is not None:
                if verbose:
                    print("[locate] %s -> RVA 0x%x (string@0x%x, ref@0x%x)"
                          % (os.path.basename(os.path.dirname(os.path.dirname(path))),
                             fn, str_rva, ref))
                return fn, path
        if verbose:
            print("[locate] no enclosing function found; fallback")
        return FALLBACK_RVA, path
    except Exception as e:
        if verbose:
            print("[locate] scan failed (%s); fallback" % e)
        return FALLBACK_RVA, path


def _func_by_marker(pe, marker):
    """RVA of the function whose body references `marker` via a `lea [rip+disp32]`."""
    s = _find_string_rva(pe, marker)
    if s is None:
        return None
    for ref in _scan_lea_refs(pe, s):
        fn = _func_start_for_rva(pe, ref)
        if fn is not None:
            return fn
    return None


def locate_bind_rvas(node_path=None, prefer_running_build=None, verbose=False):
    """Return (connectUserRva, disconnectUserRva); either may be None if not found.
    These hook the native ConnectUser/DisconnectUser so we get authoritative ssrc->userId
    with no webpack/BetterDiscord/CDP (works on vanilla stable/Canary)."""
    path = node_path or find_voice_node(prefer_running_build)
    if not path or not os.path.exists(path):
        return None, None
    try:
        pe = pefile.PE(path, fast_load=True)
        cu = _func_by_marker(pe, CONNECT_FUNCSIG)
        du = _func_by_marker(pe, DISCONNECT_FUNCSIG)
        if verbose:
            print("[locate] ConnectUser=%s DisconnectUser=%s (%s)"
                  % (hex(cu) if cu else None, hex(du) if du else None, os.path.basename(path)))
        return cu, du
    except Exception as e:
        if verbose:
            print("[locate] bind-rva scan failed (%s)" % e)
        return None, None


def locate_create_rva(node_path=None, prefer_running_build=None, verbose=False):
    """RVA of discord::voice::Connection::Create, or None. Hooking it yields the per-connection
    context label (mic 'voice' vs screenshare 'stream') at creation time."""
    path = node_path or find_voice_node(prefer_running_build)
    if not path or not os.path.exists(path):
        return None
    try:
        pe = pefile.PE(path, fast_load=True)
        rva = _func_by_marker(pe, CREATE_FUNCSIG)
        if verbose:
            print("[locate] Create=%s (%s)" % (hex(rva) if rva else None, os.path.basename(path)))
        return rva
    except Exception as e:
        if verbose:
            print("[locate] create-rva scan failed (%s)" % e)
        return None


def locate_event_rvas(node_path=None, prefer_running_build=None, verbose=False):
    """Return {'speaking','selfmute','selfdeaf'} RVAs (any may be None). These hook the native
    event setters so we get remote-speaking + self mute/deaf with no CDP/webpack."""
    path = node_path or find_voice_node(prefer_running_build)
    out = {"speaking": None, "selfmute": None, "selfdeaf": None}
    if not path or not os.path.exists(path):
        return out
    try:
        pe = pefile.PE(path, fast_load=True)
        out["speaking"] = _func_by_marker(pe, SPEAKING_FUNCSIG)
        out["selfmute"] = _func_by_marker(pe, SELFMUTE_FUNCSIG)
        out["selfdeaf"] = _func_by_marker(pe, SELFDEAF_FUNCSIG)
        if verbose:
            print("[locate] events %s (%s)" % ({k: hex(v) if v else None for k, v in out.items()},
                                               os.path.basename(path)))
    except Exception as e:
        if verbose:
            print("[locate] event-rva scan failed (%s)" % e)
    return out


if __name__ == "__main__":
    # self-test across every installed build
    for p in sorted(glob.glob(NODE_GLOB)):
        rva, _ = locate_rva(p, verbose=True)
        cu, du = locate_bind_rvas(p, verbose=True)
        cr = locate_create_rva(p, verbose=True)
        ev = locate_event_rvas(p, verbose=True)
        print("  => audio 0x%x  ConnectUser %s  DisconnectUser %s  events %s  (%s)"
              % (rva, hex(cu) if cu else None, hex(du) if du else None,
                 {k: hex(v) if v else None for k, v in ev.items()}, p))
