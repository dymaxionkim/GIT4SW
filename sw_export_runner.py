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


# We only import win32com and pythoncom when running on Windows
try:
    import win32com.client
    import pythoncom
    WIN32_AVAILABLE = True
except ImportError:
    WIN32_AVAILABLE = False

SW_MOD = None

def load_sw_typelib():
    global SW_MOD
    if SW_MOD is not None:
        return SW_MOD
    try:
        import win32com.client.gencache
        # Find path to sldworks.tlb dynamically to get the correct registered or unregistered version
        tlb_path = r"C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\sldworks.tlb"
        major, minor = 34, 0  # Fallback to SW 2026 (Major 34)
        if os.path.exists(tlb_path):
            try:
                import pythoncom
                tlb = pythoncom.LoadTypeLib(tlb_path)
                attr = tlb.GetLibAttr()
                major = attr[3]
                minor = attr[4]
            except Exception as e:
                print(f"Warning: Failed to load typelib metadata from file: {e}")
        
        # Load and compile early-binding module using the exact version
        SW_MOD = win32com.client.gencache.EnsureModule('{83A33D31-27C5-11CE-BFD4-00400513BB57}', 0, major, minor)
        
        # Also ensure constants module is loaded
        tlb_const_path = r"C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\swconst.tlb"
        if os.path.exists(tlb_const_path):
            try:
                import pythoncom
                tlb = pythoncom.LoadTypeLib(tlb_const_path)
                attr = tlb.GetLibAttr()
                c_major = attr[3]
                c_minor = attr[4]
                win32com.client.gencache.EnsureModule('{4687F359-55D0-4CD3-B6CF-2EB42C11F989}', 0, c_major, c_minor)
            except Exception as e:
                pass
    except Exception as e:
        print(f"Warning: Failed to load early-binding typelibs: {e}", flush=True)
    return SW_MOD

def get_dynamic_sw_app(raw_obj):
    if raw_obj is None:
        return None
    load_sw_typelib()
    try:
        import win32com.client.dynamic
        return win32com.client.dynamic.Dispatch(raw_obj)
    except Exception as e:
        print(f"Warning: Failed to ensure dynamic dispatch wrapper: {e}", flush=True)
        try:
            return win32com.client.Dispatch(raw_obj)
        except Exception:
            return raw_obj

def get_component_model(comp):
    if comp is None:
        return None
    
    # 1. Try early binding wrapper first if possible
    mod = load_sw_typelib()
    if mod and hasattr(mod, 'IComponent2'):
        try:
            raw_ole = comp._oleobj_ if hasattr(comp, '_oleobj_') else comp
            comp_early = mod.IComponent2(raw_ole)
            doc = comp_early.GetModelDoc2()
            if doc:
                raw_doc_ole = doc._oleobj_ if hasattr(doc, '_oleobj_') else doc
                if hasattr(mod, 'IModelDoc2'):
                    return mod.IModelDoc2(raw_doc_ole)
                return win32com.client.Dispatch(raw_doc_ole)
        except Exception as e_early:
            pass

    # 2. Try late binding property/method fallback
    try:
        # Try calling it as a method (common in late-binding)
        comp_model = comp.GetModelDoc2()
        if comp_model:
            return comp_model
    except Exception:
        pass

    try:
        # Try getting it as a property
        comp_model_val = comp.GetModelDoc2
        comp_model = comp_model_val() if callable(comp_model_val) else comp_model_val
        if comp_model:
            return comp_model
    except Exception:
        pass

    try:
        # Try old GetModelDoc method
        comp_model = comp.GetModelDoc()
        if comp_model:
            return comp_model
    except Exception:
        pass

    return None

def get_custom_property_value(prop_mgr, name):
    if prop_mgr is None:
        return ""
    # 1. Try early-binding signature (returns (res, val, resolved_val, was_resolved))
    try:
        res_tuple = prop_mgr.Get5(name, False)
        if isinstance(res_tuple, tuple) and len(res_tuple) >= 3:
            return res_tuple[2]
    except Exception:
        pass
        
    try:
        res_tuple = prop_mgr.Get6(name, False)
        if isinstance(res_tuple, tuple) and len(res_tuple) >= 3:
            return res_tuple[2]
    except Exception:
        pass

    # 2. Try late-binding signature with VARIANT output parameters
    try:
        val_out = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BSTR, "")
        res_out = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BSTR, "")
        was_resolved = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BOOL, False)
        try:
            prop_mgr.Get5(name, False, val_out, res_out, was_resolved)
            return res_out.value
        except Exception:
            try:
                prop_mgr.Get6(name, False, val_out, res_out, was_resolved)
                return res_out.value
            except Exception:
                pass
    except Exception:
        pass
    return ""

def _clean_material_name(raw):
    if not raw:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    # Reject color/appearance tuples: "(1.0, 0.94, ...)" or "(0.5, 0.5, ...)"
    if s.startswith("(") and s.endswith(")"):
        inner = s[1:-1]
        parts = [p.strip() for p in inner.split(",")]
        if parts and all(_is_float_like(p) for p in parts):
            return ""
    # Parse "solidworks materials|보통 탄소강|9" → extract middle part
    if "|" in s:
        segments = s.split("|")
        if len(segments) >= 2:
            idx = 1 if len(segments) >= 2 else 0
            candidate = segments[idx].strip()
            if candidate:
                return candidate
    return s

def _is_float_like(s):
    try:
        float(s)
        return True
    except ValueError:
        return False

def get_builtin_material(model, config_name):
    if model is None:
        return ""
    candidates = []
    # 1. IModelDoc2.MaterialUserName — user-facing material name (SW 2024+)
    try:
        mat_raw = model.MaterialUserName
        mat_name = mat_raw() if callable(mat_raw) else mat_raw
        if mat_name:
            cleaned = _clean_material_name(str(mat_name))
            if cleaned:
                candidates.append(cleaned)
    except Exception:
        pass
    # 2. IModelDoc2.MaterialIdName — internal material ID (SW 2024+)
    try:
        mat_raw = model.MaterialIdName
        mat_name = mat_raw() if callable(mat_raw) else mat_raw
        if mat_name:
            cleaned = _clean_material_name(str(mat_name))
            if cleaned:
                candidates.append(cleaned)
    except Exception:
        pass
    # 3. IModelDocExtension.GetMaterial — method that may return material
    try:
        ext_val = model.Extension
        ext = ext_val() if callable(ext_val) else ext_val
        if ext:
            mat_raw = ext.GetMaterial
            mat_name = mat_raw() if callable(mat_raw) else mat_raw
            if mat_name:
                cleaned = _clean_material_name(str(mat_name))
                if cleaned:
                    candidates.append(cleaned)
    except Exception:
        pass
    # 4. IModelDoc2.MaterialPropertyValues — might contain name
    try:
        mat_raw = model.MaterialPropertyValues
        mat_name = mat_raw() if callable(mat_raw) else mat_raw
        if mat_name:
            cleaned = _clean_material_name(str(mat_name))
            if cleaned:
                candidates.append(cleaned)
    except Exception:
        pass
    # Return the first non-empty, valid candidate
    for c in candidates:
        return c
    return ""

def get_builtin_weight(model):
    if model is None:
        return None
    try:
        ext_val = model.Extension
        ext = ext_val() if callable(ext_val) else ext_val
        if ext is None:
            return None
        mp_raw = ext.CreateMassProperty2
        mp = mp_raw() if callable(mp_raw) else mp_raw
        if mp is None:
            mp_raw = ext.CreateMassProperty
            mp = mp_raw() if callable(mp_raw) else mp_raw
        if mp is None:
            return None
        mass_raw = mp.Mass
        mass = mass_raw() if callable(mass_raw) else mass_raw
        if mass is None:
            return None
        mass_kg = float(mass)
        # Convert to kg based on document unit system
        try:
            unit_raw = model.GetUnits
            unit_val = unit_raw() if callable(unit_raw) else unit_raw
            unit_int = int(unit_val) if unit_val is not None else 0
            # swMM=0(grams), swCM=1(grams), swMeters=2(kg), swInches=3(pounds), swFeet=4(pounds)
            if unit_int in (0, 1):
                mass_kg = mass_kg / 1000.0
            elif unit_int in (3, 4):
                mass_kg = mass_kg * 0.45359237
        except Exception:
            pass
        return mass_kg
    except Exception:
        pass
    return None

def force_visible(swApp):
    if not swApp:
        return
    try:
        swApp.Visible = True
        swApp.UserControl = True
        try:
            frame = swApp.Frame()
            if frame:
                frame.KeepInvisible = False
        except:
            pass
    except Exception as e_vis:
        print(f"Warning: Early-binding visible property set failed ({e_vis}). Trying dynamic wrapper fallback...", flush=True)
        try:
            import win32com.client.dynamic
            dyn_sw = win32com.client.dynamic.Dispatch(swApp)
            dyn_sw.Visible = True
            dyn_sw.UserControl = True
            try:
                frame = dyn_sw.Frame()
                if frame:
                    frame.KeepInvisible = False
            except:
                pass
        except Exception as e_dyn:
            print(f"Warning: Could not set visibility even via dynamic dispatch: {e_dyn}", flush=True)

def start_or_bind_solidworks():
    pythoncom.CoInitialize()
    swApp = None
    was_already_running = False
    
    # 1. Try to connect to an existing active instance first
    print("Attempting to connect to active SolidWorks instance via GetActiveObject...", flush=True)
    try:
        raw_obj = win32com.client.GetActiveObject("SldWorks.Application")
        swApp = get_dynamic_sw_app(raw_obj)
        print("Successfully bound to active SolidWorks instance.", flush=True)
        was_already_running = True
    except Exception as e_active:
        pass
        
    # 2. If not found, launch SOLIDWORKS directly and poll ROT
    if swApp is None:
        sldworks_exe = r"C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\sldworks.exe"
        if os.path.exists(sldworks_exe):
            print(f"No active instance found. Spawning SolidWorks process directly: {sldworks_exe}", flush=True)
            try:
                import subprocess
                subprocess.Popen([sldworks_exe])
                
                # Poll ROT for registration up to 30 seconds
                poll_timeout = 30.0
                poll_start = time.time()
                while time.time() - poll_start < poll_timeout:
                    try:
                        raw_obj = win32com.client.GetActiveObject("SldWorks.Application")
                        swApp = get_dynamic_sw_app(raw_obj)
                        print("Successfully bound to SolidWorks instance after manual process launch.", flush=True)
                        break
                    except Exception:
                        time.sleep(0.5)
            except Exception as e_launch:
                print(f"Failed to launch SolidWorks directly: {e_launch}.", flush=True)
                
    # 3. Fallback to GetObject (dynamic representation)
    if swApp is None:
        print("Attempting fallback connection via GetObject...", flush=True)
        try:
            raw_obj = win32com.client.GetObject(Class="SldWorks.Application")
            swApp = get_dynamic_sw_app(raw_obj)
            print("Successfully bound to SolidWorks instance via GetObject.", flush=True)
            was_already_running = True
        except Exception as e_get:
            print(f"Failed to start or bind SolidWorks instance: {e_get}", flush=True)
            
    return swApp, was_already_running

def close_all_documents_without_saving(swApp, already_open_paths=None):
    print("close_all_documents_without_saving: start", flush=True)
    if not swApp:
        print("close_all_documents_without_saving: swApp is None", flush=True)
        return

    orig_user_control = True
    try:
        orig_user_control = swApp.UserControl
        swApp.UserControl = False
    except Exception:
        pass

    # Path normalization helper: unify slash direction and case to prevent matching failures
    def _norm(p):
        if not p:
            return ""
        return os.path.normpath(p).replace("\\", "/").lower()

    # Rebuild already_open_paths using the same normalization scheme
    normalized_already_open = set()
    if already_open_paths:
        for ap in already_open_paths:
            if ap:
                normalized_already_open.add(_norm(ap))
                base = os.path.basename(ap)
                if base:
                    normalized_already_open.add(_norm(base))
                    no_ext = os.path.splitext(base)[0]
                    if no_ext:
                        normalized_already_open.add(no_ext.lower())

    def _is_already_open(path, title):
        if not normalized_already_open:
            return False
        np = _norm(path)
        nt = _norm(title)
        if np and np in normalized_already_open:
            return True
        if nt:
            if nt in normalized_already_open:
                return True
            no_ext = os.path.splitext(nt)[0]
            if no_ext and no_ext in normalized_already_open:
                return True
        return False

    # Helper to query the list of open documents via GetDocuments()
    def _get_open_docs():
        try:
            val = getattr(swApp, 'GetDocuments')
            return val() if callable(val) else val
        except Exception:
            try:
                return swApp.GetDocuments()
            except Exception as e:
                print(f"close_all_documents_without_saving: GetDocuments() failed: {e}", flush=True)
                return None

    try:
        time.sleep(0.1)
        iteration = 0
        last_doc_count = -1
        stuck_count = 0

        while iteration < 50:
            docs_left = _get_open_docs()

            if not docs_left:
                break

            current_count = len(docs_left)
            print(f"close_all_documents_without_saving: iteration {iteration + 1}, {current_count} document(s) open.", flush=True)
            if current_count == last_doc_count:
                stuck_count += 1
                if stuck_count > 5:
                    print(f"close_all_documents_without_saving: detected stuck count ({current_count} docs), breaking.", flush=True)
                    break
            else:
                stuck_count = 0
            last_doc_count = current_count

            parent_files = []
            child_files = []

            for d in docs_left:
                try:
                    try:
                        path_val = d.GetPathName
                        path = path_val() if callable(path_val) else path_val
                    except Exception:
                        path = getattr(d, 'GetPathName')
                        if callable(path):
                            path = path()

                    try:
                        title_val = d.GetTitle
                        title = title_val() if callable(title_val) else title_val
                    except Exception:
                        title = getattr(d, 'GetTitle')
                        if callable(title):
                            title = title()

                    if not title:
                        title = path
                    if not title:
                        continue

                    # Skip documents that were already open before EXPORT execution
                    if _is_already_open(path, title):
                        continue

                    try:
                        dtype = d.GetType()
                    except Exception:
                        dtype = getattr(d, 'GetType')
                        if callable(dtype):
                            dtype = dtype()

                    path_lower = (path or "").lower()
                    title_lower = (title or "").lower()

                    is_parent = (dtype in (2, 3) or
                                 path_lower.endswith(".sldasm") or
                                 path_lower.endswith(".slddrw") or
                                 title_lower.endswith(".sldasm") or
                                 title_lower.endswith(".slddrw"))

                    ids = []
                    if path:
                        ids.append(path)
                        ids.append(os.path.normpath(path))
                        base = os.path.basename(path)
                        if base:
                            ids.append(base)
                            base_no_ext = os.path.splitext(base)[0]
                            if base_no_ext:
                                ids.append(base_no_ext)
                    if title:
                        ids.append(title)
                        title_no_ext = os.path.splitext(title)[0]
                        if title_no_ext:
                            ids.append(title_no_ext)

                    uniq_ids = []
                    for iid in ids:
                        if iid and isinstance(iid, str):
                            iid_s = iid.strip()
                            if iid_s and iid_s not in uniq_ids:
                                uniq_ids.append(iid_s)

                    doc_entry = {'title': title, 'path': path, 'uniq_ids': uniq_ids}
                    if is_parent:
                        parent_files.append(doc_entry)
                    else:
                        child_files.append(doc_entry)
                except Exception as doc_err:
                    print(f"Error inspecting document: {doc_err}", flush=True)

            docs_left = None
            d = None
            import gc
            gc.collect()
            try:
                pythoncom.CoCollectFreeUnusedLibraries()
            except:
                pass

            if not parent_files and not child_files:
                break

            print(f"  Closing {len(parent_files)} parent(s) + {len(child_files)} child(ren)...", flush=True)

            closed_any = False
            for doc_entry in parent_files:
                title = doc_entry['title']
                path = doc_entry['path']
                uniq_ids = doc_entry['uniq_ids']
                print(f"Closing parent: '{title}' (path: '{path}')", flush=True)
                closed_this = False
                for identifier in uniq_ids:
                    try:
                        swApp.CloseDoc(identifier)
                        closed_this = True
                        closed_any = True
                        print(f"  Closed via CloseDoc('{identifier}')", flush=True)
                        break
                    except:
                        pass
                    try:
                        swApp.QuitDoc(identifier)
                        closed_this = True
                        closed_any = True
                        print(f"  Closed via QuitDoc('{identifier}')", flush=True)
                        break
                    except:
                        pass
                if not closed_this:
                    print(f"  ⚠️ Failed to close parent: {title} (path: {path})", flush=True)

            if closed_any:
                time.sleep(0.15)

            for doc_entry in child_files:
                title = doc_entry['title']
                path = doc_entry['path']
                uniq_ids = doc_entry['uniq_ids']
                print(f"Closing child: '{title}' (path: '{path}')", flush=True)
                closed_this = False
                for identifier in uniq_ids:
                    try:
                        swApp.CloseDoc(identifier)
                        closed_this = True
                        closed_any = True
                        print(f"  Closed via CloseDoc('{identifier}')", flush=True)
                        break
                    except:
                        pass
                    try:
                        swApp.QuitDoc(identifier)
                        closed_this = True
                        closed_any = True
                        print(f"  Closed via QuitDoc('{identifier}')", flush=True)
                        break
                    except:
                        pass
                if not closed_this:
                    print(f"  ⚠️ Failed to close child: {title} (path: {path})", flush=True)

            time.sleep(0.15)
            gc.collect()
            try:
                pythoncom.CoCollectFreeUnusedLibraries()
            except:
                pass
            iteration += 1

        # ----- Final verification pass -----
        for verify_iter in range(3):
            docs_remaining = _get_open_docs()
            if not docs_remaining:
                print("close_all_documents_without_saving: cleanup verified - all newly opened documents are closed.", flush=True)
                break

            still_open = []
            for d in docs_remaining:
                try:
                    p_val = d.GetPathName
                    path = p_val() if callable(p_val) else p_val
                    if not path:
                        tv = d.GetTitle
                        path = tv() if callable(tv) else tv
                    tv2 = d.GetTitle
                    title = tv2() if callable(tv2) else tv2
                    if not _is_already_open(path, title):
                        still_open.append({'title': title, 'path': path})
                except:
                    pass

            if not still_open:
                print(f"close_all_documents_without_saving: {len(docs_remaining)} pre-existing document(s) remain open (expected).", flush=True)
                break

            for entry in still_open:
                print(f"⚠️ document failed to close and is still open: '{entry['title']}' (path: {entry['path']})", flush=True)
            print(f"Verification retry {verify_iter + 1}/3: {len(still_open)} document(s) still open. Retrying close...", flush=True)

            parents = [e for e in still_open if (e['path'] or "").lower().endswith((".sldasm", ".slddrw")) or (e['title'] or "").lower().endswith((".sldasm", ".slddrw"))]
            children = [e for e in still_open if e not in parents]

            def _try_close(entry):
                ids = []
                p = entry['path']
                t = entry['title']
                if p:
                    ids.append(p)
                    b = os.path.basename(p)
                    if b:
                        ids.append(b)
                        ids.append(os.path.splitext(b)[0])
                if t:
                    ids.append(t)
                    ids.append(os.path.splitext(t)[0])
                seen_ids = []
                for i in ids:
                    if i and isinstance(i, str) and i.strip() and i.strip() not in seen_ids:
                        seen_ids.append(i.strip())
                closed = False
                for identifier in seen_ids:
                    try:
                        swApp.CloseDoc(identifier)
                        closed = True
                        break
                    except:
                        pass
                    try:
                        swApp.QuitDoc(identifier)
                        closed = True
                        break
                    except:
                        pass
                return closed

            closed_any = False
            for e in parents:
                if _try_close(e):
                    closed_any = True
            if closed_any:
                time.sleep(0.15)
            closed_any2 = False
            for e in children:
                if _try_close(e):
                    closed_any2 = True
            if closed_any or closed_any2:
                time.sleep(0.15)

        # Final re-verification
        docs_final = _get_open_docs()
        if docs_final:
            leftover = []
            for d in docs_final:
                try:
                    p_val = d.GetPathName
                    p = p_val() if callable(p_val) else p_val
                    if not p:
                        tv = d.GetTitle
                        p = tv() if callable(tv) else tv
                    tv2 = d.GetTitle
                    t = tv2() if callable(tv2) else tv2
                    if not _is_already_open(p, t):
                        leftover.append((t, p))
                except:
                    pass
            if leftover:
                print("Error: close_all_documents_without_saving FAILED - the following documents could not be closed:", flush=True)
                for t, p in leftover:
                    print(f"Error:   - '{t}' (path: {p})", flush=True)
            else:
                print(f"close_all_documents_without_saving: {len(docs_final)} pre-existing document(s) remain open (expected).", flush=True)
        else:
            print("close_all_documents_without_saving: cleanup OK - no documents remain open.", flush=True)

    except Exception as e:
        print(f"Error in close_all_documents_without_saving cleanup: {e}", flush=True)
    finally:
        try:
            swApp.UserControl = orig_user_control
        except Exception:
            pass
    print("close_all_documents_without_saving: end", flush=True)

def run_single_export(file_abs, target_formats, output_dir, workspace_path, every_configurations=True):
    if not WIN32_AVAILABLE:
        print("Error: PyWin32 is not installed or not running on Windows.")
        sys.exit(1)
        
    pythoncom.CoInitialize()
    swApp, was_running = start_or_bind_solidworks()
    if swApp is None:
        print("Failed to bind to active SolidWorks instance for single export.")
        sys.exit(1)
        
    # --- Key fix: do not populate already_open_paths from GetDocuments() ---
    # Same principle as BOM: the EXPORT subprocess is invoked in batch mode,
    # and files left open by previous subprocesses may remain in GetDocuments().
    # Adding them to already_open_paths would prevent closing them, so use an empty set.
    # At close time, all currently open documents are closed; since already_open_paths is empty, all become close targets.
    already_open_paths = set()
        
    model = None
    try:
                        
        # Ensure preference settings are set to suppress any popup warnings/dialogs
        try:
            force_visible(swApp)
                
            swApp.SetUserPreferenceToggle(11, False)  # swDxfIssuingWarning
            swApp.SetUserPreferenceToggle(143, False) # swDxfMappingFileEnabled
            swApp.SetUserPreferenceToggle(15, True)   # swExtRefNoPromptOrSave (Don't prompt to save read-only referenced docs)
            swApp.SetUserPreferenceToggle(119, False) # swShowErrorsEveryRebuild (Suppress rebuild errors dialog)
            swApp.SetUserPreferenceIntegerValue(246, 1) # swRebuildErrorAction -> swStopContinuePrompt_Continue (Always continue on rebuild errors)
            swApp.SetUserPreferenceToggle(249, False) # swWarnSaveUpdateErrors (Suppress save warnings on rebuild errors)
            swApp.SetUserPreferenceToggle(46, False)  # swAutoSaveEnable (Disable Auto-Save/Recovery to prevent recovery prompts)
            swApp.SetUserPreferenceIntegerValue(242, 1) # swLoadExternalReferences -> swLoadExternalReferences_All (Load all references silently)
            swApp.SetUserPreferenceIntegerValue(243, 1) # swAssemblyLoadLightweightResolve -> Always (Resolve lightweight components silently)
            swApp.SetUserPreferenceIntegerValue(245, 1) # swLargeAssemblyModeResolveLightweight -> Always (Resolve in large assembly mode silently)
            swApp.SetUserPreferenceToggle(389, False) # swShowStartupScreen -> False (Bypass Welcome screen on startup)
        except Exception as pref_e:
            print(f"Failed to set user preferences in single export: {pref_e}")
                
        f_lower = file_abs.lower()
        # Common flags:
        #   1   = swOpenDocOptions_Silent
        #   2   = swOpenDocOptions_ReadOnly
        #   32  = swOpenDocOptions_LoadModel        (drawing/assembly: load referenced model/components)
        #   64  = swOpenDocOptions_IgnoreActivationAndSuppression
        #         (suppress parent assembly auto-activation/loading - applied to sldprt/slddrw/sldasm to
        #          block the parent assembly from being opened alongside)
        #   128 = swOpenDocOptions_AutoMissingComponentResolve
        if f_lower.endswith(".slddrw"):
            doc_type = 3  # swDocDRAWING
            # Drawing: referenced model loads normally via LoadModel(32); only parent assembly auto-loading is blocked via 64.
            open_options = 1 | 32 | 64 | 2 | 128
        elif f_lower.endswith(".sldprt"):
            doc_type = 1  # swDocPART
            # 1 = swOpenDocOptions_Silent, 64 = IgnoreActivation, 2 = ReadOnly, 128 = AutoMissingComponentResolve
            open_options = 1 | 64 | 2 | 128
        elif f_lower.endswith(".sldasm"):
            doc_type = 2  # swDocASSEMBLY
            # Assembly: components load normally via LoadModel(32); only parent assembly auto-loading is blocked via 64.
            open_options = 1 | 32 | 64 | 2 | 128
        else:
            print(f"Unsupported file format: {file_abs}")
            sys.exit(1)

        # For .sldprt: suppress parent assembly auto-loading during OpenDoc6.
        # Assembly and drawing files still load references normally.
        orig_load_ext_for_open = None
        if f_lower.endswith(".sldprt"):
            try:
                orig_load_ext_for_open = swApp.GetUserPreferenceIntegerValue(242)
                swApp.SetUserPreferenceIntegerValue(242, 0)  # swLoadExternalReferences_None
            except Exception:
                pass

        print(f"Opening: {file_abs}")
        error = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        warning = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)

        # --- Key fix: do not update already_open_paths from GetDocuments() ---
        # Same principle as BOM: files that a previous subprocess failed to close may remain in GetDocuments(),
        # and adding them to already_open_paths would prevent closing them. Use an empty set for already_open_paths.
        # At close time, all currently open documents are closed.

        model = swApp.OpenDoc6(file_abs, doc_type, open_options, "", error, warning)

        # Restore swLoadExternalReferences immediately after opening
        if orig_load_ext_for_open is not None:
            try:
                swApp.SetUserPreferenceIntegerValue(242, orig_load_ext_for_open)
            except Exception:
                pass

        if model is None:
            print(f"Failed to open {file_abs}. Error: {error.value}")
            sys.exit(1)

            
        act_error = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        swApp.ActivateDoc3(os.path.basename(file_abs), False, 0, act_error)
        time.sleep(1) # Delay for large document stabilization
        
        if f_lower.endswith(".sldasm"):
            # Unified sldasm processing (STEP_ASM)
            do_step_asm = "STEP_ASM" in target_formats
            
            if do_step_asm:
                configs_to_process = [None]
                active_cfg_name = None
                try:
                    cfg_mgr = getattr(model, 'ConfigurationManager', None)
                    if cfg_mgr:
                        active_cfg = getattr(cfg_mgr, 'ActiveConfiguration', None)
                        if active_cfg:
                            active_cfg_name = active_cfg.Name
                except:
                    pass
                if not active_cfg_name:
                    try:
                        act_cfg_val = getattr(model, 'GetActiveConfiguration', None)
                        active_cfg = act_cfg_val() if callable(act_cfg_val) else act_cfg_val
                        if active_cfg:
                            active_cfg_name = active_cfg.Name
                    except:
                        pass
                        
                if every_configurations:
                    try:
                        conf_val = model.GetConfigurationNames
                        if callable(conf_val):
                            config_names = conf_val()
                        else:
                            config_names = conf_val
                        if config_names and len(config_names) >= 2:
                            configs_to_process = config_names
                        else:
                            configs_to_process = [active_cfg_name] if active_cfg_name else [None]
                    except Exception as conf_err:
                        print(f"Failed to get configuration names for sldasm: {conf_err}")
                        configs_to_process = [active_cfg_name] if active_cfg_name else [None]
                else:
                    configs_to_process = [active_cfg_name] if active_cfg_name else [None]
                
                orig_step_ap = None
                orig_step_appearances = None
                try:
                    orig_step_ap = swApp.GetUserPreferenceIntegerValue(75)
                    orig_step_appearances = swApp.GetUserPreferenceToggle(787)
                    
                    swApp.SetUserPreferenceIntegerValue(75, 214)                          # AP214
                    swApp.SetUserPreferenceToggle(787, True)                              # Export Appearances
                except Exception as pref_e:
                    print(f"Failed to set STEP_ASM preferences: {pref_e}")
                
                file_dir = os.path.dirname(file_abs)
                base_filename = os.path.splitext(os.path.basename(file_abs))[0]
                
                for config_name in configs_to_process:
                    if config_name:
                        try:
                            print(f"Switching to configuration: {config_name}", flush=True)
                            success = model.ShowConfiguration2(config_name)
                            if not success:
                                print(f"Warning: ShowConfiguration2 returned False for configuration: {config_name}", flush=True)
                            time.sleep(1) # Delay for large configuration switching rebuild
                        except Exception as show_conf_err:
                            print(f"Failed to show configuration {config_name}: {show_conf_err}", flush=True)
                    
                    dest_dir_step = os.path.join(file_dir, output_dir, "STEP_ASM")
                    os.makedirs(dest_dir_step, exist_ok=True)
                    if config_name:
                        dest_file_path = os.path.join(dest_dir_step, f"{base_filename}__{config_name}.step")
                    else:
                        dest_file_path = os.path.join(dest_dir_step, f"{base_filename}.step")
                        
                    if os.path.exists(dest_file_path):
                        try:
                            os.remove(dest_file_path)
                        except Exception as del_e:
                            print(f"Failed to remove existing file {dest_file_path}: {del_e}")
                            
                    # Save with Silent option (9 = Silent | AvoidDialogueOnSave) and current version (0)
                    result = model.SaveAs3(dest_file_path, 0, 9)
                    time.sleep(1) # Crucial delay to allow disk write to finalize and release lock
                    if result == 0:
                        print(f"Successfully exported STEP_ASM {file_abs} -> {dest_file_path}")
                    else:
                        print(f"Failed to save STEP_ASM {dest_file_path} (SaveAs3 code: {result})")
                            
                # Restore STEP_ASM settings
                try:
                    if orig_step_ap is not None:
                        swApp.SetUserPreferenceIntegerValue(75, orig_step_ap)
                    if orig_step_appearances is not None:
                        swApp.SetUserPreferenceToggle(787, orig_step_appearances)
                except Exception as restore_e:
                    print(f"Failed to restore STEP_ASM preferences: {restore_e}")
        else:
            # Drawing and Part export logic
            for active_fmt in target_formats:
                orig_pdf_color = None
                orig_pdf_line_weights = None
                orig_pdf_high_quality = None
                orig_step_ap = None
                orig_step_appearances = None
                
                # Prepare paths & target preferences
                if active_fmt == "PDF":
                    format_subdir = "PDF"
                    target_ext = ".pdf"
                    try:
                        orig_pdf_color = swApp.GetUserPreferenceToggle(323)
                        orig_pdf_line_weights = swApp.GetUserPreferenceToggle(327)
                        orig_pdf_high_quality = swApp.GetUserPreferenceToggle(325)
                        
                        swApp.SetUserPreferenceToggle(323, False)                             # Black and White
                        swApp.SetUserPreferenceToggle(327, True)                              # Use printer line weights
                        swApp.SetUserPreferenceToggle(325, True)                              # High quality lines
                    except Exception as pref_e:
                        print(f"Failed to set PDF preferences: {pref_e}")
                        
                elif active_fmt == "DXF":
                    format_subdir = "DXF"
                    target_ext = ".dxf"
                    
                elif active_fmt == "STEP":
                    format_subdir = "STEP"
                    target_ext = ".step"
                    try:
                        orig_step_ap = swApp.GetUserPreferenceIntegerValue(75)
                        orig_step_appearances = swApp.GetUserPreferenceToggle(787)
                        
                        swApp.SetUserPreferenceIntegerValue(75, 214)                          # AP214
                        swApp.SetUserPreferenceToggle(787, True)                              # Export Appearances
                    except Exception as pref_e:
                        print(f"Failed to set STEP preferences: {pref_e}")

                # Determine output directory
                file_dir = os.path.dirname(file_abs)
                dest_dir = os.path.join(file_dir, output_dir, format_subdir)
                os.makedirs(dest_dir, exist_ok=True)
                
                base_filename = os.path.splitext(os.path.basename(file_abs))[0]
                
                # Determine configurations list to iterate
                configs_to_process = [None]
                if active_fmt == "STEP":
                    active_cfg_name = None
                    try:
                        cfg_mgr = getattr(model, 'ConfigurationManager', None)
                        if cfg_mgr:
                            active_cfg = getattr(cfg_mgr, 'ActiveConfiguration', None)
                            if active_cfg:
                                active_cfg_name = active_cfg.Name
                    except:
                        pass
                    if not active_cfg_name:
                        try:
                            act_cfg_val = getattr(model, 'GetActiveConfiguration', None)
                            active_cfg = act_cfg_val() if callable(act_cfg_val) else act_cfg_val
                            if active_cfg:
                                active_cfg_name = active_cfg.Name
                        except:
                            pass
                            
                    if every_configurations:
                        try:
                            conf_val = model.GetConfigurationNames
                            if callable(conf_val):
                                config_names = conf_val()
                            else:
                                config_names = conf_val
                            if config_names and len(config_names) >= 2:
                                configs_to_process = config_names
                            else:
                                configs_to_process = [active_cfg_name] if active_cfg_name else [None]
                        except Exception as conf_err:
                            print(f"Failed to get configuration names: {conf_err}")
                            configs_to_process = [active_cfg_name] if active_cfg_name else [None]
                    else:
                        configs_to_process = [active_cfg_name] if active_cfg_name else [None]
                        
                for config_name in configs_to_process:
                    if config_name:
                        try:
                            print(f"Switching to configuration: {config_name} for STEP export", flush=True)
                            success = model.ShowConfiguration2(config_name)
                            if not success:
                                print(f"Warning: ShowConfiguration2 returned False for configuration: {config_name}", flush=True)
                            time.sleep(1) # Delay for configuration switching rebuild
                            dest_file_path = os.path.join(dest_dir, f"{base_filename}__{config_name}{target_ext}")
                        except Exception as show_conf_err:
                            print(f"Failed to show configuration {config_name}: {show_conf_err}", flush=True)
                            dest_file_path = os.path.join(dest_dir, base_filename + target_ext)
                    else:
                        dest_file_path = os.path.join(dest_dir, base_filename + target_ext)
                        
                    # Remove target file if exists
                    if os.path.exists(dest_file_path):
                        try:
                            os.remove(dest_file_path)
                        except Exception as del_e:
                            print(f"Failed to remove existing file {dest_file_path}: {del_e}")
                            
                    # Save with Silent option (9 = Silent | AvoidDialogueOnSave) and current version (0)
                    result = model.SaveAs3(dest_file_path, 0, 9)
                    time.sleep(1) # Crucial delay to allow disk write to finalize and release lock
                    if result == 0:
                        print(f"Successfully exported {file_abs} -> {dest_file_path}")
                    else:
                        print(f"Failed to save {dest_file_path} (SaveAs3 code: {result})")
                    
                # Restore settings
                if active_fmt == "PDF":
                    try:
                        if orig_pdf_color is not None:
                            swApp.SetUserPreferenceToggle(323, orig_pdf_color)
                        if orig_pdf_line_weights is not None:
                            swApp.SetUserPreferenceToggle(327, orig_pdf_line_weights)
                        if orig_pdf_high_quality is not None:
                            swApp.SetUserPreferenceToggle(325, orig_pdf_high_quality)
                    except Exception as restore_e:
                        print(f"Failed to restore PDF preferences: {restore_e}")
                elif active_fmt == "STEP":
                    try:
                        if orig_step_ap is not None:
                            swApp.SetUserPreferenceIntegerValue(75, orig_step_ap)
                        if orig_step_appearances is not None:
                            swApp.SetUserPreferenceToggle(787, orig_step_appearances)
                    except Exception as restore_e:
                        print(f"Failed to restore STEP preferences: {restore_e}")

        # Close the specific main model document first to release active reference links.
        if model:
            try:
                time.sleep(0.1)
                
                # Try to get the document title
                title_to_close = None
                try:
                    title_val = model.GetTitle
                    title_to_close = title_val() if callable(title_val) else title_val
                except Exception:
                    title_to_close = getattr(model, 'GetTitle')
                    if callable(title_to_close):
                        title_to_close = title_to_close()
                        
                if not title_to_close:
                    title_to_close = os.path.basename(file_abs)
                
                # Release model COM reference and run garbage collection
                model = None
                title_val = None
                import gc
                gc.collect()
                try:
                    pythoncom.CoCollectFreeUnusedLibraries()
                except:
                    pass
                    
                norm_file_abs = os.path.normpath(file_abs).lower()
                is_main_newly_opened = (norm_file_abs not in already_open_paths) and (title_to_close.lower() not in already_open_paths)
                
                if is_main_newly_opened:
                    print(f"Closing main document: '{title_to_close}' (path: '{file_abs}') via CloseDoc/QuitDoc", flush=True)
                    orig_uc = True
                    try:
                        orig_uc = swApp.UserControl
                        swApp.UserControl = False
                    except:
                        pass
                    
                    try:
                        main_ids = []
                        if file_abs:
                            main_ids.append(file_abs)
                            main_ids.append(os.path.normpath(file_abs))
                            base = os.path.basename(file_abs)
                            if base:
                                main_ids.append(base)
                                base_no_ext = os.path.splitext(base)[0]
                                if base_no_ext:
                                    main_ids.append(base_no_ext)
                        if title_to_close:
                            main_ids.append(title_to_close)
                            title_no_ext = os.path.splitext(title_to_close)[0]
                            if title_no_ext:
                                main_ids.append(title_no_ext)
                                
                        uniq_main_ids = []
                        for iid in main_ids:
                            if iid and isinstance(iid, str):
                                iid_s = iid.strip()
                                if iid_s and iid_s not in uniq_main_ids:
                                    uniq_main_ids.append(iid_s)
                                    
                        for identifier in uniq_main_ids:
                            try:
                                print(f"  Attempting CloseDoc('{identifier}') for main doc", flush=True)
                                swApp.CloseDoc(identifier)
                            except:
                                pass
                            try:
                                print(f"  Attempting QuitDoc('{identifier}') for main doc", flush=True)
                                swApp.QuitDoc(identifier)
                            except:
                                pass
                    finally:
                        try:
                            swApp.UserControl = orig_uc
                        except:
                            pass
                else:
                    print(f"Main document '{title_to_close}' was already open before export. Keeping it open.", flush=True)
            except Exception as e_close_main:
                print(f"Warning: Failed to close main document '{file_abs}': {e_close_main}", flush=True)

        # Final cleanup for any remaining documents (skeletons, assemblies, etc.)
        close_all_documents_without_saving(swApp, already_open_paths)
        
    except Exception as file_e:
        print(f"Error processing {file_abs}: {repr(file_e)}")
        if swApp:
            if model:
                try:
                    time.sleep(0.1)
                    
                    title_to_close = None
                    try:
                        title_val = model.GetTitle
                        title_to_close = title_val() if callable(title_val) else title_val
                    except Exception:
                        title_to_close = getattr(model, 'GetTitle')
                        if callable(title_to_close):
                            title_to_close = title_to_close()
                            
                    if not title_to_close:
                        title_to_close = os.path.basename(file_abs)
                        
                    # Release model COM reference and run garbage collection
                    model = None
                    title_val = None
                    import gc
                    gc.collect()
                    try:
                        pythoncom.CoCollectFreeUnusedLibraries()
                    except:
                        pass
                        
                    norm_file_abs = os.path.normpath(file_abs).lower()
                    is_main_newly_opened = (norm_file_abs not in already_open_paths) and (title_to_close.lower() not in already_open_paths)
                    
                    if is_main_newly_opened:
                        orig_uc = True
                        try:
                            orig_uc = swApp.UserControl
                            swApp.UserControl = False
                        except:
                            pass
                        try:
                            main_ids = []
                            if file_abs:
                                main_ids.append(file_abs)
                                main_ids.append(os.path.normpath(file_abs))
                                base = os.path.basename(file_abs)
                                if base:
                                    main_ids.append(base)
                                    base_no_ext = os.path.splitext(base)[0]
                                    if base_no_ext:
                                        main_ids.append(base_no_ext)
                            if title_to_close:
                                main_ids.append(title_to_close)
                                title_no_ext = os.path.splitext(title_to_close)[0]
                                if title_no_ext:
                                    main_ids.append(title_no_ext)
                                    
                            uniq_main_ids = []
                            for iid in main_ids:
                                if iid and isinstance(iid, str):
                                    iid_s = iid.strip()
                                    if iid_s and iid_s not in uniq_main_ids:
                                        uniq_main_ids.append(iid_s)
                                        
                            for identifier in uniq_main_ids:
                                try:
                                    swApp.CloseDoc(identifier)
                                except:
                                    pass
                                try:
                                    swApp.QuitDoc(identifier)
                                except:
                                    pass
                        finally:
                            try:
                                swApp.UserControl = orig_uc
                            except:
                                pass
                except:
                    pass
            close_all_documents_without_saving(swApp, already_open_paths)
        sys.exit(1)
    finally:
        pythoncom.CoUninitialize()

def run_export(job_file):
    if not WIN32_AVAILABLE:
        print("Error: PyWin32 is not installed or not running on Windows.", flush=True)
        sys.exit(1)
        
    with open(job_file, "r", encoding="utf-8") as f:
        job = json.load(f)
        
    workspace_path = job.get("workspace_path")
    formats = job.get("formats", [])  # List of active formats e.g. ["PDF", "DXF"]
    prefix = job.get("prefix", "")
    output_dir = job.get("output_dir", "2D")
    all_files = job.get("files", [])
    every_configurations = job.get("every_configurations", True)
    
    # Normalize prefix (handle None or empty string or "*")
    if prefix is None or prefix.strip() == "" or prefix.strip() == "*":
        prefix = ""
    else:
        prefix = prefix.strip()
        
    # Gather matching files and mapping their target formats
    file_jobs = {}
    print(f"Loaded Job Info: prefix='{prefix}', formats={formats}, total_files_in_job={len(all_files)}", flush=True)
    
    for f_rel in all_files:
        base_name = os.path.basename(f_rel)
        if prefix != "" and not base_name.startswith(prefix):
            continue
            
        f_lower = f_rel.lower()
        target_formats = []
        
        if f_lower.endswith(".slddrw"):
            if "PDF" in formats:
                target_formats.append("PDF")
            if "DXF" in formats:
                target_formats.append("DXF")
        elif f_lower.endswith(".sldprt"):
            if "STEP" in formats:
                target_formats.append("STEP")
        elif f_lower.endswith(".sldasm"):
            if "STEP_ASM" in formats:
                target_formats.append("STEP_ASM")
                
        if target_formats:
            # Sort target_formats in the order of: PDF -> DXF -> STEP -> STEP_ASM
            format_order = {"PDF": 0, "DXF": 1, "STEP": 2, "STEP_ASM": 3}
            target_formats.sort(key=lambda fmt: format_order.get(fmt, 9))
            file_jobs[f_rel] = target_formats
            
    if not file_jobs:
        print(f"[ERROR] No matching files to export. Job formats: {formats}, Prefix: '{prefix}', files count: {len(all_files)}", flush=True)
        pythoncom.CoUninitialize()
        sys.exit(1)

    has_errors = False
    was_already_running = False
    # --- Key fix: do not populate already_open_paths from GetDocuments() ---
    # Same principle as BOM: in batch mode, subprocesses are invoked sequentially,
    # and files that a previous subprocess failed to close may remain in GetDocuments().
    # Adding them to already_open_paths would prevent closing them, so use an empty set.
    already_open_paths = set()

    # Initialize COM
    pythoncom.CoInitialize()
    
    # Connect to SolidWorks
    sw_pid = None
    swApp, was_already_running = start_or_bind_solidworks()
    if swApp is None:
        print("[ERROR] Failed to start or bind SolidWorks instance.", flush=True)
        pythoncom.CoUninitialize()
        sys.exit(1)

    # Apply configuration and preferences to the bound swApp instance (always executed)
    try:
        force_visible(swApp)
            
        # Get process ID
        try:
            if hasattr(swApp, "GetProcessID"):
                pid_val = swApp.GetProcessID
                if callable(pid_val):
                    sw_pid = pid_val()
                else:
                    sw_pid = pid_val
            else:
                sw_pid = None
        except Exception as pid_e:
            print(f"Could not retrieve SolidWorks PID: {pid_e}", flush=True)
            sw_pid = None
            
        # Suppress warnings and dialog prompts
        try:
            swApp.SetUserPreferenceToggle(11, False)  # swDxfIssuingWarning
            swApp.SetUserPreferenceToggle(143, False) # swDxfMappingFileEnabled
            swApp.SetUserPreferenceToggle(15, True)   # swExtRefNoPromptOrSave (Don't prompt to save read-only referenced docs)
            swApp.SetUserPreferenceToggle(119, False) # swShowErrorsEveryRebuild (Suppress rebuild errors dialog)
            swApp.SetUserPreferenceIntegerValue(246, 1) # swRebuildErrorAction -> swStopContinuePrompt_Continue (Always continue on rebuild errors)
            swApp.SetUserPreferenceToggle(249, False) # swWarnSaveUpdateErrors (Suppress save warnings on rebuild errors)
            swApp.SetUserPreferenceToggle(46, False)  # swAutoSaveEnable (Disable Auto-Save/Recovery to prevent recovery prompts)
            swApp.SetUserPreferenceToggle(389, False) # swShowStartupScreen -> False (Bypass Welcome screen on startup)
        except Exception as pref_e:
            print(f"Failed to set user preferences: {pref_e}", flush=True)

        # List of file jobs to run, sorted by format order: PDF/DXF (.slddrw) -> STEP (.sldprt) -> STEP_ASM (.sldasm)
        def get_file_priority(f_rel):
            ext = os.path.splitext(f_rel)[1].lower()
            if ext == ".slddrw":
                return 0
            elif ext == ".sldprt":
                return 1
            elif ext == ".sldasm":
                return 2
            return 3

        file_list = list(file_jobs.keys())
        file_list.sort(key=lambda x: (get_file_priority(x), x.lower()))
        total_files = len(file_list)
        
        # processed_count: counts actual CAD files processed (per CAD file, not per STEP/config output)
        # This ensures [PROGRESS] increments by 1 per target CAD file, regardless of how many
        # configurations or STEP files are generated from that CAD file.
        processed_count = 0
        
        for idx, file_rel in enumerate(file_list):
            file_abs = os.path.normpath(os.path.join(workspace_path, file_rel))
            if not os.path.exists(file_abs):
                print(f"Skipping missing file: {file_abs}")
                continue
                
            target_formats = file_jobs[file_rel]
            
            # Increment processed_count: strictly per CAD file, not per STEP output or configuration
            processed_count += 1
            
            # Send real-time progress update via stdout (Completed: processed_count - 1 = before this file)
            # format: [PROGRESS] current_completed/total_files : base_filename
            print(f"[PROGRESS] {processed_count - 1}/{total_files} : {os.path.basename(file_rel)}", flush=True)
            
            import subprocess
            import queue
            import threading
            
            def reader_thread(pipe, out_queue):
                try:
                    for line in iter(pipe.readline, ''):
                        out_queue.put(line)
                except Exception:
                    pass
                finally:
                    pipe.close()

            # Spawn watchdog subprocess
            import os as os_env
            env_vars = os_env.environ.copy()
            env_vars["PYTHONIOENCODING"] = "utf-8"
            env_vars["SW_BATCH_EXPORT"] = "True"
            proc = subprocess.Popen(
                [sys.executable, "-u", __file__, "--single", file_abs, ",".join(target_formats), output_dir, workspace_path, str(every_configurations)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env_vars
            )
            
            out_queue = queue.Queue()
            t_read = threading.Thread(target=reader_thread, args=(proc.stdout, out_queue), daemon=True)
            t_read.start()
            
            start_time = time.time()
            timeout = 180.0 # 180 seconds watchdog silence timeout
            timed_out = False
            
            while True:
                has_output = False
                while True:
                    try:
                        line = out_queue.get_nowait()
                        sys.stdout.write(line)
                        sys.stdout.flush()
                        has_output = True
                    except queue.Empty:
                        break
                
                if has_output:
                    start_time = time.time() # Reset watchdog timer on output
                    
                if proc.poll() is not None:
                    # Drain any remaining logs
                    while not out_queue.empty():
                        try:
                            sys.stdout.write(out_queue.get_nowait())
                            sys.stdout.flush()
                        except queue.Empty:
                            break
                    break
                    
                if time.time() - start_time > timeout:
                    timed_out = True
                    print(f"\n[WARNING] Watchdog Timeout: File conversion exceeded 180 seconds limit ({file_rel}). Force terminating process...", flush=True)
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    break
                    
                time.sleep(0.05)
                    
            if timed_out:
                has_errors = True
                # Forcefully terminate SolidWorks by PID and image name to prevent DispatchEx locks on recovery
                if sw_pid:
                    try:
                        print(f"Forcefully terminating hung SolidWorks PID {sw_pid} due to timeout...", flush=True)
                        subprocess.run(f"taskkill /F /PID {sw_pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except:
                        pass
                try:
                    print("Forcefully terminating all remaining SLDWORKS.exe and sldworks_fs.exe processes to ensure clean recovery...", flush=True)
                    subprocess.run("taskkill /F /IM SLDWORKS.exe", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    subprocess.run("taskkill /F /IM sldworks_fs.exe", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    time.sleep(1.5)
                except Exception as kill_e:
                    print(f"Could not terminate SolidWorks processes: {kill_e}", flush=True)
                
                print("Launching a new SolidWorks instance to resume...", flush=True)
                try:
                    swApp, was_already_running = start_or_bind_solidworks()
                    if swApp:
                        force_visible(swApp)
                        try:
                            if hasattr(swApp, "GetProcessID"):
                                pid_val = swApp.GetProcessID
                                if callable(pid_val):
                                    sw_pid = pid_val()
                                else:
                                    sw_pid = pid_val
                            else:
                                sw_pid = None
                        except Exception as pid_e:
                            print(f"Could not retrieve SolidWorks PID: {pid_e}", flush=True)
                            sw_pid = None
                            
                        # Suppress warnings and prompts on the new instance
                        swApp.SetUserPreferenceToggle(11, False)
                        swApp.SetUserPreferenceToggle(143, False)
                        swApp.SetUserPreferenceToggle(15, True)
                        swApp.SetUserPreferenceToggle(119, False)
                        swApp.SetUserPreferenceIntegerValue(246, 1)
                        swApp.SetUserPreferenceToggle(249, False)
                        swApp.SetUserPreferenceToggle(46, False)
                except Exception as re_e:
                    print(f"Failed to restart SolidWorks instance: {re_e}", flush=True)
                    swApp = None
                    sw_pid = None
                    # Send failure progress update before skipping
                    print(f"[PROGRESS] {processed_count}/{total_files} : {os.path.basename(file_rel)}", flush=True)
                    continue
            elif proc.returncode != 0:
                has_errors = True
                print(f"[ERROR] Subprocess failed for file {file_rel} with exit code {proc.returncode}.", flush=True)

            # Send real-time progress update via stdout after completion (Completed: processed_count)
            # processed_count is incremented per CAD file, not per STEP output file or configuration.
            print(f"[PROGRESS] {processed_count}/{total_files} : {os.path.basename(file_rel)}", flush=True)

            # Stabilizing recovery delay for SolidWorks engine recovery
            if not timed_out:
                ext = os.path.splitext(file_rel)[1].lower()
                if ext == ".sldasm":
                    print("[INFO] Stabilizing SolidWorks engine after Assembly processing...", flush=True)
                    time.sleep(1.5)
                else:
                    time.sleep(0.25)

        # Restore global warning preferences
        try:
            swApp.SetUserPreferenceToggle(11, True)
            swApp.SetUserPreferenceToggle(143, True)
            swApp.SetUserPreferenceToggle(15, False)
        except:
            pass

    finally:
        # Close all open documents and exit SolidWorks if we started it
        if swApp:
            try:
                # --- Key fix: do not update already_open_paths from GetDocuments() ---
                # Same principle as BOM: during batch processing, files that a subprocess failed to close
                # may remain in GetDocuments(); adding them to already_open_paths would prevent closing them.
                # Use an empty set for already_open_paths so that all documents get closed.
                # (If SolidWorks was already running, files opened by the user will also be closed,
                #  but EXPORT is a batch process, so closing all files on completion is safer.)
                if was_already_running:
                    print("Closing all documents opened during export...", flush=True)
                    close_all_documents_without_saving(swApp, already_open_paths)
                else:
                    # If SolidWorks was started fresh: close all documents and exit via ExitApp
                    print("Closing all documents (SolidWorks was launched by export runner)...", flush=True)
                    close_all_documents_without_saving(swApp, already_open_paths=None)
            except Exception as close_err:
                print(f"Error during final documents cleanup: {close_err}")
                
            if not was_already_running:
                def exit_sw_async(app):
                    try:
                        app.ExitApp()
                    except Exception as exit_e:
                        print(f"Error during swApp.ExitApp(): {exit_e}")
                        
                try:
                    t_exit = threading.Thread(target=exit_sw_async, args=(swApp,), daemon=True)
                    t_exit.start()
                    t_exit.join(timeout=5.0) # Wait up to 5.0 seconds for graceful ExitApp
                except Exception as t_err:
                    print(f"Failed to start async ExitApp thread: {t_err}")
            else:
                print("SolidWorks was already running before export. Skipping ExitApp.")
                
        # Clean up process if still alive and we started it
        if not was_already_running:
            try:
                import subprocess
                if sw_pid:
                    subprocess.run(f"taskkill /F /PID {sw_pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run("taskkill /F /IM SLDWORKS.exe", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run("taskkill /F /IM sldworks_fs.exe", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print("Forcefully terminated all remaining SolidWorks processes to ensure clean exit.")
            except Exception as kill_e:
                print(f"Could not clean up SolidWorks processes: {kill_e}")
        else:
            print("SolidWorks was already running. Skipping process termination.")
                
        pythoncom.CoUninitialize()
        
        # Clean up job file
        try:
            if os.path.exists(job_file):
                os.remove(job_file)
                print(f"Successfully cleaned up temporary job file: {job_file}")
        except Exception as cleanup_e:
            print(f"Failed to remove job file {job_file}: {cleanup_e}")
            
        if has_errors:
            print("[ERROR] Export finished with errors. One or more files failed to convert.", flush=True)
            sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--single":
        if len(sys.argv) < 6:
            print("Error: Missing arguments for --single mode")
            sys.exit(1)
        file_abs = sys.argv[2]
        formats_str = sys.argv[3]
        output_dir = sys.argv[4]
        workspace_path = sys.argv[5]
        target_formats = formats_str.split(",")
        if target_formats == [""]:
            target_formats = []
        every_cfg_str = sys.argv[6] if len(sys.argv) > 6 else "True"
        every_configurations = (every_cfg_str.lower() == "true")
        run_single_export(file_abs, target_formats, output_dir, workspace_path, every_configurations)
    else:
        if len(sys.argv) < 2:
            print("Usage: python sw_export_runner.py <job_file_path>")
            sys.argv = [sys.argv[0], "export_job.json"]
        run_export(sys.argv[1])
