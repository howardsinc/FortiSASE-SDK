# METHOD: how the BOR generator is built, and how to port another design onto it

**Who this is for.** An engineer or AI agent who did not build this generator and now has to
(a) reproduce the same method for a different FortiOS design, for example the SD-WAN
BGP-on-loopback lab, or (b) drive it from FortiManager or FortiSOAR. It explains the method, not
the BOR network design. For the design, read `README.md` and `BOR-TRAFFIC-MODEL-AND-POSITIONING.md`.

All paths are relative to this folder (`automation/sdwan-ztp/config-generator/` in the
FortiSASE-SDK repo) unless a different repo is named.

---

## 1. The idea in one paragraph

A working, lab-tested FortiOS config is the starting point. Every value in it is sorted into one of
five **scopes**. The scopes live in one schema file. That one file drives three outputs: the config
text, the web form an SE fills in, and the FortiManager CSV. Because all three read the same file,
they cannot drift apart. A change is made in the schema first, then the template, then proven by
re-rendering known sites and diffing the result.

```
golden config (lab-tested)
        |  extract every value, classify by scope
        v
schema/variables.yaml  ----->  generator_page.py   the SE form (Streamlit)
        |                               |
        |  build_context()              |  values typed by the SE
        v                               v
generator.py  -- render -->  templates/*.conf.j2  -->  <host>.conf
        |
        +-- fmg_csv_row() -->  <host>.fmg.csv  -->  FortiManager "Add Model Device from CSV"
```

---

## 2. The five scopes

This split is the core of the method. It is what keeps the form short and what makes the port to
FortiManager meta fields mechanical.

| Scope | Meaning | In the form | In FortiManager |
|---|---|---|---|
| `tenant` | Entered once per customer. AS number, PoPs, SLA thresholds, timers, crypto | Shown once, prefilled with a default | ADOM-level metadata default. The CSV column is left **blank** when the value equals the schema default, so the ADOM default applies |
| `site` | Entered per device. Hostname, WAN and LAN addressing, router-id | Always shown | Per-device metadata. One CSV column, always filled |
| `secret` | Never stored. Tunnel key, admin password | Typed at generate time | Injected at deploy. Not kept in the schema or in git |
| `derived` | Computed from other values. LAN network, fabric pool, object names | Never shown | Precomputed by the generator and sent as a column, or computed in the template |
| `const` | Fixed baseline. MTU, DPD, IKE version | Never shown | Literal text in the CLI template |

Rule of thumb for classifying a value from a golden config:

1. Would two sites of the same customer have different values? It is `site`.
2. Would two customers have different values, but all sites of one customer share it? It is `tenant`.
3. Can it be calculated from values already collected? It is `derived`. Do not ask for it.
4. Is it a credential? It is `secret`, whatever else is true.
5. Is it the same for everyone, always? It is `const`.

---

## 3. Anatomy of `schema/variables.yaml`

| Block | What it holds | Why it is separate |
|---|---|---|
| `meta.roles` | The roles a site can be: `bor`, `bor-spa` | A role selects a template |
| `meta.emit_order` | The FortiOS dependency order every template must follow | A tunnel must exist before anything references it. SD-WAN rules go last |
| `platforms` | Model to port-name map: `vm`, `fgt-30g`, `fgt-50g`, `fgt-120g` | A model is only a port map. All routing logic is identical across models |
| `pops`, `pops_dual` | The remote ends a site connects to, as a list of objects | Templates loop over the list, so nothing is hard-coded per PoP. List order is meaningful: it sets member order, priority and BGP preference together |
| `sla_probe_pool` | Tested probe targets, one per health check | FortiOS rejects one detect server shared across health checks |
| `features` | On/off switches for whole config blocks | A feature is a block of config, not a single value |
| `variables` | The 51 flat variables | See the attribute table below |
| `constants` | Fixed values grouped by config section | Documented in one place, not scattered in templates |
| `role_matrix` | Which behaviours each role has | Lets a template ask "does this role peer BGP with the PoP" without testing role names |

Attributes on one entry under `variables:`

| Attribute | Purpose |
|---|---|
| `key` | Name used in templates as `{{ key }}`. Upper-cased, it is the FortiManager meta field and CSV column name |
| `label`, `help`, `group` | Form label, help text, and the form section it appears in |
| `scope` | One of the five scopes above |
| `type` | `string`, `int`, `ipv4`, `enum`, `secret` and so on. Drives the widget and validation |
| `default` | Prefilled value. For tenant scope this is also the value treated as "leave the CSV column blank" |
| `required`, `required_if` | Validation |
| `show_if` | Show the field only when another value matches, for example `role == bor` |
| `roles` | Limit the field to some roles, for example `[bor-spa]` |
| `advanced` | Put the field inside an "Advanced" expander so the default form stays short |
| `secret`, `inject_at_deploy` | Mark a credential. Never give these a real default |
| `formula` | For `derived` entries: documents the computation. The real code is in `build_context()` |

---

## 4. What `generator.py` does

`build_context(site, schema)` builds the dictionary the template sees, in this order:

1. Start from every schema `default`.
2. Add every feature toggle default.
3. Overlay the site's own values. Site values win.
4. Resolve the platform port map. An explicit site choice wins over the platform default.
5. Pick the PoP list for the circuit count and give each entry a stable **role label**.
6. Compute every `derived` value.
7. Run guards that **fail loudly** with a clear message: a port used twice, too few probe targets,
   a platform with missing port names.

`render(site)` then picks the template from two axes, role and circuit count:

| | Single circuit | Dual circuit |
|---|---|---|
| `bor` (spoke) | `templates/bor.conf.j2` | `templates/bor-dual.conf.j2` |
| `bor-spa` (hub) | `templates/bor-spa.conf.j2` | `templates/bor-spa-dual.conf.j2` |

Two properties are worth copying:

- **Role-based object names.** Tunnels, health checks, route-maps and address objects are named by
  position (`BOR_Primary`, `HC_Secondary`), never by customer-specific identity such as a city.
  A NOC reads them the same way on every customer. Identity appears only in comments.
- **Everything positional follows list order.** Member numbers, priority order, the `config sla`
  order, communities and local-preference are all derived from the position of a PoP in the list.
  Reordering the list changes all of them together, which keeps forward and return paths symmetric.

One caveat: the Jinja environment is **not** strict. An undefined variable renders as empty text
instead of raising an error. This is deliberate, so optional blocks can be gated with
`{% if key %}`, but it means a misspelled variable fails silently. The acceptance gate is what
catches that.

---

## 5. The acceptance gate

The rule: **a schema plus template must reproduce the lab-tested config byte for byte, apart from
secrets.** If it does, the extraction is complete and nothing was lost or invented.

How it works here, stated plainly:

- `SITES` in `generator.py` holds the input values for eight known sites, one per role, circuit
  count and platform combination that was tested on real or virtual hardware.
- `python generator.py --roundtrip` renders all of them into `generated/*.rendered.conf`.
- Those rendered files **are committed**. The check is `git diff generated/` after a change. Only
  the lines you meant to change may differ.
- There is no automated assertion today. The diff is read by a person or an agent. When porting
  the method, make this an automated test that exits non-zero on any unexpected diff.

When the original extraction was done, the reference was the hand-built golden config in
`../Finalized-Template/`. After that first match, the committed renders became the reference.

Practical notes for a new design:

- Remove or freeze anything that changes per run, such as a timestamp in a header comment.
  Otherwise byte-for-byte can never pass.
- Compare with secrets replaced by a placeholder on both sides, so golden files can be committed.
- Keep one known site per combination you claim to support. An untested combination is a guess.

---

## 6. The FortiManager leg

The same schema feeds FortiManager zero-touch provisioning. Read `FMG-PORTING-MAP.md` for the full
variable-by-variable map. The model has four layers:

1. **Metadata variables.** One per schema variable, named as the upper-cased key. Tenant scope
   becomes an ADOM-level default. Site scope becomes a per-device value.
2. **CLI templates.** One per `emit_order` section, written with `$(VAR)` references.
3. **CLI template group.** One per role and circuit combination.
4. **Device blueprint.** One per role and platform. Named `BOR-{SPA-}{SINGLE|DUAL}-STD-{VM|30G|50G|120G}`.

A CSV row then creates a model device: serial number, blueprint name, device name, and one column
per site-scope variable.

Three translation rules do most of the work:

| Schema shape | FortiManager shape |
|---|---|
| Tenant-scope variable | Column left blank when equal to the default, so the ADOM default resolves |
| A list of objects such as `pops` | Flattened to fixed slots: `POP1_FQDN`, `POP2_FQDN`, `POP1_PROBE`. FortiManager CLI templates do not loop over lists |
| Derived value | Computed by the generator and sent as a ready column, for example `LAN_SUBNET` |

Drift between the two repos is stopped by a contract:

- `contract/roles-and-columns.yaml` is the single list of roles, meta variables and CSV columns.
  This repo owns it. The FortiManager-AI-SDK repo carries a byte-identical copy.
- `validate_contract.py` checks that the real CSV export matches the contract and exits non-zero on
  any difference. The FortiManager SDK runs the mirror check against its ADOM manifest.
- A new role starts as a role-spec file in the `BOR-Role-Registry` folder, then both sides build
  it, then the contract is updated, then both validators must pass.

---

## 7. Where everything lives

In this folder:

| Path | What it is |
|---|---|
| `schema/variables.yaml` | The single source of truth. Edit here first |
| `templates/*.conf.j2` | Four Jinja templates, one per role and circuit count |
| `generator.py` | Context building, guards, rendering, round-trip sites, FortiManager CSV export |
| `generator_page.py` | The form. Walks the schema: `applies()`, `show_if_ok()`, `widget()`, `validate()` |
| `app.py`, `pages/` | Streamlit entry point and the two extra pages |
| `generated/` | Committed renders of the known sites. The acceptance reference |
| `examples/` | Example site value files for `--values` |
| `contract/roles-and-columns.yaml` | Cross-repo contract for roles, meta variables, CSV columns |
| `validate_contract.py` | Contract check, exit 1 on drift |
| `fmg-export/` | CSV column specs and sample imports |
| `SKILL-READ-FIRST_MACD.md` | The step-by-step procedure for adding, changing or removing a variable |
| `FMG-PORTING-MAP.md` | Every variable mapped to its FortiManager meta field. A working draft, not yet in this repo |
| `GOTCHAS.md` | FortiManager install failures already diagnosed |
| `README.md`, `SETUP.md`, `PARTNER-GUIDE.md` | Overview, install and run, partner quick start |

In the MSSP-SE-Tools folder, outside this repo:

| Path | What it is |
|---|---|
| `FortiManager-AI-SDK/tools/org.ulysses.noc.fortimanager-adom-init/content/adom-manifest.yaml` | The ADOM state: meta variables, template groups, blueprints, packages |
| `FortiManager-AI-SDK/tools/org.ulysses.noc.fortimanager-adom-init/content/templates/` | The FortiManager CLI templates ported from the Jinja templates here |
| `FortiManager-AI-SDK/contract/roles-and-columns.yaml` | The vendored copy of the contract |
| `FortiManager-AI-SDK/tools/` | One tool per FortiManager action: metadata, blueprint, CSV import, install |
| `BOR-Role-Registry/` | Role-spec template, contract rules, the role list |

---

## 8. Recipe: porting a new design onto this method

1. **Freeze a golden config per role.** It must be lab-tested. Commit it with secrets replaced.
2. **List every value that varies.** Anything that would differ on another device or customer.
3. **Classify each value** with the five-scope rule of thumb in section 2.
4. **Model repeated things as lists of objects**, for example hubs and transports, the way `pops`
   is modelled here. Decide what list order means and derive everything positional from it.
5. **Write the schema.** One entry per variable, with a default wherever a sane one exists.
6. **Write the template in FortiOS dependency order.** Replace literals with `{{ key }}`. Gate
   optional blocks on a feature switch, not on a role name.
7. **Write the context builder.** Defaults, then site values, then derived values, then guards.
8. **Prove it.** Render each golden site and compare byte for byte. Automate the comparison.
9. **Only then add outputs.** The form, then the FortiManager CSV, then the contract.

Do not start with the form or with FortiManager. Both are thin once steps 1 to 8 pass, and both
are painful to debug when the extraction underneath them is incomplete.

---

## 9. Lessons that cost time

- Change the schema first. A value hard-coded in a template is a second source of truth.
- Fail loudly. A guard with a clear message beats a config that installs and misbehaves.
- Never put customer identity in an object name.
- One detect server per health check. Sharing one is rejected by FortiOS.
- The form caches the generator module. Restart Streamlit after changing `generator.py`.
- FortiManager's device database does not infer `set mode static` from `set ip`. See `GOTCHAS.md`.
- Real 30G and 50G units ship factory config that blocks some designs until purged. See `GOTCHAS.md`.
- Keep secrets out of schema defaults. Known exception to clean up: a POC admin password default
  is still present in `generator.py` and in schema help text. Replace it before any real deployment.
- Some comments in `SKILL-READ-FIRST_MACD.md` predate the dual-circuit templates and the form.
  Where it disagrees with this file or with the code, the code is right.

---

## 10. Driving this from FortiSOAR

Nothing here is specific to Streamlit. The form is one consumer of the schema. A playbook can be
another. The parts map as follows. These are suggestions for the person building the playbooks,
not tested claims.

| Here | In a playbook or Solution Pack |
|---|---|
| Schema variables with scope, type, default | The playbook's input fields or a record module's fields. Tenant scope fits a per-customer record, site scope a per-device record |
| `build_context()` | One step that merges defaults, site values and derived values |
| Template render | A Jinja render step. Keep the template text identical to the files here so the same golden files prove both |
| FortiManager CSV export | Calls to FortiManager for metadata, model device and install |
| Acceptance gate | The same golden files. A playbook that renders site-1 must produce the committed render |
| Contract and validator | Still the check that column and meta field names match on both sides |

Keep one schema. If the playbook carries its own copy of variable names, it becomes a third thing
that can drift, and the contract check should be extended to cover it.

---

## 11. Patterns worth reusing, with line references

Line numbers are as of 2026-11-24. Search for the quoted text if they have moved.

| Pattern | Where | Why copy it |
|---|---|---|
| Policies name a zone, never a port | `templates/bor.conf.j2` zone at line 98, policies from line 355 | A new LAN interface or VLAN joins `LAN_ZONE` and no policy changes |
| A list variable rendered with a loop | `schema/variables.yaml` `advertised_networks` at line 391, `templates/bor.conf.j2` line 268 | One row on the form becomes one BGP network statement. Four rows or twenty cost the same |
| A list on the form as an add-a-row table | `generator_page.py` line 181 | The form shows only the rows in use |
| A reserved number block per list | `templates/bor.conf.j2` line 194, `edit {{ 30 + loop.index0 }}` | Entries made from a list never collide with the fixed ones |
| Static or DHCP per circuit | `schema/variables.yaml` `wan_mode` at line 317, `templates/bor.conf.j2` lines 79 and 162 | One switch. Under DHCP the routes use `dynamic-gateway` and the address fields are left empty |
| Port filled in from the model | `schema/variables.yaml` `platforms` at line 53, `generator.py` lines 52 to 61 | Nobody types a port name. An override is allowed |
| Guard that ports are distinct | `generator.py` lines 68 to 79 | FortiOS accepts a duplicate port silently and the later block wins |
| Slots built ahead on the hub, with a count | `templates/bor-spa.conf.j2` lines 216 and 236, `generator.py` line 110 | The hub is not rendered again when a spoke is added |
| Object names from the role, not the identity | `generator.py` lines 85 to 92 | A NOC reads the same names at every customer |
| `advanced: true` | `generator_page.py` line 469 | The value stays in the schema with its default and leaves the main form |
| A feature that is off renders nothing new | `features` in `schema/variables.yaml` from line 167 | Adding a feature does not move the committed renders of existing sites |

**Do not copy these.**

- **The BGP block.** This design runs BGP per tunnel, with the hub as a route reflector
  (`templates/bor-spa.conf.j2` lines 253 to 275: `route-reflector-client`, `additional-path`,
  `link-down-failover enable`). Those settings are right here and wrong for BGP on loopback.
- **One template per circuit count.** `bor` and `bor-dual` are separate files, and so are the two
  hub files. Four templates must be kept in step. A template that loops over the circuits is
  better.
- **The list handling toward FortiManager.** `advertised_networks` and `trusted_host_routes` work
  in the generator and the form. They are not in the shipped CSV or in the contract. The plan in
  `FMG-PORTING-MAP.md` was a semicolon-joined value split by the template, and it was never
  tested. A consumer with flat columns is the only place where the length of a list costs
  anything.

One FortiOS rule to plan for: an interface cannot join a zone while a policy still names that
interface. Moving a configured device to a zone means removing those policy references first.
