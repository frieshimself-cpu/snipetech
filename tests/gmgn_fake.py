"""A stand-in for openapi.gmgn.ai in tests: every documented read answers with its example from tests/fixtures/gmgn/.

The examples are in the shape GMGN's own field tables give (the public gmgn-skills repository, 29 Oct 2026); the
addresses and numbers are made up, because no answer of a real key was available on 12 Nov 2026. One file is a real
answer: error_auth_invalid.json is what openapi.gmgn.ai said that day to a request without a key (HTTP 401)."""

import json
from pathlib import Path

import httpx

KEY = "gmgn-test-key-0123456789abcdef"
FIX = Path(__file__).parent / "fixtures" / "gmgn"
K1 = "Dc4gM8pJBGrNNxBJxGBj5DEqvSWAER1bRwb18xw1Ku2D"  # "Cupsey" (@cupseyy): on the KOL list and the smart-money list
K2 = "7nHfa5SnxPJnryZxLoAcv2pjhivKJgp89mH2ENFXJ2Tb"  # @frankdegods, no name: KOL list
K3 = "F3eock2UoEAdRDMQ7kYZuj6eNUxAPRHidyGoAZ2jc9V2"  # KOL list, neither name nor handle
S1 = "9MFPQ8VzmPNuaK3kJMwGdwasCsSp1eMXmYCYgWtzPhPL"  # smart money, nobody's name
S2 = "CAKzhjHor8i7tXvFpPRUxiNB7wXZubJ7fWCxGLqHnz3e"  # "Latuche" (@Latuche95): smart money
M1 = "CcnpRLK4pnA35KjAd2aGZr4GAat16h8oTTKQq9pSpump"
M2 = "o5NbBLfzj32SGMF8NA72aE8iN43VsdEHob8EKYqjpump"
M3 = "8jqiLmWixCrFcoVDPKBHf5RfrvNeZ5t8k38brSrPAebz"
POOL = "6JWo6VtZqN5hs6qsPEjk23VN5YEz8SL76rkuwYabjNyq"
DEV = "EoNVqoCkE4cADjRfHP5hHFfuaqEaVUK39MKFzoLnXPv"
RAT = "7PJADWqzJdRiFNCqCGp4LDiS7VNsShcgBZWMoMTGwNGA"
SIDE = "4ZrJ6r76hRgMXzSaU8C64ocRafQgVY3im2iKgRivDBKd"  # a second wallet a search for "cupseyy" finds
FAN = "FGomXMFp4WSaZefwmt2UTsLmRZPLPKSqLS6gex8m8jwb"  # @cupseyy_fan: a search is fuzzy
T0 = 1794502000


def fixture(name: str) -> dict:
    return json.loads((FIX / name).read_text())


def _stats_of(req: httpx.Request) -> str:
    """Only Cupsey's wallet has a card with a name; the others answer in the plain shape."""
    return "wallet_stats.json" if req.url.params.get("wallet_address") == K1 else "wallet_stats_pnl_stat.json"


ROUTES = {
    "/v1/user/kol": "kol.json",
    "/v1/user/smartmoney": "smartmoney.json",
    "/v1/user/wallet_stats": _stats_of,
    "/v1/user/wallet_profits": "wallet_profits.json",
    "/v1/user/wallet_activity": "wallet_activity.json",
    "/v1/token/info": "token_info.json",
    "/v1/token/security": "token_security.json",
    "/v1/market/token_top_holders": "token_holders.json",
    "/v1/market/token_top_traders": "token_traders.json",
    "/v1/market/rank": "rank.json",
    "/v1/trenches": "trenches.json",
    "/v1/market/token_kline": "kline.json",
    "/v1/market/search": "search.json",
    "/v1/trade/gas_price": "gas_price.json",
    "/v1/user/created_tokens": "created_tokens.json",
}
PRO = {"x-ratelimit-limit": "50", "x-ratelimit-remaining": "47"}


def fake_gmgn(seen: list | None = None, routes: dict | None = None) -> httpx.MockTransport:
    """A transport that answers every documented read with its example and keeps what was asked in `seen`. A route in
    `routes` is a fixture's name, a ready response, or a function of the request that gives either."""
    table = dict(ROUTES, **(routes or {}))

    def handler(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(req)
        answer = table.get(req.url.path)
        if callable(answer):
            answer = answer(req)
        if answer is None:
            return httpx.Response(404, json={"code": 404, "error": "NOT_FOUND", "message": "no such route"})
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, json=fixture(answer), headers=PRO)

    return httpx.MockTransport(handler)


def refused(status: int, error: str, message: str = "refused") -> httpx.Response:
    return httpx.Response(status, json={"code": status, "error": error, "message": message})
