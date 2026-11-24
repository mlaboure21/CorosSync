"""Minimal client for the (unofficial) COROS Training Hub web API.

Endpoints were taken from the Training Hub web app (training.coros.com) and
cross-checked against the open-source projects corosexport and coros-mcp.
Nothing here is documented by COROS, so expect breakage when they change it.
"""

from __future__ import annotations

import hashlib
import json
import logging

import bcrypt
import requests

log = logging.getLogger(__name__)

REGIONS = {
    "eu": "https://teameuapi.coros.com",
    "global": "https://teamapi.coros.com",
}

FILE_TYPES = {"csv": 0, "gpx": 1, "kml": 2, "tcx": 3, "fit": 4}


class CorosError(Exception):
    pass


class CorosAuthError(CorosError):
    pass


class CorosClient:
    def __init__(self, email: str, password: str, region: str = "eu", timeout: int = 30):
        self.base = REGIONS[region]
        self.email = email
        self._pwd_md5 = hashlib.md5(password.encode()).hexdigest()
        self.timeout = timeout
        self.token: str | None = None
        self.user_id: str | None = None
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://training.coros.com",
            "Referer": "https://training.coros.com/",
        })

    # ---------------------------------------------------------------- auth
    def login(self) -> None:
        """Log in. The web app sends bcrypt(md5(pwd)) as p1/p2; older clients
        sent the plain md5 as `pwd`. We try the current scheme first."""
        salt = bcrypt.gensalt(rounds=10)
        payloads = [
            {"account": self.email, "accountType": 2,
             "p1": bcrypt.hashpw(self._pwd_md5.encode(), salt).decode(),
             "p2": salt.decode()},
            {"account": self.email, "accountType": 2, "pwd": self._pwd_md5},
        ]
        last = None
        for p in payloads:
            r = self.s.post(f"{self.base}/account/login", json=p, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
            if data.get("result") == "0000":
                self.token = data["data"]["accessToken"]
                self.user_id = str(data["data"]["userId"])
                self.s.cookies["CPL-coros-token"] = self.token
                log.info("Logged in to COROS (%s)", self.base)
                return
            last = data
        raise CorosAuthError(f"Login failed: {last.get('result')} {last.get('message')}")

    # ------------------------------------------------------------- helpers
    def _headers(self) -> dict:
        if not self.token:
            raise CorosAuthError("Not logged in")
        return {"accesstoken": self.token,
                "yfheader": json.dumps({"userId": self.user_id})}

    def _call(self, method: str, path: str, *, params=None, body=None, _retry=True):
        r = self.s.request(method, f"{self.base}{path}", params=params,
                           json=body, headers=self._headers(), timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        code = data.get("result")
        if code == "1019" and _retry:  # token expired -> log in again once
            log.info("Token expired, logging in again")
            self.login()
            return self._call(method, path, params=params, body=body, _retry=False)
        if code != "0000":
            raise CorosError(f"{path}: {code} {data.get('message')}")
        return data.get("data")

    # ----------------------------------------------------------- endpoints
    def activities(self, page: int = 1, size: int = 50) -> dict:
        """One page of activities: {'dataList': [...], 'totalPage': n, ...}."""
        return self._call("GET", "/activity/query",
                          params={"size": size, "pageNumber": page, "modeList": ""})

    def iter_activities(self, size: int = 50):
        """Yield every activity, newest first."""
        page = 1
        while True:
            data = self.activities(page, size)
            yield from data.get("dataList", [])
            if page >= data.get("totalPage", 1):
                return
            page += 1

    def activity_detail(self, label_id: str, sport_type: int) -> dict:
        return self._call("POST", "/activity/detail/query", body={}, params={
            "labelId": label_id, "sportType": sport_type,
            "screenW": 1920, "screenH": 1080})

    def download(self, label_id: str, sport_type: int, fmt: str = "fit") -> bytes:
        data = self._call("POST", "/activity/detail/download", params={
            "labelId": label_id, "sportType": sport_type, "fileType": FILE_TYPES[fmt]})
        url = (data or {}).get("fileUrl")
        if not url:
            raise CorosError(f"No file URL for activity {label_id}")
        r = self.s.get(url, timeout=self.timeout * 4)
        r.raise_for_status()
        return r.content

    def analyse(self) -> dict:
        """EvoLab: 'dayList' holds ~84 days of daily metrics (rhr, avgSleepHrv,
        sleepHrvBase, ati, cti, tiredRate, trainingLoadRatio, t7d, t28d...)."""
        return self._call("GET", "/analyse/query")

    def dashboard(self) -> dict:
        """'summaryInfo': recoveryPct, fullRecoveryHours, staminaLevel, lthr, ltsp..."""
        return self._call("GET", "/dashboard/query")

    def profile(self) -> dict:
        return self._call("GET", "/profile/private/query")
