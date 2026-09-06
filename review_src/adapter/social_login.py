"""OIDC authorization-code adapters; provider endpoints are never client supplied."""

import base64
import hashlib
import hmac
import os
from urllib.parse import urlencode, urlsplit
import jwt
import requests

PROVIDERS = {
    "google": ("Google", "https://accounts.google.com/o/oauth2/v2/auth",
               "https://oauth2.googleapis.com/token", "https://www.googleapis.com/oauth2/v3/certs",
               "https://accounts.google.com"),
    "apple": ("Apple", "https://appleid.apple.com/auth/authorize",
              "https://appleid.apple.com/auth/token", "https://appleid.apple.com/auth/keys",
              "https://appleid.apple.com"),
    "line": ("LINE", "https://access.line.me/oauth2/v2.1/authorize",
             "https://api.line.me/oauth2/v2.1/token", "", "https://access.line.me"),
}


def config(provider):
    if provider not in PROVIDERS:
        raise ValueError("不支援此登入方式")
    base = os.getenv("EQUITY_AUTH_PUBLIC_URL", "").rstrip("/")
    parsed = urlsplit(base)
    prefix = "EQUITY_OAUTH_" + provider.upper()
    client = os.getenv(prefix + "_CLIENT_ID", "").strip()
    secret = os.getenv(prefix + "_CLIENT_SECRET", "").strip()
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path or not client or not secret):
        raise ValueError("此登入方式尚未開通")
    callback = ("/api/auth/social/apple/callback" if provider == "apple"
                else "/api/auth/social/" + provider + "/return")
    return {"client": client, "secret": secret, "redirect": base + callback}


def options():
    result = []
    for provider, values in PROVIDERS.items():
        try:
            config(provider)
            enabled = True
        except ValueError:
            enabled = False
        result.append({"provider": provider, "label": values[0], "enabled": enabled})
    return result


def authorization_url(provider, state, nonce, verifier):
    cfg = config(provider)
    params = dict(client_id=cfg["client"], redirect_uri=cfg["redirect"],
                  response_type="code", scope="openid", state=state, nonce=nonce)
    if provider == "apple":
        params["response_mode"] = "form_post"
    else:
        params.update(code_challenge=base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode(),
            code_challenge_method="S256")
    return PROVIDERS[provider][1] + "?" + urlencode(params)


def _json_request(method, url, **kwargs):
    with requests.request(method, url, timeout=(5, 12), allow_redirects=False, **kwargs) as response:
        response.raise_for_status()
        return response.json()


def verify_identity(provider, code, nonce, verifier):
    cfg = config(provider)
    data = dict(grant_type="authorization_code", code=code, client_id=cfg["client"],
                client_secret=cfg["secret"], redirect_uri=cfg["redirect"])
    if provider != "apple":
        data["code_verifier"] = verifier
    tokens = _json_request("POST", PROVIDERS[provider][2], data=data)
    token = tokens["id_token"]
    if provider == "line":
        claims = _json_request("POST", "https://api.line.me/oauth2/v2.1/verify",
                               data=dict(id_token=token, client_id=cfg["client"], nonce=nonce))
    else:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256":
            raise ValueError("登入驗證失敗")
        keys = _json_request("GET", PROVIDERS[provider][3])["keys"]
        matches = [key for key in keys if key.get("kid") == header.get("kid") and key.get("kty") == "RSA"]
        if len(matches) != 1:
            raise ValueError("登入驗證失敗")
        claims = jwt.decode(token, jwt.PyJWK.from_dict(matches[0]).key, algorithms=["RS256"],
                            audience=cfg["client"], issuer=([PROVIDERS[provider][4],"accounts.google.com"] if provider=="google" else PROVIDERS[provider][4]),
                            options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce"]})
    if (not isinstance(claims.get("nonce"), str)
            or not hmac.compare_digest(claims["nonce"], nonce)
            or not isinstance(claims.get("sub"), str) or not claims["sub"]
            or len(claims["sub"]) > 512):
        raise ValueError("登入驗證失敗")
    return claims["sub"]
