"""Discord Live Transcriber — desktop wrapper.

One entry point, two modes:
  * `app.py --backend`  -> runs the transcription engine (live_transcribe.main)
  * `app.py`            -> opens the GUI, which spawns the engine as a `--backend` child

Packaged as a single PyInstaller exe: the GUI re-execs the same exe with --backend,
so there is only one binary to ship. Settings live in config.json next to the exe.
"""
import os, sys, json, subprocess, threading, collections

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


# ----------------------------------------------------------------------------- engine control
class Engine:
    def __init__(self):
        self.proc = None
        self.log = collections.deque(maxlen=600)

    def start(self):
        if self.proc and self.proc.poll() is None:
            return True
        args = [sys.executable]
        if not getattr(sys, "frozen", False):
            args.append(os.path.join(paths.resource_dir(), "app.py"))
        args.append("--backend")
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        env.setdefault("VT_INJECT_OVERLAY", "1")   # on by default; honour VT_INJECT_OVERLAY=0 to disable all overlays
        self.log.clear()
        self.proc = subprocess.Popen(
            args, cwd=paths.resource_dir(), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=NO_WINDOW)
        threading.Thread(target=self._pump, daemon=True).start()
        return True

    def _pump(self):
        try:
            for line in self.proc.stdout:
                self.log.append(line.rstrip("\n"))
        except Exception:
            pass

    def stop(self):
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
        return True

    def running(self):
        return bool(self.proc and self.proc.poll() is None)


# ----------------------------------------------------------------------------- js_api bridge
class Api:
    def __init__(self):
        self.engine = Engine()

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

    def get_log(self):
        return "\n".join(self.engine.log)

    def cuda_status(self):
        try:
            from cuda_setup import cuda_present
            return bool(cuda_present())
        except Exception:
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
def run_gui():
    import webview
    api = Api()
    window = webview.create_window(
        "Discord Live Transcriber",
        url=paths.resource("ui", "index.html"),
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
    if "--backend" in sys.argv:
        run_backend()
    else:
        run_gui()


if __name__ == "__main__":
    main()
