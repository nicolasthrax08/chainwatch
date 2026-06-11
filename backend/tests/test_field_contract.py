#!/usr/bin/env python3
"""
Tests for the field_contract.py frontend-backend field validator.
Verifies that field contract checks correctly detect mismatches.
"""
import os
import sys
import tempfile
import textwrap

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "services"))
import field_contract as fc


class TestFieldAccessExtraction:

    def test_extracts_simple_field_access(self, tmp_path):
        """Simple obj.field access is extracted."""
        jsx_dir = tmp_path / "src"
        jsx_dir.mkdir()
        (jsx_dir / "Dashboard.jsx").write_text(textwrap.dedent("""
            function Dashboard({ data }) {
                return <div>{data.portfolio.total_value_usd}</div>
            }
        """))
        accesses = fc.find_frontend_field_accesses(str(jsx_dir))
        paths = [a.access_path for a in accesses]
        assert any("data.portfolio" in p for p in paths)

    def test_extracts_wallet_field_access(self, tmp_path):
        """wallet.balance_usd access is extracted via w alias."""
        jsx_dir = tmp_path / "src"
        jsx_dir.mkdir()
        (jsx_dir / "Dashboard.jsx").write_text(textwrap.dedent("""
            {wallets.map(w => (
                <div key={w.id}>{w.balance_usd}</div>
            ))}
        """))
        accesses = fc.find_frontend_field_accesses(str(jsx_dir))
        assert any(a.access_path == "w.balance_usd" for a in accesses)

    def test_skips_common_builtins(self, tmp_path):
        """console.log, Math.round etc. are skipped."""
        jsx_dir = tmp_path / "src"
        jsx_dir.mkdir()
        (jsx_dir / "Dashboard.jsx").write_text(textwrap.dedent("""
            console.log(data.length)
            Math.round(value)
        """))
        accesses = fc.find_frontend_field_accesses(str(jsx_dir))
        assert not any(a.object_name == "console" for a in accesses)
        assert not any(a.object_name == "Math" for a in accesses)

    def test_skips_jsx_attributes(self, tmp_path):
        """JSX attributes like className, onClick are skipped."""
        jsx_dir = tmp_path / "src"
        jsx_dir.mkdir()
        (jsx_dir / "Dashboard.jsx").write_text(textwrap.dedent("""
            <div className="card" onClick={handleClick}>
                {wallet.balance_usd}
            </div>
        """))
        accesses = fc.find_frontend_field_accesses(str(jsx_dir))
        assert not any(a.object_name == "className" for a in accesses)
        assert not any(a.object_name == "onClick" for a in accesses)
        # But wallet.balance_usd should be extracted
        assert any(a.access_path == "wallet.balance_usd" for a in accesses)


class TestContractValidation:

    def test_passes_when_field_exists_in_endpoint(self, tmp_path):
        """All frontend-accessed fields exist in endpoint responses → no violations."""
        jsx_dir = tmp_path / "src"
        jsx_dir.mkdir()
        (jsx_dir / "Dashboard.jsx").write_text(textwrap.dedent("""
            {wallets.map(w => (
                <div key={w.id}>{w.balance_usd}</div>
            ))}
        """))
        accesses = fc.find_frontend_field_accesses(str(jsx_dir))
        violations, unmatched = fc.validate_contracts(accesses)
        # balance_usd is in the wallet nested shape
        balance_usd_violations = [v for v in violations if v.field_name == "balance_usd"]
        assert len(balance_usd_violations) == 0

    def test_detects_missing_field(self, tmp_path):
        """Frontend accesses a field the backend doesn't return → violation."""
        import copy
        original = copy.deepcopy(fc.ENDPOINT_RESPONSES)
        try:
            # Remove balance_hkd from ALL endpoint shapes (wallet_meta and wallet)
            for ep in fc.ENDPOINT_RESPONSES:
                for rk in ep.nested:
                    ep.nested[rk].discard("balance_hkd")
                ep.fields.discard("balance_hkd")

            jsx_dir = tmp_path / "src"
            jsx_dir.mkdir()
            (jsx_dir / "Dashboard.jsx").write_text(textwrap.dedent("""
                {wallets.map(w => (
                    <div key={w.id}>{w.balance_hkd}</div>
                ))}
            """))
            accesses = fc.find_frontend_field_accesses(str(jsx_dir))
            violations, unmatched = fc.validate_contracts(accesses)
            # Should detect that balance_hkd is missing
            balance_hkd_violations = [v for v in violations if v.field_name == "balance_hkd"]
            assert len(balance_hkd_violations) >= 1
        finally:
            fc.ENDPOINT_RESPONSES.clear()
            fc.ENDPOINT_RESPONSES.extend(original)

    def test_unmapped_object_goes_to_unmatched(self, tmp_path):
        """Unknown object names go to unmatched, not violations."""
        jsx_dir = tmp_path / "src"
        jsx_dir.mkdir()
        (jsx_dir / "Dashboard.jsx").write_text(textwrap.dedent("""
                <div>{unknown_obj.some_field}</div>
        """))
        accesses = fc.find_frontend_field_accesses(str(jsx_dir))
        violations, unmatched = fc.validate_contracts(accesses)
        # unknown_obj is not in OBJECT_NAME_MAP, so it should be unmatched
        assert any(a.object_name == "unknown_obj" for a in unmatched)
        # But not a violation
        assert not any(v.object_name == "unknown_obj" for v in violations)


class TestWSFieldValidation:

    def test_ws_signal_fields_in_shape(self, tmp_path):
        """WebSocket signal.created fields are in WS_MESSAGE_SHAPES."""
        ws_fields = fc.WS_MESSAGE_SHAPES.get("signal", {}).get("created", set())
        assert "id" in ws_fields
        assert "token_symbol" in ws_fields
        assert "action" in ws_fields
        assert "confidence_score" in ws_fields

    def test_ws_alert_fields_in_shape(self, tmp_path):
        """WebSocket alert.fired fields are in WS_MESSAGE_SHAPES."""
        ws_fields = fc.WS_MESSAGE_SHAPES.get("alert", {}).get("fired", set())
        assert "alert_id" in ws_fields
        assert "rule_type" in ws_fields
        assert "message" in ws_fields


class TestObjectNameMap:

    def test_wallet_maps_to_both_shapes(self):
        """'wallet' object name maps to both wallet_meta and wallet shapes."""
        assert "wallet_meta" in fc.OBJECT_NAME_MAP.get("wallet", [])
        assert "wallet" in fc.OBJECT_NAME_MAP.get("wallet", [])

    def test_signal_maps_to_signal_shape(self):
        """'signal' object name maps to signal shape."""
        assert "signal" in fc.OBJECT_NAME_MAP.get("signal", [])

    def test_s_maps_to_both_signal_and_suggestion(self):
        """'s' maps to both signal and suggestion (context-dependent)."""
        assert "signal" in fc.OBJECT_NAME_MAP.get("s", [])
        assert "suggestion" in fc.OBJECT_NAME_MAP.get("s", [])


class TestIntegrationFieldContract:

    def test_field_contract_on_real_codebase(self):
        """Run field contract validation on the actual codebase — should pass."""
        base = os.path.dirname(os.path.abspath(__file__))
        frontend_base = os.path.join(base, "..", "..", "frontend", "src")

        if not os.path.isdir(frontend_base):
            pytest.skip("Frontend directory not found")

        accesses = fc.find_frontend_field_accesses(frontend_base)
        violations, unmatched = fc.validate_contracts(accesses)
        # The real codebase should have zero violations
        assert len(violations) == 0, (
            f"Field contract violations: "
            + "; ".join(
                f"{v.frontend_file}:{v.frontend_line} {v.object_name}.{v.field_name}"
                for v in violations
            )
        )
