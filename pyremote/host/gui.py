"""Ana makine penceresi: ID ve parolayı gösterir, etkin oturumları bildirir."""
import asyncio
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from ..config import Config, format_id
from .backend import Backend
from .service import HostService

STATUS_TEXT = {
    "connecting": "Sunucuya bağlanılıyor…",
    "online": "Bağlantıya hazır",
    "offline": "Sunucuya ulaşılamıyor, yeniden deneniyor…",
}


class HostApp:
    def __init__(self, config: Config, backend: Backend):
        self.config = config
        self.root = tk.Tk()
        self.root.title("PyRemote – Ana Makine")
        self.root.resizable(False, False)
        self.loop = asyncio.new_event_loop()
        self.service = HostService(config, backend)
        threading.Thread(target=self._run_loop, daemon=True).start()
        self._build()
        self._refresh()

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.service.run())

    def _build(self):
        pad = {"padx": 14, "pady": 4}
        main = ttk.Frame(self.root, padding=12)
        main.grid()
        ttk.Label(main, text="Kimliğiniz (ID)").grid(row=0, column=0, sticky="w", **pad)
        self.id_var = tk.StringVar(master=self.root)
        ttk.Entry(main, textvariable=self.id_var, state="readonly", width=14, justify="center",
                  font=("Segoe UI", 20, "bold")).grid(row=0, column=1, columnspan=2, **pad)
        ttk.Label(main, text="Tek kullanımlık parola").grid(row=1, column=0, sticky="w", **pad)
        self.pw_var = tk.StringVar(master=self.root)
        ttk.Entry(main, textvariable=self.pw_var, state="readonly", width=10, justify="center",
                  font=("Consolas", 16)).grid(row=1, column=1, **pad)
        ttk.Button(main, text="Yenile", command=self.service.new_session_password
                   ).grid(row=1, column=2, **pad)

        self.status = tk.Label(main, anchor="w", padx=8, pady=6)
        self.status.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 4))
        self.kick_btn = ttk.Button(main, text="Bağlantıları kes", command=self._kick)
        self.kick_btn.grid(row=3, column=0, columnspan=3, sticky="ew", **pad)

        ttk.Separator(main).grid(row=4, column=0, columnspan=3, sticky="ew", pady=8)
        self.perm_var = tk.StringVar(master=self.root)
        ttk.Label(main, textvariable=self.perm_var).grid(row=5, column=0, columnspan=2,
                                                         sticky="w", **pad)
        self.perm_btn = ttk.Button(main, command=self._toggle_permanent)
        self.perm_btn.grid(row=5, column=2, **pad)
        self.relay_var = tk.StringVar(master=self.root)
        ttk.Label(main, textvariable=self.relay_var, foreground="#666"
                  ).grid(row=6, column=0, columnspan=2, sticky="w", **pad)
        ttk.Button(main, text="Sunucu adresi…", command=self._change_relay
                   ).grid(row=6, column=2, sticky="ew", **pad)
        if not self.service.backend.can_control:
            ttk.Label(main, text="Bu işletim sisteminde yalnızca ekran izlenebilir "
                                 "(fare/klavye kontrolü Windows'ta çalışır).",
                      foreground="#a00", wraplength=380).grid(row=7, column=0, columnspan=3, **pad)

    def _refresh(self):
        s = self.service
        self.id_var.set(format_id(self.config.host_id))
        self.pw_var.set(s.session_password)
        peers = list(s.sessions.values())
        if peers:
            # Makine kontrol edilirken bunu açıkça göster.
            self.status.config(text="● UZAKTAN KONTROL EDİLİYOR: " + ", ".join(peers),
                               bg="#c62828", fg="white")
            self.kick_btn.state(["!disabled"])
        else:
            ok = s.status == "online"
            self.status.config(text=("● " if ok else "○ ") + STATUS_TEXT[s.status],
                               bg="#e8f5e9" if ok else "#fff3e0", fg="#1b5e20" if ok else "#e65100")
            self.kick_btn.state(["disabled"])
        self.relay_var.set(f"Sunucu: {self.config.relay}")
        has_perm = self.config.permanent_key is not None
        self.perm_var.set("Gözetimsiz erişim: " + ("AÇIK (kalıcı parola var)" if has_perm else "kapalı"))
        self.perm_btn.config(text="Kaldır" if has_perm else "Kalıcı parola ayarla…")
        self.root.after(300, self._refresh)

    def _kick(self):
        asyncio.run_coroutine_threadsafe(self.service.disconnect_all(), self.loop)

    def _change_relay(self):
        url = simpledialog.askstring(
            "Sunucu adresi", "Aktarma sunucusunun adresi\n(ör. wss://ornek.onrender.com):",
            initialvalue=self.config.relay, parent=self.root)
        if not url:
            return
        url = url.strip()
        if "://" not in url:
            url = "wss://" + url
        if not url.startswith(("ws://", "wss://")):
            return messagebox.showerror("PyRemote", "Adres ws:// ya da wss:// ile başlamalı.")
        asyncio.run_coroutine_threadsafe(self.service.set_relay(url), self.loop)

    def _toggle_permanent(self):
        if self.config.permanent_key is not None:
            if messagebox.askyesno("PyRemote", "Kalıcı parola kaldırılsın mı?"):
                self.service.set_permanent_password(None)
            return
        pw = simpledialog.askstring("Kalıcı parola", "Gözetimsiz erişim için parola "
                                    "(en az 8 karakter):", show="•", parent=self.root)
        if not pw:
            return
        if pw != simpledialog.askstring("Kalıcı parola", "Parolayı tekrar girin:", show="•",
                                        parent=self.root):
            return messagebox.showerror("PyRemote", "Parolalar eşleşmiyor.")
        try:
            self.service.set_permanent_password(pw)
        except ValueError as e:
            messagebox.showerror("PyRemote", str(e))

    def run(self):
        self.root.mainloop()
