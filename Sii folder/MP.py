import requests

##no probado todavía

BASE_URI = "https://servicios-prd.mercadopublico.cl"


class PublicMarketClient:
    def __init__(self, tin: str):
        self.tin = tin
        self.access_token = None
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        self.access_token = self._get_auth_token()
        self.session.headers["Authorization"] = f"Bearer {self.access_token}"

    def _get_auth_token(self) -> str:
        response = self.session.get(f"{BASE_URI}/v1/auth/publico")
        response.raise_for_status()
        return response.json()["payload"]["access_token"]

    def get_business_situation(self) -> dict:
        response = self.session.get(f"{BASE_URI}/v3/proveedor/estado/{self.tin}/0")
        if not response.ok:
            raise Exception(f"PublicMarketError {response.status_code}: {response.text}")
        return response.json().get("payload") or {}

    def get_business_address(self) -> dict | None:
        response = self.session.get(f"{BASE_URI}/v1/proveedor/ficha/direccion/{self.tin}/0")
        if not response.ok and response.status_code != 404:
            raise Exception(f"PublicMarketError {response.status_code}: {response.text}")
        return response.json().get("payload")


# ── Uso ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    client = PublicMarketClient(tin="76.123.456-7")

    situacion = client.get_business_situation()
    print("Situación:", situacion)

    direccion = client.get_business_address()
    print("Dirección:", direccion)
