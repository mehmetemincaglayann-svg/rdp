"""İzleyici penceresi: uzak ekranı gösterir, fare/klavyeyi karşıya iletir."""
import asyncio
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from ..config import Config, format_id, parse_target
from ..protocol import MAX_CLIPBOARD
from .client import ConnectError, ViewerClient

QUALITY = {  # ad -> (JPEG kalitesi, ölçek)
    "Düşük (yavaş bağlantı)": (35, 0.5),
    "Orta": (50, 0.75),
    "Yüksek": (60, 1.0),
    "En yüksek": (85, 1.0),
}
BUTTONS = {1: "left", 2: "right", 3: "middle"} if sys.platform == "darwin" else \
          {1: "left", 2: "middle", 3: "right"}
SPECIAL_KEYS = {
    "Windows tuşu": ["Super_L"],
    "Alt+Tab": ["Alt_L", "Tab"],
    "Alt+F4": ["Alt_L", "F4"],
    "Ctrl+Shift+Esc (Görev Yöneticisi)": ["Control_L", "Shift_L", "Escape"],
    "Win+D (Masaüstü)": ["Super_L", "d"],
    "Win+R (Çalıştır)": ["Super_L", "r"],
    "Win+L (Kilitle)": ["Super_L", "l"],
}


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


class Net:
    """Ağ işlemlerini ayrı bir iş parçacığında yürütür; olayları kuyruğa koyar."""

    def __init__(self, config, host_id, password, events: queue.Queue, address=None):
        self.events = events
        self.loop = asyncio.new_event_loop()
        self.client = ViewerClient(config, host_id, password,
                                   lambda ev, **kw: events.put((ev, kw)), address)
        threading.Thread(target=self._main, daemon=True).start()

    def _main(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self.client.connect())
        except ConnectError as e:
            return self.events.put(("connect_error", {"error": str(e)}))
        except Exception as e:
            return self.events.put(("connect_error", {"error": repr(e)}))
        self.events.put(("connected", {}))
        try:
            self.loop.run_until_complete(self.client.run())
        finally:
            self.events.put(("closed", {}))

    def call(self, coro):
        if self.loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self.loop)
        else:
            coro.close()

    def send(self, obj):
        self.call(self.client.send(obj))

    def close(self):
        self.call(self.client.close())


class RemoteBrowser(tk.Toplevel):
    """Uzak bilgisayardaki dosyalara göz atma ve indirme penceresi."""

    def __init__(self, app: "ViewerApp"):
        super().__init__(app.root)
        self.app = app
        self.title("Uzak bilgisayardan dosya al")
        self.geometry("560x420")
        self.result = None
        top = ttk.Frame(self, padding=6)
        top.pack(fill="x")
        ttk.Button(top, text="↑ Yukarı", command=self._up).pack(side="left")
        self.path_var = tk.StringVar(master=self)
        entry = ttk.Entry(top, textvariable=self.path_var)
        entry.pack(side="left", fill="x", expand=True, padx=6)
        entry.bind("<Return>", lambda e: self.go(self.path_var.get()))
        self.tree = ttk.Treeview(self, columns=("size",), selectmode="browse")
        self.tree.heading("#0", text="Ad", anchor="w")
        self.tree.heading("size", text="Boyut", anchor="e")
        self.tree.column("size", width=100, anchor="e", stretch=False)
        self.tree.pack(fill="both", expand=True, padx=6)
        self.tree.bind("<Double-1>", lambda e: self._open())
        self.tree.bind("<Return>", lambda e: self._open())
        ttk.Button(self, text="Seçili dosyayı indir", command=self._open).pack(pady=6)
        self.go("")

    def go(self, path):
        self.app.net.send({"t": "ls", "path": path})

    def show(self, result):
        if result.get("error"):
            return messagebox.showerror("PyRemote", f"Klasör açılamadı: {result['error']}",
                                        parent=self)
        self.result = result
        self.path_var.set("" if result["path"].startswith("::") else result["path"])
        self.tree.delete(*self.tree.get_children())
        for i, e in enumerate(result["entries"]):
            self.tree.insert("", "end", iid=str(i),
                             text=("📁 " if e["dir"] else "📄 ") + e["name"],
                             values=("" if e["dir"] else human_size(e["size"]),))

    def _up(self):
        if self.result and self.result.get("parent"):
            self.go(self.result["parent"])

    def _open(self):
        sel = self.tree.selection()
        if not sel or not self.result:
            return
        entry = self.result["entries"][int(sel[0])]
        if entry["dir"]:
            return self.go(entry["path"])
        local = filedialog.asksaveasfilename(parent=self, initialfile=entry["name"],
                                             title="Farklı kaydet")
        if local:
            self.app.net.call(self.app.net.client.download(entry["path"], local))


class ViewerApp:
    def __init__(self, config: Config):
        self.config = config
        self.root = tk.Tk()
        self.root.title("PyRemote")
        self.events = queue.Queue()
        self.net = None
        self.connected = False
        self.fb = None            # uzak ekranın yerel kopyası
        self.dirty = False
        self.photo = None
        self.view = (0, 0, 1, 1)  # resmin tuval içindeki konumu: x, y, genişlik, yükseklik
        self.pending_move = None
        self.last_clip = None
        self.browser = None
        self.info = {}
        self._build_connect()
        self.root.protocol("WM_DELETE_WINDOW", self._quit)
        self.root.after(15, self._tick)
        self.root.after(700, self._clipboard_tick)

    # --- bağlantı ekranı -----------------------------------------------
    def _build_connect(self):
        self.root.config(menu="")
        self.connect_frame = f = ttk.Frame(self.root, padding=24)
        f.pack(expand=True)
        ttk.Label(f, text="Uzak bilgisayara bağlan", font=("Segoe UI", 14, "bold")
                  ).grid(row=0, column=0, columnspan=2, pady=(0, 14))
        self.id_var = tk.StringVar(master=self.root, value=self.config.data.get("last_target", ""))
        self.pw_var = tk.StringVar(master=self.root)
        for row, (label, var, show) in enumerate((("ID ya da IP adresi", self.id_var, ""),
                                                  ("Parola", self.pw_var, "•")), start=1):
            ttk.Label(f, text=label).grid(row=row, column=0, sticky="w", padx=6, pady=5)
            e = ttk.Entry(f, textvariable=var, show=show, width=30)
            e.grid(row=row, column=1, padx=6, pady=5)
            e.bind("<Return>", lambda ev: self._connect())
            if row == 1:
                e.focus_set()
        self.connect_btn = ttk.Button(f, text="Bağlan", command=self._connect)
        self.connect_btn.grid(row=4, column=0, columnspan=2, pady=(14, 4), sticky="ew")
        self.connect_msg = ttk.Label(f, foreground="#a00", wraplength=340, justify="left")
        self.connect_msg.grid(row=5, column=0, columnspan=2)

    def _connect(self):
        target = parse_target(self.id_var.get())
        if target is None or not self.pw_var.get():
            return self.connect_msg.config(
                text="9 haneli ID'yi ya da IP adresini (ör. 192.168.1.20) ve parolayı girin.",
                foreground="#a00")
        kind, value = target
        self.config.data["last_target"] = format_id(value) if kind == "id" else self.id_var.get().strip()
        self.config.save()
        self.connect_btn.state(["disabled"])
        self.connect_msg.config(text="Bağlanılıyor…" if kind == "direct" else
                                "Bağlanılıyor… (bu 10-20 saniye sürebilir)", foreground="#444")
        self.net = Net(self.config, value if kind == "id" else "", self.pw_var.get(), self.events,
                       value if kind == "direct" else None)

    # --- oturum ekranı -------------------------------------------------
    def _build_session(self):
        self.connect_frame.destroy()
        self.connected = True
        self.fb = None
        self.canvas = c = tk.Canvas(self.root, bg="#202020", highlightthickness=0,
                                    takefocus=True)
        self.status = ttk.Label(self.root, anchor="w", padding=(6, 2))
        self.status.pack(side="bottom", fill="x")
        c.pack(fill="both", expand=True)
        self.img_id = c.create_image(0, 0, anchor="nw")
        c.bind("<Configure>", lambda e: setattr(self, "dirty", True))
        c.bind("<Motion>", self._on_motion)
        for n in (1, 2, 3):
            c.bind(f"<ButtonPress-{n}>", lambda e, n=n: self._on_button(e, n, 1))
            c.bind(f"<ButtonRelease-{n}>", lambda e, n=n: self._on_button(e, n, 0))
        c.bind("<MouseWheel>", self._on_wheel)
        c.bind("<Button-4>", lambda e: self.net.send({"t": "wheel", "dy": 1}))
        c.bind("<Button-5>", lambda e: self.net.send({"t": "wheel", "dy": -1}))
        c.bind("<KeyPress>", lambda e: self._on_key(e, 1))
        c.bind("<KeyRelease>", lambda e: self._on_key(e, 0))
        c.focus_set()
        try:
            self.last_clip = self.root.clipboard_get()
        except tk.TclError:
            self.last_clip = None

    def _build_menu(self):
        menu = tk.Menu(self.root)
        m = tk.Menu(menu, tearoff=0)
        m.add_command(label="Ekranı yenile", command=lambda: self.net.send({"t": "refresh"}))
        m.add_separator()
        m.add_command(label="Bağlantıyı kes", command=self.net.close)
        menu.add_cascade(label="Bağlantı", menu=m)

        m = tk.Menu(menu, tearoff=0)
        self.mon_var = tk.IntVar(master=self.root, value=self.info.get("monitor", 0))
        for mon in self.info.get("monitors", []):
            m.add_radiobutton(
                label=f"Monitör {mon['i'] + 1}  ({mon['w']}×{mon['h']})"
                      + ("  – birincil" if mon.get("primary") else ""),
                variable=self.mon_var, value=mon["i"],
                command=lambda: self.net.send({"t": "monitor", "i": self.mon_var.get()}))
        menu.add_cascade(label="Monitör", menu=m)

        m = tk.Menu(menu, tearoff=0)
        self.q_var = tk.StringVar(master=self.root, value="Yüksek")
        for name in QUALITY:
            m.add_radiobutton(label=name, variable=self.q_var, value=name,
                              command=self._set_quality)
        menu.add_cascade(label="Görüntü", menu=m)

        m = tk.Menu(menu, tearoff=0)
        m.add_command(label="Dosya gönder…", command=self._send_file)
        m.add_command(label="Dosya al…", command=self._open_browser)
        menu.add_cascade(label="Dosya", menu=m)

        m = tk.Menu(menu, tearoff=0)
        for name, keys in SPECIAL_KEYS.items():
            m.add_command(label=name, command=lambda k=keys: self._send_combo(k))
        menu.add_cascade(label="Tuş gönder", menu=m)
        self.root.config(menu=menu)

    def _set_quality(self):
        q, scale = QUALITY[self.q_var.get()]
        self.net.send({"t": "quality", "q": q, "scale": scale})

    def _send_combo(self, keys):
        for k in keys:
            self.net.send({"t": "key", "d": 1, "sym": k, "ch": "", "code": None})
        for k in reversed(keys):
            self.net.send({"t": "key", "d": 0, "sym": k, "ch": "", "code": None})
        self.canvas.focus_set()

    def _send_file(self):
        path = filedialog.askopenfilename(parent=self.root, title="Gönderilecek dosya")
        if path:
            self.net.call(self.net.client.upload(path))

    def _open_browser(self):
        if self.browser is None or not self.browser.winfo_exists():
            self.browser = RemoteBrowser(self)
        self.browser.lift()

    # --- girdi ---------------------------------------------------------
    def _norm(self, e):
        ox, oy, w, h = self.view
        return (min(max((e.x - ox) / max(w - 1, 1), 0.0), 1.0),
                min(max((e.y - oy) / max(h - 1, 1), 0.0), 1.0))

    def _on_motion(self, e):
        self.pending_move = self._norm(e)   # _tick içinde seyreltilerek gönderilir

    def _on_button(self, e, n, down):
        self.canvas.focus_set()
        x, y = self._norm(e)
        self.pending_move = None
        self.net.send({"t": "button", "b": BUTTONS[n], "d": down, "x": x, "y": y})

    def _on_wheel(self, e):
        dy = e.delta if sys.platform == "darwin" else e.delta / 120
        self.net.send({"t": "wheel", "dy": dy})

    def _on_key(self, e, down):
        self.net.send({"t": "key", "d": down, "sym": e.keysym, "ch": e.char,
                       "code": e.keycode,
                       "vk": e.keycode if sys.platform == "win32" else None})
        return "break"   # Tab, Alt, F10 gibi tuşları yerel pencere yorumlamasın

    # --- olay döngüsü --------------------------------------------------
    def _tick(self):
        try:
            for _ in range(200):
                ev, kw = self.events.get_nowait()
                getattr(self, "_ev_" + ev, lambda **k: None)(**kw)
        except queue.Empty:
            pass
        finally:
            self.root.after(15, self._tick)
        if self.connected:
            if self.pending_move:
                x, y = self.pending_move
                self.pending_move = None
                self.net.send({"t": "move", "x": x, "y": y})
            if self.dirty and self.fb is not None:
                self.dirty = False
                self._render()

    def _render(self):
        cw, ch = max(self.canvas.winfo_width(), 1), max(self.canvas.winfo_height(), 1)
        fw, fh = self.fb.size
        s = min(cw / fw, ch / fh)
        dw, dh = max(int(fw * s), 1), max(int(fh * s), 1)
        img = self.fb if (dw, dh) == (fw, fh) else self.fb.resize((dw, dh), Image.BILINEAR)
        self.photo = ImageTk.PhotoImage(img, master=self.root)
        self.view = ((cw - dw) // 2, (ch - dh) // 2, dw, dh)
        self.canvas.itemconfig(self.img_id, image=self.photo)
        self.canvas.coords(self.img_id, self.view[0], self.view[1])

    def _ev_connect_error(self, error):
        self.connect_btn.state(["!disabled"])
        self.connect_msg.config(text=error, foreground="#a00")

    def _ev_connected(self):
        self._build_session()

    def _ev_info(self, **info):
        self.info = info
        self._build_menu()
        mon = info["monitors"][info["monitor"]]
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        s = min(1.0, sw * 0.85 / mon["w"], sh * 0.8 / mon["h"])
        self.root.geometry(f"{int(mon['w'] * s)}x{int(mon['h'] * s) + 50}")
        self.root.title(f"PyRemote – {info.get('host', '')}")
        note = "" if info.get("control") else "  (yalnızca izleme)"
        self._set_status(f"Bağlandı: {info.get('host', '')}{note}")

    def _ev_frame(self, seq, x, y, image, size):
        if self.fb is None or self.fb.size != size:
            self.fb = Image.new("RGB", size)
        self.fb.paste(image, (x, y))
        self.dirty = True
        self.net.send({"t": "ack"})   # işlendi; ana makine yeni kare gönderebilir

    def _ev_clip(self, text):
        self.last_clip = text
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def _ev_ls_result(self, **result):
        if self.browser is not None and self.browser.winfo_exists():
            self.browser.show(result)

    def _ev_progress(self, id, done, total, filename):
        pct = 100 * done // total if total else 100
        self._set_status(f"{filename}: %{pct}  ({human_size(done)} / {human_size(total)})")

    def _ev_file_done(self, id, path, direction):
        where = "Karşı bilgisayara kaydedildi" if direction == "up" else "İndirildi"
        self._set_status(f"{where}: {path}")

    def _ev_file_error(self, id, error):
        self._set_status(f"Dosya aktarımı başarısız: {error}")
        messagebox.showerror("PyRemote", f"Dosya aktarımı başarısız:\n{error}", parent=self.root)

    def _ev_closed(self):
        if not self.connected:
            return
        self.connected = False
        if self.browser is not None and self.browser.winfo_exists():
            self.browser.destroy()
        self.canvas.destroy()
        self.status.destroy()
        self.root.geometry("")
        self.root.title("PyRemote")
        self._build_connect()
        self.connect_msg.config(text="Bağlantı kesildi.", foreground="#444")

    def _set_status(self, text):
        self.status.config(text=text)

    def _clipboard_tick(self):
        if self.connected:
            try:
                text = self.root.clipboard_get()
            except tk.TclError:
                text = None
            if text and text != self.last_clip and len(text) <= MAX_CLIPBOARD:
                self.last_clip = text
                self.net.send({"t": "clip", "text": text})
        self.root.after(700, self._clipboard_tick)

    def _quit(self):
        if self.net:
            self.net.close()
        self.root.destroy()

    def run(self):
        self.root.mainloop()
