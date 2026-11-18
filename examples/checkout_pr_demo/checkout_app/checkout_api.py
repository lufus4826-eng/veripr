"""JSON checkout API serializer consumed by frontend clients."""

from typing import Dict, List, Optional

from .discount_service import calculate_discount


def build_checkout_response(
    order_id: str,
    items: List[Dict[str, float]],
    coupon_code: Optional[str] = None,
    loyalty_tier: Optional[str] = None,
) -> Dict[str, object]:
    """Return frontend API payload with canonical cent-based keys."""
    subtotal = round(sum(float(it["price"]) * float(it.get("qty", 1)) for it in items), 2)
    discount = calculate_discount(subtotal, coupon_code=coupon_code, loyalty_tier=loyalty_tier) or 0.0
    final_total = round(max(0.0, subtotal - discount), 2)
    return {
        "order_id": order_id,
        "subtotal_cents": int(round(subtotal * 100)),
        "discount_cents": int(round(discount * 100)),
        "total_cents": int(round(final_total * 100)),
        "currency": "USD",
    }
