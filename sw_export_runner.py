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
            f_lower = file_rel.lower()
            
            # Decide document type based on extension
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
                continue
                
            # Send real-time progress update via stdout
            # format: [PROGRESS] current_index/total_files : base_filename
            print(f"[PROGRESS] {idx+1}/{total_files} : {os.path.basename(file_rel)}", flush=True)
            
            print(f"Opening: {file_abs}")
            error = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            warning = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            
            try:
                model = swApp.OpenDoc6(file_abs, doc_type, open_options, "", error, warning)
                if model is None:
                    print(f"Failed to open {file_abs}. Error: {error.value}")
                    continue
                    
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
                            orig_pdf_color = swApp.GetUserPreferenceToggle(323)                   # swPDFExportInColor
                            orig_pdf_line_weights = swApp.GetUserPreferenceToggle(327)            # swPDFExportUseCurrentPrintLineWeights
                            orig_pdf_high_quality = swApp.GetUserPreferenceToggle(325)            # swPDFExportHighQuality
                            
                            swApp.SetUserPreferenceToggle(323, False)                             # False = Black and White
                            swApp.SetUserPreferenceToggle(327, True)                              # True = Use printer line weights (respects pen table)
                            swApp.SetUserPreferenceToggle(325, True)                              # True = High quality lines
                        except Exception as pref_e:
                            print(f"Failed to set PDF preferences: {pref_e}")
                            
                    elif active_fmt == "DXF":
                        format_subdir = "DXF"
                        target_ext = ".dxf"
                        
                    elif active_fmt in ("STEP", "STEP_ASM"):
                        format_subdir = "STEP" if active_fmt == "STEP" else "STEP_ASM"
                        target_ext = ".step"
                        try:
                            orig_step_ap = swApp.GetUserPreferenceIntegerValue(75)               # swStepAP
                            orig_step_appearances = swApp.GetUserPreferenceToggle(787)            # swStepExportAppearances
                            
                            swApp.SetUserPreferenceIntegerValue(75, 214)                         # 214 = AP214 (supports color)
                            swApp.SetUserPreferenceToggle(787, True)                              # True = Export Appearances (color/textures)
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
                            
                # Close files
                close_all_documents_without_saving(swApp)
            except Exception as file_e:
                print(f"Error processing {file_abs}: {file_e}")
                close_all_documents_without_saving(swApp)

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
    if len(sys.argv) < 2:
        print("Usage: python sw_export_runner.py <job_file_path>")
        sys.argv = [sys.argv[0], "export_job.json"]
    run_export(sys.argv[1])
