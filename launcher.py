"""
Coup Game Launcher
Checks GitHub for Coup and CoupLauncher releases, auto-updates the game,
lets the user pick any game version, and supports one-click launcher self-update.
"""

import os
import sys
import json
import platform
import threading
import subprocess
import zipfile
import tarfile
import shutil
import ssl
import random
import urllib.request
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

# Version injected at build time by build_launcher.py (via _version.py).
# Falls back to "v0.0.0-dev" when running from source.
try:
    from _version import LAUNCHER_VERSION
except ImportError:
    LAUNCHER_VERSION = "v0.0.0-dev"

# ---------------------------------------------------------------------------
# SSL context
# ---------------------------------------------------------------------------

def _build_ssl_ctx() -> ssl.SSLContext:
    try:
        import certifi
        print("[ssl] Using certifi CA bundle")
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    print("[ssl] certifi not found; disabling SSL verification (GitHub-only)")
    return ssl._create_unverified_context()

SSL_CTX = _build_ssl_ctx()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GITHUB_USER          = "lmartim4"
GITHUB_REPO          = "Coup"
LAUNCHER_GITHUB_USER = "lmartim4"
LAUNCHER_GITHUB_REPO = "CoupLauncher"

GAME_API_ALL   = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/releases"
LAUNCHER_API   = f"https://api.github.com/repos/{LAUNCHER_GITHUB_USER}/{LAUNCHER_GITHUB_REPO}/releases/latest"

PLATFORM  = platform.system()   # "Windows", "Linux", "Darwin"
IS_FROZEN = getattr(sys, "frozen", False)

if IS_FROZEN:
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent

GAME_DIR     = BASE_DIR / "game_files"
VERSION_FILE = GAME_DIR / "version.txt"

# ---------------------------------------------------------------------------
# Coup tips (shown randomly at the bottom of the launcher)
# ---------------------------------------------------------------------------

COUP_TIPS = [
    "If you have 10 or more coins, you MUST Coup. Plan your economy around this.",
    "The Duke is the backbone of any economy. Claiming it early discourages Foreign Aid.",
    "Bluffing the Contessa right after losing a card surprises opponents who think you're weak.",
    "Watch the discard pile. If two Dukes are already dead, that Duke claim is a lie.",
    "Saving up for a Coup is often safer than Assassinating — it can't be blocked or challenged.",
    "The Ambassador is underrated. Swapping bad cards mid-game can completely reset your position.",
    "Never reveal desperation. Confident body language (even in a digital game) sells the bluff.",
    "Target the richest player before they reach 7 coins, not after.",
    "If everyone is afraid to challenge you, you don't even need the card you're claiming.",
    "Two-card players can afford risks. One-card players should play conservatively.",
    "Claiming Captain on a player with only 1 coin is a tell — there's nothing to steal.",
    "Let others eliminate each other early. Patience is a weapon.",
    "If you've shown a real Duke before, claiming it again later is nearly unchallengeable.",
    "The Assassin at 3 coins is terrifying. Don't let anyone sit at exactly 3 coins for long.",
    "Challenging early in the game when stakes are low teaches you who bluffs under pressure.",
    "A blocked action still cost your opponent their turn. Blocking is never wasted.",
    "Claiming Ambassador when you have 6+ coins is suspicious — why not just Coup?",
    "Keep mental notes of which cards opponents have revealed or lost. Information is power.",
    "The first Coup of the game sets the tone. Make it a political statement, not just math.",
    "If two players are allied, break it up fast — a 2v1 endgame is nearly impossible to win.",
    "Calling a bluff is a gamble. Ask yourself: what do I lose if I'm wrong?",
    "Foreign Aid is bait. Use it to find out who has (or claims) the Duke.",
    "Sometimes doing nothing and taking 1 coin Income is the best move — don't telegraph plans.",
    "A Captain claim pairs well with an Ambassador claim: one steals, the other refreshes.",
    "In endgame, every challenge matters. Both players are fragile — force the risk onto them.",
    "Losing a card on purpose can be a strategy if it hides your real remaining card.",
    "Players who never bluff are predictable. Mix real and fake claims to stay unreadable.",
    "Don't Assassinate the player who is about to Coup someone else — let them do your work.",
    "If someone blocks your steal with a Captain, they probably have it. File that away.",
    "The best liars believe their own lie. Commit fully or don't bluff at all.",
]

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------

BG      = "#1a1a2e"
CARD    = "#16213e"
ACCENT  = "#e94560"
TEXT    = "#eaeaea"
MUTED   = "#aaaaaa"
SUCCESS = "#4caf50"
WARN    = "#ff9800"

# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _fetch_json(url: str):
    """Fetch JSON from a URL. Returns (parsed_data, None) or (None, error_str)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "CoupLauncher/1.0"})
        with urllib.request.urlopen(req, timeout=10, context=SSL_CTX) as resp:
            return json.loads(resp.read().decode()), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return None, str(e.reason)
    except Exception as e:
        return None, str(e)

# ---------------------------------------------------------------------------
# Game helpers
# ---------------------------------------------------------------------------

def get_local_version() -> str:
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text().strip()
    return "Not installed"


def get_all_game_releases():
    """Return ([(tag, assets), ...], None) or (None, error_str)."""
    data, err = _fetch_json(GAME_API_ALL)
    if err:
        return None, err
    if not isinstance(data, list):
        return None, "Unexpected API response"
    releases = [
        (r["tag_name"], r["assets"])
        for r in data
        if not r.get("draft") and "tag_name" in r and "assets" in r
    ]
    return releases, None


def find_executable(search_dir: Path):
    exe_name = "CoupGame.exe" if PLATFORM == "Windows" else "CoupGame"
    for path in search_dir.rglob(exe_name):
        if path.is_file():
            return path
    return None


def download_and_extract(assets: list, progress_cb, status_cb) -> bool:
    """Download the platform asset from `assets` and extract it to GAME_DIR."""
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

    exe = find_executable(GAME_DIR)
    if exe and PLATFORM != "Windows":
        exe.chmod(0o755)

    return True

# ---------------------------------------------------------------------------
# Launcher self-update helpers
# ---------------------------------------------------------------------------

def get_launcher_remote_info():
    """Return (tag_name, assets) for the latest launcher release, or (None, err)."""
    data, err = _fetch_json(LAUNCHER_API)
    if err:
        return None, err
    if data is None or "tag_name" not in data:
        msg = data.get("message", "Unexpected API response") if data else "Empty response"
        return None, msg
    return data["tag_name"], data["assets"]


def download_launcher_update(assets: list, progress_cb, status_cb) -> "Path | None":
    """
    Download the launcher archive for this platform and extract the binary.
    Returns the path to the extracted binary (pending update), or None on failure.
    Self-update is not supported on macOS (app bundle layout).
    """
    os_label = {"Windows": "Windows", "Linux": "Linux", "Darwin": "macOS"}.get(PLATFORM)
    if not os_label:
        status_cb(f"Unsupported platform: {PLATFORM}")
        return None

    asset = next((a for a in assets if os_label in a["name"]), None)
    if not asset:
        status_cb(f"No launcher asset found for {os_label}")
        return None

    fname      = asset["name"]
    url        = asset["browser_download_url"]
    total_size = asset.get("size", 0)
    tmp_archive = BASE_DIR / fname

    status_cb(f"Downloading launcher update {fname}...")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "CoupLauncher/1.0"})
        with urllib.request.urlopen(req, context=SSL_CTX) as resp, open(tmp_archive, "wb") as out:
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
        if tmp_archive.exists():
            tmp_archive.unlink()
        return None

    status_cb("Extracting launcher update...")
    progress_cb(100)

    exe_in_archive = "CoupLauncher.exe" if PLATFORM == "Windows" else "CoupLauncher"
    pending_name   = "CoupLauncher_pending.exe" if PLATFORM == "Windows" else "CoupLauncher_pending"
    pending_path   = BASE_DIR / pending_name

    try:
        if fname.endswith(".zip"):
            with zipfile.ZipFile(tmp_archive) as zf:
                with zf.open(exe_in_archive) as src, open(pending_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        else:
            with tarfile.open(tmp_archive, "r:gz") as tf:
                member = tf.getmember(exe_in_archive)
                src = tf.extractfile(member)
                if src is None:
                    raise ValueError(f"Cannot read {exe_in_archive} from archive")
                with src, open(pending_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    except Exception as e:
        status_cb(f"Extraction failed: {e}")
        if pending_path.exists():
            pending_path.unlink()
        return None
    finally:
        if tmp_archive.exists():
            tmp_archive.unlink()

    if PLATFORM != "Windows":
        os.chmod(str(pending_path), 0o755)

    return pending_path


def apply_launcher_update(pending_path: Path):
    """
    Replace the running launcher with the pending binary, then restart.
    - Linux/macOS: overwrite in-place and exec (no restart gap needed).
    - Windows: spawn a bat script that swaps after the process exits.
    """
    current_exe = Path(sys.executable)

    if PLATFORM == "Windows":
        bat = BASE_DIR / "_coup_launcher_update.bat"
        bat.write_text(
            "@echo off\n"
            "ping -n 3 127.0.0.1 > nul\n"
            f'move /y "{pending_path}" "{current_exe}"\n'
            f'start "" "{current_exe}"\n'
            'del "%~f0"\n',
            encoding="utf-8",
        )
        _NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        _DETACHED  = getattr(subprocess, "DETACHED_PROCESS",  0x00000008)
        subprocess.Popen(
            ["cmd", "/c", str(bat)],
            creationflags=_NO_WINDOW | _DETACHED,
            close_fds=True,
        )
        sys.exit(0)
    else:
        # Linux: safe to overwrite a running binary (kernel holds the old inode open).
        shutil.copy2(str(pending_path), str(current_exe))
        pending_path.unlink(missing_ok=True)
        os.execv(str(current_exe), sys.argv)

# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class LauncherApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Coup Launcher")
        self.resizable(False, False)
        self.configure(bg=BG)

        self._releases: list = []          # [(tag_name, assets), ...]
        self._launcher_assets = None       # assets for pending launcher update
        self._installed_ver = get_local_version()

        self._build_ui()
        self._center_window()
        self.after(200, self._start_init)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        # ---- Title ----
        tk.Label(self, text="COUP",
                 font=("Georgia", 56, "bold"), bg=BG, fg=ACCENT).pack(pady=(30, 0))
        tk.Label(self, text="G A M E   L A U N C H E R",
                 font=("Arial", 10), bg=BG, fg=MUTED).pack()

        # ---- Launcher version card ----
        lcard = tk.Frame(self, bg=CARD, padx=20, pady=10)
        lcard.pack(fill="x", padx=40, pady=(18, 4))

        lrow = tk.Frame(lcard, bg=CARD)
        lrow.pack(fill="x")

        tk.Label(lrow, text="Launcher:", font=("Arial", 10),
                 bg=CARD, fg=MUTED, width=10, anchor="w").pack(side="left")
        tk.Label(lrow, text=LAUNCHER_VERSION, font=("Arial", 10, "bold"),
                 bg=CARD, fg=TEXT).pack(side="left")

        self.lbl_launcher_status = tk.Label(
            lrow, text="  Checking...", font=("Arial", 9), bg=CARD, fg=MUTED
        )
        self.lbl_launcher_status.pack(side="left", padx=(6, 0))

        self.btn_launcher_update = tk.Button(
            lrow, text="Update Launcher",
            font=("Arial", 9, "bold"),
            bg=WARN, fg="white",
            activebackground="#e65c00", activeforeground="white",
            relief="flat", cursor="hand2",
            command=self._on_launcher_update,
            state="disabled",
        )
        # Packed dynamically when an update is available

        # ---- Game version card ----
        gcard = tk.Frame(self, bg=CARD, padx=20, pady=14)
        gcard.pack(fill="x", padx=40, pady=(4, 6))

        row1 = tk.Frame(gcard, bg=CARD)
        row1.pack(fill="x", pady=2)
        tk.Label(row1, text="Installed:", font=("Arial", 10),
                 bg=CARD, fg=MUTED, width=10, anchor="w").pack(side="left")
        self.lbl_installed = tk.Label(
            row1, text=self._installed_ver,
            font=("Arial", 10, "bold"), bg=CARD, fg=TEXT
        )
        self.lbl_installed.pack(side="left")

        row2 = tk.Frame(gcard, bg=CARD)
        row2.pack(fill="x", pady=2)
        tk.Label(row2, text="Version:", font=("Arial", 10),
                 bg=CARD, fg=MUTED, width=10, anchor="w").pack(side="left")

        # Style setup (must happen before widgets that use the styles)
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Coup.Horizontal.TProgressbar",
            troughcolor=CARD, background=ACCENT,
            bordercolor=CARD, lightcolor=ACCENT, darkcolor=ACCENT,
            thickness=8,
        )
        style.configure(
            "Coup.TCombobox",
            fieldbackground=BG, background=CARD,
            foreground=TEXT, selectbackground=ACCENT,
            selectforeground="white",
            arrowcolor=TEXT,
        )
        style.map("Coup.TCombobox",
                  fieldbackground=[("readonly", BG)],
                  foreground=[("disabled", MUTED)])

        self._selected_var = tk.StringVar(value="Loading...")
        self.combo_version = ttk.Combobox(
            row2, textvariable=self._selected_var,
            state="disabled", style="Coup.TCombobox",
            width=24, font=("Arial", 9),
        )
        self.combo_version.pack(side="left")
        self.combo_version.bind("<<ComboboxSelected>>", self._on_version_selected)

        # ---- Status + progress + play ----
        self.lbl_status = tk.Label(
            self, text="", font=("Arial", 9),
            bg=BG, fg=MUTED, wraplength=360
        )
        self.lbl_status.pack(pady=(12, 4))

        self.progress = ttk.Progressbar(
            self, length=360, mode="determinate",
            style="Coup.Horizontal.TProgressbar"
        )
        self.progress.pack(pady=(0, 18))

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
        self.btn_play.pack(pady=(0, 20))

        # ---- Tip of the day ----
        tip_frame = tk.Frame(self, bg=CARD, padx=16, pady=10)
        tip_frame.pack(fill="x", padx=40, pady=(0, 24))

        tk.Label(tip_frame, text="TIP", font=("Arial", 7, "bold"),
                 bg=CARD, fg=ACCENT).pack(anchor="w")
        tk.Label(tip_frame, text=random.choice(COUP_TIPS),
                 font=("Arial", 9), bg=CARD, fg=MUTED,
                 wraplength=340, justify="left").pack(anchor="w")

        self.geometry("440x600")

    def _center_window(self):
        self.update_idletasks()
        w = self.winfo_width()
        h = self.winfo_height()
        x = (self.winfo_screenwidth()  - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    # ------------------------------------------------------------------
    # Thread-safe UI helpers
    # ------------------------------------------------------------------

    def _ui(self, fn):
        self.after(0, fn)

    def _set_status(self, msg: str, color: str = MUTED):
        self._ui(lambda m=msg, c=color: self.lbl_status.config(text=m, fg=c))

    def _set_progress(self, val: float):
        self._ui(lambda v=val: self.progress.config(value=v))

    def _set_installed_label(self, text: str):
        self._ui(lambda t=text: self.lbl_installed.config(text=t))

    def _enable_play(self, enabled: bool, text: str = "PLAY"):
        self._ui(lambda e=enabled, t=text:
                 self.btn_play.config(state="normal" if e else "disabled", text=t))

    def _set_launcher_status(self, text: str, color: str = MUTED):
        self._ui(lambda t=text, c=color: self.lbl_launcher_status.config(text=t, fg=c))

    def _show_launcher_update_btn(self, show: bool):
        def _do():
            if show:
                self.btn_launcher_update.config(state="normal")
                self.btn_launcher_update.pack(side="right", padx=(8, 0))
            else:
                self.btn_launcher_update.pack_forget()
        self._ui(_do)

    def _populate_combo(self, releases):
        def _do():
            values = [
                f"{tag} (Latest)" if i == 0 else tag
                for i, (tag, _) in enumerate(releases)
            ]
            self.combo_version.config(values=values, state="readonly")
            if values:
                self.combo_version.current(0)
        self._ui(_do)

    # ------------------------------------------------------------------
    # Init worker — runs once at startup
    # ------------------------------------------------------------------

    def _start_init(self):
        threading.Thread(target=self._init_worker, daemon=True).start()

    def _init_worker(self):
        self._set_status("Checking for updates...")

        # Fetch game releases and launcher info in parallel
        results: dict = {}

        def fetch_game():
            results["game"] = get_all_game_releases()

        def fetch_launcher():
            results["launcher"] = get_launcher_remote_info()

        t1 = threading.Thread(target=fetch_game,     daemon=True)
        t2 = threading.Thread(target=fetch_launcher, daemon=True)
        t1.start(); t2.start()
        t1.join();  t2.join()

        # ---- Launcher update check ----
        launcher_tag, launcher_info = results.get("launcher") or (None, None)
        if launcher_tag is not None:
            if launcher_tag != LAUNCHER_VERSION:
                self._launcher_assets = launcher_info
                self._set_launcher_status(f"  {launcher_tag} available", WARN)
                # Self-update supported on Linux and Windows only
                if IS_FROZEN and PLATFORM != "Darwin":
                    self._show_launcher_update_btn(True)
            else:
                self._set_launcher_status("  Up to date", SUCCESS)
        else:
            self._set_launcher_status("  (offline)", MUTED)

        # ---- Game releases ----
        releases, err = results.get("game") or (None, "Network error")
        if err or not releases:
            reason = err or "No releases found."
            self._set_status(f"Could not fetch game versions: {reason}", ACCENT)
            if self._installed_ver != "Not installed":
                self._set_status("Could not check for updates. Playing offline.", MUTED)
                self._set_progress(100)
                self._enable_play(True)
            return

        self._releases = releases
        latest_tag, latest_assets = releases[0]
        self._populate_combo(releases)

        if self._installed_ver == latest_tag:
            self._set_status("Game is up to date.", SUCCESS)
            self._set_progress(100)
            self._enable_play(True)
            return

        # Auto-download latest game
        action = "Updating to" if self._installed_ver != "Not installed" else "Installing"
        self._set_status(f"{action} {latest_tag}...")
        success = download_and_extract(latest_assets, self._set_progress, self._set_status)

        if success:
            VERSION_FILE.write_text(latest_tag)
            self._installed_ver = latest_tag
            self._set_installed_label(latest_tag)
            self._set_status("Ready to play!", SUCCESS)
            self._enable_play(True)
        else:
            if self._installed_ver != "Not installed":
                self._set_status("Update failed. You can still play the previous version.", MUTED)
                self._enable_play(True)
            else:
                self._set_status("Installation failed. Check your connection.", ACCENT)

    # ------------------------------------------------------------------
    # Version combobox
    # ------------------------------------------------------------------

    def _on_version_selected(self, _=None):
        if not self._releases:
            return
        idx = self.combo_version.current()
        if idx < 0 or idx >= len(self._releases):
            return
        tag, _ = self._releases[idx]
        if tag == self._installed_ver:
            self._enable_play(True, "PLAY")
            self._set_status("Version already installed.", SUCCESS)
        else:
            label = "INSTALL & PLAY" if self._installed_ver == "Not installed" else "SWITCH & PLAY"
            self._enable_play(True, label)
            self._set_status(
                f"{tag} will be downloaded when you press play.", MUTED
            )

    def _get_selected_release(self):
        """Return (tag, assets) for the current combobox selection, or (None, None)."""
        if not self._releases:
            return None, None
        idx = self.combo_version.current()
        if idx < 0 or idx >= len(self._releases):
            return None, None
        return self._releases[idx]

    # ------------------------------------------------------------------
    # Play / version switch
    # ------------------------------------------------------------------

    def _on_play(self):
        tag, assets = self._get_selected_release()

        # If a different version is selected, download it first
        if tag and tag != self._installed_ver:
            self._enable_play(False)
            threading.Thread(
                target=self._switch_version_worker,
                args=(tag, assets),
                daemon=True,
            ).start()
            return

        # Launch the installed game
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

    def _switch_version_worker(self, tag: str, assets: list):
        self._set_status(f"Downloading {tag}...")
        success = download_and_extract(assets, self._set_progress, self._set_status)
        if success:
            VERSION_FILE.write_text(tag)
            self._installed_ver = tag
            self._set_installed_label(tag)
            self._set_status("Launching...", SUCCESS)
            exe = find_executable(GAME_DIR)

            def _launch():
                if exe:
                    try:
                        subprocess.Popen([str(exe)], cwd=str(exe.parent))
                        self.after(800, self.destroy)
                    except Exception as e:
                        messagebox.showerror("Launch Error", f"Failed to start the game:\n{e}")
                        self._enable_play(True)
                else:
                    messagebox.showerror("Error", "Game executable not found after installation.")
                    self._enable_play(True)

            self._ui(_launch)
        else:
            self._set_status("Download failed. Select another version.", ACCENT)
            self._enable_play(True)

    # ------------------------------------------------------------------
    # Launcher self-update
    # ------------------------------------------------------------------

    def _on_launcher_update(self):
        if not self._launcher_assets:
            return
        self.btn_launcher_update.config(state="disabled")
        self._enable_play(False)
        threading.Thread(target=self._launcher_update_worker, daemon=True).start()

    def _launcher_update_worker(self):
        assets = self._launcher_assets
        if not assets:
            return
        pending = download_launcher_update(
            assets, self._set_progress, self._set_status
        )
        if not pending:
            self._set_status("Launcher update failed. Try again later.", ACCENT)
            self._ui(lambda: self.btn_launcher_update.config(state="normal"))
            self._enable_play(True)
            return

        self._set_status("Applying update and restarting...", SUCCESS)
        apply_launcher_update(pending)
        # Reached only if exec failed (shouldn't happen on Linux/Windows)
        self._set_status("Launcher updated. Please restart manually.", SUCCESS)


if __name__ == "__main__":
    app = LauncherApp()
    app.mainloop()
