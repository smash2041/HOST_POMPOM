"""
Dynamic UPI QR Code Generator
Generates QR codes for UPI payment deep links.
Returns BytesIO PNG buffer for direct Telegram sending.
"""
import io
import qrcode
from urllib.parse import quote


def generate_upi_qr(upi_id: str, amount: float, name: str = "VIP Bot") -> io.BytesIO:
    """
    Generate a UPI payment QR code as a BytesIO PNG image.

    Args:
        upi_id: UPI VPA (e.g., 'merchant@upi')
        amount: Payment amount in INR
        name: Payee display name

    Returns:
        BytesIO buffer containing the QR code PNG image
    """
    # Build UPI deep link URL
    upi_url = (
        f"upi://pay?"
        f"pa={upi_id}"
        f"&pn={quote(name)}"
        f"&am={amount:.2f}"
        f"&cu=INR"
        f"&tn={quote(f'Payment for {name}')}"
    )

    # Generate QR with high error correction for reliable scanning
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr.add_data(upi_url)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    # Save to in-memory buffer (no disk I/O)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    buffer.name = "upi_qr.png"
    return buffer
