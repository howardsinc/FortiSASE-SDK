# BOR / BOR+SPA Config Generator

Self-service generator: an SE picks a **role** (BOR Node / BOR+SPA Hub) + **model**,
fills a plain-English per-site form, and gets a validated, ready-to-**ZTP** FortiOS
config — download, or push to **FortiManager / FortiZTP**.

## Design principle: schema-first
`schema/variables.yaml` is the **single source of truth**. It drives *both* the config
rendering *and* the SE form — so they can never drift. Add/edit/remove a variable in one
place and the form + template follow (MACD).

**Scope split** keeps the form short:
| Scope | Entered | Examples |
|---|---|---|
| `tenant` | once per FortiSASE tenant | AS, PoPs, SLA, BGP timers, crypto |
| `site` | per device | hostname, WAN/LAN, router-id |
| `secret` | never stored — injected at deploy | PSKs, admin pw |
| `derived` | computed, never prompted | LAN subnet, fabric pool, object names |
| `const` | fixed baseline | mtu, dpd, ike-version |

The **2 PoPs are modeled as tenant objects** (`pops:`), so the template iterates them
instead of hard-coding Dallas/Miami — generalizes to any tenant.

## Build phases
- [x] **0 · Schema** — `schema/variables.yaml` (source of truth) + HTML variable index
- [x] **1 · Templatize** — `templates/{bor,bor-spa}.conf.j2`, dependency-ordered
- [x] **2 · Generator core** — `generator.py` (`--roundtrip` / `--values site.yaml`)
- [x] **3 · Streamlit UI** — `app.py`, schema-driven form → Generate → download
- [ ] **4 · ZTP output** — FortiManager API push / FortiZTP bundle (download works today)

## Run
```
pip install -r requirements.txt
python generator.py --roundtrip        # render the 3 known sites -> generated/
streamlit run app.py                   # the SE form (http://localhost:8501)
```
> **Installing on a Windows laptop, step by step? Follow [`INSTALL-WINDOWS.md`](INSTALL-WINDOWS.md).**
> **Setting up a partner (or an AI agent) from scratch? Follow [`SETUP.md`](SETUP.md)** — it covers
> both the offline generator and the live FortiManager provisioning in one doc.

**The app has three pages** — switch between them from the left sidebar:

| Page | URL | What it's for |
|---|---|---|
| **Config Generator** (home) | `http://localhost:8501` | Fill the per-site form → validated FortiOS **BOR** / **BOR+SPA** config → download `.conf` + FMG-import CSV |
| **FortiSASE Tenant Status** | `http://localhost:8501/FortiSASE_Tenant_Status` | Read-only dashboard — paste FortiSASE **API creds** → green-lights for **BGP / SPA / BOR** + public-IP→PoP mapping |
| **MSSP Deploy** | `http://localhost:8501/MSSP_Deploy` | Point-and-fire **FortiManager** provisioning — create ADOMs, import devices, install. Needs the **FortiManager-AI-SDK** (see `SETUP.md`) |

## Roles & key features
Pick a **role** + **model**; the generator renders the matching FortiOS config + FMG-import CSV.

| Role | What it is |
|---|---|
| **BOR Node** — single / dual circuit | Branch spoke: 1 or 2 WANs, IPsec overlays to two FortiSASE PoPs |
| **BOR + SPA Hub** — single / dual | Hub that terminates the SPA fabric + relays BOR overlays for site-to-site private access |

- **Alt-primary (`Primary on-ramp PoP`)** — a per-site choice (POP1 default / POP2). Set **POP2** to flip a whole **spoke** site's traffic (private *and* internet) to the **secondary** BOR node, so you can split a fleet across both PoPs and share the tenant's aggregate SASE bandwidth. Rides straight through config → CSV → FMG install. *(Spoke roles only — SPA hubs pin to primary; a hub's BOR on-ramps are outbound-only and don't run BGP.)*
- **Role-based object naming** — tunnels, route-maps, health-checks, and address objects use stable **role** names (`BOR_Primary` / `BOR_Secondary`, `RM_FABRIC_IN`, …), never tenant-specific PoP identity (Dallas / NY / Ashburn), so a NOC reads them the same across every tenant. PoP identity stays in comments only. A build-time drift guard fails loud if identity ever leaks into an object name.
- **FGFM underlay pin (`fmg_ip`)** — tenant-level FortiManager address renders a `/32` static route out the WAN underlay (route 12, +13 on dual), so FMG management never migrates onto the SASE overlay once the on-ramp default takes over. Blank = no route.
- **Placeholder guard** — the CSV import pre-flight rejects a blank or placeholder (`<…>`) PoP FQDN with a clear *"enter the real FortiSASE BOR PoP FQDN"* message, before anything reaches FortiManager.

To change ANY field/section, follow **`SKILL-READ-FIRST_MACD.md`** (schema-first).

## Acceptance gate for Phase 0→1
The schema + template must **reproduce the committed `site-1_bor-spoke.conf` and
`site-5_bor-spa-hub.conf` byte-for-byte** (minus secrets). If it round-trips, the extraction
is complete.

## Sources
`../Finalized-Template/bor-spoke.conf` (BOR) · `../Finalized-Template/site-5_bor-spa-hub.conf`
(BOR+SPA) · `../Finalized-Template/Send-Communities/` (BGP tags).
