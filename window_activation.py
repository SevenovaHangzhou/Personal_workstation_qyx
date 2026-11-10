"""Wake the window that owns a data space through a local, user-only socket."""
import hashlib
import json
import os
from pathlib import Path
import time

from PyQt5.QtCore import QObject, QTimer, Qt, pyqtSignal
from PyQt5.QtNetwork import QLocalServer, QLocalSocket
from PyQt5.QtWidgets import QApplication


class WindowActivation(QObject):
    requested = pyqtSignal(str)

    def __init__(self, directory, parent=None):
        super().__init__(parent)
        identity = str(Path(directory).resolve()).encode('utf-8')
        self.name = 'orbit-window-' + hashlib.sha256(identity).hexdigest()[:32]
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        self.server.setMaxPendingConnections(8)
        self.server.newConnection.connect(self._accept)

    def request(self, timeout_ms=1200, retry=False):
        """Return success only after the owner has handled the wake request."""
        deadline = time.monotonic() + timeout_ms / 1000
        payload = json.dumps({'command': 'activate',
                              'startup_id': os.environ.get('DESKTOP_STARTUP_ID', '')[:512]}).encode() + b'\n'
        while time.monotonic() < deadline:
            socket = QLocalSocket()
            try:
                remaining = lambda: max(1, int((deadline - time.monotonic()) * 1000))
                socket.connectToServer(self.name)
                if socket.waitForConnected(min(200, remaining())):
                    socket.write(payload)
                    socket.flush()
                    response = b''
                    while time.monotonic() < deadline and len(response) < 32:
                        response += bytes(socket.readAll())
                        if b'\n' in response:
                            return response == b'activated\n'
                        if not socket.waitForReadyRead(remaining()):
                            response += bytes(socket.readAll())
                            return response == b'activated\n'
                if not retry:
                    return False
            finally:
                socket.abort()
            time.sleep(min(.05, max(0, deadline - time.monotonic())))
        return False

    def listen(self):
        """Call only while holding the application's data lock."""
        # A crash can leave a socket file. The data lock proves no owner remains.
        QLocalServer.removeServer(self.name)
        if not self.server.listen(self.name):
            raise OSError('星序窗口唤起服务启动失败：' + self.server.errorString())

    def close(self):
        self.server.close()

    def _accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.setReadBufferSize(2049)
            socket.disconnected.connect(socket.deleteLater)
            socket.readyRead.connect(lambda s=socket: self._read(s))
            QTimer.singleShot(2000, socket.disconnectFromServer)
            self._read(socket)

    def _read(self, socket):
        if socket.property('handled'):
            return
        if not socket.canReadLine():
            if socket.bytesAvailable() > 2048:
                socket.disconnectFromServer()
            return
        socket.setProperty('handled', True)
        try:
            message = json.loads(bytes(socket.readLine(2049)))
            if not isinstance(message, dict) or message.get('command') != 'activate':
                raise ValueError('Unknown request')
            startup_id = message.get('startup_id', '')
            if not isinstance(startup_id, str) or len(startup_id) > 512:
                raise ValueError('Invalid startup identifier')
        except (ValueError, UnicodeError):
            socket.disconnectFromServer()
            return
        self.requested.emit(startup_id)
        socket.write(b'activated\n')
        socket.flush()
        socket.disconnectFromServer()


def reveal_window(window, startup_id=''):
    """Restore without losing maximized state, then focus any blocking dialog."""
    try:
        from PyQt5.QtX11Extras import QX11Info
        if QX11Info.isPlatformX11():
            if startup_id:
                QX11Info.setNextStartupId(startup_id.encode('utf-8'))
            QX11Info.setAppUserTime(QX11Info.getTimestamp())
    except ImportError:
        pass
    window.setWindowState(window.windowState() & ~Qt.WindowMinimized)
    window.show()
    window.raise_()
    window.activateWindow()
    if window.windowHandle():
        window.windowHandle().requestActivate()
    modal = QApplication.activeModalWidget()
    if modal and modal.window() != window.window():
        modal.show()
        modal.raise_()
        modal.activateWindow()
        if modal.windowHandle():
            modal.windowHandle().requestActivate()
