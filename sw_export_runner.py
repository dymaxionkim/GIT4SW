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

def close_all_documents_without_saving(swApp):
    # Close all documents including unsaved ones without prompting (discards changes)
    try:
        swApp.CloseAllDocuments(True)
    except Exception as e:
        print(f"Error calling CloseAllDocuments(True): {e}")
        # Fallback to manual close using QuitDoc if CloseAllDocuments fails
        try:
            docs = swApp.GetDocuments
            if docs:
                for doc in docs:
                    try:
                        title = doc.GetTitle()
                        if title:
                            swApp.QuitDoc(title)
                    except:
                        pass
        except Exception as fallback_e:
            print(f"Fallback manual close failed: {fallback_e}")

def run_single_export(file_abs, target_formats, output_dir, workspace_path, export_bom):
    if not WIN32_AVAILABLE:
        print("Error: PyWin32 is not installed or not running on Windows.")
        sys.exit(1)
        
    pythoncom.CoInitialize()
    swApp = None
    try:
        # Try to bind to active SolidWorks with retries
        max_retries = 5
        retry_interval = 1.0
        for attempt in range(1, max_retries + 1):
            try:
                swApp = win32com.client.GetActiveObject("SldWorks.Application")
                break
            except Exception as e_active:
                try:
                    swApp = win32com.client.GetObject(Class="SldWorks.Application")
                    break
                except Exception as e_get:
                    if attempt < max_retries:
                        print(f"[{attempt}/{max_retries}] Waiting for SolidWorks to register in ROT...")
                        time.sleep(retry_interval)
                    else:
                        print(f"Failed to bind to active SolidWorks instance after {max_retries} attempts. Last error: {e_get}")
                        sys.stdout.flush()
                        sys.stderr.flush()
                        sys.exit(1)
                
        f_lower = file_abs.lower()
        if f_lower.endswith(".slddrw"):
            doc_type = 3  # swDocDRAWING
            open_options = 1 | 32  # swOpenDocOptions_Silent | swOpenDocOptions_LoadModel
        elif f_lower.endswith(".sldprt"):
            doc_type = 1  # swDocPART
            open_options = 1  # swOpenDocOptions_Silent
        elif f_lower.endswith(".sldasm"):
            doc_type = 2  # swDocASSEMBLY
            open_options = 1 | 32  # swOpenDocOptions_Silent | swOpenDocOptions_LoadModel
        else:
            print(f"Unsupported file format: {file_abs}")
            sys.exit(1)
            
        print(f"Opening: {file_abs}")
        error = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        warning = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        
        model = swApp.OpenDoc6(file_abs, doc_type, open_options, "", error, warning)
        if model is None:
            print(f"Failed to open {file_abs}. Error: {error.value}")
            sys.exit(1)
            
        act_error = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        swApp.ActivateDoc3(os.path.basename(file_abs), False, 0, act_error)
        time.sleep(1)
        
        # Perform export for each target format (PDF, DXF, STEP, STEP_ASM)
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
                
            elif active_fmt in ("STEP", "STEP_ASM"):
                format_subdir = "STEP" if active_fmt == "STEP" else "STEP_ASM"
                target_ext = ".step"
                try:
                    orig_step_ap = swApp.GetUserPreferenceIntegerValue(75)
                    orig_step_appearances = swApp.GetUserPreferenceToggle(787)
                    
                    swApp.SetUserPreferenceIntegerValue(75, 214)                          # AP214
                    swApp.SetUserPreferenceToggle(787, True)                              # Export Appearances
                except Exception as pref_e:
                    print(f"Failed to set STEP/STEP_ASM preferences: {pref_e}")

            # Determine output directory
            file_dir = os.path.dirname(file_abs)
            dest_dir = os.path.join(file_dir, output_dir, format_subdir)
            os.makedirs(dest_dir, exist_ok=True)
            
            base_filename = os.path.splitext(os.path.basename(file_abs))[0]
            
            # Determine configurations list to iterate
            configs_to_process = [None]
            if active_fmt in ("STEP", "STEP_ASM"):
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
                        time.sleep(1)
                        dest_file_path = os.path.join(dest_dir, f"{base_filename}_{config_name}{target_ext}")
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
                        
                # Save
                result = model.SaveAs3(dest_file_path, 0, 1)
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
            elif active_fmt in ("STEP", "STEP_ASM"):
                try:
                    if orig_step_ap is not None:
                        swApp.SetUserPreferenceIntegerValue(75, orig_step_ap)
                    if orig_step_appearances is not None:
                        swApp.SetUserPreferenceToggle(787, orig_step_appearances)
                except Exception as restore_e:
                    print(f"Failed to restore STEP/STEP_ASM preferences: {restore_e}")
        # BOM Export block if it is sldasm and export_bom is enabled (runs AFTER STEP_ASM export)
        if f_lower.endswith(".sldasm") and export_bom:
            print(f"Starting BOM extraction for: {file_abs}")
            try:
                conf_val = model.GetConfigurationNames
                if callable(conf_val):
                    config_names = conf_val()
                else:
                    config_names = conf_val
                file_dir = os.path.dirname(file_abs)
                dest_dir = os.path.join(file_dir, output_dir, "BOM")
                os.makedirs(dest_dir, exist_ok=True)
                
                base_filename = os.path.splitext(os.path.basename(file_abs))[0]
                configurations_to_run = config_names if (config_names and len(config_names) >= 2) else [None]
                
                for config_name in configurations_to_run:
                    if config_name:
                        print(f"Switching to configuration: {config_name}")
                        model.ShowConfiguration2(config_name)
                        time.sleep(1)
                        csv_filename = f"{base_filename}_{config_name}.csv"
                    else:
                        csv_filename = f"{base_filename}.csv"
                        
                    dest_csv_path = os.path.join(dest_dir, csv_filename)
                    
                    assembly = model
                    components = assembly.GetComponents(False)
                    
                    comp_counts = {}
                    comp_objects = {}
                    
                    if components:
                        for comp in components:
                            is_supp_val = comp.IsSuppressed
                            is_supp = is_supp_val() if callable(is_supp_val) else is_supp_val
                            if is_supp:
                                continue
                            path_val = comp.GetPathName
                            path = path_val() if callable(path_val) else path_val
                            if not path:
                                continue
                            path_lower = path.lower()
                            comp_counts[path_lower] = comp_counts.get(path_lower, 0) + 1
                            comp_objects[path_lower] = comp
                            
                    all_prop_names = set()
                    bom_rows = []
                    
                    for path_lower, comp in comp_objects.items():
                        qty = comp_counts[path_lower]
                        props = {}
                        comp_model = None
                        try:
                            comp_model_val = comp.GetModelDoc2
                            comp_model = comp_model_val() if callable(comp_model_val) else comp_model_val
                        except Exception as doc_e:
                            print(f"    Warning: Could not get ModelDoc2 for {os.path.basename(path_lower)}: {doc_e}")
                        except:
                            print(f"    Warning: COM error getting ModelDoc2 for {os.path.basename(path_lower)}")
                        if comp_model:
                            # Global
                            prop_mgr_global = comp_model.Extension.CustomPropertyManager("")
                            names_global = prop_mgr_global.GetNames()
                            if names_global:
                                for name in names_global:
                                    val_out = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BSTR, "")
                                    res_out = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BSTR, "")
                                    was_resolved = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BOOL, False)
                                    try:
                                        prop_mgr_global.Get5(name, False, val_out, res_out, was_resolved)
                                        props[name] = res_out.value
                                    except:
                                        try:
                                            prop_mgr_global.Get6(name, False, val_out, res_out, was_resolved)
                                            props[name] = res_out.value
                                        except:
                                            pass
                                            
                            # Configuration-specific
                            ref_config = comp.ReferencedConfiguration
                            if ref_config:
                                prop_mgr_config = comp_model.Extension.CustomPropertyManager(ref_config)
                                names_config = prop_mgr_config.GetNames()
                                if names_config:
                                    for name in names_config:
                                        val_out = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BSTR, "")
                                        res_out = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BSTR, "")
                                        was_resolved = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BOOL, False)
                                        try:
                                            prop_mgr_config.Get5(name, False, val_out, res_out, was_resolved)
                                            props[name] = res_out.value
                                        except:
                                            try:
                                                prop_mgr_config.Get6(name, False, val_out, res_out, was_resolved)
                                                props[name] = res_out.value
                                            except:
                                                pass
                                                
                        for k in props.keys():
                            all_prop_names.add(k)
                            
                        # Retrieve path safely or fall back to path_lower
                        comp_path_val = comp.GetPathName
                        comp_path = comp_path_val() if callable(comp_path_val) else comp_path_val
                        if not comp_path:
                            comp_path = path_lower
                            
                        row_data = {
                            "Component Name": os.path.splitext(os.path.basename(path_lower))[0],
                            "File Path": comp_path,
                            "Quantity": qty,
                            "properties": props
                        }
                        bom_rows.append(row_data)
                        
                    import csv
                    headers = ["Component Name", "File Path", "Quantity"] + sorted(list(all_prop_names))
                    try:
                        with open(dest_csv_path, "w", newline="", encoding="utf-8-sig") as csvfile:
                            writer = csv.writer(csvfile)
                            writer.writerow(headers)
                            for row in bom_rows:
                                line = [row["Component Name"], row["File Path"], row["Quantity"]]
                                for prop in headers[3:]:
                                    line.append(row["properties"].get(prop, ""))
                                writer.writerow(line)
                        print(f"Successfully generated BOM: {dest_csv_path}")
                    except Exception as csv_err:
                        print(f"Failed to write BOM CSV {dest_csv_path}: {csv_err}")
                        
            except Exception as bom_err:
                print(f"Error extracting BOM: {repr(bom_err)}")

        # Close documents
        close_all_documents_without_saving(swApp)
        
    except Exception as file_e:
        print(f"Error processing {file_abs}: {repr(file_e)}")
        if swApp:
            close_all_documents_without_saving(swApp)
        sys.exit(1)
    finally:
        pythoncom.CoUninitialize()

def run_export(job_file):
    if not WIN32_AVAILABLE:
        print("Error: PyWin32 is not installed or not running on Windows.")
        return
        
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
    is_bom_enabled = job.get("export_bom", True)
    
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
                
        if target_formats or (f_lower.endswith(".sldasm") and is_bom_enabled):
            # Sort target_formats in the order of: PDF -> DXF -> STEP -> STEP_ASM
            format_order = {"PDF": 0, "DXF": 1, "STEP": 2, "STEP_ASM": 3}
            target_formats.sort(key=lambda fmt: format_order.get(fmt, 9))
            file_jobs[f_rel] = target_formats
            
    if not file_jobs:
        print("No matching files to export.")
        return

    has_errors = False

    # Initialize COM
    pythoncom.CoInitialize()
    
    # Launch new, separate instance of SolidWorks
    swApp = None
    sw_pid = None
    try:
        swApp = win32com.client.DispatchEx('SldWorks.Application')
        time.sleep(5)
    except Exception as e:
        print(f"Failed to start a new SolidWorks instance: {e}")
        pythoncom.CoUninitialize()
        return
        
    try:
        # Show the UI and enable user control to prevent background hangs/dialog freezes
        swApp.Visible = True
        swApp.UserControl = True
        
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
            print(f"Could not retrieve SolidWorks PID: {pid_e}")
            sw_pid = None
            
        # Suppress warnings and prompts
        try:
            swApp.SetUserPreferenceToggle(11, False)  # swDxfIssuingWarning
            swApp.SetUserPreferenceToggle(143, False) # swDxfMappingFileEnabled
            swApp.SetUserPreferenceToggle(15, True)   # swExtRefNoPromptOrSave (Don't prompt to save read-only referenced docs)
            swApp.SetUserPreferenceToggle(119, False) # swShowErrorsEveryRebuild (Suppress rebuild errors dialog)
            swApp.SetUserPreferenceIntegerValue(246, 1) # swRebuildErrorAction -> swStopContinuePrompt_Continue (Always continue on rebuild errors)
            swApp.SetUserPreferenceToggle(249, False) # swWarnSaveUpdateErrors (Suppress save warnings on rebuild errors)
            swApp.SetUserPreferenceToggle(46, False)  # swAutoSaveEnable (Disable Auto-Save/Recovery to prevent recovery prompts)
        except Exception as pref_e:
            print(f"Failed to set user preferences: {pref_e}")

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
        
        for idx, file_rel in enumerate(file_list):
            file_abs = os.path.normpath(os.path.join(workspace_path, file_rel))
            if not os.path.exists(file_abs):
                print(f"Skipping missing file: {file_abs}")
                continue
                
            target_formats = file_jobs[file_rel]
            
            # Send real-time progress update via stdout
            # format: [PROGRESS] current_index/total_files : base_filename
            print(f"[PROGRESS] {idx+1}/{total_files} : {os.path.basename(file_rel)}", flush=True)
            
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
            export_bom_str = str(job.get("export_bom", True))
            import os as os_env
            env_vars = os_env.environ.copy()
            env_vars["PYTHONIOENCODING"] = "utf-8"
            proc = subprocess.Popen(
                [sys.executable, "-u", __file__, "--single", file_abs, ",".join(target_formats), output_dir, workspace_path, export_bom_str],
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
            timeout = 40.0 # 40 seconds watchdog
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
                    print(f"\n[WARNING] Watchdog Timeout: File conversion exceeded 40 seconds limit ({file_rel}). Force terminating process...", flush=True)
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
                if sw_pid:
                    try:
                        print(f"Forcefully terminating hung SolidWorks PID {sw_pid} due to timeout...", flush=True)
                        subprocess.run(f"taskkill /F /PID {sw_pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        time.sleep(3)
                    except Exception as kill_e:
                        print(f"Could not terminate SolidWorks PID {sw_pid}: {kill_e}", flush=True)
                
                print("Launching a new SolidWorks instance to resume...", flush=True)
                try:
                    swApp = win32com.client.DispatchEx('SldWorks.Application')
                    time.sleep(5)
                    swApp.Visible = True
                    swApp.UserControl = True
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
                    break
            elif proc.returncode != 0:
                has_errors = True
                print(f"[ERROR] Subprocess failed for file {file_rel} with exit code {proc.returncode}.", flush=True)

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
        if sw_pid:
            try:
                import subprocess
                time.sleep(2)
                subprocess.run(f"taskkill /F /PID {sw_pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(f"Forcefully terminated SolidWorks PID {sw_pid} to ensure clean exit.")
            except Exception as kill_e:
                print(f"Could not terminate SolidWorks PID {sw_pid}: {kill_e}")
                
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
        if len(sys.argv) < 7:
            print("Error: Missing arguments for --single mode")
            sys.exit(1)
        file_abs = sys.argv[2]
        formats_str = sys.argv[3]
        output_dir = sys.argv[4]
        workspace_path = sys.argv[5]
        export_bom_val = sys.argv[6].lower() == "true"
        target_formats = formats_str.split(",")
        if target_formats == [""]:
            target_formats = []
        run_single_export(file_abs, target_formats, output_dir, workspace_path, export_bom_val)
    else:
        if len(sys.argv) < 2:
            print("Usage: python sw_export_runner.py <job_file_path>")
            sys.argv = [sys.argv[0], "export_job.json"]
        run_export(sys.argv[1])
