# Config Generator — Gotchas (read before debugging FMG install failures)

Hard-won lessons. If you're an AI or engineer hitting a weird FMG error, check here first —
these cost real hours to diagnose.

---

## GOTCHA #1 — real 30G/50G ship a factory `lan → wan` policy that blocks `wan` as an SD-WAN member UNTIL it's purged

### Symptom
FMG install / install-preview on a **real hardware** model device (FortiGate-30G / 50G) fails with:

```
Commit failed: datasrc invalid. object: system sdwan members.X:interface. detail: wan.
solution: data cannot be used. reason: invalid value - prop[interface]:
firewall policy dstintf(wan) can not be used in system sdwan members.
```

The same config installs fine on a VM, direct-to-box via CLI, and on real HW **once the factory
policy is purged first**.

### Root cause (CONFIRMED on a truly-fresh device, 2026-09-09)
It is **NOT** a hardcoded name reject. A real FortiGate ships a **factory `lan → wan` firewall
policy** (id 1, srcintf `lan`, dstintf `wan`). That registers `wan` as a **firewall-policy dstintf**,
and FMG's SD-WAN-member datasrc validator then refuses `set interface "wan"` because it's in use as a
policy dstintf — verbatim the `firewall policy dstintf(wan) can not be used in system sdwan members`
error. **Purge that factory policy first and `wan` deregisters → the SD-WAN member validates fine.**

`BOR-02-GREENFIELD-HW-SMALL` does exactly this (`config firewall policy / purge`). The fix is to run
it as the **first member of every HW template group**, before the SD-WAN template. FMG's
`install/device` applies template-group members **in order** and validates against accumulated state
as each commits, so purge → validate is the correct order.

**Proof:** fresh 50G `spoke-99` (`FGT50GTK26048289`, never installed, never GUI-touched) had the
factory `lan→wan` policy; baseline `install/device` FAILED with the `wan` reject; adding
`BOR-02-GREENFIELD-HW-SMALL` at position 0 of the template group → `install/device` **GREEN**
(task done, 0 err), device DB shows SD-WAN **member 5 = `wan`** (Underlay_ZONE). Nothing else changed.

### Why the platform matrix looked confusing
| Case | `wan` as SD-WAN member | Why |
|---|---|---|
| Real HW modeled install, **factory policy present** | ❌ reject | `wan` is a factory-policy dstintf |
| Real HW modeled install, **greenfield purge first** | ✅ passes | factory policy gone → `wan` free |
| VM model device | ✅ passes | VM ships **no** factory `lan→wan` policy (port1) |
| Direct FortiOS CLI (console/SSH) | ✅ passes | the `.conf` runs `firewall policy / purge` up front |
| FMG GUI install | ✅ passes | the wizard's flow purges the factory policy first |

VMs, CLI, and the GUI all "worked" because none presented the factory policy to the SD-WAN validator.
The historical "30G" success was a VM stand-in (`30G-spoke-4`, serial `FGVMMLTM26000451`, platform
`FortiGate-ARM64-AWS`). `spoke-13` passed because an earlier GUI install had already purged its
factory policy. **This week was simply the first real-30G *platform* modeled install through a HW
template group that lacked the greenfield purge.**

### THE FIX
- **App side:** keep `wan_port: wan`. `wan` IS usable as WAN1 / the underlay SD-WAN member on real
  30G/50G — **single AND dual**. No rename, no `a`, no "impossible triangle" (that earlier conclusion
  was measured on template groups missing the greenfield purge).
- **FMG-AI-SDK side:** every HW template group (`BOR-SINGLE / DUAL / SPA-SINGLE / SPA-DUAL-STD-HW`)
  MUST start with `BOR-02-GREENFIELD-HW-SMALL` (or `BOR-02-GREENFIELD-120G` for the 120G). adom-init's
  `content/adom-manifest.yaml` ships the groups with it at position 0 by default. See FMG-AI-SDK
  gotcha #23.

Status: root cause + fix **CONFIRMED** on a fresh 50G (`install/device` green). FMG-SDK is rolling the
purge to all four HW groups + the manifest; full package-install across siblings + a real dial-home
are being finalized.

---

## GOTCHA #2 — `lan3`/`lan4` only exist AFTER the virtual-switch purge (so the purge must come first)

Real 30G/50G ship their LAN ports inside a default **hard virtual-switch** (`config system
virtual-switch`, often named `lan`). The greenfield purge (`config system virtual-switch / purge`,
also in `BOR-02-GREENFIELD-HW-SMALL`) breaks it out into real independent physical interfaces. Until
it runs, ports like `lan3` don't exist standalone (or default to VLAN type) and FMG's validator
throws `error system interface - lan3 :-999 - invalid value`. Same remedy as #1: keep the greenfield
purge as the **first** HW template-group member so ports are physical before anything references
them; belt-and-suspenders, the HW interface template can also `set type physical` + `set vdom "root"`
on them. (Hardware note: the 30G has **no `lan3`** at all — ports are `wan`/`lan1`/`lan2`/`a`. The 50G
**does** have a real `lan3`.)

---

## Where the fix lives
- **This repo:** `schema/variables.yaml` platform port-map (30G/50G rows) + `PLATFORM_PORTS` in
  `generator_page.py`. Templates are data-driven off `{{ wan_port }}` / `{{ wan2_port }}` /
  `{{ lan_port }}` — the schema stays **`wan`-based**; no rename needed.
- **FMG-AI-SDK repo:** the four HW template groups must start with `BOR-02-GREENFIELD-HW-SMALL`; see
  its gotcha #23. CLI templates are otherwise data-driven.
- **Rule of thumb:** `wan` IS a valid SD-WAN member on real HW **as long as the factory `lan→wan`
  policy is purged first** (greenfield purge = template-group member 0). The reject means the purge
  isn't running before the SD-WAN validation — not that `wan` is forbidden.
