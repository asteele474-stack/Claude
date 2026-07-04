"""
BlackSky Catalog Watcher — New Imagery Alert over China
========================================================
Polls the BlackSky archive catalog (/catalog/stac/search) for newly added
scenes over a China AOI and pushes an alert (LINE / Slack) for each new one.

Why polling instead of webhooks or archive subscriptions:
  - Webhooks only fire for YOUR tasking plan items — not catalog additions.
  - POST /archive/subscription auto-ORDERS every matching scene (that's a
    purchase for each image — over all of China that could be enormous).
  - Catalog search is read-only and free to poll, so a scheduled search
    with de-duplication is the right "alert on everything collected" tool.

Usage:
  pip install requests python-dotenv
  .env:
    BLACKSKY_API_KEY=...
    LINE_CHANNEL_ACCESS_TOKEN=...   (optional)
    LINE_TO_ID=...                  (optional)
    SLACK_WEBHOOK_URL=...           (optional)

  # one-off:
  python catalog_watcher.py --lookback PT2H

  # cron every 15 min (lookback slightly larger than interval for overlap;
  # the seen-cache prevents duplicate alerts):
  */15 * * * * cd /path && python catalog_watcher.py --lookback PT30M

Options:
  --aoi-file china.wkt     Use your own precise boundary (WKT). Default is a
                           built-in simplified China polygon.
  --max-cloud 80           Skip very cloudy scenes.
  --sensors ALL            sensorId filter (default: ALL vendors/sensors
                           visible to your subscription).
  --quiet                  Print only, don't push notifications.
"""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.blacksky.com/v1"
HEADERS = {"Authorization": os.environ["BLACKSKY_API_KEY"]}
LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_TO = os.getenv("LINE_TO_ID")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")

STATE_FILE = Path(__file__).with_name("seen_scenes.json")
MAX_SEEN = 20000  # cap the dedup cache

# Simplified China boundary (coarse ~30-vertex polygon, WGS84 lon lat).
# Good enough to exclude most of Japan/Korea/India/SE Asia — swap in your
# precise boundary via --aoi-file for production (e.g. from your KMZ work).
CHINA_WKT = (
    "POLYGON (("
    "73.6 39.4, 74.9 37.2, 78.9 34.3, 78.0 31.0, 81.0 30.2, 85.0 28.3, "
    "88.8 27.9, 92.1 27.7, 94.6 29.3, 97.3 28.2, 98.7 25.8, 97.5 23.9, "
    "100.1 21.5, 101.8 21.1, 105.3 23.3, 108.0 21.5, 109.7 20.2, 110.8 20.0, "
    "111.0 21.5, 113.5 22.0, 114.5 22.3, 117.0 23.5, 119.5 25.3, 121.0 27.8, "
    "122.5 30.5, 122.0 33.5, 119.5 35.0, 122.5 37.4, 121.5 39.0, 124.3 39.8, "
    "126.0 41.0, 128.0 41.4, 130.5 42.5, 131.2 45.0, 134.5 47.5, 134.0 48.5, "
    "127.5 50.0, 125.0 53.2, 121.5 53.4, 119.5 50.0, 116.5 49.8, 111.5 45.0, "
    "105.0 41.8, 101.0 42.5, 96.5 44.0, 91.0 45.1, 87.5 49.2, 85.5 47.0, "
    "82.5 45.5, 80.5 45.0, 79.9 42.0, 76.0 41.0, 73.6 39.4"
    "))"
)


# --------------------------------------------------------------------------
def load_seen() -> set[str]:
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text()))
        except json.JSONDecodeError:
            pass
    return set()


def save_seen(seen: set[str]) -> None:
    # keep the most recent entries only (scene IDs embed timestamps, sortable)
    trimmed = sorted(seen)[-MAX_SEEN:]
    STATE_FILE.write_text(json.dumps(trimmed))


# --------------------------------------------------------------------------
def search_catalog(aoi_wkt: str, lookback: str, max_cloud: float,
                   sensor_id: str, vendor_id: str) -> list[dict]:
    """Page through /catalog/stac/search for the lookback window."""
    features: list[dict] = []
    params = {
        "aoi": aoi_wkt,
        "time": lookback,          # ISO 8601 duration, e.g. PT30M, PT2H, P1D
        "limit": 300,
        "cloudPercentTo": max_cloud,
        "sensorId": sensor_id,
        "vendorId": vendor_id,
        "sort": "-timestamp",
    }
    url = f"{BASE_URL}/catalog/stac/search"
    r = requests.get(url, headers=HEADERS, params=params, timeout=120)
    r.raise_for_status()
    body = r.json()
    features.extend(body.get("features", []))

    # follow pagination links (searchAfterId) until exhausted
    while body.get("links"):
        next_url = body["links"][0].get("href")
        if not next_url:
            break
        r = requests.get(next_url, headers=HEADERS, timeout=120)
        r.raise_for_status()
        body = r.json()
        page = body.get("features", [])
        if not page:
            break
        features.extend(page)

    return features


# --------------------------------------------------------------------------
def format_alert(feature: dict) -> str:
    scene_id = feature["id"]
    props = feature.get("properties", {})
    bbox = feature.get("bbox", [])
    center = ""
    if len(bbox) == 4:
        center = f"{(bbox[1] + bbox[3]) / 2:.3f}N, {(bbox[0] + bbox[2]) / 2:.3f}E"
    cloud = props.get("cloudPercent")
    gsd = props.get("gsd")
    lines = [
        "🛰 New scene over China",
        scene_id,
        f"Collected: {props.get('datetime', '?')}",
        f"Sensor: {props.get('vendorId', '?')}/{props.get('sensorId', '?')}",
    ]
    if gsd is not None:
        lines.append(f"GSD: {gsd:.2f}m")
    if cloud is not None:
        lines.append(f"Cloud: {cloud:.0f}%")
    if center:
        lines.append(f"Center: {center}")
    lines.append(f"Thumbnail: {BASE_URL}/browse/{scene_id}")
    return "\n".join(lines)


def notify(text: str, quiet: bool) -> None:
    print(text, "\n" + "-" * 50)
    if quiet:
        return
    if LINE_TOKEN and LINE_TO:
        requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={"Authorization": f"Bearer {LINE_TOKEN}",
                     "Content-Type": "application/json"},
            json={"to": LINE_TO, "messages": [{"type": "text", "text": text[:4900]}]},
            timeout=30,
        )
    if SLACK_WEBHOOK_URL:
        requests.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=30)


# --------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="Alert on new BlackSky scenes over China")
    ap.add_argument("--lookback", default="PT2H",
                    help="ISO8601 duration search window (PT30M, PT2H, P1D)")
    ap.add_argument("--aoi-file", help="Path to a WKT file with a precise boundary")
    ap.add_argument("--max-cloud", type=float, default=100.0)
    ap.add_argument("--sensors", default="ALL", help="sensorId filter")
    ap.add_argument("--vendors", default="ALL", help="vendorId filter")
    ap.add_argument("--max-alerts", type=int, default=25,
                    help="Cap individual pushes per run; the rest are summarized")
    ap.add_argument("--quiet", action="store_true", help="Print only, no push")
    args = ap.parse_args()

    aoi = Path(args.aoi_file).read_text().strip() if args.aoi_file else CHINA_WKT

    seen = load_seen()
    features = search_catalog(aoi, args.lookback, args.max_cloud,
                              args.sensors, args.vendors)
    new = [f for f in features if f["id"] not in seen]

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"[{ts}] catalog returned {len(features)} scenes, {len(new)} new")

    for f in new[: args.max_alerts]:
        notify(format_alert(f), args.quiet)

    if len(new) > args.max_alerts:
        notify(
            f"🛰 +{len(new) - args.max_alerts} more new scenes over China "
            f"in this window (capped at {args.max_alerts} alerts). "
            f"Run digest or check Spectra for the full list.",
            args.quiet,
        )

    seen.update(f["id"] for f in new)
    save_seen(seen)


if __name__ == "__main__":
    main()
