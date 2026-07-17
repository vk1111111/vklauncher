from __future__ import annotations

import os
import re
import time
import uuid
import webbrowser
from dataclasses import dataclass, asdict
from typing import Any, Callable, Optional

import requests

from . import config

MS_DEVICE_CODE_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
MS_TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
XBL_AUTH_URL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_AUTH_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"
MC_LOGIN_URL = "https://api.minecraftservices.com/authentication/login_with_xbox"
MC_PROFILE_URL = "https://api.minecraftservices.com/minecraft/profile"
MC_ENTITLEMENTS_URL = "https://api.minecraftservices.com/entitlements/mcstore"

DEVICE_CODE_SCOPE = "XboxLive.signin offline_access"

DEFAULT_MS_CLIENT_ID = "c36a9fb6-4f2a-41ff-90bd-ae7cc92031eb"

_CLIENT_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _looks_like_client_id(value: str) -> bool:
    return bool(_CLIENT_ID_RE.match(value.strip()))


def resolve_ms_client_id(settings: dict | None = None) -> str:
    if settings is None:
        settings = config.load_settings()
    override = (settings.get("ms_client_id") or "").strip()
    if override and _looks_like_client_id(override):
        return override
    env = os.environ.get("MC_TUI_MS_CLIENT_ID", "").strip()
    if env and _looks_like_client_id(env):
        return env
    return DEFAULT_MS_CLIENT_ID


class AuthError(RuntimeError):
    pass


@dataclass
class Account:
    kind: str
    username: str
    uuid: str
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0 

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def is_expired(self) -> bool:
        return self.kind == "microsoft" and time.time() > self.expires_at - 60


def offline_uuid(username: str) -> str:
    return str(uuid.uuid3(uuid.NAMESPACE_URL, f"OfflinePlayer:{username}"))


def _load_all() -> dict[str, dict]:
    return config.load_json(config.ACCOUNTS_FILE, {"accounts": {}, "active": ""})


def _save_all(data: dict) -> None:
    config.save_json(config.ACCOUNTS_FILE, data)


def list_accounts() -> list[Account]:
    data = _load_all()
    return [Account(**v) for v in data.get("accounts", {}).values()]


def get_active_account() -> Optional[Account]:
    data = _load_all()
    active = data.get("active", "")
    accounts = data.get("accounts", {})
    if active in accounts:
        return Account(**accounts[active])
    return None


def set_active_account(username: str) -> None:
    data = _load_all()
    if username not in data.get("accounts", {}):
        raise ValueError(f"No such account: {username}")
    data["active"] = username
    _save_all(data)


def save_account(account: Account, make_active: bool = True) -> None:
    data = _load_all()
    data.setdefault("accounts", {})[account.username] = account.to_dict()
    if make_active or not data.get("active"):
        data["active"] = account.username
    _save_all(data)


def remove_account(username: str) -> None:
    data = _load_all()
    data.get("accounts", {}).pop(username, None)
    if data.get("active") == username:
        data["active"] = ""
    _save_all(data)


def add_offline_account(username: str, make_active: bool = True) -> Account:
    username = username.strip()
    if not (3 <= len(username) <= 16):
        raise ValueError("Minecraft usernames must be 3-16 characters.")
    acc = Account(kind="offline", username=username, uuid=offline_uuid(username))
    save_account(acc, make_active)
    return acc

# minecraft services oauth

def start_device_code(client_id: str | None = None) -> dict:
    client_id = client_id or resolve_ms_client_id()
    if not client_id:
        raise AuthError("No Microsoft OAuth client id available.")
    resp = requests.post(
        MS_DEVICE_CODE_URL,
        data={"client_id": client_id, "scope": DEVICE_CODE_SCOPE},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def open_device_code_browser(payload: dict) -> bool:
    url = payload.get("verification_uri_complete")
    if not url:
        base = payload.get("verification_uri") or "https://www.microsoft.com/link"
        code = payload.get("user_code", "")
        if code:
            sep = "&" if "?" in base else "?"
            url = f"{base}{sep}otc={code}"
        else:
            url = base
    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


def poll_device_code(
    client_id: str | None,
    device_code: str,
    interval: int,
    expires_in: int,
    on_tick: Optional[Callable[[int], None]] = None,
) -> dict:
    client_id = client_id or resolve_ms_client_id()
    waited = 0
    while waited < expires_in:
        time.sleep(interval)
        waited += interval
        if on_tick:
            on_tick(waited)
        resp = requests.post(
            MS_TOKEN_URL,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": client_id,
                "device_code": device_code,
            },
            timeout=30,
        )
        payload = resp.json()
        if resp.status_code == 200:
            return payload
        err = payload.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5
            continue
        if err == "expired_token":
            raise AuthError("The login code expired. Please try again.")
        if err == "authorization_declined":
            raise AuthError("Sign-in was declined.")
        raise AuthError(f"Microsoft sign-in failed: {payload}")
    raise AuthError("Timed out waiting for sign-in.")


def refresh_ms_token(client_id: str | None, refresh_token: str) -> dict:
    client_id = client_id or resolve_ms_client_id()
    resp = requests.post(
        MS_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
            "scope": DEVICE_CODE_SCOPE,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _xbox_live_auth(ms_access_token: str) -> tuple[str, str]:
    resp = requests.post(
        XBL_AUTH_URL,
        json={
            "Properties": {
                "AuthMethod": "RPS",
                "SiteName": "user.auth.xboxlive.com",
                "RpsTicket": f"d={ms_access_token}",
            },
            "RelyingParty": "http://auth.xboxlive.com",
            "TokenType": "JWT",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data["Token"]
    user_hash = data["DisplayClaims"]["xui"][0]["uhs"]
    return token, user_hash


def _xsts_auth(xbl_token: str) -> tuple[str, str]:
    resp = requests.post(
        XSTS_AUTH_URL,
        json={
            "Properties": {"SandboxId": "RETAIL", "UserTokens": [xbl_token]},
            "RelyingParty": "rp://api.minecraftservices.com/",
            "TokenType": "JWT",
        },
        timeout=30,
    )
    if resp.status_code == 401:
        payload = resp.json()
        code = payload.get("XErr")
        messages = {
            2148916233: "This Microsoft account has no Xbox Live profile. "
            "Sign in to xbox.com once to create one, then try again.",
            2148916235: "Xbox Live is not available in this account's region.",
            2148916236: "This account needs adult verification (South Korea).",
            2148916237: "This account needs adult verification (South Korea).",
            2148916238: "This is a child account. Add it to a Family group and try again.",
        }
        raise AuthError(messages.get(code, f"Xbox Live authorization failed ({code})."))
    resp.raise_for_status()
    data = resp.json()
    token = data["Token"]
    user_hash = data["DisplayClaims"]["xui"][0]["uhs"]
    return token, user_hash


def _minecraft_login(xsts_token: str, user_hash: str) -> dict:
    resp = requests.post(
        MC_LOGIN_URL,
        json={"identityToken": f"XBL3.0 x={user_hash};{xsts_token}"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _fetch_profile(mc_access_token: str) -> dict:
    resp = requests.get(
        MC_PROFILE_URL,
        headers={"Authorization": f"Bearer {mc_access_token}"},
        timeout=30,
    )
    if resp.status_code == 404:
        raise AuthError(
            "This Microsoft account does not own Minecraft: Java Edition."
        )
    resp.raise_for_status()
    return resp.json()


def complete_login_from_ms_tokens(ms_token_payload: dict) -> Account:
    ms_access_token = ms_token_payload["access_token"]
    refresh_token = ms_token_payload.get("refresh_token", "")

    xbl_token, _ = _xbox_live_auth(ms_access_token)
    xsts_token, user_hash = _xsts_auth(xbl_token)
    mc_login = _minecraft_login(xsts_token, user_hash)
    mc_access_token = mc_login["access_token"]
    expires_in = mc_login.get("expires_in", 86400)

    profile = _fetch_profile(mc_access_token)

    return Account(
        kind="microsoft",
        username=profile["name"],
        uuid=profile["id"],
        access_token=mc_access_token,
        refresh_token=refresh_token,
        expires_at=time.time() + expires_in,
    )


def ensure_fresh(account: Account, client_id: str | None = None) -> Account:
    if account.kind != "microsoft" or not account.is_expired:
        return account
    if not account.refresh_token:
        raise AuthError("Session expired and no refresh token is available. Please sign in again.")
    ms_payload = refresh_ms_token(client_id or resolve_ms_client_id(), account.refresh_token)
    fresh = complete_login_from_ms_tokens(ms_payload)
    save_account(fresh, make_active=False)
    return fresh
