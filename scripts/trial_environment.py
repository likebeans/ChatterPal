#!/usr/bin/env python3
"""Private, pinned macOS LAN trial. Uses only the standard library.

Prepare with an existing full venv and an already-built frontend; this command
never installs packages, starts models, reads dotenv values, or installs trust.
The API child alone loads the original dotenv at launch, before applying its
fallback timeout. All commands return JSON, including sanitized failures.
"""

from __future__ import annotations

import argparse
import ast
import fcntl
import hashlib
import http.client
import io
import ipaddress
import json
import os
import plistlib
import re
import shutil
import socket
import sqlite3
import ssl
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterator, Sequence
from urllib.parse import quote

BASE_COMMIT = "173f9f3fe17555f63c14fa6cb18705516b2db49e"
RELEASE_ID = BASE_COMMIT[:7]
SUPPORTED_COMMITS = {
    "a253d924e7ad92b4585afe1f4d334527b8a61722",
    BASE_COMMIT,
}
DEFAULT_STATE_DIR = Path.home() / "Library/Application Support/ChatterPal/trial"
LABELS = {
    "api": "local.chatterpal.trial.api",
    "https": "local.chatterpal.trial.https",
    "awake": "local.chatterpal.trial.awake",
}
API_PORT = 18300
HTTPS_PORT = 8443
CA_PORT = 18080
ADMIN_PORT = 12019
SCHEMA_VERSION = 1

# The final process is the ordinary CLI invocation. Loading dotenv in this small
# child first lets a timeout supplied there win over the trial's fallback, without
# ever reading or persisting its values in prepare, status, or the manifest.
API_LAUNCHER = '''"""Load the original provider file in the API child only."""
import os
from pathlib import Path
import sys
from chatterpal.cli.config import load_environment

# Settings accepts case-insensitive names. Reapply launchd's enforced trial
# settings after dotenv, so a lowercase dotenv duplicate cannot redirect data.
fixed = {key: value for key, value in os.environ.items() if key in {
    "LEARNING_ENABLED", "LEARNING_DB_PATH", "LEARNING_COOKIE_SECURE",
    "LEARNING_ALLOWED_ORIGINS", "AUDIO_TEMP_DIR", "CACHE_DIR", "MODEL_CACHE_DIR",
    "LOG_FILE", "TMPDIR", "XDG_CACHE_HOME", "HOME", "PATH", "PYTHONPATH", "PYTHONHOME",
    "PYTHONNOUSERSITE", "PYTHONSAFEPATH", "PYTHONDONTWRITEBYTECODE",
}}
load_environment(Path(sys.argv[sys.argv.index("--env-file") + 1]))
for key in list(os.environ):
    if key.upper() in fixed:
        os.environ[key] = fixed[key.upper()]
os.environ.update(fixed)
if not any(key.upper() == "OPENAI_TIMEOUT" for key in os.environ):
    os.environ["OPENAI_TIMEOUT"] = "60"
os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
'''


class TrialError(Exception):
    """A deliberately secret-free operator error."""


def _absolute(path: str | Path) -> Path:
    # Do not resolve the venv's Python symlink: doing so loses the venv prefix.
    return Path(os.path.abspath(Path(path).expanduser()))


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _tree_hashes(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise TrialError("Release inputs must not contain symlinks.")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = _digest(path)
        elif not path.is_dir():
            raise TrialError("Release inputs must contain only regular files.")
    return result


def _bundle_digest(hashes: dict[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _write_private(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(content)


def _safe_process_env() -> dict[str, str]:
    # Git and version probes have no reason to inherit provider credentials.
    return {
        "HOME": str(Path.home()),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin",
        "LANG": "en_US.UTF-8",
    }


def _run(command: Sequence[str], *, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(command),
        capture_output=True,
        check=False,
        timeout=timeout,
        env=_safe_process_env(),
    )


def _hostname(value: str) -> str:
    hostname = value.lower().rstrip(".")
    labels = hostname.split(".")
    if (
        len(hostname) > 253
        or not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in labels
        )
        or hostname == "localhost"
        or not (len(labels) == 1 or hostname.endswith((".local", ".lan", ".home.arpa")))
    ):
        raise TrialError("Use a LAN hostname, for example chatterpal.local.")
    return hostname


def _lan_ip(value: str) -> str:
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        raise TrialError("--lan-ip must be a private RFC1918 IPv4 address.") from None
    networks = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    if not any(address in ipaddress.ip_network(network) for network in networks):
        raise TrialError("--lan-ip must be a private RFC1918 IPv4 address.")
    return str(address)


def _existing_file(path: str | Path, description: str, *, executable=False) -> Path:
    selected = _absolute(path)
    if not selected.is_file() or (executable and not os.access(selected, os.X_OK)):
        raise TrialError(f"An existing {description} is required.")
    return selected


def _python_version(python: Path) -> dict:
    program = (
        "import importlib.metadata as m, json, sys; "
        "print(json.dumps({'version':sys.version, 'prefix':sys.prefix, "
        "'base_prefix':sys.base_prefix, 'packages': sorted("
        "[(d.metadata['Name'], d.version) for d in m.distributions()])}))"
    )
    result = _run([str(python), "-I", "-c", program])
    if result.returncode:
        raise TrialError("The selected Python could not report its environment.")
    try:
        details = json.loads(result.stdout)
        if details["prefix"] == details["base_prefix"]:
            raise TrialError("--python must name an existing venv interpreter.")
        packages = {name.lower(): version for name, version in details["packages"]}
        if "python-dotenv" not in packages or "fastapi" not in packages:
            raise TrialError("The existing venv needs ChatterPal's API dependencies.")
        details["sha256"] = _digest(python)
        return details
    except (KeyError, ValueError, TypeError):
        raise TrialError(
            "The selected Python returned invalid version metadata."
        ) from None


def _caddy_version(caddy: Path) -> dict:
    result = _run([str(caddy), "version"])
    if result.returncode:
        raise TrialError("The selected Caddy could not report its version.")
    # Do not relay arbitrary executable output into a manifest or an error.
    match = re.match(rb"v?([0-9]+\.[0-9]+\.[0-9]+)(?:\s|$|[-+])", result.stdout)
    if match is None:
        raise TrialError("The selected Caddy returned invalid version metadata.")
    return {"version": match[1].decode("ascii"), "sha256": _digest(caddy)}


def _verify_entrypoint(source: Path) -> None:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "__name__"
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)
            and len(test.comparators) == 1
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value == "__main__"
            and any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id == "main"
                for child in ast.walk(node)
            )
        ):
            return
    raise TrialError("The pinned CLI has no supported module entrypoint.")


def _freeze_source(repository: Path, destination: Path) -> dict:
    result = _run(
        [
            "/usr/bin/git",
            "-C",
            str(repository),
            "archive",
            "--format=tar",
            BASE_COMMIT,
            "src",
            "pyproject.toml",
            "uv.lock",
        ]
    )
    if result.returncode:
        raise TrialError("The pinned Git source is unavailable in this checkout.")
    destination.mkdir(parents=True, mode=0o700)
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
        for member in archive:
            relative = PurePosixPath(member.name)
            if relative.is_absolute() or ".." in relative.parts:
                raise TrialError("The pinned source archive has an unsafe path.")
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
            elif member.isfile():
                stream = archive.extractfile(member)
                if stream is None:
                    raise TrialError("The pinned source archive is incomplete.")
                with stream:
                    _write_private(target, stream.read())
            else:
                raise TrialError(
                    "The pinned source archive contains a nonregular file."
                )
    _verify_entrypoint(destination / "src/chatterpal/cli/run.py")
    metadata = tomllib.loads((destination / "pyproject.toml").read_text())
    hashes = _tree_hashes(destination)
    return {
        "commit": BASE_COMMIT,
        "release": RELEASE_ID,
        "package_version": metadata["project"]["version"],
        "source_sha256": _bundle_digest(hashes),
        "source_files": hashes,
    }


def _frontend_hashes(source: Path) -> dict[str, str]:
    if not source.is_dir() or not (source / "index.html").is_file():
        raise TrialError("--frontend-dist must contain an already-built index.html.")
    hashes = _tree_hashes(source)
    for relative in hashes:
        path = PurePosixPath(relative)
        if any(part.startswith(".") for part in path.parts) or path.suffix.lower() in {
            ".env",
            ".key",
            ".pem",
            ".db",
            ".sqlite",
            ".sqlite3",
        }:
            raise TrialError("Frontend dist contains a private or hidden file.")
    return hashes


def _api_health() -> dict:
    result = {"reachable": False, "ready": False}
    connection = http.client.HTTPConnection("127.0.0.1", API_PORT, timeout=2)
    try:
        connection.request("GET", "/health")
        response = connection.getresponse()
        result["reachable"] = True
        body = response.read(65537)
        if response.status != 200 or len(body) > 65536:
            return result
        data = json.loads(body)
        names = ("asr", "tts", "llm", "chat", "correction", "ai_correction")
        services = data.get("services", {})
        result["services"] = {name: services.get(name) is True for name in names}
        result["ready"] = data.get("status") == "healthy" and all(
            result["services"].values()
        )
    except (OSError, ValueError, TypeError, AttributeError, http.client.HTTPException):
        pass
    finally:
        connection.close()
    return result


def _listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


class TrialEnvironment:
    def __init__(self, state_dir: str | Path, repository: Path | None = None):
        selected = _absolute(state_dir)
        if selected.is_symlink():
            raise TrialError("The trial state directory must not be a symlink.")
        self.root = selected.resolve()
        self.repository = repository or Path(__file__).resolve().parents[1]
        self.domain = f"gui/{os.getuid()}"

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    def paths(self) -> dict[str, str]:
        release_id = (
            self._manifest()["version"]["release"]
            if self.manifest_path.exists()
            else RELEASE_ID
        )
        return {
            "state": str(self.root),
            "release": str(self.root / "release" / release_id),
            "frontend": str(self.root / "release" / release_id / "frontend"),
            "runtime": str(self.root / "runtime"),
            "database": str(self.root / "data/learning.sqlite3"),
            "backups": str(self.root / "backups"),
            "logs": str(self.root / "logs"),
            "manifest": str(self.manifest_path),
            "caddyfile": str(self.root / "Caddyfile"),
            "ca_root": str(
                self.root / "caddy-data/caddy/pki/authorities/local/root.crt"
            ),
            "ca_public": str(self.root / "public/root.crt"),
        }

    @contextmanager
    def _lock(self) -> Iterator[None]:
        if sys.platform != "darwin":
            raise TrialError("Trial user agents require macOS launchd.")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink() or stat.S_IMODE(self.root.stat().st_mode) != 0o700:
            raise TrialError("The trial state directory must have mode 0700.")
        lock = self.root / ".lock"
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        previous_umask = os.umask(0o077)
        try:
            os.fchmod(descriptor, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise TrialError(
                    "Another trial environment operation is active."
                ) from None
            yield
        finally:
            os.umask(previous_umask)
            os.close(descriptor)

    def _launchctl(self, *arguments: str) -> subprocess.CompletedProcess:
        return _run(["/bin/launchctl", *arguments])

    def _job(self, name: str) -> dict:
        label = LABELS[name]
        result = self._launchctl("print", f"{self.domain}/{label}")
        expected = self.root / "launchagents" / f"{label}.plist"
        info = {"label": label, "loaded": False, "pid": None, "owned": False}
        if result.returncode in (3, 113):
            return info
        if result.returncode:
            raise TrialError("Could not inspect the macOS user launchd domain.")
        text = result.stdout.decode("utf-8", errors="replace")
        path = re.search(r"^\s*path = (.+)$", text, re.MULTILINE)
        pid = re.search(r"^\s*pid = ([0-9]+)$", text, re.MULTILINE)
        state = re.search(r"^\s*state = ([a-z ]+)$", text, re.MULTILINE)
        info.update(
            loaded=True,
            owned=path is not None and path[1].strip() == str(expected),
            pid=int(pid[1]) if pid else None,
            state=(
                state[1].strip()
                if state and state[1].strip() in {"running", "waiting", "exited"}
                else "unknown"
            ),
        )
        return info

    def _jobs(self) -> dict[str, dict]:
        return {name: self._job(name) for name in LABELS}

    @staticmethod
    def _check_ownership(jobs: dict[str, dict]) -> None:
        if any(job["loaded"] and not job["owned"] for job in jobs.values()):
            raise TrialError("A trial label belongs to a different state directory.")

    def _manifest(self, *, verify: bool = False, snapshot: Path | None = None) -> dict:
        managed_root = snapshot or self.root
        path = managed_root / "manifest.json"
        if path.is_symlink() or not path.is_file():
            raise TrialError("Run prepare before using this trial environment.")
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            version = manifest["version"]
            managed_names = {
                "api_launcher.py",
                "Caddyfile",
                *(f"launchagents/{label}.plist" for label in LABELS.values()),
            }
            if (
                manifest["schema_version"] != SCHEMA_VERSION
                or version["commit"] not in SUPPORTED_COMMITS
                or version["release"] != version["commit"][:7]
                or manifest["config"]["state_dir"] != str(self.root)
                or set(manifest["managed_files"]) != managed_names
            ):
                raise TrialError("This state contains a different trial release.")
            if verify:
                release = self.root / "release" / version["release"]
                if release.is_symlink() or not release.is_dir():
                    raise TrialError(
                        "The prepared release directory is missing or unsafe."
                    )
                source = {
                    key: value
                    for key, value in _tree_hashes(release).items()
                    if not key.startswith("frontend/")
                }
                if (
                    source != version["source_files"]
                    or _tree_hashes(release / "frontend") != version["frontend_files"]
                    or any(
                        (managed_root / relative).is_symlink()
                        or _digest(managed_root / relative) != digest
                        for relative, digest in manifest["managed_files"].items()
                    )
                ):
                    raise TrialError(
                        "Prepared release files changed; restore the frozen files."
                    )
            return manifest
        except (KeyError, ValueError, TypeError):
            raise TrialError("The trial manifest is invalid.") from None

    def _environment(
        self, config: dict, release_id: str | None = None
    ) -> dict[str, str]:
        release_id = release_id or RELEASE_ID
        return {
            "HOME": str(Path.home()),
            "PATH": (
                f"{Path(config['python']).parent}:/usr/bin:/bin:/usr/sbin:/sbin:"
                "/opt/homebrew/bin:/usr/local/bin"
            ),
            "PYTHONPATH": str(self.root / "release" / release_id / "src"),
            "PYTHONHOME": "",
            "PYTHONNOUSERSITE": "1",
            "PYTHONSAFEPATH": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "LEARNING_ENABLED": "true",
            "LEARNING_DB_PATH": str(self.root / "data/learning.sqlite3"),
            "LEARNING_COOKIE_SECURE": "true",
            "LEARNING_ALLOWED_ORIGINS": ",".join(
                f"https://{host}:{HTTPS_PORT}"
                for host in (config["hostname"], config["lan_ip"])
            ),
            "AUDIO_TEMP_DIR": str(self.root / "audio"),
            "CACHE_DIR": str(self.root / "cache"),
            "MODEL_CACHE_DIR": str(self.root / "cache/models"),
            "LOG_FILE": str(self.root / "logs/application.log"),
            "TMPDIR": str(self.root / "tmp"),
            "XDG_CACHE_HOME": str(Path.home() / ".cache"),
        }

    def _plists(self, config: dict) -> dict[str, bytes]:
        commands = {
            "api": [
                config["python"],
                str(self.root / "api_launcher.py"),
                "-m",
                "chatterpal.cli.run",
                "--mode",
                "api",
                "--env-file",
                config["env_file"],
                "--host",
                "127.0.0.1",
                "--port",
                str(API_PORT),
            ],
            "https": [
                config["caddy"],
                "run",
                "--config",
                str(self.root / "Caddyfile"),
                "--adapter",
                "caddyfile",
            ],
            "awake": ["/usr/bin/caffeinate", "-is"],
        }
        result = {}
        for name, command in commands.items():
            plist = {
                "Label": LABELS[name],
                "ProgramArguments": command,
                "WorkingDirectory": str(self.root / "runtime"),
                "RunAtLoad": True,
                "KeepAlive": True,
                "ThrottleInterval": 10,
                "Umask": 0o077,
                "StandardOutPath": str(self.root / "logs" / f"{name}.stdout.log"),
                "StandardErrorPath": str(self.root / "logs" / f"{name}.stderr.log"),
            }
            if name == "api":
                plist["EnvironmentVariables"] = self._environment(config)
            elif name == "https":
                plist["EnvironmentVariables"] = {
                    "HOME": str(Path.home()),
                    "XDG_DATA_HOME": str(self.root / "caddy-data"),
                    "XDG_CONFIG_HOME": str(self.root / "caddy-config"),
                }
            result[f"launchagents/{LABELS[name]}.plist"] = plistlib.dumps(plist)
        return result

    def _caddyfile(self, config: dict) -> str:
        # JSON quoting is also valid Caddyfile quoting; validated hosts cannot
        # inject placeholders, directives, ports, or additional site addresses.
        frontend = json.dumps(str(self.root / "release" / RELEASE_ID / "frontend"))
        public = json.dumps(str(self.root / "public"))
        hostname, lan_ip = config["hostname"], config["lan_ip"]
        return f"""{{
    admin 127.0.0.1:{ADMIN_PORT}
    auto_https disable_redirects
    skip_install_trust
}}

https://{hostname}:{HTTPS_PORT}, https://{lan_ip}:{HTTPS_PORT} {{
    bind {lan_ip} 127.0.0.1
    tls internal
    encode zstd gzip
    handle /api/health {{
        rewrite * /health
        reverse_proxy 127.0.0.1:{API_PORT}
    }}
    handle /api/* {{
        reverse_proxy 127.0.0.1:{API_PORT}
    }}
    handle {{
        root * {frontend}
        try_files {{path}} /index.html
        file_server
    }}
}}

http://:{CA_PORT} {{
    bind {lan_ip} 127.0.0.1
    handle /trial-ca.crt {{
        root * {public}
        rewrite * /root.crt
        header Content-Type application/x-x509-ca-cert
        file_server
    }}
    handle / {{
        header Content-Type "text/plain; charset=utf-8"
        respond "ChatterPal LAN trial. Download /trial-ca.crt and explicitly trust this local CA on your trial device, then visit https://{hostname}:{HTTPS_PORT}. No application or private data is available on this HTTP port."
    }}
    handle {{
        respond 404
    }}
}}
"""

    def prepare(
        self,
        *,
        python: str | Path,
        env_file: str | Path,
        frontend_dist: str | Path,
        caddy: str | Path | None = None,
        hostname: str = "chatterpal.local",
        lan_ip: str,
    ) -> dict:
        python_path = _existing_file(python, "venv Python interpreter", executable=True)
        env_path = _existing_file(env_file, "provider env file")
        binary = caddy or shutil.which("caddy")
        if binary is None:
            raise TrialError("Pass --caddy with an existing Caddy executable.")
        caddy_path = _existing_file(binary, "Caddy executable", executable=True)
        frontend = _absolute(frontend_dist).resolve()
        if self.root == frontend or self.root.is_relative_to(frontend):
            raise TrialError("The trial state must be outside the frontend dist.")
        if env_path.resolve().is_relative_to(frontend):
            raise TrialError("The provider env file must be outside the frontend dist.")
        config = {
            "state_dir": str(self.root),
            "python": str(python_path),
            "env_file": str(env_path),
            "caddy": str(caddy_path),
            "frontend_dist": str(frontend),
            "hostname": _hostname(hostname),
            "lan_ip": _lan_ip(lan_ip),
        }
        with self._lock():
            jobs = self._jobs()
            frontend_files = _frontend_hashes(frontend)
            if self.manifest_path.exists():
                existing = self._manifest(verify=True)
                if (
                    existing["version"]["commit"] != BASE_COMMIT
                    or existing["config"] != config
                    or existing["version"]["frontend_files"] != frontend_files
                    or existing["version"]["python"]["sha256"] != _digest(python_path)
                    or existing["version"]["caddy"]["sha256"] != _digest(caddy_path)
                ):
                    raise TrialError(
                        "Prepare cannot replace an existing different release."
                    )
                self._check_ownership(jobs)
                return existing
            if any(job["loaded"] for job in jobs.values()):
                raise TrialError("Prepare cannot replace a live trial service.")
            if any(path.name != ".lock" for path in self.root.iterdir()):
                raise TrialError("Prepare requires an empty state directory.")
            with tempfile.TemporaryDirectory(prefix=".prepare-", dir=self.root) as temp:
                staging = Path(temp)
                release = staging / "release" / RELEASE_ID
                version = _freeze_source(self.repository, release)
                version.update(
                    python=_python_version(python_path),
                    caddy=_caddy_version(caddy_path),
                )
                for relative in frontend_files:
                    _write_private(
                        release / "frontend" / relative,
                        (frontend / relative).read_bytes(),
                    )
                if _tree_hashes(release / "frontend") != frontend_files:
                    raise TrialError("Frontend changed during prepare; build it again.")
                version.update(
                    frontend_files=frontend_files,
                    frontend_sha256=_bundle_digest(frontend_files),
                )
                for directory in (
                    "runtime",
                    "data",
                    "backups",
                    "audio",
                    "cache/models",
                    "tmp",
                    "logs",
                    "public",
                    "caddy-data",
                    "caddy-config",
                    "launchagents",
                ):
                    (staging / directory).mkdir(parents=True, exist_ok=True, mode=0o700)
                managed = {
                    "api_launcher.py": API_LAUNCHER.encode(),
                    "Caddyfile": self._caddyfile(config).encode(),
                    **self._plists(config),
                }
                for relative, content in managed.items():
                    _write_private(staging / relative, content)
                for name in LABELS:
                    for suffix in ("stdout", "stderr"):
                        _write_private(staging / "logs" / f"{name}.{suffix}.log", b"")
                manifest = {
                    "schema_version": SCHEMA_VERSION,
                    "prepared_at": datetime.now(timezone.utc).isoformat(),
                    "config": config,
                    "version": version,
                    "managed_files": {
                        relative: hashlib.sha256(content).hexdigest()
                        for relative, content in managed.items()
                    },
                    "policy": {
                        "openai_timeout_fallback_seconds": 60,
                        "audio_retention": "participant_opt_in_in_ui",
                        "trust_installation": "manual_on_trial_device",
                    },
                }
                _write_private(
                    staging / "manifest.json",
                    (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(),
                )
                moved = []
                try:
                    for child in sorted(
                        staging.iterdir(), key=lambda path: path.name == "manifest.json"
                    ):
                        destination = self.root / child.name
                        os.replace(child, destination)
                        moved.append(destination)
                except OSError:
                    for destination in reversed(moved):
                        if destination.is_dir():
                            shutil.rmtree(destination)
                        else:
                            destination.unlink(missing_ok=True)
                    raise
                return manifest

    def _require_stopped(self) -> None:
        jobs = self._jobs()
        self._check_ownership(jobs)
        if any(job["loaded"] for job in jobs.values()):
            raise TrialError("Stop the trial service before switching releases.")

    def _activate_snapshot(self, snapshot: Path) -> None:
        manifest = self._manifest(verify=True, snapshot=snapshot)
        replacements = {
            relative: (snapshot / relative).read_bytes()
            for relative in (*manifest["managed_files"], "manifest.json")
        }
        original = {
            relative: (self.root / relative).read_bytes() for relative in replacements
        }

        def install(files: dict[str, bytes]) -> None:
            for relative, content in files.items():
                destination = self.root / relative
                with tempfile.TemporaryDirectory(
                    prefix=".switch-", dir=destination.parent
                ) as temp:
                    temporary = Path(temp) / destination.name
                    _write_private(temporary, content)
                    os.replace(temporary, destination)

        try:
            install(replacements)
        except BaseException:
            install(original)
            raise

    def upgrade(self, *, frontend_dist: str | Path) -> dict:
        frontend = _absolute(frontend_dist).resolve()
        with self._lock():
            existing = self._manifest(verify=True)
            config = {**existing["config"], "frontend_dist": str(frontend)}
            if (
                self.root == frontend
                or self.root.is_relative_to(frontend)
                or Path(config["env_file"]).resolve().is_relative_to(frontend)
            ):
                raise TrialError(
                    "Trial state and provider env must be outside the frontend dist."
                )
            frontend_files = _frontend_hashes(frontend)
            self._require_stopped()
            if existing["version"]["commit"] == BASE_COMMIT:
                if existing["version"]["frontend_files"] != frontend_files:
                    raise TrialError(
                        "The active release already has a different frontend build."
                    )
                return {"upgraded": False, "version": existing["version"]["commit"]}
            for name in ("python", "caddy"):
                if _digest(Path(config[name])) != existing["version"][name]["sha256"]:
                    raise TrialError(
                        "A prepared executable changed; restore it before upgrading."
                    )
            _existing_file(config["env_file"], "original provider env file")
            destination = self.root / "release" / RELEASE_ID
            if destination.exists() or destination.is_symlink():
                raise TrialError(
                    "The target release directory already exists; inspect it before retrying."
                )
            with tempfile.TemporaryDirectory(prefix=".upgrade-", dir=self.root) as temp:
                staging = Path(temp)
                release = staging / RELEASE_ID
                version = _freeze_source(self.repository, release)
                version.update(
                    python=existing["version"]["python"],
                    caddy=existing["version"]["caddy"],
                )
                for relative in frontend_files:
                    _write_private(
                        release / "frontend" / relative,
                        (frontend / relative).read_bytes(),
                    )
                if _tree_hashes(release / "frontend") != frontend_files:
                    raise TrialError("Frontend changed during upgrade; build it again.")
                version.update(
                    frontend_files=frontend_files,
                    frontend_sha256=_bundle_digest(frontend_files),
                )
                # Both supported releases use the same application and database schema.
                if {
                    key: value
                    for key, value in version["source_files"].items()
                    if key.startswith("src/") or key == "uv.lock"
                } != {
                    key: value
                    for key, value in existing["version"]["source_files"].items()
                    if key.startswith("src/") or key == "uv.lock"
                }:
                    raise TrialError(
                        "This upgrade requires a separate database migration review."
                    )
                backup = self._backup()
                timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
                upgrade_dir = self.root / "upgrades" / timestamp
                previous = upgrade_dir / "previous"
                for relative in (*existing["managed_files"], "manifest.json"):
                    _write_private(
                        previous / relative, (self.root / relative).read_bytes()
                    )
                managed = {
                    "api_launcher.py": API_LAUNCHER.encode(),
                    "Caddyfile": self._caddyfile(config).encode(),
                    **self._plists(config),
                }
                manifest = {
                    **existing,
                    "upgraded_at": datetime.now(timezone.utc).isoformat(),
                    "config": config,
                    "version": version,
                    "managed_files": {
                        relative: hashlib.sha256(content).hexdigest()
                        for relative, content in managed.items()
                    },
                    "upgrade": {
                        "from_commit": existing["version"]["commit"],
                        "rollback_snapshot": str(previous),
                        "backup": backup,
                    },
                }
                next_snapshot = upgrade_dir / "next"
                for relative, content in managed.items():
                    _write_private(next_snapshot / relative, content)
                _write_private(
                    next_snapshot / "manifest.json",
                    (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(),
                )
                os.replace(release, destination)
                try:
                    self._activate_snapshot(next_snapshot)
                except BaseException:
                    shutil.rmtree(destination)
                    raise
            return {
                "upgraded": True,
                "version": version["commit"],
                "backup": backup,
                "rollback_snapshot": str(previous),
                "paths": self.paths(),
            }

    def rollback(self, *, snapshot: str | Path) -> dict:
        selected = _absolute(snapshot)
        if (
            selected.is_symlink()
            or selected.resolve().parent.parent != self.root / "upgrades"
            or selected.name not in {"previous", "next"}
        ):
            raise TrialError(
                "Select a release snapshot from this trial's upgrades directory."
            )
        with self._lock():
            self._require_stopped()
            # Verify the saved release even if an interrupted switch left active files inconsistent.
            manifest = self._manifest(verify=True, snapshot=selected)
            self._activate_snapshot(selected)
            return {
                "restored": True,
                "version": manifest["version"]["commit"],
                "paths": self.paths(),
            }

    def _assert_ports_available(self, config: dict, jobs: dict) -> None:
        listeners = []
        if not jobs["api"]["loaded"]:
            listeners.append(("127.0.0.1", API_PORT))
        if not jobs["https"]["loaded"]:
            listeners += [
                ("127.0.0.1", ADMIN_PORT),
                ("127.0.0.1", HTTPS_PORT),
                ("127.0.0.1", CA_PORT),
                (config["lan_ip"], HTTPS_PORT),
                (config["lan_ip"], CA_PORT),
            ]
        for host, port in listeners:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    listener.bind((host, port))
            except OSError:
                raise TrialError(
                    f"Required listener {host}:{port} is unavailable; check the LAN IP and existing services."
                ) from None

    def _wait_for_api(self, timeout: float, previous_pid: int | None = None) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self._job("api")
            if (
                job["loaded"]
                and job["owned"]
                and job["pid"]
                and job["pid"] != previous_pid
                and _api_health()["ready"]
            ):
                return
            time.sleep(0.25)
        raise TrialError("API readiness timed out; inspect the private trial logs.")

    def _wait_for_agents(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            jobs = self._jobs()
            if all(
                job["loaded"] and job["owned"] and job["pid"] for job in jobs.values()
            ):
                return
            time.sleep(0.25)
        raise TrialError(
            "Trial agents did not become ready; inspect the private trial logs."
        )

    def _publish_ca(self, timeout: float, config: dict) -> None:
        deadline = time.monotonic() + timeout
        certificate = Path(self.paths()["ca_root"])
        while time.monotonic() < deadline:
            job = self._job("https")
            if (
                job["loaded"]
                and job["owned"]
                and job["pid"]
                and certificate.is_file()
                and not certificate.is_symlink()
                and _listening("127.0.0.1", ADMIN_PORT)
                and _listening(config["lan_ip"], HTTPS_PORT)
                and _listening(config["lan_ip"], CA_PORT)
            ):
                content = certificate.read_bytes()
                if len(content) > 65536 or not re.fullmatch(
                    rb"-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\s]+-----END CERTIFICATE-----\s*",
                    content,
                ):
                    raise TrialError("Caddy's public root certificate is invalid.")
                try:
                    ssl.PEM_cert_to_DER_cert(content.decode("ascii"))
                except (ValueError, UnicodeError):
                    raise TrialError(
                        "Caddy's public root certificate is invalid."
                    ) from None
                public = Path(self.paths()["ca_public"])
                temporary = public.with_suffix(".tmp")
                temporary.unlink(missing_ok=True)
                _write_private(temporary, content)
                os.replace(temporary, public)
                return
            time.sleep(0.25)
        raise TrialError("HTTPS readiness timed out; inspect the private trial logs.")

    def _bootout(self, name: str) -> None:
        job = self._job(name)
        self._check_ownership({name: job})
        if job["loaded"]:
            result = self._launchctl("bootout", f"{self.domain}/{LABELS[name]}")
            if result.returncode not in (0, 3, 113):
                raise TrialError(f"Could not stop the trial {name} agent.")
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                remaining = self._job(name)
                self._check_ownership({name: remaining})
                if not remaining["loaded"]:
                    return
                time.sleep(0.1)
            raise TrialError(f"The trial {name} agent did not finish stopping.")

    def start(self, timeout: float = 120) -> dict:
        with self._lock():
            manifest = self._manifest(verify=True)
            config = manifest["config"]
            for name in ("python", "caddy"):
                if _digest(Path(config[name])) != manifest["version"][name]["sha256"]:
                    raise TrialError(
                        "A prepared executable changed; use a new state directory."
                    )
            _existing_file(config["env_file"], "original provider env file")
            jobs = self._jobs()
            self._check_ownership(jobs)
            self._assert_ports_available(config, jobs)
            attempted = []
            try:
                for name in LABELS:
                    if not jobs[name]["loaded"]:
                        attempted.append(name)
                        plist = self.root / "launchagents" / f"{LABELS[name]}.plist"
                        result = self._launchctl("bootstrap", self.domain, str(plist))
                        if result.returncode:
                            raise TrialError(
                                f"Could not bootstrap the trial {name} agent."
                            )
                    if name == "api":
                        self._wait_for_api(timeout)
                self._publish_ca(min(timeout, 30), config)
                self._wait_for_agents(min(timeout, 30))
                result = self.status()
                if not result["ready"]:
                    raise TrialError(
                        "Trial readiness failed; inspect the private trial logs."
                    )
            except BaseException as error:
                failed = []
                for name in reversed(attempted):
                    try:
                        self._bootout(name)
                    except (TrialError, OSError, subprocess.SubprocessError):
                        failed.append(name)
                if failed:
                    raise TrialError(
                        "Start failed and some new trial agents could not be stopped; run stop."
                    ) from None
                if isinstance(error, (TrialError, KeyboardInterrupt, SystemExit)):
                    raise
                raise TrialError(
                    "Start failed; newly started trial agents were stopped."
                ) from None
            return result

    def stop(self) -> dict:
        with self._lock():
            self._manifest()
            self._check_ownership(self._jobs())
            failed = []
            for name in reversed(LABELS):
                try:
                    self._bootout(name)
                except (TrialError, OSError, subprocess.SubprocessError):
                    failed.append(name)
            if failed:
                raise TrialError("Some trial agents could not be stopped; run status.")
            return self.status()

    def restart_api(self, timeout: float = 120) -> dict:
        with self._lock():
            self._manifest(verify=True)
            job = self._job("api")
            self._check_ownership({"api": job})
            if not job["loaded"]:
                raise TrialError("The API agent is not loaded; use start first.")
            result = self._launchctl(
                "kickstart", "-k", f"{self.domain}/{LABELS['api']}"
            )
            if result.returncode:
                raise TrialError("Could not restart the trial API agent.")
            self._wait_for_api(timeout, previous_pid=job["pid"])
            return self.status()

    def status(self) -> dict:
        jobs = self._jobs()
        manifest = self._manifest() if self.manifest_path.exists() else None
        health = (
            _api_health()
            if manifest and jobs["api"]["owned"] and jobs["api"]["pid"]
            else {"reachable": False, "ready": False}
        )
        paths = self.paths()
        https_ready = bool(
            manifest
            and jobs["https"]["owned"]
            and jobs["https"]["pid"]
            and _listening("127.0.0.1", ADMIN_PORT)
            and _listening(manifest["config"]["lan_ip"], HTTPS_PORT)
            and _listening(manifest["config"]["lan_ip"], CA_PORT)
            and Path(paths["ca_public"]).is_file()
        )
        health["https_ready"] = https_ready
        version = None
        urls = None
        if manifest:
            config = manifest["config"]
            paths.update(
                {name: config[name] for name in ("python", "env_file", "caddy")}
            )
            version = {
                name: manifest["version"][name]
                for name in (
                    "commit",
                    "release",
                    "package_version",
                    "source_sha256",
                    "frontend_sha256",
                )
            }
            urls = {
                "application": f"https://{config['hostname']}:{HTTPS_PORT}",
                "application_ip": f"https://{config['lan_ip']}:{HTTPS_PORT}",
                "health": f"https://{config['hostname']}:{HTTPS_PORT}/api/health",
                "certificate": f"http://{config['lan_ip']}:{CA_PORT}/trial-ca.crt",
            }
        return {
            "prepared": manifest is not None,
            "ready": bool(
                manifest
                and health["ready"]
                and https_ready
                and all(
                    job["loaded"] and job["owned"] and job["pid"]
                    for job in jobs.values()
                )
            ),
            "jobs": jobs,
            "health": health,
            "version": version,
            "paths": paths,
            "urls": urls,
        }

    def backup(self) -> dict:
        with self._lock():
            self._manifest()
            return self._backup()

    def _backup(self) -> dict:
        database = self.root / "data/learning.sqlite3"
        if database.is_symlink() or not database.is_file():
            raise TrialError("No trial learning database exists yet.")
        backups = self.root / "backups"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        destination = backups / f"learning-{timestamp}.sqlite3"
        temporary = destination.with_suffix(".tmp")
        _write_private(temporary, b"")
        deadline = time.monotonic() + 60

        def progress(status: int, remaining: int, total: int) -> None:
            if time.monotonic() > deadline:
                raise TrialError("Database backup timed out; retry when writes settle.")

        try:
            source = sqlite3.connect(
                f"file:{quote(str(database))}?mode=ro", uri=True, timeout=5
            )
            try:
                target = sqlite3.connect(temporary)
                try:
                    source.backup(target, pages=256, progress=progress, sleep=0.05)
                    if target.execute("PRAGMA quick_check").fetchone() != ("ok",):
                        raise TrialError("Database backup failed its integrity check.")
                finally:
                    target.close()
            finally:
                source.close()
            os.replace(temporary, destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return {
            "backup": str(destination),
            "bytes": destination.stat().st_size,
            "sha256": _digest(destination),
        }


def _timeout(value: str) -> float:
    try:
        selected = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "timeout must be between 1 and 600 seconds"
        ) from None
    if not 1 <= selected <= 600:
        raise argparse.ArgumentTypeError("timeout must be between 1 and 600 seconds")
    return selected


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    subcommands = parser.add_subparsers(dest="command", required=True)
    for name in (
        "prepare",
        "start",
        "stop",
        "status",
        "restart-api",
        "backup",
        "upgrade",
        "rollback",
    ):
        child = subcommands.add_parser(name)
        # Accept the shared option on either side of the subcommand.
        child.add_argument("--state-dir", type=Path, default=argparse.SUPPRESS)
        if name == "prepare":
            child.add_argument("--python", type=Path, required=True)
            child.add_argument("--env-file", type=Path, required=True)
            child.add_argument("--frontend-dist", type=Path, required=True)
            child.add_argument("--caddy", type=Path)
            child.add_argument("--hostname", default="chatterpal.local")
            child.add_argument("--lan-ip", required=True)
        if name == "upgrade":
            child.add_argument("--frontend-dist", type=Path, required=True)
        if name == "rollback":
            child.add_argument("--snapshot", type=Path, required=True)
        if name in ("start", "restart-api"):
            child.add_argument("--timeout", type=_timeout, default=120)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = create_parser().parse_args(argv)
    try:
        trial = TrialEnvironment(args.state_dir)
        if args.command == "prepare":
            manifest = trial.prepare(
                python=args.python,
                env_file=args.env_file,
                frontend_dist=args.frontend_dist,
                caddy=args.caddy,
                hostname=args.hostname,
                lan_ip=args.lan_ip,
            )
            result = {
                "prepared": True,
                "manifest": str(trial.manifest_path),
                "commit": manifest["version"]["commit"],
                "paths": trial.paths(),
            }
        elif args.command == "upgrade":
            result = trial.upgrade(frontend_dist=args.frontend_dist)
        elif args.command == "rollback":
            result = trial.rollback(snapshot=args.snapshot)
        elif args.command == "start":
            result = trial.start(args.timeout)
        elif args.command == "restart-api":
            result = trial.restart_api(args.timeout)
        else:
            result = getattr(trial, args.command)()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except TrialError as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
    except (
        OSError,
        ValueError,
        sqlite3.Error,
        subprocess.SubprocessError,
        tarfile.TarError,
    ):
        print(
            json.dumps(
                {
                    "error": "Trial operation failed; check paths, permissions, and private trial logs."
                }
            ),
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
