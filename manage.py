#!/usr/bin/env python
import os
import sys


def main() -> None:
    # Local SQLite settings by default, so a clean checkout runs with no setup.
    # Override for other environments: DJANGO_SETTINGS_MODULE=config.settings.prod
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "Django is not importable. Activate your virtualenv and run "
            "`pip install -r requirements.txt`."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
