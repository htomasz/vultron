# SPDX-License-Identifier: GPL-3.0-or-later
"""API mobilne eduVULCAN ("Hebe CE") z kluczem urządzenia zamiast przeglądarki.

Pomysł jest kradziony: podpatrzony w dampud/vulcan (AGPL-3.0), który oparł się
na iris (AGPL-3.0). Ten plik to niezależna implementacja tego protokołu, bez
kopiowania ich kodu.

Przebieg:
1. Zalogowana sesja eduvulcan.pl otwiera AP_URL. Ukryte pole #ap zawiera JSON
   z tokenami JWT profili uczniów, ważnymi około godziny.
2. Generujemy własną parę kluczy RSA i rejestrujemy ją tymi tokenami
   (mobile/register/jwt). Konto ma wtedy nowe "urządzenie".
3. Każde kolejne żądanie podpisujemy kluczem prywatnym. Hasło i przeglądarka
   nie są potrzebne, dopóki eduVULCAN nie unieważni urządzenia (np. po zmianie
   hasła).

Bezpieczeństwo: tokeny z #ap i klucz prywatny dają dostęp do dziennika. Nie
trafiają do logu ani do komunikatów wyjątków; klucz leży w pliku 0600.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

AP_URL = "https://eduvulcan.pl/api/ap"
API_BASE = "https://lekcjaplus.vulcan.net.pl"

# Tożsamość aplikacji, którą serwer sprawdza. Jeśli zacznie odrzucać żądania,
# pierwsze podejrzenie to zbyt stara wersja aplikacji.
APP_NAME = "DzienniczekPlus 3.0"
APP_VERSION = "25.09.24 (G)"
APP_VERSION_CODE = "802"
USER_AGENT = "Dart/3.8 (dart:io)"
DEVICE_OS = "Android"
# Nazwa widoczna na liście urządzeń konta: od razu wiadomo, co usunąć.
DEVICE_MODEL = "Vultron (Home Assistant)"

# Kody koperty oznaczające, że serwer nie zna już naszego certyfikatu.
CERT_GONE_CODES = frozenset({108, 154})

_RE_TENANT = re.compile(r"[a-z0-9_-]{1,64}")
# Hosty, do których wolno wysłać podpisane żądanie.
_ALLOWED_HOST_SUFFIXES = (".vulcan.net.pl",)


class ApError(ValueError):
    """Odpowiedź /api/ap nie nadaje się do rejestracji."""


class HebeError(Exception):
    """Błąd API mobilnego; `code` to kod z koperty (None: błąd transportu)."""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


# ── /api/ap ──────────────────────────────────────────────────────────────

def jwt_claims(token: str) -> dict:
    """Część z danymi tokenu JWT, BEZ weryfikacji podpisu (tylko do odczytu
    tenanta; tokenu i tak używa serwer, który go wystawił)."""
    parts = token.split(".")
    if len(parts) != 3 or not parts[1]:
        raise ApError("token nie ma formatu JWT")
    try:
        raw = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        claims = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as e:
        raise ApError("nieczytelny token JWT") from e
    if not isinstance(claims, dict):
        raise ApError("nieczytelny token JWT")
    return claims


def _load_ap(raw: str) -> dict:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as e:
        raise ApError("odpowiedź /api/ap to nie JSON (sesja wygasła?)") from e
    if not isinstance(data, dict):
        raise ApError("odpowiedź /api/ap ma nieoczekiwany format")
    return data


def parse_ap(raw: str) -> dict[str, list[str]]:
    """JSON z pola #ap -> {tenant: [token, ...]}. Odrzuca wszystko, co nie
    wygląda dokładnie jak poprawna odpowiedź."""
    data = _load_ap(raw)
    if data.get("Success") is not True:
        raise ApError("/api/ap: Success != true")
    if data.get("IsConsentAccepted") is not True:
        raise ApError("/api/ap: zgody na koncie nie są zaakceptowane (eduvulcan.pl)")
    tokens = data.get("Tokens")
    if not isinstance(tokens, list) or not tokens:
        raise ApError("/api/ap: brak tokenów (konto bez uczniów?)")
    by_tenant: dict[str, list[str]] = {}
    for token in tokens:
        if not isinstance(token, str):
            raise ApError("/api/ap: token nie jest tekstem")
        tenant = jwt_claims(token).get("tenant")
        if not isinstance(tenant, str) or not _RE_TENANT.fullmatch(tenant):
            raise ApError("/api/ap: token bez poprawnego pola tenant")
        by_tenant.setdefault(tenant, []).append(token)
    return by_tenant


def ap_summary(raw: str) -> str:
    """Opis odpowiedzi /api/ap do logu: tylko struktura, bez tokenów, imion,
    adresów e-mail i identyfikatorów."""
    try:
        data = _load_ap(raw)
    except ApError as e:
        return str(e)
    tokens = data.get("Tokens") if isinstance(data.get("Tokens"), list) else []
    profiles = []
    for token in tokens:
        try:
            c = jwt_claims(token) if isinstance(token, str) else {}
        except ApError:
            profiles.append("nieczytelny")
            continue
        exp, iat = c.get("exp"), c.get("iat")
        minutes = (exp - iat) // 60 if isinstance(exp, int) and isinstance(iat, int) else "?"
        profiles.append(f"tenant={c.get('tenant')!s:.64} service={c.get('service')!s:.8} "
                        f"caps={c.get('caps')!s:.80} ważny={minutes} min")
    return (f"Success={data.get('Success')} IsConsentAccepted={data.get('IsConsentAccepted')} "
            f"tokeny={len(tokens)} [" + "; ".join(profiles) + "]")


# ── klucz urządzenia ─────────────────────────────────────────────────────

@dataclass
class Device:
    """Para kluczy RSA zarejestrowana w eduVULCAN jako urządzenie."""

    private_key_pem: str
    device_id: str
    registrations: dict[str, str] = field(default_factory=dict)  # tenant -> rest_url

    @classmethod
    def new(cls) -> Device:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode("ascii")
        return cls(private_key_pem=pem, device_id=str(uuid.uuid4()))

    def _private_key(self) -> rsa.RSAPrivateKey:
        key = self.__dict__.get("_key")
        if key is None:
            key = serialization.load_pem_private_key(self.private_key_pem.encode("ascii"), password=None)
            if not isinstance(key, rsa.RSAPrivateKey):
                raise ValueError("klucz urządzenia nie jest kluczem RSA")
            self.__dict__["_key"] = key
        return key

    def _public_pem(self) -> bytes:
        return self._private_key().public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)

    @property
    def certificate(self) -> str:
        """Klucz publiczny (SubjectPublicKeyInfo, DER) w base64 - treść PEM bez nagłówków."""
        der = self._private_key().public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        return base64.b64encode(der).decode("ascii")

    @property
    def fingerprint(self) -> str:
        # MD5 wymaga protokół (identyfikator certyfikatu, nie zabezpieczenie).
        return hashlib.md5(self._public_pem(), usedforsecurity=False).hexdigest()

    def sign(self, data: bytes) -> str:
        sig = self._private_key().sign(data, padding.PKCS1v15(), hashes.SHA256())
        return base64.b64encode(sig).decode("ascii")

    # zapis i odczyt

    def save(self, path: str) -> None:
        """Zapis atomowy z prawami 0600: plik zawiera klucz prywatny."""
        directory = os.path.dirname(path) or "."
        data = json.dumps({"version": 1, "private_key": self.private_key_pem,
                           "device_id": self.device_id, "registrations": self.registrations})
        fd, tmp = tempfile.mkstemp(prefix=".hebe-", dir=directory)  # mkstemp tworzy plik 0600
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @classmethod
    def load(cls, path: str) -> Device | None:
        """None, gdy pliku nie ma. ValueError, gdy jest, ale uszkodzony."""
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return None
        if not (isinstance(data, dict) and data.get("version") == 1
                and isinstance(data.get("private_key"), str)
                and isinstance(data.get("device_id"), str)
                and isinstance(data.get("registrations"), dict)
                and all(isinstance(k, str) and isinstance(v, str)
                        for k, v in data["registrations"].items())):
            raise ValueError(f"uszkodzony plik urządzenia: {path}")
        device = cls(data["private_key"], data["device_id"], dict(data["registrations"]))
        device._private_key()  # od razu wykrywa zły klucz
        return device


# ── żądania ──────────────────────────────────────────────────────────────

def canonical_url(url: str) -> str:
    """Wartość nagłówka vCanonicalUrl: ścieżka od "api/mobile/" (bez zapytania),
    zakodowana w całości i małymi literami."""
    path = urlsplit(url).path
    i = path.find("api/mobile/")
    if i < 0:
        raise ValueError("adres API mobilnego musi zawierać api/mobile/")
    return quote(path[i:], safe="").lower()


def signed_headers(device: Device, url: str, body: str | None, now: datetime) -> dict[str, str]:
    """Nagłówki żądania z podpisem RSA-SHA256 nad vCanonicalUrl [+ Digest] + vDate."""
    headers = {
        "User-Agent": USER_AGENT,
        "vOS": DEVICE_OS,
        "vVersionCode": APP_VERSION_CODE,
        "vAPI": "1",
        "vCanonicalUrl": canonical_url(url),
        "vDate": now.astimezone(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT"),
        "Content-Type": "application/json; charset=utf-8" if body is not None else "application/json",
    }
    signed = ["vCanonicalUrl", "vDate"]
    values = [headers["vCanonicalUrl"], headers["vDate"]]
    if body is not None:
        digest = base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode("ascii")
        signed.insert(1, "Digest")
        values.insert(1, digest)
        headers["Digest"] = f"SHA-256={digest}"
    signature = device.sign("".join(values).encode("utf-8"))
    headers["Signature"] = (f'keyId="{device.fingerprint}",headers="{" ".join(signed)}",'
                            f'algorithm="sha256withrsa",signature=Base64(SHA256withRSA({signature}))')
    return headers


def request_body(envelope: object, now: datetime) -> str:
    return json.dumps({
        "AppName": APP_NAME,
        "AppVersion": APP_VERSION,
        "NotificationToken": "",
        "API": 1,
        "RequestId": str(uuid.uuid4()),
        "Timestamp": int(now.timestamp()),
        "TimestampFormatted": now.strftime("%Y-%m-%d %H:%M:%S"),
        "Envelope": envelope,
    })


def check_rest_url(url: str) -> str:
    """Podpisane żądania idą tylko po https do hostów Vulcana."""
    p = urlsplit(url)
    host = p.hostname or ""
    try:
        port = p.port
    except ValueError:
        port = -1
    if (p.scheme != "https" or p.username or p.password or port not in (None, 443)
            or p.query or p.fragment or not host.endswith(_ALLOWED_HOST_SUFFIXES)):
        raise HebeError(f"niedozwolony adres API mobilnego: {p.scheme}://{host}")
    return url.rstrip("/")


class HebeClient:
    def __init__(self, device: Device, http: httpx.Client):
        self.device = device
        self.http = http

    def call(self, method: str, rest_url: str, endpoint: str, *,
             envelope: object = None, query: dict[str, str] | None = None) -> object:
        url = f"{check_rest_url(rest_url)}/{endpoint}"
        now = datetime.now().astimezone()
        body = request_body(envelope, now) if envelope is not None else None
        headers = signed_headers(self.device, url, body, now)
        try:
            r = self.http.request(method, url, params=query, headers=headers,
                                  content=body.encode("utf-8") if body is not None else None,
                                  follow_redirects=False)
        except httpx.HTTPError as e:
            raise HebeError(f"{method} {endpoint}: {type(e).__name__}") from e
        if r.status_code != 200:
            raise HebeError(f"{method} {endpoint}: HTTP {r.status_code}")
        try:
            data = r.json()
        except ValueError:
            raise HebeError(f"{method} {endpoint}: odpowiedź nie jest JSON") from None
        status = data.get("Status") if isinstance(data, dict) else None
        if not isinstance(status, dict) or not isinstance(status.get("Code"), int):
            raise HebeError(f"{method} {endpoint}: odpowiedź bez statusu")
        if status["Code"] != 0:
            message = str(status.get("Message") or "")[:200]
            raise HebeError(f"{method} {endpoint}: kod {status['Code']}: {message}", status["Code"])
        return data.get("Envelope")

    def register(self, tenant: str, tokens: list[str]) -> str:
        """Rejestruje urządzenie tokenami z /api/ap; zwraca rest_url tenanta."""
        if not _RE_TENANT.fullmatch(tenant):
            raise HebeError("niepoprawny tenant")
        rest_url = f"{API_BASE}/{tenant}/api"
        self.call("POST", rest_url, "mobile/register/jwt", envelope={
            "OS": DEVICE_OS,
            "Certificate": self.device.certificate,
            "CertificateType": "RSA_PEM",
            "DeviceModel": DEVICE_MODEL,
            "SelfIdentifier": self.device.device_id,
            "CertificateThumbprint": self.device.fingerprint,
            "Tokens": tokens,
        })
        self.device.registrations[tenant] = rest_url
        return rest_url

    def accounts(self, rest_url: str) -> list[dict]:
        """Konta (uczniowie) widoczne dla tego urządzenia."""
        envelope = self.call("GET", rest_url, "mobile/register/hebe", query={"mode": "2"})
        if not isinstance(envelope, list):
            raise HebeError("mobile/register/hebe: odpowiedź nie jest listą")
        return [a for a in envelope if isinstance(a, dict)]
