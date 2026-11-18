"""One background operation per window; all callbacks stay on the Tk thread."""
from concurrent.futures import ThreadPoolExecutor
import tkinter as tk


class UiJobs:
    def __init__(self, root):
        self.root = root
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='prime-ui')
        self.pending = None
        self.closed = False
        self.timer = root.after(40, self.pump)

    def submit(self, work, success, failure):
        if self.closed or self.pending is not None:
            return False
        self.pending = (self.pool.submit(work), success, failure)
        return True

    def pump(self):
        if self.closed:
            return
        if self.pending is not None and self.pending[0].done():
            future, success, failure = self.pending
            self.pending = None
            try:
                value = future.result()
                success(value)
            except Exception as error:
                failure(error)
        if not self.closed:
            self.timer = self.root.after(40, self.pump)

    def close(self):
        self.closed = True
        try:
            self.root.after_cancel(self.timer)
        except (RuntimeError, tk.TclError):
            pass
        self.pool.shutdown(wait=False, cancel_futures=True)
