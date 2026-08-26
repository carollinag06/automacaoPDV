from __future__ import annotations

from typing import Any


def send_keys(window: Any, keys: str) -> None:
    """Send a named pywinauto key sequence to the focused VCL window."""
    window.set_focus()
    window.type_keys(keys, set_foreground=True)


def press(window: Any, key: str) -> None:
    send_keys(window, f"{{{key}}}")


def hotkey(window: Any, *keys: str) -> None:
    # pywinauto accepts modifier syntax such as ^{F4} and %{P}.
    modifiers = {"CTRL": "^", "ALT": "%", "SHIFT": "+"}
    if len(keys) == 2 and keys[0].upper() in modifiers:
        key = keys[1]
        token = f"{{{key}}}" if len(key) > 1 else key
        send_keys(window, modifiers[keys[0].upper()] + token)
        return
    raise ValueError(f"Unsupported hotkey: {keys}")
