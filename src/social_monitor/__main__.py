"""Entry point for python -m social_monitor."""

import sys


def main():
    from social_monitor.app import run_app

    sys.exit(run_app())


if __name__ == "__main__":
    main()
