"""
Extract translatable strings and compile .mo catalogues without GNU gettext.

Django's `makemessages`/`compilemessages` shell out to xgettext and msgfmt,
which are not present on every developer or CI machine (notably Windows
without an extra install). This script does the same job in pure Python:

  * `extract` walks the source, finds gettext calls via the AST for .py files
    and a regex for templates, and writes a .po catalogue per locale, merging
    with any existing translations so human work is never lost.
  * `compile` writes the binary .mo files Django actually loads at runtime.

The .mo format is documented in the GNU gettext manual; the implementation
below produces the standard little-endian hash-free variant, which every
gettext runtime — including Python's `gettext` module — reads.

Usage:
    python scripts/extract_messages.py extract
    python scripts/extract_messages.py compile
"""

from __future__ import annotations

import ast
import re
import struct
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
LOCALE_DIR = BASE_DIR / "locale"
LOCALES = ["fr", "en"]

# Directories that contain no translatable application strings.
SKIP_DIRS = {
    ".venv", "node_modules", "__pycache__", "migrations", ".git",
    "staticfiles", "media", "scripts",
}

# Callables that mark a string for translation.
GETTEXT_NAMES = {
    "gettext", "gettext_lazy", "ugettext", "ugettext_lazy",
    "_", "gettext_noop", "pgettext", "npgettext",
}
NGETTEXT_NAMES = {"ngettext", "ngettext_lazy", "ungettext", "ungettext_lazy"}

# {% trans "…" %} and {% blocktrans %}…{% endblocktrans %} in templates.
TEMPLATE_TRANS = re.compile(r"{%\s*trans(?:late)?\s+([\"'])(.*?)\1", re.DOTALL)
TEMPLATE_BLOCKTRANS = re.compile(
    r"{%\s*blocktrans(?:late)?[^%]*%}(.*?){%\s*endblocktrans(?:late)?\s*%}",
    re.DOTALL,
)


class Message:
    """One translatable string and where it was found."""

    __slots__ = ("msgid", "msgid_plural", "locations")

    def __init__(self, msgid: str, msgid_plural: str | None = None):
        self.msgid = msgid
        self.msgid_plural = msgid_plural
        self.locations: list[tuple[str, int]] = []


def _iter_source_files() -> list[Path]:
    files: list[Path] = []
    for path in BASE_DIR.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix in {".py", ".html", ".txt"}:
            files.append(path)
    return sorted(files)


def _extract_from_python(path: Path, messages: OrderedDict[str, Message]) -> None:
    """
    Pull gettext calls out of a Python file using the AST.

    AST rather than regex: a regex would match `_("…")` inside a comment or a
    docstring, and would miss calls split across lines. Only literal string
    arguments are extractable — a call like `_(variable)` cannot be resolved
    statically and is skipped, which is also what xgettext does.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return

    relative = path.relative_to(BASE_DIR).as_posix()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        func = node.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else None
        )
        if name is None or not node.args:
            continue

        if name in GETTEXT_NAMES:
            first = node.args[0]
            # pgettext takes (context, message); the message is the second arg.
            if name.startswith("p") and len(node.args) > 1:
                first = node.args[1]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                text = first.value
                if not text.strip():
                    continue
                message = messages.setdefault(text, Message(text))
                message.locations.append((relative, node.lineno))

        elif name in NGETTEXT_NAMES and len(node.args) >= 2:
            singular, plural = node.args[0], node.args[1]
            if (
                isinstance(singular, ast.Constant)
                and isinstance(singular.value, str)
                and isinstance(plural, ast.Constant)
                and isinstance(plural.value, str)
            ):
                text = singular.value
                message = messages.setdefault(
                    text, Message(text, plural.value)
                )
                message.msgid_plural = plural.value
                message.locations.append((relative, node.lineno))


def _extract_from_template(path: Path, messages: OrderedDict[str, Message]) -> None:
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return

    relative = path.relative_to(BASE_DIR).as_posix()

    for match in TEMPLATE_TRANS.finditer(source):
        text = match.group(2)
        if not text.strip():
            continue
        line = source.count("\n", 0, match.start()) + 1
        message = messages.setdefault(text, Message(text))
        message.locations.append((relative, line))

    for match in TEMPLATE_BLOCKTRANS.finditer(source):
        text = match.group(1).strip()
        if not text.strip():
            continue
        line = source.count("\n", 0, match.start()) + 1
        message = messages.setdefault(text, Message(text))
        message.locations.append((relative, line))


def extract_messages() -> OrderedDict[str, Message]:
    messages: OrderedDict[str, Message] = OrderedDict()
    for path in _iter_source_files():
        if path.suffix == ".py":
            _extract_from_python(path, messages)
        else:
            _extract_from_template(path, messages)
    return messages


# ---------------------------------------------------------------------------
# .po reading and writing
# ---------------------------------------------------------------------------


def _po_escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )


def _po_unescape(text: str) -> str:
    return (
        text.replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )


def _format_po_string(prefix: str, text: str) -> str:
    """Multi-line strings are emitted in the standard continuation form."""
    if "\n" not in text:
        return f'{prefix} "{_po_escape(text)}"\n'

    lines = [f'{prefix} ""\n']
    parts = text.split("\n")
    for index, part in enumerate(parts):
        suffix = "\\n" if index < len(parts) - 1 else ""
        if part or suffix:
            lines.append(f'"{_po_escape(part)}{suffix}"\n')
    return "".join(lines)


def read_po(path: Path) -> dict[str, str]:
    """Read existing translations so re-extraction never discards human work."""
    if not path.exists():
        return {}

    catalogue: dict[str, str] = {}
    msgid: list[str] = []
    msgstr: list[str] = []
    state: str | None = None

    def flush() -> None:
        if msgid or msgstr:
            key = _po_unescape("".join(msgid))
            value = _po_unescape("".join(msgstr))
            if key and value:
                catalogue[key] = value

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()

        if line.startswith("#") or not line:
            if not line:
                flush()
                msgid, msgstr, state = [], [], None
            continue

        if line.startswith("msgid_plural"):
            state = "skip"
            continue
        if line.startswith("msgid "):
            flush()
            msgid, msgstr = [line[6:].strip().strip('"')], []
            state = "msgid"
            continue
        if line.startswith("msgstr["):
            state = "skip"
            continue
        if line.startswith("msgstr "):
            msgstr = [line[7:].strip().strip('"')]
            state = "msgstr"
            continue
        if line.startswith('"'):
            fragment = line.strip('"')
            if state == "msgid":
                msgid.append(fragment)
            elif state == "msgstr":
                msgstr.append(fragment)

    flush()
    return catalogue


def _load_seed(locale: str) -> dict[str, str]:
    """
    Pre-supplied translations for a locale, if any.

    Kept in `scripts/translations_<locale>.py` so the translations live in a
    reviewable, diffable Python file rather than only inside a generated .po.
    """
    if locale == "fr":
        try:
            from translations_fr import TRANSLATIONS

            return TRANSLATIONS
        except ImportError:
            try:
                from scripts.translations_fr import TRANSLATIONS

                return TRANSLATIONS
            except ImportError:
                return {}
    return {}


def write_po(locale: str, messages: OrderedDict[str, Message]) -> Path:
    directory = LOCALE_DIR / locale / "LC_MESSAGES"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "django.po"

    existing = read_po(path)
    seed = _load_seed(locale)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M%z")

    lines = [
        "# Translation catalogue for the wholesale pharmacy management system.\n",
        "# Generated by scripts/extract_messages.py — edit msgstr values only.\n",
        "#\n",
        'msgid ""\n',
        'msgstr ""\n',
        '"Project-Id-Version: pharmagros 1.0\\n"\n',
        f'"POT-Creation-Date: {timestamp}\\n"\n',
        f'"PO-Revision-Date: {timestamp}\\n"\n',
        f'"Language: {locale}\\n"\n',
        '"MIME-Version: 1.0\\n"\n',
        '"Content-Type: text/plain; charset=UTF-8\\n"\n',
        '"Content-Transfer-Encoding: 8bit\\n"\n',
        '"Plural-Forms: nplurals=2; plural=(n > 1);\\n"\n'
        if locale == "fr"
        else '"Plural-Forms: nplurals=2; plural=(n != 1);\\n"\n',
        "\n",
    ]

    for message in messages.values():
        for filename, lineno in message.locations[:6]:
            lines.append(f"#: {filename}:{lineno}\n")

        lines.append(_format_po_string("msgid", message.msgid))

        if message.msgid_plural:
            lines.append(_format_po_string("msgid_plural", message.msgid_plural))
            translation = existing.get(message.msgid, "")
            lines.append(f'msgstr[0] "{_po_escape(translation)}"\n')
            lines.append(f'msgstr[1] "{_po_escape(translation)}"\n')
        else:
            # The source strings in this codebase are written in English
            # (`_("View medicines")`), so English is the msgid language and
            # needs no catalogue entry — gettext falls back to the msgid.
            # French is the one requiring real translation, despite being the
            # application's default display language.
            #
            # Precedence: an existing .po msgstr (a translator's manual edit)
            # always wins over the seed dictionary, so re-running extraction
            # never overwrites human work.
            translation = existing.get(message.msgid) or seed.get(message.msgid, "")
            lines.append(_format_po_string("msgstr", translation))

        lines.append("\n")

    path.write_text("".join(lines), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# .mo compilation
# ---------------------------------------------------------------------------


def compile_mo(po_path: Path) -> tuple[Path, int]:
    """
    Write the binary catalogue Django loads at runtime.

    Format: magic number, revision, two parallel string tables (originals and
    translations), each an array of (length, offset) pairs, then the string
    data. Entries must be sorted by msgid — gettext binary-searches the table.
    """
    catalogue = read_po(po_path)

    # An empty msgstr means untranslated; gettext then falls back to the
    # msgid, so those entries are simply omitted.
    entries = sorted(
        (key, value) for key, value in catalogue.items() if key and value
    )

    # The empty msgid carries the catalogue metadata header.
    metadata = (
        "Project-Id-Version: pharmagros 1.0\n"
        "MIME-Version: 1.0\n"
        "Content-Type: text/plain; charset=UTF-8\n"
        "Content-Transfer-Encoding: 8bit\n"
    )
    entries.insert(0, ("", metadata))

    keys = b"".join(key.encode("utf-8") + b"\x00" for key, _ in entries)
    values = b"".join(value.encode("utf-8") + b"\x00" for _, value in entries)

    count = len(entries)
    key_table_offset = 28
    value_table_offset = key_table_offset + count * 8
    keys_offset = value_table_offset + count * 8
    values_offset = keys_offset + len(keys)

    key_table = b""
    offset = keys_offset
    for key, _ in entries:
        encoded = key.encode("utf-8")
        key_table += struct.pack("<II", len(encoded), offset)
        offset += len(encoded) + 1

    value_table = b""
    offset = values_offset
    for _, value in entries:
        encoded = value.encode("utf-8")
        value_table += struct.pack("<II", len(encoded), offset)
        offset += len(encoded) + 1

    header = struct.pack(
        "<IIIIIII",
        0x950412DE,          # magic, little-endian
        0,                   # revision
        count,
        key_table_offset,
        value_table_offset,
        0,                   # hash table size (unused)
        0,                   # hash table offset
    )

    mo_path = po_path.with_suffix(".mo")
    mo_path.write_bytes(header + key_table + value_table + keys + values)
    return mo_path, count


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "extract"

    if command == "extract":
        messages = extract_messages()
        print(f"Extracted {len(messages)} unique translatable strings.")
        for locale in LOCALES:
            path = write_po(locale, messages)
            catalogue = read_po(path)
            translated = sum(1 for value in catalogue.values() if value)
            print(
                f"  {locale}: {path.relative_to(BASE_DIR)} "
                f"({translated}/{len(messages)} translated)"
            )
        return 0

    if command == "compile":
        total = 0
        for locale in LOCALES:
            po_path = LOCALE_DIR / locale / "LC_MESSAGES" / "django.po"
            if not po_path.exists():
                print(f"  {locale}: no catalogue, skipped")
                continue
            mo_path, count = compile_mo(po_path)
            total += count
            print(f"  {locale}: {mo_path.relative_to(BASE_DIR)} ({count} entries)")
        print(f"Compiled {total} entries.")
        return 0

    print(f"Unknown command: {command}. Use 'extract' or 'compile'.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
