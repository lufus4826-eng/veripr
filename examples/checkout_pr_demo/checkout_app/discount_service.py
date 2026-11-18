"""Discount service (Base state prior to PR edits)."""

from typing import Optional


COUPONS = {
    "SAVE10": 10.0,
    "VIP20": 20.0,
}


def calculate_discount(
    subtotal: float,
    coupon_code: Optional[str] = None,
    loyalty_tier: Optional[str] = None,
) -> float:
    """Return numeric discount amount for the given subtotal and optional coupon."""
    if subtotal <= 0:
        return 0.0
    if not coupon_code:
        return 0.0
    rate = COUPONS.get(coupon_code.upper(), 0.0)
    return round(min(subtotal, rate), 2)
