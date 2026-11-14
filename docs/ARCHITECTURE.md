# Arhitectura VELLUR AI Core (resetare strategică, 14.11.2026)

**Decizie:** platforma e acum **API-first, cu GPU opțional**. Modelele open-weight se consumă prin API-uri compatibile
OpenAI (plată pe token, plan fix sau nivel gratuit); un GPU închiriat (Vast.ai, HyperAI) rămâne un backend posibil,
dar nu mai e necesar. HyperAI nu mai e furnizor principal: plăcile lui au fost constant „FULL LOAD”.

```
OpenCode / aplicații ─► ai.vellurdigital.com/v1 ─► LiteLLM ─┐
Spațiul de lucru (/autopilot/: chat, agent) ────────────────┼─► routerul VELLUR ─► API-uri de modele (OpenRouter, Groq, DeepSeek, …)
Agent (container izolat, OpenCode) ── token temporar ───────┘                  └─► GPU Autopilot (Vast.ai / HyperAI), opțional
```

## De ce (cercetare 14.11.2026; prețuri din surse publice, de verificat la furnizor înainte de plată)

| Arhitectură | Cost realist pe lună | Calitate cod | Pornire | Disponibilitate | Verdict |
|---|---|---|---|---|---|
| **API modele open-weight** | 0 $ (niveluri gratuite) … ~5–30 $ la uz intens (DeepSeek V4.1 Flash ≈ 0,15 $ / 0,60 $ per milion tokeni; Kimi K3 ≈ 2,5 $ / 14 $) | cea mai bună accesibilă: modele mari (Kimi K3, GLM-5.x, DeepSeek V4.x) care nu încap pe un GPU de buget | instant | mare, mai mulți furnizori | **ales** |
| Plan fix (abonament) | OpenCode Go ≈ 10 $, GLM Coding Plan ≈ 18 $, Synthetic ≈ 30 $ | aceleași modele, cu limite de utilizare | instant | bună | bun dacă vrei cost fix; se adaugă ca furnizor „subscription” |
| GPU serverless (RunPod) | ~0,69 $/h clasa 24 GB, facturat și la pornire | doar modele ≤ 24–48 GB (Qwen3.8-27B) | rece: secunde–minute | bună | mai scump și mai slab decât API-ul pentru aceleași modele |
| GPU la cerere (Vast.ai) | RTX 5090 ≈ 0,23–0,65 $/h → 20–60 $ la 4 h/zi | Qwen3.8-27B (sub modelele mari) | 5–15 min la instanță nouă | variabilă | opțional, deja implementat (netestat real) |
| GPU fix lunar | Hetzner GEX44 (20 GB) ≈ 184 €; GEX131 ≈ 889 € | limitat de VRAM | instant | mare | peste buget |
| HyperAI Pro | 4,99 $ (deja plătit) | — | — | **indisponibil în practică** | păstrat doar ca opțiune |

Modele (noiembrie 2026, raportări ale autorilor și agregatoare; **netestate de noi**): Kimi K3 și GLM-5.2/5.3 sunt
citate ca cele mai bune open-weight pentru agenți de cod; DeepSeek V4.1 Flash e opțiunea ieftină serioasă; Qwen3.8-27B
e cel mai bun care încape pe 32 GB. Recomandare de pornire: `vellur-coder` → DeepSeek V4.1 Flash, `vellur-max` →
Kimi K3 sau GLM-5.3, `vellur-chat` → un model gratuit. ID-urile exacte se iau din lista live a furnizorului
(Models → „Models”), nu din acest document. Calitatea reală se judecă pe sarcini VELLUR, nu pe clasamente.

Niveluri gratuite (legitime, cu limite): OpenRouter `:free` — 50 cereri/zi (1000/zi după o încărcare de 10 $);
Groq — limite pe minut și zi; Mistral — nivel de experiment; Gemini — gratuit, dar conținutul poate fi folosit de
Google; Cerebras — sursele se contrazic în 2026. Nu se ocolesc limitele și nu se fac conturi multiple.

Agent: **OpenCode** (MIT, activ: 1.19.5 din 28.10.2026, mod headless `opencode run`), rulat într-un container izolat.
Aider nu a mai avut lansări din februarie 2026; OpenHands e mai greu și cere acces la Docker. Notă: în octombrie 2026
serviciul găzduit „OpenCode Console” a avut o breșă de chei; noi folosim doar CLI-ul local, fără Console.

## Componente

| Componentă | Rol |
|---|---|
| `autopilot/app/router.py` | lanțuri de ținte pe fiecare nume public (`vellur-coder`, `vellur-chat`, `vellur-max`): `furnizor:model` sau `gpu`; chei doar pe server; evidență tokeni/cost/latență pe fiecare cerere; cost raportat de furnizor separat de estimare; țintele plătite se opresc la buget; comutarea pe o țintă plătită doar cu „Paid fallback” |
| `autopilot/app/workspace.py` | conversații persistente, furnizori și rute, raport de costuri, sarcini de agent (toate după login) |
| `autopilot/app/agent.py` + `agent/` | sarcini de cod: instantaneu al repo-ului de pe GitHub (tokenul rămâne în controller) → container izolat cu OpenCode → jurnal + diff → la cerere, branch + pull request draft prin API-ul GitHub. Limite pe sarcină: minute și USD |
| GPU Autopilot (`lifecycle.py`, `providers/`) | neschimbat ca funcție; acum e doar una dintre ținte. Adăugat: limită de cost pe sesiune |
| `autopilot/app/auth.py` | **înghețat** (login-ul aprobat); un test verifică hash-ul fișierului |

Rutare automată după tipul cererii: **neimplementată**. Fără un model real nu am putut măsura un beneficiu, iar
regula cerea beneficiu măsurabil. Alegerea e manuală, prin numele modelului.

## Costuri

Bugetul lunar acoperă: GPU (confirmat/estimat) + API (raportat de furnizor/estimat) + abonamente + costurile fixe
marcate „în buget”. Costurile fixe din afara bugetului (VPS, HyperAI Pro) apar în totalul „all-in”. La buget epuizat:
țintele plătite și GPU-ul sunt blocate; cele gratuite merg în continuare. Plafonul de pe server rămâne
(`sudo vac autopilot limits <lunar> <orar>`). Limita controllerului nu înlocuiește limitele de la furnizor: folosește
sold preplătit și, la OpenRouter, o limită de credit pe cheie.

## Ce e testat

93 de teste automate (router, costuri, buget, conversații, agent, GitHub simulat, autentificare, GPU). În sandbox, cap
la cap: instalare peste versiunea veche, Caddy real, login neschimbat, chat persistent, sarcină de agent reală
(OpenCode în containerul izolat a executat o comandă cerută de un backend de test și a raportat diff-ul).
**Netestat:** orice furnizor real de modele, GitHub real, calitatea unui model real.
