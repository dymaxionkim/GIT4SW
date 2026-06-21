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

def run_single_export(file_abs, target_formats, output_dir, workspace_path):
    if not WIN32_AVAILABLE:
        print("Error: PyWin32 is not installed or not running on Windows.")
        sys.exit(1)
        
    pythoncom.CoInitialize()
    swApp = None
    try:
        try:
            swApp = win32com.client.GetActiveObject("SldWorks.Application")
        except Exception:
            try:
                swApp = win32com.client.GetObject(Class="SldWorks.Application")
            except Exception as e2:
                print(f"Failed to bind to active SolidWorks instance: {e2}")
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
            open_options = 1  # swOpenDocOptions_Silent
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
        
        # Perform export for each target format
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
                    
        # Close documents
        close_all_documents_without_saving(swApp)
        
    except Exception as file_e:
        print(f"Error processing {file_abs}: {file_e}")
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
        print("No matching files to export.")
        return

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
        # Hide the UI and disable user control for true background run
        swApp.Visible = False
        swApp.UserControl = False
        
        # Get process ID
        try:
            sw_pid = swApp.GetProcessID()
        except Exception as pid_e:
            print(f"Could not retrieve SolidWorks PID: {pid_e}")
            sw_pid = None
            
        # Suppress warnings and prompts
        try:
            swApp.SetUserPreferenceToggle(11, False)  # swDxfIssuingWarning
            swApp.SetUserPreferenceToggle(143, False) # swDxfMappingFileEnabled
            swApp.SetUserPreferenceToggle(15, True)   # swExtRefNoPromptOrSave (Don't prompt to save read-only referenced docs)
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
            proc = subprocess.Popen(
                [sys.executable, __file__, "--single", file_abs, ",".join(target_formats), output_dir, workspace_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8"
            )
            
            out_queue = queue.Queue()
            t_read = threading.Thread(target=reader_thread, args=(proc.stdout, out_queue), daemon=True)
            t_read.start()
            
            start_time = time.time()
            timeout = 120.0 # 2 minutes watchdog
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
                    print(f"\n[WARNING] Watchdog Timeout: File conversion exceeded 2 minutes limit ({file_rel}). Force terminating process...", flush=True)
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
                    swApp.Visible = False
                    swApp.UserControl = False
                    try:
                        sw_pid = swApp.GetProcessID()
                    except Exception as pid_e:
                        print(f"Could not retrieve SolidWorks PID: {pid_e}", flush=True)
                        sw_pid = None
                        
                    # Suppress warnings and prompts on the new instance
                    swApp.SetUserPreferenceToggle(11, False)
                    swApp.SetUserPreferenceToggle(143, False)
                    swApp.SetUserPreferenceToggle(15, True)
                except Exception as re_e:
                    print(f"Failed to restart SolidWorks instance: {re_e}", flush=True)
                    swApp = None
                    sw_pid = None
                    break

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
        run_single_export(file_abs, target_formats, output_dir, workspace_path)
    else:
        if len(sys.argv) < 2:
            print("Usage: python sw_export_runner.py <job_file_path>")
            sys.argv = [sys.argv[0], "export_job.json"]
        run_export(sys.argv[1])
