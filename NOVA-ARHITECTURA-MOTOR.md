# NOVA — Motor stabil (arhitectura de la `63f7b10`)

NOVA este motorul. Aplicațiile viitoare sunt produsele, construite PE deasupra motorului.

## Straturile motorului (fișiere reale, verificate la `63f7b10`)

**1. NOVA Core — baza**
- `main.py` (pornește aplicația), `config.py`, `settings.py`, `db.py`
- securitate și limite: `auth.py` (înghețat), `policy.py`, `budget.py`
- observabilitate: `health.py`

**2. Strat modele (de unde vine AI-ul)**
- `router.py` — o singură ușă pentru inferență; nume publice → furnizori
- `providers/`, `proxy.py`, `tunnel.py`, `model_intel.py`

**3. Strat execuție (ce face motorul)**
- `agent.py` — orchestrare sarcini + recovery; rulează agenți izolați cu tools
- `lifecycle.py`, `mission.py`, `notify.py`

**4. Strat workspace / proiecte**
- `workspace.py` (conversații cu istoric), `knowledge.py`, `attachments.py`, `images.py`, `timeline.py`

**5. Strat API — suprafața stabilă pentru produsele viitoare**
- `/healthz` — stare
- `POST /autopilot/api/auth/*` — autentificare (login, logout, reset, schimbare parolă)
- `GET /autopilot/api/status | events | history` — observabilitate
- `GET /autopilot/api/public/status | public/events` — vizualizare publică (fără date sensibile)
- `POST /autopilot/api/start | stop | protect`, `PUT /autopilot/api/settings`
- `POST /autopilot/api/hyperai/connect | billing/sync | providers/{name}/check | hostkeys/forget`
- API sarcini agent: `/autopilot/api/agent/*` (taskuri, preview, diff, delivery)

**6. Interfață minimă de administrare**
- `static/dashboard.html` — panou de control (stări, costuri, sarcini). NU este produs pentru clienți.

## Reguli pentru produsele viitoare
1. Produsele consumă API-ul de mai sus; NU modifică motorul pentru nevoi proprii.
2. Fiecare produs nou își are propriul director/serviciu; dashboard-ul nu se multiplică.
3. Limitele de resurse rămân în `budget.py` + `policy.py` — un singur loc.
4. Nimic din motor nu depinde de un produs anume.

## Stadiul curent
- Ramura `rollback/engine-baseline-63f7b10` = motorul curat (278 teste trecute).
- Starea de produs „Artist Desk" păstrată pe `nova/artist-desk-backup`.
- `main` și producția: neatinse până la aprobare separată.
