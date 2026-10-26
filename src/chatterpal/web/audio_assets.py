"""Bounded generated audio files with leases for active HTTP downloads."""

import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from starlette.responses import FileResponse

_NAME = re.compile(r"response_[0-9a-f]{32}\.(?:wav|mp3|ogg|opus|flac|webm|bin)")
_MEDIA = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".flac": "audio/flac",
    ".webm": "audio/webm",
    ".bin": "application/octet-stream",
}


class AudioAssetLimit(RuntimeError):
    """Active downloads currently occupy the configured audio budget."""


class GeneratedAudioStore:
    def __init__(
        self,
        directory,
        *,
        ttl=3600,
        max_files=256,
        max_bytes=64 * 1024 * 1024,
        clock=time.time,
    ):
        if ttl <= 0 or max_files < 1 or max_bytes < 1:
            raise ValueError("Generated audio limits must be positive")
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.ttl, self.max_files, self.max_bytes = ttl, max_files, max_bytes
        self.clock = clock
        self._lock = threading.RLock()
        self._leases = {}

    def _files(self):
        files = []
        for path in self.directory.iterdir():
            if _NAME.fullmatch(path.name) and path.is_file() and not path.is_symlink():
                try:
                    info = path.stat()
                    files.append((info.st_mtime, info.st_size, path))
                except FileNotFoundError:
                    continue
        return sorted(files)

    def _prune(self, *, reserve_bytes=0, reserve_files=0):
        files = self._files()
        total, count = sum(size for _, size, _ in files), len(files)
        for modified, size, path in files:
            expired = self.clock() >= modified + self.ttl
            over = (
                total + reserve_bytes > self.max_bytes
                or count + reserve_files > self.max_files
            )
            if (expired or over) and not self._leases.get(path.name):
                path.unlink(missing_ok=True)
                total -= size
                count -= 1
        return total, count

    def cleanup(self):
        with self._lock:
            self._prune()

    def save(self, content: bytes, extension: str) -> str:
        if not isinstance(content, bytes) or not content:
            raise ValueError("Audio must contain nonempty bytes")
        if extension not in {suffix[1:] for suffix in _MEDIA}:
            raise ValueError("Unsupported audio extension")
        if len(content) > self.max_bytes:
            raise AudioAssetLimit("Generated audio exceeds the storage budget")
        with self._lock:
            total, count = self._prune(reserve_bytes=len(content), reserve_files=1)
            if total + len(content) > self.max_bytes or count + 1 > self.max_files:
                raise AudioAssetLimit("Generated audio storage is full")
            name = f"response_{uuid.uuid4().hex}.{extension}"
            path = self.directory / name
            temporary = path.with_suffix(".part")
            try:
                with os.fdopen(
                    os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                    "wb",
                ) as output:
                    output.write(content)
                os.utime(temporary, (self.clock(), self.clock()))
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            return name

    @contextmanager
    def download(self, name):
        with self._lock:
            if not _NAME.fullmatch(name):
                raise FileNotFoundError(name)
            path = self.directory / name
            if path.is_symlink() or not path.is_file():
                raise FileNotFoundError(name)
            if self.clock() >= path.stat().st_mtime + self.ttl:
                if not self._leases.get(name):
                    path.unlink(missing_ok=True)
                raise FileNotFoundError(name)
            self._leases[name] = self._leases.get(name, 0) + 1
        try:
            yield path, _MEDIA[path.suffix]
        finally:
            with self._lock:
                remaining = self._leases[name] - 1
                if remaining:
                    self._leases[name] = remaining
                else:
                    self._leases.pop(name)
                self._prune()

    def snapshot(self):
        with self._lock:
            self._prune()
            files = self._files()
            return {
                "files": len(files),
                "bytes": sum(size for _, size, _ in files),
                "active_downloads": sum(self._leases.values()),
                "max_files": self.max_files,
                "max_bytes": self.max_bytes,
                "ttl_seconds": self.ttl,
            }


class AudioDownload(FileResponse):
    """Release even when a streaming response is cancelled or fails."""

    def __init__(self, lease, path, media_type):
        super().__init__(
            path,
            media_type=media_type,
            filename=path.name,
            headers={"Cache-Control": "no-store"},
        )
        self._lease = lease

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._lease.__exit__(None, None, None)
