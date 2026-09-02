"""
Host Bot — Helper Utilities
"""


def format_price(price) -> str:
    """Format price with ₹ symbol. Shows integer if whole number."""
    try:
        price = float(price)
    except (ValueError, TypeError):
        return "₹0"
    if price == int(price):
        return f"₹{int(price)}"
    return f"₹{price:.2f}"


def truncate(text: str, length: int = 50) -> str:
    """Truncate text to specified length with ellipsis."""
    if not text:
        return ""
    if len(text) > length:
        return text[: length - 3] + "..."
    return text


def escape_md(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    special_chars = [
        "_", "*", "[", "]", "(", ")", "~", "`",
        ">", "#", "+", "-", "=", "|", "{", "}", ".", "!",
    ]
    for char in special_chars:
        text = text.replace(char, f"\\{char}")
    return text
