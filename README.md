# VELLUR AI CORE

Infrastructura AI privată VELLUR. **VPS-ul existent coordonează** (gateway, chei, HTTPS, rutare), **GPU-ul închiriat
la nevoie (HyperAI, RTX 5090) face inferența**, iar **OpenCode / VS Code** sunt interfața de lucru.

```
PC: OpenCode / VS Code
   │ HTTPS + cheie client (sk-vellur-…)
   ▼
ai.vellurdigital.com  ── Caddy existent pe VPS (certificat automat)
   ├─ /        pagina de stare (sau Open WebUI, opțional)
   ├─ /healthz „gateway online”
   └─ /v1/*    doar endpoint-uri de inferență → LiteLLM (127.0.0.1)
                                             │ tunel SSH deschis de VPS (pornit doar când GPU-ul e pornit)
                                             ▼
                               HyperAI: vLLM + Qwen3.8-27B (4 biți) pe RTX 5090, ascultă doar pe 127.0.0.1
```

## Stare (13.11.2026)

| | Ce |
|---|---|
| **Gata și verificat** (testat complet într-un mediu de test, cu un Caddy real și un site existent simulat) | gateway LiteLLM izolat; chei client revocabile; HTTPS prin Caddy-ul existent cu backup, validare și restaurare automată; pagina de stare; rollback care readuce Caddyfile-ul identic; backup/restore; tunel SSH către GPU cu reconectare automată; streaming și cereri cu tools prin tot lanțul (cu un server de test care răspunde fix „[protocol stub: no model]”, nu un model) |
| **Pregătit, netestat pe serverul real** | instalarea pe VPS-ul `ro-prod-01` (sesiunea nu are acces SSH la el); OpenCode cu endpoint-ul real; Open WebUI ca imagine oficială (aplicația în sine a fost testată: login, înregistrare blocată, persistență) |
| **Așteaptă GPU** | orice inferență reală; alegerea build-ului exact al modelului; performanța și calitatea |
| **Blocat de conturi/permisiuni** | înregistrarea DNS `ai` în Cloudflare; contul HyperAI (cost: aprobarea ta) |

## Servicii, porturi, resurse (măsurate în test)

| Serviciu | Container | Port | RAM (limită) | Măsurat |
|---|---|---|---|---|
| Gateway LiteLLM `v1.108.7-lts` | `vellur-ai-gateway` | 127.0.0.1:3311 | 1 GB | ~550 MB în repaus, ~610 MB după cereri, ~0,2 % CPU, pornește în ~25 s (imagine 1,8 GB pe disc) |
| Tunel GPU (doar cu GPU pornit) | `vellur-ai-gpu-tunnel` | niciunul (doar rețeaua internă) | 48 MB | ~2,5 MB |
| Open WebUI `v0.13.3` (opțional, oprit implicit) | `vellur-ai-webui` | 127.0.0.1:3310 | 1,5 GB | ~1,3 GB (din care ~0,8 GB memorie proprie) |

Pe VPS-ul de 4 GB, implicit rulează **doar gateway-ul**. Open WebUI se pornește doar dacă rămâne memorie
(`sudo vac ui enable` refuză sub 1,8 GB liberi). Nicio inferență locală pe VPS.

Fiecare container: rețea proprie `vellur-ai-core`, porturi doar pe 127.0.0.1, fără socket Docker, fără privilegii,
capabilități eliminate, sistem de fișiere doar-citire (gateway, tunel), limite de memorie/CPU/procese, jurnale rotite
(3 × 10 MB), repornire automată.

## GPU Autopilot

Panou: **https://ai.vellurdigital.com/autopilot/** (login). Pornește și eliberează GPU-ul automat, cu buget lunar,
plafoane pe server și dry run implicit. HyperAI e asistat (fără API de pornire/oprire), Vast.ai e automat (după ce
pui o cheie). Detalii, limite și ce e testat: [`docs/AUTOPILOT.md`](docs/AUTOPILOT.md).

| Serviciu | Container | Port | RAM (limită) | Măsurat |
|---|---|---|---|---|
| GPU Autopilot | `vellur-ai-autopilot` | 127.0.0.1:3312 (+ intern `gpu-backend:8000`) | 256 MB | ~51 MB, ~0,2 % CPU |

## Domenii

- `ai.vellurdigital.com` — un singur nume pentru tot: pagina (sau Open WebUI) la `/`, gateway-ul la `/v1`.
  Un singur DNS, un singur certificat. Înregistrare Cloudflare: **A `ai` → IP-ul VPS-ului, „DNS only” (nor gri)**, la fel ca `bloom`.
- `gateway.ai.vellurdigital.com` — opțional (`GATEWAY_DOMAIN` în `.env` + încă o înregistrare A). Nu e necesar.

## Fișiere pe VPS

| Unde | Ce |
|---|---|
| `/opt/vellur-ai-core/.env` | setări și secrete (root, 600) |
| `/opt/vellur-ai-core/secrets/` | chei client, cheia SSH pentru GPU (root/10001, 700) |
| `/opt/vellur-ai-core/litellm/models.yaml` | modelele active (gol până la GPU); `models.gpu.yaml`, `models.example.yaml` = șabloane |
| `/opt/vellur-ai-core/compose.yaml` | serviciile |
| `/etc/caddy/vellur-ai-core.caddy` | site-ul Caddy generat (root:caddy, 640) + **o singură linie** `import` în `/etc/caddy/Caddyfile` |
| `/var/backups/vellur-ai-core/` | raport inițial, backup-uri |
| `/usr/local/bin/vac` | comanda de administrare |

## Instalare

De pe PC (PowerShell, cu `gh` autentificat), din acest folder:
```powershell
powershell -ExecutionPolicy Bypass -File deploy-from-pc.ps1
```
Scriptul copiază folderul pe VPS și pornește `install.sh` acolo. Instalatorul:
1. face un raport doar-citire al serverului și se oprește dacă nu sunt resurse;
2. **întreabă** înainte de swap (2 GB, recomandat pe 4 GB fără swap) și de instalarea Docker (dacă lipsește);
3. pune fișierele în `/opt/vellur-ai-core`, generează secretele;
4. pornește gateway-ul și creează prima cheie client (`opencode`, afișată **o singură dată**);
5. publică site-ul în Caddy **doar dacă DNS-ul indică deja VPS-ul**; altfel îți spune exact ce înregistrare să adaugi;
6. rulează verificările.

Nu oprește, nu repornește și nu modifică alte proiecte, containere, firewall sau DNS. Se poate rula din nou oricând.

## Folosire zilnică

```bash
sudo vac status            # containere, memorie, public sau nu, modele
sudo vac verify            # toate verificările (local + HTTPS + inferență dacă e GPU)
sudo vac logs gateway -f
sudo vac keys add laptop   # cheie nouă (afișată o dată); sudo vac keys revoke laptop
sudo vac expose            # după ce ai adăugat DNS-ul
sudo vac gpu up | down     # tunelul către GPU (vezi docs/GPU-AND-MODELS.md)
sudo vac ui enable         # Open WebUI, doar dacă ajunge memoria
sudo vac autopilot status  # GPU Autopilot (panou: /autopilot/)
```

## OpenCode

1. Pe VPS: `sudo vac keys add opencode` (sau folosește cheia de la instalare).
2. Pe PC: variabila de mediu `VELLUR_AI_KEY` cu cheia (PowerShell: `setx VELLUR_AI_KEY "sk-vellur-…"`).
3. Copiază `opencode/opencode.json` în proiect sau în `~/.config/opencode/opencode.json`.
4. Test: `opencode/test-endpoint.ps1`. Până la GPU răspunde „No inference backend connected yet”.

Configurația: provider OpenAI-compatibil, model `vellur/vellur-coder` (128k context, 32k ieșire, tool calling,
raționament), permisiuni prudente (editări și comenzi cu confirmare). **Nu a fost testată cu un model real**: abia după
activarea GPU-ului putem spune că merge.

Agenții de cod lucrează pe PC sau într-un mediu de dezvoltare izolat, cu acces la depozitele alese explicit
(token GitHub limitat la acele depozite). **Niciodată** pe VPS-ul de producție și niciodată cu cheile lui.

## Modele

Clienții văd doar nume publice (`vellur-coder`). Ce model și ce GPU stau în spate se schimbă în
`litellm/models.yaml` + `.env`, apoi `sudo vac restart gateway`. OpenCode nu se reconfigurează.
Detalii, recomandarea pentru RTX 5090 și costuri: [`docs/GPU-AND-MODELS.md`](docs/GPU-AND-MODELS.md).

## Pornire, oprire, actualizare, restaurare

| Ce | Comandă |
|---|---|
| oprire / pornire | `sudo vac stop` / `sudo vac start` |
| actualizare | schimbi versiunile în `.env` → `sudo vac update` |
| backup | `sudo vac backup` (setări + secrete + date Open WebUI) |
| restaurare | `sudo vac restore /var/backups/vellur-ai-core/<fișier>` |
| scoatere de pe internet (rollback) | `sudo vac unexpose` — readuce Caddyfile-ul exact cum era |
| dezinstalare completă | `sudo vac unexpose && cd /opt/vellur-ai-core && sudo docker compose down` (datele rămân în volum) |

## Securitate

- HTTPS prin Caddy-ul existent; site-urile existente nu sunt atinse (o singură linie `import`, validare înainte de
  reîncărcare, restaurare automată la orice eroare).
- `/v1` acceptă doar endpoint-uri de inferență și doar cu o cheie client; cheia master a gateway-ului nu e acceptată
  din internet și nu părăsește serverul. Căile de administrare ale LiteLLM nu sunt publice.
- Cheile nu apar în jurnale (Authorization e mascat de Caddy, X-Api-Key este șters).
- Secretele stau doar pe VPS (`.env`, `secrets/`), niciodată în git (`.gitignore`).
- GPU-ul nu primește nicio cheie a VPS-ului, a GitHub-ului sau a producției.
- Fără porturi noi deschise în firewall; totul trece prin 443, deja deschis pentru Caddy.

## Faza 2 (propunere)

1. Activarea GPU (după aprobare și răspunsurile HyperAI din `docs/GPU-AND-MODELS.md`); alegerea build-ului modelului;
   evaluare pe sarcini VELLUR reale.
2. Mediu izolat pentru agenți de cod (devcontainer sau VM separată), token GitHub fin pe depozite.
3. Automatizare start/stop GPU din OpenCode/Claude Code prin MCP-ul oficial HyperAI.
4. Memorie de proiect partajată (ușoară: fișiere de context versionate + un server MCP), doar dacă aduce valoare.
5. Monitorizare ușoară: `vac verify` programat, cu alertă pe email.
6. Dacă vor fi mai mulți utilizatori: chei cu bugete în LiteLLM (cere o bază de date, deci mai multă memorie).
