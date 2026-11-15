"""Per-user GUI activation channel, independent from the automation mutex.

The Qt local server prevents a second GUI/tray process. A second launch sends
SHOW to the first process; the updater can send QUIT and wait for its exit.
No network port, elevated privileges or saved application state is involved.
"""

import hashlib
import os
from pathlib import Path
import time

from PySide6.QtCore import QObject, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket


def server_name() -> str:
    """Stable across source and installed launches, but scoped to one account."""
    user_root = os.path.normcase(os.path.abspath(
        os.environ.get("LOCALAPPDATA") or str(Path.home())
    )).casefold()
    suffix = hashlib.sha256(user_root.encode("utf-8")).hexdigest()[:20]
    return f"WallpaperOrchestrator-GUI-{suffix}"


def send_command(command: str, *, timeout_ms: int = 1000) -> bool:
    """Return True only if the running GUI acknowledged a supported command."""
    if command not in {"SHOW", "QUIT", "PING"}:
        raise ValueError("Unsupported GUI command")
    socket = QLocalSocket()
    try:
        socket.connectToServer(server_name())
        if not socket.waitForConnected(timeout_ms):
            return False
        if socket.write((command + "\n").encode("ascii")) < 0:
            return False
        socket.flush()
        if socket.bytesToWrite() > 0 and not socket.waitForBytesWritten(timeout_ms):
            return False
        if socket.bytesAvailable() == 0 and not socket.waitForReadyRead(timeout_ms):
            return False
        return bytes(socket.readAll()).startswith(b"OK\n")
    finally:
        socket.close()


def request_gui_exit(*, timeout_seconds: float = 20.0) -> bool:
    """Ask a tray-hidden GUI to exit before an installer replaces its DLLs.

    No running GUI is success. A GUI that fails to respond or keeps running
    after acknowledging QUIT is an error; the installer must not assume exit.
    """
    if not send_command("PING", timeout_ms=700):
        return True
    if not send_command("QUIT", timeout_ms=1500):
        return False
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not send_command("PING", timeout_ms=350):
            return True
        time.sleep(0.25)
    return False


class GuiInstanceGateway(QObject):
    """Own the named server for the entire lifetime of the QApplication."""

    def __init__(self, on_show=None, on_quit=None):
        super().__init__()
        self.on_show = on_show
        self.on_quit = on_quit
        self._server = QLocalServer(self)
        self._connections = set()
        # Restrict access to this Windows logon user when Qt supports it.
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._accept)

    def claim(self) -> bool:
        """False means a pre-existing GUI was notified; never start a twin."""
        if send_command("SHOW", timeout_ms=400):
            return False
        if self._server.listen(server_name()):
            return True
        # Another instance might have won startup between connect and listen.
        if send_command("SHOW", timeout_ms=1000):
            return False
        raise RuntimeError("Cannot acquire the GUI single-instance channel. "
                           "Close an old Wallpaper Orchestrator process and retry.")

    def _accept(self):
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            self._connections.add(socket)
            socket.readyRead.connect(lambda s=socket: self._receive(s))
            socket.disconnected.connect(lambda s=socket: self._drop(s))
            if socket.bytesAvailable():
                self._receive(socket)

    def _receive(self, socket):
        command = bytes(socket.readAll()).strip()
        if command == b"SHOW":
            if self.on_show:
                self.on_show()
        elif command == b"QUIT":
            if self.on_quit:
                # Defer quitting until after the IPC response is transmitted.
                QTimer.singleShot(0, self.on_quit)
        elif command != b"PING":
            socket.write(b"ERROR\n")
            socket.flush()
            return
        socket.write(b"OK\n")
        socket.flush()

    def _drop(self, socket):
        self._connections.discard(socket)
        socket.deleteLater()

    def close(self):
        self._server.close()
        for socket in tuple(self._connections):
            socket.close()
        self._connections.clear()
