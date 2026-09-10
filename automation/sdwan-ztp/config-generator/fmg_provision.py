"""
FortiManager provisioning adapter for the "MSSP Deploy" page.

A THIN subprocess wrapper over the FortiManager-AI-SDK CLI tools. The SDK is kept as a
separate, partner-distributable checkout (NOT vendored here) — point the app at it with the
`FMG_SDK_DIR` env var. Each helper shells out to one tool and returns its parsed JSON.

AUTH is handled entirely by the SDK tools: they read a Bearer token from
`~/.config/mcp/fortimanager_credentials.yaml`. This app NEVER handles credentials — it only
passes a host and reads back JSON. Keeps the generator/UI pure and the FMG logic single-sourced.

Tool CLIs are not uniform (adom-list takes a positional host; adom-init uses --flags; the
push/import tools are params-dict style), so `_run` centralises the subprocess+JSON handling and
each public helper knows its own tool's arg shape.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

def _resolve_sdk_dir():
    """Find the FortiManager-AI-SDK checkout so this works for a partner's one-clone too, not just
    a hardcoded local path. Priority: FMG_SDK_DIR env var -> submodule/vendored inside this repo ->
    sibling-repo layout. Returns the first that actually contains the tools; else the recommended
    submodule location (for a helpful 'not found' message)."""
    env = os.environ.get("FMG_SDK_DIR")
    if env:
        return pathlib.Path(env)
    # config-generator -> sdwan-ztp -> automation -> <FortiSASE-SDK repo root>
    repo_root = pathlib.Path(__file__).resolve().parents[3]
    candidates = [
        repo_root / "FortiManager-AI-SDK",                           # git submodule / vendored at repo root
        repo_root / "vendor" / "FortiManager-AI-SDK",                # vendored under vendor/
        repo_root.parent / "FortiManager-AI-SDK",                    # side-by-side clone (same parent folder)
        repo_root.parent / "MSSP-SE-Tools" / "FortiManager-AI-SDK",  # dev layout (Daniel's checkout)
    ]
    for c in candidates:
        if (c / "tools" / "org.ulysses.noc.fortimanager-adom-list").exists():
            return c
    return candidates[0]   # recommended (submodule) location, named in the 'not found' error


# Where the FortiManager-AI-SDK lives. Auto-resolved; override with the FMG_SDK_DIR env var.
FMG_SDK_DIR = _resolve_sdk_dir()
CREDS_YAML = pathlib.Path.home() / ".config" / "mcp" / "fortimanager_credentials.yaml"


class ToolError(Exception):
    """An SDK tool was missing, timed out, errored, or returned non-JSON."""


def _tool_path(name):
    d = FMG_SDK_DIR / "tools" / f"org.ulysses.noc.fortimanager-{name}"
    return d / f"org.ulysses.noc.fortimanager-{name}.py"


def sdk_available():
    """Is the SDK checkout reachable? (adom-list is the canary.)"""
    return _tool_path("adom-list").exists()


def _run(name, args, timeout=120):
    """Run one SDK tool as a subprocess -> parsed JSON dict. Raises ToolError on any failure.

    Uses sys.executable so it runs in the same interpreter as Streamlit (that interpreter needs
    the tools' deps: pyyaml, requests)."""
    tp = _tool_path(name)
    if not tp.exists():
        raise ToolError(f"SDK tool not found:\n{tp}\n\nSet the FMG_SDK_DIR env var to your "
                        f"FortiManager-AI-SDK checkout and restart the app.")
    try:
        r = subprocess.run([sys.executable, str(tp), *[str(a) for a in args]],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ToolError(f"'{name}' timed out after {timeout}s — FortiManager unreachable?")
    out = (r.stdout or "").strip()
    if not out:
        raise ToolError(f"'{name}' produced no output (exit {r.returncode}).\n\n"
                        f"{(r.stderr or '').strip()[:800] or '(no stderr)'}")
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        # tool printed something non-JSON (traceback / import error / banner)
        raise ToolError(f"'{name}' returned non-JSON output:\n\n{out[:800]}"
                        + (f"\n\nstderr:\n{r.stderr.strip()[:400]}" if r.stderr.strip() else ""))


# ---- READ-ONLY -------------------------------------------------------------
def adom_list(host):
    """READ-ONLY. Health-check + ADOM inventory in one call.
    -> {success, count, adoms:[{name, os_ver, mr, state, device_count}]}."""
    return _run("adom-list", [host], timeout=60)


def known_hosts():
    """Best-effort: [(friendly_name, host)] from the creds yaml, for the host dropdown.
    The yaml is `<section>: {<name>: {host: ...}}` (e.g. devices: aws-lab-fmg: host: ...).
    Never returns the token; returns [] on any parse problem (page falls back to free entry)."""
    try:
        import yaml
        data = yaml.safe_load(CREDS_YAML.read_text()) or {}
    except Exception:
        return []
    out, seen = [], set()
    for section in (data.values() if isinstance(data, dict) else []):
        if not isinstance(section, dict):
            continue
        for name, cfg in section.items():
            if isinstance(cfg, dict) and cfg.get("host") and cfg["host"] not in seen:
                seen.add(cfg["host"])
                out.append((str(name), str(cfg["host"])))
    return out


def add_credential(name, host, port=443, api_token="", username="FMG_REST_API", verify_ssl=False):
    """WRITE-ONCE onboarding: append a new FMG entry to the creds yaml. The app never reads
    tokens back — this is the single place it ever touches one, and only to write it.

    Returns ("host_exists", <existing_name>) if the host already has an entry (nothing written),
    or ("added", <snapshot_bytes_or_None>) — pass the snapshot to restore_creds_snapshot() to
    roll back if the follow-up connection test fails. Existing file bytes are NEVER re-encoded
    (append-only, ASCII entry), so comments and formatting survive untouched. Raises ValueError
    on bad input, a duplicate name, or an unappendable layout; errors never include the token."""
    import re
    import yaml
    name, host = str(name).strip(), str(host).strip()
    username = str(username).strip() or "FMG_REST_API"
    api_token = str(api_token).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name):
        raise ValueError("friendly name: 1-64 chars, letters/digits/dot/dash/underscore, "
                         "starting with a letter or digit (e.g. customer2-fmg).")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,253}", host):
        raise ValueError("host must be an IP or FQDN (letters/digits/dot/dash/colon).")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,63}", username):
        raise ValueError("username: letters/digits/dot/dash/underscore/@ only.")
    if not api_token or not api_token.isascii() or any(c.isspace() for c in api_token):
        raise ValueError("API key is required (ASCII, no spaces) — paste it exactly as "
                         "`execute api-user generate-key` printed it.")
    port = int(port)
    if not 1 <= port <= 65535:
        raise ValueError("port must be 1-65535.")

    prev = CREDS_YAML.read_bytes() if CREDS_YAML.exists() else None
    if prev is None:
        prev_text, data = None, {}
    else:
        try:
            prev_text = prev.decode("utf-8")
        except UnicodeDecodeError:
            prev_text = prev.decode("cp1252", errors="replace")
        try:
            data = yaml.safe_load(prev_text) or {}
        except Exception:
            raise ValueError(f"{CREDS_YAML} exists but is not valid YAML — fix it by hand "
                             "first (nothing was written).")
    sections = list(data.items()) if isinstance(data, dict) else []
    for _sec, entries in sections:                       # host wins over name: "already onboarded"
        if isinstance(entries, dict):
            for n, cfg in entries.items():
                if isinstance(cfg, dict) and str(cfg.get("host", "")) == host:
                    return ("host_exists", str(n))
    for _sec, entries in sections:
        if isinstance(entries, dict) and name in entries:
            raise ValueError(f"an entry named '{name}' already exists (different host) — "
                             "pick another friendly name.")

    tok = api_token.replace("\\", "\\\\").replace('"', '\\"')
    entry = (f"  {name}:\n"
             f"    host: {host}\n"
             f"    port: {port}\n"
             f"    auth_method: token\n"
             f'    api_token: "{tok}"\n'
             f"    username: {username}\n"
             f"    verify_ssl: {'true' if verify_ssl else 'false'}\n"
             f'    notes: "added via MSSP Deploy onboarding form"\n')
    if prev is None:
        head = ("# LOCAL ONLY - never commit. Created by the MSSP Deploy onboarding form;\n"
                "# the FortiManager-AI-SDK reads it, the app never reads tokens back.\n"
                "devices:\n")
        cand_text, cand_bytes = head + entry, (head + entry).encode("ascii")
    else:
        nl = "" if prev_text.endswith("\n") else "\n"
        # Append the bare entry (attaches to the trailing top-level map) unless the file has no
        # devices: key at all; the parse-verify below catches any layout this can't extend.
        add = entry if isinstance(data, dict) and isinstance(data.get("devices"), dict) \
            else "devices:\n" + entry
        cand_text = prev_text + nl + add
        cand_bytes = prev + nl.encode("ascii") + add.encode("ascii")
    try:                                                 # verify BEFORE writing: parse + nothing lost
        nd = yaml.safe_load(cand_text) or {}
        dev = nd["devices"][name]
        ok = str(dev.get("host")) == host and dev.get("api_token") == api_token
        ok = ok and all(nd.get(k) == v for k, v in sections if k != "devices")
        old_devs = data.get("devices") or {} if isinstance(data, dict) else {}
        ok = ok and all(nd["devices"].get(n) == c for n, c in old_devs.items())
    except Exception:
        ok = False
    if not ok:
        raise ValueError("couldn't safely append to the creds yaml (unusual layout) — nothing "
                         "was written; add the entry by hand instead.")
    CREDS_YAML.parent.mkdir(parents=True, exist_ok=True)
    CREDS_YAML.write_bytes(cand_bytes)
    return ("added", prev)


def restore_creds_snapshot(snapshot):
    """Roll back an add_credential() write: snapshot=None means the file didn't exist before."""
    if snapshot is None:
        CREDS_YAML.unlink(missing_ok=True)
    else:
        CREDS_YAML.write_bytes(snapshot)


def list_adom_devices(host, adom):
    """READ-ONLY. Live device inventory for an ADOM (fresher than adom-list's device_count).
    There is no SDK tool for this yet, so we call the FMG client directly (read-only) — the tool
    self-installs its own sdk path, and the client reads the same creds yaml.
    -> [{name, sn, platform, conn, db, conf}] (conn/db/conf are FMG status ints)."""
    import importlib
    sdk_dir = FMG_SDK_DIR / "sdk"
    if str(sdk_dir) not in sys.path:
        sys.path.insert(0, str(sdk_dir))
    try:
        fmc = importlib.import_module("fortimanager_client")
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"Couldn't load the FMG client from {sdk_dir}:\n{e}")
    try:
        c = fmc.FortiManagerClient(host=str(host))
        r = c.get(f"/dvmdb/adom/{adom}/device",
                  fields=["name", "sn", "platform_str", "conn_status", "db_status", "conf_status"])
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"device list for {adom}: {e}")
    st = (r.get("result", [{}])[0] or {})
    code = (st.get("status") or {}).get("code")
    if code not in (0, None):
        raise ToolError(f"device list for {adom}: {(st.get('status') or {}).get('message')}")
    return [{"name": d.get("name"), "sn": d.get("sn"), "platform": d.get("platform_str"),
             "conn": d.get("conn_status"), "db": d.get("db_status"), "conf": d.get("conf_status")}
            for d in (st.get("data") or [])]


def list_all_devices(host):
    """READ-ONLY. Every device managed by this FMG across ALL ADOMs (the root DVM table).
    A serial is registered ONCE in the device manager, so this is how we tell whether a CSV serial
    already lives somewhere before trying to import it into an ADOM. -> [{name, sn, platform}]."""
    import importlib
    sdk_dir = FMG_SDK_DIR / "sdk"
    if str(sdk_dir) not in sys.path:
        sys.path.insert(0, str(sdk_dir))
    try:
        fmc = importlib.import_module("fortimanager_client")
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"Couldn't load the FMG client from {sdk_dir}:\n{e}")
    try:
        c = fmc.FortiManagerClient(host=str(host))
        r = c.get("/dvmdb/device", fields=["name", "sn", "platform_str"])
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"global device list: {e}")
    st = (r.get("result", [{}])[0] or {})
    code = (st.get("status") or {}).get("code")
    if code not in (0, None):
        raise ToolError(f"global device list: {(st.get('status') or {}).get('message')}")
    return [{"name": d.get("name"), "sn": d.get("sn"), "platform": d.get("platform_str")}
            for d in (st.get("data") or [])]


def locate_serials(host, serials, target_adom):
    """ADOM-aware import pre-flight (the 'wrong-ADOM' guard). FortiManager registers a serial ONCE
    across the device manager, so importing a serial into any ADOM other than its home ADOM fails.
    Classify where each serial already lives:
      new        — not managed by this FMG → safe to import into target_adom
      in_target  — already in the selected ADOM → re-import is idempotent (re-bind)
      elsewhere  — managed in a DIFFERENT ADOM (named) → importing here WILL fail; target that ADOM
    -> {sn: {"state": new|in_target|elsewhere, "adom": <home adom or None>}}. Read-only.

    Both source lists (target-ADOM + global DVM) MUST be trustworthy — every classification is an
    intersection of the two, so soft-failing either into an empty set produces wrong answers (F2:
    a swallowed target-ADOM ToolError misclassified in-target serials as 'elsewhere', and the
    naming scan below skips target_adom so it could not recover). We propagate the ToolError
    instead; the caller retries or renders a legitimate error."""
    serials = [str(s).strip() for s in (serials or []) if str(s).strip()]
    out = {}
    if not serials:
        return out
    # Fail-fast on either read (F2): NO try/except — a soft-fail into an empty set here would
    # corrupt every classification (the two lists are intersected below).
    target_sns = {d["sn"] for d in list_adom_devices(host, target_adom) if d.get("sn")}
    managed = {d["sn"] for d in list_all_devices(host) if d.get("sn")}
    unresolved = []
    for sn in serials:
        if sn in target_sns:
            out[sn] = {"state": "in_target", "adom": target_adom}
        elif sn in managed:
            out[sn] = {"state": "elsewhere", "adom": None}
            unresolved.append(sn)
        else:
            out[sn] = {"state": "new", "adom": None}
    # Name the home ADOM for conflicts. device_count is an unreliable meta field (see
    # list_adom_devices' docstring: "fresher than adom-list's device_count") and adom-list can
    # soft-fail — so DON'T gate the scan on it (F1). Scan every non-target ADOM, using device_count
    # only to ORDER the scan populated-first, with early-exit once all conflicts are named.
    if unresolved:
        try:
            _al = adom_list(host)
        except ToolError:
            _al = {}
        _adoms = [] if _al.get("success") is False else (_al.get("adoms") or [])
        _cands = sorted((a for a in _adoms if a.get("name") and a.get("name") != target_adom),
                        key=lambda a: (a.get("device_count") or 0), reverse=True)
        for a in _cands:
            _name = a.get("name")
            try:
                sns = {d["sn"] for d in list_adom_devices(host, _name) if d.get("sn")}
            except ToolError:
                continue
            for sn in list(unresolved):
                if sn in sns:
                    out[sn]["adom"] = _name
                    unresolved.remove(sn)
            if not unresolved:
                break
    return out


def validate_import_rows(rows):
    """Pre-flight a device CSV BEFORE import — catch blank fields that would break the install with
    a cryptic Jinja 'undefined' error (the exact class that bites hand-edited CSVs). Conditional
    rules, NOT a per-blueprint table (that would drift from the generator). Returns a list of
    (row_number, message); empty = clean. NOTE: MGMT_GATEWAY blank is VALID (static reuses the WAN
    gateway) — deliberately not flagged."""
    issues = []
    for i, r in enumerate(rows, start=1):
        bp = str(r.get("Device Blueprint", "")).upper()
        wan_static = str(r.get("WAN_MODE", "")).strip().lower() == "static"

        def blank(k):
            return not str(r.get(k, "")).strip()

        for k in ("Serial Number", "Name", "LAN_IP", "LAN_MASK", "POP1_FQDN", "POP2_FQDN"):
            if blank(k):
                issues.append((i, f"`{k}` is blank (required)."))
        if wan_static:
            for k in ("WAN_IP", "WAN_MASK", "WAN_GATEWAY"):
                if blank(k):
                    issues.append((i, f"`{k}` is blank but WAN_MODE=static → install will fail."))
        if "DUAL" in bp:
            if blank("WAN2_PORT"):
                issues.append((i, "`WAN2_PORT` is blank but this is a DUAL blueprint."))
            if wan_static:
                for k in ("WAN2_IP", "WAN2_MASK", "WAN2_GATEWAY"):
                    if blank(k):
                        issues.append((i, f"`{k}` is blank but DUAL + WAN_MODE=static."))
        if "SPA" in bp and blank("FABRIC_OVERLAY"):
            issues.append((i, "`FABRIC_OVERLAY` is blank but this is an SPA-hub blueprint."))
        for k in ("POP1_FQDN", "POP2_FQDN"):
            v = str(r.get(k, "")).strip()
            # Any angle bracket = an unfilled placeholder (<tenant>, <your-pop>, ...), NOT a real
            # FQDN. It also trips FMG's REST XSS filter with a cryptic datasrc-invalid at install
            # time (gotcha #21) — catch it here with a clear, actionable message instead.
            if "<" in v or ">" in v or "your-tenant" in v.lower():
                issues.append((i, f"`{k}` is a placeholder, not a real FQDN (`{v}`) — enter the real "
                                  "FortiSASE BOR PoP FQDN for your tenant (from Secure Private Access / "
                                  "your BOR location in the FortiSASE portal) before importing. "
                                  "A placeholder means the config is incomplete and will not install."))
        # Failover sanity: identical PoP endpoints. Same FQDN on both on-ramps = valid config but NO
        # failover (advisory). Same PROBE = FortiOS -7 duplicate-detect-server -> the install FAILS.
        _p1 = str(r.get("POP1_FQDN", "")).strip().lower()
        _p2 = str(r.get("POP2_FQDN", "")).strip().lower()
        if _p1 and _p2 and _p1 == _p2:
            issues.append((i, "POP1_FQDN and POP2_FQDN are identical -> both on-ramps land on the SAME "
                              "PoP, so there is NO failover. If that's deliberate (lab), ignore; "
                              "otherwise set distinct Primary/Secondary PoP FQDNs."))
        _pr1 = str(r.get("POP1_PROBE", "")).strip().lower()
        _pr2 = str(r.get("POP2_PROBE", "")).strip().lower()
        if _pr1 and _pr2 and _pr1 == _pr2:
            issues.append((i, "POP1_PROBE and POP2_PROBE are identical -> FortiOS rejects a duplicate "
                              "SLA detect server (-7), so the install FAILS. Use a distinct probe per "
                              "on-ramp (or leave blank to auto-assign)."))
    return issues


# ---- MUTATING (guardrailed: every action has a dry-run / preview mode) ------
_RUNNER = pathlib.Path(__file__).parent / "_fmg_exec.py"
# Standard auto_bind spec (from the SDK docs): infer template-group + policy-pkg from the CSV's
# blueprint, bind the role's device group, and pre-create the 3 normalized zone shells (LAN=system,
# SDWAN/Underlay=sdwan — avoids the -553 sdwan-zone namespace collision on install).
_NORMALIZED_INTERFACES = [
    {"name": "LAN_ZONE", "zone_type": "system"},
    {"name": "SDWAN_ZONE", "zone_type": "sdwan"},
    {"name": "Underlay_ZONE", "zone_type": "sdwan"},
]


def device_group_for_blueprint(blueprint):
    """CSV `Device Blueprint` -> FMG device group (role-based). SPA wins over DUAL (a dual hub is
    still an SPA hub); else DUAL vs SINGLE."""
    bp = str(blueprint).upper()
    if "SPA" in bp:
        return "BOR_Branch_SPA_Hub"
    if "DUAL" in bp:
        return "BOR_Branch_Dual"
    return "BOR_Branch_Single"


def _exec_runner(name, params, timeout=600):
    """Drive a params-dict tool via _fmg_exec.py (its own CLI can't take our params). -> JSON dict."""
    tp = _tool_path(name)
    if not tp.exists():
        raise ToolError(f"SDK tool not found:\n{tp}\n\nSet FMG_SDK_DIR.")
    tf = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    try:
        json.dump(params, tf)
        tf.close()
        r = subprocess.run([sys.executable, str(_RUNNER), str(tp), tf.name],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ToolError(f"'{name}' timed out after {timeout}s.")
    finally:
        try:
            os.unlink(tf.name)
        except OSError:
            pass
    out = (r.stdout or "").strip()
    if not out:
        raise ToolError(f"'{name}' produced no output (exit {r.returncode}).\n\n"
                        f"{(r.stderr or '').strip()[:800] or '(no stderr)'}")
    for line in reversed(out.splitlines()):          # tool result is the last JSON line
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                break
    raise ToolError(f"'{name}' returned non-JSON output:\n\n{out[:800]}")


def adom_init(host, adom, tenant_config=None, create=True, dry_run=False):
    """Phase 1 — bootstrap an ADOM. adom-init prints HUMAN TEXT (not JSON), so we return the text
    + a best-effort ok flag. tenant_config is a flat dict of meta-var overrides (written to a temp
    YAML the tool reads). -> {ok, dry_run, output, returncode}."""
    tp = _tool_path("adom-init")
    if not tp.exists():
        raise ToolError(f"SDK tool not found:\n{tp}\n\nSet FMG_SDK_DIR.")
    args = ["--fmg-host", str(host), "--adom", str(adom)]
    tmp = None
    if tenant_config:
        import yaml
        tf = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
        yaml.safe_dump(dict(tenant_config), tf, sort_keys=False)
        tf.close()
        tmp = tf.name
        args += ["--tenant-config", tmp]
    if create:
        args.append("--create-adom")
    if dry_run:
        args.append("--dry-run")
    try:
        r = subprocess.run([sys.executable, str(tp), *args],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    except subprocess.TimeoutExpired:
        raise ToolError("adom-init timed out after 300s.")
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    text = (r.stdout or "").rstrip()
    if r.stderr.strip():
        text += "\n\n[stderr]\n" + r.stderr.strip()
    # adom-init's SUMMARY prints an "OK :" count and (on trouble) FAIL/ERROR lines.
    lowered = (r.stdout or "").lower()
    ok = r.returncode == 0 and ("fail  :" not in lowered) and (" error" not in lowered
                                                               or "0 error" in lowered)
    return {"ok": ok, "dry_run": dry_run, "output": text.strip(), "returncode": r.returncode}


ADOM_INIT_STAGES = [
    "1. ADOM", "2. Meta variables", "3. Normalized interfaces", "4. CLI templates",
    "5. CLI template groups", "6. Firewall addresses", "7. Traffic shapers",
    "8. Policy packages", "9. Device blueprints", "10. DVMDB device groups",
]


def adom_init_stream(host, adom, tenant_config=None, create=True, dry_run=False):
    """Generator — yields adom-init stdout LINES as they happen (run with `python -u` so the
    tool's print()s flush per-line instead of block-buffering into a 30s void). The caller
    accumulates the lines and calls parse_adom_summary(full_text) at the end. Raises ToolError
    only if the tool can't launch."""
    tp = _tool_path("adom-init")
    if not tp.exists():
        raise ToolError(f"SDK tool not found:\n{tp}\n\nSet FMG_SDK_DIR.")
    args = ["--fmg-host", str(host), "--adom", str(adom)]
    tmp = None
    if tenant_config:
        import yaml
        tf = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
        yaml.safe_dump(dict(tenant_config), tf, sort_keys=False)
        tf.close()
        tmp = tf.name
        args += ["--tenant-config", tmp]
    if create:
        args.append("--create-adom")
    if dry_run:
        args.append("--dry-run")
    try:
        proc = subprocess.Popen([sys.executable, "-u", str(tp), *args],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", bufsize=1)
    except OSError as e:
        raise ToolError(f"Couldn't launch adom-init: {e}")
    try:
        for line in proc.stdout:
            yield line.rstrip("\n")
        proc.wait()
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def parse_adom_summary(text):
    """Pull OK / Failed counts + readiness out of adom-init's SUMMARY block."""
    import re
    ok = failed = None
    for line in text.splitlines():
        m = re.match(r"\s*OK\s*:\s*(\d+)", line)
        if m:
            ok = int(m.group(1))
            continue
        m = re.match(r"\s*Failed\s*:\s*(\d+)", line)
        if m:
            failed = int(m.group(1))
    ready = "READY for CSV imports" in text
    success = (failed == 0) if failed is not None else ready
    return {"ok": ok, "failed": failed, "ready": ready, "success": bool(success)}


def import_csv(host, adom, csv_path, device_group, dry_run=False):
    """Phase 2 — create model devices from a CSV row-set + auto-bind (blueprint/group/zones).
    dry_run=True previews the exact FMG payload without writing. -> tool JSON dict."""
    params = {
        "fmg_host": str(host), "adom": str(adom), "csv_path": str(csv_path),
        "dry_run": bool(dry_run),
        "auto_bind": {
            "resolve_from_blueprint": True,
            "device_group": str(device_group),
            "normalized_interfaces": _NORMALIZED_INTERFACES,
        },
    }
    return _exec_runner("model-device-import-csv", params, timeout=600)


def _is_benign(node):
    """A bind sub-result that's fine on a re-import: OK (0/None) or already-exists (-2)."""
    if not isinstance(node, dict):
        return True
    c = node.get("code")
    return c in (0, None) or c == -2 or ("already exist" in str(node.get("msg", "")).lower())


def import_verdict(res):
    """Classify a real (non-dry-run) import result -> 'ok' | 'idempotent' | 'failed'.

    The tool sets success=false whenever ANY sub-op returns non-zero — including a re-import of an
    existing device, where the device is present + bound but the ADD ops return -2 "already exists".
    That's not a failure. 'idempotent' = device(s) created, NO failed devices, and EVERY auto_bind
    op is benign (0 or -2). Note: -2 can come from template-group / policy-package / device-group /
    zones — NOT just zones — so we check them all (was too strict before)."""
    if res.get("success"):
        return "ok"
    if res.get("devices_failed"):
        return "failed"
    if not (res.get("devices_created") or []):
        return "failed"
    ab = res.get("auto_bind") or {}
    checks = [ab.get("template_group"), ab.get("policy_package"), ab.get("device_group")]
    checks += [r for i in (ab.get("normalized_interfaces") or []) for r in (i.get("results") or [])]
    return "idempotent" if all(_is_benign(x) for x in checks if x is not None) else "failed"


def install_push(host, adom, device, preview_only=True):
    """Phase 3 — install (or install-preview) a device's config. install-push has a clean --flags
    CLI that emits JSON. preview_only=True validates without pushing to the box. -> JSON dict."""
    args = ["--fmg-host", str(host), "--adom", str(adom), "--device", str(device)]
    if preview_only:
        args.append("--preview-only")
    return _run("install-push", args, timeout=600)
