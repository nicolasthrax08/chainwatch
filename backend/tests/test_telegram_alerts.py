"""
Tests for services/telegram_alerts.py
======================================
Telegram alert service: send_telegram_alert, send_whale_alert,
send_portfolio_alert, send_copy_trade_signal.
Tests cover: missing config, HTTP success/failure, message formatting.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

from services.telegram_alerts import (
    send_telegram_alert,
    send_whale_alert,
    send_portfolio_alert,
    send_copy_trade_signal,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
)


class TestSendTelegramAlert:
    """Core send_telegram_alert function."""

    @pytest.mark.asyncio
    async def test_missing_token_returns_false(self):
        """Should return False when TELEGRAM_BOT_TOKEN is empty."""
        with patch("services.telegram_alerts.TELEGRAM_BOT_TOKEN", ""), \
             patch("services.telegram_alerts.TELEGRAM_CHAT_ID", "123"):
            result = await send_telegram_alert("test message")
            assert result is False

    @pytest.mark.asyncio
    async def test_missing_chat_id_returns_false(self):
        """Should return False when chat_id is empty."""
        with patch("services.telegram_alerts.TELEGRAM_BOT_TOKEN", "fake-token"), \
             patch("services.telegram_alerts.TELEGRAM_CHAT_ID", ""):
            result = await send_telegram_alert("test message")
            assert result is False

    @pytest.mark.asyncio
    async def test_successful_send_returns_true(self):
        """Should return True when Telegram API responds successfully."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch("services.telegram_alerts.TELEGRAM_BOT_TOKEN", "test-token"), \
             patch("services.telegram_alerts.TELEGRAM_CHAT_ID", "12345"):
            with patch("httpx.AsyncClient") as mock_client_ctx:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(return_value=mock_resp)
                mock_client_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

                result = await send_telegram_alert("test message")
                assert result is True

    @pytest.mark.asyncio
    async def test_http_error_returns_false(self):
        """Should return False when Telegram API raises HTTPError."""
        with patch("services.telegram_alerts.TELEGRAM_BOT_TOKEN", "test-token"), \
             patch("services.telegram_alerts.TELEGRAM_CHAT_ID", "12345"):
            with patch("httpx.AsyncClient") as mock_client_ctx:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(side_effect=httpx.HTTPError("fail"))
                mock_client_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

                result = await send_telegram_alert("test message")
                assert result is False

    @pytest.mark.asyncio
    async def test_uses_custom_chat_id(self):
        """Should use provided chat_id over env var."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch("services.telegram_alerts.TELEGRAM_BOT_TOKEN", "test-token"), \
             patch("services.telegram_alerts.TELEGRAM_CHAT_ID", "default-chat"):
            with patch("httpx.AsyncClient") as mock_client_ctx:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(return_value=mock_resp)
                mock_client_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

                result = await send_telegram_alert("msg", chat_id="custom-chat")
                assert result is True
                # Verify the custom chat_id was used in the POST
                call_args = mock_client.post.call_args
                assert call_args[1]["json"]["chat_id"] == "custom-chat"

    @pytest.mark.asyncio
    async def test_correct_api_url(self):
        """Should construct the correct Telegram Bot API URL."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch("services.telegram_alerts.TELEGRAM_BOT_TOKEN", "my-bot-token"), \
             patch("services.telegram_alerts.TELEGRAM_CHAT_ID", "123"):
            with patch("httpx.AsyncClient") as mock_client_ctx:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(return_value=mock_resp)
                mock_client_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

                await send_telegram_alert("test")
                url = mock_client.post.call_args[0][0]
                assert "my-bot-token" in url
                assert "sendMessage" in url

    @pytest.mark.asyncio
    async def test_payload_format(self):
        """Should send correct payload with HTML parse mode and no preview."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch("services.telegram_alerts.TELEGRAM_BOT_TOKEN", "test-token"), \
             patch("services.telegram_alerts.TELEGRAM_CHAT_ID", "123"):
            with patch("httpx.AsyncClient") as mock_client_ctx:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(return_value=mock_resp)
                mock_client_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

                await send_telegram_alert("hello world")
                payload = mock_client.post.call_args[1]["json"]
                assert payload["chat_id"] == "123"
                assert payload["text"] == "hello world"
                assert payload["parse_mode"] == "HTML"
                assert payload["disable_web_page_preview"] is True


class TestSendWhaleAlert:
    """Whale transaction alert formatting."""

    @pytest.mark.asyncio
    async def test_eth_receive_message_format(self):
        """ETH receive should show correct emoji and direction."""
        with patch("services.telegram_alerts.send_telegram_alert", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            result = await send_whale_alert(
                chain="ETH", address="0x" + "a" * 40, tx_type="receive",
                amount=100.5, token="ETH", usd_value=350000, tx_hash="0x" + "b" * 64
            )
            assert result is True
            msg = mock_send.call_args[0][0]
            assert "🟣" in msg
            assert "📥" in msg
            assert "RECEIVE" in msg
            assert "100.500000 ETH" in msg
            assert "$350,000.00" in msg

    @pytest.mark.asyncio
    async def test_sol_send_message_format(self):
        """SOL send should show correct emoji and direction."""
        with patch("services.telegram_alerts.send_telegram_alert", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            await send_whale_alert(
                chain="SOL", address="A" * 44, tx_type="send",
                amount=50.0, token="SOL", usd_value=7500, tx_hash="C" * 88
            )
            msg = mock_send.call_args[0][0]
            assert "🟦" in msg
            assert "📤" in msg
            assert "SEND" in msg

    @pytest.mark.asyncio
    async def test_btc_message_format(self):
        """BTC should show yellow emoji."""
        with patch("services.telegram_alerts.send_telegram_alert", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            await send_whale_alert(
                chain="BTC", address="1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa", tx_type="receive",
                amount=1.5, token="BTC", usd_value=97500, tx_hash="abc123"
            )
            msg = mock_send.call_args[0][0]
            assert "🟡" in msg

    @pytest.mark.asyncio
    async def test_unknown_chain_emoji(self):
        """Unknown chain should use white/gray emoji."""
        with patch("services.telegram_alerts.send_telegram_alert", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            await send_whale_alert(
                chain="DOGE", address="D" * 34, tx_type="receive",
                amount=1000, token="DOGE", usd_value=100, tx_hash="x" * 20
            )
            msg = mock_send.call_args[0][0]
            assert "⚪" in msg

    @pytest.mark.asyncio
    async def test_address_truncation(self):
        """Address should be truncated to first 10 and last 6 chars."""
        with patch("services.telegram_alerts.send_telegram_alert", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            address = "0x" + "a" * 40
            await send_whale_alert(
                chain="ETH", address=address, tx_type="receive",
                amount=1, token="ETH", usd_value=100, tx_hash="0x" + "b" * 64
            )
            msg = mock_send.call_args[0][0]
            # Format: address[:10] + "..." + address[-6:]
            assert address[:10] in msg
            assert address[-6:] in msg

    @pytest.mark.asyncio
    async def test_tx_hash_truncation(self):
        """TX hash should be truncated to first 20 chars."""
        with patch("services.telegram_alerts.send_telegram_alert", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            tx_hash = "0x" + "b" * 64
            await send_whale_alert(
                chain="ETH", address="0x" + "a" * 40, tx_type="receive",
                amount=1, token="ETH", usd_value=100, tx_hash=tx_hash
            )
            msg = mock_send.call_args[0][0]
            assert tx_hash[:20] in msg

    @pytest.mark.asyncio
    async def test_delegates_to_send_telegram_alert(self):
        """Should delegate to send_telegram_alert."""
        with patch("services.telegram_alerts.send_telegram_alert", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            result = await send_whale_alert(
                chain="ETH", address="0x" + "a" * 40, tx_type="receive",
                amount=1, token="ETH", usd_value=100, tx_hash="0x" + "b" * 64
            )
            mock_send.assert_called_once()
            assert result is True


class TestSendPortfolioAlert:
    """Portfolio change alert formatting."""

    @pytest.mark.asyncio
    async def test_negative_change_shows_red(self):
        """Negative portfolio change should show red emoji."""
        with patch("services.telegram_alerts.send_telegram_alert", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            await send_portfolio_alert("daily_change", value=-5.2, threshold=3.0)
            msg = mock_send.call_args[0][0]
            assert "🔴" in msg
            assert "-5.20%" in msg

    @pytest.mark.asyncio
    async def test_positive_change_shows_green(self):
        """Positive portfolio change should show green emoji."""
        with patch("services.telegram_alerts.send_telegram_alert", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            await send_portfolio_alert("daily_change", value=10.5, threshold=5.0)
            msg = mock_send.call_args[0][0]
            assert "🟢" in msg
            assert "+10.50%" in msg

    @pytest.mark.asyncio
    async def test_message_contains_type_and_threshold(self):
        """Message should contain alert type and threshold."""
        with patch("services.telegram_alerts.send_telegram_alert", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            await send_portfolio_alert("weekly_drop", value=-15.0, threshold=10.0)
            msg = mock_send.call_args[0][0]
            assert "weekly_drop" in msg
            assert "10.00%" in msg


class TestSendCopyTradeSignal:
    """Copy trade signal formatting."""

    @pytest.mark.asyncio
    async def test_message_format(self):
        """Copy trade signal should contain all key info."""
        with patch("services.telegram_alerts.send_telegram_alert", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            await send_copy_trade_signal(
                chain="ETH", wallet_label="Smart Money #1",
                action="BUY", token="PEPE", amount_usd=50000, confidence=0.85
            )
            msg = mock_send.call_args[0][0]
            assert "🔄" in msg
            assert "Smart Money #1" in msg
            assert "ETH" in msg
            assert "BUY" in msg
            assert "PEPE" in msg
            assert "$50,000.00" in msg
            assert "85%" in msg
            assert "/mirror" in msg

    @pytest.mark.asyncio
    async def test_delegates_to_send_telegram_alert(self):
        """Should delegate to send_telegram_alert."""
        with patch("services.telegram_alerts.send_telegram_alert", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            result = await send_copy_trade_signal(
                chain="SOL", wallet_label="Alpha",
                action="SELL", token="SOL", amount_usd=1000, confidence=0.5
            )
            mock_send.assert_called_once()
            assert result is True
