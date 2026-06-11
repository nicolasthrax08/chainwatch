"""
Tests for services/blockchain.py
=================================
Blockchain API clients: EtherscanClient, SolscanClient, BlockchairClient,
and the get_eth_price_usd price feed.
Tests cover: balance fetching, error handling, fallback logic, response parsing.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from services.blockchain import (
    EtherscanClient,
    SolscanClient,
    BlockchairClient,
    get_eth_price_usd,
)


# ─── EtherscanClient ─────────────────────────────────────────────────

class TestEtherscanClient:
    """Ethereum balance via publicnode JSON-RPC."""

    @pytest.mark.asyncio
    async def test_get_eth_balance_success(self):
        """Should parse hex balance and return ETH + wei."""
        client = EtherscanClient()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": "0xde0b6b3a7640000"  # 1 ETH in wei
        }
        mock_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_resp)
        mock_http.is_closed = False
        client._client = mock_http

        result = await client.get_eth_balance("0x" + "a" * 40)
        assert result["balance_eth"] == 1.0
        assert result["balance_wei"] == 1e18
        assert result["address"] == "0x" + "a" * 40

    @pytest.mark.asyncio
    async def test_get_eth_balance_zero(self):
        """Should handle zero balance."""
        client = EtherscanClient()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": "0x0"
        }
        mock_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_resp)
        mock_http.is_closed = False
        client._client = mock_http

        result = await client.get_eth_balance("0x" + "b" * 40)
        assert result["balance_eth"] == 0.0
        assert result["balance_wei"] == 0

    @pytest.mark.asyncio
    async def test_get_eth_balance_api_error(self):
        """Should return error field when API returns error."""
        client = EtherscanClient()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32600, "message": "bad request"}
        }
        mock_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_resp)
        mock_http.is_closed = False
        client._client = mock_http

        result = await client.get_eth_balance("0x" + "c" * 40)
        assert result["balance_eth"] == 0
        assert "error" in result

    @pytest.mark.asyncio
    async def test_get_eth_balance_http_error(self):
        """Should return error dict on HTTP failure."""
        client = EtherscanClient()
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(side_effect=httpx.HTTPError("timeout"))
        mock_http.is_closed = False
        client._client = mock_http

        result = await client.get_eth_balance("0x" + "d" * 40)
        assert result["balance_eth"] == 0
        assert "error" in result
        assert "timeout" in result["error"]

    @pytest.mark.asyncio
    async def test_get_eth_balance_sends_correct_rpc_payload(self):
        """Should send correct JSON-RPC payload."""
        client = EtherscanClient()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"result": "0x0"}
        mock_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_resp)
        mock_http.is_closed = False
        client._client = mock_http

        address = "0x" + "e" * 40
        await client.get_eth_balance(address)

        call_args = mock_http.post.call_args
        payload = call_args[1]["json"]
        assert payload["method"] == "eth_getBalance"
        assert payload["params"] == [address, "latest"]
        assert payload["jsonrpc"] == "2.0"

    @pytest.mark.asyncio
    async def test_get_transactions_returns_empty(self):
        """get_transactions currently returns empty list."""
        client = EtherscanClient()
        result = await client.get_transactions("0x" + "a" * 40)
        assert result == []

    @pytest.mark.asyncio
    async def test_close(self):
        """close() should close the underlying client."""
        client = EtherscanClient()
        mock_http = AsyncMock()
        mock_http.is_closed = False
        mock_http.aclose = AsyncMock()
        client._client = mock_http

        await client.close()
        mock_http.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_already_closed(self):
        """close() should not fail if client is already closed."""
        client = EtherscanClient()
        mock_http = AsyncMock()
        mock_http.is_closed = True
        client._client = mock_http

        await client.close()  # Should not raise

    @pytest.mark.asyncio
    async def test_get_client_creates_if_closed(self):
        """_get_client should create new client if current is closed."""
        client = EtherscanClient()
        mock_http = AsyncMock()
        mock_http.is_closed = True
        client._client = mock_http

        with patch("httpx.AsyncClient") as mock_cls:
            new_mock = AsyncMock()
            new_mock.is_closed = False
            mock_cls.return_value = new_mock
            result = await client._get_client()
            assert result is new_mock


# ─── SolscanClient ───────────────────────────────────────────────────

class TestSolscanClient:
    """Solana balance via public JSON-RPC."""

    @pytest.mark.asyncio
    async def test_get_balance_success(self):
        """Should parse lamports and return SOL + lamports."""
        client = SolscanClient()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"value": 5000000000}  # 5 SOL
        }
        mock_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_resp)
        mock_http.is_closed = False
        client._client = mock_http

        result = await client.get_balance("A" * 44)
        assert result["balance_sol"] == 5.0
        assert result["balance_lamports"] == 5000000000

    @pytest.mark.asyncio
    async def test_get_balance_zero(self):
        """Should handle zero balance."""
        client = SolscanClient()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"value": 0}
        }
        mock_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_resp)
        mock_http.is_closed = False
        client._client = mock_http

        result = await client.get_balance("B" * 44)
        assert result["balance_sol"] == 0.0

    @pytest.mark.asyncio
    async def test_get_balance_api_error(self):
        """Should return error when API returns error."""
        client = SolscanClient()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32600, "message": "invalid"}
        }
        mock_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_resp)
        mock_http.is_closed = False
        client._client = mock_http

        result = await client.get_balance("C" * 44)
        assert result["balance_sol"] == 0
        assert "error" in result

    @pytest.mark.asyncio
    async def test_get_balance_http_error(self):
        """Should return error dict on HTTP failure."""
        client = SolscanClient()
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(side_effect=httpx.HTTPError("conn refused"))
        mock_http.is_closed = False
        client._client = mock_http

        result = await client.get_balance("D" * 44)
        assert result["balance_sol"] == 0
        assert "error" in result

    @pytest.mark.asyncio
    async def test_get_balance_sends_correct_rpc_payload(self):
        """Should send correct JSON-RPC payload."""
        client = SolscanClient()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"result": {"value": 0}}
        mock_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_resp)
        mock_http.is_closed = False
        client._client = mock_http

        address = "E" * 44
        await client.get_balance(address)

        call_args = mock_http.post.call_args
        payload = call_args[1]["json"]
        assert payload["method"] == "getBalance"
        assert payload["params"] == [address]

    @pytest.mark.asyncio
    async def test_get_transactions_returns_empty(self):
        """get_transactions currently returns empty list."""
        client = SolscanClient()
        result = await client.get_transactions("A" * 44)
        assert result == []

    @pytest.mark.asyncio
    async def test_close(self):
        """close() should close the underlying client."""
        client = SolscanClient()
        mock_http = AsyncMock()
        mock_http.is_closed = False
        mock_http.aclose = AsyncMock()
        client._client = mock_http

        await client.close()
        mock_http.aclose.assert_called_once()


# ─── BlockchairClient ────────────────────────────────────────────────

class TestBlockchairClient:
    """Bitcoin balance via mempool.space with BlockCypher fallback."""

    @pytest.mark.asyncio
    async def test_get_balance_success(self):
        """Should compute balance from funded - spent."""
        client = BlockchairClient()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "chain_stats": {
                "funded_txo_sum": 5000000000,  # 50 BTC in satoshis
                "spent_txo_sum": 2000000000,   # 20 BTC
                "tx_count": 150
            }
        }
        mock_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_resp)
        mock_http.is_closed = False
        client._client = mock_http

        result = await client.get_balance("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
        assert result["balance_btc"] == 30.0  # 50 - 20
        assert result["balance_satoshis"] == 3000000000
        assert result["tx_count"] == 150

    @pytest.mark.asyncio
    async def test_get_balance_zero(self):
        """Should handle zero balance."""
        client = BlockchairClient()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "chain_stats": {
                "funded_txo_sum": 0,
                "spent_txo_sum": 0,
                "tx_count": 0
            }
        }
        mock_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_resp)
        mock_http.is_closed = False
        client._client = mock_http

        result = await client.get_balance("1A" * 33)
        assert result["balance_btc"] == 0.0
        assert result["tx_count"] == 0

    @pytest.mark.asyncio
    async def test_get_balance_http_error_triggers_fallback(self):
        """Should fall back to BlockCypher on mempool.space failure."""
        client = BlockchairClient()

        # First call (mempool) fails
        fail_resp = MagicMock()
        fail_resp.raise_for_status = MagicMock(side_effect=httpx.HTTPError("mempool down"))

        # Second call (BlockCypher) succeeds
        fallback_resp = MagicMock()
        fallback_resp.json.return_value = {
            "balance": 100000000,  # 1 BTC
            "n_tx": 5
        }
        fallback_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=[fail_resp, fallback_resp])
        mock_http.is_closed = False
        client._client = mock_http

        result = await client.get_balance("1B" * 33)
        assert result["balance_btc"] == 1.0
        assert result["tx_count"] == 5

    @pytest.mark.asyncio
    async def test_get_balance_both_fail(self):
        """Should return error when both mempool and BlockCypher fail."""
        client = BlockchairClient()
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=httpx.HTTPError("all down"))
        mock_http.is_closed = False
        client._client = mock_http

        result = await client.get_balance("1C" * 33)
        assert result["balance_btc"] == 0
        assert "error" in result

    @pytest.mark.asyncio
    async def test_get_transactions_success(self):
        """Should parse transaction list from mempool.space."""
        client = BlockchairClient()
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {
                "txid": "abc123",
                "status": {
                    "block_time": 1700000000,
                    "confirmed": True,
                    "block_height": 800000
                }
            },
            {
                "txid": "def456",
                "status": {
                    "block_time": 1700001000,
                    "confirmed": True,
                    "block_height": 800001
                }
            }
        ]
        mock_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_resp)
        mock_http.is_closed = False
        client._client = mock_http

        result = await client.get_transactions("1D" * 33)
        assert len(result) == 2
        assert result[0]["tx_hash"] == "abc123"
        assert result[0]["confirmed"] is True
        assert result[0]["block_height"] == 800000
        assert result[0]["token"] == "BTC"

    @pytest.mark.asyncio
    async def test_get_transactions_empty_on_error(self):
        """Should return empty list on HTTP error."""
        client = BlockchairClient()
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=httpx.HTTPError("fail"))
        mock_http.is_closed = False
        client._client = mock_http

        result = await client.get_transactions("1E" * 33)
        assert result == []

    @pytest.mark.asyncio
    async def test_get_transactions_respects_limit(self):
        """Should limit returned transactions."""
        client = BlockchairClient()
        txs = [{"txid": f"tx{i}", "status": {"confirmed": False}} for i in range(30)]
        mock_resp = MagicMock()
        mock_resp.json.return_value = txs
        mock_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_resp)
        mock_http.is_closed = False
        client._client = mock_http

        result = await client.get_transactions("1F" * 33, limit=5)
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_get_transactions_no_block_time(self):
        """Should handle transactions without block_time."""
        client = BlockchairClient()
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"txid": "abc", "status": {"confirmed": False}}
        ]
        mock_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_resp)
        mock_http.is_closed = False
        client._client = mock_http

        result = await client.get_transactions("1G" * 33)
        assert len(result) == 1
        assert result[0]["timestamp"] == ""

    @pytest.mark.asyncio
    async def test_close(self):
        """close() should close the underlying client."""
        client = BlockchairClient()
        mock_http = AsyncMock()
        mock_http.is_closed = False
        mock_http.aclose = AsyncMock()
        client._client = mock_http

        await client.close()
        mock_http.aclose.assert_called_once()


# ─── Price Feed ──────────────────────────────────────────────────────

class TestGetEthPriceUsd:
    """get_eth_price_usd() CoinGecko price feed."""

    @pytest.mark.asyncio
    async def test_successful_price_fetch(self):
        """Should return ETH, SOL, BTC prices."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "ethereum": {"usd": 3500.0},
            "solana": {"usd": 150.0},
            "bitcoin": {"usd": 65000.0}
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_ctx:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await get_eth_price_usd()
            assert result["ETH"] == 3500.0
            assert result["SOL"] == 150.0
            assert result["BTC"] == 65000.0

    @pytest.mark.asyncio
    async def test_http_error_returns_zeros(self):
        """Should return zeros on HTTP error."""
        with patch("httpx.AsyncClient") as mock_client_ctx:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.HTTPError("rate limited"))
            mock_client_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await get_eth_price_usd()
            assert result == {"ETH": 0, "SOL": 0, "BTC": 0}

    @pytest.mark.asyncio
    async def test_missing_coin_returns_zero(self):
        """Should return 0 for missing coins in response."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "ethereum": {"usd": 3500.0}
            # solana and bitcoin missing
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_ctx:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await get_eth_price_usd()
            assert result["ETH"] == 3500.0
            assert result["SOL"] == 0
            assert result["BTC"] == 0

    @pytest.mark.asyncio
    async def test_correct_api_params(self):
        """Should call CoinGecko with correct ids and vs_currencies."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_ctx:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            await get_eth_price_usd()
            call_args = mock_client.get.call_args
            params = call_args[1]["params"]
            assert "ethereum,solana,bitcoin" in params["ids"]
            assert params["vs_currencies"] == "usd"

    @pytest.mark.asyncio
    async def test_generic_exception_returns_zeros(self):
        """Should return zeros on any exception (not just HTTPError)."""
        with patch("httpx.AsyncClient") as mock_client_ctx:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=ValueError("unexpected"))
            mock_client_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await get_eth_price_usd()
            assert result == {"ETH": 0, "SOL": 0, "BTC": 0}
