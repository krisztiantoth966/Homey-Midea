import os

import certifi
from homey.app import App

# Homey Python containers can have an incomplete system CA trust store.
# Point Python/OpenSSL at Mozilla's CA bundle for all HTTPS clients used by the app.
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()


class MideaLanApp(App):
    async def on_init(self) -> None:
        await super().on_init()
        self.log("Midea LAN app initialized with certifi CA bundle")


homey_export = MideaLanApp
