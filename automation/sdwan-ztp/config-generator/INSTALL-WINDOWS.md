# Install on a Windows laptop — step by step

For an engineer setting this up for the first time. Every step is a command you paste into
**PowerShell** (press the Windows key, type `PowerShell`, open it). Each step ends with a
**Check** so you know it worked before moving on. Total time: about 20 minutes.

You will end up with the app running in your browser, able to (1) build FortiGate configs
offline and (2) push them to FortiManager.

---

## Step 1 — Install Git

1. Download: <https://git-scm.com/download/win> → **64-bit Git for Windows Setup**.
2. Run the installer. Click **Next** on every screen (the defaults are fine). Click **Finish**.
3. **Close PowerShell and open a new one** (so it sees the new program).

**Check** — paste:
```powershell
git --version
```
You should see something like `git version 2.47.1.windows.1`.

---

## Step 2 — Install Python

1. Download: <https://www.python.org/downloads/windows/> → the latest **Python 3.12** or **3.13**
   "Windows installer (64-bit)".
2. Run it. On the first screen **tick the box "Add python.exe to PATH"** (bottom of the window —
   easy to miss), then click **Install Now**.
3. **Close PowerShell and open a new one.**

**Check** — paste:
```powershell
python --version
```
You should see `Python 3.12.x` or `3.13.x`. If PowerShell says `python is not recognized`, re-run
the installer and make sure the PATH box is ticked.

---

## Step 3 — Download the two repositories

Paste these four lines one at a time:
```powershell
New-Item -ItemType Directory -Force C:\Projects
cd C:\Projects
git clone https://github.com/howardsinc/FortiSASE-SDK.git
git clone https://github.com/howardsinc/FortiManager-AI-SDK.git
```
The two repos **must** sit next to each other like this — the app finds the second one
automatically:
```
C:\Projects\
├── FortiSASE-SDK\            ← the app
└── FortiManager-AI-SDK\      ← the FortiManager tools the app uses
```

**Check** — paste:
```powershell
dir C:\Projects
```
You should see both folders listed.

---

## Step 4 — Install the Python packages the app needs

Paste these three lines one at a time (the last one downloads for a minute or two):
```powershell
cd C:\Projects\FortiSASE-SDK\automation\sdwan-ztp\config-generator
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

**Check** — paste:
```powershell
python -m streamlit --version
```
You should see `Streamlit, version 1.xx.x`.

---

## Step 5 — Start the app (offline config builder works right away)

Paste these two lines:
```powershell
cd C:\Projects\FortiSASE-SDK\automation\sdwan-ztp\config-generator
python -m streamlit run app.py
```
- The **first time only**, Streamlit asks for an email address in the PowerShell window —
  just press **Enter** to skip.
- If Windows Firewall pops up, click **Allow access**.
- Your browser opens by itself. If it doesn't, open a browser and go to **http://localhost:8501**

**Check** — you see the **Config Generator** page with a form. You can already build and
download `.conf` files — no FortiManager needed for that.

To stop the app: click the PowerShell window and press **Ctrl + C**. Leave it running while you
use the app.

---

## Step 6 — Connect to FortiManager (for the MSSP Deploy page)

The app never stores a password — it reads a FortiManager **API token** from a small file on
your laptop. You create that token on **your own FortiManager** once.

**Before you start, check your FortiManager:**
- Version **7.6 or newer** (the app uses Jinja CLI templates, which older versions don't have).
- **ADOMs enabled**: FortiManager GUI → *System Settings → Dashboard → System Information →
  Administrative Domain* must say **Enabled**. (If it says Disabled, click it and enable; the
  app creates one ADOM per customer.)
- Leave **Workspace Mode** at its default (*Disabled*).

**6a. Find your laptop's public IP** — paste:
```powershell
(Invoke-WebRequest -UseBasicParsing https://api.ipify.org).Content
```
(If that fails, open <https://whatismyip.com> in a browser.) Write it down. FortiManager only
accepts API calls from IPs you list, so if you later move to a different network — home,
hotel, phone hotspot — you'll have to add the new IP in step 6b.

**6b. Create the API user on your FortiManager** (GUI):
1. *System Settings → Administrators → Create New → REST API Admin*
2. **User Name:** `FMG_REST_API`
3. **Admin Profile:** `Super_User`
4. **Administrative Domain:** All ADOMs
5. **JSON API Access:** Read-Write
6. **Trusted Hosts:** your public IP from 6a, as `<your IP>/32` (you can add more later)
7. Click **OK**. FortiManager shows the **API token once** — copy it somewhere safe right now.
   If you lose it, come back here and click **Regenerate** to get a new one.

**6c. Put the token in the credentials file.** Paste these two lines (the second one opens Notepad):
```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.config\mcp"
notepad "$env:USERPROFILE\.config\mcp\fortimanager_credentials.yaml"
```
Notepad asks whether to create the file — click **Yes**. Paste this in, replace the two
`<...>` values with your FortiManager's address and the token from 6b, keep everything else
exactly as is (the spaces matter), then **File → Save** and close Notepad:
```yaml
devices:
  lab-fmg:
    host: <your FortiManager IP or hostname>
    port: 443
    auth_method: token
    api_token: <paste the token from 6b here>
    username: FMG_REST_API
    verify_ssl: false
```

**Check** — start the app (Step 5), open the **MSSP Deploy** page in the left sidebar, pick
`lab-fmg` in the host dropdown and click **🔌 Test Connection**. You should get a green gate
and a list of ADOMs. If it's red, see Troubleshooting below.

Your first deployment then follows the page top to bottom: **② Create new ADOM** (dry-run, then
create — it bootstraps the templates and variables), **③** upload the CSV from the Config
Generator (dry-run, then import), **④** preview, then install. The FortiGates must be able to
reach your FortiManager on TCP 541 (FGFM) to dial home and pick up their config.

---

## Step 7 (optional) — FortiSASE Tenant Status page

This read-only dashboard needs an API user for **your FortiSASE tenant**. Whoever administers
the tenant creates it in FortiCloud IAM (*support.fortinet.com → Services → IAM → API Users →
Add New*, with FortiSASE access) and gives you its **API ID** and **password**. Then paste:
```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.config\fortisase"
notepad "$env:USERPROFILE\.config\fortisase\fortisase_credentials.yaml"
```
Paste, fill in, save:
```yaml
api_id: <API ID of the FortiSASE API user>
client_id: FortiSASE
password: <its password>
```
Then in the app open **FortiSASE Tenant Status** → **🔑 Use saved lab creds**.

---

## Every day after that

Start the app (two lines):
```powershell
cd C:\Projects\FortiSASE-SDK\automation\sdwan-ztp\config-generator
python -m streamlit run app.py
```
Stop it: **Ctrl + C** in the PowerShell window.

Get the latest version (run every week, or whenever you're told there's an update):
```powershell
cd C:\Projects\FortiSASE-SDK
git pull
cd C:\Projects\FortiManager-AI-SDK
git pull
```
Then restart the app.

---

## Troubleshooting

| You see | What to do |
|---|---|
| `python is not recognized` | Re-run the Python installer; tick **Add python.exe to PATH**; open a **new** PowerShell. |
| `git is not recognized` | Re-run the Git installer; open a **new** PowerShell. |
| `streamlit is not recognized` | Always start it as `python -m streamlit run app.py` (as written above). |
| `pip install` fails with an SSL or proxy error | You are probably on a corporate network or VPN. Try again off VPN, or ask IT for the proxy address and run `python -m pip install -r requirements.txt --proxy http://<proxy>:<port>`. |
| Browser shows "This site can't be reached" | The app isn't running. Check the PowerShell window for a red error and start it again (Step 5). |
| `Port 8501 is already in use` | Another copy is running. Close the other PowerShell window, or start with `python -m streamlit run app.py --server.port 8502` and open http://localhost:8502. |
| MSSP Deploy says **FortiManager-AI-SDK not found** | The second repo isn't next to the first. Re-check Step 3 — both folders must be directly under `C:\Projects`. |
| **Create new ADOM** fails immediately | ADOMs aren't enabled on your FortiManager, or the API user isn't `Super_User` / *All ADOMs*. Re-check the "Before you start" list in Step 6. |
| **Test Connection** is red | Either the token in the file is wrong or expired, or your current public IP isn't in the API user's Trusted Hosts. Re-check 6a (your IP may have changed) and add it under *System Settings → Administrators → FMG_REST_API → Trusted Hosts*. |
| The host dropdown on MSSP Deploy is empty | The credentials file is in the wrong place or has a typo. Re-open it with the `notepad` line in Step 6 and compare with the template — indentation must be exactly two spaces. |
| The app looks different after `git pull` | Expected — restart the app to load the update. |

---

Need the longer technical version (Linux/macOS, how the pieces fit)? See [`SETUP.md`](SETUP.md).
Need the "how do I build a config" walkthrough? See [`PARTNER-GUIDE.md`](PARTNER-GUIDE.md).
