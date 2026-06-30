#!/usr/bin/env python3
"""cassini GUI - a simple cross-platform front end for the Saturn save relay.

Pure standard library (Tkinter). Open a Saturn backup dump, inspect the
saves inside it, extract them, convert formats, or deploy per-game saves
straight to a MiSTer over SSH using a game->saves map.

Dual-mode, like the intv2convert binary: run with arguments it behaves as
the `cassini` CLI; run with no arguments it opens the GUI. Tkinter is
imported lazily so CLI mode works even on a Python built without Tk.

Packaged for macOS / Linux / Windows with PyInstaller (see .github/workflows).
"""
import os
import queue
import sys
import threading

import cassini as eng


def launch_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    class App(ttk.Frame):
        def __init__(self, master):
            super().__init__(master, padding=8)
            self.master.title("cassini %s  -  Saturn save relay" % eng.__version__)
            self.master.geometry("860x560")
            self.pack(fill="both", expand=True)

            self.packed = None
            self.image_path = tk.StringVar(value="(no dump loaded)")
            self.log_q = queue.Queue()

            self._build()
            self.after(120, self._drain_log)

        # ---- layout ---------------------------------------------------
        def _build(self):
            top = ttk.Frame(self); top.pack(fill="x")
            ttk.Button(top, text="Open dump…", command=self.open_dump).pack(side="left")
            ttk.Label(top, textvariable=self.image_path).pack(side="left", padx=8)

            nb = ttk.Notebook(self); nb.pack(fill="both", expand=True, pady=8)
            self._saves_tab(nb)
            self._deploy_tab(nb)

            self.log = tk.Text(self, height=8, wrap="none", state="disabled",
                               bg="#101418", fg="#c8d0d8", font=("Menlo", 11))
            self.log.pack(fill="both", expand=False)

        def _saves_tab(self, nb):
            f = ttk.Frame(nb, padding=6); nb.add(f, text="Saves")
            cols = ("id", "bytes", "comment")
            self.tree = ttk.Treeview(f, columns=cols, show="headings", height=12)
            for c, w in (("id", 160), ("bytes", 80), ("comment", 240)):
                self.tree.heading(c, text=c.upper()); self.tree.column(c, width=w)
            self.tree.pack(side="left", fill="both", expand=True)
            ttk.Scrollbar(f, command=self.tree.yview).pack(side="left", fill="y")

            b = ttk.Frame(f, padding=(8, 0)); b.pack(side="left", fill="y")
            ttk.Button(b, text="Extract selected…",
                       command=self.extract_selected).pack(fill="x", pady=2)
            ttk.Button(b, text="Extract all…",
                       command=lambda: self.extract_selected(all_=True)).pack(fill="x", pady=2)
            ttk.Separator(b).pack(fill="x", pady=6)
            ttk.Button(b, text="Save as MiSTer .sav…",
                       command=lambda: self.convert("mister")).pack(fill="x", pady=2)
            ttk.Button(b, text="Save as packed .BUP…",
                       command=lambda: self.convert("packed")).pack(fill="x", pady=2)

        def _deploy_tab(self, nb):
            f = ttk.Frame(nb, padding=6); nb.add(f, text="Deploy to MiSTer")
            row = ttk.Frame(f); row.pack(fill="x")
            ttk.Label(row, text="SSH host:").pack(side="left")
            self.host = tk.StringVar(value="root@mister.local")
            ttk.Entry(row, textvariable=self.host, width=26).pack(side="left", padx=6)
            ttk.Label(row, text="Remote dir:").pack(side="left")
            self.rdir = tk.StringVar(value="/media/fat/saves/Saturn")
            ttk.Entry(row, textvariable=self.rdir, width=26).pack(side="left", padx=6)

            ttk.Label(f, text="Map  (one per line:  <MiSTer ROM name><TAB><ID1,ID2>):"
                      ).pack(anchor="w", pady=(8, 2))
            self.map_text = tk.Text(f, height=10, wrap="none", font=("Menlo", 11))
            self.map_text.pack(fill="both", expand=True)

            bar = ttk.Frame(f); bar.pack(fill="x", pady=6)
            ttk.Button(bar, text="Load map…", command=self.load_map).pack(side="left")
            ttk.Button(bar, text="Dry run",
                       command=lambda: self.deploy(True)).pack(side="left", padx=6)
            ttk.Button(bar, text="Deploy", command=lambda: self.deploy(False)).pack(side="left")

        # ---- helpers --------------------------------------------------
        def _say(self, msg):
            self.log_q.put(msg)

        def _drain_log(self):
            try:
                while True:
                    msg = self.log_q.get_nowait()
                    self.log.configure(state="normal")
                    self.log.insert("end", msg + "\n"); self.log.see("end")
                    self.log.configure(state="disabled")
            except queue.Empty:
                pass
            self.after(120, self._drain_log)

        def _need_dump(self):
            if self.packed is None:
                messagebox.showwarning("cassini", "Open a dump first."); return False
            return True

        # ---- actions --------------------------------------------------
        def open_dump(self):
            path = filedialog.askopenfilename(
                title="Open Saturn backup dump",
                filetypes=[("Saturn backup", "*.BUP *.bup *.sav *.bin"), ("All files", "*.*")])
            if not path:
                return
            try:
                self.packed = eng.load_packed(path)
            except Exception as e:
                messagebox.showerror("cassini", "Could not read image:\n%s" % e); return
            self.image_path.set(path)
            self.tree.delete(*self.tree.get_children())
            for s in eng.parse_saves(self.packed):
                com = "".join(chr(c) if 32 <= c < 127 else "." for c in s["comment"])
                self.tree.insert("", "end", values=(s["name"], s["datasize"], com))
            self._say("loaded %s (%d saves)"
                      % (os.path.basename(path), len(self.tree.get_children())))

        def extract_selected(self, all_=False):
            if not self._need_dump():
                return
            ids = None if all_ else [self.tree.item(i, "values")[0]
                                     for i in self.tree.selection()]
            if ids is not None and not ids:
                messagebox.showinfo("cassini", "Select one or more saves first."); return
            outdir = filedialog.askdirectory(title="Extract to folder")
            if not outdir:
                return
            n = 0
            for s in eng.parse_saves(self.packed):
                if ids is None or s["name"] in ids:
                    open(os.path.join(outdir, s["name"] + ".raw"), "wb").write(s["data"])
                    open(os.path.join(outdir, s["name"] + ".BUP"), "wb").write(eng.make_bup(s))
                    n += 1
            self._say("extracted %d save(s) to %s" % (n, outdir))

        def convert(self, to):
            if not self._need_dump():
                return
            out = filedialog.asksaveasfilename(
                title="Save as", defaultextension=".sav" if to == "mister" else ".BUP")
            if not out:
                return
            data = eng.to_mister(self.packed) if to == "mister" else self.packed
            open(out, "wb").write(data)
            self._say("wrote %s (%d bytes, %s)" % (out, len(data), to))

        def load_map(self):
            path = filedialog.askopenfilename(
                title="Open map (.tsv)",
                filetypes=[("TSV map", "*.tsv *.txt"), ("All", "*.*")])
            if path:
                self.map_text.delete("1.0", "end")
                self.map_text.insert("1.0", open(path, encoding="utf-8").read())

        def deploy(self, dry):
            if not self._need_dump():
                return
            rows = []
            for line in self.map_text.get("1.0", "end").splitlines():
                if not line.strip() or line.lstrip().startswith("#") or "\t" not in line:
                    continue
                game, ids = line.split("\t", 1)
                rows.append((game.strip(), [x.strip() for x in ids.split(",") if x.strip()]))
            if not rows:
                messagebox.showinfo("cassini", "Map is empty (need <name><TAB><IDs>)."); return
            if not dry and not messagebox.askyesno(
                    "cassini", "Deploy %d game saves to %s?\nExisting files are backed up first."
                    % (len(rows), self.host.get())):
                return
            threading.Thread(target=self._deploy_worker, args=(rows, dry),
                             daemon=True).start()

        def _deploy_worker(self, rows, dry):
            host, rdir = self.host.get(), self.rdir.get()
            self._say("%s %d game(s) -> %s" % ("DRY-RUN" if dry else "Deploying",
                                               len(rows), host))
            for game, ids in rows:
                try:
                    chosen = [s for s in eng.parse_saves(self.packed) if s["name"] in ids]
                    if len(chosen) != len(set(ids)):
                        self._say("  SKIP %s (missing saves)" % game); continue
                    if dry:
                        self._say("  would deploy %s <- %s" % (game, ",".join(ids))); continue
                    eng.deploy_mister(self.packed, ids, host, game, rdir, 0xFF, "gui", False)
                    self._say("  deployed %s" % game)
                except Exception as e:
                    self._say("  ERROR %s: %s" % (game, e))
            self._say("done.")

    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


def main():
    if len(sys.argv) > 1:          # arguments -> CLI (no Tk needed)
        eng.main()
        return
    launch_gui()


if __name__ == "__main__":
    main()
