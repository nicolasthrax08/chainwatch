#!/usr/bin/env python3
"""
Unit tests for the tx_fetcher service.

Tests cover:
- _get_client: singleton creation, reuse, and close
- close_client: idempotent close
- _retry_request: success on first try, 429 backoff then success,
  non-429 HTTP error propagation, exhausted retries
- fetch_btc_transactions: response parsing, net amount calculation,
  fallback to BlockCypher, empty results on total failure
- fetch_eth_transactions: API key gating, response parsing,
  status=0 handling (rate limit, invalid key, generic)
- fetch_sol_transactions: signature fetching, concurrent tx detail fetch,
  net SOL calculation, error field handling, partial error isolation
- fetch_transactions_for_wallet: chain dispatch, unknown chain
- fetch_transactions_for_wallets: concurrent multi-wallet, sorting,
  exception isolation, wallet annotation
- classify_wallet: whale threshold (>=), fresh wallet, normal,
  unknown chain, boundary values
- tx_type_determine: always returns 'unknown' for BlockCypher

Run: python3 -m pytest tests/test_tx_fetcher.py -v
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from services import tx_fetcher


def _run(coro):
    """Run a coroutine in a new event loop (avoids 'no current loop' issues)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestGetClient(unittest.TestCase):
    """Test the shared HTTP client singleton."""

    def setUp(self):
        """Reset the global client before each test."""
        # We need to reset the module-level _http_client
        tx_fetcher._http_client = None

    def tearDown(self):
        """Clean up any created client."""
        if tx_fetcher._http_client is not None:
            _run(tx_fetcher._http_client.aclose())
            tx_fetcher._http_client = None

    def test_get_client_creates_new_client(self):
        """First call should create a new httpx.AsyncClient."""
        client = _run(tx_fetcher._get_client())
        self.assertIsNotNone(client)
        self.assertIsInstance(client, __import__("httpx").AsyncClient)

    def test_get_client_returns_same_instance(self):
        """Second call should return the same client object."""
        c1 = _run(tx_fetcher._get_client())
        c2 = _run(tx_fetcher._get_client())
        self.assertIs(c1, c2)

    def test_get_client_recreates_after_close(self):
        """After the client is closed, a new one should be created."""
        c1 = _run(tx_fetcher._get_client())
        _run(c1.aclose())
        # Mark as closed
        c2 = _run(tx_fetcher._get_client())
        self.assertIsNot(c1, c2)
        self.assertFalse(c2.is_closed)


class TestCloseClient(unittest.TestCase):
    """Test the close_client shutdown helper."""

    def setUp(self):
        tx_fetcher._http_client = None

    def tearDown(self):
        if tx_fetcher._http_client is not None:
            _run(tx_fetcher._http_client.aclose())
            tx_fetcher._http_client = None

    def test_close_client_closes_open_client(self):
        """close_client should close an open client and set to None."""
        client = _run(tx_fetcher._get_client())
        self.assertFalse(client.is_closed)
        _run(tx_fetcher.close_client())
        self.assertTrue(client.is_closed)
        self.assertIsNone(tx_fetcher._http_client)

    def test_close_client_idempotent(self):
        """Calling close_client twice should not raise."""
        _run(tx_fetcher._get_client())
        _run(tx_fetcher.close_client())
        _run(tx_fetcher.close_client())  # Should not raise
        self.assertIsNone(tx_fetcher._http_client)


class TestRetryRequest(unittest.TestCase):
    """Test the _retry_request helper with 429 backoff."""

    def setUp(self):
        tx_fetcher._http_client = None

    def tearDown(self):
        if tx_fetcher._http_client is not None:
            _run(tx_fetcher._http_client.aclose())
            tx_fetcher._http_client = None

    def test_success_on_first_try(self):
        """A successful response should be returned without retry."""
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_client.request = AsyncMock(return_value=mock_resp)

        result = _run(tx_fetcher._retry_request(mock_client, "GET", "https://example.com"))
        self.assertEqual(result.status_code, 200)
        self.assertEqual(mock_client.request.call_count, 1)

    def test_429_then_success(self):
        """A 429 followed by a 200 should retry and succeed."""
        mock_client = AsyncMock()
        mock_429 = MagicMock()
        mock_429.status_code = 429
        mock_429.headers = {"Retry-After": "0"}
        mock_200 = MagicMock()
        mock_200.status_code = 200
        mock_200.raise_for_status = MagicMock()
        mock_client.request = AsyncMock(side_effect=[mock_429, mock_200])

        result = _run(tx_fetcher._retry_request(mock_client, "GET", "https://example.com"))
        self.assertEqual(result.status_code, 200)
        self.assertEqual(mock_client.request.call_count, 2)

    def test_non_429_http_error_raises(self):
        """A non-429 HTTP error (e.g., 500) should raise immediately."""
        mock_client = AsyncMock()
        mock_500 = MagicMock()
        mock_500.status_code = 500
        mock_500.raise_for_status = MagicMock(
            side_effect=__import__("httpx").HTTPStatusError(
                "error", request=MagicMock(), response=mock_500
            )
        )
        mock_client.request = AsyncMock(return_value=mock_500)

        with self.assertRaises(__import__("httpx").HTTPStatusError):
            _run(tx_fetcher._retry_request(mock_client, "GET", "https://example.com"))

    def test_exhausted_retries_raises_runtime_error(self):
        """All retries returning 429 should raise RuntimeError."""
        mock_client = AsyncMock()
        mock_429 = MagicMock()
        mock_429.status_code = 429
        mock_429.headers = {"Retry-After": "0"}
        mock_client.request = AsyncMock(return_value=mock_429)

        with self.assertRaises(RuntimeError):
            _run(tx_fetcher._retry_request(mock_client, "GET", "https://example.com"))


class TestFetchBtcTransactions(unittest.TestCase):
    """Test BTC transaction fetching from mempool.space."""

    def setUp(self):
        tx_fetcher._http_client = None

    def tearDown(self):
        if tx_fetcher._http_client is not None:
            _run(tx_fetcher._http_client.aclose())
            tx_fetcher._http_client = None

    def _make_mempool_tx(self, txid="abc123", net_sat=100000000,
                         block_time=1700000000, confirmed=True):
        """Build a mempool.space-style transaction dict."""
        return {
            "txid": txid,
            "status": {"block_time": block_time, "confirmed": confirmed},
            "vin": [
                {
                    "prevout": {
                        "value": 50000000,
                        "scriptpubkey_address": "bc1qtest",
                    }
                }
            ],
            "vout": [
                {
                    "value": 150000000,
                    "scriptpubkey_address": "bc1qtest",
                }
            ],
        }

    def test_fetch_btc_parses_receive(self):
        """A positive net amount should be classified as 'receive'."""
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [self._make_mempool_tx()]
        mock_client.request = AsyncMock(return_value=mock_resp)

        with patch.object(tx_fetcher, "_get_client", return_value=mock_client):
            with patch.object(tx_fetcher, "_mempool_sem", new=AsyncMock()):
                # We need to use the actual semaphore context manager
                import asyncio
                real_sem = asyncio.Semaphore(8)
                with patch.object(tx_fetcher, "_mempool_sem", new=real_sem):
                    txs = _run(tx_fetcher.fetch_btc_transactions("bc1qtest", limit=10))

        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]["type"], "receive")
        self.assertEqual(txs[0]["token"], "BTC")
        self.assertEqual(txs[0]["chain"], "btc")
        self.assertIn("tx_hash", txs[0])
        self.assertIn("timestamp", txs[0])

    def test_fetch_btc_parses_send(self):
        """A negative net amount should be classified as 'send'."""
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        # vin > vout means net negative (sending)
        tx = self._make_mempool_tx(net_sat=-100000000)
        tx["vin"] = [
            {"prevout": {"value": 200000000, "scriptpubkey_address": "bc1qtest"}}
        ]
        tx["vout"] = [
            {"value": 100000000, "scriptpubkey_address": "bc1qtest"}
        ]
        mock_resp.json.return_value = [tx]
        mock_client.request = AsyncMock(return_value=mock_resp)

        real_sem = asyncio.Semaphore(8)
        with patch.object(tx_fetcher, "_get_client", return_value=mock_client):
            with patch.object(tx_fetcher, "_mempool_sem", new=real_sem):
                txs = _run(tx_fetcher.fetch_btc_transactions("bc1qtest", limit=10))

        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]["type"], "send")

    def test_fetch_btc_pending_status(self):
        """A transaction with confirmed=false should have status 'pending'."""
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        tx = self._make_mempool_tx(confirmed=False)
        mock_resp.json.return_value = [tx]
        mock_client.request = AsyncMock(return_value=mock_resp)

        real_sem = asyncio.Semaphore(8)
        with patch.object(tx_fetcher, "_get_client", return_value=mock_client):
            with patch.object(tx_fetcher, "_mempool_sem", new=real_sem):
                txs = _run(tx_fetcher.fetch_btc_transactions("bc1qtest", limit=10))

        self.assertEqual(txs[0]["status"], "pending")
        self.assertFalse(txs[0]["is_confirmed"])

    def test_fetch_btc_respects_limit(self):
        """The limit parameter should cap the number of returned transactions."""
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        txs = [self._make_mempool_tx(txid=f"tx{i}") for i in range(20)]
        mock_resp.json.return_value = txs
        mock_client.request = AsyncMock(return_value=mock_resp)

        real_sem = asyncio.Semaphore(8)
        with patch.object(tx_fetcher, "_get_client", return_value=mock_client):
            with patch.object(tx_fetcher, "_mempool_sem", new=real_sem):
                result = _run(tx_fetcher.fetch_btc_transactions("bc1qtest", limit=5))

        self.assertEqual(len(result), 5)

    def test_fetch_btc_mempool_fails_falls_back_to_blockcypher(self):
        """When mempool.space fails, should fall back to BlockCypher."""
        mock_client = AsyncMock()

        # First call (mempool) raises
        mock_client.request = AsyncMock(
            side_effect=[
                __import__("httpx").HTTPStatusError(
                    "error", request=MagicMock(), response=MagicMock(status_code=500)
                ),
                # Second call (BlockCypher) succeeds
                MagicMock(
                    status_code=200,
                    raise_for_status=MagicMock(),
                    json=MagicMock(return_value={
                        "txrefs": [
                            {
                                "tx_hash": "btc-fallback-1",
                                "value": 50000000,
                                "confirmations": 3,
                                "confirmed": "2024-01-15T12:00:00Z",
                            }
                        ]
                    }),
                ),
            ]
        )

        real_sem = asyncio.Semaphore(8)
        with patch.object(tx_fetcher, "_get_client", return_value=mock_client):
            with patch.object(tx_fetcher, "_mempool_sem", new=real_sem):
                with patch.object(tx_fetcher, "_blockcypher_sem", new=real_sem):
                    result = _run(tx_fetcher.fetch_btc_transactions("bc1qtest", limit=10))

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["tx_hash"], "btc-fallback-1")
        self.assertEqual(result[0]["type"], "unknown")  # BlockCypher can't determine direction

    def test_fetch_both_fail_returns_empty(self):
        """When both mempool and BlockCypher fail, should return empty list."""
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            side_effect=Exception("network down")
        )

        real_sem = asyncio.Semaphore(8)
        with patch.object(tx_fetcher, "_get_client", return_value=mock_client):
            with patch.object(tx_fetcher, "_mempool_sem", new=real_sem):
                with patch.object(tx_fetcher, "_blockcypher_sem", new=real_sem):
                    result = _run(tx_fetcher.fetch_btc_transactions("bc1qtest", limit=10))

        self.assertEqual(result, [])


class TestFetchEthTransactions(unittest.TestCase):
    """Test ETH transaction fetching via Etherscan."""

    def setUp(self):
        tx_fetcher._http_client = None

    def tearDown(self):
        if tx_fetcher._http_client is not None:
            _run(tx_fetcher._http_client.aclose())
            tx_fetcher._http_client = None

    def test_no_api_key_returns_empty(self):
        """Without ETHERSCAN_API_KEY, should return empty list immediately."""
        real_sem = asyncio.Semaphore(3)
        with patch.object(tx_fetcher, "_etherscan_sem", new=real_sem):
            with patch.dict(os.environ, {"ETHERSCAN_API_KEY": ""}):
                result = _run(tx_fetcher.fetch_eth_transactions("0xabc", limit=10))
        self.assertEqual(result, [])

    def test_eth_parses_receive(self):
        """A transaction where 'to' matches our address should be 'receive'."""
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "status": "1",
            "message": "OK",
            "result": [
                {
                    "hash": "0xhash1",
                    "to": "0xABC123",
                    "value": "1000000000000000000",  # 1 ETH
                    "timeStamp": "1700000000",
                    "confirmations": "10",
                }
            ],
        }
        mock_client.request = AsyncMock(return_value=mock_resp)

        real_sem = asyncio.Semaphore(3)
        with patch.object(tx_fetcher, "_get_client", return_value=mock_client):
            with patch.object(tx_fetcher, "_etherscan_sem", new=real_sem):
                with patch.dict(os.environ, {"ETHERSCAN_API_KEY": "test-key"}):
                    txs = _run(tx_fetcher.fetch_eth_transactions("0xabc123", limit=10))

        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]["type"], "receive")
        self.assertEqual(txs[0]["token"], "ETH")
        self.assertEqual(txs[0]["chain"], "eth")

    def test_eth_parses_send(self):
        """A transaction where 'to' differs from our address should be 'send'."""
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "status": "1",
            "message": "OK",
            "result": [
                {
                    "hash": "0xhash2",
                    "to": "0xOTHER",
                    "value": "500000000000000000",
                    "timeStamp": "1700000000",
                    "confirmations": "5",
                }
            ],
        }
        mock_client.request = AsyncMock(return_value=mock_resp)

        real_sem = asyncio.Semaphore(3)
        with patch.object(tx_fetcher, "_get_client", return_value=mock_client):
            with patch.object(tx_fetcher, "_etherscan_sem", new=real_sem):
                with patch.dict(os.environ, {"ETHERSCAN_API_KEY": "test-key"}):
                    txs = _run(tx_fetcher.fetch_eth_transactions("0xabc123", limit=10))

        self.assertEqual(txs[0]["type"], "send")

    def test_eth_status_zero_rate_limit(self):
        """Etherscan status=0 with 'rate limit' in result should log and return empty."""
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "status": "0",
            "message": "NOTOK",
            "result": "Max rate limit reached",
        }
        mock_client.request = AsyncMock(return_value=mock_resp)

        real_sem = asyncio.Semaphore(3)
        with patch.object(tx_fetcher, "_get_client", return_value=mock_client):
            with patch.object(tx_fetcher, "_etherscan_sem", new=real_sem):
                with patch.dict(os.environ, {"ETHERSCAN_API_KEY": "test-key"}):
                    txs = _run(tx_fetcher.fetch_eth_transactions("0xabc", limit=10))

        self.assertEqual(txs, [])

    def test_eth_status_zero_invalid_key(self):
        """Etherscan status=0 with 'invalid api key' should return empty."""
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "status": "0",
            "message": "NOTOK",
            "result": "Invalid API Key",
        }
        mock_client.request = AsyncMock(return_value=mock_resp)

        real_sem = asyncio.Semaphore(3)
        with patch.object(tx_fetcher, "_get_client", return_value=mock_client):
            with patch.object(tx_fetcher, "_etherscan_sem", new=real_sem):
                with patch.dict(os.environ, {"ETHERSCAN_API_KEY": "bad-key"}):
                    txs = _run(tx_fetcher.fetch_eth_transactions("0xabc", limit=10))

        self.assertEqual(txs, [])

    def test_eth_status_zero_generic(self):
        """Etherscan status=0 with generic message should return empty."""
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "status": "0",
            "message": "No transactions found",
            "result": [],
        }
        mock_client.request = AsyncMock(return_value=mock_resp)

        real_sem = asyncio.Semaphore(3)
        with patch.object(tx_fetcher, "_get_client", return_value=mock_client):
            with patch.object(tx_fetcher, "_etherscan_sem", new=real_sem):
                with patch.dict(os.environ, {"ETHERSCAN_API_KEY": "test-key"}):
                    txs = _run(tx_fetcher.fetch_eth_transactions("0xabc", limit=10))

        self.assertEqual(txs, [])

    def test_eth_api_error_returns_empty(self):
        """An exception during Etherscan fetch should return empty list."""
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=Exception("timeout"))

        real_sem = asyncio.Semaphore(3)
        with patch.object(tx_fetcher, "_get_client", return_value=mock_client):
            with patch.object(tx_fetcher, "_etherscan_sem", new=real_sem):
                with patch.dict(os.environ, {"ETHERSCAN_API_KEY": "test-key"}):
                    txs = _run(tx_fetcher.fetch_eth_transactions("0xabc", limit=10))

        self.assertEqual(txs, [])


class TestFetchSolTransactions(unittest.TestCase):
    """Test SOL transaction fetching via Solana JSON-RPC."""

    def setUp(self):
        tx_fetcher._http_client = None

    def tearDown(self):
        if tx_fetcher._http_client is not None:
            _run(tx_fetcher._http_client.aclose())
            tx_fetcher._http_client = None

    def _make_sig_response(self, sigs=None):
        if sigs is None:
            sigs = [{"signature": "sig1"}, {"signature": "sig2"}]
        return {"jsonrpc": "2.0", "result": sigs}

    def _make_tx_response(self, sig, net_lamports=1000000000, block_time=1700000000,
                          err=None, address="addr1"):
        return {
            "jsonrpc": "2.0",
            "result": {
                "blockTime": block_time,
                "meta": {
                    "err": err,
                    "preBalances": [5000000000, 1000000000],
                    "postBalances": [
                        5000000000 + net_lamports,
                        1000000000 - net_lamports,
                    ],
                },
                "transaction": {
                    "message": {
                        "accountKeys": [
                            {"pubkey": address},
                            {"pubkey": "other_addr"},
                        ]
                    }
                },
            },
        }

    def test_sol_parses_receive(self):
        """Positive net SOL should be classified as 'receive'."""
        mock_client = AsyncMock()

        sig_resp = MagicMock()
        sig_resp.status_code = 200
        sig_resp.raise_for_status = MagicMock()
        sig_resp.json.return_value = self._make_sig_response()

        tx_resp = MagicMock()
        tx_resp.status_code = 200
        tx_resp.raise_for_status = MagicMock()
        tx_resp.json.return_value = self._make_tx_response("sig1", net_lamports=1000000000)

        mock_client.request = AsyncMock(side_effect=[sig_resp, tx_resp])

        real_sem = asyncio.Semaphore(5)
        with patch.object(tx_fetcher, "_get_client", return_value=mock_client):
            with patch.object(tx_fetcher, "_sol_rpc_sem", new=real_sem):
                txs = _run(tx_fetcher.fetch_sol_transactions("addr1", limit=10))

        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]["type"], "receive")
        self.assertEqual(txs[0]["token"], "SOL")
        self.assertEqual(txs[0]["chain"], "sol")

    def test_sol_parses_send(self):
        """Negative net SOL should be classified as 'send'."""
        mock_client = AsyncMock()

        sig_resp = MagicMock()
        sig_resp.status_code = 200
        sig_resp.raise_for_status = MagicMock()
        sig_resp.json.return_value = self._make_sig_response()

        tx_resp = MagicMock()
        tx_resp.status_code = 200
        tx_resp.raise_for_status = MagicMock()
        tx_resp.json.return_value = self._make_tx_response("sig1", net_lamports=-500000000)

        mock_client.request = AsyncMock(side_effect=[sig_resp, tx_resp])

        real_sem = asyncio.Semaphore(5)
        with patch.object(tx_fetcher, "_get_client", return_value=mock_client):
            with patch.object(tx_fetcher, "_sol_rpc_sem", new=real_sem):
                txs = _run(tx_fetcher.fetch_sol_transactions("addr1", limit=10))

        self.assertEqual(txs[0]["type"], "send")

    def test_sol_failed_tx_status(self):
        """A transaction with meta.err should have status 'failed'."""
        mock_client = AsyncMock()

        sig_resp = MagicMock()
        sig_resp.status_code = 200
        sig_resp.raise_for_status = MagicMock()
        sig_resp.json.return_value = self._make_sig_response()

        tx_resp = MagicMock()
        tx_resp.status_code = 200
        tx_resp.raise_for_status = MagicMock()
        tx_resp.json.return_value = self._make_tx_response(
            "sig1", net_lamports=1000000000, err={"InstructionError": [0, "test"]}
        )

        mock_client.request = AsyncMock(side_effect=[sig_resp, tx_resp])

        real_sem = asyncio.Semaphore(5)
        with patch.object(tx_fetcher, "_get_client", return_value=mock_client):
            with patch.object(tx_fetcher, "_sol_rpc_sem", new=real_sem):
                txs = _run(tx_fetcher.fetch_sol_transactions("addr1", limit=10))

        self.assertEqual(txs[0]["status"], "failed")
        self.assertFalse(txs[0]["is_confirmed"])

    def test_sol_no_signatures_returns_empty(self):
        """When there are no signatures, should return empty list."""
        mock_client = AsyncMock()
        sig_resp = MagicMock()
        sig_resp.status_code = 200
        sig_resp.raise_for_status = MagicMock()
        sig_resp.json.return_value = {"jsonrpc": "2.0", "result": []}
        mock_client.request = AsyncMock(return_value=sig_resp)

        real_sem = asyncio.Semaphore(5)
        with patch.object(tx_fetcher, "_get_client", return_value=mock_client):
            with patch.object(tx_fetcher, "_sol_rpc_sem", new=real_sem):
                txs = _run(tx_fetcher.fetch_sol_transactions("addr1", limit=10))

        self.assertEqual(txs, [])

    def test_sol_partial_error_isolation(self):
        """If one tx detail fetch fails, others should still be returned."""
        mock_client = AsyncMock()

        sig_resp = MagicMock()
        sig_resp.status_code = 200
        sig_resp.raise_for_status = MagicMock()
        sig_resp.json.return_value = self._make_sig_response(
            sigs=[{"signature": "sig1"}, {"signature": "sig2"}]
        )

        tx_resp_ok = MagicMock()
        tx_resp_ok.status_code = 200
        tx_resp_ok.raise_for_status = MagicMock()
        tx_resp_ok.json.return_value = self._make_tx_response("sig1", net_lamports=1000000000)

        tx_resp_fail = MagicMock()
        tx_resp_fail.status_code = 200
        tx_resp_fail.raise_for_status = MagicMock()
        tx_resp_fail.json.return_value = {"jsonrpc": "2.0", "result": None}

        mock_client.request = AsyncMock(side_effect=[sig_resp, tx_resp_ok, tx_resp_fail])

        real_sem = asyncio.Semaphore(5)
        with patch.object(tx_fetcher, "_get_client", return_value=mock_client):
            with patch.object(tx_fetcher, "_sol_rpc_sem", new=real_sem):
                txs = _run(tx_fetcher.fetch_sol_transactions("addr1", limit=10))

        # Only sig1 should be returned; sig2 had null result
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]["tx_hash"], "sig1")

    def test_sol_string_account_keys(self):
        """Account keys can be plain strings instead of dicts with 'pubkey'."""
        mock_client = AsyncMock()

        sig_resp = MagicMock()
        sig_resp.status_code = 200
        sig_resp.raise_for_status = MagicMock()
        sig_resp.json.return_value = self._make_sig_response()

        tx_data = self._make_tx_response("sig1", net_lamports=1000000000)
        # Use string keys instead of dict keys
        tx_data["result"]["transaction"]["message"]["accountKeys"] = [
            "addr1",
            "other_addr",
        ]

        tx_resp = MagicMock()
        tx_resp.status_code = 200
        tx_resp.raise_for_status = MagicMock()
        tx_resp.json.return_value = tx_data

        mock_client.request = AsyncMock(side_effect=[sig_resp, tx_resp])

        real_sem = asyncio.Semaphore(5)
        with patch.object(tx_fetcher, "_get_client", return_value=mock_client):
            with patch.object(tx_fetcher, "_sol_rpc_sem", new=real_sem):
                txs = _run(tx_fetcher.fetch_sol_transactions("addr1", limit=10))

        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]["type"], "receive")

    def test_sol_api_error_returns_empty(self):
        """An exception during Solana fetch should return empty list."""
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=Exception("RPC error"))

        real_sem = asyncio.Semaphore(5)
        with patch.object(tx_fetcher, "_get_client", return_value=mock_client):
            with patch.object(tx_fetcher, "_sol_rpc_sem", new=real_sem):
                txs = _run(tx_fetcher.fetch_sol_transactions("addr1", limit=10))

        self.assertEqual(txs, [])


class TestFetchTransactionsForWallet(unittest.TestCase):
    """Test the chain dispatcher."""

    def test_known_chains(self):
        """Known chains (btc, eth, sol) should dispatch to the correct fetcher."""
        mock_btc = AsyncMock(return_value=[])
        mock_eth = AsyncMock(return_value=[])
        mock_sol = AsyncMock(return_value=[])
        with patch.object(tx_fetcher, "fetch_btc_transactions", new=mock_btc):
            with patch.object(tx_fetcher, "fetch_eth_transactions", new=mock_eth):
                with patch.object(tx_fetcher, "fetch_sol_transactions", new=mock_sol):
                    _run(tx_fetcher.fetch_transactions_for_wallet("addr1", "btc"))
                    _run(tx_fetcher.fetch_transactions_for_wallet("addr2", "eth"))
                    _run(tx_fetcher.fetch_transactions_for_wallet("addr3", "sol"))
                    mock_btc.assert_called_once()
                    mock_eth.assert_called_once()
                    mock_sol.assert_called_once()

    def test_unknown_chain_returns_empty(self):
        """An unknown chain should return empty list."""
        result = _run(tx_fetcher.fetch_transactions_for_wallet("addr", "doge"))
        self.assertEqual(result, [])


class TestFetchTransactionsForWallets(unittest.TestCase):
    """Test concurrent multi-wallet transaction fetching."""

    def test_multi_wallet_concurrent(self):
        """Multiple wallets should be fetched concurrently."""
        wallets = [
            {"address": "addr1", "chain": "btc", "label": "Whale1"},
            {"address": "addr2", "chain": "eth", "label": "Whale2"},
        ]

        async def mock_fetch(address, chain, limit=10):
            return [{"tx_hash": f"tx-{chain}", "timestamp": "2024-01-01", "amount": "1.0",
                      "type": "receive", "token": chain.upper(), "usd_value": 0,
                      "chain": chain, "status": "confirmed", "is_confirmed": True}]

        with patch.object(tx_fetcher, "fetch_transactions_for_wallet", new=mock_fetch):
            result = _run(tx_fetcher.fetch_transactions_for_wallets(wallets, limit=10))

        self.assertEqual(len(result), 2)
        # Each tx should be annotated with wallet info
        self.assertEqual(result[0]["wallet_label"], "Whale1")
        self.assertEqual(result[1]["wallet_label"], "Whale2")

    def test_sorted_by_timestamp_desc(self):
        """Results should be sorted by timestamp descending."""
        wallets = [
            {"address": "addr1", "chain": "btc", "label": "W1"},
            {"address": "addr2", "chain": "eth", "label": "W2"},
        ]

        async def mock_fetch(address, chain, limit=10):
            ts = "2024-01-02" if chain == "btc" else "2024-01-01"
            return [{"tx_hash": f"tx-{chain}", "timestamp": ts, "amount": "1.0",
                      "type": "receive", "token": chain.upper(), "usd_value": 0,
                      "chain": chain, "status": "confirmed", "is_confirmed": True}]

        with patch.object(tx_fetcher, "fetch_transactions_for_wallet", new=mock_fetch):
            result = _run(tx_fetcher.fetch_transactions_for_wallets(wallets, limit=10))

        # BTC (2024-01-02) should come first
        self.assertEqual(result[0]["chain"], "btc")
        self.assertEqual(result[1]["chain"], "eth")

    def test_exception_isolation(self):
        """One wallet failing should not prevent others from returning."""
        wallets = [
            {"address": "addr1", "chain": "btc", "label": "W1"},
            {"address": "addr2", "chain": "eth", "label": "W2"},
        ]

        async def mock_fetch(address, chain, limit=10):
            if chain == "btc":
                raise Exception("BTC API down")
            return [{"tx_hash": "tx-eth", "timestamp": "2024-01-01", "amount": "1.0",
                      "type": "receive", "token": "ETH", "usd_value": 0,
                      "chain": "eth", "status": "confirmed", "is_confirmed": True}]

        with patch.object(tx_fetcher, "fetch_transactions_for_wallet", new=mock_fetch):
            result = _run(tx_fetcher.fetch_transactions_for_wallets(wallets, limit=10))

        # Only ETH wallet should be returned
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["chain"], "eth")


class TestClassifyWallet(unittest.TestCase):
    """Test wallet classification logic."""

    def test_whale_btc(self):
        """A BTC wallet with >= 5.0 BTC should be classified as whale."""
        result = tx_fetcher.classify_wallet(balance_native=10.0, chain="btc", tx_count=100)
        self.assertTrue(result["is_whale"])
        self.assertEqual(result["risk_label"], "whale")

    def test_whale_eth(self):
        """An ETH wallet with >= 50.0 ETH should be classified as whale."""
        result = tx_fetcher.classify_wallet(balance_native=100.0, chain="eth", tx_count=50)
        self.assertTrue(result["is_whale"])
        self.assertEqual(result["risk_label"], "whale")

    def test_whale_sol(self):
        """A SOL wallet with >= 500.0 SOL should be classified as whale."""
        result = tx_fetcher.classify_wallet(balance_native=1000.0, chain="sol", tx_count=200)
        self.assertTrue(result["is_whale"])
        self.assertEqual(result["risk_label"], "whale")

    def test_exact_threshold_is_whale(self):
        """A wallet at exactly the threshold should be whale (>= comparison)."""
        result = tx_fetcher.classify_wallet(balance_native=5.0, chain="btc", tx_count=10)
        self.assertTrue(result["is_whale"])

    def test_just_below_threshold_not_whale(self):
        """A wallet just below threshold should not be whale."""
        result = tx_fetcher.classify_wallet(balance_native=4.99, chain="btc", tx_count=10)
        self.assertFalse(result["is_whale"])

    def test_fresh_wallet(self):
        """A wallet with 0 transactions should be classified as fresh_wallet."""
        result = tx_fetcher.classify_wallet(balance_native=0.001, chain="eth", tx_count=0)
        self.assertTrue(result["is_fresh_wallet"])
        self.assertEqual(result["risk_label"], "fresh_wallet")

    def test_normal_wallet(self):
        """A wallet below threshold with tx_count > 0 should be normal."""
        result = tx_fetcher.classify_wallet(balance_native=1.0, chain="btc", tx_count=50)
        self.assertFalse(result["is_whale"])
        self.assertFalse(result["is_fresh_wallet"])
        self.assertEqual(result["risk_label"], "normal")

    def test_unknown_chain_never_whale(self):
        """An unknown chain should never be classified as whale (threshold=inf)."""
        result = tx_fetcher.classify_wallet(balance_native=1e9, chain="doge", tx_count=1000)
        self.assertFalse(result["is_whale"])
        self.assertEqual(result["risk_label"], "normal")

    def test_whale_takes_priority_over_fresh(self):
        """A whale with 0 tx should be classified as whale (whale > fresh priority)."""
        result = tx_fetcher.classify_wallet(balance_native=100.0, chain="eth", tx_count=0)
        self.assertTrue(result["is_whale"])
        self.assertTrue(result["is_fresh_wallet"])
        self.assertEqual(result["risk_label"], "whale")


class TestTxTypeDetermine(unittest.TestCase):
    """Test the BlockCypher tx direction placeholder."""

    def test_always_returns_unknown(self):
        """tx_type_determine should always return 'unknown' for BlockCypher."""
        self.assertEqual(tx_fetcher.tx_type_determine({}, "any_addr"), "unknown")
        self.assertEqual(
            tx_fetcher.tx_type_determine({"value": 1000}, "bc1qtest"), "unknown"
        )


if __name__ == "__main__":
    unittest.main()
