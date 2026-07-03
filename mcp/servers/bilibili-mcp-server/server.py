import html
import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()


class BilibiliAPIError(RuntimeError):
    """Raised when a public Bilibili API request fails."""


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    return html.unescape(re.sub(r"<[^>]+>", "", value)).strip()


class BilibiliClient:
    def __init__(self) -> None:
        base_url = os.getenv("BILIBILI_API_BASE", "https://api.bilibili.com").rstrip("/")
        timeout = float(os.getenv("BILIBILI_HTTP_TIMEOUT", "15"))
        user_agent = os.getenv(
            "BILIBILI_USER_AGENT",
            "Mozilla/5.0",
        )

        self.base_url = base_url
        self.timeout = timeout
        self.default_headers = {
            "User-Agent": user_agent,
            "Referer": "https://www.bilibili.com/",
            "Accept": "application/json, text/plain, */*",
        }

    def _get(
        self,
        path: str,
        params: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = dict(self.default_headers)
        if extra_headers:
            headers.update(extra_headers)
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = html.unescape(response.read().decode("utf-8"))
        except socket.timeout as exc:
            raise BilibiliAPIError("Request to Bilibili API timed out.") from exc
        except urllib.error.HTTPError as exc:
            raise BilibiliAPIError(
                f"Request to Bilibili API failed: HTTP {exc.code}"
            ) from exc
        except urllib.error.URLError as exc:
            raise BilibiliAPIError(f"Request to Bilibili API failed: {exc.reason}") from exc

        try:
            payload = json.loads(payload)
        except ValueError as exc:
            raise BilibiliAPIError("Bilibili API returned invalid JSON.") from exc

        code = payload.get("code", 0)
        if code != 0:
            message = payload.get("message") or payload.get("msg") or "Unknown API error"
            raise BilibiliAPIError(f"Bilibili API error {code}: {message}")

        data = payload.get("data")
        if data is None:
            raise BilibiliAPIError("Bilibili API returned an empty response body.")
        return data

    def search_videos(self, keyword: str, limit: int = 10) -> list[dict[str, Any]]:
        keyword = keyword.strip()
        if not keyword:
            raise ValueError("keyword cannot be empty.")

        limit = max(1, min(limit, 50))
        data = self._get(
            "/x/web-interface/wbi/search/type",
            {"search_type": "video", "keyword": keyword},
            {"Referer": f"https://search.bilibili.com/all?keyword={keyword}"},
        )
        results = data.get("result") or []
        videos: list[dict[str, Any]] = []
        for item in results[:limit]:
            bvid = item.get("bvid", "")
            videos.append(
                {
                    "bvid": bvid,
                    "title": _clean_text(item.get("title")),
                    "link": f"https://www.bilibili.com/video/{bvid}" if bvid else "",
                    "play_count": item.get("play", 0),
                }
            )
        return videos

    def get_video_info(self, bvid: str) -> dict[str, Any]:
        bvid = bvid.strip()
        if not bvid:
            raise ValueError("bvid cannot be empty.")

        data = self._get(
            "/x/web-interface/view",
            {"bvid": bvid},
            {"Referer": f"https://www.bilibili.com/video/{bvid}"},
        )
        owner = data.get("owner") or {}
        stat = data.get("stat") or {}
        return {
            "bvid": bvid,
            "title": data.get("title", ""),
            "description": data.get("desc", ""),
            "uploader": {
                "uid": owner.get("mid"),
                "name": owner.get("name", ""),
            },
            "play_count": stat.get("view", 0),
            "link": f"https://www.bilibili.com/video/{bvid}",
        }

    def get_user_info(self, uid: int) -> dict[str, Any]:
        if uid <= 0:
            raise ValueError("uid must be a positive integer.")

        data = self._get(
            "/x/web-interface/card",
            {"mid": uid},
            {"Referer": f"https://space.bilibili.com/{uid}"},
        )
        card = data.get("card") or {}
        return {
            "uid": uid,
            "name": card.get("name", ""),
            "follower_count": data.get("follower", card.get("fans", 0)),
            "bio": card.get("sign") or card.get("description", ""),
            "space_url": f"https://space.bilibili.com/{uid}",
        }


client = BilibiliClient()
mcp = FastMCP("bilibili-mcp-server")


@mcp.tool()
def search_videos(keyword: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search Bilibili videos by keyword and return title, link, and play count."""
    return client.search_videos(keyword=keyword, limit=limit)


@mcp.tool()
def get_video_info(bvid: str) -> dict[str, Any]:
    """Get a video's title, description, uploader, and play count by BVID."""
    return client.get_video_info(bvid=bvid)


@mcp.tool()
def get_user_info(uid: int) -> dict[str, Any]:
    """Get a Bilibili creator's basic profile, follower count, and bio by UID."""
    return client.get_user_info(uid=uid)


if __name__ == "__main__":
    mcp.run()
