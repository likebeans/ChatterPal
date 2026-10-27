"""Trial launcher contracts; no launchd jobs, models, or providers are started.

Run directly with the existing venv to avoid the application's audio fixtures:
    python -I tests/test_trial_environment.py
Git snapshots and SQLite backups exercise the real standard-library boundaries.
Only launchctl is mocked at the subprocess boundary; version probes are isolated
because these tests should also work on machines without a Caddy installation.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import plistlib
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY / "scripts/trial_environment.py"
SPEC = importlib.util.spec_from_file_location("trial_environment", SCRIPT)
trial = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(trial)


class Launchctl:
    """Minimal user-domain fake; never forwards a command to launchd."""

    def __init__(self, environment):
        self.environment = environment
        self.loaded = {}
        self.calls = []
        self.fail_bootstrap = None
        self.sequence = 1000

    def __call__(self, command, **kwargs):
        if command[:1] != ["/bin/launchctl"]:
            return self.real_run(command, **kwargs)
        self.calls.append(tuple(command[1:]))
        action = command[1]
        target = command[-1]
        if action == "print":
            name = target.rsplit("/", 1)[-1]
            if name not in self.loaded:
                return subprocess.CompletedProcess(command, 113, b"", b"not found")
            path, pid = self.loaded[name]
            text = f"state = running\npath = {path}\npid = {pid}\n"
            return subprocess.CompletedProcess(command, 0, text.encode(), b"")
        if action == "bootstrap":
            name = Path(target).stem
            self.sequence += 1
            # Model launchd loading a job even when bootstrap reports a failure.
            self.loaded[name] = (target, self.sequence)
            return subprocess.CompletedProcess(
                command, 5 if name == self.fail_bootstrap else 0, b"", b""
            )
        if action == "bootout":
            self.loaded.pop(target.rsplit("/", 1)[-1], None)
            return subprocess.CompletedProcess(command, 0, b"", b"")
        if action == "kickstart":
            name = target.rsplit("/", 1)[-1]
            path, _ = self.loaded[name]
            self.sequence += 1
            self.loaded[name] = (path, self.sequence)
            return subprocess.CompletedProcess(command, 0, b"", b"")
        raise AssertionError(f"Unexpected launchctl action {action}")

    def load(self, name, *, other_root=None):
        root = other_root or self.environment.root
        path = root / "launchagents" / f"{trial.LABELS[name]}.plist"
        self.sequence += 1
        self.loaded[trial.LABELS[name]] = (str(path), self.sequence)


class TrialEnvironmentTests(unittest.TestCase):
    def setUp(self):
        # launchctl is simulated; only the launcher sees the supported platform.
        platform = mock.patch.object(
            trial, "sys", types.SimpleNamespace(platform="darwin")
        )
        platform.start()
        self.addCleanup(platform.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        self.environment = trial.TrialEnvironment(self.directory / "state", REPOSITORY)
        self.launchctl = Launchctl(self.environment)
        self.launchctl.real_run = subprocess.run
        patch = mock.patch.object(trial.subprocess, "run", self.launchctl)
        patch.start()
        self.addCleanup(patch.stop)
        self.frontend = self.directory / "dist"
        (self.frontend / "assets").mkdir(parents=True)
        (self.frontend / "index.html").write_text(
            '<script src="/assets/app.js"></script>'
        )
        (self.frontend / "assets/app.js").write_text("window.trial = true;")
        self.provider_file = self.directory / "private.env"
        self.secret = "synthetic-provider-secret-not-for-state"
        self.provider_file.write_text(f"OPENAI_API_KEY={self.secret}\n")
        # Paths must be executable and hashed, but no Caddy installation or
        # imports of application dependencies are needed for prepare contracts.
        python = Path(os.path.abspath(sys.executable))
        digest = hashlib.sha256(python.read_bytes()).hexdigest()
        python_probe = mock.patch.object(
            trial,
            "_python_version",
            return_value={"version": "test-python", "sha256": digest, "packages": []},
        )
        caddy_probe = mock.patch.object(
            trial,
            "_caddy_version",
            return_value={"version": "2.11.7", "sha256": digest},
        )
        python_probe.start()
        caddy_probe.start()
        self.addCleanup(python_probe.stop)
        self.addCleanup(caddy_probe.stop)
        self.options = {
            "python": python,
            "env_file": self.provider_file,
            "frontend_dist": self.frontend,
            "caddy": python,
            "hostname": "chatterpal.local",
            "lan_ip": "192.168.1.23",
        }

    def prepare(self):
        return self.environment.prepare(**self.options)

    def patch_start_boundaries(self):
        for name in (
            "_assert_ports_available",
            "_wait_for_api",
            "_wait_for_agents",
            "_publish_ca",
        ):
            patch = mock.patch.object(self.environment, name)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(
            self.environment, "status", return_value={"ready": True}
        )
        patch.start()
        self.addCleanup(patch.stop)

    def test_source_is_exact_pinned_git_tree_even_from_another_checkout(self):
        # A misleading working source beside the correct object store must not
        # be copied into the trial's release.
        shadow = self.directory / "dirty-checkout"
        shadow.mkdir()
        git_dir = subprocess.run(
            ["/usr/bin/git", "-C", str(REPOSITORY), "rev-parse", "--absolute-git-dir"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        (shadow / ".git").write_text(f"gitdir: {git_dir}\n")
        dirty = shadow / "src/chatterpal/cli/run.py"
        dirty.parent.mkdir(parents=True)
        dirty.write_text("raise RuntimeError('uncommitted source must not run')\n")
        self.environment.repository = shadow
        manifest = self.prepare()
        source = (
            self.environment.root
            / f"release/{trial.RELEASE_ID}/src/chatterpal/cli/run.py"
        )
        committed = subprocess.run(
            [
                "/usr/bin/git",
                "-C",
                str(REPOSITORY),
                "show",
                f"{trial.BASE_COMMIT}:src/chatterpal/cli/run.py",
            ],
            check=True,
            capture_output=True,
        ).stdout
        self.assertEqual(source.read_bytes(), committed)
        self.assertEqual(manifest["version"]["commit"], trial.BASE_COMMIT)
        self.assertEqual(manifest["version"]["package_version"], "0.1.0")
        self.assertEqual(
            manifest["version"]["source_files"]["src/chatterpal/cli/run.py"],
            hashlib.sha256(committed).hexdigest(),
        )
        self.assertIn("uv.lock", manifest["version"]["source_files"])

    def test_prepare_never_opens_provider_file_or_persists_ambient_secrets(self):
        original_open = Path.open

        def guarded(path, *args, **kwargs):
            if path == self.provider_file:
                raise AssertionError("prepare/status opened the private provider file")
            return original_open(path, *args, **kwargs)

        with (
            mock.patch.object(Path, "open", guarded),
            mock.patch.dict(
                os.environ, {"OPENAI_API_KEY": self.secret, "OPENAI_TIMEOUT": "17"}
            ),
        ):
            self.prepare()
            status = self.environment.status()
        self.assertNotIn(self.secret, json.dumps(status))
        for path in self.environment.root.rglob("*"):
            mode = stat.S_IMODE(path.stat().st_mode)
            if path.is_dir():
                self.assertEqual(mode, 0o700, path)
            else:
                self.assertEqual(mode, 0o600, path)
                self.assertNotIn(self.secret.encode(), path.read_bytes(), path)
        self.assertEqual(stat.S_IMODE(self.environment.root.stat().st_mode), 0o700)
        self.assertEqual(status["paths"]["env_file"], str(self.provider_file))
        self.assertEqual(status["health"]["ready"], False)

    def test_prepare_is_idempotent_and_rejects_configuration_or_asset_replacement(self):
        first = self.prepare()
        manifest_path = self.environment.manifest_path
        before = manifest_path.read_bytes()
        modified_at = manifest_path.stat().st_mtime_ns
        self.launchctl.load("api")
        self.assertEqual(self.prepare(), first)
        self.assertEqual(manifest_path.stat().st_mtime_ns, modified_at)
        with self.assertRaisesRegex(trial.TrialError, "different release"):
            self.environment.prepare(**{**self.options, "hostname": "different.local"})
        (self.frontend / "index.html").write_text("new build")
        with self.assertRaisesRegex(trial.TrialError, "different release"):
            self.prepare()
        self.assertEqual(manifest_path.read_bytes(), before)

    def test_prepare_rejects_existing_release_and_live_or_foreign_job(self):
        self.launchctl.load("api", other_root=self.directory / "other")
        with self.assertRaisesRegex(trial.TrialError, "live trial"):
            self.prepare()
        self.assertFalse(self.environment.manifest_path.exists())
        self.launchctl.loaded.clear()
        (self.environment.root / "data").mkdir()
        (self.environment.root / "data/learning.sqlite3").write_bytes(b"keep")
        with self.assertRaisesRegex(trial.TrialError, "empty state"):
            self.prepare()
        self.assertEqual(
            (self.environment.root / "data/learning.sqlite3").read_bytes(), b"keep"
        )

    def test_prepared_assets_are_verified_before_any_start(self):
        self.prepare()
        asset = (
            self.environment.root / f"release/{trial.RELEASE_ID}/frontend/assets/app.js"
        )
        asset.write_text("changed after prepare")
        with self.assertRaisesRegex(trial.TrialError, "files changed"):
            self.environment.start()
        self.assertFalse(any(call[0] == "bootstrap" for call in self.launchctl.calls))

    def test_frontend_cannot_include_symlinks_or_private_files(self):
        for name in (
            ".env",
            "provider.env",
            "PROVIDER.ENV",
            "private.key",
            "learning.sqlite3",
        ):
            with self.subTest(name=name):
                path = self.frontend / name
                path.write_text("synthetic private file")
                with self.assertRaises(trial.TrialError):
                    self.prepare()
                path.unlink()
        link = self.frontend / "public.js"
        link.symlink_to(self.provider_file)
        with self.assertRaisesRegex(trial.TrialError, "symlink"):
            self.prepare()
        self.assertFalse(self.environment.manifest_path.exists())

    def test_provider_file_inside_frontend_is_rejected_before_reading(self):
        # The selected private file may have an ordinary asset extension, and an
        # outside symlink must not bypass the canonical containment check.
        selected = self.frontend / "provider.txt"
        selected.write_text(self.secret)
        alias = self.directory / "provider-link.txt"
        alias.symlink_to(selected)
        original_open = Path.open

        def guarded(path, *args, **kwargs):
            if path in (selected, alias):
                raise AssertionError("prepare read the private frontend input")
            return original_open(path, *args, **kwargs)

        for provider in (selected, alias):
            with (
                self.subTest(provider=provider),
                mock.patch.object(Path, "open", guarded),
            ):
                with self.assertRaisesRegex(trial.TrialError, "outside the frontend"):
                    self.environment.prepare(**{**self.options, "env_file": provider})
        self.assertFalse(self.environment.manifest_path.exists())

    def test_launch_contract_uses_existing_venv_and_private_trial_paths(self):
        self.prepare()
        root = self.environment.root
        api = plistlib.loads(
            (root / f"launchagents/{trial.LABELS['api']}.plist").read_bytes()
        )
        arguments = api["ProgramArguments"]
        self.assertEqual(arguments[0], str(self.options["python"]))
        self.assertEqual(
            arguments[2:],
            [
                "-m",
                "chatterpal.cli.run",
                "--mode",
                "api",
                "--env-file",
                str(self.provider_file),
                "--host",
                "127.0.0.1",
                "--port",
                "18300",
            ],
        )
        self.assertEqual(api["WorkingDirectory"], str(root / "runtime"))
        self.assertTrue(api["KeepAlive"])
        self.assertTrue(api["RunAtLoad"])
        self.assertEqual(api["Umask"], 0o077)
        env = api["EnvironmentVariables"]
        self.assertEqual(
            env["PYTHONPATH"], str(root / f"release/{trial.RELEASE_ID}/src")
        )
        self.assertEqual(env["LEARNING_DB_PATH"], str(root / "data/learning.sqlite3"))
        self.assertEqual(env["LEARNING_COOKIE_SECURE"], "true")
        self.assertEqual(env["XDG_CACHE_HOME"], str(Path.home() / ".cache"))
        self.assertEqual(env["PATH"].split(":")[0], str(self.options["python"].parent))
        self.assertIn("/opt/homebrew/bin", env["PATH"].split(":"))
        self.assertEqual(
            set(env["LEARNING_ALLOWED_ORIGINS"].split(",")),
            {
                "https://chatterpal.local:8443",
                "https://192.168.1.23:8443",
            },
        )
        self.assertNotIn("LEARNING_AUDIO_RETENTION_DAYS", env)
        self.assertNotIn("OPENAI_TIMEOUT", env)
        for key in (
            "AUDIO_TEMP_DIR",
            "CACHE_DIR",
            "MODEL_CACHE_DIR",
            "LOG_FILE",
            "TMPDIR",
        ):
            self.assertTrue(Path(env[key]).is_relative_to(root), key)
        awake = plistlib.loads(
            (root / f"launchagents/{trial.LABELS['awake']}.plist").read_bytes()
        )
        self.assertEqual(awake["ProgramArguments"], ["/usr/bin/caffeinate", "-is"])

    def test_proxy_does_not_expose_private_state_and_has_no_public_bind(self):
        self.prepare()
        config = (self.environment.root / "Caddyfile").read_text()
        self.assertIn("admin 127.0.0.1:12019", config)
        self.assertIn("auto_https disable_redirects", config)
        self.assertIn("skip_install_trust", config)
        self.assertIn("tls internal", config)
        self.assertIn("bind 192.168.1.23 127.0.0.1", config)
        self.assertNotIn("0.0.0.0", config)
        self.assertIn(
            "https://chatterpal.local:8443, https://192.168.1.23:8443", config
        )
        self.assertIn("handle /api/health", config)
        self.assertIn("rewrite * /health", config)
        self.assertIn("reverse_proxy 127.0.0.1:18300", config)
        http_site = config.split("http://:18080", 1)[1]
        self.assertIn("handle /trial-ca.crt", http_site)
        self.assertIn("rewrite * /root.crt", http_site)
        self.assertIn("respond 404", http_site)
        self.assertNotIn("reverse_proxy", http_site)
        self.assertNotIn(str(self.provider_file), config)
        for private in ("data/learning.sqlite3", "logs", "caddy-data", "runtime"):
            self.assertNotIn(str(self.environment.root / private), http_site)
        self.assertIn(f'root * "{self.environment.root}/public"', http_site)

    def test_host_and_address_inputs_reject_ports_public_addresses_and_injection(self):
        for address in (
            "127.0.0.1",
            "0.0.0.0",
            "169.254.2.3",
            "8.8.8.8",
            "::1",
            "192.168.1.2:8443",
            "224.0.0.1",
        ):
            with self.subTest(address=address), self.assertRaises(trial.TrialError):
                trial._lan_ip(address)
        for address in ("10.2.3.4", "172.16.2.3", "192.168.2.3"):
            self.assertEqual(trial._lan_ip(address), address)
        for host in (
            "*",
            "localhost",
            "example.com",
            "192.168.1.2",
            "https://trial.local",
            "trial.local:8443",
            "trial.local\n}",
            "{env.SECRET}",
        ):
            with self.subTest(host=host), self.assertRaises(trial.TrialError):
                trial._hostname(host)
        self.assertEqual(trial._hostname("ChatterPal.local."), "chatterpal.local")

    def test_failed_partial_start_cleans_new_jobs_and_preserves_existing_api(self):
        self.prepare()
        self.patch_start_boundaries()
        self.launchctl.load("api")
        original = self.launchctl.loaded[trial.LABELS["api"]]
        self.launchctl.fail_bootstrap = trial.LABELS["awake"]
        with self.assertRaisesRegex(trial.TrialError, "bootstrap"):
            self.environment.start()
        self.assertEqual(self.launchctl.loaded, {trial.LABELS["api"]: original})
        stopped = [call[-1] for call in self.launchctl.calls if call[0] == "bootout"]
        self.assertEqual(
            stopped,
            [
                f"{self.environment.domain}/{trial.LABELS['awake']}",
                f"{self.environment.domain}/{trial.LABELS['https']}",
            ],
        )

    def test_unhealthy_start_cleans_new_api_and_never_bootstraps_proxy(self):
        self.prepare()
        self.patch_start_boundaries()
        self.environment._wait_for_api.side_effect = trial.TrialError(
            "API readiness timed out"
        )
        with self.assertRaisesRegex(trial.TrialError, "readiness"):
            self.environment.start()
        self.assertEqual(self.launchctl.loaded, {})
        bootstrapped = [
            call[-1] for call in self.launchctl.calls if call[0] == "bootstrap"
        ]
        self.assertEqual(
            bootstrapped,
            [str(self.environment.root / f"launchagents/{trial.LABELS['api']}.plist")],
        )

    def test_start_is_idempotent_and_rejects_foreign_labels(self):
        self.prepare()
        self.patch_start_boundaries()
        for name in trial.LABELS:
            self.launchctl.load(name)
        self.assertTrue(self.environment.start()["ready"])
        self.assertFalse(
            any(call[0] in ("bootstrap", "kickstart") for call in self.launchctl.calls)
        )
        self.launchctl.load("https", other_root=self.directory / "other")
        with self.assertRaisesRegex(trial.TrialError, "different state"):
            self.environment.start()
        with self.assertRaisesRegex(trial.TrialError, "different state"):
            self.environment.stop()
        self.assertFalse(any(call[0] == "bootout" for call in self.launchctl.calls))

    def test_stop_and_restart_only_target_own_labels_and_preserve_data(self):
        self.prepare()
        database = self.environment.root / "data/learning.sqlite3"
        database.write_bytes(b"existing database")
        for name in trial.LABELS:
            self.launchctl.load(name)
        old_api_pid = self.launchctl.loaded[trial.LABELS["api"]][1]
        with (
            mock.patch.object(self.environment, "_wait_for_api") as ready,
            mock.patch.object(self.environment, "status", return_value={}),
        ):
            self.environment.restart_api()
        ready.assert_called_once_with(120, previous_pid=old_api_pid)
        mutations = [call for call in self.launchctl.calls if call[0] != "print"]
        self.assertEqual(
            mutations,
            [("kickstart", "-k", f"{self.environment.domain}/{trial.LABELS['api']}")],
        )
        self.environment.stop()
        self.assertEqual(self.launchctl.loaded, {})
        self.assertEqual(database.read_bytes(), b"existing database")
        self.assertTrue(self.environment.manifest_path.is_file())
        self.environment.stop()

    def test_bootout_waits_for_launchd_to_finish_unloading(self):
        self.prepare()
        self.launchctl.load("api")
        original = self.environment._job("api")
        with (
            mock.patch.object(
                self.environment,
                "_job",
                side_effect=[
                    original,
                    original,
                    {**original, "loaded": False, "pid": None},
                ],
            ) as inspect,
            mock.patch.object(trial.time, "sleep") as sleep,
        ):
            self.environment._bootout("api")
        self.assertEqual(inspect.call_count, 3)
        sleep.assert_called_once_with(0.1)

    def test_online_backup_contains_committed_wal_data_without_changing_database(self):
        self.prepare()
        database = self.environment.root / "data/learning.sqlite3"
        connection = sqlite3.connect(database)
        self.addCleanup(connection.close)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE trial_notes (text TEXT)")
        connection.execute("INSERT INTO trial_notes VALUES ('committed record')")
        connection.commit()
        connection.execute("INSERT INTO trial_notes VALUES ('uncommitted record')")
        result = self.environment.backup()
        backup = Path(result["backup"])
        self.assertEqual(backup.parent, self.environment.root / "backups")
        self.assertFalse(backup.is_relative_to(database.parent))
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        with sqlite3.connect(backup) as copied:
            self.assertEqual(
                copied.execute("SELECT text FROM trial_notes").fetchall(),
                [("committed record",)],
            )
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM trial_notes").fetchone(), (2,)
        )
        self.assertEqual(
            result["sha256"], hashlib.sha256(backup.read_bytes()).hexdigest()
        )
        self.assertFalse(any(path.suffix == ".tmp" for path in backup.parent.iterdir()))

    def test_status_filters_health_output_and_includes_version_and_pid(self):
        self.prepare()
        self.launchctl.load("api")
        response = mock.Mock(status=200)
        response.read.return_value = json.dumps(
            {
                "status": "healthy",
                "services": {
                    name: True
                    for name in (
                        "asr",
                        "tts",
                        "llm",
                        "chat",
                        "correction",
                        "ai_correction",
                    )
                },
                "runtime": {"unexpected_secret": self.secret},
            }
        ).encode()
        connection = mock.Mock()
        connection.getresponse.return_value = response
        with mock.patch.object(
            trial.http.client, "HTTPConnection", return_value=connection
        ) as http:
            result = self.environment.status()
        http.assert_called_once_with("127.0.0.1", 18300, timeout=2)
        connection.request.assert_called_once_with("GET", "/health")
        self.assertTrue(result["health"]["ready"])
        self.assertFalse(result["ready"])
        self.assertEqual(result["version"]["commit"], trial.BASE_COMMIT)
        self.assertEqual(
            result["jobs"]["api"]["pid"], self.launchctl.loaded[trial.LABELS["api"]][1]
        )
        self.assertNotIn(self.secret, json.dumps(result))
        self.assertEqual(
            result["urls"]["health"], "https://chatterpal.local:8443/api/health"
        )

    def prepare_previous_release(self):
        previous = "a253d924e7ad92b4585afe1f4d334527b8a61722"
        with (
            mock.patch.object(trial, "BASE_COMMIT", previous),
            mock.patch.object(trial, "RELEASE_ID", previous[:7]),
        ):
            manifest = self.prepare()
        database = self.environment.root / "data/learning.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE trial_notes (text TEXT)")
            connection.execute(
                "INSERT INTO trial_notes VALUES ('keep learner identity')"
            )
        self.certificate = self.environment.root / "public/root.crt"
        trial._write_private(self.certificate, b"synthetic unchanged CA")
        return manifest

    def test_upgrade_and_rollback_keep_database_certificate_and_previous_release(self):
        previous = self.prepare_previous_release()
        before = self.environment.manifest_path.read_bytes()
        database = self.environment.root / "data/learning.sqlite3"
        original_data = database.read_bytes()
        (self.frontend / "assets/app.js").write_text("window.accepted = true;")
        original_open = Path.open

        def guarded(path, *args, **kwargs):
            if path == self.provider_file:
                raise AssertionError("upgrade opened provider configuration")
            return original_open(path, *args, **kwargs)

        with mock.patch.object(Path, "open", guarded):
            result = self.environment.upgrade(frontend_dist=self.frontend)
        self.assertTrue(result["upgraded"])
        current = self.environment._manifest(verify=True)
        self.assertEqual(current["version"]["commit"], trial.BASE_COMMIT)
        self.assertEqual(
            self.environment.paths()["release"],
            str(self.environment.root / "release" / trial.RELEASE_ID),
        )
        self.assertEqual(database.read_bytes(), original_data)
        self.assertEqual(self.certificate.read_bytes(), b"synthetic unchanged CA")
        self.assertTrue((self.environment.root / "release/a253d92").is_dir())
        with sqlite3.connect(result["backup"]["backup"]) as connection:
            self.assertEqual(
                connection.execute("SELECT text FROM trial_notes").fetchone(),
                ("keep learner identity",),
            )
        api = plistlib.loads(
            (
                self.environment.root / f"launchagents/{trial.LABELS['api']}.plist"
            ).read_bytes()
        )
        self.assertIn(trial.RELEASE_ID, api["EnvironmentVariables"]["PYTHONPATH"])
        self.assertIn(
            f"release/{trial.RELEASE_ID}/frontend",
            (self.environment.root / "Caddyfile").read_text(),
        )
        self.assertEqual(
            self.environment.upgrade(frontend_dist=self.frontend)["upgraded"], False
        )
        # A rollback must preserve records added after the upgrade, not restore its backup.
        with sqlite3.connect(database) as connection:
            connection.execute("INSERT INTO trial_notes VALUES ('new record')")
        restored = self.environment.rollback(snapshot=result["rollback_snapshot"])
        self.assertEqual(restored["version"], previous["version"]["commit"])
        self.assertEqual(self.environment.manifest_path.read_bytes(), before)
        self.environment._manifest(verify=True)
        self.assertIn("a253d92", self.environment.paths()["release"])
        with sqlite3.connect(database) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM trial_notes").fetchone(), (2,)
            )
        self.assertEqual(self.certificate.read_bytes(), b"synthetic unchanged CA")
        for path in (self.environment.root / "upgrades").rglob("*"):
            self.assertEqual(
                stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600
            )
        self.patch_start_boundaries()
        self.assertTrue(self.environment.start()["ready"])

    def test_upgrade_rejects_loaded_or_foreign_agents_before_changing_state(self):
        self.prepare_previous_release()
        before = self.environment.manifest_path.read_bytes()
        for root, message in (
            (None, "Stop the trial"),
            (self.directory / "other", "different state"),
        ):
            with self.subTest(root=root):
                self.launchctl.load("api", other_root=root)
                with self.assertRaisesRegex(trial.TrialError, message):
                    self.environment.upgrade(frontend_dist=self.frontend)
                self.assertEqual(self.environment.manifest_path.read_bytes(), before)
                self.assertFalse(
                    (self.environment.root / "release" / trial.RELEASE_ID).exists()
                )
                self.launchctl.loaded.clear()

    def test_upgrade_rejects_provider_containment_before_reading_frontend(self):
        self.prepare_previous_release()
        with mock.patch.object(
            trial, "_frontend_hashes", side_effect=AssertionError("read private input")
        ):
            with self.assertRaisesRegex(trial.TrialError, "outside the frontend"):
                self.environment.upgrade(frontend_dist=self.directory)

    def test_failed_upgrade_restores_all_active_files_and_can_retry(self):
        self.prepare_previous_release()
        original = self.environment._manifest()
        before = {
            relative: (self.environment.root / relative).read_bytes()
            for relative in (*original["managed_files"], "manifest.json")
        }
        real_replace = trial.os.replace
        failed = False

        def replace(source, destination):
            nonlocal failed
            if Path(destination) == self.environment.manifest_path and not failed:
                failed = True
                raise OSError("simulated interrupted manifest switch")
            return real_replace(source, destination)

        with mock.patch.object(trial.os, "replace", side_effect=replace):
            with self.assertRaisesRegex(OSError, "interrupted"):
                self.environment.upgrade(frontend_dist=self.frontend)
        self.assertTrue(failed)
        for relative, content in before.items():
            self.assertEqual((self.environment.root / relative).read_bytes(), content)
        self.assertFalse(
            (self.environment.root / "release" / trial.RELEASE_ID).exists()
        )
        self.environment._manifest(verify=True)
        self.assertTrue(
            self.environment.upgrade(frontend_dist=self.frontend)["upgraded"]
        )

    def test_partial_switch_write_cleans_up_and_allows_upgrade_retry(self):
        self.prepare_previous_release()
        original = self.environment._manifest()
        before = {
            relative: (self.environment.root / relative).read_bytes()
            for relative in (*original["managed_files"], "manifest.json")
        }
        write = trial._write_private
        failed = False

        def partial_write(path, content):
            nonlocal failed
            if (
                path.parent.name.startswith(".switch-")
                and path.name == "api_launcher.py"
                and not failed
            ):
                failed = True
                write(path, content[:8])
                raise OSError("simulated disk exhaustion")
            write(path, content)

        # An abandoned temporary directory from an uncatchable termination must
        # not collide with a later switch or recovery.
        abandoned = self.environment.root / ".switch-abandoned"
        abandoned.mkdir(mode=0o700)
        (abandoned / "Caddyfile").write_text("partial")
        with mock.patch.object(trial, "_write_private", side_effect=partial_write):
            with self.assertRaisesRegex(OSError, "disk exhaustion"):
                self.environment.upgrade(frontend_dist=self.frontend)
        self.assertTrue(failed)
        for relative, content in before.items():
            self.assertEqual((self.environment.root / relative).read_bytes(), content)
        self.assertEqual(list(self.environment.root.glob(".switch-*")), [abandoned])
        self.environment._manifest(verify=True)
        result = self.environment.upgrade(frontend_dist=self.frontend)
        self.assertTrue(result["upgraded"])
        self.environment.rollback(snapshot=result["rollback_snapshot"])
        self.environment._manifest(verify=True)

    def test_upgrade_refuses_application_or_schema_change_and_tampered_files(self):
        self.prepare_previous_release()
        before = self.environment.manifest_path.read_bytes()
        freeze = trial._freeze_source

        def changed(*args):
            version = freeze(*args)
            version["source_files"]["src/chatterpal/cli/run.py"] = "changed"
            return version

        with mock.patch.object(trial, "_freeze_source", side_effect=changed):
            with self.assertRaisesRegex(trial.TrialError, "migration review"):
                self.environment.upgrade(frontend_dist=self.frontend)
        self.assertEqual(self.environment.manifest_path.read_bytes(), before)
        self.assertFalse(
            (self.environment.root / "release" / trial.RELEASE_ID).exists()
        )
        asset = self.environment.root / "release/a253d92/frontend/assets/app.js"
        asset.write_text("tampered")
        with self.assertRaisesRegex(trial.TrialError, "files changed"):
            self.environment.upgrade(frontend_dist=self.frontend)

    def test_port_conflict_fails_before_bootstrap(self):
        self.prepare()
        # A live loopback listener blocks the fixed API port, even if it belongs
        # to a program whose response resembles our health endpoint.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            try:
                listener.bind(("127.0.0.1", 18300))
                listener.listen()
            except OSError:
                self.skipTest("The API port is already in use by another local process")
            with self.assertRaisesRegex(trial.TrialError, "18300.*unavailable"):
                self.environment.start()
        self.assertFalse(any(call[0] == "bootstrap" for call in self.launchctl.calls))


class PlatformGuardTests(unittest.TestCase):
    def test_real_operations_still_reject_unsupported_platforms(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = trial.TrialEnvironment(Path(directory) / "state")
            with mock.patch.object(
                trial, "sys", types.SimpleNamespace(platform="linux")
            ):
                with self.assertRaisesRegex(trial.TrialError, "require macOS"):
                    with environment._lock():
                        self.fail("An unsupported platform entered a trial operation")
            self.assertFalse(environment.root.exists())


class ApiChildLauncherTests(unittest.TestCase):
    def test_timeout_precedence_and_forced_paths_survive_case_insensitive_dotenv(self):
        for supplied, expected, timeout_key in (
            (None, "60", "OPENAI_TIMEOUT"),
            ("30", "30", "OPENAI_TIMEOUT"),
            ("24", "24", "openai_timeout"),
        ):
            with self.subTest(timeout=supplied, key=timeout_key):
                module = types.ModuleType("chatterpal.cli.config")

                def dotenv(path):
                    self.assertEqual(path, Path("/private/provider.env"))
                    os.environ["learning_db_path"] = "/wrong/database.sqlite3"
                    os.environ["LEARNING_DB_PATH"] = "/also-wrong/database.sqlite3"
                    if supplied:
                        os.environ[timeout_key] = supplied

                module.load_environment = dotenv
                argv = [
                    "api_launcher.py",
                    "-m",
                    "chatterpal.cli.run",
                    "--mode",
                    "api",
                    "--env-file",
                    "/private/provider.env",
                ]
                observed = {}

                def execv(executable, arguments):
                    observed.update(os.environ)
                    self.assertEqual(arguments, [sys.executable, *argv[1:]])

                with (
                    mock.patch.dict(sys.modules, {"chatterpal.cli.config": module}),
                    mock.patch.dict(
                        os.environ,
                        {"LEARNING_DB_PATH": "/trial/data/learning.sqlite3"},
                        clear=True,
                    ),
                    mock.patch.object(sys, "argv", argv),
                    mock.patch.object(os, "execv", execv),
                ):
                    exec(compile(trial.API_LAUNCHER, "api_launcher.py", "exec"), {})
                self.assertEqual(
                    observed["LEARNING_DB_PATH"], "/trial/data/learning.sqlite3"
                )
                self.assertEqual(
                    observed["learning_db_path"], "/trial/data/learning.sqlite3"
                )
                self.assertEqual(
                    {
                        value
                        for key, value in observed.items()
                        if key.upper() == "OPENAI_TIMEOUT"
                    },
                    {expected},
                )

    def test_env_parsing_errors_are_sanitized_at_cli_boundary(self):
        with (
            mock.patch.object(
                trial.TrialEnvironment,
                "status",
                side_effect=ValueError("synthetic-secret"),
            ),
            mock.patch.object(sys, "stderr") as output,
        ):
            self.assertEqual(
                trial.main(["status", "--state-dir", "/tmp/nonexistent-trial-state"]), 1
            )
        message = "".join(call.args[0] for call in output.write.call_args_list)
        self.assertNotIn("synthetic-secret", message)
        self.assertIn("Trial operation failed", message)

    def test_state_option_is_accepted_before_or_after_subcommand(self):
        parser = trial.create_parser()
        for arguments in (
            ["--state-dir", "/tmp/trial", "status"],
            ["status", "--state-dir", "/tmp/trial"],
        ):
            self.assertEqual(parser.parse_args(arguments).state_dir, Path("/tmp/trial"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
