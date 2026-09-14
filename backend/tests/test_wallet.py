"""Unit tests for wallet.py (pure functions, no DB/network)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wallet as w


def test_price_lookup():
    assert w.get_price("full_analysis") == 0.25
    assert w.get_price("compare") == 0.39
    assert w.get_price("portfolio_optimize") == 0.05
    assert w.get_price("unknown_action") == 0.0


def test_sufficient_balance():
    assert w.has_sufficient_balance(0.25, "full_analysis") is True
    assert w.has_sufficient_balance(0.24, "full_analysis") is False
    assert w.has_sufficient_balance(0.0, "full_analysis") is False


def test_no_cache_always_charges():
    assert w.should_charge_for_recheck(None, 100.0, 100.0) is True
    assert w.should_charge_for_recheck({"decision": "BUY"}, None, 100.0) is True
    assert w.should_charge_for_recheck({"decision": "BUY"}, 100.0, None) is True
    assert w.should_charge_for_recheck({"decision": "BUY"}, 100.0, 0.0) is True


def test_small_price_move_is_free():
    cached = {"decision": "BUY", "target_price": 120.0, "stop_loss": 90.0}
    # 0.5% move, well under the 1.5% threshold, price nowhere near target/stop
    assert w.should_charge_for_recheck(cached, 100.5, 100.0) is False


def test_price_move_past_threshold_charges():
    cached = {"decision": "BUY", "target_price": 120.0, "stop_loss": 90.0}
    # 2% move
    assert w.should_charge_for_recheck(cached, 102.0, 100.0) is True


def test_buy_hitting_target_charges_even_with_small_move_from_reference():
    # reference price was recorded near the target already (verdict aging),
    # and live price has now reached the target itself
    cached = {"decision": "BUY", "target_price": 101.0, "stop_loss": 90.0}
    assert w.should_charge_for_recheck(cached, 101.0, 100.5) is True


def test_buy_hitting_stop_charges():
    cached = {"decision": "BUY", "target_price": 120.0, "stop_loss": 99.0}
    assert w.should_charge_for_recheck(cached, 99.0, 100.0) is True


def test_sell_direction_is_inverted():
    cached = {"decision": "SELL", "target_price": 90.0, "stop_loss": 110.0}
    # price dropped to target -> charge (thesis realized, stale to reuse)
    assert w.should_charge_for_recheck(cached, 90.0, 100.5) is True
    # price rose to stop -> charge
    assert w.should_charge_for_recheck(cached, 110.0, 100.5) is True
    # small quiet move, nowhere near either level -> free
    assert w.should_charge_for_recheck(cached, 100.8, 100.5) is False


def test_hold_with_no_levels_only_uses_price_move():
    cached = {"decision": "HOLD", "target_price": None, "stop_loss": None}
    assert w.should_charge_for_recheck(cached, 100.3, 100.0) is False
    assert w.should_charge_for_recheck(cached, 103.0, 100.0) is True


def test_new_balance_after_charge_never_negative():
    assert w.new_balance_after_charge(0.25, "full_analysis") == 0.0
    assert w.new_balance_after_charge(0.10, "full_analysis") == 0.0
    assert w.new_balance_after_charge(1.00, "full_analysis") == 0.75


def test_admin_phone_matching():
    admins = ["+918446307145"]
    assert w.is_admin_phone("+918446307145", admins) is True
    assert w.is_admin_phone(" +918446307145 ", admins) is True
    assert w.is_admin_phone("+918446307146", admins) is False
    assert w.is_admin_phone("918446307145", admins) is False  # must be E.164
    assert w.is_admin_phone(None, admins) is False
    assert w.is_admin_phone("", admins) is False


def test_no_admins_configured_means_nobody_is_admin():
    assert w.is_admin_phone("+918446307145", []) is False
    assert w.is_admin_phone("+918446307145", ["", "  "]) is False
