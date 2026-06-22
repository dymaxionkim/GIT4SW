import os
import sys
import json
import time

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
        return win32com.client.Dispatch(raw_obj)
    except Exception as e:
        print(f"Warning: Failed to ensure early-binding dispatch wrapper: {e}", flush=True)
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
    
    # 1. Try to connect to an existing active instance first
    print("Attempting to connect to active SolidWorks instance via GetActiveObject...", flush=True)
    try:
        raw_obj = win32com.client.GetActiveObject("SldWorks.Application")
        swApp = get_dynamic_sw_app(raw_obj)
        print("Successfully bound to active SolidWorks instance.", flush=True)
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
                        time.sleep(1.0)
            except Exception as e_launch:
                print(f"Failed to launch SolidWorks directly: {e_launch}.", flush=True)
                
    # 3. Fallback to GetObject (dynamic representation)
    if swApp is None:
        print("Attempting fallback connection via GetObject...", flush=True)
        try:
            raw_obj = win32com.client.GetObject(Class="SldWorks.Application")
            swApp = get_dynamic_sw_app(raw_obj)
            print("Successfully bound to SolidWorks instance via GetObject.", flush=True)
        except Exception as e_get:
            print(f"Failed to start or bind SolidWorks instance: {e_get}", flush=True)
            
    return swApp

def close_all_documents_without_saving(swApp):
    print("close_all_documents_without_saving: start", flush=True)
    if not swApp:
        print("close_all_documents_without_saving: swApp is None", flush=True)
        return
    try:
        # Safe cleanup loop for remaining dependent/referenced documents.
        # Prioritizes closing parent documents (assemblies, drawings) first to release references on parts.
        time.sleep(0.2)
        iteration = 0
        last_doc_count = -1
        stuck_count = 0
        
        while iteration < 50:
            try:
                try:
                    docs_left = swApp.GetDocuments()
                except Exception:
                    val = getattr(swApp, 'GetDocuments')
                    docs_left = val() if callable(val) else val
            except Exception:
                break
                
            if not docs_left:
                break
                
            current_count = len(docs_left)
            if current_count == last_doc_count:
                stuck_count += 1
                if stuck_count > 3:
                    print(f"close_all_documents_without_saving: detected stuck count ({current_count} docs), escaping to avoid deadlock loop", flush=True)
                    break
            else:
                stuck_count = 0
            last_doc_count = current_count
            
            parent_docs = []  # assemblies and drawings
            child_docs = []   # parts
            
            for d in docs_left:
                try:
                    try:
                        title_val = d.GetTitle
                        title = title_val() if callable(title_val) else title_val
                    except Exception:
                        title = getattr(d, 'GetTitle')
                        
                    try:
                        dtype = d.GetType()
                    except Exception:
                        dtype = getattr(d, 'GetType')
                        if callable(dtype):
                            dtype = dtype()
                            
                    if not title:
                        continue
                        
                    title_lower = title.lower()
                    # dtype: 2 = swDocASSEMBLY, 3 = swDocDRAWING
                    if dtype in (2, 3) or title_lower.endswith(".sldasm") or title_lower.endswith(".slddrw"):
                        parent_docs.append((d, title))
                    else:
                        child_docs.append((d, title))
                except Exception:
                    pass
            
            closed_any = False
            # Close parents first to break references
            for d, title in parent_docs:
                try:
                    print(f"close_all_documents_without_saving: closing parent '{title}' via QuitDoc", flush=True)
                    swApp.QuitDoc(title)
                    base_title, _ = os.path.splitext(title)
                    if base_title != title:
                        swApp.QuitDoc(base_title)
                    closed_any = True
                except Exception as e:
                    print(f"Error closing parent {title}: {e}", flush=True)
                    
            if closed_any:
                time.sleep(0.1)
                
            # Close children second
            for d, title in child_docs:
                try:
                    print(f"close_all_documents_without_saving: closing child '{title}' via QuitDoc", flush=True)
                    swApp.QuitDoc(title)
                    base_title, _ = os.path.splitext(title)
                    if base_title != title:
                        swApp.QuitDoc(base_title)
                except Exception as e:
                    print(f"Error closing child {title}: {e}", flush=True)
                    
            time.sleep(0.1)
            iteration += 1
            
    except Exception as e:
        print(f"Error in close_all_documents_without_saving cleanup: {e}", flush=True)
    print("close_all_documents_without_saving: end", flush=True)

def run_single_export(file_abs, target_formats, output_dir, workspace_path):
    if not WIN32_AVAILABLE:
        print("Error: PyWin32 is not installed or not running on Windows.")
        sys.exit(1)
        
    pythoncom.CoInitialize()
    swApp = start_or_bind_solidworks()
    if swApp is None:
        print("Failed to bind to active SolidWorks instance for single export.")
        sys.exit(1)
        
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
        if f_lower.endswith(".slddrw"):
            doc_type = 3  # swDocDRAWING
            # 1 = swOpenDocOptions_Silent, 32 = swOpenDocOptions_LoadModel, 2 = swOpenDocOptions_ReadOnly, 128 = swOpenDocOptions_AutoMissingComponentResolve
            open_options = 1 | 32 | 2 | 128
        elif f_lower.endswith(".sldprt"):
            doc_type = 1  # swDocPART
            # Flag 64 = swOpenDocOptions_IgnoreActivationAndSuppression: prevents parent assembly auto-loading
            # 1 = swOpenDocOptions_Silent, 64 = IgnoreActivation, 2 = ReadOnly, 128 = AutoMissingComponentResolve
            open_options = 1 | 64 | 2 | 128
        elif f_lower.endswith(".sldasm"):
            doc_type = 2  # swDocASSEMBLY
            # 1 = swOpenDocOptions_Silent, 32 = swOpenDocOptions_LoadModel, 2 = swOpenDocOptions_ReadOnly, 128 = swOpenDocOptions_AutoMissingComponentResolve
            open_options = 1 | 32 | 2 | 128
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
        time.sleep(2) # Delay for large document stabilization
        
        if f_lower.endswith(".sldasm"):
            # Unified sldasm processing (STEP_ASM)
            do_step_asm = "STEP_ASM" in target_formats
            
            if do_step_asm:
                configs_to_process = [None]
                try:
                    conf_val = model.GetConfigurationNames
                    if callable(conf_val):
                        config_names = conf_val()
                    else:
                        config_names = conf_val
                    if config_names and len(config_names) >= 2:
                        configs_to_process = config_names
                except Exception as conf_err:
                    print(f"Failed to get configuration names for sldasm: {conf_err}")
                
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
                            print(f"Switching to configuration: {config_name}")
                            model.ShowConfiguration2(config_name)
                            time.sleep(2) # Delay for large configuration switching rebuild
                        except Exception as show_conf_err:
                            print(f"Failed to show configuration {config_name}: {show_conf_err}")
                    
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
                    time.sleep(2) # Crucial delay to allow disk write to finalize and release lock
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
                    try:
                        conf_val = model.GetConfigurationNames
                        if callable(conf_val):
                            config_names = conf_val()
                        else:
                            config_names = conf_val
                        if config_names and len(config_names) >= 2:
                            configs_to_process = config_names
                    except Exception as conf_err:
                        print(f"Failed to get configuration names: {conf_err}")
                        
                for config_name in configs_to_process:
                    if config_name:
                        try:
                            print(f"Switching to configuration: {config_name} for STEP export")
                            model.ShowConfiguration2(config_name)
                            time.sleep(2) # Delay for configuration switching rebuild
                            dest_file_path = os.path.join(dest_dir, f"{base_filename}__{config_name}{target_ext}")
                        except Exception as show_conf_err:
                            print(f"Failed to show configuration {config_name}: {show_conf_err}")
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
                    time.sleep(2) # Crucial delay to allow disk write to finalize and release lock
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
                time.sleep(0.2)
                title_val = model.GetTitle
                title = title_val() if callable(title_val) else title_val
                if title:
                    print(f"Closing main document: '{title}' via QuitDoc", flush=True)
                    swApp.QuitDoc(title)
                    base_title, _ = os.path.splitext(title)
                    if base_title != title:
                        swApp.QuitDoc(base_title)
            except Exception as e_close_main:
                print(f"Warning: Failed to close main document '{file_abs}': {e_close_main}", flush=True)

        # Final cleanup for any remaining documents (skeletons, assemblies, etc.)
        close_all_documents_without_saving(swApp)
        
    except Exception as file_e:
        print(f"Error processing {file_abs}: {repr(file_e)}")
        if swApp:
            if model:
                try:
                    time.sleep(0.2)
                    title_val = model.GetTitle
                    title = title_val() if callable(title_val) else title_val
                    if title:
                        swApp.QuitDoc(title)
                        base_title, _ = os.path.splitext(title)
                        if base_title != title:
                            swApp.QuitDoc(base_title)
                except:
                    pass
            close_all_documents_without_saving(swApp)
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

    # Initialize COM
    pythoncom.CoInitialize()
    
    # Connect to SolidWorks
    sw_pid = None
    swApp = start_or_bind_solidworks()
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
                [sys.executable, "-u", __file__, "--single", file_abs, ",".join(target_formats), output_dir, workspace_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                env=env_vars
            )
            
            out_queue = queue.Queue()
            t_read = threading.Thread(target=reader_thread, args=(proc.stdout, out_queue), daemon=True)
            t_read.start()
            
            start_time = time.time()
            timeout = 180.0 # 180 seconds watchdog
            timed_out = False
            
            while True:
                try:
                    line = out_queue.get_nowait()
                    sys.stdout.write(line)
                    sys.stdout.flush()
                except queue.Empty:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.1)
                    
                if time.time() - start_time > timeout:
                    timed_out = True
                    print(f"\n[WARNING] Watchdog Timeout: File conversion exceeded 180 seconds limit ({file_rel}). Force terminating process...", flush=True)
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    break
                    
            # Flush remaining logs
            while not out_queue.empty():
                try:
                    sys.stdout.write(out_queue.get_nowait())
                    sys.stdout.flush()
                except queue.Empty:
                    break
                    
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
                    time.sleep(3)
                except Exception as kill_e:
                    print(f"Could not terminate SolidWorks processes: {kill_e}", flush=True)
                
                print("Launching a new SolidWorks instance to resume...", flush=True)
                try:
                    swApp = start_or_bind_solidworks()
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
                    # NOTE: Use 'continue' (not 'break') so the loop proceeds to remaining files.
                    # Child subprocesses connect to SolidWorks independently and do not need
                    # the parent's swApp reference. Even if the parent's restart fails, subsequent
                    # child subprocesses can still attempt to connect or start their own SW instance.
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
                    time.sleep(3.0)
                else:
                    time.sleep(0.5)

        # Restore global warning preferences
        try:
            swApp.SetUserPreferenceToggle(11, True)
            swApp.SetUserPreferenceToggle(143, True)
            swApp.SetUserPreferenceToggle(15, False)
        except:
            pass

    finally:
        # Exit SolidWorks
        if swApp:
            try:
                swApp.ExitApp()
            except Exception as exit_e:
                print(f"Error during swApp.ExitApp(): {exit_e}")
                
        # Clean up process if still alive
        try:
            import subprocess
            if sw_pid:
                subprocess.run(f"taskkill /F /PID {sw_pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run("taskkill /F /IM SLDWORKS.exe", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run("taskkill /F /IM sldworks_fs.exe", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("Forcefully terminated all remaining SolidWorks processes to ensure clean exit.")
        except Exception as kill_e:
            print(f"Could not clean up SolidWorks processes: {kill_e}")
                
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
        run_single_export(file_abs, target_formats, output_dir, workspace_path)
    else:
        if len(sys.argv) < 2:
            print("Usage: python sw_export_runner.py <job_file_path>")
            sys.argv = [sys.argv[0], "export_job.json"]
        run_export(sys.argv[1])
