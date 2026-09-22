"""Tests for observatory.redact — the write-time credential scrubber.

No dedicated test module existed for this before; coverage was incidental
via test_secure_ledger.py's use of it. Added after a real bug was found and
fixed: numeric values under a key matching the secret-key pattern (e.g.
"tokens_in", a token *count*, not an auth token) were being replaced with
REDACTED even though a number can never itself be a credential value.
"""
from __future__ import annotations

import unittest

from observatory import REDACTED, Redactor, redact


class RedactByKeyNameTests(unittest.TestCase):
    def test_string_value_under_secret_key_is_redacted(self) -> None:
        self.assertEqual(redact({"api_token": "sk-abc123"}), {"api_token": REDACTED})
        self.assertEqual(redact({"password": "hunter2"}), {"password": REDACTED})
        self.assertEqual(redact({"authorization": "Bearer xyz"}), {"authorization": REDACTED})

    def test_numeric_value_under_secret_key_passes_through(self) -> None:
        """Regression test: a key matching /token/ etc. does not make a
        NUMBER under it a credential. Real-world case: usage["input_tokens"]
        = 12000 (an LLM token count) must survive, not become REDACTED."""
        self.assertEqual(redact({"tokens_in": 12000}), {"tokens_in": 12000})
        self.assertEqual(redact({"input_tokens": 12000}), {"input_tokens": 12000})
        self.assertEqual(redact({"api_token_count": 42}), {"api_token_count": 42})
        self.assertEqual(redact({"secret_count": 3.5}), {"secret_count": 3.5})

    def test_boolean_value_under_secret_key_passes_through(self) -> None:
        self.assertEqual(redact({"has_token": True}), {"has_token": True})
        self.assertEqual(redact({"has_token": False}), {"has_token": False})

    def test_none_and_empty_string_under_secret_key_pass_through(self) -> None:
        self.assertEqual(redact({"api_key": None}), {"api_key": None})
        self.assertEqual(redact({"api_key": ""}), {"api_key": ""})

    def test_nested_dict_under_secret_key_is_still_fully_redacted(self) -> None:
        """A dict or list under a secret-shaped key is not a plain scalar, so
        it is not exempted -- only definitively-safe scalar types are."""
        self.assertEqual(redact({"credential": {"value": "x"}}), {"credential": REDACTED})
        self.assertEqual(redact({"secret": ["a", "b"]}), {"secret": REDACTED})

    def test_nested_numeric_usage_field_survives_at_any_depth(self) -> None:
        """The real-world shape this bug affected: token counts nested
        inside a 'usage' dict, several levels deep."""
        payload = {
            "kind": "hermes.model.request.finish",
            "data": {
                "usage": {
                    "input_tokens": 12000,
                    "output_tokens": 3000,
                    "total_tokens": 15000,
                }
            },
        }
        result = redact(payload)
        self.assertEqual(
            result["data"]["usage"],
            {"input_tokens": 12000, "output_tokens": 3000, "total_tokens": 15000},
        )

    def test_value_shape_detectors_still_catch_credentials_in_prose(self) -> None:
        """Key-name exemption for numbers must not weaken the independent
        value-shape detectors (JWTs, bearer tokens, vendor key prefixes)."""
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        self.assertEqual(redact({"note": jwt}), {"note": REDACTED})

    def test_custom_redactor_literal_still_scrubbed_regardless_of_key(self) -> None:
        redactor = Redactor(literals=["my-secret-value-123"])
        self.assertEqual(
            redactor.scrub({"message": "contains my-secret-value-123 inline"}),
            {"message": f"contains {REDACTED} inline"},
        )


if __name__ == "__main__":
    unittest.main()
