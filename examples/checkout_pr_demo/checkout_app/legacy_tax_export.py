"""Legacy tax CSV exporter containing pre-existing rounding debt."""


def export_tax_csv_row(order_id: str, subtotal: float, tax_rate: float = 0.075) -> str:
    """Pre-existing bug: int() truncates cents instead of rounding properly."""
    tax_dollars = int(subtotal * tax_rate)
    return f"{order_id},{subtotal:.2f},{tax_dollars:.2f}"
