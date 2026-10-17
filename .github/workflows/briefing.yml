name: Ranní briefing

on:
  schedule:
    - cron: "30 12 * * 1-5"
  workflow_dispatch:

permissions:
  contents: write

jobs:
  briefing:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -r requirements.txt
      - run: python briefing.py
      - run: |
          git config user.name "briefing-bot"
          git config user.email "briefing-bot@users.noreply.github.com"
          git add docs
          git diff --cached --quiet || git commit -m "Briefing $(date -u +%F)"
          git push
