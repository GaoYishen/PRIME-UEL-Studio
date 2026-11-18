"""Run the desktop from this checkout or an exported self-contained bundle."""
from pathlib import Path
import sys

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))
backend = APP / "backend"
if not backend.is_dir():
    backend = APP.parents[1] / "src"
if backend.is_dir():
    sys.path.insert(1, str(backend))

if __name__ == "__main__":
    try:
        from prime_studio.gui import main
        main()
    except Exception as error:
        import traceback
        from tkinter import Tk, messagebox
        root = Tk()
        root.withdraw()
        messagebox.showerror("PRIME UEL Studio 启动失败", str(error) + "\n\n" + traceback.format_exc(), parent=root)
        root.destroy()
        raise
