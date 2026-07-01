#!/usr/bin/env python3
"""cassini GUI - a simple cross-platform front end for the Saturn save relay.

Pure standard library (Tkinter). Open a Saturn backup dump, inspect the
saves inside it, extract them, convert formats, or deploy the memory to a
MiSTer over SSH by picking a game from a searchable list of your library.

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
            ttk.Label(row, text="MiSTer SSH host:").pack(side="left")
            self.host = tk.StringVar(value="root@mister.local")
            ttk.Entry(row, textvariable=self.host, width=22).pack(side="left", padx=6)
            ttk.Button(row, text="Load games ↺", command=self.load_games).pack(side="left")
            self.rdir = tk.StringVar(value="/media/fat/saves/Saturn")

            box = ttk.LabelFrame(f, text="Pick the game to deploy this save to", padding=8)
            box.pack(fill="both", expand=True, pady=(10, 0))
            sr = ttk.Frame(box); sr.pack(fill="x")
            ttk.Label(sr, text="Search:").pack(side="left")
            self.search = tk.StringVar()
            se = ttk.Entry(sr, textvariable=self.search)
            se.pack(side="left", fill="x", expand=True, padx=6)
            se.bind("<KeyRelease>", lambda e: self._filter_games())

            lb = ttk.Frame(box); lb.pack(fill="both", expand=True, pady=6)
            self.games_lb = tk.Listbox(lb, height=9, activestyle="dotbox")
            self.games_lb.pack(side="left", fill="both", expand=True)
            sb = ttk.Scrollbar(lb, command=self.games_lb.yview); sb.pack(side="left", fill="y")
            self.games_lb.config(yscrollcommand=sb.set)
            self.games_lb.bind("<Double-Button-1>", lambda e: self.deploy_single())

            br = ttk.Frame(box); br.pack(fill="x")
            ttk.Button(br, text="Deploy to selected game",
                       command=self.deploy_single).pack(side="left")
            ttk.Label(br, text="  no list? just type the exact ROM name above and Deploy "
                      "uses that.", foreground="#888").pack(side="left")
            ttk.Label(box, text="Writes the whole memory to that one .sav (the game finds "
                      "its own save). After deploying: on the MiSTer exit the core to the "
                      "main menu, then mount the game FRESH.", foreground="#888",
                      wraplength=760, justify="left").pack(anchor="w", pady=(4, 0))
            self.all_games = []

        def load_games(self):
            self._say("loading Saturn games from %s ..." % self.host.get())

            def work():
                try:
                    self.all_games = eng.list_mister_games(self.host.get(), self.rdir.get())
                    self._say("loaded %d games from the MiSTer" % len(self.all_games))
                    self.after(0, self._filter_games)
                except Exception as e:
                    self._say("ERROR loading games: %s" % e)
            threading.Thread(target=work, daemon=True).start()

        def _filter_games(self):
            q = self.search.get().strip().lower()
            self.games_lb.delete(0, "end")
            for g in self.all_games:
                if q in g.lower():
                    self.games_lb.insert("end", g)

        def _target_game(self):
            sel = self.games_lb.curselection()
            if sel:
                return self.games_lb.get(sel[0])
            return self.search.get().strip().removesuffix(".sav")

        def deploy_single(self):
            if not self._need_dump():
                return
            game = self._target_game()
            if not game:
                messagebox.showinfo("cassini",
                                    "Select a game, or type its ROM name in Search.")
                return
            if not messagebox.askyesno(
                    "cassini", "Deploy this dump to:\n   %s.sav\non %s?\n\n"
                    "The existing file is backed up first." % (game, self.host.get())):
                return
            ids = [s["name"] for s in eng.parse_saves(self.packed)]
            threading.Thread(target=self._single_worker, args=(game, ids),
                             daemon=True).start()

        def _single_worker(self, game, ids):
            self._say("deploying dump -> %s.sav on %s ..." % (game, self.host.get()))
            try:
                eng.deploy_mister(self.packed, ids, self.host.get(), game,
                                  self.rdir.get(), 0xFF, "gui", False, True)
                self._say("done: %s.sav written and md5-verified" % game)
                self._say("REMEMBER on the MiSTer: exit the core to the main menu, then "
                          "mount the game FRESH so it reloads the save.")
            except Exception as e:
                self._say("ERROR: %s" % e)

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

    root = tk.Tk()
    try:
        # macOS renders correctly only with its native 'aqua' theme; forcing
        # 'clam' there paints widgets invisible. 'clam' does look better than
        # the dated default on Linux/Windows, so use it only off-macOS.
        if sys.platform != "darwin":
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
