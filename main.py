from __future__ import annotations

import asyncio
import os
import re
import sys
import threading
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
import tkinter as tk
from tkinter import ttk, messagebox, filedialog


FIGURINIFY_URL = "https://andytwoods.github.io/Figurinify/"


@dataclass
class Resolved:
    glb_url: str
    filename: str


def _is_url(s: str) -> bool:
    try:
        u = urlparse(s)
        return u.scheme in ("http", "https") and bool(u.netloc)
    except Exception:
        return False


def _guess_filename_from_url(url: str) -> str:
    path = urlparse(url).path
    name = os.path.basename(path) or "model.glb"
    if not name.lower().endswith(".glb"):
        # keep extension if there is one, otherwise default to .glb
        if "." not in name:
            name += ".glb"
    return name


def _extract_model_id(text: str) -> Optional[str]:
    """
    best-effort: pulls a meshy model id like:
    v2-019a7474-3f2a-7a7d-9282-cc5599095a44
    """
    m = re.search(r"(v2-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})", text, re.IGNORECASE)
    return m.group(1) if m else None


def resolve_to_glb_url(user_input: str, log) -> Resolved:
    """
    tries (in order):
    1) direct .glb url
    2) any url that contains a .glb url in its html (best-effort)
    3) meshy model page built from a detected model id, then scan html for .glb
    """
    s = user_input.strip()

    # 1) direct .glb
    if _is_url(s) and s.lower().split("?")[0].endswith(".glb"):
        return Resolved(glb_url=s, filename=_guess_filename_from_url(s))

    # helper: fetch page + scan for glb url
    def scan_page_for_glb(page_url: str) -> Optional[str]:
        log(f"fetching page to look for a .glb link – {page_url}")
        r = requests.get(
            page_url,
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; GLB-Downloader/1.0)"
            },
            allow_redirects=True,
        )
        r.raise_for_status()
        html = r.text

        # helper to normalize possibly escaped/encoded GLB URLs extracted from HTML/JSON
        def _normalize_found_url(raw: str) -> str:
            import re as _re
            try:
                s = raw.strip()
                # Remove a single trailing backslash that sometimes appears in JSON strings
                if s.endswith("\\") and not s.endswith("\\\\"):
                    s = s[:-1]

                # First, unescape common JSON escape sequences
                s = s.replace("\\/", "/").replace("\\\"", '"')
                # Reduce double backslashes to single where appropriate (but avoid killing URL backslashes in schemes)
                s = s.replace("\\\\", "\\")

                # Convert double-escaped unicode sequences like \\u0026 -> &
                def _u_repl(m):
                    try:
                        return chr(int(m.group(1), 16))
                    except Exception:
                        return m.group(0)
                s = _re.sub(r"\\\\u([0-9a-fA-F]{4})", _u_repl, s)

                # Then convert single-escaped unicode sequences like \u0026 -> &
                s = _re.sub(r"\\u([0-9a-fA-F]{4})", _u_repl, s)

                # HTML-unescape entities like &amp; -> &
                try:
                    import html as _html
                    s = _html.unescape(s)
                except Exception:
                    pass

                # Final cleanup of whitespace
                s = s.strip()
                return s
            except Exception:
                return raw

        # look for absolute .glb urls first
        candidates = re.findall(r"https?://[^\s\"']+?\.glb(?:\?[^\s\"']+)?", html, flags=re.IGNORECASE)
        if candidates:
            norm = _normalize_found_url(candidates[0])
            if norm != candidates[0]:
                log(f"normalized url: {candidates[0]} -> {norm}")
            return norm

        # occasionally a relative path might appear – this is a fallback
        # use urljoin to correctly resolve both root-absolute and page-relative links
        from urllib.parse import urljoin

        # 2a) root-absolute like "/assets/model.glb"
        rel = re.findall(r"(/[^\s\"']+?\.glb(?:\?[^\s\"']+)?)", html, flags=re.IGNORECASE)
        if rel:
            joined = urljoin(r.url, rel[0])
            norm = _normalize_found_url(joined)
            if norm != joined:
                log(f"normalized url: {joined} -> {norm}")
            return norm

        # 2b) truly relative like "assets/model.glb" referenced in href/src
        rel2 = re.findall(
            r"(?:href|src)=[\"'](?!https?://)([^\"']+?\.glb(?:\?[^\"']+)?)[\"']",
            html,
            flags=re.IGNORECASE,
        )
        if rel2:
            joined = urljoin(r.url, rel2[0])
            norm = _normalize_found_url(joined)
            if norm != joined:
                log(f"normalized url: {joined} -> {norm}")
            return norm
        
        return None

    # 2) user pasted a normal url (maybe a meshy model page)
    if _is_url(s):
        glb = scan_page_for_glb(s)
        if glb:
            return Resolved(glb_url=glb, filename=_guess_filename_from_url(glb))

    # 3) user pasted an id / share code-ish string
    model_id = _extract_model_id(s)
    if model_id:
        # meshy model page pattern (best-effort)
        # if your real links differ, adjust this template
        page = f"https://www.meshy.ai/3d-models/{model_id}"
        glb = scan_page_for_glb(page)
        if glb:
            return Resolved(glb_url=glb, filename=_guess_filename_from_url(glb))

    raise ValueError(
        "couldn’t find a direct .glb download link from what you pasted.\n"
        "tip – in meshy, use the download/export option and copy the direct .glb link if available."
    )


def download_file(url: str, out_path: Path, progress_cb, log) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with requests.get(url, stream=True, timeout=60, allow_redirects=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        got = 0

        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 512):
                if not chunk:
                    continue
                f.write(chunk)
                got += len(chunk)

                if total > 0:
                    pct = int(got * 100 / total)
                    progress_cb(pct)
                else:
                    # unknown size – just pulse a little
                    progress_cb(min(99, (got // (1024 * 1024)) % 100))

    progress_cb(100)
    log(f"saved – {out_path}")

class TkDownloaderApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("meshy glb downloader")
        # Basic cross-platform sizing
        self.geometry("720x480")

        # Variables
        self.input_var = tk.StringVar()
        self.status_var = tk.StringVar(value="ready")
        self.progress_var = tk.IntVar(value=0)

        # Default download directory (User's Documents/Figurinify)
        try:
            self.download_dir = (Path.home() / "Documents" / "Figurinify").resolve()
        except Exception:
            self.download_dir = (Path.cwd() / "downloads").resolve()

        # Layout
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        lbl = ttk.Label(self, text="paste a meshy share link, model id, or a direct .glb url:")
        lbl.grid(row=0, column=0, sticky="w", padx=10, pady=(10, 4))

        inp = ttk.Entry(self, textvariable=self.input_var)
        inp.grid(row=1, column=0, sticky="ew", padx=10)
        inp.focus_set()

        # Row for directory selection
        dir_frame = ttk.Frame(self)
        dir_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=(4, 0))
        dir_frame.columnconfigure(0, weight=1)
        self.dir_label = ttk.Label(dir_frame, text=f"save to: {self.download_dir}")
        self.dir_label.grid(row=0, column=0, sticky="w")
        self.btn_choose_dir = ttk.Button(dir_frame, text="choose directory", command=self.on_choose_directory)
        self.btn_choose_dir.grid(row=0, column=1, sticky="e", padx=(8, 0))

        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=3, column=0, sticky="ew", padx=10, pady=8)
        btn_frame.columnconfigure((0, 1, 2, 3), weight=1)

        self.btn_download = ttk.Button(btn_frame, text="download", command=self.on_download)
        self.btn_download.grid(row=0, column=0, sticky="w")

        self.btn_open = ttk.Button(btn_frame, text="open figurinify", command=self.on_open_figurinify)
        self.btn_open.grid(row=0, column=1, sticky="w", padx=8)

        self.btn_quit = ttk.Button(btn_frame, text="quit", command=self.on_quit)
        self.btn_quit.grid(row=0, column=2, sticky="w")

        self.progress = ttk.Progressbar(self, maximum=100, variable=self.progress_var)
        self.progress.grid(row=5, column=0, sticky="ew", padx=10, pady=(8, 0))

        self.status = ttk.Label(self, textvariable=self.status_var)
        self.status.grid(row=6, column=0, sticky="w", padx=10, pady=(4, 8))

        # Log text (read-only)
        self.log_text = tk.Text(self, height=10, wrap="word")
        self.log_text.grid(row=4, column=0, sticky="nsew", padx=10, pady=4)
        self.log_text.configure(state="disabled")

        # Menu (optional minimal)
        self.create_menus()

        # Make sure the window becomes visible and focused (helps on macOS)
        self.after(150, self._force_focus)

    # UI helpers
    def log(self, msg: str) -> None:
        def append():
            self.log_text.configure(state="normal")
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.after(0, append)

    def set_status(self, msg: str) -> None:
        self.after(0, lambda: self.status_var.set(msg))

    def set_progress(self, pct: int) -> None:
        self.after(0, lambda: self.progress_var.set(pct))

    # Actions
    def on_quit(self) -> None:
        self.destroy()

    def on_open_figurinify(self) -> None:
        webbrowser.open(FIGURINIFY_URL)
        self.set_status("opened figurinify – load the .glb you downloaded.")

    def on_choose_directory(self) -> None:
        try:
            initial = str(self.download_dir)
        except Exception:
            initial = str(Path.home())
        chosen = filedialog.askdirectory(initialdir=initial, title="Choose download folder")
        if chosen:
            try:
                self.download_dir = Path(chosen).expanduser().resolve()
            except Exception:
                self.download_dir = Path(chosen)
            # Update label and status
            self.dir_label.configure(text=f"save to: {self.download_dir}")
            self.set_status(f"download folder set to: {self.download_dir}")

    def on_download(self) -> None:
        s = self.input_var.get().strip()
        if not s:
            self.set_status("please paste something first.")
            self.bell()
            return

        # reset ui
        self.set_progress(0)
        self.set_status("resolving link…")
        self.log("starting…")
        # disable while working to avoid duplicate tasks
        try:
            self.btn_download.configure(state=tk.DISABLED)
        except Exception:
            pass

        def worker():
            try:
                # resolve
                resolved = resolve_to_glb_url(s, self.log)
                self.log(f"found glb url – {resolved.glb_url}")

                # Open Figurinify with modelUrl pointing to the resolved GLB (so the site can offer a one-click load)
                try:
                    from urllib.parse import quote
                    model_url_param = quote(resolved.glb_url, safe=':/?&=~.-_%')
                    fig_url = f"{FIGURINIFY_URL}?modelUrl={model_url_param}"
                    self.log(f"opening figurinify with detected model url…")
                    webbrowser.open(fig_url)
                except Exception as _e:
                    self.log(f"could not open figurinify with modelUrl: {_e}")

                # Use selected/default download directory
                downloads_dir = self.download_dir
                out_path = downloads_dir / resolved.filename
                self.log(f"downloading to – {out_path}")

                def progress_cb(pct: int) -> None:
                    self.set_progress(pct)

                download_file(resolved.glb_url, out_path, progress_cb, self.log)

                self.set_status(f"download complete – {out_path.name}")
                self.log("next step – opening figurinify…")
                webbrowser.open(FIGURINIFY_URL)
                self.log("in figurinify – click load and select the .glb from the downloads folder.")
                self.set_status("figurinify opened – please load the .glb you just downloaded.")
            except Exception as e:
                self.set_status("failed – see log.")
                self.log(f"error – {e}")
            finally:
                # re-enable button
                try:
                    self.after(0, lambda: self.btn_download.configure(state=tk.NORMAL))
                except Exception:
                    pass

        t = threading.Thread(target=worker, daemon=True)
        t.start()


    def create_menus(self) -> None:
        try:
            menubar = tk.Menu(self)
            self.config(menu=menubar)
            file_menu = tk.Menu(menubar, tearoff=False)
            file_menu.add_command(label="Open Figurinify", command=self.on_open_figurinify)
            file_menu.add_separator()
            file_menu.add_command(label="Quit", command=self.on_quit)
            menubar.add_cascade(label="File", menu=file_menu)
        except Exception:
            # Menus can be skipped on some minimalist environments
            pass

    def _force_focus(self) -> None:
        try:
            self.update_idletasks()
            self.deiconify()
            self.lift()
            # Toggle topmost to bring to front then revert
            self.attributes('-topmost', True)
            self.attributes('-topmost', False)
            self.focus_force()
        except Exception:
            pass


if __name__ == "__main__":
    import traceback
    try:
        app = TkDownloaderApp()
        app.mainloop()
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        # If Tk fails to initialize, try to show a message box; always print traceback for terminal runs
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        try:
            # attempt to show a GUI error dialog
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Startup Error", f"The app failed to start.\n\n{e}")
            root.destroy()
        except Exception:
            pass
        sys.exit(1)
