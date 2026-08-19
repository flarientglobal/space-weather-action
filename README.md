# Space Weather Check — GitHub Action

A reusable GitHub Action that checks current space weather conditions in your CI/CD pipeline. Fail deployments during geomagnetic storms, generate status reports, and create issues on significant space weather events.

## Why?

Space weather affects:
- **HF radio communications** — solar flares cause radio blackouts
- **GNSS/GPS accuracy** — geomagnetic storms degrade satellite navigation
- **Satellite operations** — increased drag and radiation
- **Power grids** — GICs from severe storms can damage infrastructure
- **Aurora visibility** — Kp ≥ 5 means aurora may be visible at mid-latitudes

Use this action to gate deployments, operations, or observatory scheduling on real-time space weather conditions.

## Usage

### Basic check

\`\`\`yaml
- name: Check space weather
  uses: flarientglobal/space-weather-action@v1
\`\`\`

This uses default thresholds (Kp ≥ 7, X-class flares) and fails the step if exceeded.

### Deployment gate

\`\`\`yaml
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Check space weather before deploy
        id: space-weather
        uses: flarientglobal/space-weather-action@v1
        with:
          kp-threshold: '6'
          fail-on-warning: 'true'

      - name: Deploy
        if: steps.space-weather.outputs.has-warning != 'true'
        run: ./deploy.sh

      - name: Skip deployment (space weather alert)
        if: steps.space-weather.outputs.has-warning == 'true'
        run: |
          echo "Deployment skipped due to space weather conditions"
          echo "Kp: ${{ steps.space-weather.outputs.kp }}"
          echo "Storm level: ${{ steps.space-weather.outputs.storm-level }}"
\`\`\`

### Observatory scheduling

\`\`\`yaml
- name: Check aurora conditions
  uses: flarientglobal/space-weather-action@v1
  with:
    kp-threshold: '5'
    fail-on-warning: 'false'
    summary: 'true'
\`\`\`

### NEO monitoring with issue creation

\`\`\`yaml
- name: Check for hazardous NEOs
  uses: flarientglobal/space-weather-action@v1
  with:
    neo-hazardous-only: 'true'
    create-issue: 'true'
    fail-on-warning: 'false'
\`\`\`

## Inputs

| Input | Description | Default |
|-------|-------------|---------|
| `kp-threshold` | Fail if Kp ≥ this value (0-9). 0 to disable. | `7` |
| `flare-threshold` | Fail if flare class ≥ this (A/B/C/M/X). Empty to disable. | `X` |
| `solar-wind-threshold` | Fail if solar wind speed ≥ this (km/s). 0 to disable. | `0` |
| `bz-threshold` | Fail if Bz ≤ this (nT, negative = southward). 0 to disable. | `0` |
| `neo-hazardous-only` | Only count potentially hazardous NEOs. | `false` |
| `fail-on-warning` | Exit non-zero if any threshold exceeded. | `true` |
| `create-issue` | Create a GitHub issue on significant events. | `false` |
| `summary` | Add report to job summary. | `true` |
| `nasa-api-key` | NASA API key for NeoWS. | `DEMO_KEY` |

## Outputs

| Output | Description |
|--------|-------------|
| `kp` | Current Kp index (0-9) |
| `flare-class` | Current X-ray flare class (A/B/C/M/X) |
| `solar-wind-speed` | Solar wind speed in km/s |
| `bz` | Bz magnetic field component in nT |
| `neo-count` | Number of near-Earth objects today |
| `has-warning` | `true` if any threshold was exceeded |
| `storm-level` | G-scale storm level (G0-G5) derived from Kp |

## Data Sources

- **Kp index** — NOAA SWPC Planetary K-index
- **Solar wind** — NOAA SWPC ACE real-time solar wind
- **X-ray flux** — NOAA SWPC GOES X-ray flux
- **NEOs** — NASA NeoWS (Near Earth Object Web Service)

All data is fetched directly from public government APIs. No API keys required (NASA `DEMO_KEY` has rate limits but works for low-frequency CI checks).

## Cost

**Free** — all data sources are public government APIs.

## License

MIT — see [LICENSE](LICENSE).

## About

Built by [Flarient](https://flarient.com) — the space weather intelligence platform.
