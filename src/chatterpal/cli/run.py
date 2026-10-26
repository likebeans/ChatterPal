"""Launch ChatterPal from an installed wheel or a source checkout."""

import argparse
import importlib.util
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from .config import runtime_settings


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run ChatterPal's web UI, API, terminal chat, tests, or demos.",
        epilog=(
            "Install chatterpal[gradio] for the full web UI, chatterpal[api,cloud] "
            "for a cloud API, and chatterpal[local-whisper] for local ASR. "
            "Environment variables alone are supported; no .env file is required."
        ),
    )
    parser.add_argument(
        "--mode", choices=("web", "api", "chat", "test", "demo"), default="web"
    )
    parser.add_argument("--host", help="Server bind address (web or API)")
    parser.add_argument("--port", type=_port, help="Server port (web or API)")
    parser.add_argument(
        "--share",
        action="store_true",
        default=None,
        help="Enable Gradio public sharing",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Optional dotenv file; process environment takes precedence",
    )
    parser.add_argument(
        "--test-type", choices=("all", "unit", "integration", "config"), default="all"
    )
    parser.add_argument(
        "--tests-path",
        type=Path,
        help="Checkout's tests directory (test mode; default: ./tests)",
    )
    parser.add_argument(
        "--demo-type", choices=("basic", "asr", "tts", "assessment"), default="basic"
    )
    parser.add_argument(
        "--audio-file", type=Path, help="Input audio for ASR/assessment demo"
    )
    parser.add_argument("--text", help="Text for TTS or assessment demo")
    parser.add_argument("--output", type=Path, help="Output audio path for TTS demo")
    return parser


def _port(value: str) -> int:
    port = int(value)
    if not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1024 and 65535")
    return port


def _run_tests(args: argparse.Namespace) -> int:
    """Run an explicitly available checkout suite using this interpreter."""
    root = (args.tests_path or Path.cwd() / "tests").expanduser().resolve()
    if not root.is_dir():
        raise ValueError(
            f"Tests directory not found: {root}. Tests are distributed with the "
            "source checkout; pass --tests-path /path/to/checkout/tests."
        )
    if importlib.util.find_spec("pytest") is None:
        raise ImportError("Test mode requires chatterpal[dev] and a source checkout")
    target = root / "test_config.py" if args.test_type == "config" else root
    command = [sys.executable, "-m", "pytest", str(target), "-v"]
    if args.test_type in {"unit", "integration"}:
        command += [
            "-k",
            "not integration" if args.test_type == "unit" else "integration",
        ]
    return subprocess.run(command, cwd=root.parent, check=False).returncode


def _run_chat(settings) -> int:
    from chatterpal.config.loader import create_llm
    from chatterpal.services.chat import ChatService

    config = {
        "max_history_length": settings.max_history_length,
        "session_timeout": settings.session_timeout,
        "max_sessions": settings.max_sessions,
    }
    if settings.system_prompt:
        config["default_system_prompt"] = settings.system_prompt
    service = ChatService(llm=create_llm(settings), config=config)
    session_id = service.create_session()
    print("Chat started. Type 'quit' or 'exit' to leave.")
    try:
        while True:
            try:
                text = input("You: ").strip()
            except EOFError:
                break
            if text.lower() in {"quit", "exit", "退出"}:
                break
            if text:
                response, session_id = service.chat_with_text(text, session_id)
                print(f"AI: {response}")
    finally:
        service.delete_session(session_id)
    return 0


def _run_demo(args: argparse.Namespace, settings) -> int:
    if args.demo_type == "basic":
        print(
            json.dumps(
                {
                    "asr_provider": settings.asr_provider,
                    "tts_provider": settings.tts_provider,
                    "llm_provider": settings.llm_provider,
                    "audio_sample_rate": settings.audio_sample_rate,
                    "whisper_model": settings.whisper_model,
                },
                indent=2,
            )
        )
        return 0
    if args.demo_type == "asr":
        from chatterpal.config.loader import create_asr

        print(create_asr(settings).recognize_file(str(args.audio_file)))
    elif args.demo_type == "tts":
        from chatterpal.config.loader import create_tts

        if not create_tts(settings).synthesize_to_file(args.text, str(args.output)):
            raise RuntimeError("Speech synthesis failed")
        print(f"Audio written to {args.output}")
    elif args.demo_type == "assessment":
        from chatterpal.core.assessment.corrector import PronunciationCorrector

        result = PronunciationCorrector().assess(str(args.audio_file), args.text)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


def _validate_demo(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.demo_type in {"asr", "assessment"}:
        if args.audio_file is None or not args.audio_file.is_file():
            parser.error("ASR and assessment demos require an existing --audio-file")
    if args.demo_type in {"tts", "assessment"} and not args.text:
        parser.error("TTS and assessment demos require --text")
    if args.demo_type == "tts" and args.output is None:
        parser.error("The TTS demo requires --output")


def main(argv: Sequence[str] | None = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)
    if args.mode == "demo":
        _validate_demo(parser, args)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    try:
        if args.mode == "test":
            return _run_tests(args)
        settings = runtime_settings(args.env_file)
        if args.mode == "api":
            from chatterpal.web.api_server import create_api_server

            create_api_server(settings).run(
                host=args.host or settings.api_host,
                port=args.port or settings.api_port,
                debug=args.debug,
            )
        elif args.mode == "web":
            from chatterpal.web.app import create_app

            create_app(settings).launch(
                server_name=args.host or settings.gradio_server_name,
                server_port=args.port or settings.gradio_port,
                share=settings.gradio_share if args.share is None else args.share,
                show_error=args.debug,
            )
        elif args.mode == "chat":
            return _run_chat(settings)
        elif args.mode == "demo":
            return _run_demo(args, settings)
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        if args.debug:
            logging.exception("ChatterPal failed")
        else:
            print(f"ChatterPal failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
