# Obchodní cockpit pro Nasdaq

Dvě stránky na jedné adrese:

- **Ranní briefing** se každý pracovní den obnoví před 15:00 našeho času: nálada (bullish/bearish) podle futures, VIX, dolar a výnosy, akcie v pre-marketu, dnešní důležité zprávy z Forex Factory a odkazy na tvé účty na X.
- **Deník obchodů** (`journal.html`): zápis obchodů se vstupem, stopem, cílem a výstupem, automatický výpočet R:R a výsledku v R, statistiky, graf vývoje, kalkulačka velikosti pozice podle rizika a screenshoty (vložíš je Ctrl+V nebo souborem).

## Nastavení (10 minut)

1. Na github.com vytvoř nový **veřejný** repozitář, např. `cockpit`.
2. Nahraj soubory: `briefing.py`, `config.json`, `requirements.txt`, `README.md` a složku `docs`.
3. Přidej workflow: "Add file → Create new file", název přesně `.github/workflows/briefing.yml`, vlož obsah stejnojmenného souboru.
4. Settings → Actions → General → Workflow permissions → "Read and write permissions" → Save.
5. Záložka Actions → "Ranní briefing" → "Run workflow". Za minutu je hotovo.
6. Settings → Pages → Deploy from a branch → `main`, složka `/docs` → Save.
7. Adresa: `https://TVOJE-JMENO.github.io/cockpit/`. Přidej si ji na plochu telefonu jako aplikaci ("Přidat na plochu").

## Úpravy

- Akcie, futures a účty na X: `config.json`. Do `x_accounts` napiš uživatelská jména bez zavináče, třeba účty, které sleduje tvůj mentor.
- Čas spuštění: řádek `cron` v `.github/workflows/briefing.yml` (čas je v UTC). Je nastaven na 14:30 našeho času v létě a 13:30 v zimě, protože GitHub spuštění občas o pár minut zpozdí.
- Hranice nálady: funkce `bias` v `briefing.py`.

## Dobré vědět

- Deník ukládá obchody **jen do prohlížeče na daném zařízení**. Tlačítkem "Zálohovat do souboru" je pravidelně ukládej. Do telefonu je pak přeneseš tlačítkem "Obnovit ze zálohy".
- Účty na X jsou jen odkazy. Automatické čtení příspěvků vyžaduje placené API X, a ty neobsahuje.
- O víkendu a ve svátek pre-market data nejsou a stránka to napíše.
- Nálada je jen jednoduchý výpočet z futures, ne obchodní signál ani investiční doporučení.
