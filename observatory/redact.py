"""Write-time redaction. CONTRACT.md §5.

The rule this module exists to enforce: a secret's *existence* may be recorded, its
*value* never is — and the scrub happens before anything reaches disk. Retroactive
redaction is not a substitute, because by the time you run it the value has already been
written, replicated, and backed up.

Two detectors, because either alone leaks:

* **By key name.** `{"authorization": "..."}` is a secret whatever the value looks like.
* **By value shape.** A JWT pasted into a free-text note is a secret even though the key
  is `"note"`. Key-name matching alone misses every secret that arrives inside prose.

Plus registered literals: when a caller already knows a specific string is a credential,
it says so, and that string is scrubbed wherever it appears at any depth.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

REDACTED = "[redacted]"

#: Key names whose value is a credential regardless of its shape.
_SECRET_KEY = re.compile(
    r"(authorization|api[-_ ]?key|secret|password|passwd|token|cookie|"
    r"private[-_ ]?key|credential|client[-_ ]?secret|access[-_ ]?key)",
    re.I,
)

#: Value shapes that are credentials regardless of the key they arrived under.
_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # JWT: three dot-separated base64url segments starting with the usual header.
    re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}"),
    # "Bearer <something long>"
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    # Common vendor prefixes for issued keys.
    re.compile(r"\b(?:sk|pk|rk|ghp|gho|ghs|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{12,}"),
    # AWS access key id.
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)


class Redactor:
    """Scrubs a payload before it is persisted.

    ``literals`` are exact strings the caller already knows are secret — a token read
    from a file, for instance. They are matched anywhere at any depth, including inside
    a longer string, which is what catches a credential interpolated into a URL.
    """

    def __init__(self, literals: Iterable[str] = ()) -> None:
        # Short strings would scrub half the corpus; a real credential is not 4 chars.
        self._literals = tuple(sorted(
            {s for s in literals if isinstance(s, str) and len(s.strip()) >= 8},
            key=len,
            reverse=True,  # longest first, so a prefix cannot mask a longer match
        ))

    def add_literal(self, value: str) -> None:
        if isinstance(value, str) and len(value.strip()) >= 8:
            self._literals = tuple(sorted(
                set(self._literals) | {value}, key=len, reverse=True
            ))

    def scrub(self, value: Any, *, key_hint: str | None = None) -> Any:
        """Return a copy with every detected credential replaced by ``REDACTED``."""
        if isinstance(value, dict):
            return {k: self.scrub(v, key_hint=str(k)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            scrubbed = [self.scrub(v, key_hint=key_hint) for v in value]
            return type(value)(scrubbed) if isinstance(value, tuple) else scrubbed
        if isinstance(value, str):
            return self._scrub_str(value, key_hint)
        return value

    def _scrub_str(self, text: str, key_hint: str | None) -> str:
        if key_hint and _SECRET_KEY.search(key_hint):
            # The key says it is a credential. Keep the fact, drop the value entirely —
            # a partial reveal of a secret is still a reveal.
            return REDACTED if text else text
        out = text
        for literal in self._literals:
            if literal in out:
                out = out.replace(literal, REDACTED)
        for pattern in _VALUE_PATTERNS:
            out = pattern.sub(REDACTED, out)
        return out


#: A module-level default, so the common case needs no wiring.
default_redactor = Redactor()


def redact(value: Any, *, redactor: Redactor | None = None) -> Any:
    return (redactor or default_redactor).scrub(value)
