"""
Coup Game Launcher
Checks GitHub for the latest Coup release, downloads/updates if needed, then launches the game.
"""

import sys
import json
import platform
import threading
import subprocess
import zipfile
import tarfile
import shutil
import ssl
import urllib.request
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

# Build an SSL context that works on Windows.
# Windows Python installs often lack a trusted CA bundle, so we try certifi
# first, then fall back to disabling verification (GitHub URLs only).
def _build_ssl_ctx() -> ssl.SSLContext:
    try:
        import certifi
        print("[ssl] Using certifi CA bundle")
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    # On Windows, load_default_certs() succeeds but the resulting context
    # still cannot verify GitHub's cert chain. Skip it and go straight to
    # the unverified context so the launcher actually works.
    print("[ssl] certifi not found; disabling SSL verification (GitHub-only)")
    ctx = ssl._create_unverified_context()
    return ctx

SSL_CTX = _build_ssl_ctx()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
GITHUB_USER = "lmartim4"
GITHUB_REPO = "Coup"
API_URL = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/releases/latest"

PLATFORM = platform.system()  # "Windows", "Linux", "Darwin"

# Store game files alongside the launcher executable
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent

GAME_DIR = BASE_DIR / "game_files"
VERSION_FILE = GAME_DIR / "version.txt"

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
BG      = "#1a1a2e"
CARD    = "#16213e"
ACCENT  = "#e94560"
TEXT    = "#eaeaea"
MUTED   = "#aaaaaa"
SUCCESS = "#4caf50"

# ---------------------------------------------------------------------------
# Core update logic
# ---------------------------------------------------------------------------

def get_local_version() -> str:
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text().strip()
    return "Not installed"


def get_remote_info():
    """Returns (tag_name, assets_list) from the latest GitHub release, or (None, reason_str)."""
    try:
        req = urllib.request.Request(
            API_URL, headers={"User-Agent": "CoupLauncher/1.0"}
        )
        with urllib.request.urlopen(req, timeout=10, context=SSL_CTX) as resp:
            raw = resp.read().decode()
            data = json.loads(raw)

            if "tag_name" not in data:
                msg = data.get("message", "Unexpected API response")
                print(f"[remote_info] GitHub API error: {msg}")
                print(f"[remote_info] Full response: {raw[:500]}")
                return None, msg

            return data["tag_name"], data["assets"]

    except urllib.error.HTTPError as e:
        reason = f"HTTP {e.code}: {e.reason}"
        print(f"[remote_info] HTTPError: {reason}")
        return None, reason
    except urllib.error.URLError as e:
        reason = str(e.reason)
        print(f"[remote_info] URLError: {reason}")
        return None, reason
    except Exception as e:
        print(f"[remote_info] Unexpected error: {type(e).__name__}: {e}")
        return None, str(e)


def find_executable(search_dir: Path):
    """Recursively search for the CoupGame executable in the extracted directory."""
    exe_name = "CoupGame.exe" if PLATFORM == "Windows" else "CoupGame"
    for path in search_dir.rglob(exe_name):
        if path.is_file():
            return path
    return None


def download_and_extract(assets: list, progress_cb, status_cb) -> bool:
    """
    Download the platform-appropriate release asset and extract it to GAME_DIR.
    progress_cb(float 0-100) and status_cb(str) are called from a background thread.
    """
    os_label = {"Windows": "Windows", "Linux": "Linux", "Darwin": "macOS"}.get(PLATFORM)
    if not os_label:
        status_cb(f"Unsupported platform: {PLATFORM}")
        return False

    asset = next((a for a in assets if os_label in a["name"]), None)
    if not asset:
        status_cb(f"No release found for {os_label}")
        return False

    url        = asset["browser_download_url"]
    fname      = asset["name"]
    total_size = asset.get("size", 0)
    tmp_path   = BASE_DIR / fname

    status_cb(f"Downloading {fname}...")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "CoupLauncher/1.0"})
        with urllib.request.urlopen(req, context=SSL_CTX) as resp, open(tmp_path, "wb") as out:
            downloaded = 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                out.write(chunk)
                downloaded += len(chunk)
                if total_size:
                    progress_cb(downloaded / total_size * 100)
    except Exception as e:
        status_cb(f"Download failed: {e}")
        if tmp_path.exists():
            tmp_path.unlink()
        return False

    status_cb("Extracting files...")
    progress_cb(100)

    # Remove old installation before extracting
    if GAME_DIR.exists():
        shutil.rmtree(GAME_DIR)
    GAME_DIR.mkdir(parents=True, exist_ok=True)

    try:
        if fname.endswith(".zip"):
            with zipfile.ZipFile(tmp_path) as zf:
                zf.extractall(GAME_DIR)
        else:
            with tarfile.open(tmp_path, "r:gz") as tf:
                try:
                    tf.extractall(GAME_DIR, filter="data")  # Python 3.12+
                except TypeError:
                    tf.extractall(GAME_DIR)
    except Exception as e:
        status_cb(f"Extraction failed: {e}")
        return False
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    # Ensure executable bit is set on Unix
    exe = find_executable(GAME_DIR)
    if exe and PLATFORM != "Windows":
        exe.chmod(0o755)

    return True


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class LauncherApp(tk.Tk):
    lbl_local:  tk.Label
    lbl_remote: tk.Label
    lbl_status: tk.Label
    progress:   ttk.Progressbar
    btn_play:   tk.Button

    def __init__(self):
        super().__init__()
        self.title("Coup Launcher")
        self.resizable(False, False)
        self.configure(bg=BG)
        self._build_ui()
        self._center_window()
        self.after(200, self._start_update_check)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        # Header
        tk.Label(self, text="COUP",
                 font=("Georgia", 56, "bold"), bg=BG, fg=ACCENT).pack(pady=(30, 0))
        tk.Label(self, text="G A M E   L A U N C H E R",
                 font=("Arial", 10), bg=BG, fg=MUTED).pack()

        # Version card
        card = tk.Frame(self, bg=CARD, padx=20, pady=14)
        card.pack(fill="x", padx=40, pady=(18, 6))

        for label_text, attr in [("Installed:", "lbl_local"), ("Latest:", "lbl_remote")]:
            row = tk.Frame(card, bg=CARD)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label_text, font=("Arial", 10),
                     bg=CARD, fg=MUTED, width=10, anchor="w").pack(side="left")
            lbl = tk.Label(row, text="—", font=("Arial", 10, "bold"), bg=CARD, fg=TEXT)
            lbl.pack(side="left")
            setattr(self, attr, lbl)

        self.lbl_local.config(text=get_local_version())

        # Status label
        self.lbl_status = tk.Label(
            self, text="", font=("Arial", 9),
            bg=BG, fg=MUTED, wraplength=320
        )
        self.lbl_status.pack(pady=(10, 4))

        # Progress bar
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Coup.Horizontal.TProgressbar",
            troughcolor=CARD, background=ACCENT,
            bordercolor=CARD, lightcolor=ACCENT, darkcolor=ACCENT,
            thickness=8,
        )
        self.progress = ttk.Progressbar(
            self, length=320, mode="determinate",
            style="Coup.Horizontal.TProgressbar"
        )
        self.progress.pack(pady=(0, 18))

        # Play button
        self.btn_play = tk.Button(
            self, text="PLAY",
            font=("Arial", 14, "bold"),
            bg=ACCENT, fg="white",
            activebackground="#c73652", activeforeground="white",
            relief="flat", cursor="hand2",
            width=20, height=2,
            command=self._on_play,
            state="disabled",
        )
        self.btn_play.pack(pady=(0, 30))

        self.geometry("400x430")

    def _center_window(self):
        self.update_idletasks()
        w = self.winfo_width()
        h = self.winfo_height()
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    # ------------------------------------------------------------------
    # Thread-safe UI helpers
    # ------------------------------------------------------------------

    def _set_status(self, msg: str, color: str = MUTED):
        self.after(0, lambda m=msg, c=color: self.lbl_status.config(text=m, fg=c))

    def _set_progress(self, val: float):
        self.after(0, lambda v=val: self.progress.config(value=v))

    def _set_remote_label(self, text: str):
        self.after(0, lambda t=text: self.lbl_remote.config(text=t))

    def _set_local_label(self, text: str):
        self.after(0, lambda t=text: self.lbl_local.config(text=t))

    def _enable_play(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        self.after(0, lambda s=state: self.btn_play.config(state=s))

    # ------------------------------------------------------------------
    # Update logic (runs in a background thread)
    # ------------------------------------------------------------------

    def _start_update_check(self):
        threading.Thread(target=self._update_worker, daemon=True).start()

    def _update_worker(self):
        self._set_status("Checking for updates...")
        local_ver  = get_local_version()
        remote_ver, assets = get_remote_info()

        if remote_ver is None:
            error_reason = assets if isinstance(assets, str) else "Unknown error"
            self._set_remote_label("Unavailable")
            print(f"[update_worker] Failed to get remote info: {error_reason}")
            if local_ver != "Not installed":
                self._set_status(f"Could not reach GitHub ({error_reason}). Playing offline.")
                self._set_progress(100)
                self._enable_play(True)
            else:
                self._set_status(f"Cannot install: {error_reason}", ACCENT)
            return

        self._set_remote_label(remote_ver)

        if local_ver == remote_ver:
            self._set_status("Game is up to date.", SUCCESS)
            self._set_progress(100)
            self._enable_play(True)
            return

        action = "Updating to" if local_ver != "Not installed" else "Installing"
        self._set_status(f"{action} {remote_ver}...")
        success = download_and_extract(assets, self._set_progress, self._set_status)

        if success:
            VERSION_FILE.write_text(remote_ver)
            self._set_local_label(remote_ver)
            self._set_status("Ready to play!", SUCCESS)
            self._enable_play(True)
        else:
            if local_ver != "Not installed":
                self._set_status("Update failed. You can play the previous version.", MUTED)
                self._enable_play(True)
            else:
                self._set_status("Installation failed. Check your connection.", ACCENT)

    # ------------------------------------------------------------------
    # Launch
    # ------------------------------------------------------------------

    def _on_play(self):
        exe = find_executable(GAME_DIR)
        if not exe:
            messagebox.showerror(
                "Error",
                "Game executable not found.\nRestart the launcher to reinstall."
            )
            return
        try:
            subprocess.Popen([str(exe)], cwd=str(exe.parent))
            self.after(800, self.destroy)
        except Exception as e:
            messagebox.showerror("Launch Error", f"Failed to start the game:\n{e}")


if __name__ == "__main__":
    app = LauncherApp()
    app.mainloop()