"""GetCases in the notification area of the Windows taskbar.

Closing GetCases's window only hides it, and Ctrl+Space opens the spotlight
from any application — but started without a terminal (``pythonw``, or
packaged as an .exe with no console), a GetCases running in the background had
nothing on screen to show it was there, and nothing but the hotkey to reach
it: no way back to the main window, and no way to quit.  This module puts it
at the far right of the taskbar, as the scales the spotlight shows beside its
search field.  A click opens the spotlight; a right-click offers a menu (the
spotlight, the main window, Quit).

The scales are Segoe UI Emoji's outline glyph — the one Tk draws in the
spotlight — in the colour the taskbar's own icons take: dark on a light
taskbar, white on a dark one, redrawn when the reader switches.  Pillow draws
them; without it a stock application icon stands in.

Plain ctypes over the Win32 shell API, so nothing else is needed.  The icon
has a thread of its own, running a hidden window's message loop: the taskbar
talks to the icon through that window.  Clicks and menu choices reach the
callbacks the app gives on that thread, so they must not touch Tk.

Tkinter-free and started only from the app's ``main()``: the tests that build
the app never put an icon in the taskbar.
"""

from __future__ import annotations

import io
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

#: The scales, as the spotlight shows them.
GLYPH = "⚖"
_GLYPH_FONT = "seguiemj.ttf"      # Segoe UI Emoji, in every Windows since 8.1
_INK_ON_LIGHT = "#1c1d21"         # the app's text colour
_INK_ON_DARK = "#ffffff"

_PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"


@dataclass(frozen=True)
class MenuItem:
    """A line of the icon's right-click menu: *label* (after a tab, the
    shortcut, set to the right), and what choosing it does.  The *default*
    line is drawn bold, and should be what a click on the icon does."""
    label: str
    action: Callable[[], None]
    default: bool = False


#: In a menu, a line between groups of items.
SEPARATOR = None


def taskbar_is_light() -> bool:
    """Whether the taskbar is light (Windows 11's default) rather than dark,
    so the icon can be drawn the way the taskbar's own icons are."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PERSONALIZE_KEY) as key:
            return bool(winreg.QueryValueEx(key, "SystemUsesLightTheme")[0])
    except (ImportError, OSError):
        return False    # Windows 10 before its light theme: a dark taskbar


def draw_scales(size: int, light_taskbar: bool):
    """The scales on a transparent square *size* pixels across, as a Pillow
    image, inked for a light or a dark taskbar.  Drawn eight times over and
    scaled down, which keeps the glyph's fine lines smooth."""
    from PIL import Image, ImageColor, ImageDraw, ImageFont

    font = ImageFont.truetype(_GLYPH_FONT, size * 8)
    left, top, right, bottom = font.getbbox(GLYPH)
    shape = Image.new("L", (right - left, bottom - top), 0)
    ImageDraw.Draw(shape).text((-left, -top), GLYPH, font=font, fill=255)
    # Fitted by its ink: the font's box for it takes in the space either
    # side, which would leave the scales a quarter smaller than they can be.
    shape = shape.crop(shape.getbbox())
    room = size - 2 * (size // 16)      # a pixel's margin at 16
    fit = room / max(shape.size)
    w, h = (max(1, round(n * fit)) for n in shape.size)
    alpha = Image.new("L", (size, size), 0)
    alpha.paste(shape.resize((w, h), Image.LANCZOS),
                ((size - w) // 2, (size - h) // 2))
    ink = ImageColor.getrgb(_INK_ON_LIGHT if light_taskbar else _INK_ON_DARK)
    icon = Image.new("RGBA", (size, size), ink)
    icon.putalpha(alpha)
    return icon


# ---------------------------------------------------------------------------
# Win32
# ---------------------------------------------------------------------------

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    # Libraries of our own, so the prototypes set here never change those
    # of ctypes.windll's shared ones, which the app declares its own way.
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _LRESULT = ctypes.c_ssize_t
    _WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM)

    class _WNDCLASSEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("style", wintypes.UINT),
            ("lpfnWndProc", _WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
            ("hIconSm", wintypes.HICON),
        ]

    class _GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", wintypes.BYTE * 8)]

    class _NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("uVersion", wintypes.UINT),        # a union with uTimeout
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", _GUID),
            ("hBalloonIcon", wintypes.HICON),
        ]

    def _declare(lib, name, restype, *argtypes):
        fn = getattr(lib, name)
        fn.restype = restype
        fn.argtypes = list(argtypes)
        return fn

    _H, _P = wintypes.HANDLE, ctypes.c_void_p
    _RegisterClassExW = _declare(_user32, "RegisterClassExW", wintypes.ATOM,
                                 ctypes.POINTER(_WNDCLASSEXW))
    _UnregisterClassW = _declare(_user32, "UnregisterClassW", wintypes.BOOL,
                                 wintypes.LPCWSTR, wintypes.HINSTANCE)
    _CreateWindowExW = _declare(
        _user32, "CreateWindowExW", wintypes.HWND, wintypes.DWORD,
        wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
        wintypes.HMENU, wintypes.HINSTANCE, _P)
    _DestroyWindow = _declare(_user32, "DestroyWindow", wintypes.BOOL,
                              wintypes.HWND)
    _DefWindowProcW = _declare(_user32, "DefWindowProcW", _LRESULT,
                               wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                               wintypes.LPARAM)
    _GetMessageW = _declare(_user32, "GetMessageW", wintypes.BOOL,
                            ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                            wintypes.UINT, wintypes.UINT)
    _TranslateMessage = _declare(_user32, "TranslateMessage", wintypes.BOOL,
                                 ctypes.POINTER(wintypes.MSG))
    _DispatchMessageW = _declare(_user32, "DispatchMessageW", _LRESULT,
                                 ctypes.POINTER(wintypes.MSG))
    _PostMessageW = _declare(_user32, "PostMessageW", wintypes.BOOL,
                             wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                             wintypes.LPARAM)
    _PostQuitMessage = _declare(_user32, "PostQuitMessage", None,
                                ctypes.c_int)
    _RegisterWindowMessageW = _declare(_user32, "RegisterWindowMessageW",
                                       wintypes.UINT, wintypes.LPCWSTR)
    _CreatePopupMenu = _declare(_user32, "CreatePopupMenu", wintypes.HMENU)
    _AppendMenuW = _declare(_user32, "AppendMenuW", wintypes.BOOL,
                            wintypes.HMENU, wintypes.UINT, ctypes.c_size_t,
                            wintypes.LPCWSTR)
    _SetMenuDefaultItem = _declare(_user32, "SetMenuDefaultItem",
                                   wintypes.BOOL, wintypes.HMENU,
                                   wintypes.UINT, wintypes.UINT)
    _TrackPopupMenuEx = _declare(_user32, "TrackPopupMenuEx", wintypes.BOOL,
                                 wintypes.HMENU, wintypes.UINT, ctypes.c_int,
                                 ctypes.c_int, wintypes.HWND, _P)
    _DestroyMenu = _declare(_user32, "DestroyMenu", wintypes.BOOL,
                            wintypes.HMENU)
    _SetForegroundWindow = _declare(_user32, "SetForegroundWindow",
                                    wintypes.BOOL, wintypes.HWND)
    _GetCursorPos = _declare(_user32, "GetCursorPos", wintypes.BOOL,
                             ctypes.POINTER(wintypes.POINT))
    _GetSystemMetrics = _declare(_user32, "GetSystemMetrics", ctypes.c_int,
                                 ctypes.c_int)
    _GetDoubleClickTime = _declare(_user32, "GetDoubleClickTime",
                                   wintypes.UINT)
    _CreateIconFromResourceEx = _declare(
        _user32, "CreateIconFromResourceEx", wintypes.HICON, _P,
        wintypes.DWORD, wintypes.BOOL, wintypes.DWORD, ctypes.c_int,
        ctypes.c_int, wintypes.UINT)
    _LoadIconW = _declare(_user32, "LoadIconW", wintypes.HICON,
                          wintypes.HINSTANCE, _P)
    _DestroyIcon = _declare(_user32, "DestroyIcon", wintypes.BOOL,
                            wintypes.HICON)
    _GetModuleHandleW = _declare(_kernel32, "GetModuleHandleW",
                                 wintypes.HMODULE, wintypes.LPCWSTR)
    _Shell_NotifyIconW = _declare(_shell32, "Shell_NotifyIconW",
                                  wintypes.BOOL, wintypes.DWORD,
                                  ctypes.POINTER(_NOTIFYICONDATAW))
    try:        # Windows 10, 1607 on
        _SetThreadDpiAwarenessContext = _declare(
            _user32, "SetThreadDpiAwarenessContext", _P, _P)
    except AttributeError:
        _SetThreadDpiAwarenessContext = None

_WM_NULL = 0x0000
_WM_DESTROY = 0x0002
_WM_CLOSE = 0x0010
_WM_SETTINGCHANGE = 0x001A
_WM_CONTEXTMENU = 0x007B
_WM_APP = 0x8000
_WM_TRAY = _WM_APP + 1            # the taskbar's word about the icon
_WM_TIP = _WM_APP + 2             # set_tooltip, from another thread
_NIN_SELECT = 0x0400
_NIN_KEYSELECT = 0x0401
_NIM_ADD, _NIM_MODIFY, _NIM_DELETE, _NIM_SETVERSION = 0, 1, 2, 4
_NIF_MESSAGE, _NIF_ICON, _NIF_TIP, _NIF_SHOWTIP = 0x01, 0x02, 0x04, 0x80
_NOTIFYICON_VERSION_4 = 4
_MF_STRING, _MF_SEPARATOR = 0x0000, 0x0800
_TPM_LEFTALIGN, _TPM_RIGHTALIGN = 0x0000, 0x0008
_TPM_RIGHTBUTTON, _TPM_NONOTIFY, _TPM_RETURNCMD = 0x0002, 0x0080, 0x0100
_SM_CXSMICON = 49
_SM_MENUDROPALIGNMENT = 40
_IDI_APPLICATION = 32512
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
_ICON_ID = 1


def _signed16(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


class TrayIcon:
    """GetCases's icon in the notification area.

    *on_click* runs when the icon is clicked (or chosen from the keyboard);
    *menu* is called at each right-click for the menu's lines — MenuItem, or
    SEPARATOR — so it can name things as they are at that moment.  Both run
    on the icon's own thread.  A double-click counts as one click.
    """

    def __init__(self, tooltip: str, on_click: Callable[[], None],
                 menu: Callable[[], Sequence[Optional[MenuItem]]]) -> None:
        self._tooltip = tooltip
        self._on_click = on_click
        self._menu = menu
        self._hwnd = None
        self._hicon = None
        self._icon_is_ours = False      # LoadIconW's stock icon is shared
        self._shown = False
        self._dpi_aware = False
        self._last_click = float("-inf")
        self._taskbar_created = 0
        self._class_name = f"GetCasesTray{id(self):x}"
        self._ready = threading.Event()
        self._closing = False
        self._thread: Optional[threading.Thread] = None
        # Kept for as long as the window lives: Windows calls back into it.
        self._wndproc = None

    @property
    def shown(self) -> bool:
        """Whether the icon is in the taskbar now."""
        return self._shown

    def start(self, timeout: float = 5.0) -> bool:
        """Put the icon in the taskbar.  Returns whether the icon is up and
        listening — False off Windows, or where the shell will have none of
        it.  (With no taskbar yet, as early in a sign-in, the icon arrives
        when the taskbar does.)"""
        if sys.platform != "win32" or self._thread is not None:
            return self._hwnd is not None
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="getcases-tray")
        self._thread.start()
        self._ready.wait(timeout)
        return self._hwnd is not None

    def set_tooltip(self, text: str) -> None:
        """Change what the icon says when the pointer rests on it."""
        self._tooltip = text
        hwnd = self._hwnd
        if hwnd is not None:
            _PostMessageW(hwnd, _WM_TIP, 0, 0)

    def close(self, timeout: float = 2.0) -> None:
        """Take the icon out of the taskbar and end its thread
        (idempotent)."""
        self._closing = True        # for an icon still on its way up
        hwnd = self._hwnd
        if hwnd is not None:
            _PostMessageW(hwnd, _WM_CLOSE, 0, 0)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    # ------------------------------------------------------------------
    # The icon's thread
    # ------------------------------------------------------------------

    def _run(self) -> None:
        try:
            self._open_window()
        except Exception as exc:
            print(f"[tray] could not put GetCases in the taskbar: {exc}")
            self._ready.set()
            return
        self._add()
        self._ready.set()
        if self._closing:           # closed while it was coming up
            _PostMessageW(self._hwnd, _WM_CLOSE, 0, 0)
        msg = wintypes.MSG()
        while _GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            _TranslateMessage(ctypes.byref(msg))
            _DispatchMessageW(ctypes.byref(msg))
        self._release_icon()
        _UnregisterClassW(self._class_name, _GetModuleHandleW(None))

    def _open_window(self) -> None:
        # Per-monitor DPI awareness for this thread alone, whatever the rest
        # of the app has chosen: the icon is drawn at the size the taskbar
        # shows it, the menu sharp, and the taskbar's coordinates are taken
        # as given.
        if _SetThreadDpiAwarenessContext is not None:
            self._dpi_aware = bool(_SetThreadDpiAwarenessContext(
                _DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2))
        # Sent to every top-level window when Explorer (re)starts, which
        # empties the notification area.
        self._taskbar_created = _RegisterWindowMessageW("TaskbarCreated")
        hinstance = _GetModuleHandleW(None)
        self._wndproc = _WNDPROC(self._window_proc)
        wc = _WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(_WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinstance
        wc.lpszClassName = self._class_name
        if not _RegisterClassExW(ctypes.byref(wc)):
            raise ctypes.WinError(ctypes.get_last_error())
        # A top-level window that is never shown, not a message-only one:
        # those miss the broadcasts (TaskbarCreated, a change of theme).
        hwnd = _CreateWindowExW(0, self._class_name, "GetCases", 0, 0, 0, 0,
                                0, None, None, hinstance, None)
        if not hwnd:
            error = ctypes.get_last_error()
            _UnregisterClassW(self._class_name, hinstance)
            raise ctypes.WinError(error)
        self._hwnd = hwnd
        self._hicon, self._icon_is_ours = self._make_icon()

    def _window_proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == _WM_TRAY:
                event = lparam & 0xFFFF
                if event in (_NIN_SELECT, _NIN_KEYSELECT):
                    self._clicked()
                elif event == _WM_CONTEXTMENU:
                    self._show_menu(_signed16(wparam), _signed16(wparam >> 16))
                return 0
            if msg == _WM_TIP:
                self._notify(_NIM_MODIFY, _NIF_TIP | _NIF_SHOWTIP)
                return 0
            if self._taskbar_created and msg == self._taskbar_created:
                self._redraw()      # the new taskbar's size and colours
                self._add()
                return 0
            if (msg == _WM_SETTINGCHANGE and lparam
                    and ctypes.wstring_at(lparam) == "ImmersiveColorSet"):
                self._redraw()      # light taskbar to dark, or back
            elif msg == _WM_CLOSE:
                self._remove()
                _DestroyWindow(hwnd)
                return 0
            elif msg == _WM_DESTROY:
                self._hwnd = None
                _PostQuitMessage(0)
                return 0
        except Exception as exc:
            print(f"[tray] {exc}")
        return _DefWindowProcW(hwnd, msg, wparam, lparam)

    def _clicked(self) -> None:
        # Explorer passes a double-click on as two clicks, and Enter on the
        # icon as two selections: one click per double-click time.
        now = time.monotonic()
        if now - self._last_click < _GetDoubleClickTime() / 1000:
            return
        self._last_click = now
        self._call(self._on_click)

    def _show_menu(self, x: int, y: int) -> None:
        if not self._dpi_aware:
            # The anchor Explorer gives is in physical pixels, which a
            # thread that is not DPI-aware does not use; the pointer is
            # near enough.
            point = wintypes.POINT()
            _GetCursorPos(ctypes.byref(point))
            x, y = point.x, point.y
        actions: dict[int, Callable[[], None]] = {}
        hmenu = _CreatePopupMenu()
        if not hmenu:
            return
        try:
            for command, item in enumerate(self._menu(), start=1):
                if item is SEPARATOR:
                    _AppendMenuW(hmenu, _MF_SEPARATOR, 0, None)
                    continue
                _AppendMenuW(hmenu, _MF_STRING, command, item.label)
                actions[command] = item.action
                if item.default:
                    _SetMenuDefaultItem(hmenu, command, 0)
            # Without the foreground, the menu stays up when the reader
            # clicks away from it; the null message after it is the
            # documented companion (KB135788).
            _SetForegroundWindow(self._hwnd)
            align = (_TPM_RIGHTALIGN if _GetSystemMetrics(_SM_MENUDROPALIGNMENT)
                     else _TPM_LEFTALIGN)
            chosen = _TrackPopupMenuEx(
                hmenu, align | _TPM_RIGHTBUTTON | _TPM_NONOTIFY | _TPM_RETURNCMD,
                x, y, self._hwnd, None)
            _PostMessageW(self._hwnd, _WM_NULL, 0, 0)
        finally:
            _DestroyMenu(hmenu)
        action = actions.get(chosen)
        if action is not None:
            self._call(action)

    @staticmethod
    def _call(action: Callable[[], None]) -> None:
        try:
            action()
        except Exception as exc:        # never take the icon down with it
            print(f"[tray] {exc}")

    # ------------------------------------------------------------------
    # The icon itself
    # ------------------------------------------------------------------

    def _make_icon(self):
        """The scales, sized and inked for the taskbar: (HICON, whether it
        is ours to destroy)."""
        size = _GetSystemMetrics(_SM_CXSMICON) or 16
        try:
            png = io.BytesIO()
            draw_scales(size, taskbar_is_light()).save(png, "PNG")
            data = png.getvalue()
            buffer = ctypes.create_string_buffer(data, len(data))
            # Windows reads an icon resource stored as PNG (Vista on).
            hicon = _CreateIconFromResourceEx(buffer, len(data), True,
                                              0x00030000, size, size, 0)
            if hicon:
                return hicon, True
        except Exception as exc:
            print(f"[tray] could not draw the scales: {exc}")
        return _LoadIconW(None, _IDI_APPLICATION), False

    def _release_icon(self) -> None:
        hicon, self._hicon = self._hicon, None
        if hicon and self._icon_is_ours:
            _DestroyIcon(hicon)

    def _redraw(self) -> None:
        old, old_is_ours = self._hicon, self._icon_is_ours
        self._hicon, self._icon_is_ours = self._make_icon()
        if self._shown:
            self._notify(_NIM_MODIFY, _NIF_ICON)
        if old and old_is_ours:
            _DestroyIcon(old)

    def _data(self, flags: int) -> "_NOTIFYICONDATAW":
        data = _NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = _ICON_ID
        data.uFlags = flags
        data.uCallbackMessage = _WM_TRAY
        data.hIcon = self._hicon
        data.szTip = self._tooltip[:127]
        return data

    def _notify(self, message: int, flags: int) -> bool:
        if self._hwnd is None:
            return False
        return bool(_Shell_NotifyIconW(message, ctypes.byref(self._data(flags))))

    def _add(self) -> None:
        flags = _NIF_MESSAGE | _NIF_ICON | _NIF_TIP | _NIF_SHOWTIP
        # Modified where the add is refused because the icon is still there:
        # a TaskbarCreated from a taskbar that kept its icons.
        self._shown = (self._notify(_NIM_ADD, flags)
                       or self._notify(_NIM_MODIFY, flags))
        if self._shown:
            # Clicks as NIN_SELECT, the keyboard as NIN_KEYSELECT, and the
            # menu as WM_CONTEXTMENU at the icon — Shift+F10 included.
            data = self._data(0)
            data.uVersion = _NOTIFYICON_VERSION_4
            _Shell_NotifyIconW(_NIM_SETVERSION, ctypes.byref(data))
        else:
            print("[tray] the taskbar did not take the icon; it will be "
                  "added when the taskbar next starts.")

    def _remove(self) -> None:
        if self._shown:
            self._notify(_NIM_DELETE, 0)
            self._shown = False
