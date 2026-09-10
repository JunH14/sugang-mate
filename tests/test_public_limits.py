"""Public-demo quotas: no model SDK or network access is needed."""

from __future__ import annotations

import hashlib
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

from sugang_mate.public_limits import MeteredModels, PublicLimitError, PublicLimits


def client(label: str = "visitor") -> str:
    return hashlib.sha256(label.encode()).hexdigest()


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class PublicLimitsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()

    def test_default_chat_limit_is_per_client_and_window_is_sliding(self):
        limits = PublicLimits(clock=self.clock)
        for _ in range(3):
            limits.check_chat(client())
        self.clock.now = 30
        for _ in range(3):
            limits.check_chat(client())
        with self.assertRaises(PublicLimitError):
            limits.check_chat(client())
        limits.check_chat(client("another"))
        self.clock.now = 60
        for _ in range(3):
            limits.check_chat(client())
        with self.assertRaises(PublicLimitError):
            limits.check_chat(client())

    def test_default_api_limit_has_an_hour_window_and_independent_chat_budget(self):
        limits = PublicLimits(clock=self.clock)
        for _ in range(60):
            limits.reserve_api()
        with self.assertRaises(PublicLimitError):
            limits.reserve_api()
        limits.check_chat(client())
        self.clock.now = 3599.9
        with self.assertRaises(PublicLimitError):
            limits.reserve_api()
        self.clock.now = 3600
        limits.reserve_api()

    def test_capacity_never_evicts_active_quotas_and_expired_slots_are_reused(self):
        limits = PublicLimits(chat_limit=1, max_clients=2, client_ttl_seconds=60, clock=self.clock)
        limits.check_chat(client("first"))
        limits.check_chat(client("second"))
        for number in range(50):
            with self.assertRaises(PublicLimitError):
                limits.check_chat(client(f"new-{number}"))
        self.assertEqual(limits.tracked_clients, 2)
        with self.assertRaises(PublicLimitError):
            limits.check_chat(client("first"))
        self.clock.now = 60
        self.assertEqual(limits.tracked_clients, 0)
        limits.check_chat(client("third"))
        self.assertEqual(limits.tracked_clients, 1)

    def test_refused_requests_do_not_keep_an_expired_slot_alive(self):
        limits = PublicLimits(chat_limit=1, max_clients=1, client_ttl_seconds=60, clock=self.clock)
        limits.check_chat(client("first"))
        self.clock.now = 59
        with self.assertRaises(PublicLimitError):
            limits.check_chat(client("first"))
        self.clock.now = 60
        limits.check_chat(client("second"))
        self.assertEqual(limits.tracked_clients, 1)

    def test_zero_limits_block_instead_of_disabling_protection(self):
        for settings in ({"chat_limit": 0}, {"max_clients": 0}):
            limits = PublicLimits(**settings, clock=self.clock)
            with self.assertRaises(PublicLimitError):
                limits.check_chat(client())
            self.assertEqual(limits.tracked_clients, 0)
        with self.assertRaises(PublicLimitError):
            PublicLimits(api_limit=0, clock=self.clock).reserve_api()

    def test_invalid_settings_fail_early(self):
        for settings in (
            {"chat_limit": -1}, {"api_limit": 1.5}, {"max_clients": True},
            {"chat_window_seconds": 0}, {"api_window_seconds": float("inf")},
            {"client_ttl_seconds": float("nan")}, {"client_ttl_seconds": 59},
        ):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                PublicLimits(**settings)

    def test_raw_addresses_are_not_accepted_and_digest_case_cannot_bypass_quota(self):
        limits = PublicLimits(chat_limit=1, clock=self.clock)
        for raw in ("203.0.113.7", "", None, "x" * 64):
            with self.assertRaises(ValueError):
                limits.check_chat(raw)
        self.assertEqual(limits.tracked_clients, 0)
        limits.check_chat(client())
        with self.assertRaises(PublicLimitError):
            limits.check_chat(client().upper())

    def test_simultaneous_requests_cannot_over_reserve_chat_or_api(self):
        for kind in ("chat", "api"):
            with self.subTest(kind=kind):
                limits = PublicLimits(chat_limit=5, api_limit=5, clock=self.clock)
                barrier = threading.Barrier(20)

                def reserve(_):
                    barrier.wait(timeout=5)
                    try:
                        if kind == "chat":
                            limits.check_chat(client())
                        else:
                            limits.reserve_api()
                        return True
                    except PublicLimitError:
                        return False

                with ThreadPoolExecutor(max_workers=20) as executor:
                    allowed = list(executor.map(reserve, range(20)))
                self.assertEqual(sum(allowed), 5)

    def test_simultaneous_new_clients_cannot_exceed_storage_capacity(self):
        limits = PublicLimits(max_clients=3, clock=self.clock)
        barrier = threading.Barrier(20)

        def reserve(number):
            barrier.wait(timeout=5)
            try:
                limits.check_chat(client(str(number)))
                return True
            except PublicLimitError:
                return False

        with ThreadPoolExecutor(max_workers=20) as executor:
            allowed = list(executor.map(reserve, range(20)))
        self.assertEqual(sum(allowed), 3)
        self.assertEqual(limits.tracked_clients, 3)

    def test_proxy_shares_budget_and_blocks_before_the_underlying_call(self):
        models = Mock()
        models.generate_content.return_value = object()
        models.embed_content.return_value = object()
        proxy = MeteredModels(models, PublicLimits(api_limit=2, clock=self.clock))
        generated = proxy.generate_content(model="demo-model", contents="question")
        embedded = proxy.embed_content(model="demo-embedding", contents=["question"])
        self.assertIs(generated, models.generate_content.return_value)
        self.assertIs(embedded, models.embed_content.return_value)
        with self.assertRaises(PublicLimitError):
            proxy.generate_content(model="demo-model", contents="rewrite")
        models.generate_content.assert_called_once_with(model="demo-model", contents="question")
        models.embed_content.assert_called_once_with(model="demo-embedding", contents=["question"])

    def test_failed_sdk_attempt_consumes_budget_before_error_is_raised(self):
        models = Mock()
        models.generate_content.side_effect = RuntimeError("simulated provider failure")
        proxy = MeteredModels(models, PublicLimits(api_limit=1, clock=self.clock))
        with self.assertRaisesRegex(RuntimeError, "simulated provider failure"):
            proxy.generate_content(contents="question")
        with self.assertRaises(PublicLimitError):
            proxy.embed_content(contents=["question"])
        models.generate_content.assert_called_once()
        models.embed_content.assert_not_called()


if __name__ == "__main__":
    unittest.main()
