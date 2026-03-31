import struct
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


MAGIC = b"TLWVF"
XOR_KEY = bytes([0x13, 0x02, 0x20, 0x05, 0x54, 0x4C, 0x57, 0x00])

WINDOW_TITLE = "World Of Soccer TLWV/DAT Extractor"

COLOR_BG = "#323232"
COLOR_PANEL = "#3A3A3A"
COLOR_DROP = "#363636"
COLOR_BORDER = "#4F4F4F"
COLOR_TEXT = "#F0F0F0"
COLOR_TEXT_SECONDARY = "#C9C9C9"
COLOR_CONTROL = "#424242"
COLOR_CONTROL_BORDER = "#525252"

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except Exception:
    DND_FILES = None
    TkinterDnD = None
    DND_AVAILABLE = False


def parse_drop_files(data: str) -> list[str]:
    if not data:
        return []
    data = data.strip()
    if data.startswith("{") and data.endswith("}"):
        data = data[1:-1]
    if "}" in data or "{" in data:
        parts = []
        current = ""
        in_brace = False
        for ch in data:
            if ch == "{":
                in_brace = True
                current = ""
            elif ch == "}":
                in_brace = False
                if current:
                    parts.append(current)
            elif in_brace:
                current += ch
        return parts
    return data.split()


@dataclass
class ArchiveEntry:
    name: str
    size: int
    offset: int


class XorFileReader:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle = path.open("rb")
        self.pos = 0

    def close(self) -> None:
        self.handle.close()

    def read(self, size: int) -> bytes:
        data = self.handle.read(size)
        if len(data) != size:
            raise ValueError("Unexpected end of file while reading archive header.")
        decoded = xor_chunk(data, self.pos)
        self.pos += size
        return decoded


def xor_chunk(data: bytes, offset: int) -> bytes:
    key = XOR_KEY
    klen = len(key)
    out = bytearray(data)
    for idx in range(len(out)):
        out[idx] ^= key[(offset + idx) % klen]
    return bytes(out)


def safe_join(root: Path, name: str) -> Path:
    clean = name.replace("/", "\\")
    win_path = PureWindowsPath(clean)
    parts = [
        p
        for p in win_path.parts
        if p not in ("", ".", "..", win_path.drive, win_path.root)
    ]
    return root.joinpath(*parts)


def parse_archive_index(path: Path) -> tuple[list[ArchiveEntry], int]:
    reader = XorFileReader(path)
    try:
        magic = reader.read(len(MAGIC))
        if magic != MAGIC:
            raise ValueError("Not a TLWVF archive (magic mismatch).")
        count = struct.unpack("<I", reader.read(4))[0]
        entries: list[ArchiveEntry] = []
        for _ in range(count):
            name_len = struct.unpack("<I", reader.read(4))[0]
            if name_len <= 0 or name_len > 4096:
                raise ValueError(f"Invalid name length: {name_len}")
            name = reader.read(name_len).decode("latin-1", errors="replace")
            size = struct.unpack("<I", reader.read(4))[0]
            entries.append(ArchiveEntry(name=name, size=size, offset=0))
        data_offset = reader.pos
        current = data_offset
        for entry in entries:
            entry.offset = current
            current += entry.size
        return entries, data_offset
    finally:
        reader.close()


class DatExtractorGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(WINDOW_TITLE)
        self.root.geometry("520x320")
        self.root.minsize(420, 260)
        self.root.resizable(False, False)
        self.root.configure(bg=COLOR_BG)

        self.busy = False
        self.dnd_available = DND_AVAILABLE

        self._setup_styles()
        self._build_ui()

    def _setup_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")

    def _build_ui(self) -> None:
        self.container = tk.Frame(self.root, bg=COLOR_BG)
        self.container.pack(fill="both", expand=True, padx=16, pady=12)

        self.drop_area = tk.Frame(
            self.container,
            bg=COLOR_DROP,
            highlightbackground=COLOR_BORDER,
            highlightthickness=2,
            bd=0,
        )
        self.drop_area.pack(fill="both", expand=True, pady=(0, 12))

        drop_center = tk.Frame(self.drop_area, bg=COLOR_DROP)
        drop_center.pack(pady=16)

        drop_title = tk.Label(
            drop_center,
            text="Drop WorldOfSoccer.dat here",
            font=("Segoe UI", 11, "bold"),
            fg=COLOR_TEXT,
            bg=COLOR_DROP,
        )
        drop_title.pack()
        drop_subtitle = tk.Label(
            drop_center,
            text="or click to browse.",
            font=("Segoe UI", 9),
            fg=COLOR_TEXT_SECONDARY,
            bg=COLOR_DROP,
            pady=6,
        )
        drop_subtitle.pack()

        for widget in (self.drop_area, drop_center, drop_title, drop_subtitle):
            widget.bind("<Button-1>", lambda _event: self.browse_dat())
            widget.configure(cursor="hand2")

        self.log_panel = tk.Frame(
            self.container,
            bg=COLOR_PANEL,
            highlightbackground=COLOR_BORDER,
            highlightthickness=1,
            bd=0,
        )

        log_body = tk.Frame(self.log_panel, bg=COLOR_PANEL)
        log_body.pack(fill="both", expand=True, padx=12, pady=12)

        self.log_text = tk.Text(
            log_body,
            height=18,
            state="disabled",
            wrap="none",
            bg=COLOR_CONTROL,
            fg=COLOR_TEXT,
            insertbackground=COLOR_TEXT,
            relief="flat",
            highlightbackground=COLOR_CONTROL_BORDER,
            highlightthickness=1,
        )
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(log_body, orient="vertical", command=self.log_text.yview)
        scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scroll.set)

        self.log_visible = False

        if self.dnd_available:
            self._register_drop_targets()

    def _register_drop_targets(self) -> None:
        def register(widget: tk.Widget) -> None:
            try:
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_drop)
            except Exception:
                return
            for child in widget.winfo_children():
                register(child)

        register(self.drop_area)

    def _show_log(self) -> None:
        if not self.log_visible:
            self.drop_area.pack_forget()
            self.drop_area.pack(fill="x", pady=(0, 12))
            self.log_panel.pack(fill="both", expand=True, pady=(0, 0))
            self.log_visible = True

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _default_output_dir(self, dat_path: Path) -> Path:
        return dat_path.parent / "dat_extract"

    def browse_dat(self) -> None:
        path = filedialog.askopenfilename(
            title="Select WorldOfSoccer.dat",
            filetypes=[("DAT files", "*.dat"), ("All files", "*.*")],
        )
        if path:
            self.start_extraction(Path(path))

    def _on_drop(self, event: object) -> None:
        raw_data = getattr(event, "data", "")
        if not raw_data:
            return
        try:
            files = list(self.root.tk.splitlist(raw_data))
        except Exception:
            files = parse_drop_files(raw_data)
        if not files:
            return
        path = Path(files[0])
        if not path.is_file():
            messagebox.showerror("Error", "Drop a valid WorldOfSoccer.dat file.")
            return
        self.start_extraction(path)

    def log(self, msg: str) -> None:
        def _append() -> None:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

        self.root.after(0, _append)

    def set_busy(self, busy: bool) -> None:
        self.root.after(0, lambda: setattr(self, "busy", busy))

    def start_extraction(self, dat_path: Path) -> None:
        if self.busy:
            messagebox.showinfo("Busy", "Extraction is already running.")
            return
        if not dat_path.is_file():
            messagebox.showerror("Error", "Select a valid WorldOfSoccer.dat file.")
            return

        output_dir = self._default_output_dir(dat_path)
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Error", f"Failed to create output folder:\n{exc}")
            return

        self.busy = True
        self._show_log()
        self._clear_log()
        self.log(f"Input: {dat_path}")
        self.log(f"Output: {output_dir}")

        def task() -> None:
            try:
                entries, data_offset = parse_archive_index(dat_path)
                total = len(entries)
                self.log(f"Archive entries: {total} (data starts at {data_offset})")

                with dat_path.open("rb") as handle:
                    for idx, entry in enumerate(entries, start=1):
                        self.log(f"[{idx}/{total}] {entry.name}")
                        out_path = safe_join(output_dir, entry.name)
                        out_path.parent.mkdir(parents=True, exist_ok=True)
                        handle.seek(entry.offset)
                        remaining = entry.size
                        read_pos = entry.offset
                        with out_path.open("wb") as out_f:
                            while remaining > 0:
                                chunk = handle.read(min(1024 * 1024, remaining))
                                if not chunk:
                                    raise ValueError("Unexpected end of file while extracting data.")
                                decoded = xor_chunk(chunk, read_pos)
                                out_f.write(decoded)
                                read_pos += len(chunk)
                                remaining -= len(chunk)

                self.log(f"Done. Extracted: {total}")
            except Exception:
                self.log(traceback.format_exc())
                self.root.after(0, lambda: messagebox.showerror("Error", "Extraction failed."))
            finally:
                self.set_busy(False)

        threading.Thread(target=task, daemon=True).start()


def main() -> None:
    root = TkinterDnD.Tk() if DND_AVAILABLE else tk.Tk()
    app = DatExtractorGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
