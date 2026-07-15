"""Per-data-root single-instance lock and activation channel."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path

from PySide6.QtCore import QIODevice, QLockFile
from PySide6.QtNetwork import QLocalServer, QLocalSocket

_ACTIVATE_MESSAGE = b"ACTIVATE\n"


class InstanceOutcome(StrEnum):
    """Result of attempting to become the primary local process."""

    PRIMARY = "primary"
    NOTIFIED_EXISTING = "notified_existing"
    EXISTING_UNREACHABLE = "existing_unreachable"


class SingleInstanceError(RuntimeError):
    """Raised when the primary activation server cannot be created."""


class SingleInstanceCoordinator:
    """Protect one SQLite root and ask an existing process to raise its window."""

    def __init__(self, cache_dir: Path, data_dir: Path) -> None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        identity = hashlib.sha256(str(data_dir.resolve()).encode()).hexdigest()[:20]
        self.server_name = f"astraweft-{identity}"
        self._lock = QLockFile(str(cache_dir / f"{self.server_name}.lock"))
        self._server = QLocalServer()
        self._server.newConnection.connect(self._accept_connections)
        self._activation_handler: Callable[[], None] | None = None
        self._pending_activation = False
        self._primary = False

    def start(self) -> InstanceOutcome:
        if self._primary:
            return InstanceOutcome.PRIMARY
        if self._lock.tryLock(0):
            QLocalServer.removeServer(self.server_name)
            if not self._server.listen(self.server_name):
                self._lock.unlock()
                raise SingleInstanceError(
                    f"unable to listen on local activation channel: {self._server.errorString()}"
                )
            self._primary = True
            return InstanceOutcome.PRIMARY

        return (
            InstanceOutcome.NOTIFIED_EXISTING
            if self._notify_existing()
            else InstanceOutcome.EXISTING_UNREACHABLE
        )

    def set_activation_handler(self, handler: Callable[[], None]) -> None:
        self._activation_handler = handler
        if self._pending_activation:
            self._pending_activation = False
            handler()

    def close(self) -> None:
        if self._server.isListening():
            self._server.close()
            QLocalServer.removeServer(self.server_name)
        if self._primary:
            self._lock.unlock()
            self._primary = False

    def _notify_existing(self) -> bool:
        socket = QLocalSocket()
        socket.connectToServer(self.server_name, QIODevice.OpenModeFlag.WriteOnly)
        if not socket.waitForConnected(1000):
            return False
        if socket.write(_ACTIVATE_MESSAGE) != len(_ACTIVATE_MESSAGE):
            socket.abort()
            return False
        socket.flush()
        written = socket.bytesToWrite() == 0 or socket.waitForBytesWritten(1000)
        socket.disconnectFromServer()
        return written

    def _accept_connections(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            socket.readyRead.connect(lambda connection=socket: self._read_message(connection))
            socket.disconnected.connect(socket.deleteLater)
            if socket.bytesAvailable():
                self._read_message(socket)

    def _read_message(self, socket: QLocalSocket) -> None:
        if _ACTIVATE_MESSAGE.strip() in socket.readAll().data():
            if self._activation_handler is None:
                self._pending_activation = True
            else:
                self._activation_handler()
