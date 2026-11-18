"""Ana makinenin işletim sistemiyle konuşan katman: ekran yakalama, girdi, pano."""
import sys
import threading

from PIL import Image

from . import keymap


class Backend:
    """Arayüz. Windows gerçeklemesi aşağıda; testler sahte bir alt sınıf kullanır."""

    can_control = False

    def monitors(self) -> list[dict]:
        raise NotImplementedError

    def grab(self, index: int) -> Image.Image:
        raise NotImplementedError

    def mouse_move(self, index: int, nx: float, ny: float): ...
    def mouse_button(self, button: str, down: bool): ...
    def mouse_wheel(self, dx: float, dy: float): ...
    def key(self, ev: dict): ...
    def release_all(self): ...

    def clipboard_get(self) -> str | None:
        return None

    def clipboard_set(self, text: str): ...


class MssBackend(Backend):
    """mss ile ekran yakalama + pyperclip ile pano. Girdi üretmez (yalnızca izleme)."""

    def __init__(self):
        import mss
        self._mss = mss
        self._local = threading.local()

    def _sct(self):
        # mss nesneleri oluşturuldukları iş parçacığına bağlıdır.
        if not hasattr(self._local, "sct"):
            self._local.sct = self._mss.mss()
        return self._local.sct

    def _raw_monitors(self):
        return self._sct().monitors[1:]

    def monitors(self):
        return [{"i": i, "x": m["left"], "y": m["top"], "w": m["width"], "h": m["height"],
                 "primary": m["left"] == 0 and m["top"] == 0}
                for i, m in enumerate(self._raw_monitors())]

    def grab(self, index):
        mons = self._raw_monitors()
        shot = self._sct().grab(mons[index if 0 <= index < len(mons) else 0])
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    def clipboard_get(self):
        try:
            import pyperclip
            return pyperclip.paste() or None
        except Exception:
            return None

    def clipboard_set(self, text):
        try:
            import pyperclip
            pyperclip.copy(text)
        except Exception:
            pass


class WindowsBackend(MssBackend):
    can_control = True

    def __init__(self):
        from . import win_input
        self._in = win_input
        win_input.enable_dpi_awareness()
        super().__init__()
        self._pressed = {}   # izleyicinin tuş kodu -> ("vk"/"unicode", değer)
        self._held_vks = set()
        self._buttons = set()

    def mouse_move(self, index, nx, ny):
        mons = self._raw_monitors()
        m = mons[index if 0 <= index < len(mons) else 0]
        x = m["left"] + round(min(max(nx, 0.0), 1.0) * (m["width"] - 1))
        y = m["top"] + round(min(max(ny, 0.0), 1.0) * (m["height"] - 1))
        self._in.mouse_move(x, y)

    def mouse_button(self, button, down):
        self._in.mouse_button(button, down)
        (self._buttons.add if down else self._buttons.discard)(button)

    def mouse_wheel(self, dx, dy):
        self._in.mouse_wheel(dx, dy)

    def _emit(self, action, down):
        kind, value = action
        if kind == "unicode":
            self._in.key_unicode(value, down)
        else:
            self._in.key_vk(value, down)
            (self._held_vks.add if down else self._held_vks.discard)(value)

    def key(self, ev):
        # Shift durumu değişince tuş adı değişebilir; eşleştirme tuş koduyla yapılır.
        code = ev.get("code") if ev.get("code") is not None else ev.get("sym")
        if ev.get("d"):
            action = keymap.decide(ev, self._held_vks, self._in.char_to_vk)
            if action is None:
                return
            self._pressed[code] = action
            self._emit(action, True)
        else:
            # Bırakma, basarken ne üretildiyse onun karşılığı olmalı.
            action = self._pressed.pop(code, None)
            if action:
                self._emit(action, False)

    def release_all(self):
        """Bağlantı koparsa basılı kalan tuş/düğme olmasın."""
        for action in list(self._pressed.values()):
            self._emit(action, False)
        self._pressed.clear()
        for vk in list(self._held_vks):
            self._in.key_vk(vk, False)
        self._held_vks.clear()
        for b in list(self._buttons):
            self._in.mouse_button(b, False)
        self._buttons.clear()


def default_backend() -> Backend:
    return WindowsBackend() if sys.platform == "win32" else MssBackend()
