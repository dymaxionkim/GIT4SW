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


def connect_to_solidworks():
    swApp = None
    was_already_running = False
    print("Connecting to SolidWorks...", flush=True)
    try:
        raw_obj = win32com.client.GetActiveObject("SldWorks.Application")
        swApp = get_dynamic_sw_app(raw_obj)
        print("Connected to active SolidWorks instance.", flush=True)
        was_already_running = True
    except Exception:
        pass

    if swApp is None:
        sldworks_exe = r"C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\sldworks.exe"
        if os.path.exists(sldworks_exe):
            print(f"No active instance found. Launching SolidWorks: {sldworks_exe}", flush=True)
            import subprocess
            subprocess.Popen([sldworks_exe])
            poll_timeout = 30.0
            poll_start = time.time()
            while time.time() - poll_start < poll_timeout:
                try:
                    raw_obj = win32com.client.GetActiveObject("SldWorks.Application")
                    swApp = get_dynamic_sw_app(raw_obj)
                    print("Connected to SolidWorks after launching.", flush=True)
                    break
                except Exception:
                    time.sleep(0.5)

    if swApp is None:
        try:
            raw_obj = win32com.client.GetObject(Class="SldWorks.Application")
            swApp = get_dynamic_sw_app(raw_obj)
            print("Connected to SolidWorks via GetObject.", flush=True)
            was_already_running = True
        except Exception as e:
            print(f"Failed to connect to SolidWorks: {e}", file=sys.stderr, flush=True)

    return swApp, was_already_running


def get_assembly_children(swApp, file_abs):
    """Extract direct child component paths from an assembly using GetDocumentDependencies2.
    This reads only the dependency metadata block without loading the file into SolidWorks,
    making it orders of magnitude faster than OpenDoc6 + GetChildren.
    Returns a set of normalized child file paths."""
    f_lower = file_abs.lower()
    if not f_lower.endswith(".sldasm"):
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


def main():
    if not WIN32_AVAILABLE:
        print("Error: PyWin32 is not installed or not running on Windows.", file=sys.stderr)
        sys.exit(1)

    if len(sys.argv) < 2:
        print("Usage: sw_top_finder.py <workspace_path> [--files file1,file2,...]", file=sys.stderr)
        sys.exit(1)

    workspace_path = os.path.abspath(sys.argv[1])

    # Optional: specific files to scan (comma-separated relative paths)
    scan_files = None
    if len(sys.argv) >= 4 and sys.argv[2] == "--files":
        scan_files = [f.strip() for f in sys.argv[3].split(",") if f.strip()]

    pythoncom.CoInitialize()
    load_sw_typelib()

    swApp, was_already_running = connect_to_solidworks()
    if not swApp:
        print("Error: Could not connect to or start SolidWorks.", file=sys.stderr, flush=True)
        # Output empty result
        print(json.dumps({"top_assemblies": [], "error": "Could not connect to SolidWorks"}))
        sys.exit(1)

    try:
        # Suppress startup screen
        try:
            swApp.SetUserPreferenceToggle(389, False)
        except:
            pass

        # Collect all .sldasm files
        if scan_files:
            asm_files = []
            for rel in scan_files:
                abs_path = os.path.normpath(os.path.join(workspace_path, rel))
                if abs_path.lower().endswith(".sldasm") and os.path.exists(abs_path):
                    asm_files.append(abs_path)
        else:
            asm_files = []
            for dirpath, dirnames, filenames in os.walk(workspace_path):
                # Skip .git directory
                if ".git" in dirpath:
                    continue
                for fn in filenames:
                    if fn.lower().endswith(".sldasm"):
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
