"""A small form window to practise j5 on: ``python3 practice_form.py``.

The window is titled "j5 practice form" and has three labelled fields (Name, Email,
Password, the password masked) and a Submit button. Submitting prints one line to
standard output, ``submitted name='...' email='...' password_chars=N`` (never the
password itself), shows "Saved" under the button until a field is edited again, and
clears the fields, so a script can fill the form again. The example scripts of this
skill drive it.

Only the standard library is used (tkinter: the ``python3-tk`` package on Debian and
Ubuntu). It sends no input and reads nothing but its own fields.
"""

from __future__ import annotations

import sys
import tkinter as tk

TITLE = "j5 practice form"


def main() -> int:
    root = tk.Tk()
    root.title(TITLE)
    root.geometry("360x220")
    root.resizable(False, False)

    entries: dict[str, tk.Entry] = {}
    for row, name in enumerate(("Name", "Email", "Password")):
        tk.Label(root, text=name, anchor="w").grid(row=row, column=0, sticky="w",
                                                   padx=(16, 8), pady=8)
        entry = tk.Entry(root, width=26, show="*" if name == "Password" else "")
        entry.grid(row=row, column=1, sticky="w", pady=8)
        entries[name] = entry

    status = tk.Label(root, text="", fg="#1a7f37")
    for entry in entries.values():
        entry.bind("<Key>", lambda _event: status.config(text=""))

    def submit() -> None:
        name = entries["Name"].get()
        email = entries["Email"].get()
        chars = len(entries["Password"].get())
        print(f"submitted name={name!r} email={email!r} password_chars={chars}", flush=True)
        for entry in entries.values():
            entry.delete(0, "end")
        status.config(text="Saved")

    tk.Button(root, text="Submit", command=submit).grid(row=3, column=1, sticky="w",
                                                         pady=8)
    status.grid(row=4, column=1, sticky="w")
    print(f"ready: {TITLE}", flush=True)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
