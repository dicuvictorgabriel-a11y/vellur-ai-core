# GPU Autopilot

Autopilotul pornește un GPU când AI-ul are nevoie, îl păstrează cât lucrezi și îl eliberează după inactivitate reală,
în limita bugetului. Panoul e la **https://ai.vellurdigital.com/autopilot/**:
- **public, doar vizualizare:** oricine vede starea GPU-ului, furnizorul și bugetul (fără adrese, porturi, ID-uri de
  instanță, setări sau mesaje de eroare detaliate);
- **„Log in for controls”:** formular în pagină (fără fereastra browserului). Prima parolă e **temporară** și se
  setează pe server cu `sudo vac autopilot password [parola]`; la primul login panoul te obligă să alegi una proprie
  (minim 12 caractere). Sesiunea ține 30 de zile; „Log out” o închide. O parolă nouă setată pe server deconectează
  toate sesiunile.

> **Limită importantă.** Un controller de pe VPS **nu poate garanta un plafon strict de cheltuieli**: un furnizor poate
> continua să factureze, poate raporta cu întârziere sau poate deveni inaccesibil exact când trebuie oprit GPU-ul.
> Autopilotul blochează, oprește și alertează cât poate, dar protecția reală e la furnizor: **sold preplătit mic și
> reîncărcare automată (auto top-up) oprită**.

## Ce face, pe scurt

```
mesaj (OpenCode, chat, aplicație) → ai.vellurdigital.com/v1 → LiteLLM → Autopilot (gpu-backend:8000) → GPU
                                                                   │
                     există GPU gata? → da: trimite cererea (și o numără ca activitate)
                                      → nu: verifică politica și bugetul → pornește/atașează → așteaptă modelul → trimite
```

- **Stări:** offline · searching · provisioning · starting · loading_model · ready · processing · idle · stopping ·
  stopped · failed (la HyperAI: connecting în loc de searching/provisioning/starting). Panoul le arată live.
- **Oprirea compute-ului** e marcată „confirmed” numai când furnizorul raportează instanța oprită sau distrusă după
  cererea de oprire. Oprirea vLLM sau închiderea tunelului **nu** înseamnă că facturarea s-a oprit.
- **Inactivitatea** se măsoară la nivel de aplicație: cereri active (inclusiv tot streaming-ul), cereri în așteptare,
  sarcini protejate (`/autopilot/api/protect`), ultima cerere încheiată. Oprirea are loc numai dacă nimic nu e activ,
  nimic nu așteaptă și a trecut timpul setat (implicit 20 min). O eroare de monitorizare **nu oprește niciodată** GPU-ul.
- **Fără curse:** pornirea și oprirea sunt idempotente (10 cereri simultane = o singură instanță), starea e în SQLite
  și se reia după restartul controllerului sau al VPS-ului.
- **Watchdog separat** la 15 s: inactivitate, durata maximă a sesiunii (implicit 3 h), buget, reîncercarea opririlor
  neconfirmate, instanțe „orfane” cu eticheta `vellur-autopilot` (oprite automat doar în modul live), sincronizare facturi.

## Furnizori

| Furnizor | Mod | Ce face automat | Ce rămâne manual |
|---|---|---|---|
| **HyperAI** | asistat | se conectează prin SSH la workspace (portul îl introduci în panou sau `sudo vac gpu connect <port>`), detectează modelul gata, numără orele incluse, alertează la inactivitate, detectează când workspace-ul a dispărut | pornirea și oprirea workspace-ului în consola HyperAI |
| **Vast.ai** | automat | caută oferte (VRAM, preț maxim, fiabilitate, preferință geografică), închiriază, atașează cheia SSH, pornește vLLM, verifică, oprește/distruge, citește soldul și facturile | contul, cheia API și alimentarea soldului: decizia ta |
| mock | doar teste | — | — |

**De ce HyperAI e asistat:** la 14.11.2026 HyperAI nu are un API documentat pentru pornire/oprire. Serverul MCP oficial
(`mcp.hyper.ai`) cere autentificare interactivă în browser (OAuth, client pre-înregistrat), iar CLI-ul `bayes` e
documentat doar pentru OpenBayes. Nu am inventat endpoint-uri. Dacă apare un API oficial, se adaugă în
`autopilot/app/providers/hyperai.py` fără alte schimbări.

**Vast.ai** folosește exact apelurile CLI-ului oficial `vastai` 1.11.0 (listate în `providers/vastai.py`). La oprire,
implicit **distruge** instanța (fără costuri de disc); opțional „stop” păstrează discul (facturat) pentru reporniri
mai rapide. **Netestat cu un cont real** (nu exista cheie).

## Buget

- Implicit: **50 $/lună**, praguri **50 / 75 / 90 / 100 %**, tarif maxim **0,50 $/h**, **1** GPU activ, inactivitate
  **20 min**, sesiune maximă **3 h**, **pornire automată a GPU-urilor plătite: OPRITĂ**, **fallback plătit: OPRIT**.
- **Plafoane pe server:** `AUTOPILOT_HARD_MAX_MONTHLY_USD`, `AUTOPILOT_HARD_MAX_HOURLY_USD` (în `.env`, schimbate doar
  cu `sudo vac autopilot limits <lunar> <orar>`). Panoul nu poate trece de ele, nici dacă ar fi compromis.
- **Confirmat vs. estimat:** „provider-confirmed” = facturi raportate de furnizor (Vast.ai: `/users/me/invoices`);
  restul e **estimare** (ore × tarif + traficul de descărcare a modelului) și e afișat separat. HyperAI nu oferă date
  de facturare: orele incluse și costul suplimentar sunt **estimate** din sesiunile Autopilotului.
- **Înainte de orice închiriere:** buget disponibil, tarif ≤ limită, minim 30 min de rulare + costuri unice în buget.
- **La 100 %:** cererile noi sunt refuzate, GPU-ul activ e oprit după terminarea cererilor active (maxim 10 min),
  alertă; oprirea neconfirmată = alertă critică + reîncercări.

## Siguranță

- **DRY RUN implicit:** nu se poate crea, porni sau opri nimic plătit. Se activează doar pe server:
  `sudo vac autopilot live on` (cere să tastezi LIVE). Citirile (oferte, sold) merg și în dry run.
- Panoul: pagina și `/autopilot/api/public/*` sunt publice și filtrate (maxim 20 de fluxuri live publice simultan).
  Restul cere login: parola e stocată doar ca hash PBKDF2-SHA256; sesiunea e un cookie HttpOnly, Secure,
  SameSite=Strict, limitat la /autopilot/; 5 încercări greșite pe IP (30 în total) în 15 min = blocare temporară.
  Antetul secret al serverului e folosit doar de `vac` local; Caddy îl șterge din orice cerere venită din internet.
  Scrierile cer și antet anti-CSRF + aceeași origine. Cheile furnizorilor nu ajung în browser.
- Container: utilizator neprivilegiat (10001), sistem de fișiere doar-citire, fără capabilități, 256 MB, 0,5 CPU,
  fără socket Docker, port doar pe 127.0.0.1. Măsurat: ~51 MB RAM, ~0,2 % CPU.
- SSH: cheie dedicată (`secrets/gpu_ed25519`), GPU-ul nu primește nicio cheie a VPS-ului; cheia serverului SSH e
  memorată la prima folosire și o schimbare **blochează** conexiunea (`sudo vac autopilot forget-host <host>` după o
  schimbare legitimă).
- Testele automate nu pot apela un furnizor real (gardă în teste și în adaptor).

## Comenzi

```bash
sudo vac autopilot status                  # faza, sesiunea, bugetul
sudo vac autopilot enable | disable        # pornește/oprește controllerul (installerul îl pornește în dry run)
sudo vac autopilot password [temporară]    # parolă temporară pentru panou (o schimbi la primul login)
sudo vac autopilot live on | off           # acțiuni reale la furnizori (doar pe server)
sudo vac autopilot limits 50 0.50          # plafoane pe server: lunar, orar
sudo vac autopilot forget-host ssh.hyper.ai
sudo vac gpu connect <port>                # HyperAI: atașează workspace-ul pornit (portul din panoul SSH)
sudo vac gpu down                          # eliberează GPU-ul (la HyperAI apasă și Stop execution)
sudo vac verify                            # include verificările Autopilot
```

Notificări: implicit în panou și în jurnalul de evenimente. Opțional, `AUTOPILOT_NOTIFY_URL` în `.env` (de exemplu un
topic ntfy găzduit de tine) primește alertele ca POST text.

## Activare reală — ce trebuie făcut de tine

**HyperAI (abonamentul există):** nimic în plus. Pornești workspace-ul și scriptul, apoi în panou introduci portul
SSH (sau `sudo vac gpu connect <port>`). Autopilotul se ocupă de rest și îți spune când să-l oprești.

**Vast.ai (opțional, cost):**
1. Cont Vast.ai, sold preplătit mic, auto top-up oprit (decizia și plata ta).
2. Cheie API (Account → Keys) pusă în `/opt/vellur-ai-core/.env` ca `VAST_API_KEY=…` (nu în chat), apoi
   `sudo vac autopilot enable`.
3. În panou: „Check offers (no charge)” arată cea mai ieftină ofertă potrivită.
4. Pentru închirieri reale: `sudo vac autopilot live on`; pornire din panou sau, dacă vrei pornire la primul mesaj,
   activezi „Automatic start of paid GPUs”.

Prima închiriere reală pe Vast.ai trebuie urmărită: imaginea `vllm/vllm-openai:v0.27.1` și modelul (~19 GB) se
descarcă la fiecare instanță nouă (5–15 min). Abia după un test real putem spune că provizionarea automată merge.

## Ce e testat

| Verificare | Rezultat |
|---|---|
| 60 de teste automate (buget, setări, tranziții, concurență, dubluri, inactivitate, cereri lungi, sesiune maximă, buget epuizat, erori și timeout-uri de furnizor, credențiale lipsă, oprire neconfirmată, reconciliere facturi, recuperare după restart, orfani, autentificare, CSRF, proxy, adaptor Vast.ai cu răspunsuri simulate) | trec |
| Sandbox: instalare peste o versiune veche, Caddy real cu un site existent simulat, panou public cu login, tunel SSH real (asyncssh) către un sshd care simulează HyperAI, cereri prin tot lanțul (inclusiv streaming și chatul din panou), recuperare după restart, workspace oprit, protecție la cheie SSH schimbată | trec |
| Caddy 2.6 / 2.7 / 2.8 / latest | configurație validă |
| Furnizori reali (HyperAI cu GPU, Vast.ai) | **netestat** — fără cheie Vast.ai; HyperAI cere GPU pornit |

Teste: `cd autopilot && pip install -r requirements-dev.txt && pytest -q`.

## Fișiere

`autopilot/app/`: `lifecycle.py` (stări, watchdog), `budget.py`, `providers/` (`base.py` interfața, `hyperai.py`,
`vastai.py`, `mock.py`), `tunnel.py` (SSH), `proxy.py` (inferență), `api.py` (panou), `settings.py`, `db.py`
(SQLite în volumul `vellur-ai-core-autopilot-data`), `static/dashboard.html`. Un furnizor nou (RunPod, server dedicat,
GPU propriu) = o clasă nouă care implementează `GPUProvider`.
