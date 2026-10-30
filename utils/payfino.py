"""Small Payfino PayIn client. Credentials are read from hosted-bot settings."""
import logging

import httpx

BASE_URL = "https://api.payfino.pro"
logger = logging.getLogger(__name__)


class PayfinoError(Exception):
    pass


class PayfinoClient:
    def __init__(self, authorization_key, module_secret, merchant_id, password):
        self.authorization_key = authorization_key
        self.module_secret = module_secret
        self.merchant_id = merchant_id
        self.password = password
        self.token = None

    async def _login(self, client):
        response = await client.post(
            f"{BASE_URL}/api/merchant/login",
            json={"merchantId": self.merchant_id, "password": self.password},
        )
        response.raise_for_status()
        data = response.json()
        self.token = data.get("token") if data.get("success") else None
        if not self.token:
            raise PayfinoError("Payfino login failed")

    async def _request(self, method, path, **kwargs):
        async with httpx.AsyncClient(timeout=25) as client:
            for attempt in range(2):
                if not self.token:
                    await self._login(client)
                headers = {
                    "Authorization": f"Bearer {self.token}",
                    "X-Authorization-Key": self.authorization_key,
                    "X-Module-Secret": self.module_secret,
                }
                response = await client.request(
                    method, f"{BASE_URL}{path}", headers=headers, **kwargs
                )
                if response.status_code == 401 and attempt == 0:
                    self.token = None
                    continue
                response.raise_for_status()
                payload = response.json()
                if not payload.get("success", True):
                    raise PayfinoError("Payfino request was not successful")
                return payload
        raise PayfinoError("Payfino authorization failed")

    async def create_payin(self, payload):
        return await self._request("POST", "/api/payin/order/create", json=payload)

    async def verify_payment(self, order_id):
        return await self._request(
            "POST", "/api/payin/verify-payment", json={"order_id": order_id}
        )


async def client_from_bot_db(db):
    keys = (
        "payfino_authorization_key", "payfino_module_secret",
        "payfino_merchant_id", "payfino_password",
    )
    values = [await db.get_setting(key) for key in keys]
    if not all(values):
        return None
    return PayfinoClient(*values)


def make_qr(data):
    """Create an image QR for Payfino's returned UPI intent/link."""
    import io
    import qrcode

    image = qrcode.make(data)
    output = io.BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    output.name = "payfino_payment.png"
    return output
