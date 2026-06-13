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
    try:
        # Get all open documents (GetDocuments is a property tuple in PyWin32)
        docs = swApp.GetDocuments
        if docs:
            for doc in docs:
                try:
                    title = doc.GetTitle()
                    swApp.QuitDoc(title)
                except Exception as doc_e:
                    print(f"Error quitting doc: {doc_e}")
    except Exception as e:
        print(f"Error getting documents: {e}")
        
    # Fallback to CloseAllDocuments if needed
    try:
        swApp.CloseAllDocuments(True)
    except:
        pass

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
        
    # Gather matching files per format
    jobs_to_run = []
    
    for active_fmt in formats:
        filtered_files = []
        if active_fmt == "PDF" or active_fmt == "DXF":
            # .slddrw files
            for f_rel in all_files:
                f_lower = f_rel.lower()
                if f_lower.endswith(".slddrw"):
                    base_name = os.path.basename(f_rel)
                    if prefix == "" or base_name.startswith(prefix):
                        filtered_files.append(f_rel)
        elif active_fmt == "STEP":
            # .sldprt files
            for f_rel in all_files:
                f_lower = f_rel.lower()
                if f_lower.endswith(".sldprt"):
                    base_name = os.path.basename(f_rel)
                    if prefix == "" or base_name.startswith(prefix):
                        filtered_files.append(f_rel)
        elif active_fmt == "STEP_ASM":
            # .sldasm files
            for f_rel in all_files:
                f_lower = f_rel.lower()
                if f_lower.endswith(".sldasm"):
                    base_name = os.path.basename(f_rel)
                    if prefix == "" or base_name.startswith(prefix):
                        filtered_files.append(f_rel)
                        
        if filtered_files:
            jobs_to_run.append({
                "format": active_fmt,
                "files": filtered_files
            })
            
    if not jobs_to_run:
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

        # Run each format job
        for run_job in jobs_to_run:
            active_fmt = run_job["format"]
            files = run_job["files"]
            
            # Setup format specifics and target preferences
            orig_pdf_color = None
            orig_pdf_line_weights = None
            orig_pdf_high_quality = None
            orig_step_ap = None
            orig_step_appearances = None
            
            if active_fmt == "PDF":
                format_subdir = "PDF"
                target_ext = ".pdf"
                doc_type = 3  # swDocDRAWING
                open_options = 1 | 32  # swOpenDocOptions_Silent | swOpenDocOptions_LoadModel
                
                # Save & Set PDF user preferences (Black & White, line weights/pen tables)
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
                doc_type = 3  # swDocDRAWING
                open_options = 1 | 32  # swOpenDocOptions_Silent | swOpenDocOptions_LoadModel
            elif active_fmt == "STEP":
                format_subdir = "STEP"
                target_ext = ".step"
                doc_type = 1  # swDocPART
                open_options = 1  # swOpenDocOptions_Silent
                
                # Save & Set STEP user preferences (AP214 and Appearances)
                try:
                    orig_step_ap = swApp.GetUserPreferenceIntegerValue(75)               # swStepAP
                    orig_step_appearances = swApp.GetUserPreferenceToggle(787)            # swStepExportAppearances
                    
                    swApp.SetUserPreferenceIntegerValue(75, 214)                         # 214 = AP214 (supports color)
                    swApp.SetUserPreferenceToggle(787, True)                              # True = Export Appearances (color/textures)
                except Exception as pref_e:
                    print(f"Failed to set STEP preferences: {pref_e}")
                    
            elif active_fmt == "STEP_ASM":
                format_subdir = "STEP_ASM"
                target_ext = ".step"
                doc_type = 2  # swDocASSEMBLY
                open_options = 1  # swOpenDocOptions_Silent
                
                # Save & Set STEP_ASM user preferences
                try:
                    orig_step_ap = swApp.GetUserPreferenceIntegerValue(75)               # swStepAP
                    orig_step_appearances = swApp.GetUserPreferenceToggle(787)            # swStepExportAppearances
                    
                    swApp.SetUserPreferenceIntegerValue(75, 214)                         # 214 = AP214 (supports color)
                    swApp.SetUserPreferenceToggle(787, True)                              # True = Export Appearances (color/textures)
                except Exception as pref_e:
                    print(f"Failed to set STEP_ASM preferences: {pref_e}")

            print(f"Processing job format: {active_fmt}")
            for file_rel in files:
                file_abs = os.path.normpath(os.path.join(workspace_path, file_rel))
                if not os.path.exists(file_abs):
                    print(f"Skipping missing file: {file_abs}")
                    continue
                    
                file_dir = os.path.dirname(file_abs)
                dest_dir = os.path.join(file_dir, output_dir, format_subdir)
                os.makedirs(dest_dir, exist_ok=True)
                
                base_filename = os.path.splitext(os.path.basename(file_abs))[0]
                dest_file_path = os.path.join(dest_dir, base_filename + target_ext)
                
                # If target file exists, remove it first
                if os.path.exists(dest_file_path):
                    try:
                        os.remove(dest_file_path)
                    except Exception as del_e:
                        print(f"Failed to remove existing file {dest_file_path}: {del_e}")
                
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
                    
                    result = model.SaveAs3(dest_file_path, 0, 1)
                    if result == 0:
                        print(f"Successfully exported {file_abs} -> {dest_file_path}")
                    else:
                        print(f"Failed to save {dest_file_path} (SaveAs3 code: {result})")
                        
                    close_all_documents_without_saving(swApp)
                except Exception as file_e:
                    print(f"Error processing {file_abs}: {file_e}")
                    close_all_documents_without_saving(swApp)
                        
            # Restore preferences for the active format after processing all files
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
                    print(f"Failed to restore STEP preferences: {restore_e}")

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

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python sw_export_runner.py <job_file_path>")
        sys.argv = [sys.argv[0], "export_job.json"]
    run_export(sys.argv[1])
