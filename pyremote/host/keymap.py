"""İzleyiciden gelen tuş olaylarını Windows sanal tuş kodlarına (VK) çevirme."""

VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
VK_LWIN, VK_RWIN = 0x5B, 0x5C
CTRL_VKS = {0x11, 0xA2, 0xA3}
ALT_VKS = {0x12, 0xA4, 0xA5}
WIN_VKS = {0x5B, 0x5C}

# Tk tuş adları (keysym) -> VK
KEYSYM_TO_VK = {
    "BackSpace": 0x08, "Tab": 0x09, "ISO_Left_Tab": 0x09, "Return": 0x0D, "KP_Enter": 0x0D,
    "Pause": 0x13, "Caps_Lock": 0x14, "Escape": 0x1B, "space": 0x20,
    "Prior": 0x21, "Next": 0x22, "End": 0x23, "Home": 0x24,
    "Left": 0x25, "Up": 0x26, "Right": 0x27, "Down": 0x28,
    "Print": 0x2C, "Insert": 0x2D, "Delete": 0x2E,
    "Shift_L": 0xA0, "Shift_R": 0xA1, "Control_L": 0xA2, "Control_R": 0xA3,
    "Alt_L": 0xA4, "Alt_R": 0xA5, "Meta_L": 0xA4, "Meta_R": 0xA5, "ISO_Level3_Shift": 0xA5,
    "Super_L": 0x5B, "Super_R": 0x5C, "Win_L": 0x5B, "Win_R": 0x5C,
    "Menu": 0x5D, "App": 0x5D, "Num_Lock": 0x90, "Scroll_Lock": 0x91,
    "KP_Multiply": 0x6A, "KP_Add": 0x6B, "KP_Subtract": 0x6D, "KP_Decimal": 0x6E,
    "KP_Divide": 0x6F,
    "KP_Home": 0x24, "KP_End": 0x23, "KP_Prior": 0x21, "KP_Next": 0x22,
    "KP_Left": 0x25, "KP_Up": 0x26, "KP_Right": 0x27, "KP_Down": 0x28,
    "KP_Insert": 0x2D, "KP_Delete": 0x2E,
}
KEYSYM_TO_VK.update({f"F{i}": 0x6F + i for i in range(1, 25)})
KEYSYM_TO_VK.update({f"KP_{i}": 0x60 + i for i in range(10)})

# Noktalama tuşlarının Tk adları -> karakter (VK, ana makinenin düzeninden bulunur)
KEYSYM_TO_CHAR = {
    "minus": "-", "plus": "+", "equal": "=", "comma": ",", "period": ".", "slash": "/",
    "backslash": "\\", "semicolon": ";", "apostrophe": "'", "quoteright": "'", "grave": "`",
    "quoteleft": "`", "bracketleft": "[", "bracketright": "]", "asterisk": "*",
    "less": "<", "greater": ">", "quotedbl": '"', "exclam": "!", "at": "@",
    "numbersign": "#", "dollar": "$", "percent": "%", "ampersand": "&", "parenleft": "(",
    "parenright": ")", "underscore": "_", "colon": ":", "question": "?", "bar": "|",
    "braceleft": "{", "braceright": "}", "asciitilde": "~", "asciicircum": "^",
}


def is_text_char(ch) -> bool:
    return isinstance(ch, str) and len(ch) == 1 and ch.isprintable() and ch != " "


def decide(ev: dict, held_vks: set, char_to_vk) -> tuple[str, object] | None:
    """Bir tuşa basma olayının nasıl üretileceğine karar verir.

    Dönen değer ("unicode", karakter) ya da ("vk", kod) olur; tuş tanınmıyorsa None.
    Düz metin, iki taraftaki klavye düzenleri farklı olsa da doğru çıksın diye
    Unicode olarak yazılır. Kısayollar (Ctrl+C, Alt+F4, Win+R...) VK ile üretilir.
    """
    ch, sym, vk = ev.get("ch"), ev.get("sym") or "", ev.get("vk")
    ctrl, alt, win = (bool(held_vks & s) for s in (CTRL_VKS, ALT_VKS, WIN_VKS))
    # Ctrl+Alt birlikte basılıyken yazılabilir karakter geliyorsa bu AltGr'dir (@, {, € ...).
    if is_text_char(ch) and not win and ctrl == alt:
        return ("unicode", ch)
    if isinstance(vk, int) and 0 < vk < 256:
        return ("vk", vk)
    if sym in KEYSYM_TO_VK:
        return ("vk", KEYSYM_TO_VK[sym])
    c = sym if len(sym) == 1 else KEYSYM_TO_CHAR.get(sym)
    if c is None and is_text_char(ch):
        c = ch
    if c:
        code = char_to_vk(c)
        if code:
            return ("vk", code)
        if is_text_char(c):
            return ("unicode", c)
    return None
