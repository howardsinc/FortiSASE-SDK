"""
MSSP Deploy — point-and-fire FortiManager provisioning for the BOR / SPA solution.

Gate the page on a LIVE FortiManager connection (green light via a READ-ONLY adom-list), then
point-and-fire: create a customer ADOM, import model devices from a generated CSV, install to
devices — every write has a dry-run / preview first.

Heavy lifting is done by the FortiManager-AI-SDK CLI tools (a separate, partner-distributable
SDK) via fmg_provision.py. Auth is handled by the SDK (Bearer token in the creds yaml); the app
never READS credentials back — the one exception is the "➕ Add a FortiManager" onboarding form,
which write-once APPENDS a new entry to the creds yaml (rolled back if its connection test fails).
"""
import csv
import io
import pathlib
import sys
import tempfile
import time

import streamlit as st

# fmg_provision.py sits next to app.py, one dir up from pages/.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import fmg_provision as fmg  # noqa: E402

st.set_page_config(page_title="MSSP Deploy", page_icon="🚀", layout="wide")

st.title("🚀 MSSP Deploy — FortiManager Provisioning")
st.caption("Connect to a live FortiManager, then point-and-fire new customer ADOMs and offline "
           "model devices from your generated CSVs — create, import, and install, each with a "
           "dry-run / preview first.")

# ---- SDK availability -------------------------------------------------------
if not fmg.sdk_available():
    st.error(
        "**FortiManager-AI-SDK not found** — the provisioning tools live in a separate SDK repo "
        "that this page shells out to.\n\n"
        f"Looked in: `{fmg.FMG_SDK_DIR}`\n\n"
        "**Fix (either one):**\n"
        "1. **Submodule (one clone):** at the repo root run\n"
        "   `git submodule add https://github.com/howardsinc/FortiManager-AI-SDK.git "
        "FortiManager-AI-SDK` then `git submodule update --init`.\n"
        "2. **Env var:** point `FMG_SDK_DIR` at an existing SDK checkout and restart.\n\n"
        "*(The **Config Generator** page works without this — only this provisioning page needs the "
        "SDK.)*")
    st.stop()

# ---- stale-connection guard (#5): re-verify a persisted connection older than 5 min ----
if st.session_state.get("fmg_host") and time.time() - st.session_state.get("fmg_last_ping", 0) > 300:
    try:
        st.session_state["fmg_adoms"] = fmg.adom_list(st.session_state["fmg_host"]).get("adoms", [])
        st.session_state["fmg_last_ping"] = time.time()
    except fmg.ToolError:
        st.session_state.pop("fmg_host", None)
        st.warning("🔴 Saved FMG connection lost (token revoked or host unreachable) — reconnect below.")

with st.expander("🔧 Connection backend (for the curious)", expanded=False):
    st.markdown(f"- **SDK tools:** `{fmg.FMG_SDK_DIR}`\n"
                f"- **Credentials:** the SDK reads a Bearer token from `{fmg.CREDS_YAML}` — the "
                "app writes a new entry ONCE at onboarding (➕ below) and never reads tokens back.\n"
                "- **How:** each action shells out to a `fortimanager-*` tool and reads back JSON.")

# ---- ① Connection gate ------------------------------------------------------
st.subheader("① Connect to FortiManager")
_hosts = fmg.known_hosts()
c1, c2 = st.columns([3, 1])
if _hosts:
    _labels = [f"{n}  ·  {h}" for n, h in _hosts] + ["Other (type an IP / FQDN)…"]
    _pick = c1.selectbox("FortiManager (from saved credentials)", _labels)
    host = (c1.text_input("FortiManager IP / FQDN", placeholder="184.73.7.106")
            if _pick.startswith("Other") else _hosts[_labels.index(_pick)][1])
else:
    host = c1.text_input("FortiManager IP / FQDN", value="184.73.7.106",
                         help="The SDK reads the matching token from the creds yaml — "
                              "this app never handles credentials.")
if c2.button("🔌 Test Connection", use_container_width=True, type="primary") and host:
    with st.status(f"Connecting to {host} …", expanded=True) as s:
        try:
            res = fmg.adom_list(host)
            if res.get("success"):
                st.session_state["fmg_host"] = host
                st.session_state["fmg_adoms"] = res.get("adoms", [])
                st.session_state["fmg_last_ping"] = time.time()
                s.update(label=f"🟢 Connected to {host} — {res.get('count', 0)} ADOMs",
                         state="complete", expanded=False)
            else:
                st.session_state.pop("fmg_host", None)
                s.update(label="🔴 Reached the tool but adom-list returned success=false",
                         state="error")
                st.json(res)
        except fmg.ToolError as e:
            st.session_state.pop("fmg_host", None)
            s.update(label="🔴 Connection failed", state="error")
            st.error(str(e))

# ---- ➕ one-time onboarding of a NEW FortiManager ----------------------------
with st.expander("➕ Add a FortiManager (one-time onboarding)", expanded=not _hosts):
    st.markdown("**Step 1 — on the new FortiManager CLI**, create an API user and generate its "
                "key (FMG prints it **once** — copy it then):")
    st.code("config system admin user\n"
            "    edit FMG_REST_API\n"
            "        set user_type api\n"
            "        set profileid Super_User\n"
            "        set rpc-permit read-write\n"
            "    next\n"
            "end\n"
            "execute api-user generate-key FMG_REST_API", language="text")
    st.markdown(f"**Step 2 — save it here.** Write-once append to `{fmg.CREDS_YAML}` — the app "
                "never reads it back, and if the connection test fails the entry is rolled back "
                "so nothing broken is ever saved.")
    with st.form("add_fmg", clear_on_submit=True):
        f1, f2, f3 = st.columns([2, 2, 1])
        _name = f1.text_input("Friendly name", placeholder="customer2-fmg")
        _newhost = f2.text_input("IP / FQDN", placeholder="203.0.113.50")
        _port = f3.number_input("Port", min_value=1, max_value=65535, value=443)
        _tok = st.text_input("API key (from step 1)", type="password")
        f4, f5 = st.columns([2, 2])
        _user = f4.text_input("API username", value="FMG_REST_API")
        _vssl = f5.checkbox("Verify SSL certificate", value=False,
                            help="Leave off for lab FMGs with self-signed certs.")
        _go = st.form_submit_button("💾 Save & Test Connection", type="primary")
    if _go:
        try:
            _status, _extra = fmg.add_credential(_name, _newhost, _port, _tok, _user, _vssl)
        except ValueError as e:
            st.error(f"Not saved: {e}")
        else:
            if _status == "host_exists":
                st.info(f"ℹ️ `{_newhost}` is already onboarded (saved as **{_extra}**) — nothing "
                        "written. Pick it in the dropdown above and hit Test Connection.")
            else:
                with st.status(f"Saved — testing {_newhost} …", expanded=True) as s:
                    try:
                        res = fmg.adom_list(_newhost)
                    except fmg.ToolError as e:
                        res = {"success": False, "error": str(e)}
                    if res.get("success"):
                        st.session_state["fmg_host"] = _newhost
                        st.session_state["fmg_adoms"] = res.get("adoms", [])
                        st.session_state["fmg_last_ping"] = time.time()
                        s.update(label=f"🟢 Onboarded + connected to {_newhost} — "
                                       f"{res.get('count', 0)} ADOMs",
                                 state="complete", expanded=False)
                        st.rerun()   # refresh the saved-credentials dropdown with the new entry
                    else:
                        fmg.restore_creds_snapshot(_extra)
                        s.update(label="🔴 Connection test failed — entry ROLLED BACK, "
                                       "nothing saved", state="error")
                        st.error(res.get("error") or "adom-list returned success=false")
                        st.caption("Fix the key / reachability and submit again — the yaml is "
                                   "exactly as it was before this attempt.")

# ---- GATE: nothing below runs without a green connection --------------------
if not st.session_state.get("fmg_host"):
    st.info("🔴 **Not connected.** Enter a FortiManager and hit **Test Connection** to unlock "
            "provisioning. (This proves the SDK / credential / JSON path before any writes.)")
    st.stop()

host = st.session_state["fmg_host"]
adoms = st.session_state.get("fmg_adoms", [])
st.success(f"🟢 **Connected to {host}** — {len(adoms)} ADOMs")

# ---- ② Target ADOM ---------------------------------------------------------
_h = st.columns([4, 1])
_h[0].subheader("② Target ADOM")
if _h[1].button("🔄 Refresh", use_container_width=True,
                help="Re-fetch the ADOM list from FMG (after creating a new customer ADOM)."):
    try:
        st.session_state["fmg_adoms"] = fmg.adom_list(host).get("adoms", [])
        st.rerun()
    except fmg.ToolError as e:
        st.error(str(e))
adoms = st.session_state.get("fmg_adoms", [])
_bor = [a for a in adoms if str(a.get("name", "")).startswith("BOR_")]   # BOR customers first
_other = [a for a in adoms if not str(a.get("name", "")).startswith("BOR_")]
_ordered = _bor + _other
_names = [a["name"] for a in _ordered] + ["➕ Create new customer ADOM…"]
sel = st.selectbox("ADOM", _names,
                   help="Pick an existing customer ADOM to provision into, or create a new one.")
if sel.startswith("➕"):
    # ---- Create ADOM (fortimanager-adom-init) -------------------------------
    st.markdown("**Create a new customer ADOM** — bootstraps ~300 FMG objects (meta vars, CLI "
                "templates, blueprints, policy packages, device groups) in one shot.")
    with st.form("create_adom"):
        new_adom = st.text_input("New ADOM name", placeholder="BOR_Customer_Acme",
                                 help="FMG ADOM name — convention `BOR_Customer_<Name>`, no spaces. "
                                      "⚠️ **Case-sensitive** in FMG: `BOR_Customer_X` ≠ `BOR_customer_x`.")
        st.caption("**Tenant defaults** — these become the ADOM meta-var defaults; per-site values "
                   "come from the device CSV later. Only these 4 usually change per tenant.")
        fc = st.columns(2)
        adm_pw = fc[0].text_input("ADMIN_PASSWORD", value="FortiSASE-OnRamp-2026!", type="password")
        seed = fc[1].text_input("SEED_PSK", value="FortiSASE-OnRamp-2026!", type="password")
        pop1 = fc[0].text_input("POP1_FQDN (Primary)",
                                placeholder="ipsec-<tenant>-dfw-f3.prod.fortisase.com")
        pop2 = fc[1].text_input("POP2_FQDN (Secondary)",
                                placeholder="ipsec-<tenant>-mia-f3.prod.fortisase.com")
        bc = st.columns(2)
        do_preview = bc[0].form_submit_button("🔍 Dry-run preview", use_container_width=True)
        do_create = bc[1].form_submit_button("🚀 Create ADOM", type="primary",
                                             use_container_width=True)
    if do_preview or do_create:
        if not new_adom.strip():
            st.error("Enter an ADOM name first.")
        else:
            _adom = new_adom.strip()
            tenant = {"ADMIN_PASSWORD": adm_pw, "SEED_PSK": seed}
            if pop1.strip():
                tenant["POP1_FQDN"] = pop1.strip()
            if pop2.strip():
                tenant["POP2_FQDN"] = pop2.strip()
            _verb = "Previewing" if do_preview else "Building"
            summ = None
            with st.status(f"⚙️  {_verb} {_adom} …", expanded=True) as s:
                log_box = st.empty()          # live scrolling tail — the "it's working" feel
                lines = []
                try:
                    for line in fmg.adom_init_stream(host, _adom, tenant_config=tenant,
                                                     create=True, dry_run=do_preview):
                        lines.append(line)
                        for stg in fmg.ADOM_INIT_STAGES:      # bump the header on each new stage
                            if line.strip().startswith(stg):
                                s.update(label=f"⚙️  {line.strip()}")
                                break
                        log_box.code("\n".join(lines[-18:]) or "…", language="text")
                    summ = fmg.parse_adom_summary("\n".join(lines))
                    if summ["success"]:
                        s.update(label=("🟢 Dry-run complete — nothing written" if do_preview
                                        else f"🎉 {_adom} provisioned — {summ.get('ok', '?')} objects"),
                                 state="complete", expanded=False)
                    else:
                        s.update(label=f"🔴 adom-init: {summ.get('failed')} failure(s)", state="error")
                except fmg.ToolError as e:
                    s.update(label="🔴 Create failed", state="error")
                    st.error(str(e))
            # ---- celebratory summary (outside the collapsed status box) ----
            if summ and summ["success"]:
                if do_preview:
                    st.success(f"**🟢 Dry-run OK** — `{_adom}` would get **~{summ.get('ok') or '300'} "
                               "objects** (meta vars · templates · blueprints · policy pkgs · device "
                               "groups). Nothing written. Hit **🚀 Create ADOM** to build it for real.")
                else:
                    st.balloons()
                    st.markdown(f"### 🎉 `{_adom}` is live!")
                    mc = st.columns(3)
                    mc[0].metric("Objects created", summ.get("ok", "—"))
                    mc[1].metric("Failures", summ.get("failed", 0))
                    mc[2].metric("Stages", "10 / 10 ✅")
                    st.markdown(
                        "**What just got built** — meta variables · normalized interfaces · CLI "
                        "templates + groups · firewall addresses · traffic shapers · policy packages "
                        "· device blueprints · DVMDB device groups.\n\n"
                        "**Next steps** 👉\n"
                        f"1. Hit **🔄 Refresh** (top of ②), select **`{_adom}`**, then **③ Push "
                        "devices** with your generated site CSVs — one row per branch, a whole fleet "
                        "in a single import.\n"
                        "2. Or create another customer ADOM — same flow.")
                    try:
                        st.session_state["fmg_adoms"] = fmg.adom_list(host).get("adoms", [])
                    except fmg.ToolError:
                        pass
else:
    # ---- existing ADOM: push devices ----------------------------------------
    a = next(x for x in _ordered if x["name"] == sel)
    m1, m2, m3 = st.columns(3)
    m1.metric("ADOM", a["name"])
    m2.metric("FortiOS", f"{a.get('os_ver', '?')}.{a.get('mr', '?')}")
    m3.metric("Devices", a.get("device_count", 0))

    st.subheader("③ Push offline model devices")
    st.markdown("Upload a device CSV (from the generator's **Export FortiManager CSV** button). "
                "**One row = one device** — push a single branch or a **whole fleet in one import**. "
                f"They're created as **offline model devices** in `{sel}` and auto-bound to their "
                "blueprint + policy package + role device-group.")
    up = st.file_uploader("Device CSV (.fmg.csv)", type=["csv"], key="dev_csv")
    if up is not None:
        try:
            rows = list(csv.DictReader(io.StringIO(up.getvalue().decode("utf-8-sig"))))
        except Exception as e:  # noqa: BLE001
            st.error(f"Couldn't parse the CSV: {e}")
            rows = []
        if not rows:
            st.warning("That CSV has a header but no data rows.")
        else:
            bp = rows[0].get("Device Blueprint", "")
            dg = fmg.device_group_for_blueprint(bp)
            st.caption(f"**{len(rows)} device(s)** · blueprint `{bp}` · device group **{dg}**")
            st.dataframe([{"Name": r.get("Name"), "Serial Number": r.get("Serial Number"),
                           "Device Blueprint": r.get("Device Blueprint"),
                           "WAN_MODE": r.get("WAN_MODE")} for r in rows],
                         use_container_width=True, hide_index=True)
            # ---- #2 pre-flight: catch blank fields that break the install BEFORE firing ----
            _issues = fmg.validate_import_rows(rows)
            if _issues:
                st.warning("⚠️ **Pre-flight found blank/placeholder fields that will break the "
                           "install** (the cryptic Jinja *'undefined'* error). Fix the CSV, or "
                           "dry-run first to double-check:\n\n"
                           + "\n".join(f"- **Row {n}** · {m}" for n, m in _issues[:25]))
            else:
                st.caption("✅ Pre-flight clean — required fields present for this blueprint.")
            # ---- #2b wrong-ADOM guard: FMG registers a serial ONCE across the device manager, so
            #      importing a serial whose home is a DIFFERENT ADOM hard-fails (task err -10,
            #      'devsnexist'). locate_serials is a network read — cache per (host, adom, serials)
            #      so Streamlit reruns don't re-query the FMG on every widget interaction. ----
            _sns = [str(r.get("Serial Number") or "").strip() for r in rows]
            _lockey = (host, sel, tuple(sorted(_sns)))
            if st.session_state.get("ser_loc_key") != _lockey:
                try:
                    st.session_state["ser_loc"] = fmg.locate_serials(host, _sns, sel)
                    st.session_state["ser_loc_key"] = _lockey
                except fmg.ToolError as e:
                    # Fail-open WITH a warning: blocking on an FMG blip would strand the SE, and a
                    # wrong-ADOM import still fails safely at fire time with FMG's own error.
                    st.session_state["ser_loc"] = None
                    st.session_state.pop("ser_loc_key", None)
                    st.warning("⚠️ Couldn't verify serial ownership (FMG read failed) — the "
                               "wrong-ADOM check was skipped this run. Retry, or import knowing "
                               f"a cross-ADOM serial will fail at fire time.\n\n`{e}`")
            _where = st.session_state.get("ser_loc") or {}
            _conflicts = {sn: w for sn, w in _where.items() if w.get("state") == "elsewhere"}
            _rebinds = [sn for sn, w in _where.items() if w.get("state") == "in_target"]
            if _conflicts:
                st.error("⛔ **Already registered in a different ADOM** — FortiManager registers a "
                         "serial once, so importing these here WILL fail (`devsnexist`):\n\n"
                         + "\n".join(f"- `{sn}` → home ADOM **{w.get('adom') or 'unknown'}**"
                                     for sn, w in _conflicts.items())
                         + "\n\nEither delete the device from its home ADOM first (Device Manager "
                           "→ that ADOM → delete), or switch the ADOM selector above to the home "
                           "ADOM and import there. Import is disabled until this is resolved.")
                if st.button("🔄 Re-check serial ownership", key="imp_loc_recheck"):
                    st.session_state.pop("ser_loc_key", None)
                    st.rerun()
            if _rebinds:
                st.info(f"{len(_rebinds)} serial(s) already in **{sel}** — re-import is an "
                        "idempotent re-bind, safe to fire.")
            tmpcsv = pathlib.Path(tempfile.gettempdir()) / f"fmgimport_{up.name}"
            tmpcsv.write_bytes(up.getvalue())

            dc = st.columns(2)
            imp_preview = dc[0].button("🔍 Dry-run preview", use_container_width=True, key="imp_pv")
            imp_fire = dc[1].button("🚀 Import to FMG", type="primary", use_container_width=True,
                                    key="imp_go", disabled=bool(_conflicts))
            if imp_preview or imp_fire:
                _lbl = "Dry-run preview" if imp_preview else f"Importing {len(rows)} device(s)"
                res = None
                with st.status(f"{_lbl} …", expanded=False) as s:
                    try:
                        res = fmg.import_csv(host, sel, tmpcsv, dg, dry_run=imp_preview)
                        s.update(label=f"{_lbl} — done", state="complete")
                    except fmg.ToolError as e:
                        s.update(label="🔴 Import failed to run", state="error")
                        st.error(str(e))
                if res is not None and imp_preview:
                    # ---- dry-run ----
                    if res.get("success"):
                        st.success(f"**🟢 Dry-run OK** — {res.get('rows_parsed', len(rows))} device(s), "
                                   "nothing written to FMG.")
                        with st.expander("FMG payload that WOULD be sent", expanded=False):
                            st.json(res.get("payload_sent", res))
                    else:
                        st.error("Dry-run couldn't build the payload:")
                        st.json(res)
                elif res is not None:
                    # ---- real import: classify ok / idempotent-reimport / failed ----
                    created = res.get("devices_created") or []
                    failed = res.get("devices_failed") or []
                    verdict = fmg.import_verdict(res)
                    names = [d.get("name") for d in created] or [r.get("Name") for r in rows]
                    if verdict in ("ok", "idempotent"):     # #8: device count changed → refresh
                        try:
                            st.session_state["fmg_adoms"] = fmg.adom_list(host).get("adoms", [])
                            st.session_state["fmg_last_ping"] = time.time()
                        except fmg.ToolError:
                            pass
                    if verdict == "ok":
                        st.session_state["last_imported"] = {"adom": sel, "devices": names}
                        st.balloons()
                        st.markdown(f"### 🎉 {len(created)} device(s) live in `{sel}`!")
                        st.markdown("Created as **offline model devices** + auto-bound. "
                                    "**Next:** section **④ Install** below.")
                    elif verdict == "idempotent":
                        st.session_state["last_imported"] = {"adom": sel, "devices": names}
                        st.warning(f"**♻️ Already imported** — {len(created)} device(s) are already in "
                                   f"`{sel}` (created + bound; zone shells already existed). Re-import "
                                   "is a safe no-op. For a clean **first**-import test, point at a "
                                   "**fresh ADOM** (a new customer) or use a serial not yet imported.")
                    else:
                        st.error(f"**🔴 Import problem** — {len(failed)} device(s) failed.")
                        if failed:
                            st.json(failed)

                    def _chip(node):  # ✅ created · ♻️ already exists · ⚠️ other
                        c = (node or {}).get("code")
                        return "✅" if c == 0 else ("♻️ exists" if c == -2 else f"⚠️ {c}")

                    ab = res.get("auto_bind") or {}
                    if ab:
                        _zones = " ".join(_chip((i.get("results") or [{}])[0])
                                          for i in ab.get("normalized_interfaces", []))
                        st.caption(f"**Auto-bind** — template group {_chip(ab.get('template_group'))} · "
                                   f"policy package {_chip(ab.get('policy_package'))} · "
                                   f"device group {_chip(ab.get('device_group'))} · zones {_zones}")
                    with st.expander("Full import result (JSON)", expanded=False):
                        st.json(res)

    # ---- ④ Install to devices — ALWAYS lists this ADOM's LIVE devices --------
    st.divider()
    _h4 = st.columns([4, 1])
    _h4[0].subheader("④ Install to devices")
    if _h4[1].button("🔄 Refresh", key="dev_refresh", use_container_width=True,
                     help="Re-fetch this ADOM's devices + their status."):
        st.rerun()
    st.caption("**Preview** validates the config build (safe, no changes). **Install** builds the "
               "device-DB config — works for **offline model devices** (holds a rev until the box "
               "dials home). Both surface FMG's real error on failure.")

    def _show_install(r, dev, action):
        """Render an install-push v1.1.0 result. Top-level `success` is the overall verdict; the
        per-step detail (device settings + each policy package) lives in device_task + pkg_tasks[],
        each with lines[] and lines[].history[] (the history surfaces the REAL root cause when a
        line just says 'Aborted due to previous error'). Returns True on success."""
        ok = bool(r.get("success"))
        tasks = ([r["device_task"]] if r.get("device_task") else []) + (r.get("pkg_tasks") or [])
        if ok:
            _tids = ", ".join(str(t.get("task_id")) for t in tasks) or "—"
            _pkgs = ", ".join(r.get("pkgs_to_install") or []) or "none"
            st.success(f"**🟢 {action} OK — `{dev}`** · {len(tasks)} FMG task(s) [{_tids}] · 0 errors "
                       f"· policy package: **{_pkgs}**")
        else:
            st.error(f"**🔴 {action} failed — `{dev}`:** {r.get('error') or 'a task ended in error'}")
        for t in tasks:                                   # device step + each policy-package step
            _terr = int(t.get("num_err") or 0)
            _icon = "✅" if (t.get("state") == "done" and _terr == 0) else "🔴"
            st.markdown(f"{_icon} **{t.get('label')}** · task {t.get('task_id')} · "
                        f"{t.get('state')} · {_terr} err")
            for ln in (t.get("lines") or []):
                st.markdown(f"- `{ln.get('name')}` → **{ln.get('state')}** — {ln.get('detail')}")
                for h in (ln.get("history") or []):        # real root cause
                    st.markdown(f"    - _{h.get('detail')}_")
        with st.expander(f"{action} result (JSON)", expanded=False):
            st.json(r)
        return bool(r.get("success"))

    try:
        _devs = fmg.list_adom_devices(host, sel)
    except fmg.ToolError as e:
        _devs = []
        st.error(f"Couldn't list devices in `{sel}`: {e}")
    if not _devs:
        st.info(f"No devices in `{sel}` yet — import one in **③** above.")
    else:
        st.caption(f"**{len(_devs)} device(s)** in `{sel}`")
        for d in _devs:
            dev = d.get("name")
            badge = "🟢 online" if d.get("conn") == 1 else "🔴 offline model · dial-home pending"
            ic = st.columns([3, 1, 1])
            ic[0].markdown(f"**`{dev}`** &nbsp; {badge}  \n"
                           f"<small>{d.get('platform') or '?'} · {d.get('sn') or 'no serial'}</small>",
                           unsafe_allow_html=True)
            if ic[1].button("🔍 Preview", key=f"pv_{dev}", use_container_width=True):
                with st.status(f"Install-preview {dev} …", expanded=False) as s:
                    r = None
                    try:
                        r = fmg.install_push(host, sel, dev, preview_only=True)
                    except fmg.ToolError as e:
                        st.error(str(e))
                    _ok = bool(r and r.get("success"))
                    s.update(label=(f"🟢 Preview OK — {dev}" if _ok else f"🔴 Preview failed — {dev}"),
                             state=("complete" if _ok else "error"))
                if r is not None:
                    _show_install(r, dev, "Preview")
            if ic[2].button("🚀 Install", key=f"go_{dev}", type="primary", use_container_width=True):
                with st.status(f"Installing {dev} …", expanded=False) as s:
                    r = None
                    try:
                        r = fmg.install_push(host, sel, dev, preview_only=False)
                    except fmg.ToolError as e:
                        st.error(str(e))
                    _ok = bool(r and r.get("success"))
                    s.update(label=(f"🎉 Installed — {dev}" if _ok else f"🔴 Install failed — {dev}"),
                             state=("complete" if _ok else "error"))
                if r is not None and _show_install(r, dev, "Install"):
                    st.balloons()

    # ---- ④½ Point serials at this FMG (FortiZTP) — optional zero-touch loop -----
    import ztp_provision as ztp
    st.divider()
    st.subheader("④½ Point serials at this FMG (FortiZTP)")
    _zt_ok, _zt_why = ztp.available()
    if not _zt_ok:
        st.info(f"**FortiZTP not configured** (optional — the CSV / offline-device flow works "
                f"without it). {_zt_why}")
    else:
        _zt_fmg = None
        _fmgs = []
        try:
            _fmgs = ztp.list_fortimanagers()
        except ztp.ZtpError as e:
            st.error(str(e))
        if _fmgs:
            # Auto-match the registered FMG to the connected FMG by serial/IP; user can override.
            _h = str(host or "").strip().lower()
            _idx = 0
            for _i, _f in enumerate(_fmgs):
                _fs = str(_f.get("serial_number") or "").lower()
                _fi = str(_f.get("ip_address") or "").lower()
                if _h and (_h == _fi or _h == _fs or _h in _fi or _fi in _h):
                    _idx = _i
                    break
            _sel = st.selectbox(
                "FortiManager to point devices at",
                options=list(range(len(_fmgs))), index=_idx, key="ztp_fmg_sel",
                format_func=lambda i: f"{_fmgs[i].get('serial_number')} · "
                                      f"{_fmgs[i].get('ip_address')}  (OID {_fmgs[i].get('oid')})",
                help="Registered FortiManagers in this FortiCloud account — auto-matched to the "
                     "connected FMG by serial/IP where possible.")
            _zt_fmg = _fmgs[_sel]
        if not _zt_fmg:
            st.warning("No FortiManager is registered in FortiZTP for this account — register this "
                       "FMG first (FortiZTP → Settings → FortiManager, same account as the "
                       "FortiGates), then 🔄 Rerun. Until then, devices can't be pointed at it via ZTP.")
        else:
            _pub_ip = _zt_fmg.get("ip_address") or host
            st.success(f"🟢 FMG in FortiZTP — OID `{_zt_fmg.get('oid')}` · serial "
                       f"`{_zt_fmg.get('serial_number')}` · `{_pub_ip}`")
            _sns = [d.get("sn") for d in _devs if d.get("sn")]
            if not _sns:
                st.info("No device serials in this ADOM to map yet — import in **③** first.")
            else:
                _xr = ztp.cross_reference(_sns)
                _mappable = []
                for _sn, _v in _xr.items():
                    _ic = {"mappable": "✅", "provisioned": "♻️",
                           "not-in-account": "⛔"}.get(_v.get("class"), "⚠️")
                    _tgt = f" → {_v.get('provision_target')}" if _v.get("provision_target") else ""
                    st.markdown(f"{_ic} `{_sn}` — **{_v.get('class')}**{_tgt}")
                    if _v.get("class") == "mappable":
                        _mappable.append(_sn)
                st.caption(f"Devices will be pointed at **{_pub_ip}** (FGFM 541) — must be publicly "
                           "reachable from the branch WAN (not a private IP behind NAT).")
                if _mappable:
                    _zc = st.columns(2)
                    _fmg_ser = _zt_fmg.get("serial_number")
                    if _zc[0].button("🔍 Dry-run map", key="ztp_dry", use_container_width=True):
                        st.json(ztp.map_serials(_mappable, _zt_fmg.get("oid"), _pub_ip,
                                                fmg_serial=_fmg_ser, dry_run=True))
                    if _zc[1].button(f"🚀 Map {len(_mappable)} → FMG", key="ztp_go",
                                     type="primary", use_container_width=True):
                        with st.status("Mapping serials in FortiZTP …", expanded=True) as _s:
                            _res = ztp.map_serials(_mappable, _zt_fmg.get("oid"), _pub_ip,
                                                   fmg_serial=_fmg_ser, dry_run=False)
                            _allok = all(x.get("success") for x in _res)
                            for x in _res:
                                st.markdown(f"{'✅' if x.get('success') else '🔴'} "
                                            f"`{x['serial_number']}` — {x.get('message') or x.get('error')}")
                            _s.update(label=("🎉 Mapped — these boxes will dial home to this FMG"
                                             if _allok else "🔴 Some maps failed"),
                                      state=("complete" if _allok else "error"))
                else:
                    st.info("No mappable serials (all already provisioned, or not in the FortiCloud account).")

st.divider()
st.caption(f"Backend: FortiManager-AI-SDK at `{fmg.FMG_SDK_DIR}` · every write has a dry-run / "
           "preview · this app never handles credentials.")
