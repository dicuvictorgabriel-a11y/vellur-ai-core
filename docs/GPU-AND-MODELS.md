# GPU (HyperAI) și modele — specificație pentru activare

Stare: **pregătit, neactivat**. Nimic din acest document nu a fost cumpărat sau pornit. Prețurile sunt cele publice
găsite pe 13.11.2026 prin căutare; pagina hyper.ai a fost blocată în sesiunea în care s-a scris documentul, deci
**verifică-le pe [hyper.ai/en/pricing](https://hyper.ai/en/pricing) înainte de plată**.

## 1. Ce am aflat despre HyperAI

| Subiect | Ce spun sursele | Sigur? |
|---|---|---|
| Plan Pro | 4,99 $/lună, include **30 ore RTX 5090/lună**, o singură placă; 8 ore CPU; 70 GB stocare | pagina oficială de prețuri (prin căutare); stocarea apare diferit în alte surse (50 GB / 20 GB gratuit) |
| Peste cotă | RTX 5090 la **0,35 $/oră**, din sold preplătit, fracțiile de oră pro-rata | oficial + recenzie terță |
| Rezervare 1 lună | ~0,21 $/oră (≈153 $/lună dacă e pornit 24/7, peste bugetul tău) | doar sursă terță |
| Cozi | RTX 5090 „poate avea cozi la ore de vârf” | pagina oficială |
| Cum se taxează | întâi cota inclusă, apoi soldul contului; dacă soldul nu ajunge, containerul e oprit | documentația „limitation” |
| Risc de cost | doar sold preplătit: nu poate cheltui peste suma încărcată. Reînnoirea automată și rambursările **nu sunt documentate**: de verificat | parțial |
| Oprire automată | spațiul de lucru se oprește după **30 min** de inactivitate (pagina închisă și CPU < 2 %) | documentație |
| Stocare persistentă | doar `/hyperai/home` rămâne după oprire; restul se pierde. Stocare extra: 100 GB = 9 $/lună | documentație + prețuri |
| Instanțe | „Gear”: containere cu Jupyter, pornite din consolă; imagini cu CUDA și vLLM preinstalat (versiune veche în tutorial: 0.8.5) | documentație + tutorial |
| SSH | `ssh root@ssh.openbayes.com -p <port>`, port afișat în panoul „SSH Access” al spațiului; parolă implicit, cheie configurabilă; doar pentru spații pornite | documentație |
| Port public | până la 5 porturi mapate pe subdomenii `*.gear-c1.openbayes.net`; **cere verificare cu acte de identitate** | documentație |
| MCP oficial | da: `mcp.hyper.ai` (OAuth în browser). Poate lista, porni și opri spații și publica modele | documentație |
| Firma | HyperAI pare legată de OpenBayes (Beijing). **Locația GPU-urilor și legea aplicabilă nu sunt clare** | surse terțe, neconfirmat |

Surse: [prețuri](https://hyper.ai/en/pricing) ·
[limite și taxare](https://docs.hyper.ai/gear/limitation) ·
[persistență](https://hyper.ai/en/docs/gear/storage-persistence) ·
[tunel SSH](https://docs.hyper.ai/gear/ssh-tunnel) ·
[SSH](https://hyper.ai/en/docs/gear/ssh) ·
[porturi publice](https://beta.hyper.ai/en/docs/gear/expose-service) ·
[MCP](https://hyper.ai/en/docs/ai-features) ·
[tutorial vLLM](https://hyper.ai/en/notebooks/37048) ·
[recenzie getdeploying](https://getdeploying.com/hyper-ai).

**De lămurit înainte de plată (întrebări pentru HyperAI):**
1. În ce țară rulează GPU-urile și cine poate vedea promptul/codul? (cod VELLUR trimis acolo; GDPR)
2. Se reînnoiește Pro automat? Se poate anula oricând? Orele neconsumate se reportează?
3. Merge autentificarea SSH doar cu cheie (fără parolă)? Portul SSH rămâne același după repornire?
4. Ce versiune CUDA/driver au imaginile RTX 5090 (vLLM recent cere CUDA 12.8+ pentru Blackwell)?
5. Viteza de descărcare de pe Hugging Face (sau există o oglindă)?

## 2. De ce un tunel SSH de la VPS (și nu un port public)

```
OpenCode ─HTTPS─► ai.vellurdigital.com/v1 (Caddy: cheie client) ─► LiteLLM ─► gpu-tunnel ═SSH═► HyperAI: vLLM pe 127.0.0.1:8000
```

- **VPS-ul deschide conexiunea**, cu o cheie SSH folosită doar pentru asta. Spațiul GPU nu primește nicio parolă sau cheie
  a VPS-ului, nici acces la GitHub sau la producție. Primește doar modelul și textul cererilor.
- vLLM ascultă doar pe `127.0.0.1` în spațiul GPU; în plus cere propria cheie API (`VELLUR_GPU_API_KEY`).
- Fără port public, deci fără verificarea cu acte și fără suprafață de atac pe internet.
- Când GPU-ul e oprit, tunelul reîncearcă la 30 s; gateway-ul răspunde imediat cu o eroare clară. Nimic nu repornește în buclă
  (testat).
- Alt furnizor mai târziu: orice server cu SSH funcționează la fel (`vac gpu connect <host> <port>`); unul cu endpoint
  HTTPS se pune direct în `VELLUR_GPU_API_BASE`. OpenCode nu se schimbă.

## 3. Modelul recomandat pentru RTX 5090 (32 GB)

Calculul de bază: memoria = greutăți + cache KV (contextul) + ~2 GB pentru CUDA. Un MoE cu „3B active” tot trebuie să
încapă **întreg** în memorie.

| Model | Tip | Greutăți la 4 biți | Încape pe 32 GB? | Cod (raportat de autori) | Verdict |
|---|---|---|---|---|---|
| **Qwen3.8-27B** (Apache 2.0) | dens 27B, atenție hibridă, context nativ 262k | ~17 GB (NVFP4) | **da**, cu 128k context (cache KV mic datorită atenției hibride) | SWE-bench Pro 61,7; Terminal-Bench 2.1 73,0 | **recomandat** |
| Qwen3.6-27B | dens 27B | ~16 GB | da | SWE-bench Verified 77,2 | rezervă dacă 3.8 dă probleme în vLLM |
| Qwen3.6-35B-A3B | MoE 35B (3B active) | ~20 GB | da, context mai mic | SWE-bench Verified 73,4 | mai rapid, puțin mai slab: bun ca „vellur-coder-fast” |
| Qwen3-Coder-30B-A3B | MoE 30B | ~16 GB | da | ~64 % SWE-bench Verified (estimare terță) | depășit de Qwen3.6/3.8 |
| Devstral Small 2 (24B) | dens | ~12–14 GB | da | 68 % SWE-bench Verified | alternativă non-Qwen |
| Qwen3-Coder-Next (80B-A3B) | MoE 80B | ~45–52 GB | **nu** (doar ~Q2 cu pierdere de calitate) | 70,6 % | nu pe o singură 5090 |
| Qwen3.8-27B FP8 | dens | ~28 GB | **nu** (nu rămâne loc pentru context) | — | nu |

Așteptări realiste pe o singură 5090 (măsurători ale comunității, nu ale noastre): ~40–120 token/s la generare pentru
Qwen3.8-27B NVFP4, în funcție de context și de activarea MTP (decodare speculativă). Contextul foarte lung încetinește
mult prima replică (prefill); de aceea pornim cu **131 072 tokeni** și cache de prefix activat.
Calitatea: bună pentru sarcini de cod obișnuite și agentice, **sub Claude/Codex** la sarcini lungi și complexe. Evaluăm
cu sarcini reale VELLUR după activare.

Surse: [model card Qwen3.6-27B](https://huggingface.co/Qwen/Qwen3.6-27B) ·
[Qwen3.8-27B](https://www.morphllm.com/qwen-3-8) ·
[Qwen3.8 pe RTX 5090 (Tom's Hardware)](https://www.tomshardware.com/tech-industry/artificial-intelligence/benchmarking-qwen-3-8-27b-on-rtx-5090-and-beyond-vram-capacity-alone-cant-overcome-severe-software-and-inference-engine-bottlenecks) ·
[vLLM vs NInfer pe 5090](https://gist.github.com/PierpaoloPernici/f1d1382f8e357b4faffb1a9f584cc1df) ·
[Qwen3-Coder-Next VRAM](https://blogs.novita.ai/qwen3-coder-next-the-vram/) ·
[ghid 32 GB](https://openclawdc.com/blog/best-local-llm-32gb-vram/).

**Build-ul exact de 4 biți nu e ales încă**: Hugging Face a fost blocat aici. La activare alegem o variantă NVFP4 sau AWQ
a Qwen3.8-27B de la o sursă de încredere (Qwen oficial dacă există, altfel RedHatAI/Unsloth), verificăm licența și
dimensiunea, și o scriem în `MODEL_REPO`.

## 4. Cost estimat (Pro + ore suplimentare la 0,35 $/h)

| Utilizare pe lună | Ore GPU | Cost |
|---|---|---|
| ușoară | ≤ 30 | **4,99 $** |
| ~4 h/zi lucrătoare | ~88 | 4,99 + 58 × 0,35 ≈ **25 $** |
| ~8 h/zi lucrătoare | ~176 | 4,99 + 146 × 0,35 ≈ **56 $** |
| plafonul tău (~100 €) | ~300 | ≈ 105 $ |
| 24/7 | 730 | ≈ 250 $ — **nu**; GPU-ul se pornește doar la nevoie |

Plus eventual stocare extra (9 $/lună pentru 100 GB) dacă 70 GB nu ajung. Recomandare: încarcă sold mic (ex. 10–20 $),
astfel încât o greșeală să nu poată costa mai mult.

## 5. Procedura de activare (după aprobarea ta)

Pe VPS:
```bash
sudo vac gpu init            # arată cheia publică SSH și cheia API pentru vLLM
```
În HyperAI (consola web):
1. Abonament Pro + sold mic (aprobarea ta).
2. Adaugă cheia publică SSH în setările contului.
3. Creează un spațiu Jupyter cu **RTX 5090**, familia de imagini **VLLM**, cea mai nouă versiune (la 13.11.2026: `vllm-0.27.1-2404`, CUDA 13.0). Scriptul folosește vLLM-ul din imagine.
4. În terminalul spațiului: copiază `gpu/hyperai-serve.sh` în `/hyperai/home/`, apoi
   ```bash
   export VLLM_API_KEY='<cheia de la vac gpu init>'
   export MODEL_REPO='<build NVFP4/AWQ verificat al Qwen3.8-27B>'
   bash /hyperai/home/hyperai-serve.sh        # prima dată: instalează vLLM și descarcă modelul (o dată)
   ```
   Pe **RTX 3090 (24 GB)**: `MODEL_REPO=RedHatAI/Qwen3.8-27B-INT4`, `MAX_MODEL_LEN=32768`, `KV_CACHE_DTYPE=auto`.
5. Notează host-ul și portul din panoul „SSH Access”.

Pe VPS:
```bash
sudo vac gpu connect ssh.openbayes.com <port>    # verifică amprenta, pornește tunelul, face un test real
sudo vac verify
```
La final de zi: `sudo vac gpu down` **și** oprește spațiul în consola HyperAI. Plasă de siguranță: scriptul oprește
vLLM după 20 min fără cereri, iar HyperAI oprește spațiul după încă ~30 min.

## 6. Ce rămâne neverificat până la activare

- Funcționarea reală pe HyperAI: SSH cu cheie, portul, viteza descărcării, versiunea vLLM compatibilă cu Qwen3.8 pe 5090.
- Parserele de tool calling (`qwen3_coder`) și de raționament (`qwen3`) pentru Qwen3.8: de confirmat pe model card.
- Calitatea în OpenCode pe sarcini VELLUR reale.
- Opțiunile de plată și reînnoire ale HyperAI.
