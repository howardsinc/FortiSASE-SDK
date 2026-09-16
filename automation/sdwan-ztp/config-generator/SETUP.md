# Setup — FortiSASE BOR/SPA Config Generator + MSSP Deploy

The single onramp. Follow top to bottom and you'll have the app running with both its offline
config generator **and** its live FortiManager provisioning. Works for a human or an AI coding
agent — every step is copy-pasteable and uses no machine-specific paths.

**Two things to know first:**
- The **Config Generator** works **standalone** — no FortiManager, no credentials. If all you want
  is to produce `.conf` / CSV files, do Steps 1, 2, 4 and skip the FMG bits.
- The **MSSP Deploy** page (create ADOMs, import devices, install to FMG) needs a second repo
  (**FortiManager-AI-SDK**) and FMG credentials — Steps 1b and 3.

---

## Prerequisites

- **Python 3.9+** — check with `python --version` (Windows: install from
  <https://www.python.org/downloads/>, tick *"Add Python to PATH"*).
- **git**
- **pip** (ships with Python)
- For MSSP Deploy only: a **FortiManager 7.6+** you can reach on the network.

---

## Step 1 — Clone the repos side-by-side

Pick any folder. Clone **both repos into it, next to each other** — the app auto-detects the SDK
when they share a parent folder.

```bash
cd <your-folder>
git clone <FortiSASE-SDK repo URL>            FortiSASE-SDK
# Step 1b (only needed for MSSP Deploy / FMG provisioning):
git clone https://github.com/howardsinc/FortiManager-AI-SDK.git   FortiManager-AI-SDK
```

You should end up with exactly this layout:

```
<your-folder>/
├── FortiSASE-SDK/          ← this repo (the app)
└── FortiManager-AI-SDK/    ← the FMG automation tools (MSSP Deploy calls these)
```

> The app finds the SDK automatically in any of: the `FMG_SDK_DIR` env var → inside the repo
> (`FortiSASE-SDK/FortiManager-AI-SDK`, e.g. a submodule) → **side-by-side sibling** (the layout
> above). If you clone them somewhere non-adjacent, set `FMG_SDK_DIR` to the SDK's path instead.

---

## Step 2 — Install Python dependencies

```bash
cd FortiSASE-SDK/automation/sdwan-ztp/config-generator
pip install -r requirements.txt
```

(That's `streamlit`, `jinja2`, `pyyaml`, `requests` — enough for the app **and** the SDK tools it
shells out to.)

---

## Step 3 — FortiManager credentials  *(MSSP Deploy only — skip for offline use)*

The app never handles credentials directly — the SDK reads a **Bearer token** from a file. Set it
up once per workstation.

**3a. Create a REST API admin in FortiManager**
FMG GUI → *System Settings → Admin → Administrators → Create New*:
- **Name:** `FMG_REST_API`
- **Type:** REST API Admin
- **Admin Profile:** `Super_User` (or a profile with ADOM edit rights)
- **Trusted Hosts:** your workstation's IP
- FMG shows a **Bearer token once** — copy it now.

**3b. Drop the token into the creds file**
Create `~/.config/mcp/fortimanager_credentials.yaml`
(Windows: `C:\Users\<you>\.config\mcp\fortimanager_credentials.yaml`):

```yaml
devices:
  my-fmg:                       # any friendly name — shows in the app's host picker
    host: 192.0.2.10            # your FMG IP or FQDN
    port: 443
    auth_method: token
    api_token: PASTE-YOUR-BEARER-TOKEN-HERE
    username: FMG_REST_API
    verify_ssl: false           # true if your FMG has a trusted cert
```

Add more `friendly-name:` blocks under `devices:` for additional FortiManagers.
(See the FortiManager-AI-SDK's own `QUICKSTART.md` for the authoritative token details.)

---

## Step 4 — Run the app

```bash
cd FortiSASE-SDK/automation/sdwan-ztp/config-generator
streamlit run app.py
```

Open <http://localhost:8501>. Three pages in the left sidebar:

| Page | Needs FMG? | What it does |
|---|---|---|
| 🛰️ **Config Generator** (home) | No | Fill a per-site form → validated FortiOS **BOR** / **BOR+SPA** config → download `.conf` **and** an FMG-import CSV. Per-site **Primary on-ramp PoP** (POP1/POP2) flips a spoke to the secondary BOR to split a fleet across PoPs; object names are role-based (`BOR_Primary`/`Secondary`) so a NOC reads them the same across tenants |
| 📊 **FortiSASE Tenant Status** | No (FortiSASE API creds) | Read-only dashboard: BGP / SPA / BOR green-lights + PoP mapping |
| 🚀 **MSSP Deploy** | **Yes** (Steps 1b + 3) | Point-and-fire FMG: create a customer ADOM, import model devices from a CSV, install — each with a dry-run/preview first |

---

## The end-to-end deploy flow (MSSP Deploy page)

1. **Connect** — pick your FMG from the host dropdown → **🔌 Test Connection** → 🟢 green.
2. **② Target ADOM** — pick an existing customer ADOM, or **➕ Create new** (name it → 🔍 dry-run
   preview → 🚀 create; ~300 objects bootstrapped). *ADOM names are case-sensitive.*
3. **③ Push devices** — upload a device CSV (from the Config Generator's **Export FortiManager
   CSV** button). Pre-flight flags any blank required fields → **🔍 Dry-run** → **🚀 Import**.
   One row = one device; a whole fleet imports at once.
4. **④ Install** — each device gets **🔍 Preview** (validate, safe) and **🚀 Install** (device
   settings + policy package). Offline model devices hold a rev until they dial home (FGFM).

---

## Troubleshooting

- **"FortiManager-AI-SDK not found"** on the MSSP Deploy page → the SDK repo isn't where the app
  looks. Either clone it **next to** `FortiSASE-SDK` (Step 1b) or set `FMG_SDK_DIR` to its path and
  restart. *(The Config Generator page still works without it.)*
- **"Connection failed" / red gate** → the token in the creds yaml is wrong/expired, or the FMG
  host/trusted-hosts don't match. Re-check Step 3.
- **Edited `generator.py` or the schema and don't see the change** → Streamlit caches imported
  modules; **stop and re-run** `streamlit run app.py`. (Editing a page file hot-reloads fine.)
- **Keeping the SDK current** → the two repos are independent; `git pull` in `FortiManager-AI-SDK`
  to get the FMG coder's latest tool/template fixes.
