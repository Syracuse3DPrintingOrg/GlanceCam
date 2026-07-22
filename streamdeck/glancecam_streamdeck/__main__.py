"""Entry point: ``python -m glancecam_streamdeck``.

Loads config, sets up logging, and runs the controller until interrupted. The
Stream Deck device library is imported lazily inside the controller, so
``--help`` and ``--version`` work on a machine with no deck and no ``StreamDeck``
wheel installed.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from . import __version__
from .config import load, resolved_config_path


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="glancecam-streamdeck",
        description="Run a Stream Deck as a GlanceCam camera wall.",
    )
    p.add_argument("--config", help="Path to a TOML config file.")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Log every camera selection and poll.")
    p.add_argument("--version", action="version", version=__version__)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    config = load(args.config)
    config_path = str(resolved_config_path(args.config))
    # Import the controller only after args are parsed, so --help/--version do
    # not drag in the device library.
    from .controller import main_async
    try:
        return asyncio.run(main_async(config, config_path=config_path))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
