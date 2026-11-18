"""1-hop caller module that computes receipts using calculate_discount."""

from typing import Dict, List, Optional

from .discount_service import calculate_discount


def generate_receipt(
    order_id: str,
    items: List[Dict[str, float]],
    coupon_code: Optional[str] = None,
    loyalty_tier: Optional[str] = None,
) -> Dict[str, object]:
    """Build itemized receipt; expects numeric return from calculate_discount."""
    subtotal = round(sum(float(it["price"]) * float(it.get("qty", 1)) for it in items), 2)
    discount = calculate_discount(subtotal, coupon_code=coupon_code, loyalty_tier=loyalty_tier)
    final_total = round(subtotal - discount, 2)
    return {
        "order_id": order_id,
        "subtotal": subtotal,
        "discount": discount,
        "final_total": final_total,
    }
