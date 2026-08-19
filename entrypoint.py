#!/usr/bin/env python3
"""
Space Weather Check — GitHub Action entrypoint.
Fetches current space weather from NOAA SWPC + NASA NeoWS and evaluates
thresholds for CI/CD gating.

Zero cost: no API keys required (NASA DEMO_KEY has rate limits but works for
low-frequency CI checks). For high-frequency use, set a real NASA API key.

Data sources:
- Kp index: NOAA SWPC Planetary K-index JSON
- Solar wind: NOAA SWPC ACE real-time solar wind
- X-ray flux: NOAA SWPC GOES X-ray flux
- NEOs: NASA NeoWS (Near Earth Object Web Service)
"""

import os
import sys
import json
import math
import re
from datetime import datetime, timezone, date

import requests

# ── Configuration ──────────────────────────────────────────────────────────
NOAA_KP_URL = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"
NOAA_SOLAR_WIND_URL = "https://services.swpc.noaa.gov/products/ace/ace_swepam_1m.json"
NOAA_XRAY_URL = "https://services.swpc.noaa.gov/json/goes/primary/xrays-6-hour.json"
NASA_NEO_URL = "https://api.nasa.gov/neo/v1/feed"

NASA_API_KEY = os.environ.get("INPUT_NASA_API_KEY", "DEMO_KEY")

# Thresholds from action inputs
KP_THRESHOLD = float(os.environ.get("INPUT_KP_THRESHOLD", "7"))
FLARE_THRESHOLD = os.environ.get("INPUT_FLARE_THRESHOLD", "X").upper()
SOLAR_WIND_THRESHOLD = float(os.environ.get("INPUT_SOLAR_WIND_THRESHOLD", "0"))
BZ_THRESHOLD = float(os.environ.get("INPUT_BZ_THRESHOLD", "0"))
NEO_HAZARDOUS_ONLY = os.environ.get("INPUT_NEO_HAZARDOUS_ONLY", "false").lower() == "true"
FAIL_ON_WARNING = os.environ.get("INPUT_FAIL_ON_WARNING", "true").lower() == "true"
CREATE_ISSUE = os.environ.get("INPUT_CREATE_ISSUE", "false").lower() == "true"
ADD_SUMMARY = os.environ.get("INPUT_SUMMARY", "true").lower() == "true"

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")
GITHUB_WORKSPACE = os.environ.get("GITHUB_WORKSPACE", ".")


def log(msg):
    print(f"[space-weather] {msg}", flush=True)


def set_output(name, value):
    """Set a GitHub Actions output parameter."""
    output_file = os.environ.get("GITHUB_OUTPUT", "")
    if output_file:
        with open(output_file, "a") as f:
            f.write(f"{name}={value}\n")
    else:
        print(f"::set-output name={name}::{value}")


def kp_to_g_scale(kp):
    """Convert Kp index to G-scale storm level."""
    if kp is None:
        return "G0"
    if kp >= 9:
        return "G5"
    if kp >= 8:
        return "G4"
    if kp >= 7:
        return "G3"
    if kp >= 6:
        return "G2"
    if kp >= 5:
        return "G1"
    return "G0"


def flare_to_number(cls):
    """Convert flare class letter to a number for comparison."""
    order = {"A": 1, "B": 2, "C": 3, "M": 4, "X": 5}
    return order.get(cls.upper(), 0)


# ── Data fetchers ───────────────────────────────────────────────────────────
def fetch_kp():
    """Fetch the latest Kp index from NOAA SWPC."""
    try:
        resp = requests.get(NOAA_KP_URL, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data:
            latest = data[-1]
            return float(latest.get("kp", 0)), latest.get("time_tag", "")
    except Exception as e:
        log(f"  Kp fetch failed: {e}")
    return None, None


def fetch_solar_wind():
    """Fetch the latest solar wind speed and Bz from NOAA SWPC ACE data."""
    try:
        resp = requests.get(NOAA_SOLAR_WIND_URL, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        # ACE SWEPAM format: header row + data rows
        # Columns: time_tag, bx, by, bz, theta, phi, density, speed, temp, etc.
        if len(data) > 1:
            header = data[0]
            latest = data[-1]
            speed_idx = header.index("speed") if "speed" in header else 7
            bz_idx = header.index("bz") if "bz" in header else 3
            speed = float(latest[speed_idx]) if latest[speed_idx] not in ("", None) else None
            bz = float(latest[bz_idx]) if latest[bz_idx] not in ("", None) else None
            return speed, bz
    except Exception as e:
        log(f"  Solar wind fetch failed: {e}")
    return None, None


def fetch_xray_flux():
    """Fetch the latest X-ray flux class from NOAA SWPC GOES data."""
    try:
        resp = requests.get(NOAA_XRAY_URL, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data:
            # Find the most recent non-null flux entry
            for entry in reversed(data):
                flux = entry.get("flux")
                if flux and float(flux) > 0:
                    # Convert flux to class letter
                    flux_val = float(flux)
                    if flux_val >= 1e-4:
                        return "X"
                    elif flux_val >= 1e-5:
                        return "M"
                    elif flux_val >= 1e-6:
                        return "C"
                    elif flux_val >= 1e-7:
                        return "B"
                    else:
                        return "A"
    except Exception as e:
        log(f"  X-ray flux fetch failed: {e}")
    return None


def fetch_neos():
    """Fetch today's near-Earth objects from NASA NeoWS."""
    try:
        today = date.today().isoformat()
        resp = requests.get(
            NASA_NEO_URL,
            params={"start_date": today, "end_date": today, "api_key": NASA_API_KEY},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        neos = data.get("near_earth_objects", {}).get(today, [])
        if NEO_HAZARDOUS_ONLY:
            neos = [n for n in neos if n.get("is_potentially_hazardous_asteroid", False)]
        return neos
    except Exception as e:
        log(f"  NEO fetch failed: {e}")
    return []


# ── Threshold evaluation ───────────────────────────────────────────────────
def evaluate_thresholds(kp, flare_class, wind_speed, bz, neo_count):
    """Check if any configured threshold is exceeded."""
    warnings = []

    if KP_THRESHOLD > 0 and kp is not None and kp >= KP_THRESHOLD:
        warnings.append(f"Kp index {kp} >= threshold {KP_THRESHOLD}")

    if FLARE_THRESHOLD and flare_class is not None:
        if flare_to_number(flare_class) >= flare_to_number(FLARE_THRESHOLD):
            warnings.append(f"Flare class {flare_class} >= threshold {FLARE_THRESHOLD}")

    if SOLAR_WIND_THRESHOLD > 0 and wind_speed is not None and wind_speed >= SOLAR_WIND_THRESHOLD:
        warnings.append(f"Solar wind speed {wind_speed} km/s >= threshold {SOLAR_WIND_THRESHOLD} km/s")

    if BZ_THRESHOLD < 0 and bz is not None and bz <= BZ_THRESHOLD:
        warnings.append(f"Bz {bz} nT <= threshold {BZ_THRESHOLD} nT (strongly southward)")

    return warnings


# ── GitHub issue creation ──────────────────────────────────────────────────
def create_github_issue(warnings, data):
    """Create a GitHub issue when a significant event is detected."""
    if not CREATE_ISSUE or not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        return

    title = f"Space Weather Alert: {', '.join(warnings[:2])}"
    body = f"""## Space Weather Alert

The space weather check in workflow run has detected conditions exceeding configured thresholds.

### Warnings
{chr(10).join(f"- {w}" for w in warnings)}

### Current Conditions
- **Kp index:** {data.get('kp', 'unknown')} ({data.get('storm_level', 'G0')})
- **X-ray flare class:** {data.get('flare_class', 'unknown')}
- **Solar wind speed:** {data.get('solar_wind_speed', 'unknown')} km/s
- **Bz:** {data.get('bz', 'unknown')} nT
- **NEOs today:** {data.get('neo_count', 0)}

### Action Required
Review your deployment pipeline and consider delaying operations that could be affected by space weather:
- HF radio communications
- GNSS/GPS accuracy
- Satellite operations
- Power grid stability
- Aurora photography opportunities

---
*This issue was automatically created by the [Space Weather Check](https://github.com/flarientglobal/space-weather-action) GitHub Action.*
"""

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    try:
        resp = requests.post(
            f"https://api.github.com/repos/{GITHUB_REPOSITORY}/issues",
            headers=headers,
            json={"title": title, "body": body, "labels": ["space-weather", "alert"]},
            timeout=10,
        )
        if resp.ok:
            issue = resp.json()
            log(f"  Created issue: {issue.get('html_url', '')}")
        else:
            log(f"  Issue creation failed: {resp.status_code}")
    except Exception as e:
        log(f"  Issue creation error: {e}")


# ── Summary generation ────────────────────────────────────────────────────
def generate_summary(data, warnings):
    """Generate a markdown summary for the GitHub Actions job summary."""
    if not ADD_SUMMARY:
        return

    status_emoji = "✅" if not warnings else "⚠️"
    status_text = "All Clear" if not warnings else f"{len(warnings)} Warning(s)"

    summary = f"""## {status_emoji} Space Weather Check — {status_text}

| Metric | Value | Status |
|--------|-------|--------|
| **Kp Index** | {data.get('kp', 'N/A')} ({data.get('storm_level', 'G0')}) | {"⚠️" if any("Kp" in w for w in warnings) else "✅"} |
| **X-ray Flare** | {data.get('flare_class', 'N/A')}-class | {"⚠️" if any("Flare" in w for w in warnings) else "✅"} |
| **Solar Wind** | {data.get('solar_wind_speed', 'N/A')} km/s | {"⚠️" if any("wind" in w for w in warnings) else "✅"} |
| **Bz** | {data.get('bz', 'N/A')} nT | {"⚠️" if any("Bz" in w for w in warnings) else "✅"} |
| **NEOs Today** | {data.get('neo_count', 0)} | ✅ |

"""

    if warnings:
        summary += f"""### ⚠️ Warnings
{chr(10).join(f"- {w}" for w in warnings)}

"""

    summary += f"""### About
Data from NOAA SWPC and NASA NeoWS. Powered by [Flarient](https://flarient.com) — [Space Weather Action](https://github.com/flarientglobal/space-weather-action).

*Checked at {datetime.now(timezone.utc).isoformat()}*
"""

    # Write to GITHUB_STEP_SUMMARY
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if summary_file:
        with open(summary_file, "a") as f:
            f.write(summary)
    else:
        print(summary)


# ── Main ──────────────────────────────────────────────────────────────────
def main():
    log("Fetching current space weather conditions...")

    # Fetch all data sources in parallel-ish (sequential but fast)
    kp, kp_time = fetch_kp()
    wind_speed, bz = fetch_solar_wind()
    flare_class = fetch_xray_flux()
    neos = fetch_neos()

    storm_level = kp_to_g_scale(kp) if kp is not None else "G0"
    neo_count = len(neos)

    data = {
        "kp": kp,
        "kp_time": kp_time,
        "flare_class": flare_class,
        "solar_wind_speed": wind_speed,
        "bz": bz,
        "neo_count": neo_count,
        "storm_level": storm_level,
    }

    log(f"  Kp: {kp} ({storm_level})")
    log(f"  Flare: {flare_class}-class")
    log(f"  Solar wind: {wind_speed} km/s")
    log(f"  Bz: {bz} nT")
    log(f"  NEOs: {neo_count}")

    # Evaluate thresholds
    warnings = evaluate_thresholds(kp, flare_class, wind_speed, bz, neo_count)

    if warnings:
        log(f"  ⚠️  {len(warnings)} warning(s):")
        for w in warnings:
            log(f"    - {w}")
    else:
        log("  ✅ All conditions within thresholds")

    # Set outputs
    set_output("kp", str(kp) if kp is not None else "")
    set_output("flare-class", str(flare_class) if flare_class else "")
    set_output("solar-wind-speed", str(wind_speed) if wind_speed is not None else "")
    set_output("bz", str(bz) if bz is not None else "")
    set_output("neo-count", str(neo_count))
    set_output("has-warning", "true" if warnings else "false")
    set_output("storm-level", storm_level)

    # Generate summary
    generate_summary(data, warnings)

    # Create issue if requested
    if warnings and CREATE_ISSUE:
        create_github_issue(warnings, data)

    # Exit with error if configured to fail on warnings
    if warnings and FAIL_ON_WARNING:
        log("  Exiting with error (fail-on-warning is true)")
        sys.exit(1)

    log("  Done")


if __name__ == "__main__":
    main()
