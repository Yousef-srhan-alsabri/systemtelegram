from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class CryptoService:
    def __init__(self, keys):
        if not keys:
            raise RuntimeError("SESSION_ENCRYPTION_KEYS is not configured")
        self.cipher = MultiFernet([Fernet(key.encode("ascii")) for key in keys])

    def encrypt(self, value):
        return self.cipher.encrypt(value.encode("utf-8"))

    def decrypt(self, value):
        try:
            return self.cipher.decrypt(value).decode("utf-8")
        except InvalidToken as exc:
            raise RuntimeError("Stored secret cannot be decrypted") from exc
