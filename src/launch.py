"""Discover installed Discord clients and ensure each is running with its CDP debug port.

Discord is single-instance per client: a client already running WITHOUT the debug
flag cannot gain the port without a full restart. These helpers expose that policy
so the wrapper can decide whether to restart a running client.
"""
import os, glob, socket, subprocess, time, urllib.request

LOCALAPPDATA = os.environ.get("LOCALAPPDATA", "")

# client folder -> (exe name, debug port). Ports must match config.cdp_ports.
CLIENTS = {
    "DiscordPTB":         ("DiscordPTB.exe", 9223),
    "Discord":            ("Discord.exe", 9224),
    "DiscordCanary":      ("DiscordCanary.exe", 9225),
    "DiscordDevelopment": ("DiscordDevelopment.exe", 9226),
}
PORTS = [p for _, p in CLIENTS.values()]


def installed_clients():
    """{folder: {'exe': newest_exe_path, 'port': port}} for every installed client."""
    out = {}
    for folder, (exe, port) in CLIENTS.items():
        cands = sorted(glob.glob(os.path.join(LOCALAPPDATA, folder, "app-*", exe)))
        if cands:
            out[folder] = {"exe": cands[-1], "port": port, "name": exe}
    return out


def cdp_alive(port, timeout=1.0):
    try:
        urllib.request.urlopen("http://127.0.0.1:%d/json/version" % port, timeout=timeout).read()
        return True
    except Exception:
        return False


def is_running(exe_name):
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq %s" % exe_name],
            text=True, stderr=subprocess.DEVNULL, creationflags=0x08000000)  # NO_WINDOW
        return exe_name.lower() in out.lower()
    except Exception:
        return False


def kill_client(exe_name):
    try:
        subprocess.run(["taskkill", "/F", "/IM", exe_name, "/T"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=0x08000000)
    except Exception:
        pass


def launch_client(exe, port):
    """Start a client fully detached with its remote-debugging port."""
    DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen('cmd /c start "" "%s" --remote-debugging-port=%d' % (exe, port),
                     shell=True, creationflags=DETACHED,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def ensure_client(folder, restart_if_needed=False, wait=20):
    """Ensure one installed client has a live CDP port.
    Returns (port, status) where status in {'ready','launched','restarted','running-no-port','absent'}.
    If the client is running without the port, only restarts it when restart_if_needed=True
    (this closes the user's current call)."""
    clients = installed_clients()
    if folder not in clients:
        return None, "absent"
    info = clients[folder]
    port = info["port"]
    if cdp_alive(port):
        return port, "ready"
    running = is_running(info["name"])
    if running and not restart_if_needed:
        return port, "running-no-port"
    if running:
        kill_client(info["name"]); time.sleep(1.5)
        status = "restarted"
    else:
        status = "launched"
    launch_client(info["exe"], port)
    deadline = time.time() + wait
    while time.time() < deadline and not cdp_alive(port):
        time.sleep(0.5)
    return port, status


if __name__ == "__main__":
    found = installed_clients()
    print("installed clients:")
    for folder, info in found.items():
        state = "port %d LIVE" % info["port"] if cdp_alive(info["port"]) else \
                ("running, NO debug port" if is_running(info["name"]) else "not running")
        print("  %-20s %s  (%s)" % (folder, state, info["exe"]))
