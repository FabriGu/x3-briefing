# x3-briefing

Daily AI morning briefing for my Xteink X3 e-ink reader. **Status: active.**

A GitHub Action runs every morning and:
1. takes the newest links from [kickingkeys/2026-reads](https://github.com/kickingkeys/2026-reads) plus the top Hacker News stories
2. asks Claude (`claude-opus-5-5`) for a short summary and a ~400-word deep-dive of each
3. commits `briefing.json` (ASCII only, since the device fonts are 7-bit)

The [x3-link](https://github.com/FabriGu/x3-link) iPhone app fetches
`https://raw.githubusercontent.com/FabriGu/x3-briefing/main/briefing.json`
and streams one section at a time to the device over BLE.

## Setup
```sh
gh secret set ANTHROPIC_API_KEY -R FabriGu/x3-briefing   # paste the key when prompted
gh workflow run daily.yml -R FabriGu/x3-briefing          # first run now
```

## Develop
```sh
python3 -m unittest test_briefing -v   # stdlib only, no key needed
python3 briefing.py --dry-run          # live sources, placeholder text
```
Refusals fall back server-side (`fallbacks: "default"`); a failed link or
API call leaves a title-only topic instead of failing the run.
