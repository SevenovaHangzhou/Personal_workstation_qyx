"""Native process groups, local locks and pipe reads for Linux and Windows."""
import os
import queue
import signal
import subprocess
import threading
import time

WINDOWS=os.name=='nt'


def lock_file(stream,blocking=False):
    if WINDOWS:
        import msvcrt
        stream.seek(0,2)
        if stream.tell()==0:stream.write('0');stream.flush()
        stream.seek(0)
        if blocking:
            while True:
                try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1);return
                except OSError:time.sleep(.05)
        else:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
    else:
        import fcntl
        fcntl.flock(stream,fcntl.LOCK_EX|(0 if blocking else fcntl.LOCK_NB))


def process_options():
    return {'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_NO_WINDOW} if WINDOWS else {'start_new_session':True}


def stop_process(process):
    if process.poll() is not None:return
    if WINDOWS:
        # Stop the entire CLI tree, including npm's Node child and tools.
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,timeout=10,
                       creationflags=subprocess.CREATE_NO_WINDOW)
        try:process.wait(timeout=3)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)
    else:
        try:
            os.killpg(process.pid,signal.SIGTERM);process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=3)
        except ProcessLookupError:pass


class PipeReader:
    """Windows select() cannot read pipes; a daemon reader feeds a bounded queue."""
    def __init__(self,stream):
        self.stream=stream;self.queue=queue.Queue(maxsize=256);self.stopping=threading.Event()
        self.thread=threading.Thread(target=self._read,name='orbit-codex-pipe',daemon=True);self.thread.start()

    def _read(self):
        try:
            while not self.stopping.is_set():
                chunk=os.read(self.stream.fileno(),65536)
                while not self.stopping.is_set():
                    try:self.queue.put(chunk,timeout=.1);break
                    except queue.Full:pass
                if not chunk:return
        except (OSError,ValueError):
            if not self.stopping.is_set():
                try:self.queue.put(b'',timeout=.1)
                except queue.Full:pass

    def read(self,timeout):
        try:return self.queue.get(timeout=timeout)
        except queue.Empty:return None

    def close(self):
        self.stopping.set()
