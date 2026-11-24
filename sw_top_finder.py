import os
import sys
import json
import time

# Enforce UTF-8 output streams with replacement errors to prevent encoding crashes on Windows
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

try:
    import win32com.client
    import pythoncom
    WIN32_AVAILABLE = True
except ImportError:
    WIN32_AVAILABLE = False

try:
    from sw_export_runner import load_sw_typelib, get_dynamic_sw_app, get_component_model
except ImportError:
    def load_sw_typelib():
        return None
    def get_dynamic_sw_app(raw_obj):
        import win32com.client.dynamic
        return win32com.client.dynamic.Dispatch(raw_obj)
    def get_component_model(comp):
        try:
            return comp.GetModelDoc2()
        except:
            return None


DEFAULT_SLDWORKS_EXE = r"C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\sldworks.exe"
SOLIDWORKS_PATH_ENV = "GIT4SW_SOLIDWORKS_PATH"


def _norm_path(path):
    """Normalize a Windows path for comparison (slashes, case, env vars)."""
    if not path:
        return ""
    try:
        path = os.path.expandvars(os.path.expanduser(str(path)))
        return os.path.normpath(path).replace("\\", "/").lower()
    except Exception:
        return str(path).replace("\\", "/").lower()


def get_running_sw_exe(swApp):
    """Return the executable path of the bound SolidWorks instance, if reported."""
    try:
        exe = swApp.GetExecutablePath()
        return str(exe) if exe else None
    except Exception:
        return None


def connect_to_solidworks(sldworks_exe=None):
    """Bind to a running SolidWorks instance or launch the configured executable.

    sldworks_exe is the path from config.json ("solidworks_path"). It takes priority
    over the legacy hardcoded default when launching a new SolidWorks process.
    """
    swApp = None
    was_already_running = False

    configured_exe = sldworks_exe or DEFAULT_SLDWORKS_EXE
    configured_norm = _norm_path(configured_exe)
    print(f"Configured SolidWorks executable: {configured_exe}", flush=True)
    print("Connecting to SolidWorks...", flush=True)

    try:
        raw_obj = win32com.client.GetActiveObject("SldWorks.Application")
        swApp = get_dynamic_sw_app(raw_obj)
        running_exe = get_running_sw_exe(swApp)
        if running_exe and configured_norm and _norm_path(running_exe) != configured_norm:
            print(
                f"WARNING: Active SolidWorks instance is '{running_exe}', which differs from the "
                f"configured path '{configured_exe}'. Using the active instance.",
                flush=True,
            )
        print("Connected to active SolidWorks instance.", flush=True)
        was_already_running = True
    except Exception:
        pass

    if swApp is None:
        if not os.path.exists(configured_exe):
            print(
                f"Error: Configured SolidWorks executable not found: {configured_exe}. "
                "Check 'solidworks_path' in config.json.",
                file=sys.stderr,
                flush=True,
            )
            return None, False

        print(f"No active instance found. Launching SolidWorks: {configured_exe}", flush=True)
        import subprocess
        try:
            subprocess.Popen([configured_exe])
        except Exception as e_launch:
            print(f"Failed to launch SolidWorks '{configured_exe}': {e_launch}", file=sys.stderr, flush=True)
            return None, False

        poll_timeout = 30.0
        poll_start = time.time()
        while time.time() - poll_start < poll_timeout:
            try:
                raw_obj = win32com.client.GetActiveObject("SldWorks.Application")
                swApp = get_dynamic_sw_app(raw_obj)
                launched_exe = get_running_sw_exe(swApp)
                if launched_exe and configured_norm and _norm_path(launched_exe) != configured_norm:
                    print(
                        f"WARNING: Connected SolidWorks instance is '{launched_exe}', which differs from "
                        f"the configured path '{configured_exe}'.",
                        flush=True,
                    )
                print("Connected to SolidWorks after launching.", flush=True)
                break
            except Exception:
                time.sleep(0.5)

    if swApp is None:
        try:
            raw_obj = win32com.client.GetObject(Class="SldWorks.Application")
            swApp = get_dynamic_sw_app(raw_obj)
            running_exe = get_running_sw_exe(swApp)
            if running_exe and configured_norm and _norm_path(running_exe) != configured_norm:
                print(
                    f"WARNING: Active SolidWorks instance is '{running_exe}', which differs from the "
                    f"configured path '{configured_exe}'. Using the active instance.",
                    flush=True,
                )
            print("Connected to SolidWorks via GetObject.", flush=True)
            was_already_running = True
        except Exception as e:
            print(f"Failed to connect to SolidWorks: {e}", file=sys.stderr, flush=True)

    return swApp, was_already_running


def get_document_dependencies(swApp, file_abs):
    """Extract referenced file paths from an assembly or drawing using GetDocumentDependencies2.
    This reads only the dependency metadata block without loading the file into SolidWorks,
    making it orders of magnitude faster than OpenDoc6 + GetChildren.
    Returns a set of normalized child file paths."""
    f_lower = file_abs.lower()
    if not (f_lower.endswith(".sldasm") or f_lower.endswith(".slddrw")):
        return set()

    child_paths = set()
    file_norm = os.path.normpath(file_abs).replace("\\", "/").lower()

    # Try multiple API variants in order of preference
    methods = [
        ("GetDocumentDependencies2", (file_abs, True, True, False)),  # accurate
        ("GetDocumentDependencies2", (file_abs, True, True, True)),   # fast
        ("GetDocumentDependencies",  (file_abs, True, True, True)),
        ("GetDocumentDependencies",  (file_abs,)),
    ]

    for method_name, args in methods:
        try:
            method = getattr(swApp, method_name, None)
            if method is None:
                continue
            depends = method(*args)
            if not depends:
                continue

            # Normalize and collect paths from the result array.
            # GetDocumentDependencies returns name/path pairs; just extract valid file paths.
            for item in depends:
                try:
                    p = str(item)
                    if p and os.path.exists(p):
                        norm = os.path.normpath(p).replace("\\", "/").lower()
                        # Exclude self-reference (the parent file itself)
                        if norm != file_norm:
                            child_paths.add(norm)
                except:
                    pass

            if child_paths:
                break  # found valid results
        except Exception as e:
            print(f"  {method_name} failed for {os.path.basename(file_abs)}: {e}", flush=True)

    return child_paths


def get_assembly_children(swApp, file_abs):
    """Backward-compatible wrapper: dependencies of a .sldasm only."""
    if not file_abs.lower().endswith(".sldasm"):
        return set()
    return get_document_dependencies(swApp, file_abs)


def _load_rel_list_file(path):
    """Read a JSON array of repository-relative paths written by the GUI."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [str(item) for item in data if str(item).strip()]
    except Exception as e:
        print(f"Failed to read list file '{path}': {e}", file=sys.stderr, flush=True)
    return []


def run_orphan_scan(swApp, workspace_path, assemblies, parts, drawings):
    """Find .sldprt files that no assembly in the given scope references.

    Drawings are treated as top-level documents: they never become orphan candidates
    themselves, but a drawing whose referenced models are all orphan parts is reported
    in ``orphan_part_drawings`` so the GUI can offer to remove it together with them.
    """
    # Map normalized absolute path -> repository-relative path (original casing)
    part_norm_to_rel = {}
    for rel in parts or []:
        rel = rel.replace("\\", "/")
        if os.path.basename(rel).startswith("~$"):
            continue
        abs_p = os.path.normpath(os.path.join(workspace_path, rel))
        if os.path.exists(abs_p):
            part_norm_to_rel[_norm_path(abs_p)] = rel

    # 1. Collect every file referenced by the assemblies inside the scan scope.
    referenced = set()
    asm_total = len(assemblies or [])
    for index, rel in enumerate(assemblies or [], 1):
        abs_f = os.path.normpath(os.path.join(workspace_path, rel))
        if os.path.exists(abs_f):
            try:
                referenced.update(get_document_dependencies(swApp, abs_f))
            except Exception as e:
                print(f"  Dependency scan failed for {os.path.basename(abs_f)}: {e}", flush=True)
        print(f"[PROGRESS] A {index}/{asm_total}", flush=True)

    orphan_norm = {path for path in part_norm_to_rel if path not in referenced}
    orphans = sorted(part_norm_to_rel[path] for path in orphan_norm)
    print(f"Found {len(orphans)} orphan part(s) out of {len(part_norm_to_rel)} candidate(s).", flush=True)

    # 2. Associate drawings that only reference orphan parts with those parts.
    orphan_part_drawings = {}
    drw_total = len(drawings or [])
    for index, rel in enumerate(drawings or [], 1):
        rel = rel.replace("\\", "/")
        abs_f = os.path.normpath(os.path.join(workspace_path, rel))
        if os.path.exists(abs_f) and abs_f.lower().endswith(".slddrw"):
            try:
                deps = get_document_dependencies(swApp, abs_f)
            except Exception as e:
                print(f"  Dependency scan failed for {os.path.basename(abs_f)}: {e}", flush=True)
                deps = set()
            existing_models = [
                d for d in deps
                if os.path.exists(d) and d.lower().endswith((".sldprt", ".sldasm"))
            ]
            if existing_models and all(d in orphan_norm for d in existing_models):
                for dep in existing_models:
                    orphan_part_drawings.setdefault(part_norm_to_rel[dep], []).append(rel)
        print(f"[PROGRESS] D {index}/{drw_total}", flush=True)

    for key in orphan_part_drawings:
        orphan_part_drawings[key] = sorted(set(orphan_part_drawings[key]))

    return {
        "mode": "orphans",
        "orphans": orphans,
        "orphan_part_drawings": orphan_part_drawings,
        "scanned_assemblies": asm_total,
        "scanned_drawings": drw_total,
        "error": None,
    }


def main():
    if not WIN32_AVAILABLE:
        print("Error: PyWin32 is not installed or not running on Windows.", file=sys.stderr)
        sys.exit(1)

    if len(sys.argv) < 2:
        print(
            "Usage: sw_top_finder.py <workspace_path> [--files file1,file2,...] "
            "[--solidworks-path <SLDWORKS.exe>]",
            file=sys.stderr,
        )
        sys.exit(1)

    workspace_path = os.path.abspath(sys.argv[1])

    # Optional arguments (order-independent):
    #   --mode top|orphans            scan mode (default: top)
    #   --files file1,file2,...       specific assemblies to scan (top mode)
    #   --assemblies-file <json>      JSON array of repo-relative .sldasm paths (orphans mode)
    #   --parts-file <json>           JSON array of repo-relative .sldprt candidates (orphans mode)
    #   --drawings-file <json>        JSON array of repo-relative .slddrw paths (orphans mode)
    #   --solidworks-path <path>      SLDWORKS.exe from config.json
    scan_files = None
    sldworks_exe = None
    mode = "top"
    assemblies_file = None
    parts_file = None
    drawings_file = None
    i = 2
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--files" and i + 1 < len(sys.argv):
            scan_files = [f.strip() for f in sys.argv[i + 1].split(",") if f.strip()]
            i += 2
        elif arg == "--mode" and i + 1 < len(sys.argv):
            mode = sys.argv[i + 1].strip().lower()
            i += 2
        elif arg == "--assemblies-file" and i + 1 < len(sys.argv):
            assemblies_file = sys.argv[i + 1]
            i += 2
        elif arg == "--parts-file" and i + 1 < len(sys.argv):
            parts_file = sys.argv[i + 1]
            i += 2
        elif arg == "--drawings-file" and i + 1 < len(sys.argv):
            drawings_file = sys.argv[i + 1]
            i += 2
        elif arg == "--solidworks-path" and i + 1 < len(sys.argv):
            sldworks_exe = sys.argv[i + 1]
            i += 2
        else:
            i += 1

    pythoncom.CoInitialize()
    # Let the shared typelib loader resolve sldworks.tlb/swconst.tlb from the
    # configured executable's folder as well.
    if sldworks_exe:
        os.environ[SOLIDWORKS_PATH_ENV] = sldworks_exe
    load_sw_typelib()

    swApp, was_already_running = connect_to_solidworks(sldworks_exe)
    if not swApp:
        effective_exe = sldworks_exe or DEFAULT_SLDWORKS_EXE
        print("Error: Could not connect to or start SolidWorks.", file=sys.stderr, flush=True)
        # Output empty result
        print(json.dumps({
            "top_assemblies": [],
            "orphans": [],
            "orphan_part_drawings": {},
            "error": f"Could not connect to or start SolidWorks (configured path: {effective_exe})",
        }))
        sys.exit(1)

    try:
        # Suppress startup screen
        try:
            swApp.SetUserPreferenceToggle(389, False)
        except:
            pass

        if mode == "orphans":
            assemblies = _load_rel_list_file(assemblies_file) if assemblies_file else (scan_files or [])
            parts = _load_rel_list_file(parts_file) if parts_file else []
            drawings = _load_rel_list_file(drawings_file) if drawings_file else []
            print(
                f"Orphan scan scope: {len(assemblies)} assembly(ies), "
                f"{len(parts)} candidate part(s), {len(drawings)} drawing(s).",
                flush=True,
            )
            orphan_result = run_orphan_scan(swApp, workspace_path, assemblies, parts, drawings)
            print(f"Scan complete. Found {len(orphan_result['orphans'])} orphan part(s).", flush=True)
            print(json.dumps(orphan_result))
            return

        # Collect all .sldasm files
        if scan_files:
            asm_files = []
            for rel in scan_files:
                base_fn = os.path.basename(rel)
                if base_fn.startswith("~$"):
                    continue
                abs_path = os.path.normpath(os.path.join(workspace_path, rel))
                if abs_path.lower().endswith(".sldasm") and os.path.exists(abs_path):
                    asm_files.append(abs_path)
        else:
            asm_files = []
            for dirpath, dirnames, filenames in os.walk(workspace_path):
                # Prune .git and .backup directories in-place without skipping .github
                dirnames[:] = [d for d in dirnames if d.lower() not in (".git", ".backup")]
                for fn in filenames:
                    if fn.lower().endswith(".sldasm") and not fn.startswith("~$"):
                        asm_files.append(os.path.join(dirpath, fn))

        total = len(asm_files)
        print(f"Found {total} .sldasm files to scan.", flush=True)

        if total == 0:
            print(json.dumps({"top_assemblies": [], "error": None}))
            sys.exit(0)

        # Build dependency graph: for each assembly, what files does it reference?
        # A top-level assembly is one that is NOT referenced by any other assembly.
        all_asm_norm = set()
        referenced_set = set()

        for norm_path in (os.path.normpath(p).replace("\\", "/").lower() for p in asm_files):
            all_asm_norm.add(norm_path)

        processed = 0
        for asm_abs in asm_files:
            processed += 1
            asm_norm = os.path.normpath(asm_abs).replace("\\", "/").lower()
            basename = os.path.basename(asm_abs)
            print(f"[{processed}/{total}] Scanning: {basename}", flush=True)

            try:
                children = get_assembly_children(swApp, asm_abs)
                for child_path in children:
                    # Check if the child is one of our assembly files
                    if child_path in all_asm_norm:
                        referenced_set.add(child_path)
                        print(f"  -> References: {os.path.basename(child_path)}", flush=True)
            except Exception as e:
                print(f"  Error: {e}", flush=True)

            print(f"[PROGRESS] {processed}/{total}", flush=True)

        # Top-level = assemblies not referenced by any other assembly
        top_level = sorted(all_asm_norm - referenced_set)

        print(f"\nScan complete. Found {len(top_level)} top-level assembly(ies).", flush=True)
        for t in top_level:
            print(f"  TOP: {t}", flush=True)

        # Output JSON result
        result = {
            "top_assemblies": top_level,
            "total_scanned": total,
            "error": None
        }
        print(json.dumps(result))

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr, flush=True)
        print(json.dumps({"top_assemblies": [], "error": str(e)}))
        sys.exit(1)
    finally:
        # GetDocumentDependencies2 does not open documents, so no document cleanup needed.
        # Exit SolidWorks if we launched it.
        if not was_already_running:
            try:
                swApp.ExitApp()
            except:
                pass
            try:
                import subprocess
                subprocess.run("taskkill /F /IM SLDWORKS.exe", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except:
                pass

        pythoncom.CoUninitialize()


if __name__ == "__main__":
    main()
