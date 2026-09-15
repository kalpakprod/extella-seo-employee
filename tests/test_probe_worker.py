from __future__ import annotations

import importlib.util
import json
import gzip
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("seo_probe_worker", ROOT / "runtime" / "probe" / "entrypoint.py")
assert SPEC is not None and SPEC.loader is not None
WORKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WORKER)


def _plan(timeout_ms: int = 5000) -> bytes:
    return json.dumps({"site_url": "https://example.com/", "plan": {"timeout_ms": timeout_ms}}).encode()


class ProbeHandleRunTest(unittest.TestCase):
    def test_unknown_kind_is_rejected(self) -> None:
        status, payload = WORKER.handle_run(_plan(), "bogus")
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "unknown_probe_kind")

    def test_each_kind_rejects_a_bad_plan(self) -> None:
        for kind in ("nu", "tls", "cc"):
            with self.subTest(kind=kind):
                status, payload = WORKER.handle_run(b"{}", kind)
                self.assertEqual(status, 400)
                self.assertEqual(payload["code"], "invalid_request")

    def test_site_fetching_kinds_reject_a_private_site_host(self) -> None:
        body = json.dumps({"site_url": "http://127.0.0.1/", "plan": {"timeout_ms": 5000}}).encode()
        for kind in ("nu", "tls"):
            with self.subTest(kind=kind):
                status, _payload = WORKER.handle_run(body, kind)
                self.assertEqual(status, 400)

    def test_cc_kind_never_fetches_the_site_host(self) -> None:
        body = json.dumps({"site_url": "http://127.0.0.1/", "plan": {"timeout_ms": 5000}}).encode()
        with mock.patch.object(WORKER, "_get_json", side_effect=OSError("down")):
            status, payload = WORKER.handle_run(body, "cc")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "unavailable")


class NuProbeTest(unittest.TestCase):
    def test_messages_count_errors_and_warnings_only(self) -> None:
        errors, warnings, exemplars = WORKER._nu_messages({
            "messages": [
                {"type": "error", "lastLine": 5, "message": "bad"},
                {"type": "info", "subType": "warning", "message": "careful"},
                {"type": "info", "message": "plain note"},
            ]
        })
        self.assertEqual((errors, warnings), (1, 1))
        self.assertEqual([item["kind"] for item in exemplars], ["error", "warning"])

    def test_exemplars_are_capped_and_truncated(self) -> None:
        messages = [{"type": "error", "lastLine": 1, "message": "x" * 500} for _ in range(10)]
        _errors, _warnings, exemplars = WORKER._nu_messages({"messages": messages})
        self.assertEqual(len(exemplars), 3)
        self.assertEqual(len(exemplars[0]["message"]), 200)

    def test_fetch_failure_is_unavailable_not_an_exception(self) -> None:
        with (
            mock.patch.object(WORKER, "_is_global_host", return_value=True),
            mock.patch.object(WORKER, "fetch_bytes", side_effect=OSError("down")),
        ):
            result = WORKER.run_nu_probe("https://example.com/", 5000)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "http_503")

    def test_validator_failure_is_unavailable_not_an_exception(self) -> None:
        with (
            mock.patch.object(WORKER, "_is_global_host", return_value=True),
            mock.patch.object(WORKER, "fetch_bytes", return_value=(200, b"<html></html>", False, {})),
            mock.patch("urllib.request.urlopen", side_effect=OSError("down")),
        ):
            result = WORKER.run_nu_probe("https://example.com/", 5000)
        self.assertEqual(result["status"], "unavailable")


class TlsProbeTest(unittest.TestCase):
    def test_hsts_max_age_parsing(self) -> None:
        self.assertIsNone(WORKER._hsts_max_age(None))
        self.assertEqual(WORKER._hsts_max_age("max-age=0; includeSubDomains"), 0)
        self.assertEqual(WORKER._hsts_max_age("max-age=31536000"), 31536000)
        self.assertIsNone(WORKER._hsts_max_age("max-age=nope"))

    def test_ssl_grade_keeps_the_worst_ready_endpoint(self) -> None:
        self.assertEqual(
            WORKER._ssl_grade({"status": "READY", "endpoints": [{"grade": "A"}, {"grade": "B"}]}),
            ("ready", "B"),
        )
        self.assertEqual(WORKER._ssl_grade({"status": "DNS"}), ("dns", None))
        self.assertEqual(WORKER._ssl_grade(None), ("unavailable", None))

    def test_labs_is_queried_once_and_never_polled(self) -> None:
        seen = {"host": "example.com", "strict-transport-security": "max-age=10"}
        with (
            mock.patch.object(WORKER, "_is_global_host", return_value=True),
            mock.patch.object(WORKER, "fetch_bytes", return_value=(200, b"x", False, seen)),
            mock.patch.object(WORKER, "_get_json", return_value={"status": "IN_PROGRESS"}) as getter,
        ):
            result = WORKER.run_tls_probe("https://example.com/", 5000)
        self.assertEqual(getter.call_count, 1)
        self.assertEqual(result["ssl_labs"]["status"], "pending")
        self.assertIsNone(result["ssl_labs"]["grade"])

    def test_fetch_failure_is_unavailable_not_an_exception(self) -> None:
        with (
            mock.patch.object(WORKER, "_is_global_host", return_value=True),
            mock.patch.object(WORKER, "fetch_bytes", side_effect=OSError("down")),
        ):
            result = WORKER.run_tls_probe("https://example.com/", 5000)
        self.assertEqual(result["status"], "unavailable")


class CcProbeTest(unittest.TestCase):
    def test_record_rejects_paths_outside_crawl_data(self) -> None:
        good = {
            "url": "http://example.com/", "filename": "crawl-data/f.warc.gz",
            "offset": "10", "length": "5", "status": "200",
        }
        self.assertIsNotNone(WORKER._cc_record(good))
        bad = dict(good, filename="/etc/passwd")
        self.assertIsNone(WORKER._cc_record(bad))
        self.assertIsNone(WORKER._cc_record({"nope": True}))

    def test_archive_record_must_match_the_exact_requested_url(self) -> None:
        record = {
            "url": "https://example.com/other", "filename": "crawl-data/f.warc.gz",
            "offset": "10", "length": "5", "status": "200",
        }
        with mock.patch.object(WORKER, "_get_json", side_effect=[[{"id": "CC-MAIN-2025-30"}], record]):
            result = WORKER.run_cc_probe("https://example.com/", 5000)
        self.assertEqual(result, {"status": "unavailable", "reason": "http_503"})

    def test_missing_record_is_ok_with_empty_excerpt(self) -> None:
        with (
            mock.patch.object(WORKER, "_get_json", side_effect=[[{"id": "CC-MAIN-2025-30"}], {}]),
            mock.patch("urllib.request.urlopen") as opener,
        ):
            result = WORKER.run_cc_probe("https://example.com/", 5000)
        opener.assert_not_called()
        self.assertNotIn("status", result)
        self.assertEqual(result["schema"], "extella.cc_source.v1")
        self.assertIsNone(result["record"])
        self.assertEqual(result["excerpt"], "")

    def test_excerpt_is_capped(self) -> None:
        record = {
            "url": "https://example.com/", "filename": "crawl-data/f.warc.gz",
            "offset": "0", "length": "999999", "status": "200",
        }
        body = gzip.compress(b"WARC/1.0\r\n\r\nHTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n<p>" + b"y" * 5000 + b"</p>")

        class _Response:
            status = 206
            headers = {"Content-Range": "bytes 0-999/1000"}

            def __enter__(self) -> "_Response":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self, _limit: int) -> bytes:
                return body

        with (
            mock.patch.object(WORKER, "_get_json", side_effect=[[{"id": "CC-MAIN-2025-30"}], record]),
            mock.patch("urllib.request.urlopen", return_value=_Response()),
        ):
            result = WORKER.run_cc_probe("https://example.com/", 5000)
        self.assertEqual(result["excerpt"], "y" * 500)

    def test_index_outage_is_unavailable_not_an_exception(self) -> None:
        with mock.patch.object(WORKER, "_get_json", side_effect=OSError("down")):
            result = WORKER.run_cc_probe("https://example.com/", 5000)
        self.assertEqual(result["status"], "unavailable")


class ProbeHonestyTests(unittest.TestCase):
    def test_site_fetch_pins_the_validated_public_address(self):
        response = mock.MagicMock()
        response.status = 200
        response.getheaders.return_value = []
        response.read.return_value = b"ok"
        connection = mock.MagicMock()
        connection.getresponse.return_value = response
        with (
            mock.patch.object(WORKER, "_global_addresses", return_value=["93.184.216.34"]),
            mock.patch.object(WORKER, "_PinnedHTTPConnection", return_value=connection) as factory,
        ):
            result = WORKER.fetch_bytes(
                "http://example.com/path?q=1", 5, 10, allowed_hosts={"example.com"}
            )
        factory.assert_called_once_with("example.com", 80, "93.184.216.34", mock.ANY)
        connection.request.assert_called_once_with(
            "GET", "/path?q=1", headers={"Host": "example.com", "User-Agent": "ExtellaProbe/2.1"}
        )
        self.assertEqual(result[:3], (200, b"ok", False))

    def test_mixed_public_private_dns_answer_is_rejected(self):
        answers = [
            (WORKER.socket.AF_INET, WORKER.socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (WORKER.socket.AF_INET, WORKER.socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ]
        with mock.patch.object(WORKER.socket, "getaddrinfo", return_value=answers):
            with self.assertRaisesRegex(ValueError, "not a global address"):
                WORKER._global_addresses("example.com", 443)

    def test_deadline_cannot_be_extended_by_another_request(self):
        with mock.patch.object(WORKER.time, "monotonic", return_value=10):
            with self.assertRaises(TimeoutError):
                WORKER._remaining(9)

    def test_truncated_html_is_not_reported_as_invalid_html(self):
        with mock.patch.object(WORKER, "_is_global_host", return_value=True), mock.patch.object(WORKER, "fetch_bytes", return_value=(200, b"<html", True, {})), mock.patch("urllib.request.urlopen") as opener:
            result = WORKER.run_nu_probe("https://example.com/", 5000)
        opener.assert_not_called()
        self.assertEqual(result["status"], "unavailable")

    def test_validator_internal_errors_are_not_a_clean_validation(self):
        with self.assertRaises(ValueError):
            WORKER._nu_messages({"messages": [{"type": "non-document-error", "message": "internal error"}]})

    def test_archive_excerpt_decompresses_and_omits_scripts(self):
        raw = gzip.compress(b'WARC/1.0\r\n\r\nHTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n<title>Archive</title><script>secret()</script><p>Some text</p>')
        self.assertEqual(WORKER._archive_excerpt(raw), "Archive Some text")
        with self.assertRaises(ValueError):
            WORKER._archive_excerpt(b"not gzip")

    def test_archive_decompression_is_bounded(self):
        raw = gzip.compress(b'WARC/1.0\r\n\r\nHTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n<p>' + b'x' * 2_000_000)
        self.assertEqual(WORKER._archive_excerpt(raw), "x" * 500)

    def test_index_json_lines_and_cap(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"url":"first"}\n{"url":"second"}\n'
        with mock.patch("urllib.request.urlopen", return_value=response):
            self.assertEqual(WORKER._get_json("https://index.commoncrawl.org/CC-MAIN-2026-33-index?url=x", 1), [{"url": "first"}, {"url": "second"}])
            with self.assertRaises(ValueError):
                WORKER._get_json("https://index.commoncrawl.org/collinfo.json", 1, 5)

    def test_archive_crawler_encoding_metadata_does_not_hide_plain_html(self):
        raw = gzip.compress(b'WARC/1.0\r\n\r\nHTTP/1.1 200 OK\r\nContent-Type: text/html\r\nX-Crawler-content-encoding: br\r\n\r\n<p>Readable archive</p>')
        self.assertEqual(WORKER._archive_excerpt(raw), "Readable archive")
