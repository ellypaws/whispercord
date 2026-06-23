"""Discord Live Transcriber — desktop wrapper.

One entry point, two modes:
  * `app.py --backend`  -> runs the transcription engine (live_transcribe.main)
  * `app.py`            -> opens the GUI, which spawns the engine as a `--backend` child

Packaged as a single PyInstaller exe: the GUI re-execs the same exe with --backend,
so there is only one binary to ship. Settings live in config.json next to the exe.
"""
import os, sys, json, time, subprocess, threading, collections

import paths

NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


def icon_path():
    for p in (paths.resource("assets", "icon.ico"), paths.resource("assets", "icon.png"),
              paths.data("assets", "icon.ico"), paths.data("assets", "icon.png")):
        if os.path.exists(p):
            return p
    return None


# ----------------------------------------------------------------------------- backend mode
def run_backend():
    import live_transcribe
    live_transcribe.main()


def cleanup_overlays(log=None):
    """Remove injected overlay DOM from every reachable Discord debug port."""
    try:
        from config import load
        from cdp import CDP, cleanup_overlay
    except Exception as e:
        if log:
            log("[ui] overlay cleanup unavailable: %s" % e)
        return 0

    cfg = load()
    ports = cfg.get("cdp_ports") or [cfg.get("cdp_port", 9223)]
    cleaned = 0
    for port in dict.fromkeys(ports):
        c = None
        try:
            c = CDP(port, http_timeout=0.5, open_timeout=1.0, command_timeout=1.0)
            cleanup_overlay(c)
            cleaned += 1
        except Exception:
            pass
        finally:
            try:
                if c:
                    c.close()
            except Exception:
                pass
    if log:
        log("[ui] overlay cleanup sent to %d client(s)" % cleaned)
    return cleaned


# ----------------------------------------------------------------------------- engine control
PROG_TAG = "[[VTPROG]]"
PORT_TAG = "[[VTPORT]]"          # engine announces the relay port it actually bound (auto-fallback)
PROG_IDLE = {"stage": "idle", "label": "", "pct": None, "done": True, "active": False}


class Engine:
    # crash-loop guard: more than this many unexpected exits within the window -> stop retrying
    MAX_RESTARTS = 5
    RESTART_WINDOW_S = 120.0

    def __init__(self):
        self.proc = None
        self.log = collections.deque(maxlen=600)
        self.progress = dict(PROG_IDLE)
        self.relay_port = None                  # actual relay port the engine bound (from PORT_TAG)
        self._want_running = False              # True between start() and stop(); drives auto-restart
        self._restarts = collections.deque()    # monotonic timestamps of recent unexpected exits

    def start(self):
        if self.proc and self.proc.poll() is None:
            return True
        self._want_running = True
        self._restarts.clear()
        self.log.clear()                        # fresh log only on a user-initiated start
        self._spawn()
        return True

    def _spawn(self):
        # show the banner immediately; the backend refines it as soon as it reports
        self.relay_port = None      # re-learned from PORT_TAG (engine may auto-pick a free port)
        self.progress = {"stage": "starting", "label": "Starting engine…", "pct": None,
                         "done": False, "active": True}
        args = [sys.executable]
        if not getattr(sys, "frozen", False):
            args.append(os.path.join(paths.resource_dir(), "app.py"))
        args.append("--backend")
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        env.setdefault("VT_INJECT_OVERLAY", "1")   # on by default; honour VT_INJECT_OVERLAY=0 to disable all overlays
        self.proc = subprocess.Popen(
            args, cwd=paths.resource_dir(), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=NO_WINDOW)
        threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()

    def _pump(self, proc):
        try:
            for line in proc.stdout:
                line = line.rstrip("\n")
                if line.startswith(PROG_TAG):
                    try:
                        p = json.loads(line[len(PROG_TAG):])
                        p["active"] = not p.get("done")
                        self.progress = p
                    except Exception:
                        pass
                    continue
                if line.startswith(PORT_TAG):
                    try:
                        self.relay_port = int(line[len(PORT_TAG):])
                    except Exception:
                        pass
                    continue
                self.log.append(line)
        except Exception:
            pass
        # stdout closed -> this process exited. If the user didn't ask to stop, supervise it.
        if not self._want_running:
            self.progress = dict(PROG_IDLE)
            return
        code = proc.poll()
        now = time.monotonic()
        self._restarts.append(now)
        while self._restarts and now - self._restarts[0] > self.RESTART_WINDOW_S:
            self._restarts.popleft()
        if len(self._restarts) > self.MAX_RESTARTS:
            self._want_running = False
            self.log.append("[ui] engine exited %d times in %ds (last code %s) - not restarting; "
                            "see the log above for the cause" % (len(self._restarts),
                            int(self.RESTART_WINDOW_S), code))
            self.progress = {"stage": "error", "label": "Engine keeps stopping - paused. Check the log.",
                             "pct": None, "done": True, "active": False}
            return
        self.log.append("[ui] engine exited unexpectedly (code %s) - restarting (%d/%d)"
                        % (code, len(self._restarts), self.MAX_RESTARTS))
        self.progress = {"stage": "starting", "label": "Engine stopped unexpectedly - restarting…",
                         "pct": None, "done": False, "active": True}
        time.sleep(min(5.0, float(len(self._restarts))))   # brief backoff that grows with each retry
        if self._want_running:
            self._spawn()

    def stop(self):
        self._want_running = False              # tell the supervisor this exit is intentional
        cleanup_overlays(self.log.append)
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except Exception:
                    self.proc.kill()
            except Exception:
                pass
        self.proc = None
        self.progress = dict(PROG_IDLE)
        return True

    def running(self):
        return bool(self.proc and self.proc.poll() is None)


# ----------------------------------------------------------------------------- js_api bridge
class Api:
    def __init__(self, force_setup=False):
        self.engine = Engine()
        self.force_setup = bool(force_setup)

    def get_config(self):
        from config import load
        return load()

    def save_config(self, cfg):
        try:
            with open(paths.data("config.json"), "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
            return True
        except Exception as e:
            self.engine.log.append("[ui] save_config failed: %s" % e)
            return False

    def setup_requested(self):
        return self.force_setup

    def self_identity(self):
        """Your own Discord name(s) via RPC over the pipe - no CDP, no client restart, no running
        engine. Powers the wizard's keyword suggestions immediately. Returns [] if no client is up."""
        try:
            import discord_rpc
            names = []
            for s in discord_rpc.discover_self():
                for n in (s.get("global_name"), s.get("username")):
                    if n and n not in names:
                        names.append(n)
            return names
        except Exception as e:
            self.engine.log.append("[ui] self_identity failed: %s" % e)
            return []

    def detect_hardware(self):
        try:
            import gpu_detect
            info = gpu_detect.hardware_summary()
            if info["vendor"] == "cpu" and gpu_detect.nvidia_present():
                info = {"vendor": "nvidia", "name": "NVIDIA GPU", "gfx": None, "vulkan": True}
            vendor = info["vendor"]
            # Device follows gpu_detect.resolve("auto") verbatim so the wizard never diverges from
            # what the engine actually picks (auto now prefers vulkan over cuda, incl. on NVIDIA).
            rec_device = gpu_detect.resolve("auto", log=lambda *a: None)
            rec_engine = "whisper" if vendor in ("nvidia", "amd", "intel") else "parakeet"
            info.update({"recommended_engine": rec_engine, "recommended_device": rec_device})
            return info
        except Exception as e:
            self.engine.log.append("[ui] detect_hardware failed: %s" % e)
            return {"vendor": "cpu", "name": "CPU", "gfx": None, "vulkan": False,
                    "recommended_engine": "parakeet", "recommended_device": "cpu"}

    def list_clients(self):
        import launch
        found = launch.installed_clients()
        out = []
        for folder, info in found.items():
            out.append({
                "folder": folder,
                "exe": info["name"].lower(),
                "port": info["port"],
                "live": launch.cdp_alive(info["port"]),
                "running": launch.is_running(info["name"]),
            })
        return out

    def list_clients_quick(self):
        import launch
        found = launch.installed_clients()
        return [{
            "folder": folder,
            "exe": info["name"].lower(),
            "port": info["port"],
            "live": False,
            "running": False,
            "detecting": True,
        } for folder, info in found.items()]

    def list_input_devices(self):
        try:
            import sounddevice as sd
            seen, out = set(), []
            for i, d in enumerate(sd.query_devices()):
                if d.get("max_input_channels", 0) > 0 and d["name"] not in seen:
                    seen.add(d["name"]); out.append({"index": i, "name": d["name"]})
            return out
        except Exception:
            return []

    def ensure_client(self, folder, restart):
        import launch
        port, status = launch.ensure_client(folder, restart_if_needed=bool(restart))
        return {"port": port, "status": status}

    def start_backend(self):
        return self.engine.start()

    def stop_backend(self):
        return self.engine.stop()

    def backend_status(self):
        return self.engine.running()

    def relay_port(self):
        # the port the engine actually bound (auto-fallback if the configured one was busy); None
        # until the engine reports it -> the UI then uses the configured port.
        return self.engine.relay_port

    def get_log(self):
        return "\n".join(self.engine.log)

    def clear_log(self):
        self.engine.log.clear()
        return True

    def get_progress(self):
        return self.engine.progress

    def cuda_status(self):
        try:
            from cuda_setup import cuda_present
            return bool(cuda_present())
        except Exception:
            return False

    def app_version(self):
        from version import __version__
        return __version__

    def check_update(self):
        """Query GitHub for the latest release and report whether it's newer than us.
        Network failures degrade quietly to "no update" so an offline launch is unaffected."""
        import urllib.request
        from version import __version__, GITHUB_REPO, is_newer
        info = {"available": False, "current": __version__, "latest": __version__, "url": ""}
        try:
            req = urllib.request.Request(
                "https://api.github.com/repos/%s/releases/latest" % GITHUB_REPO,
                headers={"Accept": "application/vnd.github+json", "User-Agent": "whispercord"})
            with urllib.request.urlopen(req, timeout=6) as r:
                data = json.load(r)
            tag = (data.get("tag_name") or "").strip()
            if tag:
                info["latest"] = tag.lstrip("vV")
                info["url"] = data.get("html_url") or ""
                info["available"] = is_newer(tag, __version__)
        except Exception as e:
            self.engine.log.append("[ui] update check failed: %s" % e)
        return info

    def open_url(self, url):
        try:
            import webbrowser
            webbrowser.open(str(url))
            return True
        except Exception as e:
            self.engine.log.append("[ui] open_url failed: %s" % e)
            return False

    def list_models(self):
        try:
            import models
            return models.list_models()
        except Exception as e:
            self.engine.log.append("[ui] list_models failed: %s" % e)
            return []

    def model_cached(self, name):
        try:
            import models
            return bool(models.is_cached(name))
        except Exception:
            return False

    def delete_model(self, name):
        try:
            import models
            return bool(models.delete_model(name))
        except Exception as e:
            self.engine.log.append("[ui] delete_model failed: %s" % e)
            return False


# ----------------------------------------------------------------------------- tray (optional)
def start_tray(window):
    try:
        import pystray
        from PIL import Image, ImageDraw
    except Exception:
        return  # tray is optional; skip if deps missing
    ip = icon_path()
    if ip:
        try:
            img = Image.open(ip)
        except Exception:
            ip = None
    if not ip:
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((8, 8, 56, 56), fill=(88, 101, 242, 255))
        d.rectangle((28, 20, 36, 40), fill=(255, 255, 255, 255))
        d.rectangle((24, 40, 40, 44), fill=(255, 255, 255, 255))

    def show(icon, item):
        try: window.show()
        except Exception: pass

    def hide(icon, item):
        try: window.hide()
        except Exception: pass

    def quit_(icon, item):
        icon.stop()
        try: window.destroy()
        except Exception: pass

    menu = pystray.Menu(
        pystray.MenuItem("Show", show, default=True),
        pystray.MenuItem("Hide", hide),
        pystray.MenuItem("Quit", quit_),
    )
    icon = pystray.Icon("transcriber", img, "Discord Live Transcriber", menu)
    threading.Thread(target=icon.run, daemon=True).start()


# ----------------------------------------------------------------------------- gui mode
def run_dev_watch(args):
    if getattr(sys, "frozen", False):
        return run_gui()
    try:
        from watchfiles import watch
    except Exception as e:
        print("[dev] watchfiles is required for backend reload: %s" % e)
        print("[dev] install requirements or run without --dev-watch")
        return 1

    src_dir = paths.resource_dir()
    ui_dir = os.path.abspath(paths.resource("ui"))
    child_args = [sys.executable, os.path.join(src_dir, "app.py")] + list(args)
    env = os.environ.copy()
    env["VT_DEV"] = "1"

    while True:
        proc = subprocess.Popen(child_args, cwd=src_dir, env=env)
        print("[dev] backend watcher started pid %s" % proc.pid)
        try:
            for changes in watch(src_dir, rust_timeout=1000, yield_on_timeout=True):
                if proc.poll() is not None:
                    return proc.returncode
                if not changes:
                    continue
                restart = False
                for _, changed in changes:
                    changed = os.path.abspath(changed)
                    if changed.startswith(ui_dir + os.sep):
                        continue
                    if changed.endswith(".py"):
                        restart = True
                        break
                if restart:
                    print("[dev] Python changed, restarting app")
                    try:
                        proc.terminate()
                        proc.wait(timeout=5)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    break
        except KeyboardInterrupt:
            try:
                proc.terminate()
            except Exception:
                pass
            return 0


def run_gui():
    import webview
    api = Api(force_setup="--setup" in sys.argv)
    if os.environ.get("VT_DEV") == "1" and not getattr(sys, "frozen", False):
        url = "http://localhost:5173"
    else:
        url = paths.resource("ui", "dist", "index.html")
    window = webview.create_window(
        "Discord Live Transcriber",
        url=url,
        js_api=api, width=860, height=760, min_size=(640, 560),
        background_color="#1e1f22")

    def on_closing():
        api.engine.stop()
    window.events.closing += on_closing

    def setup():
        start_tray(window)
    try:
        webview.start(setup, icon=icon_path())   # icon arg supported on newer pywebview
    except TypeError:
        webview.start(setup)


def main():
    if "--cleanup-overlay" in sys.argv:
        n = cleanup_overlays(print)
        print("[ui] overlay cleanup complete (%d client(s))" % n)
    elif "--dev-watch" in sys.argv:
        args = [a for a in sys.argv[1:] if a != "--dev-watch"]
        raise SystemExit(run_dev_watch(args))
    elif "--backend" in sys.argv:
        run_backend()
    else:
        run_gui()


if __name__ == "__main__":
    main()
