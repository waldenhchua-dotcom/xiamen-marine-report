"""Freeze official public pages without mistaking an index for a dated bulletin.

A body adapter can be added when dates, validity and area coverage can all be
verified. A failed page, empty index, or unrecognized HTML never means no warning.
"""

from __future__ import annotations

from .common import download

OFFICIAL_PAGES = (
    ("xiamen_official_index", "https://www.fjocean.com/"),
    ("xiamen_official_forecast_page", "https://wx.fjocean.com/"),
)


def fetch_official(sites: list[dict], request_date: str, *, timeout: float = 20) -> tuple[list, dict, list, list]:
    sources, raw = [], {}
    for source_id, url in OFFICIAL_PAGES:
        response = download(url, timeout=timeout)
        source = response.source(source_id, kind="official", license_text=(
            "Official public information; redistribution permission not established. "
            "Keep evidence locally; do not publish downloaded bodies by default."
        ))
        source["metadata"].update({
            "provider": "自然资源部厦门海洋中心 / 厦门海洋预报台",
            "scope": "public index/page; not independently verified target-date warning body",
            "request_date": request_date,
            "time_convention": "Bulletin local time must be independently verified",
            "unit_conventions": "No numeric series inferred from page text",
            "body_verified": False,
        })
        sources.append(source)
        raw[source_id] = response.raw
    documents, gaps = [], []
    for site in sites:
        for category in ("warning", "marine_forecast"):
            documents.append({
                "site_id": site["id"], "category": category, "status": "not_obtained",
                "source_id": None, "request_date": request_date,
                "title": "目标日期正文、有效时段及海区覆盖尚未核验",
                "published_at": None, "valid_from": None, "valid_to": None,
                "summary": "截至取证时尚未取得经核验的正文；不得解释为无预警。",
            })
        gaps.append({"code": "OFFICIAL_BODY_NOT_VERIFIED", "site_id": site["id"],
                     "message": "Official target-date forecast/warning bodies, validity and area coverage not verified",
                     "blocking": True})
    return sources, raw, documents, gaps
