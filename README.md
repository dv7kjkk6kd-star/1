# Kompas klidu: obchodní cockpit pro Nasdaq

Webová aplikace na GitHub Pages: briefing, bias, kalendář, deník obchodů, backtest, robot, mentor a další stránky.

## Co je kde

- **`briefing_v…py`**: celý web. Robot na GitHubu vždy spustí verzi s **nejvyšším číslem** a ta vyrobí stránky do složky `docs`.
- **Nastavení** (akcie, futures, zdroje zpráv, účty na X): v nejnovějším `briefing_v…py` u řádku `CONFIG =`.
- **`docs/`**: hotové stránky, které vidíš na webu. Neupravuj je ručně, robot je při dalším spuštění přepíše.
- **`.github/workflows/briefing.yml`**: robot. V pracovní dny běží každých 30 minut mezi 13:30 a 16:30 našeho času, v neděli večer jednou. Ručně: záložka Actions → „Ranní briefing“ → Run workflow.
- **`worker.js`**: záloha kódu Workeru na Cloudflare (synchronizace mezi zařízeními, robot z TradingView, upozornění, AI). Heslo TOKEN v něm není, je jen v nastavení Workeru na Cloudflare. Když Worker na Cloudflare změníš, ulož sem novou verzi.
- **`test_sync.py`**: test pravidla synchronizace. Po každé nové verzi briefingu spusť `python3 test_sync.py`; musí napsat „OK“.

## Nová verze

1. Ulož ji sem jako `briefing_v` + další číslo + `.py`, vycházej z té nejnovější.
2. V GitHub Desktop nejdřív **Fetch origin**, pak Commit a **Push origin**.
3. Staré verze můžeš mazat, v historii na GitHubu zůstanou.

## Dobré vědět

- Obchody se ukládají v prohlížeči a s heslem TOKEN se synchronizují přes Cloudflare mezi Macem a mobilem. Zálohu do souboru dělej i tak.
- Na iPhonu používej aplikaci přidanou na plochu, ne jen Safari.
- Skóre a nálada trhu jsou jednoduchý výpočet, ne obchodní signál ani investiční doporučení.
