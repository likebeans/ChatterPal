"""Deployment regressions that do not call speech or language providers.

Run directly with ``python -m unittest discover -s tests -p test_docker_deployment.py``.
Compose checks require the Docker CLI, but do not need a running daemon.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestDockerDeployment(unittest.TestCase):
    def test_deployment_files_exist(self):
        for relative_path in (
            "Dockerfile",
            "docker-compose.yml",
            ".dockerignore",
            "frontend/Dockerfile",
            "frontend/nginx.conf",
        ):
            with self.subTest(path=relative_path):
                self.assertTrue((PROJECT_ROOT / relative_path).is_file())

    def test_frontend_requests_use_same_origin(self):
        # An absolute localhost URL targets the visitor's computer after deploy.
        requests = []
        for source in (PROJECT_ROOT / "frontend/src").rglob("*.tsx"):
            requests.extend(
                re.findall(r"axios\.(?:get|post)\(['\"`]([^'\"`]+)", source.read_text())
            )
        self.assertTrue(requests, "No frontend API requests were checked")
        for url in requests:
            with self.subTest(url=url):
                self.assertTrue(url.startswith("/api/"), url)

    @unittest.skipUnless(shutil.which("docker"), "Docker CLI is not installed")
    def test_compose_resolves_with_external_env_file(self):
        available = subprocess.run(
            ["docker", "compose", "version"], capture_output=True, text=True
        )
        if available.returncode:
            self.skipTest("Docker Compose is not installed")

        with tempfile.TemporaryDirectory(prefix="chatterpal-deployment-") as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "DASHSCOPE_API_KEY=offline-deployment-test-key\n"
                "GRADIO_PORT=9999\nGRADIO_SHARE=true\n"
                "GRADIO_SERVER_NAME=127.0.0.1\n"
            )
            environment = dict(os.environ, CHATTERPAL_ENV_FILE=str(env_file))
            command = [
                "docker",
                "compose",
                "--env-file",
                str(env_file),
                "-f",
                str(PROJECT_ROOT / "docker-compose.yml"),
            ]
            default = subprocess.run(
                command + ["config", "--services"],
                env=environment,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(set(default.stdout.splitlines()), {"backend", "frontend"})

            resolved = subprocess.run(
                command + ["--profile", "gradio", "config", "--format", "json"],
                env=environment,
                capture_output=True,
                text=True,
                check=True,
            )
            services = json.loads(resolved.stdout)["services"]

        for name, host_port, container_port in (
            ("backend", "8010", 8010),
            ("frontend", "3010", 80),
            ("gradio", "7860", 7860),
        ):
            with self.subTest(service=name):
                service = services[name]
                self.assertTrue(
                    any(
                        str(port["published"]) == host_port
                        and port["target"] == container_port
                        for port in service["ports"]
                    )
                )
                dockerfile = PROJECT_ROOT / service["build"]["dockerfile"]
                self.assertTrue(dockerfile.is_file())
                self.assertTrue(service["healthcheck"]["test"])

        self.assertEqual(
            services["frontend"]["depends_on"]["backend"]["condition"],
            "service_healthy",
        )
        self.assertEqual(
            services["backend"]["environment"]["XDG_CACHE_HOME"], "/app/.cache"
        )
        self.assertEqual(
            services["backend"]["environment"]["TMPDIR"], "/app/temp_audio"
        )
        mounts = {mount["target"] for mount in services["backend"]["volumes"]}
        self.assertTrue({"/app/.cache", "/app/data", "/app/temp_audio"} <= mounts)
        self.assertEqual(services["gradio"]["profiles"], ["gradio"])
        self.assertEqual(services["gradio"]["environment"]["GRADIO_PORT"], "7860")
        self.assertEqual(services["gradio"]["environment"]["GRADIO_SHARE"], "false")
        self.assertEqual(
            services["gradio"]["environment"]["GRADIO_SERVER_NAME"], "0.0.0.0"
        )
        self.assertIn(
            "http://127.0.0.1:7860/",
            " ".join(services["gradio"]["healthcheck"]["test"]),
        )


if __name__ == "__main__":
    unittest.main()
