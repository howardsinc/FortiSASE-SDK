"""Runner for params-dict SDK tools: python _fmg_exec.py <tool.py> <params.json>

Loads the tool module by file path (its name has dots, so it can't be imported by name), calls its
`main(params)` (the SDK's sync wrapper around `execute`), and prints the result as a single JSON
line — the LAST stdout line, which is what fmg_provision._exec_runner parses. Logging goes to
stderr so stdout stays clean. Any failure is reported as {"success": false, "error": ...} so the
UI shows a readable message instead of "produced no output".
"""
import importlib.util
import json
import logging
import pathlib
import sys
import traceback


def _load_tool(tool_path):
    sys.path.insert(0, str(tool_path.parent))
    spec = importlib.util.spec_from_file_location("fmg_tool", tool_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")
    if len(sys.argv) != 3:
        print(json.dumps({"success": False, "error": "usage: _fmg_exec.py <tool.py> <params.json>"}))
        sys.exit(2)
    tool_path = pathlib.Path(sys.argv[1]).resolve()
    params_path = pathlib.Path(sys.argv[2])
    try:
        params = json.loads(params_path.read_text(encoding="utf-8"))
        mod = _load_tool(tool_path)
        result = mod.main(params)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        result = {"success": False, "error": f"{type(e).__name__}: {e}"}
    print(json.dumps(result, default=str))
    sys.exit(0 if isinstance(result, dict) and result.get("success") else 1)


if __name__ == "__main__":
    main()
