import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import tkinter.font as tkfont
import threading
import queue
import json
import webbrowser
import subprocess
import time

from git_service import GitService, MergeConflictError, run_git_subprocess
from sw_monitor import SolidWorksMonitorService

# IShellItemImageFactory interface definition for CAD thumbnail extraction
try:
    from comtypes import GUID, IUnknown, COMMETHOD, HRESULT
    from ctypes import POINTER
    from ctypes.wintypes import SIZE, UINT, HBITMAP

    class IShellItemImageFactory(IUnknown):
        _case_insensitive_ = True
        _iid_ = GUID('{bcc18b79-ba16-442f-80c4-8a59c30c463b}')
        _idlflags_ = []

    IShellItemImageFactory._methods_ = [
        COMMETHOD([], HRESULT, 'GetImage',
                  (['in'], SIZE, 'size'),
                  (['in'], UINT, 'flags'),
                  (['out'], POINTER(HBITMAP), 'phbm')),
    ]
except Exception:
    IShellItemImageFactory = None


class CustomFileTable(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg="#ffffff")
        
        # Create Treeview
        self.treeview = ttk.Treeview(
            self,
            columns=("path", "status", "solidworks", "locked"),
            show="headings",
            selectmode="extended"
        )
        
        # Create Scrollbar
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.treeview.yview)
        self.treeview.configure(yscrollcommand=self.scrollbar.set)
        
        # Pack them
        self.treeview.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        
        # Set headings
        self.treeview.heading("path", text="File Path", anchor="w")
        self.treeview.heading("status", text="Status", anchor="center")
        self.treeview.heading("solidworks", text="SolidWorks", anchor="center")
        self.treeview.heading("locked", text="Locked", anchor="center")
        
        # Column formatting
        self.treeview.column("path", anchor="w", width=450, minwidth=40, stretch=False)
        self.treeview.column("status", anchor="center", width=120, minwidth=40, stretch=False)
        self.treeview.column("solidworks", anchor="center", width=100, minwidth=40, stretch=False)
        self.treeview.column("locked", anchor="center", width=120, minwidth=40, stretch=False)
        
        # Define Tags for coloring entire row based on extension
        self.treeview.tag_configure("sldprt", foreground="#059669")
        self.treeview.tag_configure("sldasm", foreground="#d97706")
        self.treeview.tag_configure("slddrw", foreground="#dc2626")
        self.treeview.tag_configure("default_ext", foreground="#7c3aed")
        
        # Bind keyboard shortcuts for selecting all items
        self.treeview.bind("<Control-a>", self.select_all_files)
        self.treeview.bind("<Control-A>", self.select_all_files)
        self.treeview.bind("<Control-ae>", self.select_all_files)
        
    def select_all_files(self, event=None):
        self.treeview.selection_set(self.treeview.get_children())
        return "break"

    def get_children(self):
        return self.treeview.get_children()
        
    def delete(self, item_id):
        self.treeview.delete(item_id)
        
    def selection(self):
        return self.treeview.selection()
        
    def item(self, item_id, option=None):
        if option == 'values':
            return self.treeview.item(item_id, 'values')
        if option:
            return self.treeview.item(item_id, option)
        return self.treeview.item(item_id)
        
    def insert(self, parent="", index="end", values=None):
        if not values:
            return ""
        path_val = values[0]
        _, ext = os.path.splitext(path_val)
        ext_lower = ext.lower()
        
        if ext_lower == ".sldprt":
            tag = "sldprt"
        elif ext_lower == ".sldasm":
            tag = "sldasm"
        elif ext_lower == ".slddrw":
            tag = "slddrw"
        else:
            tag = "default_ext"
            
        return self.treeview.insert(parent, index, values=values, tags=(tag,))
        
    def selection_add(self, item_id):
        self.treeview.selection_add(item_id)



class BranchSelectionDialog(tk.Toplevel):
    def __init__(self, parent, branches):
        super().__init__(parent)
        self.title("Select Branch")
        self.geometry("380x160")
        self.resizable(False, False)
        self.configure(bg="#f3f4f6")
        self.transient(parent)
        self.grab_set()
        
        self.selected_branch = None
        
        # Sort branches: put "main" first, then alphabetical
        sorted_branches = []
        if "main" in branches:
            sorted_branches.append("main")
        for b in sorted(branches):
            if b != "main":
                sorted_branches.append(b)
                
        lbl = ttk.Label(self, text="The selected version is not contained in the current branch.\nPlease select one of the branches below that contains this version:", 
                        wraplength=340, justify="left", style="TLabel")
        lbl.pack(padx=16, pady=12, fill="x")
        
        self.cb = ttk.Combobox(self, values=sorted_branches, state="readonly")
        self.cb.pack(padx=16, pady=4, fill="x")
        self.cb.set(sorted_branches[0])
        
        btn_frm = ttk.Frame(self, style="TFrame")
        btn_frm.pack(padx=16, pady=16, fill="x")
        
        btn_ok = ttk.Button(btn_frm, text="Select", style="Primary.TButton", command=self.on_ok)
        btn_ok.pack(side="right", padx=(8, 0))
        
        btn_cancel = ttk.Button(btn_frm, text="Cancel", command=self.on_cancel)
        btn_cancel.pack(side="right")
        
        # Center the window relative to parent
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")
        
        self.protocol("WM_DELETE_WINDOW", self.on_cancel)
        
    def on_ok(self):
        self.selected_branch = self.cb.get()
        self.destroy()
        
    def on_cancel(self):
        self.selected_branch = None
        self.destroy()


class MultiConflictResolutionDialog(tk.Toplevel):
    def __init__(self, parent, conflicted_files, ours_branch=None, theirs_branch=None, is_pull=False):
        super().__init__(parent)
        self.title("Conflicts")
        self.geometry("600x420")
        self.configure(bg="#f3f4f6")
        self.transient(parent)
        self.grab_set()
        
        self.result = None
        self.temp_resolutions = {}
        
        # Header
        lbl = ttk.Label(self, text="Conflicts detected! Select one or more files and choose a version to adopt:", 
                        wraplength=560, justify="left", style="TLabel", font=("TkDefaultFont", 10, "bold"))
        lbl.pack(padx=16, pady=12, fill="x")
        
        # Listbox Frame
        list_frm = ttk.Frame(self, style="Card.TFrame")
        list_frm.pack(padx=16, pady=4, fill="both", expand=True)
        
        scrollbar = ttk.Scrollbar(list_frm, orient="vertical")
        scrollbar.pack(side="right", fill="y")
        
        # Modern Flat styled Listbox with custom selection colors matching combobox
        self.listbox = tk.Listbox(
            list_frm,
            selectmode="extended",
            yscrollcommand=scrollbar.set,
            bg="#ffffff",
            fg="#1f2937",
            selectbackground="#d1fae5",
            selectforeground="#065f46",
            activestyle="none",
            bd=0,
            highlightthickness=0,
            font="TkDefaultFont"
        )
        for f in conflicted_files:
            self.listbox.insert("end", f)
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.listbox.yview)
        
        # Bind Listbox selection changes to handle Diff button activation
        self.listbox.bind("<<ListboxSelect>>", self.on_select_change)
        
        # Control Frame (Combo box + Action buttons)
        control_frm = ttk.Frame(self, style="TFrame")
        control_frm.pack(padx=16, pady=12, fill="x", side="bottom")
        
        lbl_adopt = ttk.Label(control_frm, text="Adopt version from:", style="TLabel")
        lbl_adopt.pack(side="left", padx=(0, 8))
        
        if is_pull:
            self.options = ["local", "remote"]
        else:
            self.options = [ours_branch if ours_branch else "local", theirs_branch if theirs_branch else "remote"]
            
        self.cb_choice = ttk.Combobox(control_frm, state="readonly", values=self.options, width=15)
        self.cb_choice.pack(side="left", padx=(0, 8))
        self.cb_choice.set(self.options[0])
        
        # Buttons aligned horizontally: Choose -> Diff -> Exit
        btn_ok = ttk.Button(control_frm, text="Choose", style="Primary.TButton", command=self.on_ok)
        btn_ok.pack(side="left")
        
        self.btn_diff = ttk.Button(control_frm, text="Diff", style="Diff.TButton", command=self.on_diff)
        self.btn_diff.pack(side="left", padx=(8, 0))
        self.btn_diff.state(["disabled"]) # Initially disabled until exactly 1 file is selected
        
        btn_cancel = ttk.Button(control_frm, text="Exit", command=self.on_cancel)
        btn_cancel.pack(side="left", padx=(8, 0))
        
        # Center the window
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")
        
        self.protocol("WM_DELETE_WINDOW", self.on_cancel)
        
    def on_select_change(self, event=None):
        selected_indices = self.listbox.curselection()
        if len(selected_indices) == 1:
            self.btn_diff.state(["!disabled"])
        else:
            self.btn_diff.state(["disabled"])
            
    def on_diff(self):
        selected_indices = self.listbox.curselection()
        if len(selected_indices) == 1:
            selected_file = self.listbox.get(selected_indices[0])
            messagebox.showinfo(
                "Compare Version (Diff)", 
                f"Compare versions for:\n{selected_file}\n\n(Visual comparison tool or git diff will be executed.)"
            )
        
    def on_ok(self):
        selected_indices = self.listbox.curselection()
        if not selected_indices:
            messagebox.showwarning("No Selection", "Please select one or more files from the list to resolve.")
            return
            
        choice = self.cb_choice.get()
        if choice in ("local", self.options[0]):
            res_val = "ours"
        else:
            res_val = "theirs"
            
        selected_files = [self.listbox.get(i) for i in selected_indices]
        for f in selected_files:
            self.temp_resolutions[f] = res_val
            
        # Delete from listbox in reverse order
        for index in sorted(selected_indices, reverse=True):
            self.listbox.delete(index)
            
        if self.listbox.size() == 0:
            self.result = self.temp_resolutions
            self.destroy()
            
    def on_cancel(self):
        self.result = None
        self.destroy()


class FileCommitHistoryDialog(tk.Toplevel):
    def __init__(self, parent, file_rel_path):
        super().__init__(parent)
        self.parent = parent
        self.file_rel_path = file_rel_path
        self.title(f"Commit History - {os.path.basename(file_rel_path)}")
        self.geometry("700x450")
        self.configure(bg="#f3f4f6")
        self.transient(parent)
        self.grab_set()
        
        self.selected_commit = None
        self.cancel_event = threading.Event()
        
        # UI Layout
        # Top Label
        lbl = ttk.Label(self, text=f"File: {file_rel_path}\nSelect a commit to compare with the current version:", 
                        justify="left", style="TLabel", font="TkDefaultFont")
        lbl.pack(padx=16, fill="x", pady=(16, 8))
        
        # Table Container (Frame)
        tbl_frame = ttk.Frame(self, style="TFrame")
        tbl_frame.pack(padx=16, pady=8, fill="both", expand=True)
        
        # Treeview Scrollbar
        self.scrollbar = ttk.Scrollbar(tbl_frame, orient="vertical")
        self.scrollbar.pack(side="right", fill="y")
        
        # Treeview Table
        self.tree = ttk.Treeview(
            tbl_frame,
            columns=("commit", "date", "author", "message"),
            show="headings",
            selectmode="browse",
            yscrollcommand=self.scrollbar.set
        )
        self.scrollbar.config(command=self.tree.yview)
        
        self.tree.heading("commit", text="Commit")
        self.tree.heading("date", text="Date")
        self.tree.heading("author", text="Author")
        self.tree.heading("message", text="Message")
        
        self.tree.column("commit", width=90, minwidth=70, stretch=False, anchor="center")
        self.tree.column("date", width=150, minwidth=120, stretch=False, anchor="center")
        self.tree.column("author", width=100, minwidth=80, stretch=False, anchor="center")
        self.tree.column("message", width=300, minwidth=150, stretch=True)
        
        self.tree.pack(side="left", fill="both", expand=True)
        
        # Tree selection binding
        self.tree.bind("<<TreeviewSelect>>", self.on_selection_change)
        
        # Status / Progress Label
        self.lbl_status = ttk.Label(self, text="Loading commit history...", style="TLabel", font="TkDefaultFont")
        self.lbl_status.pack(padx=16, pady=4, fill="x")
        
        # Bottom Buttons Frame
        btn_frm = ttk.Frame(self, style="TFrame")
        btn_frm.pack(padx=16, pady=16, fill="x", side="bottom")
        
        self.btn_diff = ttk.Button(btn_frm, text="Diff", style="Primary.TButton", command=self.on_diff)
        self.btn_diff.state(["disabled"])
        self.btn_diff.pack(side="right", padx=(8, 0))
        
        self.btn_exit = ttk.Button(btn_frm, text="Exit", command=self.on_exit)
        self.btn_exit.pack(side="right")
        
        # Center the window
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")
        
        self.protocol("WM_DELETE_WINDOW", self.on_exit)
        
        # Fetch history in background thread
        self.history_thread = threading.Thread(target=self._fetch_history, daemon=True)
        self.history_thread.start()
        
    def _fetch_history(self):
        try:
            repo = self.parent.git_service.repo
            if not repo:
                raise RuntimeError("Git repository not loaded.")
                
            commit_list = []
            for commit in repo.iter_commits(paths=self.file_rel_path):
                if self.cancel_event.is_set():
                    return
                commit_list.append({
                    "hexsha": commit.hexsha,
                    "short_sha": commit.hexsha[:7],
                    "author": commit.author.name,
                    "date": commit.authored_datetime.strftime("%Y-%m-%d %H:%M:%S") if commit.authored_datetime else "",
                    "message": commit.summary
                })
                
            if not self.cancel_event.is_set():
                self.after(0, lambda: self._populate_table(commit_list))
        except Exception as e:
            if not self.cancel_event.is_set():
                self.after(0, lambda: self._show_error(str(e)))
                
    def _populate_table(self, commit_list):
        self.lbl_status.pack_forget()
        if not commit_list:
            self.lbl_status.config(text="No commits found for this file.")
            self.lbl_status.pack(padx=16, pady=4, fill="x")
            return
            
        for c in commit_list:
            self.tree.insert("", "end", values=(c["short_sha"], c["date"], c["author"], c["message"]), tags=(c["hexsha"],))
            
    def _show_error(self, err_msg):
        self.lbl_status.config(text=f"Error loading history: {err_msg}", foreground="#ef4444")
        
    def on_selection_change(self, event=None):
        selected = self.tree.selection()
        if selected:
            self.btn_diff.state(["!disabled"])
        else:
            self.btn_diff.state(["disabled"])
            
    def on_diff(self):
        selected = self.tree.selection()
        if not selected:
            return
        tags = self.tree.item(selected[0], "tags")
        if not tags:
            return
        hexsha = tags[0]
        
        self.btn_diff.state(["disabled"])
        # Keep Exit button enabled so the user can cancel/close the dialog while running
        self.lbl_status.config(text="Running Visual Diff in SolidWorks... Please wait.", foreground="#2563eb")
        
        # Spawn thread for diff
        diff_thread = threading.Thread(target=self._run_visual_diff, args=(hexsha,), daemon=True)
        diff_thread.start()
        
    def _run_visual_diff(self, hexsha):
        import pythoncom
        import win32com.client
        import shutil
        import subprocess
        import os
        import time
        
        # Initialize COM for this thread
        pythoncom.CoInitialize()
        
        ours_temp_path = None
        theirs_temp_path = None
        ours_pdf_path = None
        theirs_pdf_path = None
        
        sw_app = None
        opened_docs = []
        
        try:
            # 1. Setup paths
            backup_dir = os.path.normpath(os.path.join(self.parent.workspace_path, ".backup"))
            os.makedirs(backup_dir, exist_ok=True)
            
            # Normalize path for git show (must be relative to repo and use forward slashes)
            git_file_path = self.file_rel_path
            if os.path.isabs(git_file_path):
                git_file_path = os.path.relpath(git_file_path, self.parent.workspace_path)
            git_file_path = git_file_path.replace("\\", "/")
            
            base, ext = os.path.splitext(os.path.basename(git_file_path))
            ext_lower = ext.lower()
            
            ours_temp_path = os.path.join(backup_dir, f"{base}_OURS{ext}")
            theirs_temp_path = os.path.join(backup_dir, f"{base}_THEIRS{ext}")
            
            ours_pdf_path = os.path.join(backup_dir, f"{base}_OURS.pdf")
            theirs_pdf_path = os.path.join(backup_dir, f"{base}_THEIRS.pdf")
            
            # Remove previous temp/image/pdf files if they exist
            for temp_file in [ours_temp_path, theirs_temp_path, ours_pdf_path, theirs_pdf_path]:
                if os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except Exception:
                        pass
            
            # Clean up old drawing diff pngs in the .backup directory
            for f in os.listdir(backup_dir):
                if f.startswith(f"{base}_DIFF") and f.endswith(".png"):
                    try:
                        os.remove(os.path.join(backup_dir, f))
                    except Exception:
                        pass
            
            # 2. Copy/Extract files
            # Copy OURS
            src_ours = os.path.normpath(os.path.join(self.parent.workspace_path, self.file_rel_path))
            shutil.copy2(src_ours, ours_temp_path)
            
            # Fetch and smudge THEIRS
            repo = self.parent.git_service.repo
            git_path = "git"
            if hasattr(self.parent.git_service, 'git_path') and self.parent.git_service.git_path:
                if os.path.exists(self.parent.git_service.git_path):
                    git_path = self.parent.git_service.git_path
                    
            cmd = [git_path, "show", f"{hexsha}:{git_file_path}"]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.parent.workspace_path)
            if res.returncode != 0:
                raise RuntimeError(f"Git show failed: {res.stderr.decode('utf-8', errors='replace')}")
                
            data = res.stdout
            if data.startswith(b"version https://git-lfs"):
                # Smudge LFS
                smudge_cmd = [git_path, "lfs", "smudge"]
                res_smudge = subprocess.run(smudge_cmd, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.parent.workspace_path)
                if res_smudge.returncode == 0:
                    data = res_smudge.stdout
                else:
                    print(f"Warning: git lfs smudge failed: {res_smudge.stderr.decode('utf-8')}")
                    
            with open(theirs_temp_path, "wb") as f:
                f.write(data)
                
            # Check if cancel event is set
            if self.cancel_event.is_set():
                return
                
            # 3. Connect to SolidWorks (auto-launch if not running)
            print("Connecting to SolidWorks...", flush=True)
            sw_app = None

            # 3-A. Try to bind to an already-running instance
            try:
                raw_sw = win32com.client.GetActiveObject("SldWorks.Application")
                try:
                    import win32com.client.dynamic
                    sw_app = win32com.client.dynamic.Dispatch(raw_sw)
                except Exception:
                    sw_app = win32com.client.Dispatch(raw_sw)
                print("Bound to existing SolidWorks instance.", flush=True)
            except Exception:
                pass

            # 3-B. If not found, launch sldworks.exe and poll ROT up to 60 s
            if sw_app is None:
                sldworks_exe = r"C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\sldworks.exe"
                if os.path.exists(sldworks_exe):
                    print(f"SolidWorks not running – launching: {sldworks_exe}", flush=True)
                    self.after(0, lambda: self.lbl_status.config(
                        text="SolidWorks 실행 중... 잠시만 기다려 주세요.", foreground="#f59e0b"))
                    try:
                        import subprocess as _subprocess
                        _subprocess.Popen([sldworks_exe])
                        _poll_timeout = 60.0
                        _poll_start = time.time()
                        while time.time() - _poll_start < _poll_timeout:
                            if self.cancel_event.is_set():
                                return
                            try:
                                raw_sw = win32com.client.GetActiveObject("SldWorks.Application")
                                try:
                                    import win32com.client.dynamic
                                    sw_app = win32com.client.dynamic.Dispatch(raw_sw)
                                except Exception:
                                    sw_app = win32com.client.Dispatch(raw_sw)
                                print("SolidWorks launched and bound successfully.", flush=True)
                                break
                            except Exception:
                                time.sleep(1.0)
                    except Exception as _launch_err:
                        print(f"Failed to launch SolidWorks: {_launch_err}", flush=True)
                else:
                    print(f"sldworks.exe not found at default path: {sldworks_exe}", flush=True)

            # 3-C. Fallback: GetObject
            if sw_app is None:
                try:
                    raw_sw = win32com.client.GetObject(Class="SldWorks.Application")
                    try:
                        import win32com.client.dynamic
                        sw_app = win32com.client.dynamic.Dispatch(raw_sw)
                    except Exception:
                        sw_app = win32com.client.Dispatch(raw_sw)
                    print("Bound via GetObject fallback.", flush=True)
                except Exception:
                    pass

            if sw_app is None:
                raise RuntimeError(
                    "SOLIDWORKS을 시작하거나 연결할 수 없습니다.\n"
                    "수동으로 SOLIDWORKS를 실행한 후 다시 시도해 주세요.\n"
                    "(Could not start or bind to SOLIDWORKS. Please launch it manually and retry.)"
                )
                
            sw_app.Visible = True  # Ensure visible
            
            # Document Type mapping
            if ext_lower == ".sldprt":
                doc_type = 1  # swDocPART
            elif ext_lower == ".sldasm":
                doc_type = 2  # swDocASSEMBLY
            elif ext_lower == ".slddrw":
                doc_type = 3  # swDocDRAWING
            else:
                raise ValueError(f"Unsupported file extension for diff: {ext}")
                
            # Helper to get the document title safely (handling property/method differences in early/late binding)
            def get_doc_title(model_doc):
                if not model_doc:
                    return ""
                try:
                    title_val = model_doc.GetTitle
                    return title_val() if callable(title_val) else title_val
                except Exception:
                    try:
                        return getattr(model_doc, 'GetTitle')
                    except Exception:
                        return ""
                        
            # Helper to open a file in SOLIDWORKS (visible, read-only)
            def open_document(file_path):
                print(f"Opening in SolidWorks: {file_path}", flush=True)
                error = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
                warning = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
                
                # Use robust open options based on document type
                # 1 = Silent, 2 = ReadOnly, 128 = AutoMissingComponentResolve
                # 32 = LoadModel (required for drawings/assemblies to resolve geometry)
                # 64 = IgnoreActivation (for parts, prevents parent assembly auto-loading)
                if doc_type == 3:  # swDocDRAWING
                    open_options = 1 | 32 | 2 | 128
                elif doc_type == 1:  # swDocPART
                    open_options = 1 | 64 | 2 | 128
                elif doc_type == 2:  # swDocASSEMBLY
                    open_options = 1 | 32 | 2 | 128
                else:
                    open_options = 2 | 128
                
                model = sw_app.OpenDoc6(file_path, doc_type, open_options, "", error, warning)
                if not model:
                    raise RuntimeError(f"Failed to open document in SOLIDWORKS: {os.path.basename(file_path)} (Error: {error.value})")
                
                opened_docs.append(model)
                return model
                
            # 4. Process based on document type
            if doc_type in (1, 2):  # PART / ASSEMBLY
                # ── 4-A. Load SOLIDWORKS Utilities add-in ──────────────────
                # The Utilities add-in is registered as CLSID {F80FA0F1-B13D-11d4-944A-000629992CFE}
                # and its loader DLL lives at <SW_install>\sldUtils\SwLoaderSw.dll
                print("Getting SOLIDWORKS Utilities add-in...", flush=True)
                sw_util = None

                # Strategy 1: GetAddInObject by ProgID (works if already loaded in SW)
                for _prog_id in ("Utilities.UtilitiesApp",
                                 "{F80FA0F1-B13D-11d4-944A-000629992CFE}"):
                    try:
                        sw_util = sw_app.GetAddInObject(_prog_id)
                        if sw_util:
                            print(f"GetAddInObject succeeded with '{_prog_id}'.", flush=True)
                            break
                    except Exception as _e1:
                        print(f"GetAddInObject('{_prog_id}') failed: {_e1}", flush=True)

                if not sw_util:
                    # Strategy 2: LoadAddIn using SwLoaderSw.dll (the real in-process loader)
                    print("Trying to load Utilities via SwLoaderSw.dll...", flush=True)
                    _sw_inst_dirs = [
                        r"C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS",
                        r"C:\Program Files (x86)\SOLIDWORKS Corp\SOLIDWORKS",
                    ]
                    # Also try to get the installation dir from SW itself
                    try:
                        _exe_path = sw_app.ExecutablePath  # e.g. C:\...\SLDWORKS.exe
                        _sw_inst_dirs.insert(0, os.path.dirname(_exe_path))
                    except Exception:
                        pass

                    for _inst_dir in _sw_inst_dirs:
                        _dll_path = os.path.join(_inst_dir, "sldUtils", "SwLoaderSw.dll")
                        if not os.path.exists(_dll_path):
                            print(f"DLL not found: {_dll_path}", flush=True)
                            continue
                        print(f"LoadAddIn: {_dll_path}", flush=True)
                        try:
                            sw_app.LoadAddIn(_dll_path)
                            time.sleep(2)  # give SW time to register
                            sw_util = sw_app.GetAddInObject("Utilities.UtilitiesApp")
                            if sw_util:
                                print("Utilities add-in loaded via SwLoaderSw.dll.", flush=True)
                                break
                            # Also retry with CLSID
                            sw_util = sw_app.GetAddInObject("{F80FA0F1-B13D-11d4-944A-000629992CFE}")
                            if sw_util:
                                print("Utilities add-in loaded (CLSID after LoadAddIn).", flush=True)
                                break
                        except Exception as _e2:
                            print(f"LoadAddIn attempt failed for {_dll_path}: {_e2}", flush=True)

                if not sw_util:
                    raise RuntimeError(
                        "SOLIDWORKS Utilities 애드인을 로드할 수 없습니다.\n"
                        "SolidWorks → 도구 → 애드인(Add-Ins) → 'SOLIDWORKS Utilities'를 체크한 후\n"
                        "다시 시도해 주세요."
                    )
                print("SOLIDWORKS Utilities add-in loaded.", flush=True)

                # ── 4-B. Get CompareGeometry tool interface ─────────────────
                print("Getting CompareGeometry tool interface...", flush=True)
                try:
                    # GetToolInterface(toolID, status) – toolID 2 = gtSwToolCompareGeometry
                    # long_status is an output parameter; use a simple list to capture it
                    _status_holder = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
                    sw_util_comp_geom = sw_util.GetToolInterface(2, _status_holder)
                    _status_val = _status_holder.value if hasattr(_status_holder, 'value') else 0
                except Exception as _e3:
                    print(f"GetToolInterface raised: {_e3}", flush=True)
                    sw_util_comp_geom = None
                    _status_val = -1

                if not sw_util_comp_geom:
                    raise RuntimeError(
                        f"CompareGeometry 인터페이스를 가져올 수 없습니다 (status={_status_val}).\n"
                        "SOLIDWORKS Utilities가 올바르게 설치되어 있는지 확인해 주세요."
                    )
                print(f"CompareGeometry interface obtained (status={_status_val}).", flush=True)

                if self.cancel_event.is_set():
                    return

                # ── 4-C. Pre-open both files so SW tracks them ──────────────
                # Opening first ensures they appear in the SW document list,
                # gives us model refs for cleanup, and allows CompareGeometry3
                # to use the already-open docs (avoids re-open conflicts).
                ref_path = os.path.normpath(theirs_temp_path)  # _THEIRS = Reference
                mod_path = os.path.normpath(ours_temp_path)    # _OURS   = Modified

                print(f"Opening reference doc: {ref_path}", flush=True)
                doc_ref = open_document(ref_path)   # added to opened_docs list
                print(f"Opening modified doc:  {mod_path}", flush=True)
                doc_mod = open_document(mod_path)   # added to opened_docs list

                if self.cancel_event.is_set():
                    return

                # Activate the reference (THEIRS) document as the active one
                error_act = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
                sw_app.ActivateDoc3(os.path.basename(ref_path), False, 2, error_act)
                time.sleep(0.5)

                # ── 4-D. Run CompareGeometry3 ────────────────────────────────
                # gtGdfError enum:  0=OK, 15=OpeningResultsFailed (comparison OK, UI failed)
                # gtGdfDifferenceStatus: 0=NoDifference, 1=Difference
                #
                # CompareGeometry3(File1, Config1, File2, Config2,
                #   Options, ReportOption, ReportPath,
                #   AddToBinder, Overwrite, VolDiffStatus, FaceDiffStatus)
                #
                # Options:      1 = gtGdfFaceAndVolumeCompare
                # ReportOption: 0 = gtResultNoReport
                #               1 = gtResultSaveReport
                #               2 = gtResultShowUI
                vol_status  = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
                face_status = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)

                print(f"CompareGeometry3: ref={ref_path}", flush=True)
                print(f"CompareGeometry3: mod={mod_path}", flush=True)
                print("Running CompareGeometry3 (ReportOption=ShowUI)...", flush=True)

                try:
                    res_comp = sw_util_comp_geom.CompareGeometry3(
                        ref_path, "",   # Reference (_THEIRS)
                        mod_path, "",   # Modified  (_OURS)
                        1,              # Options: gtGdfFaceAndVolumeCompare
                        2,              # ReportOption: gtResultShowUI
                        "",             # ReportPath
                        False,          # AddToBinder
                        True,           # Overwrite
                        vol_status,
                        face_status,
                    )
                    print(f"CompareGeometry3 returned: {res_comp} "
                          f"(vol={vol_status.value}, face={face_status.value})", flush=True)
                except Exception as _cg_err:
                    raise RuntimeError(f"CompareGeometry3 호출 중 오류: {_cg_err}")

                _vol  = vol_status.value
                _face = face_status.value
                _comparison_ran = (_vol in (0, 1)) and (_face in (0, 1))

                _err_names = {
                    0: "NoError",
                    1: "FileNotFound", 2: "FileNotSolidBody", 3: "DocOpenFailed",
                    4: "InvalidDocType", 5: "InvalidOptions", 6: "SaveReportFailed",
                    7: "AddToBinderFailed", 8: "FaceCompareNotDone",
                    9: "VolumeCompareNotDone", 10: "GeomCompNotDone",
                    11: "NoActiveDoc", 12: "NoSolidBodies",
                    13: "CreatingTempFileFailed", 14: "InvalidConfig",
                    15: "OpeningResultsFailed",
                }
                _err_name = _err_names.get(res_comp, f"Unknown({res_comp})")

                if res_comp == 0:
                    # Full success — SW opened the comparison result UI
                    # Keep temp files so the user can interact in SW
                    self.temp_files_to_clean = [ours_temp_path, theirs_temp_path]
                    if not self.cancel_event.is_set():
                        self.after(0, self._on_diff_success)

                elif res_comp == 15 and _comparison_ran:
                    # Comparison data computed OK but SW couldn't show results UI.
                    # Fallback: save an HTML report and open it in the browser.
                    print(f"res_comp=15 ({_err_name}): comparison data OK — saving HTML report...", flush=True)

                    _report_dir = os.path.normpath(
                        os.path.join(self.parent.workspace_path, ".backup",
                                     f"{base}_GeomCompare")
                    )
                    os.makedirs(_report_dir, exist_ok=True)

                    vol_status2  = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
                    face_status2 = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
                    _report_saved = False
                    try:
                        res2 = sw_util_comp_geom.CompareGeometry3(
                            ref_path, "",
                            mod_path, "",
                            1,              # gtGdfFaceAndVolumeCompare
                            1,              # gtResultSaveReport
                            _report_dir,    # folder to save report in
                            False,
                            True,
                            vol_status2,
                            face_status2,
                        )
                        print(f"SaveReport fallback returned: {res2} "
                              f"(vol={vol_status2.value}, face={face_status2.value})", flush=True)
                        # Check for any files in the report dir
                        _report_files = os.listdir(_report_dir) if os.path.isdir(_report_dir) else []
                        print(f"Report dir contents: {_report_files}", flush=True)
                        if _report_files:
                            _report_saved = True
                            # Open Explorer to show report
                            import subprocess as _sub
                            _sub.Popen(["explorer", _report_dir])
                    except Exception as _fb_err:
                        print(f"SaveReport fallback raised: {_fb_err}", flush=True)

                    # Build result summary
                    _diff_parts = []
                    if _vol == 1:
                        _diff_parts.append("• 부피(Volume): 차이 있음")
                    else:
                        _diff_parts.append("• 부피(Volume): 동일")
                    if _face == 1:
                        _diff_parts.append("• 면(Face): 차이 있음")
                    else:
                        _diff_parts.append("• 면(Face): 동일")
                    _summary_lines = "\n".join(_diff_parts)
                    _report_note = (f"\n\n리포트 저장 위치:\n{_report_dir}"
                                    if _report_saved
                                    else "\n\n(리포트 저장에 실패했습니다. SOLIDWORKS에서 두 파일이 열려 있습니다.)")
                    _msg = f"형상 비교 완료:\n{_summary_lines}{_report_note}"

                    self.temp_files_to_clean = [ours_temp_path, theirs_temp_path]
                    if not self.cancel_event.is_set():
                        self.after(0, lambda m=_msg: self._on_diff_success_with_msg(m))

                else:
                    # Genuine error
                    raise RuntimeError(
                        f"CompareGeometry3 실패: {_err_name} (코드={res_comp})\n"
                        f"VolStatus={_vol}, FaceStatus={_face}"
                    )

            elif doc_type == 3:  # DRAWING (.slddrw)
                model_theirs = open_document(theirs_temp_path)
                model_ours = open_document(ours_temp_path)
                
                if self.cancel_event.is_set():
                    return
                    
                # Get sheet counts
                try:
                    sheets_theirs_val = model_theirs.GetSheetNames
                    sheets_theirs = sheets_theirs_val() if callable(sheets_theirs_val) else sheets_theirs_val
                    num_sheets_theirs = len(sheets_theirs) if sheets_theirs else 1
                except Exception:
                    num_sheets_theirs = 1
                    
                try:
                    sheets_ours_val = model_ours.GetSheetNames
                    sheets_ours = sheets_ours_val() if callable(sheets_ours_val) else sheets_ours_val
                    num_sheets_ours = len(sheets_ours) if sheets_ours else 1
                except Exception:
                    num_sheets_ours = 1
                    
                num_sheets = min(num_sheets_theirs, num_sheets_ours)
                warning_msg = None
                if num_sheets_theirs != num_sheets_ours:
                    warning_msg = f"Sheet count mismatch! Ours: {num_sheets_ours}, Theirs: {num_sheets_theirs}. Only comparing first {num_sheets} sheet(s)."
                    print(warning_msg, flush=True)
                    
                # Save both as PDF
                print("Saving drawing versions as PDF...", flush=True)
                
                # Activate theirs first and save
                print("Activating reference drawing...", flush=True)
                error_act = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
                sw_app.ActivateDoc3(os.path.basename(theirs_temp_path), False, 2, error_act)
                time.sleep(1)
                print("Saving reference drawing to PDF...", flush=True)
                res_theirs = model_theirs.SaveAs3(theirs_pdf_path, 0, 9)
                time.sleep(2)
                
                # Activate ours and save
                print("Activating modified drawing...", flush=True)
                sw_app.ActivateDoc3(os.path.basename(ours_temp_path), False, 2, error_act)
                time.sleep(1)
                print("Saving modified drawing to PDF...", flush=True)
                res_ours = model_ours.SaveAs3(ours_pdf_path, 0, 9)
                time.sleep(2)
                
                # Release COM references before closing to prevent locks
                model_theirs = None
                model_ours = None
                
                # Close documents immediately to release files
                for model in opened_docs:
                    try:
                        title = get_doc_title(model)
                        print(f"Closing drawing: {title}", flush=True)
                        model = None
                        import gc
                        gc.collect()
                        pythoncom.CoCollectFreeUnusedLibraries()
                        sw_app.CloseDoc(title)
                    except Exception as close_err:
                        print(f"Failed to close doc: {close_err}", flush=True)
                opened_docs.clear()
                
                # Verify PDF creation
                if not os.path.exists(theirs_pdf_path) or not os.path.exists(ours_pdf_path):
                    raise RuntimeError("Failed to export drawing versions to PDF in SOLIDWORKS.")
                    
                # Clean up temporary slddrw files right away
                for path in [ours_temp_path, theirs_temp_path]:
                    if path and os.path.exists(path):
                        try:
                            os.remove(path)
                        except Exception:
                            pass
                ours_temp_path = None
                theirs_temp_path = None
                
                # Resolve ImageMagick path
                im_path = self.parent.load_imagemagick_path()
                if not os.path.exists(im_path):
                    im_path = "compare"  # Fallback to system path command
                    
                diff_images = []
                for i in range(num_sheets):
                    if self.cancel_event.is_set():
                        return
                        
                    if num_sheets == 1:
                        diff_img_path = os.path.join(backup_dir, f"{base}_DIFF.png")
                    else:
                        diff_img_path = os.path.join(backup_dir, f"{base}_DIFF_Page{i+1}.png")
                        
                    cmd_compare = [
                        im_path,
                        "-density", "300",
                        f"{ours_pdf_path}[{i}]",
                        f"{theirs_pdf_path}[{i}]",
                        "-fuzz", "5%",
                        "-metric", "AE",
                        "-highlight-color", "Red",
                        diff_img_path
                    ]
                    print(f"Running ImageMagick compare: {' '.join(cmd_compare)}", flush=True)
                    res_comp = subprocess.run(cmd_compare, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    
                    # ImageMagick compare returns:
                    # 0: identical, 1: different (expected success case), >= 2: error.
                    if res_comp.returncode >= 2:
                        err_out = res_comp.stderr.decode('utf-8', errors='replace')
                        raise RuntimeError(f"ImageMagick compare failed on page {i+1} (Code {res_comp.returncode}): {err_out}\nEnsure ImageMagick is correctly installed.")
                        
                    if not os.path.exists(diff_img_path):
                        raise RuntimeError(f"Compare did not generate the diff image for page {i+1}.")
                        
                    diff_images.append(diff_img_path)
                    
                # Success callback
                if not self.cancel_event.is_set():
                    self.after(0, lambda: self._on_drawing_diff_success(diff_images, warning_msg))
                    
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(f"Error in visual diff:\n{tb}", flush=True)
            if not self.cancel_event.is_set():
                # Capture e and tb as default args to avoid NameError when the
                # except-clause variable is cleared before the lambda executes.
                self.after(0, lambda _e=str(e), _tb=tb: self._on_diff_error(f"{_e}\n\n{_tb}"))
                
        finally:
            # Close documents opened during the diff (both via open_document()
            # and any documents CompareGeometry3 may have opened internally).
            if sw_app:
                # 1. Close docs we opened explicitly (tracked in opened_docs)
                for model in list(opened_docs):
                    try:
                        title = get_doc_title(model)
                        if title:
                            print(f"Closing tracked doc: {title}", flush=True)
                            sw_app.CloseDoc(title)
                    except Exception as _ce:
                        print(f"CloseDoc (tracked) failed: {_ce}", flush=True)
                opened_docs.clear()

                # 2. Also scan all open SW documents for our temp filenames
                # (handles docs CompareGeometry3 opened internally)
                _temp_basenames = set()
                for _tp in [ours_temp_path, theirs_temp_path]:
                    if _tp:
                        _temp_basenames.add(os.path.basename(_tp).lower())

                if _temp_basenames:
                    try:
                        _open_doc = sw_app.GetFirstDocument()
                        _visited = set()
                        while _open_doc:
                            try:
                                _title = get_doc_title(_open_doc)
                                if _title and _title not in _visited:
                                    _visited.add(_title)
                                    if _title.lower() in _temp_basenames:
                                        print(f"Closing SW-internal doc: {_title}", flush=True)
                                        sw_app.CloseDoc(_title)
                            except Exception:
                                pass
                            try:
                                _open_doc = _open_doc.GetNext()
                            except Exception:
                                break
                    except Exception as _scan_err:
                        print(f"Doc scan for cleanup failed: {_scan_err}", flush=True)

            # Clean up temporary PDFs
            for path in [ours_pdf_path, theirs_pdf_path]:
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception:
                        pass

            # Clean up drawing temporary CAD files if they exist and weren't cleaned
            if ext_lower == ".slddrw":
                for path in [ours_temp_path, theirs_temp_path]:
                    if path and os.path.exists(path):
                        try:
                            os.remove(path)
                        except Exception:
                            pass

            pythoncom.CoUninitialize()

            
    def _on_diff_success(self):
        self.lbl_status.config(text="Visual Diff successfully completed!", foreground="#10b981")
        self.btn_diff.state(["!disabled"])
        self.btn_exit.state(["!disabled"])

    def _on_diff_success_with_msg(self, msg):
        """Called when comparison ran OK but SW UI couldn't display results (e.g. code 15)."""
        self.lbl_status.config(text="Visual Diff 완료 (리포트 폴더 열림)", foreground="#10b981")
        self.btn_diff.state(["!disabled"])
        self.btn_exit.state(["!disabled"])
        messagebox.showinfo("Visual Diff 완료", msg, parent=self)


    def _on_drawing_diff_success(self, diff_images, warning_msg=None):
        status_text = "Visual Diff successfully completed!"
        if warning_msg:
            status_text += f" ({warning_msg})"
        self.lbl_status.config(text=status_text, foreground="#10b981")
        self.btn_diff.state(["!disabled"])
        self.btn_exit.state(["!disabled"])
        
        # Open the results popup window
        DrawingDiffResultsDialog(self, diff_images)
        
    def _on_diff_error(self, err_msg):
        self.lbl_status.config(text="Visual Diff failed.", foreground="#ef4444")
        self.btn_diff.state(["!disabled"])
        self.btn_exit.state(["!disabled"])
        messagebox.showerror("Visual Diff Error", err_msg, parent=self)
        
    def on_exit(self):
        self.cancel_event.set()
        self.grab_release()
        
        # Clean up any temporary files registered for cleanup
        if hasattr(self, 'temp_files_to_clean'):
            for path in self.temp_files_to_clean:
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception:
                        pass
                        
        self.destroy()


class DrawingDiffResultsDialog(tk.Toplevel):
    def __init__(self, parent, diff_images):
        super().__init__(parent)
        self.parent = parent
        self.diff_images = diff_images
        self.title("Drawing Diff Results")
        self.geometry("500x350")
        self.configure(bg="#f3f4f6")
        self.transient(parent)
        self.grab_set()
        
        # Header Label
        lbl_title = ttk.Label(self, text="Drawing Comparison Results", font=("TkDefaultFont", 12, "bold"), background="#f3f4f6")
        lbl_title.pack(anchor="w", padx=16, pady=(16, 4))
        
        lbl_desc = ttk.Label(self, text="Select a page to view the comparison image (Red highlights show differences):", font=("TkDefaultFont", 9), background="#f3f4f6", foreground="#4b5563")
        lbl_desc.pack(anchor="w", padx=16, pady=(0, 12))
        
        # Frame for Listbox and scrollbar
        list_frame = ttk.Frame(self)
        list_frame.pack(fill="both", expand=True, padx=16, pady=4)
        
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical")
        scrollbar.pack(side="right", fill="y")
        
        self.listbox = tk.Listbox(
            list_frame, 
            yscrollcommand=scrollbar.set, 
            font=("TkDefaultFont", 10),
            bg="#ffffff",
            fg="#1f2937",
            selectbackground="#059669",
            selectforeground="#ffffff",
            relief="flat",
            borderwidth=1
        )
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.listbox.yview)
        
        # Populate Listbox
        for img in self.diff_images:
            name = os.path.basename(img)
            self.listbox.insert("end", name)
            
        self.listbox.bind("<Double-Button-1>", self.on_view_selected)
        
        # Bottom Button Frame
        btn_frm = ttk.Frame(self)
        btn_frm.pack(fill="x", side="bottom", padx=16, pady=16)
        
        btn_view = ttk.Button(btn_frm, text="View Image", command=self.on_view_selected, style="Primary.TButton")
        btn_view.pack(side="right", padx=(8, 0))
        
        btn_close = ttk.Button(btn_frm, text="Close", command=self.destroy)
        btn_close.pack(side="right")
        
        # Center the window relative to parent
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")
        
    def on_view_selected(self, event=None):
        selected_indices = self.listbox.curselection()
        if not selected_indices:
            return
        idx = selected_indices[0]
        img_path = self.diff_images[idx]
        if os.path.exists(img_path):
            try:
                os.startfile(img_path)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to open image view:\n{e}", parent=self)
        else:
            messagebox.showerror("Error", f"Image file not found:\n{img_path}", parent=self)


class LfsCleanupWizardDialog(tk.Toplevel):
    def __init__(self, parent, git_service):
        super().__init__(parent)
        self.parent = parent
        self.git_service = git_service
        self.title("Git LFS Cache Cleanup Wizard")
        self.geometry("520x440")
        self.resizable(False, False)
        self.configure(bg="#f3f4f6")
        self.transient(parent)
        self.grab_set()
        
        self.lfs_dir = os.path.normpath(os.path.join(self.git_service.repo_path, ".git", "lfs", "objects"))
        self.unused_files = []
        self.total_size_bytes = 0
        self.freed_size_bytes = 0
        self.kept_count = 0
        self.total_count = 0
        
        self.create_widgets()
        
        # Center the window
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")
        
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        
        # Auto-run analysis when opened
        self.start_analysis()

    def create_widgets(self):
        # Description
        lbl_desc = ttk.Label(self, text="Cleans up the `.git/lfs/objects/` folder by deleting unused binaries.\nKeeps only LFS files from the last 2 commits (depth = 2) and current index.", 
                             wraplength=480, justify="left", style="TLabel", font="TkDefaultFont")
        lbl_desc.pack(padx=16, pady=(16, 12), anchor="w")
        
        # Card Frame
        card = ttk.Frame(self, style="Card.TFrame")
        card.pack(padx=16, pady=4, fill="both", expand=True)
        
        # Grid layout for stats inside the card
        stats_frm = tk.Frame(card, bg="#ffffff")
        stats_frm.pack(padx=16, pady=16, fill="both", expand=True)
        stats_frm.columnconfigure(0, weight=0)
        stats_frm.columnconfigure(1, weight=1)
        
        labels = [
            ("LFS Cache Path:", 0),
            ("Total Files in Cache:", 1),
            ("Total Cache Size:", 2),
            ("Kept Files (last 2 commits + index):", 3),
            ("Files to Delete:", 4),
            ("Estimated Space to Free:", 5)
        ]
        
        self.val_labels = {}
        for text, row in labels:
            lbl_name = tk.Label(stats_frm, text=text, bg="#ffffff", fg="#374151", font="TkDefaultFont", anchor="w")
            lbl_name.grid(row=row, column=0, sticky="w", pady=3)
            
            lbl_val = tk.Label(stats_frm, text="-", bg="#ffffff", fg="#1f2937", font="TkDefaultFont", anchor="w")
            lbl_val.grid(row=row, column=1, sticky="w", padx=10, pady=3)
            self.val_labels[text] = lbl_val
            
        self.val_labels["LFS Cache Path:"].config(text=self.lfs_dir, wraplength=320, justify="left")
        
        # Progress & Status Frame
        self.progress_frm = tk.Frame(self, bg="#f3f4f6")
        self.progress_frm.pack(padx=16, pady=8, fill="x")
        
        self.lbl_status = ttk.Label(self.progress_frm, text="Initializing...", style="TLabel", font="TkDefaultFont")
        self.lbl_status.pack(anchor="w")
        
        self.progress = ttk.Progressbar(self.progress_frm, mode="determinate", style="Custom.Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(4, 0))
        
        # Buttons Frame
        btn_frm = ttk.Frame(self, style="TFrame")
        btn_frm.pack(padx=16, pady=16, fill="x", side="bottom")
        
        self.btn_close = ttk.Button(btn_frm, text="Close", command=self.on_close)
        self.btn_close.pack(side="right", padx=(8, 0))
        
        self.btn_cleanup = ttk.Button(btn_frm, text="Cleanup Cache", style="Primary.TButton", command=self.start_cleanup, state="disabled")
        self.btn_cleanup.pack(side="right", padx=(8, 0))
        
        self.btn_analyze = ttk.Button(btn_frm, text="Re-Analyze", command=self.start_analysis, state="disabled")
        self.btn_analyze.pack(side="right")

    def start_analysis(self):
        self.btn_analyze.config(state="disabled")
        self.btn_cleanup.config(state="disabled")
        self.lbl_status.config(text="Analyzing LFS cache and commit history...")
        self.progress.config(mode="indeterminate")
        self.progress.start(10)
        
        threading.Thread(target=self.run_analysis, daemon=True).start()

    def run_analysis(self):
        try:
            if not os.path.exists(self.lfs_dir):
                def on_no_lfs():
                    self.progress.stop()
                    self.progress.config(mode="determinate", value=0)
                    self.lbl_status.config(text="No LFS cache directory found.")
                    self.btn_analyze.config(state="normal")
                    for k in self.val_labels:
                        if k != "LFS Cache Path:":
                            self.val_labels[k].config(text="0")
                self.parent.task_queue.put(('sw_status', None, on_no_lfs))
                return
                
            kept_oids = set()
            
            def collect_oids(ref=None):
                cmd = ["git", "lfs", "ls-files", "-l"]
                if ref:
                    cmd.append(ref)
                try:
                    out = self.git_service._run_lfs_cmd(cmd, check=False)
                    if out:
                        for line in out.splitlines():
                            line = line.strip()
                            if line:
                                parts = line.split(maxsplit=2)
                                if parts:
                                    oid = parts[0]
                                    if len(oid) == 64:
                                        kept_oids.add(oid)
                except Exception as e:
                    print(f"Error listing LFS files for {ref or 'index'}: {e}")
                    
            collect_oids() # Current index / working tree
            
            has_head = False
            try:
                self.git_service._run_lfs_cmd(["git", "rev-parse", "--verify", "HEAD"], check=True)
                has_head = True
            except Exception:
                pass
                
            if has_head:
                collect_oids("HEAD")
                has_parent = False
                try:
                    self.git_service._run_lfs_cmd(["git", "rev-parse", "--verify", "HEAD~1"], check=True)
                    has_parent = True
                except Exception:
                    pass
                if has_parent:
                    collect_oids("HEAD~1")
            
            all_lfs_files = []
            for root, dirs, files in os.walk(self.lfs_dir):
                for f in files:
                    if len(f) == 64:
                        path = os.path.join(root, f)
                        try:
                            size = os.path.getsize(path)
                            all_lfs_files.append((path, f, size))
                        except Exception:
                            pass
            
            self.unused_files = []
            self.total_size_bytes = 0
            unused_size_bytes = 0
            self.kept_count = 0
            self.total_count = len(all_lfs_files)
            
            for path, oid, size in all_lfs_files:
                self.total_size_bytes += size
                if oid in kept_oids:
                    self.kept_count += 1
                else:
                    self.unused_files.append((path, size))
                    unused_size_bytes += size
            
            def format_size(size_in_bytes):
                if size_in_bytes < 1024:
                    return f"{size_in_bytes} B"
                elif size_in_bytes < 1024 * 1024:
                    return f"{size_in_bytes / 1024:.2f} KB"
                elif size_in_bytes < 1024 * 1024 * 1024:
                    return f"{size_in_bytes / (1024 * 1024):.2f} MB"
                else:
                    return f"{size_in_bytes / (1024 * 1024 * 1024):.2f} GB"
            
            def on_done():
                self.progress.stop()
                self.progress.config(mode="determinate", value=0)
                self.lbl_status.config(text="Analysis complete. Ready to clean up.")
                
                self.val_labels["Total Files in Cache:"].config(text=f"{self.total_count}")
                self.val_labels["Total Cache Size:"].config(text=format_size(self.total_size_bytes))
                self.val_labels["Kept Files (last 2 commits + index):"].config(text=f"{self.kept_count}")
                self.val_labels["Files to Delete:"].config(text=f"{len(self.unused_files)}")
                self.val_labels["Estimated Space to Free:"].config(text=format_size(unused_size_bytes))
                
                self.btn_analyze.config(state="normal")
                if self.unused_files:
                    self.btn_cleanup.config(state="normal")
                else:
                    self.btn_cleanup.config(state="disabled")
                    self.lbl_status.config(text="Cache is already clean! No unused files to delete.")
                    
            self.parent.task_queue.put(('sw_status', None, on_done))
            
        except Exception as err:
            def on_fail(e_msg=str(err)):
                self.progress.stop()
                self.progress.config(mode="determinate", value=0)
                self.lbl_status.config(text=f"Analysis failed: {e_msg}")
                self.btn_analyze.config(state="normal")
            self.parent.task_queue.put(('sw_status', None, on_fail))

    def start_cleanup(self):
        if not self.unused_files:
            return
        
        confirm = messagebox.askyesno(
            "Confirm Cleanup",
            f"Are you sure you want to delete {len(self.unused_files)} unused LFS cache files?\n"
            f"This will free up local disk space. Deleted files can be re-downloaded if needed."
        )
        if not confirm:
            return
            
        self.btn_analyze.config(state="disabled")
        self.btn_cleanup.config(state="disabled")
        self.btn_close.config(state="disabled")
        self.lbl_status.config(text="Deleting unused LFS cache files...")
        self.progress.config(mode="determinate", value=0)
        
        threading.Thread(target=self.run_cleanup, daemon=True).start()

    def run_cleanup(self):
        deleted_count = 0
        self.freed_size_bytes = 0
        total_to_delete = len(self.unused_files)
        
        try:
            for idx, (path, size) in enumerate(self.unused_files):
                try:
                    if os.path.exists(path):
                        os.remove(path)
                        deleted_count += 1
                        self.freed_size_bytes += size
                except Exception as e:
                    print(f"Failed to delete {path}: {e}")
                
                if (idx + 1) % 10 == 0 or (idx + 1) == total_to_delete:
                    val = int(((idx + 1) / total_to_delete) * 100)
                    def update_progress(v=val, count=deleted_count):
                        self.progress.config(value=v)
                        self.lbl_status.config(text=f"Deleted {count}/{total_to_delete} files...")
                    self.parent.task_queue.put(('sw_status', None, update_progress))
            
            # Post-cleanup: clean up any empty folders under lfs_dir
            cleaned_dirs = 0
            for root, dirs, files in os.walk(self.lfs_dir, topdown=False):
                for d in dirs:
                    dir_path = os.path.join(root, d)
                    try:
                        if not os.listdir(dir_path):
                            os.rmdir(dir_path)
                            cleaned_dirs += 1
                    except Exception as e:
                        print(f"Failed to remove empty directory {dir_path}: {e}")
            
            def format_size(size_in_bytes):
                if size_in_bytes < 1024:
                    return f"{size_in_bytes} B"
                elif size_in_bytes < 1024 * 1024:
                    return f"{size_in_bytes / 1024:.2f} KB"
                elif size_in_bytes < 1024 * 1024 * 1024:
                    return f"{size_in_bytes / (1024 * 1024):.2f} MB"
                else:
                    return f"{size_in_bytes / (1024 * 1024 * 1024):.2f} GB"
            
            freed_str = format_size(self.freed_size_bytes)
            log_msg = f"🧹 LFS CACHE CLEANUP: Deleted {deleted_count} unused files. Freed {freed_str}."
            
            def on_done():
                self.progress.config(value=100)
                self.lbl_status.config(text=f"Cleanup complete! Freed {freed_str}.")
                messagebox.showinfo("Cleanup Complete", f"Successfully deleted {deleted_count} files.\nFreed {freed_str} of disk space.")
                
                self.parent.write_log(log_msg, "success")
                
                self.btn_analyze.config(state="normal")
                self.btn_cleanup.config(state="disabled")
                self.btn_close.config(state="normal")
                
                self.unused_files = []
                self.val_labels["Total Files in Cache:"].config(text=f"{self.kept_count}")
                self.val_labels["Total Cache Size:"].config(text=format_size(max(0, self.total_size_bytes - self.freed_size_bytes)))
                self.val_labels["Files to Delete:"].config(text="0")
                self.val_labels["Estimated Space to Free:"].config(text="0 B")
                
            self.parent.task_queue.put(('sw_status', None, on_done))
            
        except Exception as err:
            def on_fail(e_msg=str(err)):
                self.lbl_status.config(text=f"Cleanup failed: {e_msg}")
                self.btn_analyze.config(state="normal")
                self.btn_close.config(state="normal")
            self.parent.task_queue.put(('sw_status', None, on_fail))

    def on_close(self):
        if self.btn_close.cget("state") == "disabled":
            return
        self.grab_release()
        self.destroy()


def queue_during_bg_tasks(method):
    import functools
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        def task():
            method(self, *args, **kwargs)
        if self.bg_tasks_count > 0:
            action_name = method.__name__.replace("_", " ").title()
            if "Refresh" not in action_name:
                self.write_log(f"Working state: '{action_name}' queued and will start after the active process finishes.", "info")
            self.pending_button_tasks.append(task)
        else:
            task()
    return wrapper


class GIT4SWApp(tk.Tk):
    def __init__(self, workspace_path):
        super().__init__()
        self.workspace_path = workspace_path
        self.pending_button_tasks = []
        self.current_view_index = 0
        self.check_and_load_config()
        
        # Initialize Services
        self.git_service = GitService(self.workspace_path)
        self.sw_service = SolidWorksMonitorService()
        
        # Queue for thread communication
        self.task_queue = queue.Queue()
        
        # State tracking for branch switching
        self.is_switching_branch = False
        self.bg_tasks_count = 0
        self.last_active_branch = ""
        
        self.title("GIT4SW")
        self.geometry("1100x600")
        
        # Set window icon
        script_dir = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(script_dir, "GIT4SW.ico")
        if os.path.exists(icon_path):
            try:
                self.iconbitmap(default=icon_path)
            except Exception:
                try:
                    self.iconbitmap(icon_path)
                except Exception as e:
                    print(f"Failed to load icon: {e}")
                    
        self.configure(bg="#f3f4f6")
        
        # Stored auto sync configuration
        config_data = self.load_config_data()
        self.auto_sync_var = tk.BooleanVar(value=config_data.get("auto_sync", False))
        
        self.setup_styles()
        self.init_ui()
        
        # Track open files and locked files by us in SolidWorks for auto Lock/Unlock
        self.last_open_files = set()
        self.files_locked_by_us = set()
        
        # Monitor thread for SolidWorks active document status
        self.sw_monitor_active = True
        self.sw_monitor_thread = threading.Thread(target=self._monitor_sw_loop, daemon=True)
        self.sw_monitor_thread.start()
        
        # Read queue regularly
        self.after(100, self.process_queue)
        
        # Trigger Auto Sync after UI load
        self.after(500, self.trigger_auto_sync_if_enabled)
        
    def setup_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        
        # Configure Colors
        bg_color = "#f3f4f6"
        card_color = "#ffffff"
        accent_color = "#059669" # Deep Green (highly visible on light bg)
        text_color = "#1f2937"
        
        # Styles
        style.configure("TFrame", background=bg_color)
        style.configure("Card.TFrame", background=card_color, relief="solid", borderwidth=1, bordercolor="#e5e7eb")
        style.configure("TLabel", background=bg_color, foreground=text_color)
        style.configure("Card.TLabel", background=card_color, foreground=text_color)
        style.configure("Title.TLabel", background=bg_color, foreground=accent_color, font=("TkDefaultFont", 14, "bold"))
        style.configure("CardTitle.TLabel", background=card_color, foreground=accent_color, font=("TkDefaultFont", 11, "bold"))
        
        # Buttons
        style.configure("TButton", padding=6, background="#e5e7eb", foreground="#1f2937", borderwidth=0)
        style.map("TButton",
                  background=[("active", "#d1d5db"), ("disabled", "#f3f4f6")],
                  foreground=[("active", "#111827"), ("disabled", "#9ca3af")])
                  
        style.configure("Primary.TButton", padding=6, background="#059669", foreground="#ffffff", borderwidth=0)
        style.map("Primary.TButton",
                  background=[("active", "#047857"), ("disabled", "#f3f4f6")],
                  foreground=[("active", "#ffffff"), ("disabled", "#9ca3af")])
                  
        # [수정] Make my branch 전용 스타일 추가 (비활성화 시 배경 및 텍스트 모두 흰색)
        style.configure("MakeBranch.TButton", padding=6, background="#059669", foreground="#ffffff", borderwidth=0)
        style.map("MakeBranch.TButton",
                  background=[("active", "#047857"), ("disabled", "#ffffff")],
                  foreground=[("active", "#ffffff"), ("disabled", "#ffffff")])

        style.configure("Danger.TButton", padding=6, background="#ef4444", foreground="#ffffff", borderwidth=0)
        style.map("Danger.TButton",
                  background=[("active", "#dc2626"), ("disabled", "#fee2e2")],
                  foreground=[("active", "#ffffff"), ("disabled", "#f87171")])

        # BOM Button Style
        style.configure("BOM.TButton", padding=6, background="#2563eb", foreground="#ffffff", borderwidth=0)
        style.map("BOM.TButton",
                  background=[("active", "#1d4ed8"), ("disabled", "#e5e7eb")],
                  foreground=[("active", "#ffffff"), ("disabled", "#9ca3af")])

        # Diff Button Style
        style.configure("Diff.TButton", padding=6, background="#2563eb", foreground="#ffffff", borderwidth=0)
        style.map("Diff.TButton",
                  background=[("active", "#1d4ed8"), ("disabled", "#e5e7eb")],
                  foreground=[("active", "#ffffff"), ("disabled", "#9ca3af")])

        # Progressbar (Emerald green theme)
        style.configure("Custom.Horizontal.TProgressbar",
                        thickness=14,
                        troughcolor="#e5e7eb",    # progress bar background (light gray)
                        background="#059669",     # progress bar fill (emerald green)
                        lightcolor="#059669",
                        darkcolor="#059669",
                        bordercolor="#d1d5db")

        # Treeview (Modern styling)
        style.configure("Treeview", 
                        background=card_color, 
                        fieldbackground=card_color, 
                        foreground=text_color,
                        font="TkDefaultFont",
                        rowheight=20)
        style.map("Treeview", 
                  background=[("selected", "#e5e7eb")],
                  foreground=[("selected", "#111827")])
        style.configure("Treeview.Heading", 
                        background="#f3f4f6", 
                        foreground="#374151", 
                        font="TkDefaultFont",
                        borderwidth=1,
                        relief="flat")

        # Combobox (matches Entry / Card style)
        style.configure("TCombobox",
                        fieldbackground="#ffffff",
                        background="#e5e7eb",
                        foreground="#1f2937",
                        selectbackground="#d1fae5",
                        selectforeground="#065f46",
                        borderwidth=1,
                        relief="solid",
                        padding=4)
        style.map("TCombobox",
                  fieldbackground=[("readonly", "#ffffff"), ("disabled", "#f3f4f6")],
                  foreground=[("disabled", "#9ca3af")],
                  background=[("active", "#d1d5db"), ("readonly", "#e5e7eb")])
        # Combobox listbox colors
        self.option_add("*TCombobox*Listbox.background", "#ffffff")
        self.option_add("*TCombobox*Listbox.foreground", "#1f2937")
        self.option_add("*TCombobox*Listbox.selectBackground", "#d1fae5")
        self.option_add("*TCombobox*Listbox.selectForeground", "#065f46")

        # Entry (ensure no gray border/padding bleeds through on card backgrounds)
        style.configure("TEntry",
                        fieldbackground="#ffffff",
                        background="#ffffff",
                        foreground="#1f2937",
                        insertcolor="#1f2937",
                        borderwidth=1,
                        relief="solid",
                        padding=4)
        style.map("TEntry",
                  fieldbackground=[("disabled", "#f3f4f6"), ("readonly", "#f9fafb")],
                  foreground=[("disabled", "#9ca3af")])

        # TScrollbar (Modern flat styling)
        style.configure("TScrollbar",
                        troughcolor="#f3f4f6",      # Soft light track color
                        background="#d1d5db",       # Flat soft grey thumb
                        bordercolor="#f3f4f6",      # Same as track to hide border lines
                        lightcolor="#d1d5db",       # Same as thumb to make it flat
                        darkcolor="#d1d5db",        # Same as thumb to make it flat
                        arrowcolor="#4b5563",       # Subtle arrow color
                        gripcount=0,                # No vertical ridges
                        arrowsize=11)
        style.map("TScrollbar",
                  background=[("active", "#9ca3af"), ("disabled", "#f3f4f6")],
                  arrowcolor=[("active", "#111827")])

    def init_ui(self):
        # Master Layout (Sidebar + Stacked content frame)
        self.main_container = tk.Frame(self, bg="#f3f4f6")
        self.main_container.pack(fill="both", expand=True, side="top")
        
        # Log Box at bottom
        self.log_container = ttk.Frame(self, style="Card.TFrame")
        self.log_container.pack(fill="x", side="bottom", padx=0, pady=0)
        
        log_header = tk.Frame(self.log_container, bg="#ffffff")
        log_header.pack(fill="x", padx=12, pady=(6, 2))
        
        lbl_log_title = ttk.Label(log_header, text="System Log", style="CardTitle.TLabel")
        lbl_log_title.pack(side="left")
        

        
        btn_clear_log = tk.Button(
            log_header,
            text="Clear",
            command=self.clear_log,
            font=("TkDefaultFont", 9, "bold"),
            bg="#e5e7eb",
            fg="#1f2937",
            activebackground="#d1d5db",
            activeforeground="#111827",
            bd=0,
            relief="flat",
            padx=12,
            pady=3,
            cursor="hand2"
        )
        btn_clear_log.pack(side="right")
        
        self.btn_terminate = tk.Button(
            log_header,
            text="Terminate",
            command=self.terminate_git_operations,
            font=("TkDefaultFont", 9, "bold"),
            bd=0,
            relief="flat",
            padx=12,
            pady=3
        )
        self.btn_terminate.pack(side="right", padx=(0, 8))
        self.update_terminate_btn_state(False)
        
        # Status indicator: Red = Working/Busy, Green = Idle
        self.lbl_status_indicator = tk.Label(log_header, text="● Idle", fg="#10b981", bg="#ffffff", font="TkDefaultFont")
        self.lbl_status_indicator.pack(side="right", padx=(0, 15))
        
        log_body = tk.Frame(self.log_container, bg="#ffffff")
        log_body.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        
        self.txt_log = tk.Text(log_body, height=4, bg="#f9fafb", fg="#1f2937", font="TkDefaultFont",
                              relief="flat", highlightthickness=1, highlightbackground="#e5e7eb", highlightcolor="#059669")
        self.txt_log.pack(side="left", fill="both", expand=True)
        
        scrollbar = ttk.Scrollbar(log_body, orient="vertical", command=self.txt_log.yview)
        scrollbar.pack(side="right", fill="y")
        self.txt_log.config(yscrollcommand=scrollbar.set)
        self.txt_log.config(state="disabled")
        
        # 1. Sidebar Frame
        sidebar = tk.Frame(self.main_container, bg="#ffffff", width=200, bd=0)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        
        # Sidebar Font: 2 points larger than TkDefaultFont, bold
        default_f = tkfont.nametofont("TkDefaultFont")
        sz = default_f.cget("size")
        new_size = sz + 2 if sz > 0 else sz - 2
        self.sidebar_font = tkfont.Font(family=default_f.cget("family"), size=new_size, weight="bold")
        
        # Sidebar Menu Buttons
        self.btn_dash = tk.Button(sidebar, text=" 📊 Dashboard", fg="#374151", bg="#e5e7eb", activebackground="#d1d5db", activeforeground="#111827",
                               font=self.sidebar_font, bd=0, anchor="w", padx=20, command=lambda: self.switch_view(0))
        self.btn_dash.pack(fill="x", pady=(24, 4))
        
        self.btn_files = tk.Button(sidebar, text=" 📁 File Manager", fg="#374151", bg="#e5e7eb", activebackground="#d1d5db", activeforeground="#111827",
                               font=self.sidebar_font, bd=0, anchor="w", padx=20, command=lambda: self.switch_view(1))
        self.btn_files.pack(fill="x", pady=4)
        
        self.btn_history = tk.Button(sidebar, text=" 📜 History log", fg="#374151", bg="#e5e7eb", activebackground="#d1d5db", activeforeground="#111827",
                                font=self.sidebar_font, bd=0, anchor="w", padx=20, command=lambda: self.switch_view(2))
        self.btn_history.pack(fill="x", pady=4)
        
        # Preview Canvas Container (4:3 ratio) - hidden by default
        self.preview_container = tk.Frame(sidebar, bg="#ffffff", bd=0)
        self.preview_canvas = tk.Canvas(self.preview_container, width=180, height=135, bg="#ffffff", bd=0, highlightthickness=0, cursor="hand2")
        self.preview_canvas.pack(fill="both", expand=True)
        self.preview_canvas.bind("<Button-1>", self.on_preview_clicked)
               
        self.btn_about = tk.Button(sidebar, text=" 💬 About", fg="#374151", bg="#e5e7eb", activebackground="#d1d5db", activeforeground="#111827",
                               font=self.sidebar_font, bd=0, anchor="w", padx=20, command=lambda: self.switch_view(6))
        self.btn_about.pack(fill="x", side="bottom", pady=(4, 24))

        self.btn_help = tk.Button(sidebar, text=" 💡 Help", fg="#374151", bg="#e5e7eb", activebackground="#d1d5db", activeforeground="#111827",
                               font=self.sidebar_font, bd=0, anchor="w", padx=20, command=lambda: self.switch_view(5))
        self.btn_help.pack(fill="x", side="bottom", pady=4)

        self.btn_config = tk.Button(sidebar, text=" ⚙️ Config", fg="#374151", bg="#e5e7eb", activebackground="#d1d5db", activeforeground="#111827",
                                font=self.sidebar_font, bd=0, anchor="w", padx=20, command=lambda: self.switch_view(4))
        self.btn_config.pack(fill="x", side="bottom", pady=4)

        self.btn_maintainer = tk.Button(sidebar, text=" 👤 Maintainer", fg="#374151", bg="#e5e7eb", activebackground="#d1d5db", activeforeground="#111827",
                                    font=self.sidebar_font, bd=0, anchor="w", padx=20, command=lambda: self.switch_view(3))
        self.btn_maintainer.pack(fill="x", side="bottom", pady=4)
        
        # Divider Line
        divider = tk.Frame(self.main_container, bg="#e5e7eb", width=1)
        divider.pack(side="left", fill="y")
        
        # 2. Main content pages container (stacked widgets using Frame grid show/hide)
        self.content_frame = tk.Frame(self.main_container, bg="#f3f4f6")
        self.content_frame.pack(side="right", fill="both", expand=True)
        
        self.views = []
        self.views.append(self.create_dashboard_view())
        self.views.append(self.create_file_manager_view())
        self.views.append(self.create_history_view())
        self.views.append(self.create_maintainer_view())
        self.views.append(self.create_config_view())
        self.views.append(self.create_help_view())
        self.views.append(self.create_about_view())
        
        self.switch_view(0)

    def write_log(self, message, msg_type="info"):
        """Appends a message to the bottom log text box.
        msg_type can be: 'info', 'warning', 'error', 'success'
        """
        import threading
        if threading.current_thread() is not threading.main_thread():
            self.task_queue.put(('log', (message, msg_type), None))
            return

        import datetime
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        prefix = f"[{timestamp}] "
        
        # Map prefix style/color
        if msg_type == "success":
            tag = "🟢 SUCCESS: "
            tag_name = "success"
        elif msg_type == "error":
            tag = "🔴 ERROR: "
            tag_name = "error"
        elif msg_type == "warning":
            tag = "⚠️ WARNING: "
            tag_name = "warning"
        else:
            tag = "ℹ️ INFO: "
            tag_name = "info"
            
        self.txt_log.config(state="normal")
        # Insert timestamp with a neutral tag
        self.txt_log.insert("end", prefix, "timestamp")
        # Insert tag with colored style
        self.txt_log.insert("end", tag, tag_name)
        # Insert actual message
        self.txt_log.insert("end", f"{message}\n")
        
        # Configure tags (if not already done)
        self.txt_log.tag_config("timestamp", foreground="#6b7280")
        self.txt_log.tag_config("info", foreground="#3b82f6")
        self.txt_log.tag_config("success", foreground="#10b981")
        self.txt_log.tag_config("warning", foreground="#f59e0b")
        self.txt_log.tag_config("error", foreground="#ef4444")
        
        # Auto-scroll to end
        self.txt_log.see("end")
        self.txt_log.config(state="disabled")
        
    def clear_log(self):
        self.txt_log.config(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.config(state="disabled")

    def update_terminate_btn_state(self, is_enabled):
        if not hasattr(self, 'btn_terminate'):
            return
        if is_enabled:
            self.btn_terminate.config(
                state="normal",
                bg="#ef4444",      # Vibrant red background
                fg="#ffffff",      # White text
                activebackground="#dc2626",
                activeforeground="#ffffff",
                cursor="hand2"
            )
        else:
            self.btn_terminate.config(
                state="disabled",
                bg="#fee2e2",      # Very light red/pink background (visually light/disabled)
                fg="#f87171",      # Light red text
                cursor=""
            )

    def terminate_git_operations(self):
        self.write_log("🔴 Terminating active Git operations...", "warning")
        
        import os
        import subprocess
        
        terminated_pids = []
        
        # 1. Terminate manually tracked Popen processes from git_service
        try:
            from git_service import terminate_all_processes
            pids = terminate_all_processes()
            terminated_pids.extend(pids)
        except Exception as e:
            print(f"Error terminating tracked processes: {e}")
            
        # 2. Terminate any child processes of the current process via taskkill
        try:
            parent_pid = os.getpid()
            cmd = ["powershell", "-NoProfile", "-Command", 
                   f"Get-CimInstance Win32_Process -Filter 'ParentProcessId = {parent_pid}' | Select-Object -ExpandProperty ProcessId"]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            
            for line in res.stdout.strip().splitlines():
                line = line.strip()
                if line.isdigit():
                    child_pid = int(line)
                    # Forcefully terminate child process and all its children recursively
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(child_pid)], capture_output=True, check=False)
                    terminated_pids.append(child_pid)
        except Exception as e:
            print(f"Error terminating child processes via taskkill: {e}")
            
        # 3. Clean up the set of terminated PIDs
        terminated_pids = sorted(list(set(terminated_pids)))
        
        # 4. Print results
        if terminated_pids:
            pids_str = ", ".join(map(str, terminated_pids))
            self.write_log(f"🟢 Terminated {len(terminated_pids)} active subprocess tree(s) (PIDs: {pids_str}).", "success")
        else:
            self.write_log("ℹ️ No active Git processes found to terminate.", "info")
            
        # 5. Reset the background task counter and status indicator
        self.pending_button_tasks.clear()
        self.bg_tasks_count = 0
        self.lbl_status_indicator.config(text="● Idle", fg="#10b981")
        self.update_terminate_btn_state(False)
        
        # Re-enable all UI buttons
        is_repo = self.git_service.is_git_repo()
        self.btn_lock.config(text="Lock")
        if is_repo:
            self.btn_lock.state(["!disabled"])
            self.btn_unlock.state(["!disabled"])
            self.btn_force_unlock.state(["!disabled"])
            self.btn_save_ver.state(["!disabled"])
            self.btn_save_all.state(["!disabled"])
            self.btn_sync.state(["!disabled"])
            if hasattr(self, 'btn_cleanup_lfs'):
                self.btn_cleanup_lfs.state(["!disabled"])
            self.btn_merge.state(["!disabled"])
            self.btn_discard.state(["!disabled"])
        else:
            self.btn_lock.state(["disabled"])
            self.btn_unlock.state(["disabled"])
            self.btn_force_unlock.state(["disabled"])
            self.btn_save_ver.state(["disabled"])
            self.btn_save_all.state(["disabled"])
            self.btn_sync.state(["disabled"])
            if hasattr(self, 'btn_cleanup_lfs'):
                self.btn_cleanup_lfs.state(["disabled"])
            self.btn_merge.state(["disabled"])
            self.btn_discard.state(["disabled"])
            
        self.btn_save_ver.config(text="Upload Selected File Version")
        self.btn_save_all.config(text="Upload Every Files Version")
        self.btn_sync.config(text="Get Latest Version (Sync)")
        self.btn_restore.state(["!disabled"])
        self.btn_restore_latest.state(["!disabled"])
        self.btn_edrawings.state(["!disabled"])
        self.btn_solidworks.state(["!disabled"])
        
        # Refresh dashboard and file list
        self.refresh_dashboard()
        self.refresh_file_list()

    def update_repo_branch_info(self):
        if not self.git_service or not self.git_service.is_git_repo():
            self.title("GIT4SW")
            return
            
        repo_name = os.path.basename(self.workspace_path)
        
        # Get active branch name
        branch_name = self.git_service.get_current_branch()
        if not branch_name:
            # Maybe detached HEAD
            commit_hash = self.git_service.get_current_commit_hash()
            if commit_hash:
                branch_name = f"Detached: {commit_hash}"
            else:
                branch_name = "Unknown"
                
        self.title(f"{repo_name} @ {branch_name}")

    def increment_tasks(self):
        self.task_queue.put(('bg_task_start', None, None))

    def decrement_tasks(self):
        self.task_queue.put(('bg_task_end', None, None))

    def switch_view(self, index):
        self.current_view_index = index
        # Update sidebar button colors based on active view
        buttons = {
            0: self.btn_dash,
            1: self.btn_files,
            2: self.btn_history,
            3: self.btn_maintainer,
            4: self.btn_config,
            5: self.btn_help,
            6: self.btn_about
        }
        for idx, btn in buttons.items():
            if idx == index:
                btn.config(bg="#059669", fg="#ffffff", activebackground="#047857", activeforeground="#ffffff")
            else:
                btn.config(bg="#e5e7eb", fg="#374151", activebackground="#d1d5db", activeforeground="#111827")

        for idx, view in enumerate(self.views):
            if idx == index:
                view.pack(fill="both", expand=True)
                # Refresh views upon entry
                if idx == 0:
                    self.refresh_dashboard()
                elif idx == 1:
                    self.refresh_file_list()
                elif idx == 2:
                    self.refresh_history()
                elif idx == 4:
                    self.refresh_config_view()
            else:
                view.pack_forget()

        # Refresh CAD preview
        self.on_file_selected_change()

    def check_and_load_config(self):
        config_path = "config.json"
        template_path = "config.json.template"
        if not os.path.exists(config_path) and os.path.exists(template_path):
            try:
                with open(template_path, "r", encoding="utf-8") as f:
                    config_data = json.load(f)
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(config_data, f, indent=4)
                
                # If command line didn't pass a custom workspace, and template workspace path exists, update it
                import sys
                has_argv_override = False
                if len(sys.argv) > 1 and os.path.isdir(sys.argv[1]):
                    has_argv_override = True
                
                if not has_argv_override:
                    template_ws = config_data.get("workspace_path", "")
                    if template_ws and os.path.isdir(template_ws):
                        self.workspace_path = template_ws
            except Exception as e:
                print(f"Error copying template to config.json: {e}")

    def load_config_data(self):
        config_path = "config.json"
        template_path = "config.json.template"
        config_data = {}
        
        # 1. If config.json doesn't exist, read template
        if not os.path.exists(config_path):
            if os.path.exists(template_path):
                try:
                    with open(template_path, "r", encoding="utf-8") as f:
                        config_data = json.load(f)
                    # Save it immediately as config.json
                    with open(config_path, "w", encoding="utf-8") as f:
                        json.dump(config_data, f, indent=4)
                    self.write_log("config.json did not exist. Loaded from config.json.template and saved.", "info")
                except Exception as e:
                    self.write_log(f"Failed to load/apply from template: {e}", "error")
            else:
                self.write_log("Neither config.json nor config.json.template exists.", "error")
        else:
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config_data = json.load(f)
            except Exception as e:
                self.write_log(f"Failed to read config.json: {e}", "error")
                
        return config_data

    def create_config_view(self):
        view = ttk.Frame(self.content_frame)
        
        # Header Row
        header_frm = ttk.Frame(view)
        header_frm.pack(fill="x", padx=16, pady=10)
        lbl_title = ttk.Label(header_frm, text="Configuration Manager", style="Title.TLabel")
        lbl_title.pack(side="left")
        
        # Config Card
        card = ttk.Frame(view, style="Card.TFrame")
        card.pack(fill="both", expand=True, padx=16, pady=4)
        
        lbl_card_title = ttk.Label(card, text="Edit config.json variables", style="CardTitle.TLabel")
        lbl_card_title.pack(anchor="w", padx=16, pady=(12, 8))
        
        # Container to hold layout
        container = tk.Frame(card, bg="#ffffff")
        container.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        
        # Sub-frame for entry fields
        self.config_fields_frame = tk.Frame(container, bg="#ffffff")
        self.config_fields_frame.pack(fill="both", expand=True)
        
        # Save Button Frame at bottom
        btn_frm = tk.Frame(container, bg="#ffffff")
        btn_frm.pack(fill="x", side="bottom", pady=(16, 0))
        
        self.btn_save_config = ttk.Button(btn_frm, text="Save Configuration", style="Primary.TButton", command=self.save_config_from_view)
        self.btn_save_config.pack(side="left")
        
        self.config_entries = {}
        
        return view

    def refresh_config_view(self):
        # Clear existing widgets in self.config_fields_frame
        for widget in self.config_fields_frame.winfo_children():
            widget.destroy()
            
        self.config_entries.clear()
        
        config_data = self.load_config_data()
        if not config_data:
            return
            
        # Place label and entry widgets
        self.config_fields_frame.columnconfigure(0, weight=0)
        self.config_fields_frame.columnconfigure(1, weight=1)
        
        # Predefined order for keys (workspace_path is excluded as requested)
        keys_order = [
            "git_path",
            "git-lfs_path",
            "solidworks_path",
            "edrawings_path",
            "imagemagick_path",
            "github_token",
            "default_local_path",
            "organization_name"
        ]
        
        for k in config_data.keys():
            if k not in ("workspace_path", "auto_sync") and k not in keys_order:
                keys_order.append(k)
                
        for row_idx, key in enumerate(keys_order):
            if key not in config_data:
                continue
                
            val = config_data[key]
            display_name = key.replace("_", " ").title()
            
            # Label
            lbl = ttk.Label(self.config_fields_frame, text=f"{display_name}:", font=("TkDefaultFont", 9, "bold"), anchor="w", background="#ffffff")
            lbl.grid(row=row_idx, column=0, padx=(0, 10), pady=3, sticky="w")
            
            # Entry widget
            ent = ttk.Entry(self.config_fields_frame)
            ent.insert(0, str(val))
            ent.grid(row=row_idx, column=1, padx=0, pady=3, sticky="ew")
            
            # Save a reference to the entry
            self.config_entries[key] = ent

    @queue_during_bg_tasks
    def save_config_from_view(self):
        config_path = "config.json"
        
        # Read existing config to preserve any other keys
        config_data = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config_data = json.load(f)
            except Exception:
                pass
                
        # Update keys from entry fields
        for key, ent in self.config_entries.items():
            val = ent.get().strip()
            config_data[key] = val
            
        # Write to file
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config_data, f, indent=4)
                
            # Update app variables that depend on these configs
            if "workspace_path" in config_data:
                ws = config_data["workspace_path"]
                if os.path.isdir(ws):
                    if self.workspace_path != ws:
                        self.workspace_path = ws
                        self.write_log(f"Switched project workspace to: {ws}", "info")
            
            # Re-initialize GitService to apply git_path and git-lfs_path changes immediately
            self.git_service = GitService(self.workspace_path)
                    
            self.write_log("Configuration saved successfully to config.json", "success")
            
            # Refresh all views to show the new config
            self.refresh_dashboard()
            self.refresh_file_list()
            self.refresh_history()
            self.trigger_auto_sync_if_enabled()
            
        except Exception as e:
            self.write_log(f"Failed to save configuration: {e}", "error")

    def create_help_view(self):
        view = ttk.Frame(self.content_frame)
        
        # Header Row
        header_frm = ttk.Frame(view)
        header_frm.pack(fill="x", padx=16, pady=10)
        lbl_title = ttk.Label(header_frm, text="Help & Documentation", style="Title.TLabel")
        lbl_title.pack(side="left")
        
        # Card
        card = ttk.Frame(view, style="Card.TFrame")
        card.pack(fill="both", expand=True, padx=16, pady=4)
        
        # Text/Instructions Container
        container = tk.Frame(card, bg="#ffffff", height=380)
        container.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        container.pack_propagate(False)
        
        # Load help text from file
        script_dir = os.path.dirname(os.path.abspath(__file__))
        help_path = os.path.join(script_dir, "help.txt")
        help_text = ""
        if os.path.exists(help_path):
            try:
                with open(help_path, "r", encoding="utf-8") as f:
                    help_text = f.read()
            except Exception as e:
                help_text = f"Error loading help.txt:\n{e}"
        else:
            help_text = "help.txt file not found."
            
        txt_help = tk.Text(container, bg="#ffffff", fg="#1f2937", font="TkDefaultFont", wrap="word", relief="flat", height=15)
        txt_help.insert("1.0", help_text)
        txt_help.config(state="disabled")
        
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=txt_help.yview)
        txt_help.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        txt_help.pack(side="left", fill="both", expand=True)
        
        return view

    def create_about_view(self):
        view = ttk.Frame(self.content_frame)
        
        # Header Row
        header_frm = ttk.Frame(view)
        header_frm.pack(fill="x", padx=16, pady=10)
        lbl_title = ttk.Label(header_frm, text="About GIT4SW", style="Title.TLabel")
        lbl_title.pack(side="left")
        
        # Card
        card = ttk.Frame(view, style="Card.TFrame")
        card.pack(fill="both", expand=True, padx=16, pady=4)
        
        # Text/Instructions Container
        container = tk.Frame(card, bg="#ffffff", height=380)
        container.pack(fill="both", expand=True, padx=16, pady=16)
        container.pack_propagate(False)
        
        txt_about = tk.Text(container, bg="#ffffff", fg="#1f2937", font="TkDefaultFont", wrap="word", relief="flat", height=15)
        
        # Configure hyperlink tags
        txt_about.tag_config("link1", foreground="#2563eb", underline=1)
        txt_about.tag_bind("link1", "<Button-1>", lambda e: webbrowser.open_new("https://codeberg.org/dymaxionkim/GIT4SW"))
        txt_about.tag_bind("link1", "<Enter>", lambda e: txt_about.config(cursor="hand2"))
        txt_about.tag_bind("link1", "<Leave>", lambda e: txt_about.config(cursor=""))

        txt_about.tag_config("link2", foreground="#2563eb", underline=1)
        txt_about.tag_bind("link2", "<Button-1>", lambda e: webbrowser.open_new("https://youtu.be/SGs7_w_s2pI"))
        txt_about.tag_bind("link2", "<Enter>", lambda e: txt_about.config(cursor="hand2"))
        txt_about.tag_bind("link2", "<Leave>", lambda e: txt_about.config(cursor=""))
        
        # Load about text from file
        script_dir = os.path.dirname(os.path.abspath(__file__))
        about_path = os.path.join(script_dir, "about.txt")
        about_text = ""
        if os.path.exists(about_path):
            try:
                with open(about_path, "r", encoding="utf-8") as f:
                    about_text = f.read()
            except Exception as e:
                about_text = f"Error loading about.txt:\n{e}"
        else:
            about_text = "about.txt file not found."
            
        txt_about.insert("1.0", about_text)
        
        # Search for link URLs and apply tags to make them clickable
        links_info = [
            ("https://codeberg.org/dymaxionkim/GIT4SW", "link1"),
            ("https://youtu.be/SGs7_w_s2pI", "link2")
        ]
        for link_url, tag_name in links_info:
            start_idx = "1.0"
            while True:
                pos = txt_about.search(link_url, start_idx, stopindex=tk.END)
                if not pos:
                    break
                end_pos = f"{pos} + {len(link_url)}c"
                txt_about.tag_add(tag_name, pos, end_pos)
                start_idx = end_pos
        txt_about.config(state="disabled")
        
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=txt_about.yview)
        txt_about.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        txt_about.pack(side="left", fill="both", expand=True)
        
        return view

    # ==========================================
    # VIEW 1: DASHBOARD
    # ==========================================
    def create_dashboard_view(self):
        view = ttk.Frame(self.content_frame)
        
        # Top title
        lbl_title = ttk.Label(view, text="Dashboard", style="Title.TLabel")
        lbl_title.pack(anchor="w", padx=16, pady=10)
        
        # Repo Info Card
        repo_card = ttk.Frame(view, style="Card.TFrame")
        repo_card.pack(fill="x", padx=16, pady=4)
        
        lbl_repo_title = ttk.Label(repo_card, text="Repository Configuration", style="CardTitle.TLabel")
        lbl_repo_title.pack(anchor="w", padx=12, pady=(8, 2))
        
        # Local Path Frame with Change button
        local_frm = tk.Frame(repo_card, bg="#ffffff")
        local_frm.pack(fill="x", padx=12, pady=(2, 2))
        
        lbl_local_title = tk.Label(local_frm, text="Local Path:", bg="#ffffff", fg="#1f2937", width=15, anchor="w")
        lbl_local_title.pack(side="left")
        
        self.btn_change_ws = ttk.Button(local_frm, text="Change Workspace", command=self.change_workspace, width=18)
        self.btn_change_ws.pack(side="right", padx=(8, 0))
        
        self.ent_local_dir = ttk.Entry(local_frm)
        self.ent_local_dir.pack(side="left", fill="x", expand=True, padx=(8, 8))
        
        self.lbl_local_status = tk.Label(local_frm, text="", bg="#ffffff", fg="#059669", font=("TkDefaultFont", 9))
        self.lbl_local_status.pack(side="left", padx=(0, 8))
        
        # Remote Server Frame with Clone button
        remote_frm = tk.Frame(repo_card, bg="#ffffff")
        remote_frm.pack(fill="x", padx=12, pady=(2, 2))
        
        lbl_remote_title = tk.Label(remote_frm, text="Remote Server:", bg="#ffffff", fg="#1f2937", width=15, anchor="w")
        lbl_remote_title.pack(side="left")
        
        self.btn_clone = ttk.Button(remote_frm, text="Clone", command=self.clone_repository, width=18)
        self.btn_clone.pack(side="right", padx=(8, 0))
        
        self.ent_remote_url = ttk.Entry(remote_frm)
        self.ent_remote_url.pack(side="left", fill="x", expand=True, padx=(8, 8))
        
        # Branch Selection frame
        branch_frm = tk.Frame(repo_card, bg="#ffffff")
        branch_frm.pack(fill="x", padx=12, pady=(2, 8))
        
        lbl_branch = tk.Label(branch_frm, text="Active Branch:", bg="#ffffff", fg="#1f2937", width=15, anchor="w")
        lbl_branch.pack(side="left", padx=(0, 8))
        
        self.cb_branch = ttk.Combobox(branch_frm, state="readonly", width=30)
        self.cb_branch.pack(side="left", padx=(0, 8))
        self.cb_branch.bind("<<ComboboxSelected>>", self.on_branch_selected)
        
        # [수정] 기본 스타일을 전용 스타일인 "MakeBranch.TButton"으로 지정
        self.btn_make_my_branch = ttk.Button(branch_frm, text="Make my branch", command=self.make_my_branch, style="MakeBranch.TButton")
        self.btn_make_my_branch.pack(side="left")
        
        self.btn_readme = ttk.Button(branch_frm, text="README.md", command=self.open_readme, width=18)
        self.btn_readme.pack(side="right")
        
        # Sync Card
        sync_card = ttk.Frame(view, style="Card.TFrame")
        sync_card.pack(fill="x", padx=16, pady=4)
        
        lbl_sync_title = ttk.Label(sync_card, text="Synchronization", style="CardTitle.TLabel")
        lbl_sync_title.pack(anchor="w", padx=12, pady=(8, 2))
        
        lbl_sync_desc = ttk.Label(sync_card, text="Fetch the latest CAD documents from the remote Git server.", style="Card.TLabel")
        lbl_sync_desc.pack(anchor="w", padx=12, pady=1)
        
        sync_btn_frm = tk.Frame(sync_card, bg="#ffffff")
        sync_btn_frm.pack(fill="x", padx=12, pady=(4, 6))

        self.btn_sync = ttk.Button(sync_btn_frm, text="Get Latest Version (Sync)", style="Primary.TButton", command=self.sync_repository)
        self.btn_sync.pack(side="left", padx=(0, 8))

        self.btn_merge = ttk.Button(sync_btn_frm, text="Merge main branch into current branch", style="Primary.TButton", command=self.merge_main_branch)
        self.btn_merge.pack(side="left")
        
        self.chk_auto_sync = tk.Checkbutton(
            sync_btn_frm,
            text="Auto Sync",
            variable=self.auto_sync_var,
            command=self.on_auto_sync_changed,
            bg="#ffffff",
            fg="#1f2937",
            selectcolor="#ffffff",
            activebackground="#ffffff",
            activeforeground="#1f2937",
            highlightbackground="#ffffff",
            highlightcolor="#ffffff",
            font=("TkDefaultFont", 9, "bold"),
            bd=0,
            padx=10
        )
        self.chk_auto_sync.pack(side="left", padx=(12, 0))

        self.btn_cleanup_lfs = ttk.Button(sync_btn_frm, text="Cleanup LFS Cache", command=self.show_lfs_cleanup_wizard, width=18)
        self.btn_cleanup_lfs.pack(side="right")

        # SolidWorks Monitor Card
        sw_card = ttk.Frame(view, style="Card.TFrame")
        sw_card.pack(fill="x", padx=16, pady=4)
        
        lbl_sw_title = ttk.Label(sw_card, text="Live Monitor", style="CardTitle.TLabel")
        lbl_sw_title.pack(anchor="w", padx=12, pady=(8, 2))
        
        # Grid frame for side-by-side layout
        self.sw_status_grid = tk.Frame(sw_card, bg="#ffffff")
        self.sw_status_grid.pack(fill="x", padx=12, pady=(2, 8))
        self.sw_status_grid.columnconfigure(0, weight=1)
        self.sw_status_grid.columnconfigure(1, weight=1)
        
        # Left column labels
        self.lbl_sw_status_active = tk.Label(self.sw_status_grid, text="• SolidWorks Status: Inactive", bg="#ffffff", fg="#1f2937", anchor="w", font="TkDefaultFont")
        self.lbl_sw_status_active.grid(row=0, column=0, sticky="w", pady=0)
        
        self.lbl_sw_status_open = tk.Label(self.sw_status_grid, text="• Open Files: 0", bg="#ffffff", fg="#1f2937", anchor="w", font="TkDefaultFont")
        self.lbl_sw_status_open.grid(row=1, column=0, sticky="w", pady=0)
        
        self.lbl_sw_status_locked = tk.Label(self.sw_status_grid, text="• Locked Files: 0", bg="#ffffff", fg="#1f2937", anchor="w", font="TkDefaultFont")
        self.lbl_sw_status_locked.grid(row=2, column=0, sticky="w", pady=0)
        
        # Right column labels
        self.lbl_sw_status_total = tk.Label(self.sw_status_grid, text="• Total Files: 0", bg="#ffffff", fg="#1f2937", anchor="w", font="TkDefaultFont")
        self.lbl_sw_status_total.grid(row=0, column=1, sticky="w", pady=0)
        
        self.lbl_sw_status_repo_size = tk.Label(self.sw_status_grid, text="• Repository Size: -", bg="#ffffff", fg="#1f2937", anchor="w", font="TkDefaultFont")
        self.lbl_sw_status_repo_size.grid(row=1, column=1, sticky="w", pady=0)
        
        return view

    def refresh_dashboard(self):
        self.update_repo_branch_info()
        self.ent_local_dir.delete(0, tk.END)
        self.ent_local_dir.insert(0, os.path.normpath(self.workspace_path))
        
        if not self.git_service.is_git_repo():
            self.lbl_local_status.config(text="⚠️ Not a Git Repo", foreground="#ef4444")
            self.btn_sync.state(["disabled"])
            if hasattr(self, 'btn_cleanup_lfs'):
                self.btn_cleanup_lfs.state(["disabled"])
            self.btn_clone.state(["!disabled"])
            self.cb_branch.config(values=[], state="disabled")
            # [수정] 비활성화 시 전용 스타일 강제 적용
            self.btn_make_my_branch.config(style="MakeBranch.TButton", state="disabled")
        else:
            self.lbl_local_status.config(text="🟢 Git Repo Active", foreground="#10b981")
            url = self.git_service.get_remote_url()
            self.ent_remote_url.delete(0, tk.END)
            if url:
                import re
                clean_url = re.sub(r'^(https?://)[^@/]+@', r'\1', url)
                self.ent_remote_url.insert(0, clean_url)
            self.btn_sync.state(["!disabled"])
            if hasattr(self, 'btn_cleanup_lfs'):
                self.btn_cleanup_lfs.state(["!disabled"])
            self.btn_clone.state(["!disabled"])
            # [수정] 비활성화 시 전용 스타일 강제 적용
            self.btn_make_my_branch.config(style="MakeBranch.TButton", state="disabled")
            self.load_branches_in_combo()

    def load_branches_in_combo(self):
        if not self.git_service.is_git_repo():
            return
            
        old_branch = self.cb_branch.get()
        
        def run(old_val):
            try:
                branches = self.git_service.get_remote_branches()
                # Remove "HEAD" case-insensitively from branches list
                branches = [b for b in branches if b.upper() != "HEAD"]
                
                # Fetch local branches
                local_branches = self.git_service.get_local_branches()
                for lb in local_branches:
                    if lb.upper() != "HEAD" and lb not in branches:
                        branches.append(lb)
                
                current = self.git_service.get_current_branch()
                
                # If detached HEAD, try to find the corresponding branch
                is_detached = not current or current.upper() == "HEAD" or (self.git_service.repo and self.git_service.repo.head.is_detached)
                if is_detached:
                    try:
                        commit_hash = self.git_service.repo.head.commit.hexsha
                        containing_branches = self.git_service.get_branches_containing_commit(commit_hash)
                        if containing_branches:
                            if old_val in containing_branches:
                                current = old_val
                            elif "main" in containing_branches:
                                current = "main"
                            else:
                                current = sorted(containing_branches)[0]
                        else:
                            current = old_val
                    except Exception:
                        current = old_val
                
                # Make sure the current local branch is in the list (if it's not empty or HEAD)
                if current and current.upper() != "HEAD" and current not in branches:
                    branches.insert(0, current)
                    
                # Get username to determine if "Make my branch" button should be green or gray
                username = getattr(self, 'resolved_username', None)
                if not username:
                    config_path = "config.json"
                    token = ""
                    if os.path.exists(config_path):
                        try:
                            with open(config_path, "r", encoding="utf-8") as f:
                                config = json.load(f)
                                token = config.get("github_token", "")
                        except Exception:
                            pass
                    if token:
                        try:
                            from github import Github
                            g = Github(token)
                            user = g.get_user()
                            username = user.login
                        except Exception:
                            pass
                    if not username:
                        try:
                            import subprocess
                            res = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, check=True)
                            username = res.stdout.strip()
                        except Exception:
                            pass
                    if not username:
                        try:
                            username = os.getlogin()
                        except Exception:
                            pass
                    if username:
                        username = username.strip().replace(" ", "-").lower()
                        self.resolved_username = username
                        if self.git_service:
                            self.git_service.optimize_credential_helper(username)
                
                # Check if username branch exists
                branch_exists = False
                if username:
                    branch_exists = (username in local_branches) or (username in branches)
                
                def update_ui():
                    self.cb_branch.config(values=branches, state="readonly")
                    if current and current.upper() != "HEAD" and current in branches:
                        self.cb_branch.set(current)
                        self.last_active_branch = current
                    else:
                        self.cb_branch.set("")
                        
                    # [수정] 버튼의 상태에 관계없이 일관되게 전용 스타일(MakeBranch.TButton)을 유지하도록 변경
                    if username and not branch_exists:
                        self.btn_make_my_branch.config(style="MakeBranch.TButton", state="normal")
                    else:
                        self.btn_make_my_branch.config(style="MakeBranch.TButton", state="disabled")
                        
                    self.update_repo_branch_info()
                        
                self.task_queue.put(('sw_status', None, update_ui))
            except Exception as e:
                print(f"Error loading branches: {e}")
                
        threading.Thread(target=run, args=(old_branch,), daemon=True).start()

    def on_branch_selected(self, event):
        if getattr(self, 'is_switching_branch', False):
            print("DEBUG: Branch switch already in progress. Ignoring event.")
            return
            
        selected_branch = self.cb_branch.get()
        if not selected_branch:
            return
            
        current = self.git_service.get_current_branch()
        if selected_branch == current:
            return
            
        self.is_switching_branch = True
        self.cb_branch.config(state="disabled")
        
        def run(force=False):
            self.increment_tasks()
            print(f"DEBUG: run() thread started with force={force}")
            try:
                self.git_service.switch_branch(selected_branch, force=force)
                
                def on_done():
                    print("DEBUG: on_done() called in GUI thread")
                    self.is_switching_branch = False
                    self.refresh_dashboard()
                    self.refresh_file_list()
                    self.refresh_history()
                    old_display = current if current else "Detached HEAD"
                    self.write_log(f"🔄 BRANCH SWITCHED: [{old_display}] ➡️ [{selected_branch}]", "success")
                    
                self.task_queue.put(('sw_status', None, on_done))
            except Exception as e:
                err_msg = str(e)
                print(f"DEBUG: Exception in run() thread: {err_msg}")
                def on_fail():
                    print(f"DEBUG: on_fail() called in GUI thread with force={force}")
                    self.is_switching_branch = False
                    self.cb_branch.config(state="readonly")
                    self.cb_branch.set(current)
                    
                    err_msg_lower = err_msg.lower()
                    conflict_keywords = ["would be overwritten by checkout", "local changes to the following files", "local changes"]
                    is_conflict = any(kw in err_msg_lower for kw in conflict_keywords)
                    
                    print(f"DEBUG: is_conflict={is_conflict}, not force={not force}")
                    # Intercept local changes conflict error and offer force checkout ONLY if we didn't force it already
                    if not force and is_conflict:
                        ans_force = messagebox.askyesno(
                            "Local Changes Conflict",
                            f"Failed to switch branch due to uncommitted local changes.\n\n"
                            f"Would you like to FORCE switch to '{selected_branch}'?\n"
                            f"⚠️ Warning: This will discard ALL your uncommitted local changes."
                        )
                        if ans_force:
                            self.is_switching_branch = True
                            self.cb_branch.config(state="disabled")
                            threading.Thread(target=run, args=(True,), daemon=True).start()
                        return
                            
                    self.write_log(f"Failed to switch to branch {selected_branch}:\n{err_msg}", "error")
                self.task_queue.put(('sw_status', None, on_fail))
            finally:
                self.decrement_tasks()
                
        threading.Thread(target=run, args=(False,), daemon=True).start()

    @queue_during_bg_tasks
    def make_my_branch(self):
        if getattr(self, 'is_switching_branch', False):
            self.write_log("Operation already in progress.", "warning")
            return
            
        if not self.git_service.is_git_repo():
            self.write_log("Error: Not a Git repository.", "error")
            return
            
        self.is_switching_branch = True
        self.cb_branch.config(state="disabled")
        # [수정] 전용 스타일과 함께 비활성화 상태 부여
        self.btn_make_my_branch.config(style="MakeBranch.TButton", state="disabled")
        
        def run():
            self.increment_tasks()
            try:
                # 1. Resolve username from GitHub config
                username = None
                config_path = "config.json"
                token = ""
                if os.path.exists(config_path):
                    try:
                        with open(config_path, "r", encoding="utf-8") as f:
                            config = json.load(f)
                            token = config.get("github_token", "")
                    except Exception:
                        pass
                
                if token:
                    try:
                        self.write_log("Resolving account name from GitHub...", "info")
                        from github import Github
                        g = Github(token)
                        user = g.get_user()
                        username = user.login
                        self.write_log(f"GitHub account name resolved: {username}", "info")
                    except Exception as e:
                        self.write_log(f"GitHub resolution failed: {e}. Trying local Git config...", "warning")
                
                if not username:
                    # Fallback to local Git config user.name
                    try:
                        import subprocess
                        res = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, check=True)
                        username = res.stdout.strip()
                    except Exception:
                        pass
                        
                if not username:
                    # Fallback to os login name
                    try:
                        username = os.getlogin()
                    except Exception:
                        pass
                
                if not username:
                    username = "my-branch"
                
                # Sanitize branch name to a valid git branch name
                username = username.strip().replace(" ", "-").lower()
                self.resolved_username = username
                if self.git_service:
                    self.git_service.optimize_credential_helper(username)
                
                # 2. Check if branch exists
                repo = self.git_service.repo
                local_branches = self.git_service.get_local_branches()
                remote_branches = self.git_service.get_remote_branches()
                
                branch_exists = (username in local_branches) or (username in remote_branches)
                
                if branch_exists:
                    self.write_log(f"Branch '{username}' already exists. Switching to it...", "info")
                else:
                    self.write_log(f"Branch '{username}' does not exist. Creating new branch...", "info")
                    repo.create_head(username)
                    self.write_log(f"Created new branch '{username}' locally.", "success")
                
                # 3. Checkout/switch to branch
                self.git_service.switch_branch(username, force=False)
                
                # 4. Try to reflect branch to GitHub using github library and set upstream
                remote_url = self.git_service.get_remote_url()
                if remote_url and token:
                    try:
                        self.write_log(f"Creating remote branch '{username}' via GitHub API...", "info")
                        import re
                        from github import Github, GithubException
                        match = re.search(r"github\.com[:/]([^/]+)/([^/.]+)(?:\.git)?", remote_url)
                        if match:
                            owner = match.group(1)
                            repo_name = match.group(2)
                            g = Github(token)
                            gh_repo = g.get_repo(f"{owner}/{repo_name}")
                            head_commit_sha = repo.head.commit.hexsha
                            try:
                                gh_repo.create_git_ref(ref=f"refs/heads/{username}", sha=head_commit_sha)
                                self.write_log(f"Successfully created remote branch '{username}' via GitHub API.", "success")
                            except GithubException as ge:
                                if ge.status == 422:
                                    self.write_log(f"Remote branch '{username}' already exists on GitHub.", "info")
                                else:
                                    raise ge
                    except Exception as ge_err:
                        self.write_log(f"GitHub API branch creation failed: {ge_err}", "warning")
                
                # 5. Set upstream tracking in local git config via CLI
                if remote_url:
                    try:
                        self.write_log(f"Configuring local upstream tracking for '{username}'...", "info")
                        self.git_service._run_lfs_cmd(["git", "push", "-u", "origin", username])
                        self.write_log(f"Upstream tracking configured for branch '{username}'.", "success")
                    except Exception as pe:
                        self.write_log(f"Failed to configure upstream: {pe}", "warning")
                
                def on_done():
                    self.is_switching_branch = False
                    self.cb_branch.config(state="readonly")
                    self.refresh_dashboard()
                    self.refresh_file_list()
                    self.refresh_history()
                    self.write_log(f"🔄 Checked out branch '{username}' successfully.", "success")
                    
                self.task_queue.put(('sw_status', None, on_done))
                
            except Exception as e:
                err_msg = str(e)
                def on_fail():
                    self.is_switching_branch = False
                    self.cb_branch.config(state="readonly")
                    self.refresh_dashboard()
                    self.write_log(f"Failed to make/switch branch: {err_msg}", "error")
                self.task_queue.put(('sw_status', None, on_fail))
            finally:
                self.decrement_tasks()
                
        threading.Thread(target=run, daemon=True).start()

    # ==========================================
    # VIEW 2: FILE MANAGER
    # ==========================================
    def create_file_manager_view(self):
        view = ttk.Frame(self.content_frame)
        
        # Main Panel (File Table + Actions)
        main_panel = ttk.Frame(view)
        main_panel.pack(fill="both", expand=True, padx=16, pady=10)
        
        # Header Row
        header_frm = ttk.Frame(main_panel)
        header_frm.pack(fill="x", pady=(0, 6))
        lbl_file_title = ttk.Label(header_frm, text="File Check", style="Title.TLabel")
        lbl_file_title.pack(side="left", padx=(0, 20))
        btn_refresh = ttk.Button(header_frm, text="Refresh", command=self.refresh_file_list)
        btn_refresh.pack(side="right")
        btn_open = ttk.Button(header_frm, text="Open", command=self.open_workspace_in_explorer)
        btn_open.pack(side="right", padx=(0, 8))
        
        # Path filter combobox (placed to the left of "Open" button)
        self.cb_path_filter = ttk.Combobox(header_frm, state="readonly", width=55)
        self.cb_path_filter.pack(side="right", padx=(0, 8))
        self.cb_path_filter.config(values=["All Files"])
        self.cb_path_filter.set("All Files")
        self.cb_path_filter.bind("<<ComboboxSelected>>", self.on_path_filter_selected)
        
        # Sort order combobox (placed to the left of path filter)
        self.cb_sort_order = ttk.Combobox(header_frm, state="readonly", width=20, values=["by Name", "by Extension", "by Status", "by Solidworks", "by Locked"])
        self.cb_sort_order.pack(side="right", padx=(0, 8))
        self.cb_sort_order.set("by Name")
        self.cb_sort_order.bind("<<ComboboxSelected>>", self.on_sort_order_selected)
        
        # Table Scroll Frame
        table_frm = ttk.Frame(main_panel)
        table_frm.pack(fill="both", expand=True)
        
        # Multi selection checklist treeview (Custom color-coded table)
        self.tree = CustomFileTable(table_frm)
        self.tree.pack(fill="both", expand=True)
        
        # Actions Toolbar
        actions_frm = ttk.Frame(main_panel)
        actions_frm.pack(fill="x", pady=6)
        
        self.btn_lock = ttk.Button(actions_frm, text="Lock", style="Primary.TButton", width=6, command=self.lock_file)
        self.btn_lock.pack(side="left", padx=4)
        
        self.btn_unlock = ttk.Button(actions_frm, text="Unlock", width=8, command=self.unlock_file)
        self.btn_unlock.pack(side="left", padx=4)
        
        self.btn_force_unlock = ttk.Button(actions_frm, text="Force Unlock", style="Danger.TButton", width=12, command=self.force_unlock_file)
        self.btn_force_unlock.pack(side="left", padx=4)
        
        self.btn_discard = ttk.Button(actions_frm, text="Discard", style="Danger.TButton", width=8, command=self.discard_changes)
        self.btn_discard.pack(side="left", padx=4)
        
        self.btn_edrawings = ttk.Button(actions_frm, text="eDrawings", style="Primary.TButton", width=9, command=self.open_external_viewer)
        self.btn_edrawings.pack(side="left", padx=4)
        
        self.btn_solidworks = ttk.Button(actions_frm, text="Solidworks", style="Primary.TButton", width=10, command=self.open_solidworks)
        self.btn_solidworks.pack(side="left", padx=4)
        
        self.btn_export = ttk.Button(actions_frm, text="EXPORT", style="Primary.TButton", width=8, command=self.open_export_dialog)
        self.btn_export.pack(side="left", padx=4)
        
        self.btn_bom = ttk.Button(actions_frm, text="BOM", style="BOM.TButton", width=5, command=self.generate_bom_action)
        self.btn_bom.state(["disabled"])
        self.btn_bom.pack(side="left", padx=4)
        
        self.btn_diff = ttk.Button(actions_frm, text="Diff", width=8, command=self.show_diff_popup)
        self.btn_diff.state(["disabled"])
        self.btn_diff.pack(side="left", padx=4)
        
        self.lbl_selected_count = ttk.Label(actions_frm, text="Selected files: 0", style="TLabel")
        self.lbl_selected_count.pack(side="right", padx=8)
        
        # Bind tree selection event to update selected file count
        def update_selected_count(event=None):
            count = len(self.tree.selection())
            self.lbl_selected_count.config(text=f"Selected files: {count}")
            self.on_file_selected_change()
            
        self.tree.treeview.bind("<<TreeviewSelect>>", update_selected_count)
        
        # Save Version Card (Commit form)
        save_card = ttk.Frame(main_panel, style="Card.TFrame")
        save_card.pack(fill="x", pady=(6, 0))
        
        lbl_save_title = ttk.Label(save_card, text="Save Version & Upload (Check-in)", style="CardTitle.TLabel")
        lbl_save_title.pack(anchor="w", padx=8, pady=(4, 2))
        
        self.txt_message = tk.Text(save_card, height=2, bg="#ffffff", fg="#1f2937", insertbackground="#000000", relief="solid", bd=1, highlightthickness=0, font="TkDefaultFont")
        self.txt_message.pack(fill="x", padx=8, pady=2)
        
        btn_save_frm = ttk.Frame(save_card, style="TFrame")
        btn_save_frm.pack(fill="x", padx=8, pady=(2, 6))
        
        # Pack buttons first on the right side
        self.btn_save_all = ttk.Button(btn_save_frm, text="Upload Every Files Version", style="Primary.TButton", command=self.save_all_versions)
        self.btn_save_all.pack(side="right")
        
        self.btn_save_ver = ttk.Button(btn_save_frm, text="Upload Selected File Version", command=self.save_version)
        self.btn_save_ver.pack(side="right", padx=(0, 8))
        
        # Commit message selection combobox (stretched to the left end)
        self.cb_commit_msg = ttk.Combobox(btn_save_frm, state="readonly", postcommand=self.load_commit_messages)
        self.cb_commit_msg.set("")
        self.cb_commit_msg.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.cb_commit_msg.bind("<<ComboboxSelected>>", self.on_commit_msg_selected)
        
        # Load predefined commit messages from commit.json
        self.load_commit_messages()
        
        return view

    @queue_during_bg_tasks
    def refresh_file_list(self):
        if getattr(self, 'is_refreshing_file_list', False):
            return
            
        if not self.git_service.is_git_repo():
            for item in self.tree.get_children():
                self.tree.delete(item)
            return

        self.is_refreshing_file_list = True
        self.increment_tasks()

        # Carry over previous lock info to prevent visual flickering
        old_locks = {}
        if getattr(self, 'files_data', None):
            for f in self.files_data:
                old_locks[f['file'].lower().replace("\\", "/")] = {
                    'locked': f.get('locked', False),
                    'locked_by': f.get('locked_by', None),
                    'is_our_lock': f.get('is_our_lock', False)
                }

        def run():
            try:
                # 1. Fetch file list locally using locks={} to avoid network calls
                files_data = self.git_service.get_status(locks={})
                
                # Carry over previous lock info
                for f in files_data:
                    f_path_lower = f['file'].lower().replace("\\", "/")
                    if f_path_lower in old_locks:
                        f['locked'] = old_locks[f_path_lower]['locked']
                        f['locked_by'] = old_locks[f_path_lower]['locked_by']
                        f['is_our_lock'] = old_locks[f_path_lower]['is_our_lock']
                
                def update_gui():
                    self.files_data = files_data
                    
                    # Extract unique directories
                    dirs = set()
                    for file_info in files_data:
                        d = os.path.dirname(file_info['file']) or "."
                        dirs.add(d)
                        
                    sorted_dirs = ["All Files"] + sorted(list(dirs))
                    current_filter = self.cb_path_filter.get()
                    self.cb_path_filter.config(values=sorted_dirs)
                    if current_filter not in sorted_dirs:
                        self.cb_path_filter.set("All Files")
                    else:
                        self.cb_path_filter.set(current_filter)
                        
                    self.populate_file_table()
                            
                self.task_queue.put(('callback', None, update_gui))
                
                # 2. Trigger asynchronous background remote lock fetch
                def fetch_remote_locks():
                    try:
                        remote_locks = self.git_service.get_lfs_locks()
                        
                        # Sync file permissions on disk based on retrieved locks!
                        import stat
                        cleared_count = 0
                        marked_ro_count = 0
                        for rel_path, lock_info in remote_locks.items():
                            abs_path = os.path.abspath(os.path.join(self.workspace_path, rel_path))
                            if os.path.exists(abs_path):
                                try:
                                    mode = os.stat(abs_path).st_mode
                                    if lock_info.get('is_ours'):
                                        # Clear read-only (ensure S_IWRITE)
                                        if not (mode & stat.S_IWRITE):
                                            os.chmod(abs_path, mode | stat.S_IWRITE)
                                            cleared_count += 1
                                    else:
                                        # Make read-only (remove S_IWRITE)
                                        if (mode & stat.S_IWRITE):
                                            os.chmod(abs_path, mode & ~stat.S_IWRITE)
                                            marked_ro_count += 1
                                except Exception as ce:
                                    print(f"Failed to adjust attribute on '{abs_path}': {ce}")
                        
                        def update_locks_gui():
                            if not getattr(self, 'files_data', None):
                                return
                            
                            locks_lower = {k.lower().replace("\\", "/"): v for k, v in remote_locks.items()}
                            
                            for f in self.files_data:
                                f_path_lower = f['file'].lower().replace("\\", "/")
                                if f_path_lower in locks_lower:
                                    f['locked'] = True
                                    f['locked_by'] = locks_lower[f_path_lower]['owner']
                                    f['is_our_lock'] = locks_lower[f_path_lower]['is_ours']
                                else:
                                    f['locked'] = False
                                    f['locked_by'] = None
                                    f['is_our_lock'] = False
                                    
                            self.populate_file_table()
                            if cleared_count > 0 or marked_ro_count > 0:
                                self.write_log(
                                    f"Synced local file permissions: Cleared read-only on {cleared_count} files (locked by you), "
                                    f"marked {marked_ro_count} files as read-only (locked by others).",
                                    "success"
                                )
                            
                        self.task_queue.put(('callback', None, update_locks_gui))
                    except Exception as le:
                        print(f"Background locks fetch failed: {le}")
                
                threading.Thread(target=fetch_remote_locks, daemon=True).start()
                
            except Exception as e:
                def on_error():
                    self.write_log(f"Failed to load file list: {e}", "error")
                self.task_queue.put(('callback', None, on_error))
            finally:
                self.is_refreshing_file_list = False
                self.decrement_tasks()

        threading.Thread(target=run, daemon=True).start()

    def on_path_filter_selected(self, event):
        self.populate_file_table()

    def on_sort_order_selected(self, event):
        self.populate_file_table()

    def load_commit_messages(self):
        commit_messages = [""]
        commit_json_path = os.path.join(self.workspace_path, "commit.json")
        if not os.path.exists(commit_json_path):
            script_dir = os.path.dirname(os.path.abspath(__file__))
            commit_json_path = os.path.join(script_dir, "commit.json")
            
        loaded = False
        if os.path.exists(commit_json_path):
            for enc in ["utf-8-sig", "utf-8", "cp949", "euc-kr"]:
                try:
                    with open(commit_json_path, "r", encoding=enc) as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            data = [str(item) for item in data]
                            commit_messages.extend(data)
                            loaded = True
                            break
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                except Exception as e:
                    self.write_log(f"Error loading commit.json: {e}", "warning")
                    break

        if not loaded:
            if os.path.exists(commit_json_path):
                self.write_log("Could not parse commit.json. Using default templates.", "warning")
            commit_messages.extend([
                "대략설계 : ",
                "상세설계 : ",
                "디자인리뷰 : ",
                "도면작성 : ",
                "프로토타입 제작 : ",
                "프로토타입 수정보완 : ",
                "시험평가 : ",
                "생산이관 : "
            ])
            
        if hasattr(self, 'cb_commit_msg'):
            self.cb_commit_msg['values'] = commit_messages
            current = self.cb_commit_msg.get()
            if current not in commit_messages:
                self.cb_commit_msg.set("")

    def on_commit_msg_selected(self, event):
        selected = self.cb_commit_msg.get()
        self.txt_message.delete("1.0", tk.END)
        self.txt_message.insert("1.0", selected)

    def populate_file_table(self):
        # Save selection
        selected_paths = []
        for item in self.tree.selection():
            values = self.tree.item(item, 'values')
            if values:
                selected_paths.append(values[0])
                
        # Clear list
        for item in self.tree.get_children():
            self.tree.delete(item)
            
        if not getattr(self, 'files_data', None):
            return

        selected_path = self.cb_path_filter.get()
        if not selected_path:
            selected_path = "All Files"
            
        # 1. Filter files_data
        filtered_files = []
        for file_info in self.files_data:
            dir_name = os.path.dirname(file_info['file']) or "."
            if selected_path == "All Files" or dir_name == selected_path:
                filtered_files.append(file_info)

        # 2. Sort filtered_files
        sort_method = self.cb_sort_order.get()
        if sort_method == "by Extension":
            filtered_files.sort(key=lambda f: (os.path.splitext(f['file'])[1].lower(), f['file'].lower()))
        elif sort_method == "by Status":
            status_weights = {
                'untracked': 0,   # "New File"
                'modified': 1,    # "Modified"
                'unmodified': 2   # "Unmodified"
            }
            filtered_files.sort(key=lambda f: (status_weights.get(f['status'], 9), f['file'].lower()))
        elif sort_method == "by Solidworks":
            def get_sw_weight(f):
                for open_f in self.last_open_files:
                    if open_f.lower() == f['file'].lower():
                        return 0  # "Open" first
                return 1  # "—" next
            filtered_files.sort(key=lambda f: (get_sw_weight(f), f['file'].lower()))
        elif sort_method == "by Locked":
            def get_locked_owner(f):
                val = f.get('locked_by') if f.get('locked') else "—"
                return val if val else "—"
            filtered_files.sort(key=lambda f: (get_locked_owner(f).lower(), f['file'].lower()))
        else:  # "by Name"
            filtered_files.sort(key=lambda f: (os.path.basename(f['file']).lower(), f['file'].lower()))
            
        # 3. Populate tree
        for file_info in filtered_files:
            status_map = {
                'modified': '🟢 Modified',
                'untracked': '🔵 New File',
                'unmodified': '⚪ Unmodified'
            }
            status_text = status_map.get(file_info['status'], file_info['status'])
            
            is_open_in_sw = "—"
            for open_f in self.last_open_files:
                if open_f.lower() == file_info['file'].lower():
                    is_open_in_sw = "🟢 Open"
                    break
                    
            owner_text = file_info['locked_by'] if file_info['locked'] else "—"
            
            new_item = self.tree.insert("", "end", values=(
                file_info['file'],
                status_text,
                is_open_in_sw,
                owner_text
            ))
            
            if file_info['file'] in selected_paths:
                self.tree.selection_add(new_item)

        # Update the selected count label
        if hasattr(self, 'lbl_selected_count'):
            count = len(self.tree.selection())
            self.lbl_selected_count.config(text=f"Selected files: {count}")



    # Pre-check for SolidWorks files open/unsaved state
    def check_sw_open_state(self, file_rel_path):
        """
        Prompts user if file is open in SolidWorks.
        Called from background/worker threads.
        Uses cached `self.last_open_files` to check open status without calling COM.
        If the file is open, COM close is run in the worker thread.
        Any UI messagebox prompting is dispatched safely to the GUI thread.
        """
        # 1. Quick check using cached open files (runs instantly on worker thread)
        is_open = any(f.lower() == file_rel_path.lower() for f in self.last_open_files)
        if not is_open:
            return True # Not open, safe to proceed
            
        # 2. It is open. We need to query/close via COM in the background thread,
        # but the dialog must be shown on the GUI thread.
        def thread_safe_prompt(title, is_dirty):
            res_q = queue.Queue()
            
            def show_msgbox():
                dirty_msg = "\n⚠️ Warning: There are unsaved changes in SolidWorks." if is_dirty else ""
                msg = f"File '{title}' is open in SolidWorks.{dirty_msg}\n\nDo you want to Save & Close it before performing the Git operation?"
                ans = messagebox.askyesnocancel("SolidWorks File Active", msg)
                if ans is True: # Yes
                    res = 'save_and_close'
                elif ans is False: # No
                    ans2 = messagebox.askyesno("Confirm Close", "Close without saving? (Unsaved changes will be lost.)")
                    res = 'close_only' if ans2 else 'ignore'
                else: # Cancel
                    res = 'cancel'
                res_q.put(res)
                
            self.task_queue.put(('callback', None, show_msgbox))
            return res_q.get() # blocks background thread until GUI thread sets result
            
        return self.sw_service.check_and_close_file(file_rel_path, self.workspace_path, thread_safe_prompt)

    @queue_during_bg_tasks
    def lock_file(self):
        selected_items = self.tree.selection()
        if not selected_items:
            self.write_log("Select at least one file to lock.", "warning")
            return
            
        files_to_lock = [self.tree.item(item, 'values')[0] for item in selected_items]
        
        # Disable buttons on GUI thread
        self.btn_lock.config(text="Locking...")
        self.btn_lock.state(["disabled"])
        
        def run():
            self.increment_tasks()
            try:
                # Run check_sw_open_state in worker thread
                for file_rel_path in files_to_lock:
                    if not self.check_sw_open_state(file_rel_path):
                        return
                        
                success_count = 0
                errors = []
                for file_rel_path in files_to_lock:
                    try:
                        self.git_service.lock_file(file_rel_path)
                        
                        # Force clear read-only attribute on disk immediately after locking
                        abs_path = os.path.abspath(os.path.join(self.workspace_path, file_rel_path))
                        if os.path.exists(abs_path):
                            import stat
                            try:
                                mode = os.stat(abs_path).st_mode
                                os.chmod(abs_path, mode | stat.S_IWRITE)
                            except Exception as chmod_e:
                                print(f"Failed to clear read-only on locked file '{abs_path}': {chmod_e}")
                                
                        success_count += 1
                    except Exception as e:
                        errors.append(f"Failed to lock {file_rel_path}: {e}")
                
                if errors:
                    err_msg = "\n".join(errors)
                    self.task_queue.put(('error', f"Locked {success_count} files, but errors occurred:\n\n{err_msg}", None))
                else:
                    self.task_queue.put(('success', f"Locked {success_count} files successfully!", None))
            finally:
                self.decrement_tasks()
                
            import time
            time.sleep(1.5)
            self.task_queue.put(('callback', None, self.refresh_file_list))
                
        threading.Thread(target=run, daemon=True).start()

    @queue_during_bg_tasks
    def unlock_file(self):
        selected_items = self.tree.selection()
        if not selected_items:
            self.write_log("Select at least one file to unlock.", "warning")
            return
            
        files_to_unlock = [self.tree.item(item, 'values')[0] for item in selected_items]
        
        # Disable buttons on GUI thread
        self.btn_unlock.state(["disabled"])
        
        def run():
            self.increment_tasks()
            try:
                # Run check_sw_open_state in worker thread
                for file_rel_path in files_to_unlock:
                    if not self.check_sw_open_state(file_rel_path):
                        return
                        
                success_count = 0
                errors = []
                for file_rel_path in files_to_unlock:
                    # Check if file is locked by someone else
                    rel_path_lower = file_rel_path.lower().replace("\\", "/")
                    locked_by_others = False
                    if getattr(self, 'files_data', None):
                        for f in self.files_data:
                            if f['file'].lower().replace("\\", "/") == rel_path_lower:
                                if f.get('locked', False) and not f.get('is_our_lock', False):
                                    locked_by_others = True
                                break
                    if locked_by_others:
                        errors.append(f"Failed to unlock {file_rel_path}: Lock is held by another user.")
                        continue
                        
                    try:
                        self.git_service.unlock_file(file_rel_path)
                        
                        # Re-enable read-only attribute on disk immediately after unlocking
                        abs_path = os.path.abspath(os.path.join(self.workspace_path, file_rel_path))
                        if os.path.exists(abs_path):
                            import stat
                            try:
                                mode = os.stat(abs_path).st_mode
                                os.chmod(abs_path, mode & ~stat.S_IWRITE)
                            except Exception as chmod_e:
                                print(f"Failed to set read-only on unlocked file '{abs_path}': {chmod_e}")
                                
                        success_count += 1
                        
                        # Clean up our tracking set
                        matched_path = None
                        for f in self.files_locked_by_us:
                            if f.lower() == rel_path_lower:
                                matched_path = f
                                break
                        if matched_path:
                            self.files_locked_by_us.remove(matched_path)
                    except Exception as e:
                        errors.append(f"Failed to unlock {file_rel_path}: {e}")
                
                if errors:
                    err_msg = "\n".join(errors)
                    self.task_queue.put(('error', f"Unlocked {success_count} files, but errors occurred:\n\n{err_msg}", None))
                else:
                    self.task_queue.put(('success', f"Unlocked {success_count} files successfully!", None))
            finally:
                self.decrement_tasks()
                
            import time
            time.sleep(1.5)
            self.task_queue.put(('callback', None, self.refresh_file_list))
                
        threading.Thread(target=run, daemon=True).start()

    @queue_during_bg_tasks
    def force_unlock_file(self):
        selected_items = self.tree.selection()
        if not selected_items:
            self.write_log("Select at least one file to force unlock.", "warning")
            return
            
        files_to_unlock = [self.tree.item(item, 'values')[0] for item in selected_items]
        
        ans = messagebox.askyesno(
            "Confirm Force Unlock", 
            f"Are you sure you want to FORCE unlock the selected {len(files_to_unlock)} files?\n"
            "This might overwrite another user's modifications."
        )
        if not ans:
            return
            
        self.btn_force_unlock.state(["disabled"])
        
        def run():
            self.increment_tasks()
            try:
                # Run check_sw_open_state in worker thread
                for file_rel_path in files_to_unlock:
                    if not self.check_sw_open_state(file_rel_path):
                        return
                        
                success_count = 0
                errors = []
                for file_rel_path in files_to_unlock:
                    try:
                        self.git_service.unlock_file(file_rel_path, force=True)
                        
                        # Re-enable read-only attribute on disk immediately after force unlocking
                        abs_path = os.path.abspath(os.path.join(self.workspace_path, file_rel_path))
                        if os.path.exists(abs_path):
                            import stat
                            try:
                                mode = os.stat(abs_path).st_mode
                                os.chmod(abs_path, mode & ~stat.S_IWRITE)
                            except Exception as chmod_e:
                                print(f"Failed to set read-only on force unlocked file '{abs_path}': {chmod_e}")
                                
                        success_count += 1
                    except Exception as e:
                        errors.append(f"Failed to force unlock {file_rel_path}: {e}")
                
                if errors:
                    err_msg = "\n".join(errors)
                    self.task_queue.put(('error', f"Force unlocked {success_count} files, but errors occurred:\n\n{err_msg}", None))
                else:
                    self.task_queue.put(('success', f"Force unlocked {success_count} files successfully!", None))
            finally:
                self.decrement_tasks()
                
            import time
            time.sleep(1.5)
            self.task_queue.put(('callback', None, self.refresh_file_list))
                
        threading.Thread(target=run, daemon=True).start()

    @queue_during_bg_tasks
    def save_version(self):
        selected_items = self.tree.selection()
        if not selected_items:
            self.write_log("Select at least one file to upload/commit.", "warning")
            return
            
        files_to_save = [self.tree.item(item, 'values')[0] for item in selected_items]
        msg = self.txt_message.get("1.0", tk.END).strip()
        if not msg:
            self.write_log("Please write a description of the version changes.", "warning")
            return
            
        self.btn_save_ver.config(text="Uploading...")
        self.btn_save_ver.state(["disabled"])
        
        def run():
            self.increment_tasks()
            success = False
            try:
                # 1. Check open in SolidWorks
                try:
                    open_docs = self.sw_service.get_all_open_documents()
                except Exception:
                    open_docs = []
                
                repo_path_norm = os.path.abspath(self.workspace_path).replace("\\", "/")
                current_open_files = set()
                for doc in open_docs:
                    filepath = doc['path']
                    if filepath:
                        filepath_norm = os.path.abspath(filepath).replace("\\", "/")
                        if filepath_norm.lower().startswith(repo_path_norm.lower()):
                            rel_path = filepath_norm[len(repo_path_norm):].strip("/")
                            if rel_path:
                                corrected_path = self.git_service.get_correct_filepath_casing(rel_path)
                                current_open_files.add(corrected_path.lower())
                                
                open_targets = [f for f in files_to_save if f.lower() in current_open_files]
                if open_targets:
                    res_q = queue.Queue()
                    def show_sw_warning():
                        messagebox.showwarning(
                            "Warning",
                            "Warning: One or more target files are currently open in Solidworks. Upload cannot proceed."
                        )
                        res_q.put(True)
                    self.task_queue.put(('callback', None, show_sw_warning))
                    res_q.get()
                    return
                
                # 2. Check locks
                locks = self.git_service.get_lfs_locks()
                locks_lower = {k.lower(): v for k, v in locks.items()}
                
                locked_by_others = []
                for fp in files_to_save:
                    if fp.lower() in locks_lower:
                        if not locks_lower[fp.lower()]['is_ours']:
                            locked_by_others.append(fp)
                            
                files_to_commit = list(files_to_save)
                if locked_by_others:
                    res_q = queue.Queue()
                    def show_lock_warning():
                        ans = messagebox.askyesno(
                            "경고",
                            "경고: 대상 파일 중 하나 이상이 현재 다른 계정에 의하여 Locked되어 있습니다. 이 파일들을 제외하고 진행하겠습니까?"
                        )
                        res_q.put(ans)
                    self.task_queue.put(('callback', None, show_lock_warning))
                    proceed = res_q.get()
                    if not proceed:
                        return
                    else:
                        files_to_commit = [f for f in files_to_save if f not in locked_by_others]
                        if not files_to_commit:
                            res_q2 = queue.Queue()
                            def show_empty_warning():
                                messagebox.showinfo("No Files to Upload", "All target files were excluded. Upload cancelled.")
                                res_q2.put(True)
                            self.task_queue.put(('callback', None, show_empty_warning))
                            res_q2.get()
                            return
                
                import git
                # 1. Stage selected files
                rel_paths = []
                for fp in files_to_commit:
                    rel_path = self.git_service.get_correct_filepath_casing(fp)
                    rel_paths.append(rel_path)
                
                try:
                    self.git_service._run_lfs_cmd(["git", "add"] + rel_paths)
                except Exception as e:
                    raise RuntimeError(f"Failed to add files to index: {e}")
                
                # Commit locally first
                name = "SolidWorks Designer"
                email = "designer@example.com"
                try:
                    reader = self.git_service.repo.config_reader()
                    name = reader.get_value("user", "name", default="SolidWorks Designer")
                    email = reader.get_value("user", "email", default="designer@example.com")
                except Exception:
                    pass
                author = git.Actor(name, email)
                
                try:
                    import git.exc
                    try:
                        self.git_service.repo.index.commit(msg, author=author, committer=author)
                        self.write_log("Saved changes locally.", "success")
                    except git.exc.HookExecutionError as e:
                        if "post-commit" in str(e):
                            self.write_log("Saved changes locally (post-commit hook failed/ignored).", "warning")
                        else:
                            raise
                except Exception as e:
                    raise RuntimeError(f"Local commit failed: {e}")
                
                # 2. Pull & Conflict Resolution (if remote is configured)
                remote_url = self.git_service.get_remote_url()
                branch = self.git_service.get_current_branch()
                
                if remote_url and branch:
                    self.write_log("Fetching remote tracking branch...", "info")
                    try:
                        self.git_service._run_lfs_cmd(["git", "fetch", "origin"])
                        
                        conflicted_files = self.git_service.check_merge_conflicts(f"origin/{branch}")
                        if conflicted_files:
                            self.write_log(f"Conflicts pre-detected in {len(conflicted_files)} files! Showing resolution dialog...", "warning")
                            resolutions = self.prompt_multi_conflict_resolution(
                                conflicted_files,
                                is_pull=True
                            )
                            if resolutions is None:
                                self.write_log("Upload cancelled by user. Rolling back local commit...", "warning")
                                self.git_service._run_lfs_cmd(["git", "reset", "--soft", "HEAD~1"])
                                return
                            
                            self.write_log("Applying resolutions and completing sync...", "info")
                            self.git_service.sync_pull_with_resolutions(resolutions)
                        else:
                            self.write_log("No conflicts detected. Performing standard pull...", "info")
                            try:
                                self.git_service.sync_pull_clean()
                            except MergeConflictError as mce:
                                self.write_log("Merge conflict occurred during pull. Showing resolution dialog...", "warning")
                                resolutions = self.prompt_multi_conflict_resolution(
                                    mce.conflicted_files,
                                    is_pull=True
                                )
                                if resolutions is None:
                                    self.write_log("Upload cancelled by user. Aborting merge and rolling back local commit...", "warning")
                                    self.git_service.abort_merge()
                                    self.git_service._run_lfs_cmd(["git", "reset", "--soft", "HEAD~1"])
                                    return
                                
                                self.write_log("Applying resolutions to complete sync...", "info")
                                self.git_service.resolve_conflicts_and_commit(f"origin/{branch}", resolutions)
                            
                        # 3. Push to remote
                        self.write_log("Pushing committed changes to remote server...", "info")
                        self.git_service._run_lfs_cmd(["git", "push", "origin", branch])
                    except Exception as sync_err:
                        # If fetch/pull/push failed, rollback local commit too
                        self.write_log(f"Upload sync failed: {sync_err}. Rolling back local commit...", "error")
                        self.git_service._run_lfs_cmd(["git", "reset", "--soft", "HEAD~1"])
                        raise sync_err
                        
                # 4. Try to automatically unlock if they were ours
                for file_rel_path in files_to_commit:
                    try:
                        locks = self.git_service.get_lfs_locks()
                        matched_lock_path = None
                        for l_path in locks:
                            if l_path.lower() == file_rel_path.lower():
                                matched_lock_path = l_path
                                break
                        if matched_lock_path and locks[matched_lock_path]['is_ours']:
                            self.git_service.unlock_file(matched_lock_path)
                    except Exception:
                        pass
                        
                def on_done():
                    self.txt_message.delete("1.0", tk.END)
                    self.cb_commit_msg.set("")
                    
                self.task_queue.put(('success', f"Version saved and uploaded to server successfully for {len(files_to_commit)} files!", on_done))
                success = True
            except Exception as e:
                self.task_queue.put(('error', f"Upload failed:\n{e}", None))
            finally:
                self.decrement_tasks()
                
            if success:
                import time
                time.sleep(1.5)
                self.task_queue.put(('callback', None, self.refresh_file_list))
                
        threading.Thread(target=run, daemon=True).start()

    @queue_during_bg_tasks
    def save_all_versions(self):
        msg = self.txt_message.get("1.0", tk.END).strip()
        if not msg:
            self.write_log("Please write a description of the version changes.", "warning")
            return
            
        self.btn_save_ver.state(["disabled"])
        self.btn_save_all.config(text="Uploading All...")
        self.btn_save_all.state(["disabled"])
        
        def run():
            self.increment_tasks()
            try:
                # Get all changed files in the repo
                try:
                    all_changed = []
                    status_out = self.git_service._run_lfs_cmd(["git", "status", "--porcelain", "-u"])
                    for line in status_out.splitlines():
                        if len(line) >= 3:
                            filepath = line[3:].strip().replace("\\", "/")
                            if "\t" in filepath:
                                filepath = filepath.split("\t")[0].replace("\\", "/")
                            if filepath.startswith('"') and filepath.endswith('"'):
                                filepath = filepath[1:-1]
                                try:
                                    import codecs
                                    b, _ = codecs.escape_decode(bytes(filepath, "utf-8"))
                                    filepath = b.decode("utf-8").replace("\\", "/")
                                except Exception:
                                    pass
                            all_changed.append(filepath)
                except Exception as e:
                    self.task_queue.put(('error', f"Failed to get repository status: {e}", None))
                    return
                
                if not all_changed:
                    res_q = queue.Queue()
                    def show_no_changes():
                        messagebox.showinfo("No Changes", "No changes detected to upload.")
                        res_q.put(True)
                    self.task_queue.put(('callback', None, show_no_changes))
                    res_q.get()
                    return
                
                target_sw_files = [f for f in all_changed if os.path.splitext(f)[1].lower() in ('.sldprt', '.sldasm', '.slddrw')]
                
                # 1. Check open in SolidWorks
                try:
                    open_docs = self.sw_service.get_all_open_documents()
                except Exception:
                    open_docs = []
                
                repo_path_norm = os.path.abspath(self.workspace_path).replace("\\", "/")
                current_open_files = set()
                for doc in open_docs:
                    filepath = doc['path']
                    if filepath:
                        filepath_norm = os.path.abspath(filepath).replace("\\", "/")
                        if filepath_norm.lower().startswith(repo_path_norm.lower()):
                            rel_path = filepath_norm[len(repo_path_norm):].strip("/")
                            if rel_path:
                                corrected_path = self.git_service.get_correct_filepath_casing(rel_path)
                                current_open_files.add(corrected_path.lower())
                                
                open_targets = [f for f in target_sw_files if f.lower() in current_open_files]
                if open_targets:
                    res_q = queue.Queue()
                    def show_sw_warning():
                        messagebox.showwarning(
                            "Warning",
                            "Warning: One or more target files are currently open in Solidworks. Upload cannot proceed."
                        )
                        res_q.put(True)
                    self.task_queue.put(('callback', None, show_sw_warning))
                    res_q.get()
                    return
                
                # 2. Check locks
                locks = self.git_service.get_lfs_locks()
                locks_lower = {k.lower(): v for k, v in locks.items()}
                
                locked_by_others = []
                for fp in target_sw_files:
                    if fp.lower() in locks_lower:
                        if not locks_lower[fp.lower()]['is_ours']:
                            locked_by_others.append(fp)
                            
                files_to_stage = list(all_changed)
                if locked_by_others:
                    res_q = queue.Queue()
                    def show_lock_warning():
                        ans = messagebox.askyesno(
                            "경고",
                            "경고: 대상 파일 중 하나 이상이 현재 다른 계정에 의하여 Locked되어 있습니다. 이 파일들을 제외하고 진행하겠습니까?"
                        )
                        res_q.put(ans)
                    self.task_queue.put(('callback', None, show_lock_warning))
                    proceed = res_q.get()
                    if not proceed:
                        return
                    else:
                        files_to_stage = [f for f in all_changed if f not in locked_by_others]
                        if not files_to_stage:
                            res_q2 = queue.Queue()
                            def show_empty_warning():
                                messagebox.showinfo("No Files to Upload", "All target files were excluded. Upload cancelled.")
                                res_q2.put(True)
                            self.task_queue.put(('callback', None, show_empty_warning))
                            res_q2.get()
                            return
                
                import git
                if not self.git_service.is_git_repo():
                    raise RuntimeError("Not a git repository.")
                repo = self.git_service.repo
                
                # 1. Stage the files
                self.write_log("Staging changes via Git CLI...", "info")
                for chunk in [files_to_stage[i:i+100] for i in range(0, len(files_to_stage), 100)]:
                    self.git_service._run_lfs_cmd(["git", "add"] + chunk)
                
                # Get signature details from repo config or default
                name = "SolidWorks Designer"
                email = "designer@example.com"
                try:
                    reader = repo.config_reader()
                    name = reader.get_value("user", "name", default="SolidWorks Designer")
                    email = reader.get_value("user", "email", default="designer@example.com")
                except Exception:
                    pass
                author = git.Actor(name, email)
                
                # 2. Commit locally first
                self.write_log("Creating commit via GitPython...", "info")
                try:
                    import git.exc
                    try:
                        commit = repo.index.commit(msg, author=author, committer=author)
                        self.write_log(f"Created commit locally: {commit.hexsha[:7]}", "success")
                    except git.exc.HookExecutionError as e:
                        if "post-commit" in str(e):
                            commit = repo.head.commit
                            self.write_log(f"Created commit locally: {commit.hexsha[:7]} (post-commit hook failed/ignored)", "warning")
                        else:
                            raise
                except Exception as e:
                    raise RuntimeError(f"Local commit failed: {e}")
                
                # 3. Pull & Conflict Resolution (if remote configured)
                remote_url = self.git_service.get_remote_url()
                branch = self.git_service.get_current_branch()
                
                if remote_url and branch:
                    self.write_log("Fetching remote tracking branch...", "info")
                    try:
                        self.git_service._run_lfs_cmd(["git", "fetch", "origin"])
                        
                        conflicted_files = self.git_service.check_merge_conflicts(f"origin/{branch}")
                        if conflicted_files:
                            self.write_log(f"Conflicts pre-detected in {len(conflicted_files)} files! Showing resolution dialog...", "warning")
                            resolutions = self.prompt_multi_conflict_resolution(
                                conflicted_files,
                                is_pull=True
                            )
                            if resolutions is None:
                                self.write_log("Upload cancelled by user. Rolling back local commit...", "warning")
                                self.git_service._run_lfs_cmd(["git", "reset", "--soft", "HEAD~1"])
                                return
                            
                            self.write_log("Applying resolutions and completing sync...", "info")
                            self.git_service.sync_pull_with_resolutions(resolutions)
                        else:
                            self.write_log("No conflicts detected. Performing standard pull...", "info")
                            try:
                                self.git_service.sync_pull_clean()
                            except MergeConflictError as mce:
                                self.write_log("Merge conflict occurred during pull. Showing resolution dialog...", "warning")
                                resolutions = self.prompt_multi_conflict_resolution(
                                    mce.conflicted_files,
                                    is_pull=True
                                )
                                if resolutions is None:
                                    self.write_log("Upload cancelled by user. Aborting merge and rolling back local commit...", "warning")
                                    self.git_service.abort_merge()
                                    self.git_service._run_lfs_cmd(["git", "reset", "--soft", "HEAD~1"])
                                    return
                                
                                self.write_log("Applying resolutions to complete sync...", "info")
                                self.git_service.resolve_conflicts_and_commit(f"origin/{branch}", resolutions)
                            
                        # 4. Push to remote
                        self.write_log("Pushing committed changes to remote server...", "info")
                        self.git_service._run_lfs_cmd(["git", "push", "-u", "origin", branch])
                        self.write_log(f"Successfully pushed branch '{branch}' to remote server.", "success")
                    except Exception as sync_err:
                        self.write_log(f"Upload sync failed: {sync_err}. Rolling back local commit...", "error")
                        self.git_service._run_lfs_cmd(["git", "reset", "--soft", "HEAD~1"])
                        raise sync_err
                
                # Try to automatically unlock if they were ours
                sw_committed = [f for f in files_to_stage if os.path.splitext(f)[1].lower() in ('.sldprt', '.sldasm', '.slddrw')]
                for file_rel_path in sw_committed:
                    try:
                        locks = self.git_service.get_lfs_locks()
                        matched_lock_path = None
                        for l_path in locks:
                            if l_path.lower() == file_rel_path.lower():
                                matched_lock_path = l_path
                                break
                        if matched_lock_path and locks[matched_lock_path]['is_ours']:
                            self.git_service.unlock_file(matched_lock_path)
                    except Exception:
                        pass
                
                def on_done():
                    self.txt_message.delete("1.0", tk.END)
                    self.cb_commit_msg.set("")
                    self.refresh_file_list()
                    self.refresh_history()
                    self.load_branches_in_combo()
                    
                self.task_queue.put(('success', "Successfully saved all versions and uploaded to remote server!", on_done))
            except Exception as e:
                self.task_queue.put(('error', f"Upload all failed:\n{e}", None))
            finally:
                self.decrement_tasks()
                
        threading.Thread(target=run, daemon=True).start()

    # ==========================================
    # VIEW 3: HISTORY LOG
    # ==========================================
    def create_history_view(self):
        view = ttk.Frame(self.content_frame)
        
        # Header Row
        header_frm = ttk.Frame(view)
        header_frm.pack(fill="x", padx=16, pady=10)
        lbl_hist_title = ttk.Label(header_frm, text="Version History Log", style="Title.TLabel")
        lbl_hist_title.pack(side="left")
        btn_refresh = ttk.Button(header_frm, text="Refresh History", command=self.refresh_history)
        btn_refresh.pack(side="right")
        
        # Table
        table_frm = ttk.Frame(view)
        table_frm.pack(fill="both", expand=True, padx=16, pady=4)
        
        self.hist_tree = ttk.Treeview(table_frm, columns=("hash", "designer", "date", "desc"), show="headings", selectmode="browse")
        self.hist_tree.heading("hash", text="Version ID")
        self.hist_tree.heading("designer", text="Designer")
        self.hist_tree.heading("date", text="Date")
        self.hist_tree.heading("desc", text="Description")
        
        self.hist_tree.column("hash", width=100, anchor="center")
        self.hist_tree.column("designer", width=120, anchor="center")
        self.hist_tree.column("date", width=130, anchor="center")
        self.hist_tree.column("desc", width=400, anchor="w")
        
        # Bold tag for current checkout commit — inherit system default font, apply bold
        default_font = tkfont.nametofont("TkDefaultFont")
        bold_font = default_font.copy()
        bold_font.configure(weight="bold")
        self.hist_tree.tag_configure("current_commit", font=bold_font, foreground="#059669")
        self.hist_tree.bind("<Double-1>", self.on_history_double_click)
        
        vsb = ttk.Scrollbar(table_frm, orient="vertical", command=self.hist_tree.yview)
        self.hist_tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.hist_tree.pack(side="left", fill="both", expand=True)
        
        # Actions Row
        actions_frm = ttk.Frame(view)
        actions_frm.pack(fill="x", padx=16, pady=10)
        
        self.btn_restore = ttk.Button(actions_frm, text="Restore Selected Version", style="Primary.TButton", command=self.restore_version)
        self.btn_restore.pack(side="left", padx=4)
        
        self.btn_restore_latest = ttk.Button(actions_frm, text="Return to Latest Version", command=self.restore_latest)
        self.btn_restore_latest.pack(side="left", padx=4)
        
        self.btn_graph = ttk.Button(actions_frm, text="Graph", command=self.show_git_graph)
        self.btn_graph.pack(side="right", padx=4)
        
        self.btn_browse_graph = ttk.Button(actions_frm, text="Browse Graph", command=self.browse_git_graph)
        self.btn_browse_graph.pack(side="right", padx=4)
        
        return view

    def create_maintainer_view(self):
        view = ttk.Frame(self.content_frame)
        
        # Header Row
        header_frm = ttk.Frame(view)
        header_frm.pack(fill="x", padx=16, pady=10)
        lbl_title = ttk.Label(header_frm, text="Maintainer Operations", style="Title.TLabel")
        lbl_title.pack(side="left")
        
        # Make New Repository Card
        card = ttk.Frame(view, style="Card.TFrame")
        card.pack(fill="x", padx=16, pady=4)
        
        lbl_card_title = ttk.Label(card, text="Make New Repository", style="CardTitle.TLabel")
        lbl_card_title.pack(anchor="w", padx=12, pady=(8, 2))
        
        input_frm = tk.Frame(card, bg="#ffffff")
        input_frm.pack(fill="x", padx=12, pady=(2, 8))
        
        lbl_repo = ttk.Label(input_frm, text="New Repository Name:", style="Card.TLabel")
        lbl_repo.pack(side="left")
        
        self.ent_new_repo_name = ttk.Entry(input_frm)
        self.ent_new_repo_name.pack(side="left", fill="x", expand=True, padx=(8, 8))
        
        btn_make = ttk.Button(input_frm, text="Make", command=self.on_make_repo_clicked)
        btn_make.pack(side="right")
        
        # Maintain Card
        maintain_card = ttk.Frame(view, style="Card.TFrame")
        maintain_card.pack(fill="x", padx=16, pady=4)
        
        lbl_maintain_title = ttk.Label(maintain_card, text="Maintain", style="CardTitle.TLabel")
        lbl_maintain_title.pack(anchor="w", padx=12, pady=(8, 2))
        
        maintain_frm = tk.Frame(maintain_card, bg="#ffffff")
        maintain_frm.pack(fill="x", padx=12, pady=(2, 8))
        
        btn_merge_all = ttk.Button(maintain_frm, text="Merge all branches into main", style="Primary.TButton", command=self.on_merge_all_branches_clicked)
        btn_merge_all.pack(side="left")
        
        return view

    def _copy_template_dir(self, src, dest):
        import shutil
        if os.path.isdir(src):
            if not os.path.exists(dest):
                os.makedirs(dest)
            for item in os.listdir(src):
                s = os.path.join(src, item)
                d_name = ("." + item[1:]) if item.startswith("_") else item
                d = os.path.join(dest, d_name)
                self._copy_template_dir(s, d)
        else:
            shutil.copy2(src, dest)

    def backup_conflicted_files(self, conflicted_files):
        import datetime
        import shutil
        workspace_path = getattr(self.git_service, 'workspace_path', None)
        if not workspace_path:
            return
            
        backup_root = os.path.join(workspace_path, ".backup")
        try:
            os.makedirs(backup_root, exist_ok=True)
        except Exception as e:
            self.write_log(f"⚠️ Failed to create backup directory: {e}", "warning")
            return
            
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        
        for f_rel in conflicted_files:
            src_path = os.path.normpath(os.path.join(workspace_path, f_rel))
            if not os.path.exists(src_path):
                continue
                
            # backup filename format: base_filename_YYYYMMDD_HHMMSS.ext
            base_name, ext = os.path.splitext(os.path.basename(f_rel))
            backup_name = f"{base_name}_{timestamp}{ext}"
            dest_path = os.path.join(backup_root, backup_name)
            
            try:
                shutil.copy2(src_path, dest_path)
                self.write_log(f"💾 Auto-saved conflict backup: {f_rel} -> .backup/{backup_name}", "info")
            except Exception as copy_e:
                self.write_log(f"⚠️ Failed to backup {f_rel}: {copy_e}", "warning")

    def _prompt_resolve_conflict(self, filename, branch_name):
        self.backup_conflicted_files([filename])
        res_queue = queue.Queue()
        def ask():
            from tkinter import messagebox
            ans = messagebox.askyesnocancel(
                "Resolve Merge Conflict",
                f"Conflict detected in file: {filename}\n\n"
                f"Yes: Keep 'main' branch version (Ours)\n"
                f"No: Choose '{branch_name}' branch version (Theirs)\n"
                f"Cancel: Keep 'main' branch version"
            )
            if ans is True:
                res_queue.put('ours')
            elif ans is False:
                res_queue.put('theirs')
            else:
                res_queue.put('ours')
        self.after(0, ask)
        return res_queue.get()

    def prompt_multi_conflict_resolution(self, conflicted_files, ours_branch=None, theirs_branch=None, is_pull=False):
        self.backup_conflicted_files(conflicted_files)
        res_queue = queue.Queue()
        def ask():
            dialog = MultiConflictResolutionDialog(self, conflicted_files, ours_branch, theirs_branch, is_pull)
            self.wait_window(dialog)
            res_queue.put(dialog.result)
        self.after(0, ask)
        return res_queue.get()

    @queue_during_bg_tasks
    def on_merge_all_branches_clicked(self):
        from tkinter import messagebox
        ans = messagebox.askyesno(
            "Confirm Merge All",
            "Are you sure you want to merge all branches into the 'main' branch?\n\n"
            "⚠️ Caution: This will fetch all remote branch data, pull them locally, merge everything into 'main', and push the result back to origin."
        )
        if not ans:
            return
            
        if not self.git_service.is_git_repo():
            self.write_log("Error: Current workspace is not a valid Git repository.", "error")
            return
            
        original_branch = self.git_service.get_current_branch()
            
        config_path = "config.json"
        token = ""
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    token = config.get("github_token", "")
            except Exception:
                pass
                
        if not token:
            self.write_log("Error: GitHub token is missing in config.json.", "error")
            return
            
        self.write_log("🚀 Starting Maintainer Merge All Branches...", "info")
        self.increment_tasks()
        
        def run():
            import git
            import os
            
            repo_path = self.workspace_path
            try:
                repo = self.git_service.repo
                if not repo:
                    raise RuntimeError("Not a git repository.")
                
                # Fetch first
                self.write_log("Fetching latest branches from remote origin...", "info")
                repo.remotes.origin.fetch()
                
                # Collect all remote branch names
                remote_branches = []
                for ref in repo.references:
                    if ref.path.startswith('refs/remotes/origin/'):
                        branch_name = ref.path.replace('refs/remotes/origin/', '')
                        if branch_name.upper() != 'HEAD':
                            remote_branches.append(branch_name)
                            
                if not remote_branches:
                    raise RuntimeError("No remote branches found.")
                    
                self.write_log(f"Found remote branches: {remote_branches}", "info")
                
                # Ensure we have local counterparts for all remote branches,
                # force-resetting any that already exist to match the remote ref.
                # Skip the currently checked-out branch (cannot force-reset it while checked out).
                current_branch_now = self.git_service.get_current_branch() or ""
                for b in remote_branches:
                    if b == current_branch_now:
                        continue
                    try:
                        self.git_service._run_lfs_cmd(["git", "branch", "-f", b, f"origin/{b}"])
                        self.write_log(f"Local branch '{b}' set to track origin/{b}", "info")
                    except Exception as be:
                        self.write_log(f"Warning: could not reset branch '{b}' to origin/{b}: {be}", "warning")
                        
                other_branches = [b for b in remote_branches if b != 'main']
                
                # Sequentially checkout other branches and fetch+merge origin/b
                for b in other_branches:
                    self.write_log(f"Checking out and updating branch '{b}'...", "info")
                    repo.git.checkout(b)
                    
                    # Fetch remote then merge, always using merge strategy with -Xtheirs
                    try:
                        self.git_service._run_lfs_cmd(["git", "fetch", "origin", b])
                        self.git_service._run_lfs_cmd(["git", "merge", f"origin/{b}", "--no-edit", "-Xtheirs"])
                        self.write_log(f"Merged remote origin/{b} into local '{b}' successfully.", "success")
                    except Exception as pull_err:
                        self.write_log(f"Warning: merge for '{b}' failed, attempting to resolve conflicts: {pull_err}", "warning")
                        # If merge failed due to unresolved conflicts, resolve using theirs
                        conflicted_paths = self.git_service.get_merge_conflicts()
                        if conflicted_paths:
                            for f in conflicted_paths:
                                try:
                                    self.git_service._run_lfs_cmd(["git", "checkout", "--theirs", "--", f])
                                    self.git_service._run_lfs_cmd(["git", "add", f])
                                except Exception:
                                    pass
                            try:
                                self.git_service._run_lfs_cmd(["git", "commit", "-m", f"Merge remote branch origin/{b} (resolved using remote)"])
                                self.write_log(f"Resolved conflicts for '{b}' (kept remote version).", "success")
                            except Exception as commit_err:
                                raise RuntimeError(f"Failed to merge remote branch for '{b}': {commit_err}")
                        else:
                            raise pull_err
                
                # Switch to main and fetch+merge origin/main
                self.write_log("Switching to main branch and updating from origin/main...", "info")
                repo.git.checkout("main")
                try:
                    self.git_service._run_lfs_cmd(["git", "fetch", "origin", "main"])
                    self.git_service._run_lfs_cmd(["git", "merge", "origin/main", "--no-edit", "-Xtheirs"])
                    self.write_log("main branch is up to date / merged remote updates.", "success")
                except Exception as pull_err:
                    self.write_log(f"Warning: merge for main failed, attempting auto-resolution: {pull_err}", "warning")
                    conflicted_paths = self.git_service.get_merge_conflicts()
                    if conflicted_paths:
                        for f in conflicted_paths:
                            try:
                                self.git_service._run_lfs_cmd(["git", "checkout", "--theirs", "--", f])
                                self.git_service._run_lfs_cmd(["git", "add", f])
                            except Exception:
                                pass
                        try:
                            self.git_service._run_lfs_cmd(["git", "commit", "-m", "Merge remote branch origin/main (resolved using remote)"])
                            self.write_log("Resolved conflicts for main (kept remote version).", "success")
                        except Exception as commit_err:
                            raise RuntimeError(f"Failed to merge remote main branch: {commit_err}")
                    else:
                        raise pull_err
                        
                # Merge other local branches into main one by one
                for b in other_branches:
                    try:
                        main_commit = repo.commit("main")
                        b_commit = repo.commit(b)
                        if repo.is_ancestor(b_commit, main_commit):
                            self.write_log(f"Branch '{b}' is already identical or merged into main. Skipping merge.", "info")
                            continue
                    except Exception as e_sha:
                        self.write_log(f"Warning: Could not compare commits for '{b}' and 'main': {e_sha}", "warning")

                    self.write_log(f"Merging local branch '{b}' into main...", "info")
                    conflicted_files = self.git_service.check_merge_conflicts(b)
                    if conflicted_files:
                        self.write_log(f"Conflicts pre-detected while merging '{b}' into main! Showing resolution dialog...", "warning")
                        resolutions = self.prompt_multi_conflict_resolution(
                            conflicted_files,
                            ours_branch="main",
                            theirs_branch=b,
                            is_pull=False
                        )
                        if resolutions is None:
                            self.write_log(f"Merge of branch '{b}' cancelled by user. Aborting...", "warning")
                            raise RuntimeError(f"Merge cancelled by user on branch '{b}'.")
                            
                        self.write_log(f"Merging branch '{b}' with resolutions...", "info")
                        self.git_service.merge_branch_with_resolutions(b, resolutions)
                        self.write_log(f"Branch '{b}' merged into main successfully with resolutions.", "success")
                    else:
                        self.write_log(f"No conflicts detected. Performing standard merge for branch '{b}'...", "info")
                        try:
                            self.git_service.merge_branch(b)
                            self.write_log(f"Branch '{b}' merged into main successfully.", "success")
                        except MergeConflictError as mce:
                            self.write_log(f"Merge conflict occurred while merging '{b}' into main. Showing resolution dialog...", "warning")
                            resolutions = self.prompt_multi_conflict_resolution(
                                mce.conflicted_files,
                                ours_branch="main",
                                theirs_branch=b,
                                is_pull=False
                            )
                            if resolutions is None:
                                self.write_log(f"Merge of branch '{b}' cancelled by user. Aborting...", "warning")
                                self.git_service.abort_merge()
                                raise RuntimeError(f"Merge cancelled by user on branch '{b}'.")
                                
                            self.write_log("Applying resolutions to complete merge...", "info")
                            self.git_service.resolve_conflicts_and_commit(b, resolutions)
                            self.write_log(f"Branch '{b}' merged into main successfully with resolutions.", "success")
                        
                # git push -u origin main
                self.write_log("Pushing main branch to origin...", "info")
                self.git_service._run_lfs_cmd(["git", "push", "-u", "origin", "main"])
                self.write_log("Successfully pushed main branch to origin.", "success")
                
                # Return to the original branch state (ensure checkout)
                if original_branch:
                    self.write_log(f"Returning to original branch '{original_branch}'...", "info")
                    try:
                        self.git_service.switch_branch(original_branch, force=False)
                    except Exception as se:
                        print(f"Failed to switch back to original branch '{original_branch}': {se}")
                
                def on_done():
                    self.refresh_dashboard()
                    self.refresh_file_list()
                    self.refresh_history()
                    self.load_branches_in_combo()
                    self.write_log("🎉 Merge all branches into main complete!", "success")
                    
                self.task_queue.put(('success', "All branches merged and pushed successfully!", on_done))
                
            except Exception as e:
                if original_branch:
                    try:
                        self.git_service.switch_branch(original_branch, force=False)
                    except Exception:
                        pass
                self.task_queue.put(('error', f"Merge all branches failed:\n{e}", None))
            finally:
                self.decrement_tasks()
                
        threading.Thread(target=run, daemon=True).start()

    @queue_during_bg_tasks
    def on_make_repo_clicked(self):
        import subprocess
        
        repo_name = self.ent_new_repo_name.get().strip()
        if not repo_name:
            self.write_log("Repository name cannot be empty.", "error")
            return
            
        # Read settings from config.json
        config_path = "config.json"
        if not os.path.exists(config_path):
            self.write_log("config.json file does not exist.", "error")
            return
            
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception as e:
            self.write_log(f"Failed to read config.json: {e}", "error")
            return
            
        token = config.get("github_token", "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")
        default_local_path = config.get("default_local_path", "D:\\github")
        organization_name = config.get("organization_name", "mech-higenmotor")
        
        # Verify local path is valid and create if not existing
        if not os.path.exists(default_local_path):
            try:
                os.makedirs(default_local_path, exist_ok=True)
            except Exception as e:
                self.write_log(f"Failed to create default local path {default_local_path}: {e}", "error")
                return
                
        local_repo_path = os.path.abspath(os.path.join(default_local_path, repo_name))
        if os.path.exists(local_repo_path):
            self.write_log(f"Error: Local repository path already exists: {local_repo_path}", "error")
            return
            
        hooks_path = os.path.join(local_repo_path, ".git", "hooks")
        disabled_hooks_path = os.path.join(local_repo_path, ".git", "hooks.disabled")
            
        self.write_log(f"🚀 Starting repository creation: {repo_name}", "info")
        self.increment_tasks()
        
        def run():
            try:
                # 3. Connect to GitHub via PyGithub
                self.write_log("Connecting to GitHub...", "info")
                from github import Github
                g = Github(token)
                
                # Check user / connection
                try:
                    user = g.get_user()
                    username = user.login
                    self.write_log(f"Authenticated as user: {username}", "info")
                except Exception as ex:
                    raise RuntimeError(f"GitHub authentication failed: {ex}")
                
                # 4. Create empty repository on GitHub
                self.write_log("Creating repository on GitHub...", "info")
                github_repo = None
                if organization_name:
                    try:
                        org = g.get_organization(organization_name)
                        github_repo = org.create_repo(repo_name, private=True)
                        self.write_log(f"Created private repository under organization '{organization_name}'", "success")
                    except Exception as ex:
                        raise RuntimeError(f"Failed to create repository in organization '{organization_name}': {ex}")
                else:
                    try:
                        github_repo = user.create_repo(repo_name, private=True)
                        self.write_log(f"Created private repository under personal account '{username}'", "success")
                    except Exception as ex:
                        raise RuntimeError(f"Failed to create repository under personal account: {ex}")
                        
                # 5. Clone url setup
                # Embed token for automatic auth
                clone_url = github_repo.clone_url.replace("https://", f"https://x-access-token:{token}@")
                
                # 6. Clone repository via GitPython / CLI
                self.write_log(f"Cloning repository to {local_repo_path}...", "info")
                import git
                repo = git.Repo.clone_from(clone_url, local_repo_path)
                self.write_log("Clone completed successfully.", "success")
                
                # 7. Initialize Git LFS in the repository via CLI subprocess
                self.write_log("Initializing Git LFS in cloned repository...", "info")
                returncode, stdout, stderr = run_git_subprocess(["git", "lfs", "install"], cwd=local_repo_path, check=False)
                if returncode != 0:
                    self.write_log(f"Warning: git lfs install failed: {stderr}", "warning")
                else:
                    self.write_log("Git LFS initialized successfully.", "success")
                    
                # Temporarily disable ALL git hooks by renaming the hooks folder to prevent hook execution failures (e.g. WSL path errors)
                if os.path.exists(hooks_path):
                    try:
                        os.rename(hooks_path, disabled_hooks_path)
                        self.write_log("Temporarily disabled Git hooks for initial setup.", "info")
                    except Exception as he:
                        self.write_log(f"Warning: Failed to temporarily rename hooks folder: {he}", "warning")
                    
                # Disable hooks temporarily during initial setup commits to prevent hook errors
                with repo.config_writer() as writer:
                    writer.set_value("core", "hooksPath", "/dev/null")
                    
                # 8. Copy template files recursively
                self.write_log("Copying template files to workspace...", "info")
                template_path = os.path.abspath("template")
                if os.path.exists(template_path):
                    self._copy_template_dir(template_path, local_repo_path)
                    self.write_log("Templates copied successfully (renaming '_' to '.' prefixed files).", "success")
                else:
                    self.write_log("Warning: 'template' directory not found in application path.", "warning")
                    
                # 9. Staging template files via GitPython
                self.write_log("Staging template files...", "info")
                repo.git.add(all=True)
                
                # 10. Committing template files
                self.write_log("Committing template files...", "info")
                author = git.Actor(username, f"{username}@users.noreply.github.com")
                repo.index.commit("Initial commit", author=author, committer=author)
                self.write_log("Initial commit created successfully.", "success")
                
                # 11. Push main branch
                try:
                    if repo.active_branch.name != "main":
                        repo.git.branch("-M", "main")
                except Exception:
                    pass
                    
                self.write_log("Pushing main branch to origin...", "info")
                returncode, stdout, stderr = run_git_subprocess(["git", "push", "-u", "origin", "main"], cwd=local_repo_path, check=False)
                if returncode != 0:
                    raise RuntimeError(f"Failed to push main branch: {stderr}")
                self.write_log("Successfully pushed initial commit to main branch.", "success")
                
                # 12. Create and switch to developer branch
                self.write_log(f"Creating and switching to developer branch '{username}'...", "info")
                repo.create_head(username)
                repo.git.checkout(username)
                
                # 13. Committing and pushing developer branch files (empty commit allowed)
                self.write_log("Committing files on developer branch...", "info")
                repo.index.commit("Initial commit on this branch", author=author, committer=author)
                
                self.write_log(f"Pushing developer branch '{username}' to origin...", "info")
                returncode, stdout, stderr = run_git_subprocess(["git", "push", "-u", "origin", username], cwd=local_repo_path, check=False)
                if returncode != 0:
                    raise RuntimeError(f"Failed to push developer branch: {stderr}")
                self.write_log(f"Successfully pushed branch '{username}' to origin.", "success")
                
                # Restore Git hooks folder for normal development operations
                if os.path.exists(disabled_hooks_path):
                    try:
                        os.rename(disabled_hooks_path, hooks_path)
                        self.write_log("Re-enabled Git hooks for future operations.", "info")
                    except Exception as he:
                        self.write_log(f"Warning: Failed to restore hooks folder: {he}", "warning")
                
                # Re-enable hooks for normal development operations
                with repo.config_writer() as writer:
                    writer.remove_option("core", "hooksPath")
                
                # Optimize credential helper by cleaning remote URL and configuring credentials
                try:
                    temp_service = GitService(local_repo_path)
                    temp_service.optimize_credential_helper(username)
                    self.write_log("Configured Git Credential Manager for this repository.", "success")
                except Exception as oe:
                    self.write_log(f"Warning: Failed to optimize credential helper: {oe}", "warning")
                
                # 14. Save new workspace path to config.json
                self.write_log("Updating workspace path in config...", "info")
                self.save_workspace_to_config(local_repo_path)
                
                # 15. Reflect new repository in GUI Dashboard
                def on_done():
                    self.workspace_path = local_repo_path
                    self.git_service = GitService(local_repo_path)
                    
                    # Update local path entry text
                    self.ent_local_dir.delete(0, tk.END)
                    self.ent_local_dir.insert(0, os.path.normpath(local_repo_path))
                    
                    # Refresh all views
                    self.refresh_dashboard()
                    self.refresh_file_list()
                    self.refresh_history()
                    self.load_branches_in_combo()
                    self.write_log(f"🎉 Maintainer setup complete! Workspace switched to: {local_repo_path}", "success")
                    
                    # Switch view to Dashboard so the user sees the newly set repository path and clean remote URL
                    self.switch_view(0)
                    self.trigger_auto_sync_if_enabled()
                    
                self.task_queue.put(('success', f"Repository '{repo_name}' created successfully!", on_done))
                
            except Exception as e:
                try:
                    if os.path.exists(disabled_hooks_path):
                        os.rename(disabled_hooks_path, hooks_path)
                    if 'repo' in locals() and repo:
                        with repo.config_writer() as writer:
                            writer.remove_option("core", "hooksPath")
                except Exception:
                    pass
                self.task_queue.put(('error', f"Maintainer setup failed:\n{e}", None))
            finally:
                self.decrement_tasks()
                
        threading.Thread(target=run, daemon=True).start()
                
    @queue_during_bg_tasks
    def refresh_history(self):
        for item in self.hist_tree.get_children():
            self.hist_tree.delete(item)
            
        if not self.git_service.is_git_repo():
            return
            
        try:
            current_hash = self.git_service.get_current_commit_hash()
            history = self.git_service.get_history()
            for commit in history:
                tags = ()
                if current_hash and commit['hash'] == current_hash:
                    tags = ("current_commit",)
                    
                self.hist_tree.insert("", "end", values=(
                    commit['hash'],
                    commit['author'],
                    commit['date'],
                    commit['message']
                ), tags=tags)
        except Exception as e:
            self.write_log(f"Failed to retrieve history: {e}", "error")

    def on_history_double_click(self, event):
        item = self.hist_tree.identify_row(event.y)
        if item:
            self.hist_tree.selection_set(item)
            self.restore_version()

    @queue_during_bg_tasks
    def restore_version(self):
        selected_item = self.hist_tree.selection()
        if not selected_item:
            self.write_log("Select a version to restore.", "warning")
            return
            
        values = self.hist_tree.item(selected_item[0], 'values')
        commit_hash = values[0]
        desc = values[3]
        
        ans = messagebox.askyesno(
            "Confirm Restore", 
            f"Are you sure you want to restore workspace files to version '{commit_hash}'?\n"
            f"Description: '{desc}'\n\n"
            f"⚠️ Caution: Local unsaved modifications will be overwritten."
        )
        if not ans:
            return
            
        self.btn_restore.state(["disabled"])
        old_branch = self.cb_branch.get()
        
        # Determine containing branches of target commit hash
        containing_branches = self.git_service.get_branches_containing_commit(commit_hash)
        
        target_branch = None
        if containing_branches:
            if old_branch in containing_branches:
                target_branch = old_branch
            else:
                dialog = BranchSelectionDialog(self, containing_branches)
                self.wait_window(dialog)
                target_branch = dialog.selected_branch
                if not target_branch:
                    self.btn_restore.state(["!disabled"])
                    self.write_log("Version restore cancelled by user (Branch selection cancelled).", "warning")
                    return
                    
        def run():
            self.increment_tasks()
            try:
                try:
                    self.git_service.restore_version(commit_hash)
                    
                    def on_done():
                        self.refresh_file_list()
                        self.refresh_history()
                        self.load_branches_in_combo()
                        
                        old_display = old_branch if old_branch else "Detached HEAD"
                        new_display = target_branch if target_branch else "Detached HEAD"
                        self.write_log(f"✅ Successfully restored workspace files to version {commit_hash} (Detached HEAD state)", "success")
                        if old_display != new_display:
                            self.write_log(f"🔄 ACTIVE BRANCH CHANGED: [{old_display}] ➡️ [{new_display}]", "success")
                        else:
                            self.write_log(f"📌 ACTIVE BRANCH: [{new_display}]", "success")
                    self.task_queue.put(('success', f"Successfully restored to version {commit_hash}!", on_done))
                except Exception as e:
                    self.task_queue.put(('error', f"Failed to restore version:\n{e}", None))
            finally:
                self.decrement_tasks()
                
        threading.Thread(target=run, daemon=True).start()

    @queue_during_bg_tasks
    def restore_latest(self):
        ans = messagebox.askyesno("Confirm Return", "Do you want to discard checked-out state and return files to latest master/main branch?")
        if not ans:
            return
            
        self.btn_restore_latest.state(["disabled"])
        prev_branch = getattr(self, 'last_active_branch', "")
        
        def run():
            self.increment_tasks()
            try:
                try:
                    self.git_service.restore_latest(prev_branch)
                    def on_done():
                        self.refresh_file_list()
                        self.refresh_history()
                        self.load_branches_in_combo()
                    
                    msg = f"Successfully returned to latest version trunk (Branch: {prev_branch})!" if prev_branch else "Successfully returned to latest version trunk!"
                    self.task_queue.put(('success', msg, on_done))
                except Exception as e:
                    self.task_queue.put(('error', f"Failed to restore latest:\n{e}", None))
            finally:
                self.decrement_tasks()
                
        threading.Thread(target=run, daemon=True).start()

    def show_git_graph(self):
        if not self.git_service.is_git_repo():
            messagebox.showwarning("No Repository", "Current workspace is not a valid Git repository.")
            return

        git_exe = "git"
        if hasattr(self, 'git_service') and self.git_service and getattr(self.git_service, 'git_path', None):
            if os.path.exists(self.git_service.git_path):
                git_exe = self.git_service.git_path

        cmd_str = f'start "Git-Graph" cmd /K ""{git_exe}" log --graph --all --decorate"'
        import subprocess
        try:
            subprocess.Popen(cmd_str, shell=True, cwd=self.workspace_path)
            self.write_log("Successfully launched Git Graph terminal.", "success")
        except Exception as e:
            self.write_log(f"Failed to launch Git Graph terminal: {e}", "error")

    def browse_git_graph(self):
        if not self.git_service.is_git_repo():
            messagebox.showwarning("No Repository", "Current workspace is not a valid Git repository.")
            return

        repo = self.git_service.repo
        if not repo:
            messagebox.showwarning("No Repository", "Failed to load Git repository.")
            return

        # Try to get remote URL
        url = ""
        try:
            if hasattr(repo, 'remotes') and hasattr(repo.remotes, 'origin'):
                url = repo.remotes.origin.url
            elif hasattr(repo, 'remotes') and len(repo.remotes) > 0:
                url = repo.remotes[0].url
        except Exception:
            pass

        # If not found from repo, try from entry
        if not url and hasattr(self, 'ent_remote_url'):
            url = self.ent_remote_url.get().strip()

        owner = None
        repo_name = None

        if url:
            clean_url = url.strip()
            if clean_url.endswith(".git"):
                clean_url = clean_url[:-4]
            
            # Convert SSH git@github.com:owner/repo format to standard / paths
            if ":" in clean_url and not clean_url.startswith("http"):
                parts = clean_url.split(":")
                clean_url = "/".join(parts)
                
            path_parts = [p for p in clean_url.split("/") if p]
            if len(path_parts) >= 2:
                owner = path_parts[-2]
                repo_name = path_parts[-1]

        if not owner or not repo_name:
            messagebox.showwarning("Invalid Remote", "Could not parse owner and repository name from the remote URL.")
            return

        github_network_url = f"https://github.com/{owner}/{repo_name}/network"
        
        import webbrowser
        try:
            webbrowser.open(github_network_url)
            self.write_log(f"Successfully opened GitHub Network graph: {github_network_url}", "success")
        except Exception as e:
            self.write_log(f"Failed to open browser: {e}", "error")

    def on_file_selected_change(self):
        # 1. Update BOM and Diff button activation state based on selection & background task status
        selected_items = self.tree.selection()
        is_bom_enabled = False
        is_diff_enabled = False
        
        if getattr(self, 'bg_tasks_count', 0) == 0 and len(selected_items) == 1:
            is_diff_enabled = True
            values = self.tree.item(selected_items[0], 'values')
            if values:
                filepath = values[0]
                ext = os.path.splitext(filepath)[1].lower()
                if ext == '.sldasm':
                    is_bom_enabled = True
        
        if is_bom_enabled:
            self.btn_bom.state(["!disabled"])
        else:
            self.btn_bom.state(["disabled"])
            
        if hasattr(self, 'btn_diff'):
            if is_diff_enabled:
                self.btn_diff.state(["!disabled"])
            else:
                self.btn_diff.state(["disabled"])

        # 2. Existing view 1 CAD thumbnail preview logic
        if getattr(self, 'current_view_index', 0) != 1:
            self.preview_container.pack_forget()
            self._current_preview_image = None
            return

        if len(selected_items) == 1:
            values = self.tree.item(selected_items[0], 'values')
            if values:
                filepath = values[0]
                ext = os.path.splitext(filepath)[1].lower()
                if ext in ('.sldprt', '.sldasm', '.slddrw'):
                    full_path = os.path.join(self.workspace_path, filepath)
                    self.show_cad_thumbnail(full_path)
                    return

        self.preview_container.pack_forget()
        self._current_preview_image = None

    def show_cad_thumbnail(self, full_path):
        import threading
        if not hasattr(self, '_preview_request_id'):
            self._preview_request_id = 0
        self._preview_request_id += 1
        req_id = self._preview_request_id

        def bg_extract():
            try:
                import comtypes
                comtypes.CoInitialize()
                try:
                    img = self.extract_cad_thumbnail(full_path)
                    if img and req_id == self._preview_request_id:
                        self.after(0, lambda: self.display_thumbnail_in_canvas(img))
                    elif not img and req_id == self._preview_request_id:
                        self.after(0, lambda: self.preview_container.pack_forget())
                        self._current_preview_image = None
                finally:
                    comtypes.CoUninitialize()
            except Exception:
                if req_id == self._preview_request_id:
                    self.after(0, lambda: self.preview_container.pack_forget())
                    self._current_preview_image = None

        threading.Thread(target=bg_extract, daemon=True).start()

    def extract_cad_thumbnail(self, full_path):
        import os
        if not os.path.exists(full_path):
            return None

        # 1. Try legacy OLE preview extraction
        try:
            import olefile
            if olefile.isOleFile(full_path):
                with olefile.OleFileIO(full_path) as ole:
                    png_stream = None
                    for stream in ole.listdir():
                        if len(stream) > 0 and stream[-1].lower() == 'previewpng':
                            png_stream = stream
                            break
                    if png_stream:
                        data = ole.openstream(png_stream).read()
                        from PIL import Image
                        import io
                        img = Image.open(io.BytesIO(data))
                        return img.copy()
        except Exception:
            pass

        # 2. Try Windows Shell COM extraction
        if IShellItemImageFactory is not None:
            try:
                from ctypes import POINTER, byref, cast, windll
                from ctypes.wintypes import SIZE, HANDLE, HBITMAP
                import win32ui
                import win32gui
                from PIL import Image

                shell32 = windll.shell32
                SIIGBF_RESIZETOFIT = 0x00000000

                h_siif = HANDLE()
                # Use standard Windows backslash path format
                full_path_win = os.path.abspath(full_path).replace('/', '\\')
                hr = shell32.SHCreateItemFromParsingName(full_path_win, None, byref(IShellItemImageFactory._iid_), byref(h_siif))
                if hr >= 0:
                    shell_item_factory = cast(h_siif, POINTER(IShellItemImageFactory))
                    h_bitmap = shell_item_factory.GetImage(SIZE(256, 256), SIIGBF_RESIZETOFIT)
                    if h_bitmap:
                        bmp = win32ui.CreateBitmapFromHandle(h_bitmap)
                        bmp_info = bmp.GetInfo()
                        w = bmp_info['bmWidth']
                        h = bmp_info['bmHeight']
                        bmp_str = bmp.GetBitmapBits(True)
                        img = Image.frombuffer('RGB', (w, h), bmp_str, 'raw', 'BGRX', 0, 1)
                        img_copy = img.copy()
                        win32gui.DeleteObject(h_bitmap)
                        return img_copy
            except Exception:
                pass

        return None

    def display_thumbnail_in_canvas(self, img):
        from PIL import ImageTk, Image
        self._current_preview_image = img.copy()
        
        img_scaled = img.resize((180, 135), Image.Resampling.LANCZOS)
        self._preview_photo = ImageTk.PhotoImage(img_scaled)
        self.preview_canvas.delete("all")
        self.preview_canvas.create_image(0, 0, anchor="nw", image=self._preview_photo)
        try:
            self.preview_container.pack(fill="x", padx=10, pady=(12, 4), after=self.btn_history)
        except Exception:
            self.preview_container.pack(fill="x", padx=10, pady=(12, 4))

    def on_preview_clicked(self, event=None):
        if hasattr(self, '_current_preview_image') and self._current_preview_image:
            try:
                import io
                import win32clipboard
                
                # Convert PIL Image to DIB bytes
                output = io.BytesIO()
                self._current_preview_image.convert("RGB").save(output, "BMP")
                data = output.getvalue()[14:]  # Remove the 14-byte BMP file header
                output.close()
                
                win32clipboard.OpenClipboard()
                try:
                    win32clipboard.EmptyClipboard()
                    win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
                finally:
                    win32clipboard.CloseClipboard()
                    
                self.write_log("Copied thumbnail image to clipboard.", "success")
            except Exception as e:
                self.write_log(f"Failed to copy image to clipboard: {e}", "error")

    @queue_during_bg_tasks
    def show_lfs_cleanup_wizard(self):
        if not self.git_service.is_git_repo():
            messagebox.showerror("Error", "Not a valid Git repository.")
            return
        LfsCleanupWizardDialog(self, self.git_service)

    @queue_during_bg_tasks
    def sync_repository(self, on_success_callback=None, silent=False):
        # --- Step 1: Check for open SolidWorks files (runs in GUI thread) ---
        try:
            open_docs = self.sw_service.get_all_open_documents()
        except Exception:
            open_docs = []

        if open_docs:
            if silent:
                self.write_log("Auto Sync aborted: SolidWorks files are currently open.", "warning")
                return
                
            # Build file list summary for the dialog
            file_list = "\n".join(
                f"  • {doc['title']}" + (" (⚠️ Unsaved changes)" if doc['dirty'] else "")
                for doc in open_docs
            )
            ans = messagebox.askyesnocancel(
                "SolidWorks Open Files Detected",
                f"The following files are open in SolidWorks:\n\n{file_list}\n\n"
                f"[Yes] Save and close files\n"
                f"[No] Close files without saving (discard changes)\n"
                f"[Cancel] Cancel synchronization operation"
            )

            if ans is None:
                # Cancel — abort sync entirely
                return

            sw = self.sw_service._get_sw_app()
            if sw:
                orig_ref_prompt = True
                orig_warn_save = False
                orig_rebuild_err = False
                orig_load_ext_ref = 0
                orig_lightweight_resolve = 0
                orig_large_assembly_resolve = 0
                try:
                    orig_ref_prompt = sw.GetUserPreferenceToggle(15)   # swExtRefNoPromptOrSave
                    orig_warn_save = sw.GetUserPreferenceToggle(249)    # swWarnSaveUpdateErrors
                    orig_rebuild_err = sw.GetUserPreferenceToggle(119)  # swShowErrorsEveryRebuild
                    orig_load_ext_ref = sw.GetUserPreferenceIntegerValue(242) # swLoadExternalReferences
                    orig_lightweight_resolve = sw.GetUserPreferenceIntegerValue(243) # swAssemblyLoadLightweightResolve
                    orig_large_assembly_resolve = sw.GetUserPreferenceIntegerValue(245) # swLargeAssemblyModeResolveLightweight
                    
                    sw.SetUserPreferenceToggle(15, True)   # Suppress reference prompts
                    sw.SetUserPreferenceToggle(249, False) # Suppress save update warnings
                    sw.SetUserPreferenceToggle(119, False) # Suppress rebuild error dialogs
                    sw.SetUserPreferenceIntegerValue(246, 1) # Continue on rebuild errors
                    sw.SetUserPreferenceIntegerValue(242, 1) # Load all references silently
                    sw.SetUserPreferenceIntegerValue(243, 1) # Resolve lightweight silently
                    sw.SetUserPreferenceIntegerValue(245, 1) # Resolve large assembly lightweight silently
                except Exception as pref_e:
                    print(f"Warning: Failed to set user preferences: {pref_e}")

                try:
                    import time, os

                    # Collect all titles of documents we want to explicitly close
                    target_titles = set()
                    for doc in open_docs:
                        t = doc.get('title')
                        if t:
                            target_titles.add(t)

                    # Step 1: Save any docs that need saving (ans is True)
                    if ans is True:
                        for doc in open_docs:
                            doc_obj = doc.get('doc_obj')
                            if not doc_obj:
                                continue
                            try:
                                doc_obj.Save3(5, 0, 0)  # swSaveAsOptions_Silent (1) | swSaveAsOptions_SaveReferenced (4)
                            except Exception:
                                try:
                                    doc_obj.Save()
                                except Exception:
                                    pass

                    time.sleep(0.2)

                    # Step 2: Close the target documents using QuitDoc FIRST.
                    # This releases the active document lock and parent-child reference links.
                    for doc in open_docs:
                        title = doc.get('title', '')
                        if not title:
                            continue
                        try:
                            sw.QuitDoc(title)
                            base_title, _ = os.path.splitext(title)
                            if base_title != title:
                                sw.QuitDoc(base_title)
                        except Exception as ce:
                            print(f"DEBUG: Failed to close {title}: {ce}")

                    # Allow SolidWorks to settle and release COM reference locks
                    time.sleep(0.3)

                    # Step 3: Clean up all REMAINING open documents (referenced/linked assemblies and skeletons)
                    # using a dependency-aware iterative cleanup loop to avoid reference prompts.
                    try:
                        iteration = 0
                        last_doc_count = -1
                        stuck_count = 0
                        
                        while iteration < 10:  # Try up to 10 passes to resolve nested references
                            try:
                                all_open = sw.GetDocuments()
                            except Exception:
                                val = getattr(sw, 'GetDocuments')
                                all_open = val() if callable(val) else val
                            
                            if not all_open:
                                break
                            
                            current_count = len(all_open)
                            if current_count == last_doc_count:
                                stuck_count += 1
                                if stuck_count > 2:
                                    break
                            else:
                                stuck_count = 0
                            last_doc_count = current_count
                            
                            parent_docs = []  # assemblies and drawings
                            child_docs = []   # parts
                            
                            for d in all_open:
                                try:
                                    try:
                                        d_title = d.GetTitle()
                                    except Exception:
                                        d_title = getattr(d, 'GetTitle')
                                    if not d_title:
                                        continue
                                    try:
                                        dtype = d.GetType()
                                    except Exception:
                                        dtype = getattr(d, 'GetType')
                                        if callable(dtype):
                                            dtype = dtype()
                                            
                                    title_lower = d_title.lower()
                                    if dtype in (2, 3) or title_lower.endswith(".sldasm") or title_lower.endswith(".slddrw"):
                                        parent_docs.append((d, d_title))
                                    else:
                                        child_docs.append((d, d_title))
                                except Exception:
                                    pass
                            
                            closed_any = False
                            # Close parents first to release reference locks on children
                            for d, d_title in parent_docs:
                                try:
                                    sw.QuitDoc(d_title)
                                    base_d_title, _ = os.path.splitext(d_title)
                                    if base_d_title != d_title:
                                        sw.QuitDoc(base_d_title)
                                    closed_any = True
                                except Exception:
                                    pass
                                    
                            if closed_any:
                                time.sleep(0.1)
                                
                            # Close children
                            for d, d_title in child_docs:
                                try:
                                    sw.QuitDoc(d_title)
                                    base_d_title, _ = os.path.splitext(d_title)
                                    if base_d_title != d_title:
                                        sw.QuitDoc(base_d_title)
                                    closed_any = True
                                except Exception:
                                    pass
                                    
                            if not closed_any:
                                break
                            time.sleep(0.1)
                            iteration += 1
                    except Exception as e_post:
                        print(f"Warning: Failed to cleanup remaining referenced docs: {e_post}")

                finally:
                    # Restore user preferences to original state
                    try:
                        sw.SetUserPreferenceToggle(15, orig_ref_prompt)
                        sw.SetUserPreferenceToggle(249, orig_warn_save)
                        sw.SetUserPreferenceToggle(119, orig_rebuild_err)
                        sw.SetUserPreferenceIntegerValue(242, orig_load_ext_ref)
                        sw.SetUserPreferenceIntegerValue(243, orig_lightweight_resolve)
                        sw.SetUserPreferenceIntegerValue(245, orig_large_assembly_resolve)
                    except:
                        pass

        # --- Step 2: Proceed with git sync in background thread ---
        self.btn_sync.config(text="Syncing...")
        self.btn_sync.state(["disabled"])
        if hasattr(self, 'btn_cleanup_lfs'):
            self.btn_cleanup_lfs.state(["disabled"])
        
        def run():
            self.increment_tasks()
            try:
                try:
                    self.git_service._run_lfs_cmd(["git", "fetch", "origin"])
                    branch = self.git_service.get_current_branch()
                    if not branch:
                        raise RuntimeError("Cannot sync/pull because you are not currently on a branch (detached HEAD).")

                    # --- Version comparison: skip pull if already up-to-date ---
                    try:
                        local_hash = self.git_service.get_branch_tip_commit(branch)
                        remote_hash = self.git_service.get_branch_tip_commit(f"origin/{branch}")
                        if local_hash and remote_hash and local_hash == remote_hash:
                            self.write_log(
                                f"Already up-to-date with remote (local=remote={local_hash[:7]}). Pull skipped.",
                                "info"
                            )
                            def on_complete_skip():
                                self.refresh_dashboard()
                                if on_success_callback:
                                    on_success_callback()
                            self.task_queue.put(('callback', None, on_complete_skip))
                            return
                    except Exception as ver_e:
                        self.write_log(f"Version comparison skipped ({ver_e}). Proceeding with pull...", "info")
                    # ---------------------------------------------------------

                    conflicted_files = self.git_service.check_merge_conflicts(f"origin/{branch}")
                    if conflicted_files:
                        self.write_log(f"Conflicts pre-detected in {len(conflicted_files)} files! Showing resolution dialog...", "warning")
                        resolutions = self.prompt_multi_conflict_resolution(
                            conflicted_files,
                            is_pull=True
                        )
                        if resolutions is None:
                            self.write_log("Sync cancelled by user.", "warning")
                            return
                            
                        self.write_log("Applying resolutions and completing sync...", "info")
                        res = self.git_service.sync_pull_with_resolutions(resolutions)
                    else:
                        self.write_log("No conflicts detected. Performing standard pull...", "info")
                        try:
                            res = self.git_service.sync_pull_clean()
                        except MergeConflictError as mce:
                            self.write_log("Merge conflict occurred during pull. Showing resolution dialog...", "warning")
                            resolutions = self.prompt_multi_conflict_resolution(
                                mce.conflicted_files,
                                is_pull=True
                            )
                            if resolutions is None:
                                self.write_log("Sync cancelled by user. Aborting merge...", "warning")
                                self.git_service.abort_merge()
                                return
                            
                            self.write_log("Applying resolutions to complete sync...", "info")
                            self.git_service.resolve_conflicts_and_commit(f"origin/{branch}", resolutions)
                            res = "Sync complete after resolving conflicts."
                        
                    def on_complete():
                        self.refresh_dashboard()
                        if on_success_callback:
                            on_success_callback()
                    self.task_queue.put(('success', f"Synchronization complete:\n{res}", on_complete))
                except Exception as e:
                    self.task_queue.put(('error', f"Sync failed:\n{e}", None))
            finally:
                self.decrement_tasks()
                
        threading.Thread(target=run, daemon=True).start()

    def on_auto_sync_changed(self):
        val = self.auto_sync_var.get()
        config_path = "config.json"
        config_data = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config_data = json.load(f)
            except Exception:
                pass
        config_data["auto_sync"] = val
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config_data, f, indent=4)
            self.write_log(f"Auto Sync set to {'ON' if val else 'OFF'}", "info")
        except Exception as e:
            self.write_log(f"Failed to save auto sync state: {e}", "error")

    def trigger_auto_sync_if_enabled(self):
        if not self.auto_sync_var.get():
            return
        if not self.git_service or not self.git_service.is_git_repo():
            return
        if self.btn_sync.instate(['disabled']):
            return
            
        self.write_log("🤖 Auto Sync triggered...", "info")
        self.sync_repository(on_success_callback=lambda: self.merge_main_branch(confirm=False), silent=True)

    @queue_during_bg_tasks
    def clone_repository(self):
        remote_url = self.ent_remote_url.get().strip()
        if not remote_url:
            self.write_log("Please enter a Remote Server URL to clone.", "warning")
            return
            
        # Parse repository name from remote URL
        import re
        repo_name = ""
        url_clean = remote_url
        if url_clean.endswith("/"):
            url_clean = url_clean[:-1]
        if url_clean.endswith(".git"):
            url_clean = url_clean[:-4]
        parts = re.split(r'[/\\]', url_clean)
        if parts:
            last_part = parts[-1]
            if ":" in last_part:
                last_part = last_part.split(":")[-1]
            repo_name = last_part
            
        if not repo_name:
            self.write_log("Could not parse repository name from Remote Server URL.", "error")
            return
            
        # Get default_local_path from config.json
        config_data = self.load_config_data()
        config_ws = config_data.get("default_local_path", "")
        if not config_ws:
            config_ws = self.workspace_path
            
        # Discard the existing Local Path and construct a new one: default_local_path \ repo_name
        local_dir = os.path.normpath(os.path.join(config_ws, repo_name))
        
        self.ent_local_dir.delete(0, tk.END)
        self.ent_local_dir.insert(0, local_dir)
        self.write_log(f"Set local clone path: {local_dir}", "info")
                
        # Check if the repository already exists at the local path
        temp_service = GitService(local_dir)
        if temp_service.is_git_repo():
            messagebox.showerror(
                "Repository Exists",
                "The repository already exists at the local path."
            )
            self.write_log(f"Clone aborted: Repository already exists at '{local_dir}'.", "warning")
            return
            
        # Confirm with user
        ans = messagebox.askyesno(
            "Confirm Clone",
            f"Are you sure you want to clone repository:\n'{remote_url}'\n\ninto local path:\n'{local_dir}'?"
        )
        if not ans:
            return
            
        self.btn_clone.config(text="Cloning...")
        self.btn_clone.state(["disabled"])
        self.write_log(f"Starting cloning process from {remote_url}...", "info")
        
        github_token = config_data.get("github_token", "").strip()
        authenticated_url = remote_url
        if github_token and "github.com" in remote_url.lower():
            match = re.match(r'^(https?://)(github\.com/.*)$', remote_url, re.IGNORECASE)
            if match:
                prefix, rest = match.groups()
                authenticated_url = f"{prefix}{github_token}@{rest}"
                
        def redact_token(text):
            if github_token and len(github_token) >= 8:
                return text.replace(github_token, "***")
            return text

        orig_workspace_path = self.workspace_path
        orig_git_service = self.git_service
        
        def run():
            self.increment_tasks()
            try:
                try:
                    res = temp_service.clone_repository(authenticated_url)
                    clean_res = redact_token(res)
                    
                    # Optimize credential helper by cleaning remote URL and configuring credentials
                    try:
                        username = getattr(self, 'resolved_username', None)
                        if not username and github_token:
                            try:
                                from github import Github
                                g = Github(github_token)
                                user = g.get_user()
                                username = user.login.strip().replace(" ", "-").lower()
                                self.resolved_username = username
                            except Exception:
                                pass
                        if username:
                            temp_service.optimize_credential_helper(username)
                            self.write_log(f"Configured Git Credential Manager for user '{username}'.", "success")
                    except Exception as oe:
                        self.write_log(f"Warning: Failed to optimize credential helper after clone: {oe}", "warning")
                    
                    def on_success():
                        self.workspace_path = local_dir
                        self.git_service = temp_service
                        self.save_workspace_to_config(local_dir)
                        self.load_commit_messages()
                        self.refresh_dashboard()
                        self.refresh_file_list()
                        self.refresh_history()
                        self.write_log(f"Clone completed successfully and workspace updated to: {local_dir}", "success")
                        self.trigger_auto_sync_if_enabled()
                        
                    self.task_queue.put(('success', f"Clone complete successfully!\n{clean_res}", on_success))
                except Exception as e:
                    clean_err = redact_token(str(e))
                    def on_failure():
                        self.workspace_path = orig_workspace_path
                        self.git_service = orig_git_service
                        self.load_commit_messages()
                        self.refresh_dashboard()
                        
                    self.task_queue.put(('error', f"Clone failed:\n{clean_err}", on_failure))
            finally:
                self.decrement_tasks()
                # Restore button text
                def restore_btn():
                    self.btn_clone.config(text="Clone")
                self.task_queue.put(('callback', None, restore_btn))
                
        threading.Thread(target=run, daemon=True).start()

    @queue_during_bg_tasks
    def merge_main_branch(self, confirm=True):
        """Merges the main branch into the current branch."""
        current = self.git_service.get_current_branch()
        if not current:
            self.write_log("Cannot determine the current branch.", "warning")
            return
        if current in ("main", "master"):
            self.write_log(f"The current branch is '{current}'. Cannot merge main branch into itself.", "info")
            return

        # Determine the actual source branch name (main or master)
        try:
            branches = [b.name for b in self.git_service.repo.branches]
        except Exception:
            branches = []
        source = "main" if "main" in branches else ("master" if "master" in branches else None)
        if not source:
            self.write_log("Local 'main' or 'master' branch does not exist.", "error")
            return

        if confirm:
            ans = messagebox.askyesno(
                "Confirm Merge",
                f"Are you sure you want to merge branch '{source}' into current branch '{current}'?"
            )
            if not ans:
                return

        self.btn_merge.state(["disabled"])

        def run():
            self.increment_tasks()
            try:
                try:
                    # --- Fetch remote to get up-to-date remote branch info ---
                    try:
                        self.git_service._run_lfs_cmd(["git", "fetch", "origin"])
                    except Exception as fetch_e:
                        self.write_log(f"Remote fetch failed ({fetch_e}). Proceeding with local data...", "warning")

                    # --- Version comparison: skip merge if current branch already contains remote source ---
                    try:
                        remote_source_hash = self.git_service.get_branch_tip_commit(f"origin/{source}")
                        local_source_hash = self.git_service.get_branch_tip_commit(source)
                        current_hash = self.git_service.get_branch_tip_commit(current)

                        # Determine which hash to compare against (prefer remote)
                        compare_hash = remote_source_hash if remote_source_hash else local_source_hash

                        if compare_hash and current_hash and compare_hash == current_hash:
                            # current branch tip == source tip: already merged or identical
                            self.write_log(
                                f"Current branch '{current}' is already at the same commit as '{source}' "
                                f"({compare_hash[:7]}). Merge skipped.",
                                "info"
                            )
                            self.task_queue.put(('callback', None, self.refresh_dashboard))
                            return

                        if compare_hash:
                            # Check if current branch already contains the tip of source
                            try:
                                out = self.git_service._run_lfs_cmd(
                                    ["git", "merge-base", "--is-ancestor", compare_hash, current]
                                )
                                # If the above does not raise, source tip is already an ancestor of current
                                self.write_log(
                                    f"Branch '{source}' ({compare_hash[:7]}) is already fully merged into "
                                    f"'{current}'. Merge skipped.",
                                    "info"
                                )
                                self.task_queue.put(('callback', None, self.refresh_dashboard))
                                return
                            except Exception:
                                pass  # Not yet merged — proceed normally
                    except Exception as ver_e:
                        self.write_log(f"Version comparison skipped ({ver_e}). Proceeding with merge...", "info")
                    # ---------------------------------------------------------

                    conflicted_files = self.git_service.check_merge_conflicts(source)
                    if conflicted_files:
                        self.write_log(f"Conflicts pre-detected in {len(conflicted_files)} files! Showing resolution dialog...", "warning")
                        resolutions = self.prompt_multi_conflict_resolution(
                            conflicted_files,
                            ours_branch=current,
                            theirs_branch=source,
                            is_pull=False
                        )
                        if resolutions is None:
                            self.write_log("Merge cancelled by user.", "warning")
                            return
                        
                        self.write_log("Merging main branch with resolutions...", "info")
                        self.git_service.merge_branch_with_resolutions(source, resolutions)
                        result = "Merge completed with resolutions."
                    else:
                        self.write_log("No conflicts detected. Performing standard merge...", "info")
                        try:
                            result = self.git_service.merge_branch(source)
                        except MergeConflictError as mce:
                            self.write_log("Merge conflict occurred during merge. Showing resolution dialog...", "warning")
                            resolutions = self.prompt_multi_conflict_resolution(
                                mce.conflicted_files,
                                ours_branch=current,
                                theirs_branch=source,
                                is_pull=False
                            )
                            if resolutions is None:
                                self.write_log("Merge cancelled by user. Aborting merge...", "warning")
                                self.git_service.abort_merge()
                                return
                            
                            self.write_log("Applying resolutions to complete merge...", "info")
                            self.git_service.resolve_conflicts_and_commit(source, resolutions)
                            result = "Merge completed with resolutions."
                    
                    # 1. Push current branch to remote
                    push_msg = ""
                    remote_url = self.git_service.get_remote_url()
                    if remote_url and current:
                        self.write_log(f"Pushing merged branch '{current}' to remote...", "info")
                        try:
                            self.git_service._run_lfs_cmd(["git", "push", "-u", "origin", current])
                            push_msg = f"\n\nSuccessfully pushed branch '{current}' to remote."
                        except Exception as pe:
                            push_msg = f"\n\nWarning: Push failed:\n{pe}"
                            
                    # 2. Return to the original branch state (ensure checkout)
                    try:
                        self.git_service.switch_branch(current, force=False)
                    except Exception as se:
                        print(f"Failed to switch back to original branch '{current}': {se}")

                    self.task_queue.put(('success', f"Merge complete:\n{result}{push_msg}", self.refresh_dashboard))
                except Exception as e:
                    self.task_queue.put(('error', f"Merge failed:\n{e}", None))
            finally:
                self.decrement_tasks()

        threading.Thread(target=run, daemon=True).start()

    @queue_during_bg_tasks
    def open_readme(self):
        """Opens the README.md in Windows text editor, copying from template if it doesn't exist."""
        readme_path = os.path.join(self.workspace_path, "README.md")
        if not os.path.exists(readme_path):
            readme_path = os.path.join(self.workspace_path, "readme.md")
            if not os.path.exists(readme_path):
                readme_path = os.path.join(self.workspace_path, "README.MD")
                
        if not os.path.exists(readme_path):
            script_dir = os.path.dirname(os.path.abspath(__file__))
            template_readme = os.path.join(script_dir, "template", "README.md")
            if os.path.exists(template_readme):
                import shutil
                try:
                    target_path = os.path.join(self.workspace_path, "README.md")
                    shutil.copy2(template_readme, target_path)
                    self.write_log("Created README.md in workspace from template file.", "success")
                    readme_path = target_path
                except Exception as copy_err:
                    self.write_log(f"Failed to copy template README.md to workspace: {copy_err}", "error")
            else:
                self.write_log("README.md template file not found in template directory.", "warning")

        if os.path.exists(readme_path):
            def run_editor():
                self.increment_tasks()
                try:
                    import subprocess
                    self.write_log("Opened README.md in Windows Text Editor.", "success")
                    # Start notepad.exe and block until it is closed
                    subprocess.run(["notepad.exe", os.path.abspath(readme_path)])
                    self.write_log("README.md editing completed. Starting auto sync...", "info")
                    
                    # Git operations: add, commit, push
                    rel_readme = os.path.relpath(readme_path, self.workspace_path).replace("\\", "/")
                    
                    # 1. git add
                    self.git_service._run_lfs_cmd(["git", "add", rel_readme])
                    
                    # 2. check diff to see if there are changes
                    try:
                        self.git_service._run_lfs_cmd(["git", "diff", "--cached", "--quiet", rel_readme])
                        has_changes = False
                    except Exception:
                        has_changes = True
                        
                    if not has_changes:
                        self.write_log("No changes detected in README.md. Auto sync skipped.", "info")
                        return
                    
                    # 3. git commit
                    import git
                    name = "SolidWorks Designer"
                    email = "designer@example.com"
                    try:
                        reader = self.git_service.repo.config_reader()
                        name = reader.get_value("user", "name", default="SolidWorks Designer")
                        email = reader.get_value("user", "email", default="designer@example.com")
                    except Exception:
                        pass
                    author = git.Actor(name, email)
                    
                    import git.exc
                    try:
                        self.git_service.repo.index.commit("Update README.md", author=author, committer=author)
                        self.write_log("Saved README.md changes locally.", "success")
                    except git.exc.HookExecutionError as e:
                        if "post-commit" in str(e):
                            self.write_log("Saved README.md changes locally (post-commit hook failed/ignored).", "warning")
                        else:
                            raise
                    
                    # 4. git push (if remote is configured)
                    remote_url = self.git_service.get_remote_url()
                    branch = self.git_service.get_current_branch()
                    if remote_url and branch:
                        self.write_log("Pushing README.md changes to remote server...", "info")
                        self.git_service._run_lfs_cmd(["git", "push", "origin", branch])
                        self.write_log("Successfully synchronized README.md to remote server.", "success")
                    else:
                        self.write_log("README.md committed locally (no remote configured or detached HEAD).", "info")
                        
                    # Refresh views on main thread
                    self.task_queue.put(('callback', None, self.refresh_dashboard))
                    self.task_queue.put(('callback', None, self.refresh_file_list))
                    self.task_queue.put(('callback', None, self.refresh_history))
                except Exception as e:
                    self.write_log(f"README.md auto sync failed: {e}", "error")
                finally:
                    self.decrement_tasks()
                    
            threading.Thread(target=run_editor, daemon=True).start()
        else:
            self.write_log("README.md not found in the current workspace.", "warning")
            messagebox.showinfo("README.md Not Found", "README.md file does not exist in the current workspace.")

    @queue_during_bg_tasks
    def open_workspace_in_explorer(self):
        """Opens the current workspace path in Windows Explorer."""
        if os.path.exists(self.workspace_path):
            import subprocess
            try:
                subprocess.Popen(["explorer", os.path.abspath(self.workspace_path)])
                self.write_log(f"Opened workspace folder in Explorer: {self.workspace_path}", "success")
            except Exception as e:
                self.write_log(f"Failed to open workspace in Explorer: {e}", "error")
        else:
            self.write_log(f"Workspace path does not exist: {self.workspace_path}", "error")

    @queue_during_bg_tasks
    def change_workspace(self):
        typed_path = self.ent_local_dir.get().strip()
        
        if typed_path and os.path.isdir(typed_path):
            initial_dir = typed_path
        else:
            initial_dir = self.workspace_path
            # Reset entry text since the typed path was invalid
            self.ent_local_dir.delete(0, tk.END)
            self.ent_local_dir.insert(0, os.path.normpath(self.workspace_path))
            
        dir_path = filedialog.askdirectory(initialdir=initial_dir, title="Select Project Folder")
        if dir_path:
            self.workspace_path = os.path.normpath(dir_path)
            self.git_service = GitService(self.workspace_path)
            
            # Save workspace path to config.json
            self.save_workspace_to_config(self.workspace_path)
            
            # Reload commit templates for the new workspace
            self.load_commit_messages()
            
            self.refresh_dashboard()
            self.refresh_file_list()
            self.refresh_history()
            self.write_log(f"Switched project workspace to: {self.workspace_path}", "success")
            self.trigger_auto_sync_if_enabled()
        else:
            # If user cancelled, ensure the entry has the current valid workspace path
            self.ent_local_dir.delete(0, tk.END)
            self.ent_local_dir.insert(0, os.path.normpath(self.workspace_path))

    def save_workspace_to_config(self, path):
        config_path = "config.json"
        config = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
            except Exception:
                pass
        config["workspace_path"] = path
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4)
        except Exception as e:
            print(f"Error saving workspace path to config.json: {e}")


    def get_selected_file_abs_paths(self):
        selected_items = self.tree.selection()
        if not selected_items:
            return []
        file_abs_paths = []
        for item in selected_items:
            values = self.tree.item(item, 'values')
            if values:
                file_rel_path = values[0]
                file_abs_paths.append(os.path.join(self.workspace_path, file_rel_path))
        return file_abs_paths

    def load_edrawings_path(self):
        config_path = "config.json"
        default_path = "C:\\Program Files\\SOLIDWORKS Corp\\eDrawings\\eDrawings.exe"
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    return config.get("edrawings_path", default_path)
            except Exception:
                return default_path
        return default_path

    def load_imagemagick_path(self):
        config_path = "config.json"
        default_path = "C:\\Users\\dhkima\\scoop\\shims\\compare.exe"
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    return config.get("imagemagick_path", default_path)
            except Exception:
                return default_path
        return default_path

    def load_solidworks_path(self):
        config_path = "config.json"
        default_path = "C:\\Program Files\\SOLIDWORKS Corp\\SOLIDWORKS\\SLDWORKS.exe"
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    return config.get("solidworks_path", default_path)
            except Exception:
                return default_path
        return default_path
    def open_external_viewer(self):
        files = self.get_selected_file_abs_paths()
        if not files:
            self.write_log("Please select at least one SolidWorks file first.", "warning")
            return
            
        path = self.load_edrawings_path()
        if os.path.exists(path):
            errors = []
            import subprocess
            for file in files:
                try:
                    subprocess.Popen([path, os.path.abspath(file)])
                except Exception as e:
                    errors.append(f"Failed to open {os.path.basename(file)}: {e}")
            if errors:
                self.write_log("\n".join(errors), "error")
        else:
            self.write_log(f"eDrawings executable not found at path: {path}. Please check config.json.", "error")

    def show_diff_popup(self):
        selected_items = self.tree.selection()
        if len(selected_items) != 1:
            return
        values = self.tree.item(selected_items[0], 'values')
        if not values:
            return
        file_rel_path = values[0]
        FileCommitHistoryDialog(self, file_rel_path)

    def generate_bom_action(self):
        selected_items = self.tree.selection()
        if not selected_items:
            self.write_log("⚠️ Select a SolidWorks Assembly (.sldasm) file first.", "warning")
            return
        values = self.tree.item(selected_items[0], 'values')
        if not values:
            return
        filepath = values[0]
        ext = os.path.splitext(filepath)[1].lower()
        if ext != '.sldasm':
            self.write_log("⚠️ BOM generation is only supported for SolidWorks Assembly (.sldasm) files.", "warning")
            return

        full_path = os.path.join(self.workspace_path, filepath)

        # Check if SolidWorks is running before we do anything (config query or BOM runner)
        was_running_before = False
        try:
            import win32com.client
            # SldWorks.Application is registered in ROT if SolidWorks is running
            raw_obj = win32com.client.GetActiveObject("SldWorks.Application")
            if raw_obj:
                was_running_before = True
        except:
            pass
        self.was_sw_running_before_bom = was_running_before

        # Capture initially open files to pass to the BOM runner so it knows what to close
        open_before_abs = []
        for f in self.last_open_files:
            open_before_abs.append(os.path.normpath(os.path.join(self.workspace_path, f)).lower())
        self.sw_open_before_bom = ",".join(open_before_abs)

        # Connect to SolidWorks via a separate subprocess to avoid GUI thread COM apartment clashes
        config_names = []
        try:
            import json
            import sys
            import subprocess
            
            target_path_fs = os.path.normpath(full_path).replace('\\', '/')
            
            py_code = """
import win32com.client
import win32com.client.dynamic
import pythoncom
import json
import sys
import os
import time

def get_sw_app():
    # Try active object
    try:
        raw_obj = win32com.client.GetActiveObject("SldWorks.Application")
        return win32com.client.dynamic.Dispatch(raw_obj)
    except:
        pass
    # Try GetObject
    try:
        raw_obj = win32com.client.GetObject(Class="SldWorks.Application")
        return win32com.client.dynamic.Dispatch(raw_obj)
    except:
        pass
    # Try launching
    sldworks_exe = r"C:\\Program Files\\SOLIDWORKS Corp\\SOLIDWORKS\\sldworks.exe"
    if os.path.exists(sldworks_exe):
        import subprocess
        subprocess.Popen([sldworks_exe])
        poll_timeout = 20.0
        poll_start = time.time()
        while time.time() - poll_start < poll_timeout:
            try:
                raw_obj = win32com.client.GetActiveObject("SldWorks.Application")
                return win32com.client.dynamic.Dispatch(raw_obj)
            except:
                time.sleep(1.0)
    return None

try:
    pythoncom.CoInitialize()
    sw_app = get_sw_app()
    if not sw_app:
        print(json.dumps([]))
        sys.exit(0)
        
    path = __TARGET_PATH__
    config_names = []
    already_open_paths = set()
    
    # 1. Try to check open documents first
    try:
        val_docs = getattr(sw_app, 'GetDocuments', None)
        open_docs = val_docs() if callable(val_docs) else val_docs
        if open_docs:
            for d in open_docs:
                try:
                    path_val = getattr(d, 'GetPathName', None)
                    d_path = path_val() if callable(path_val) else path_val
                    if d_path:
                        already_open_paths.add(os.path.normpath(d_path).lower())
                    else:
                        t_val = getattr(d, 'GetTitle', None)
                        t = t_val() if callable(t_val) else t_val
                        if t:
                            already_open_paths.add(t.lower())
                            
                    if d_path and os.path.normpath(d_path).lower() == os.path.normpath(path).lower():
                        cfg_val = getattr(d, 'GetConfigurationNames', None)
                        cfg_list = cfg_val() if callable(cfg_val) else cfg_val
                        if cfg_list:
                            config_names = list(cfg_list)
                except:
                    pass
    except:
        pass
        
    # 2. If not found in open docs, open silently and read-only to query
    if not config_names:
        opened_temp = False
        model = None
        try:
            error = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            warning = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            # doc_type = 2 (swDocASSEMBLY), options = 1 | 2 (swOpenDocOptions_Silent | swOpenDocOptions_ReadOnly)
            model = sw_app.OpenDoc6(path, 2, 1 | 2, "", error, warning)
            if model:
                opened_temp = True
                cfg_val = getattr(model, 'GetConfigurationNames', None)
                cfg_list = cfg_val() if callable(cfg_val) else cfg_val
                if cfg_list:
                    config_names = list(cfg_list)
        except Exception as open_err:
            pass
        finally:
            model = None
            import gc
            gc.collect()
            try:
                pythoncom.CoCollectFreeUnusedLibraries()
            except:
                pass
                
            # Keep the assembly and its components open so they remain loaded
            # for the subsequent BOM extraction run.
            pass
            
    print(json.dumps(config_names))
except Exception as e:
    print(json.dumps([]))
finally:
    try:
        pythoncom.CoUninitialize()
    except:
        pass
""".replace("__TARGET_PATH__", repr(target_path_fs))
            # Run the query using the current python interpreter without opening a shell window
            creation_flags = 0
            if sys.platform == 'win32':
                creation_flags = subprocess.CREATE_NO_WINDOW
                
            res = subprocess.run(
                [sys.executable, '-c', py_code],
                capture_output=True,
                text=True,
                creationflags=creation_flags,
                timeout=30.0
            )
            
            if res.returncode == 0:
                output_str = res.stdout.strip()
                if output_str:
                    config_names = json.loads(output_str)
        except Exception as e:
            self.write_log(f"⚠️ Warning: Failed to query SolidWorks configurations: {e}", "warning")

        if len(config_names) >= 2:
            # Create modal select dialog
            dialog = tk.Toplevel(self)
            dialog.title("Select Configuration")
            dialog.geometry("380x160")
            dialog.resizable(False, False)
            dialog.configure(bg="#f3f4f6")
            dialog.transient(self)
            dialog.grab_set()

            # Center the dialog
            dialog.update_idletasks()
            width = dialog.winfo_width()
            height = dialog.winfo_height()
            x = (dialog.winfo_screenwidth() // 2) - (width // 2)
            y = (dialog.winfo_screenheight() // 2) - (height // 2)
            dialog.geometry(f"+{x}+{y}")

            lbl = tk.Label(dialog, text="Select Configuration for BOM extraction:", bg="#f3f4f6", fg="#1f2937", font="TkDefaultFont")
            lbl.pack(pady=(15, 5))

            selected_config = tk.StringVar(value=config_names[0])
            cb = ttk.Combobox(dialog, textvariable=selected_config, values=config_names, state="readonly", width=30)
            cb.pack(pady=5)
            cb.set(config_names[0])

            def on_ok():
                dialog.destroy()
                self.start_bom_runner_process(full_path, filepath, selected_config.get())

            def on_cancel():
                dialog.destroy()
                self.write_log("ℹ️ BOM extraction cancelled by user.", "info")

            btn_frm = ttk.Frame(dialog)
            btn_frm.pack(pady=15)

            btn_ok = ttk.Button(btn_frm, text="OK", command=on_ok, width=10)
            btn_ok.pack(side="left", padx=10)

            btn_cancel = ttk.Button(btn_frm, text="Cancel", command=on_cancel, width=10)
            btn_cancel.pack(side="left", padx=10)
        else:
            # Proceed directly if 0 or 1 configuration is found
            self.start_bom_runner_process(full_path, filepath, None)

    def start_bom_runner_process(self, full_path, filepath, config_name):
        # Start task in UI
        self.increment_tasks()
        self.btn_bom.state(["disabled"]) # Disable during task execution
        msg_suffix = f" (Config: {config_name})" if config_name else ""
        self.write_log(f"🚀 Starting BOM extraction for {os.path.basename(filepath)}{msg_suffix}...", "info")

        def run_bom():
            try:
                import sys
                import subprocess
                import os
                
                script_dir = os.path.dirname(os.path.abspath(__file__))
                runner_path = os.path.join(script_dir, "sw_bom_runner.py")
                
                env_vars = os.environ.copy()
                env_vars["PYTHONIOENCODING"] = "utf-8"
                
                cmd = [sys.executable, "-u", runner_path, full_path]
                if config_name:
                    cmd.extend(["--config", config_name])
                if hasattr(self, 'was_sw_running_before_bom'):
                    running_str = "true" if self.was_sw_running_before_bom else "false"
                    cmd.extend(["--was-running", running_str])
                if hasattr(self, 'sw_open_before_bom') and self.sw_open_before_bom:
                    cmd.extend(["--open-before", self.sw_open_before_bom])
                
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    encoding="utf-8",
                    errors="replace",
                    env=env_vars
                )
                
                # Read stdout line by line and print to log/console
                for line in proc.stdout:
                    clean_line = line.strip()
                    if clean_line:
                        print(f"[BOM Runner] {clean_line}")
                        if clean_line.startswith("Error:"):
                            self.task_queue.put(('log', (f"⚠️ {clean_line}", "warning"), None))
                        elif "Saving to Excel:" in clean_line or "Traversing" in clean_line:
                            self.task_queue.put(('log', (f"ℹ️ {clean_line}", "info"), None))
                
                proc.wait()
                returncode = proc.returncode
                
                if returncode == 0:
                    base_name = os.path.splitext(os.path.basename(filepath))[0]
                    config_suffix = f"__{config_name}" if config_name else ""
                    self.task_queue.put(('success', f"✅ BOM Tree and Partlist successfully saved for '{base_name}{config_suffix}' in 2D/BOM/ folder.", None))
                else:
                    self.task_queue.put(('error', f"❌ BOM extraction failed with exit code {returncode}.", None))
            except Exception as e:
                self.task_queue.put(('error', f"❌ Failed to run BOM extraction: {e}", None))
            finally:
                self.decrement_tasks()
                
        import threading
        threading.Thread(target=run_bom, daemon=True).start()

    def open_export_dialog(self):
        self._export_active_files = None
        # Create toplevel popup
        pop = tk.Toplevel(self)
        pop.title("Solidworks EXPORT")
        pop.geometry("420x460")
        pop.resizable(False, False)
        
        # Apply window background color matching main GUI
        pop.configure(bg="#f3f4f6")
        
        # Center the window relative to self
        pop.transient(self)
        pop.grab_set()
        
        # Format label frame using standard tk.LabelFrame to perfectly match the bg color
        lf = tk.LabelFrame(pop, text="Export Format (Multiple Selection)", bg="#f3f4f6", fg="#059669", font="TkDefaultFont", relief="groove")
        lf.pack(fill="x", padx=20, pady=(10, 5))
        
        # Checkbuttons for multiple selection with bg alignment
        pdf_var = tk.BooleanVar(value=True)
        dxf_var = tk.BooleanVar(value=False)
        step_var = tk.BooleanVar(value=False)
        step_asm_var = tk.BooleanVar(value=False)
        
        cb_pdf = tk.Checkbutton(lf, text="PDF (.slddrw)", variable=pdf_var, bg="#f3f4f6", activebackground="#f3f4f6", selectcolor="#ffffff", fg="#1f2937", font="TkDefaultFont")
        cb_pdf.pack(anchor="w", padx=15, pady=2)
        
        cb_dxf = tk.Checkbutton(lf, text="DXF (.slddrw)", variable=dxf_var, bg="#f3f4f6", activebackground="#f3f4f6", selectcolor="#ffffff", fg="#1f2937", font="TkDefaultFont")
        cb_dxf.pack(anchor="w", padx=15, pady=2)
        
        cb_step = tk.Checkbutton(lf, text="STEP (.sldprt)", variable=step_var, bg="#f3f4f6", activebackground="#f3f4f6", selectcolor="#ffffff", fg="#1f2937", font="TkDefaultFont")
        cb_step.pack(anchor="w", padx=15, pady=2)
        
        cb_step_asm = tk.Checkbutton(lf, text="STEP_ASM (.sldasm)", variable=step_asm_var, bg="#f3f4f6", activebackground="#f3f4f6", selectcolor="#ffffff", fg="#1f2937", font="TkDefaultFont")
        cb_step_asm.pack(anchor="w", padx=15, pady=2)
        
        # Configuration Option Frame
        cfg_lf = tk.LabelFrame(pop, text="Configuration Option", bg="#f3f4f6", fg="#059669", font="TkDefaultFont", relief="groove")
        cfg_lf.pack(fill="x", padx=20, pady=(5, 5))
        
        every_cfg_var = tk.BooleanVar(value=True)
        rb_every = tk.Radiobutton(cfg_lf, text="Every Configurations (All)", variable=every_cfg_var, value=True, bg="#f3f4f6", activebackground="#f3f4f6", selectcolor="#ffffff", fg="#1f2937", font="TkDefaultFont")
        rb_every.pack(anchor="w", padx=15, pady=2)
        
        rb_active = tk.Radiobutton(cfg_lf, text="Active Configuration Only", variable=every_cfg_var, value=False, bg="#f3f4f6", activebackground="#f3f4f6", selectcolor="#ffffff", fg="#1f2937", font="TkDefaultFont")
        rb_active.pack(anchor="w", padx=15, pady=2)
        
        # Input Frame
        input_frm = ttk.Frame(pop, style="TFrame")
        input_frm.pack(fill="x", padx=20, pady=2)
        
        # PREFIX entry
        lbl_prefix = ttk.Label(input_frm, text="PREFIX:", style="TLabel", font="TkDefaultFont")
        lbl_prefix.pack(anchor="w", pady=(2, 1))
        ent_prefix = ttk.Entry(input_frm, font="TkDefaultFont")
        ent_prefix.pack(fill="x", pady=(0, 6))
        
        # OUTPUT_DIR entry
        lbl_out_dir = ttk.Label(input_frm, text="OUTPUT_DIR:", style="TLabel", font="TkDefaultFont")
        lbl_out_dir.pack(anchor="w", pady=(2, 1))
        ent_out_dir = ttk.Entry(input_frm, font="TkDefaultFont")
        ent_out_dir.pack(fill="x", pady=(0, 2))
        ent_out_dir.insert(0, "2D")
        
        def get_filtered_files_list():
            # Get current prefix and checked formats
            prefix_val = ent_prefix.get().strip()
            if prefix_val == "*" or prefix_val == "":
                prefix_val = ""
                
            formats = []
            if pdf_var.get(): formats.append("PDF")
            if dxf_var.get(): formats.append("DXF")
            if step_var.get(): formats.append("STEP")
            if step_asm_var.get(): formats.append("STEP_ASM")
            
            # Retrieve currently displayed files from the file table treeview
            visible_files = []
            for item in self.tree.get_children():
                vals = self.tree.item(item, 'values')
                if vals:
                    visible_files.append(vals[0])
            
            if not visible_files:
                return [], formats
                
            final_list = []
            
            for f_rel in visible_files:
                f_lower = f_rel.lower()
                base_name = os.path.basename(f_rel)
                
                # Check format requirements
                match = False
                if f_lower.endswith(".slddrw") and ("PDF" in formats or "DXF" in formats):
                    match = True
                elif f_lower.endswith(".sldprt") and "STEP" in formats:
                    match = True
                elif f_lower.endswith(".sldasm") and "STEP_ASM" in formats:
                    match = True
                    
                if match:
                    if prefix_val == "" or base_name.startswith(prefix_val):
                        final_list.append(f_rel)
                        
            return final_list, formats
        
        def start_action():
            filtered_files, formats = get_filtered_files_list()
            if getattr(self, "_export_active_files", None) is not None:
                filtered_files = [f for f in filtered_files if f in self._export_active_files]
            if not formats:
                # If neither format is selected, show error
                messagebox.showerror("Error", "Please select at least one format.")
                return
                
            if not filtered_files:
                messagebox.showwarning("Warning", "No matching files found to export.")
                return
                
            # Check if any of the target files are currently open in SolidWorks
            open_in_sw_targets = []
            for f_rel in filtered_files:
                f_rel_lower = f_rel.lower().replace("\\", "/")
                for open_f in self.last_open_files:
                    if open_f.lower().replace("\\", "/") == f_rel_lower:
                        open_in_sw_targets.append(f_rel)
                        break
                        
            if open_in_sw_targets:
                targets_str = "\n".join(open_in_sw_targets[:10])
                if len(open_in_sw_targets) > 10:
                    targets_str += f"\n... and {len(open_in_sw_targets) - 10} more files."
                    
                messagebox.showwarning(
                    "Solidworks File Open Alert",
                    f"The following target files are currently open in SolidWorks. "
                    f"Please close them in SolidWorks before exporting:\n\n{targets_str}"
                )
                return
                
            prefix_val = ent_prefix.get().strip()
            out_dir_val = ent_out_dir.get().strip()
            if not out_dir_val:
                out_dir_val = "2D"
                
            # Create job dictionary
            job_data = {
                "workspace_path": self.workspace_path,
                "formats": formats,
                "prefix": prefix_val,
                "output_dir": out_dir_val,
                "every_configurations": every_cfg_var.get(),
                "files": filtered_files
            }
            
            # Temporary file path
            job_file_name = f"export_job_{int(time.time())}.json"
            job_path = os.path.join(self.workspace_path, job_file_name)
            
            try:
                with open(job_path, "w", encoding="utf-8") as f:
                    json.dump(job_data, f, indent=4)
            except Exception as io_err:
                messagebox.showerror("Error", f"Failed to create job configuration: {io_err}")
                return
                
            # Launch background runner process with progress tracking popup
            try:
                script_dir = os.path.dirname(os.path.abspath(__file__))
                runner_path = os.path.join(script_dir, "sw_export_runner.py")
                
                import subprocess
                import threading
                
                import os as os_env
                env_vars = os_env.environ.copy()
                env_vars["PYTHONIOENCODING"] = "utf-8"
                
                # Run subprocess capturing stdout and stderr
                proc = subprocess.Popen(
                    [sys.executable, "-u", runner_path, job_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1, # Line buffered
                    encoding="utf-8",
                    errors="replace",
                    env=env_vars
                )
                self.export_process = proc
                self.write_log(f"🚀 Started background export process (Formats: {formats}, Prefix: '{prefix_val}', OutDir: '{out_dir_val}').", "info")
                
                # Close the format selection window
                pop.destroy()
                
                # Create Progress Status Dialog
                progress_pop = tk.Toplevel(self)
                progress_pop.title("SolidWorks Exporting...")
                progress_pop.geometry("450x200")
                progress_pop.resizable(False, False)
                progress_pop.configure(bg="#f3f4f6")
                progress_pop.transient(self)
                progress_pop.grab_set() # Modal dialog to prevent interacting with main window
                
                # Title frame
                title_lbl = tk.Label(progress_pop, text="SolidWorks EXPORT Progress", font=("TkDefaultFont", 11, "bold"), bg="#f3f4f6", fg="#059669")
                title_lbl.pack(pady=(15, 5))
                
                # Progress labels
                lbl_status = tk.Label(progress_pop, text=f"Initializing SolidWorks... (0 / {len(filtered_files)} Completed)", font="TkDefaultFont", bg="#f3f4f6", fg="#1f2937")
                lbl_status.pack(pady=2)
                
                lbl_file = tk.Label(progress_pop, text="Launching background SolidWorks engine...", font="TkDefaultFont", bg="#f3f4f6", fg="#6b7280", wraplength=400)
                lbl_file.pack(pady=(0, 10))
                
                # Progress bar
                prog_bar = ttk.Progressbar(progress_pop, orient="horizontal", length=380, mode="determinate", maximum=len(filtered_files), style="Custom.Horizontal.TProgressbar")
                prog_bar.pack(pady=5)
                
                is_cancelled = [False]
                
                def cancel_action():
                    is_cancelled[0] = True
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    try:
                        progress_pop.destroy()
                    except Exception:
                        pass
                    self.write_log("⚠️ Export process cancelled by user.", "warning")
                    
                # Cancel Button
                btn_cancel = ttk.Button(progress_pop, text="Cancel", command=cancel_action)
                btn_cancel.pack(pady=(10, 10))
                
                # GUI Updater helper
                def update_progress(curr, total, file_name):
                    try:
                        prog_bar["value"] = curr
                        lbl_status.config(text=f"Exporting... {curr}/{total} files", font="TkDefaultFont")
                        lbl_file.config(text=f"{file_name}", font="TkDefaultFont")
                    except Exception:
                        pass
                        
                def update_config_progress(config_name):
                    try:
                        current_file_text = lbl_file.cget("text")
                        if " (Config: " in current_file_text:
                            current_file_text = current_file_text.split(" (Config: ")[0]
                        elif "\n(Config: " in current_file_text:
                            current_file_text = current_file_text.split("\n(Config: ")[0]
                        current_file_text = current_file_text.strip()
                        lbl_file.config(text=f"{current_file_text} \n(Config: {config_name})", font="TkDefaultFont")
                    except Exception:
                        pass
                        
                def on_finish(returncode, jp):
                    if os.path.exists(jp):
                        try:
                            os.remove(jp)
                        except:
                            pass
                    try:
                        progress_pop.destroy()
                    except Exception:
                        pass
                        
                    if is_cancelled[0]:
                        return
                        
                    if returncode == 0:
                        messagebox.showinfo("Export Complete", "SolidWorks export process has completed successfully!")
                        self.write_log("✅ Background export process finished successfully.", "success")
                    else:
                        messagebox.showerror("Export Failed", f"SolidWorks export process failed (Exit Code: {returncode})")
                        self.write_log(f"❌ Background export process failed with exit code {returncode}.", "error")
                
                # Background monitoring thread
                def monitor_thread():
                    try:
                        while True:
                            line = proc.stdout.readline()
                            if not line:
                                break
                            
                            line_str = line.strip()
                            if line_str.startswith("[PROGRESS]"):
                                try:
                                    # Format: [PROGRESS] curr/total : filename
                                    parts = line_str.split(":", 1)
                                    prog_info = parts[0].replace("[PROGRESS]", "").strip()
                                    file_name = parts[1].strip()
                                    curr_c, total_c = map(int, prog_info.split("/"))
                                    
                                    # Schedule UI update on main thread
                                    self.after(0, lambda c=curr_c, t=total_c, fn=file_name: update_progress(c, t, fn))
                                except Exception as e:
                                    print(f"Error parsing progress output: {e}")
                            else:
                                if "Switching to configuration:" in line_str:
                                    try:
                                        config_part = line_str.split("Switching to configuration:")[1].strip()
                                        if " for STEP export" in config_part:
                                            config_part = config_part.split(" for STEP export")[0].strip()
                                        self.after(0, lambda conf=config_part: update_config_progress(conf))
                                    except Exception as e:
                                        print(f"Error parsing configuration progress: {e}")
                                        
                                if line_str:
                                    self.after(0, lambda msg=line_str: self.write_log(f"SolidWorks: {msg}", "info"))
                    except Exception as thread_e:
                        print(f"Error in monitor thread: {thread_e}")
                    finally:
                        proc.wait()
                        self.after(0, lambda: on_finish(proc.returncode, job_path))
                        
                # Start Thread
                threading.Thread(target=monitor_thread, daemon=True).start()
                
            except Exception as run_err:
                messagebox.showerror("Error", f"Failed to start background export process: {run_err}")
                if os.path.exists(job_path):
                    try:
                        os.remove(job_path)
                    except:
                        pass
                return
            
        def info_action():
            filtered_files, formats = get_filtered_files_list()
            
            # Calculate counts
            count_slddrw = 0
            count_sldprt = 0
            count_sldasm = 0
            for f in filtered_files:
                f_lower = f.lower()
                if f_lower.endswith(".slddrw"):
                    count_slddrw += 1
                elif f_lower.endswith(".sldprt"):
                    count_sldprt += 1
                elif f_lower.endswith(".sldasm"):
                    count_sldasm += 1
            
            # Sort file list by extension
            filtered_files.sort(key=lambda x: (os.path.splitext(x)[1].lower(), x.lower()))
            
            # Open INFO dialog window
            info_pop = tk.Toplevel(pop)
            info_pop.title("EXPORT Target Files Information")
            info_pop.geometry("600x600")
            info_pop.resizable(False, False)
            info_pop.configure(bg="#f3f4f6")
            info_pop.transient(pop)
            info_pop.grab_set()
            
            # Title Card / Header
            header_card = ttk.Frame(info_pop, style="Card.TFrame")
            header_card.pack(fill="x", padx=15, pady=(15, 10))
            
            lbl_info_title = ttk.Label(header_card, text="Target Summary Statistics", style="TLabel", font="TkDefaultFont", background="#ffffff")
            lbl_info_title.pack(anchor="w", padx=10, pady=(6, 2))
            
            stat_text = (
                f"• Drawings (.slddrw): {count_slddrw} files\n"
                f"• Parts (.sldprt): {count_sldprt} files\n"
                f"• Assemblies (.sldasm): {count_sldasm} files\n"
                f"• Total Matched Targets: {len(filtered_files)} files"
            )
            lbl_stats = ttk.Label(header_card, text=stat_text, style="Card.TLabel", justify="left", font="TkDefaultFont")
            lbl_stats.pack(anchor="w", padx=10, pady=(2, 8))
            
            # Table Card
            table_card = ttk.Frame(info_pop, style="Card.TFrame")
            table_card.pack(fill="both", expand=True, padx=15, pady=5)
            
            # Scrollable Treeview Container
            tbl_container = ttk.Frame(table_card, style="TFrame")
            tbl_container.pack(fill="both", expand=True, padx=10, pady=(10, 5))
            
            # Two columns: path, active
            tree = ttk.Treeview(tbl_container, columns=("path", "active"), show="headings", selectmode="extended")
            tree.heading("path", text="Relative File Path", anchor="w")
            tree.heading("active", text="Active", anchor="center")
            tree.column("path", width=400, anchor="w")
            tree.column("active", width=80, anchor="center")
            
            # Setup tags for status color coding
            tree.tag_configure("active_on", foreground="#1d4ed8")  # Blue for On
            tree.tag_configure("active_off", foreground="#4b5563") # Dark Gray for Off
            
            # Scrollbar
            sb = ttk.Scrollbar(tbl_container, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=sb.set)
            
            tree.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            
            # Ctrl+A binding for select all
            def select_all(event):
                tree.selection_set(tree.get_children())
                return "break"
            tree.bind("<Control-a>", select_all)
            
            # Insert items with extension tag and preserve active states
            for f in filtered_files:
                is_on = True
                if getattr(self, "_export_active_files", None) is not None:
                    is_on = (f in self._export_active_files)
                    
                active_str = "On" if is_on else "Off"
                tag = "active_on" if is_on else "active_off"
                
                tree.insert("", "end", values=(f, active_str), tags=(tag,))
                
            # Control Buttons Panel (On / Off) inside the table card
            ctrl_btn_frm = ttk.Frame(table_card, style="TFrame")
            ctrl_btn_frm.pack(fill="x", side="bottom", padx=10, pady=(5, 10))
            
            def set_active_status(status):
                selected = tree.selection()
                if not selected:
                    return
                for item in selected:
                    # Update column value
                    tree.set(item, "active", status)
                    # Update tags
                    tag = "active_on" if status == "On" else "active_off"
                    tree.item(item, tags=(tag,))
                    
            btn_on = ttk.Button(ctrl_btn_frm, text="On", style="Primary.TButton", command=lambda: set_active_status("On"))
            btn_on.pack(side="left", padx=(0, 5))
            
            btn_off = ttk.Button(ctrl_btn_frm, text="Off", command=lambda: set_active_status("Off"))
            btn_off.pack(side="left", padx=5)
            
            # Close action with caching active files
            def save_and_close():
                active_list = []
                for item in tree.get_children():
                    path_val = tree.set(item, "path")
                    active_val = tree.set(item, "active")
                    if active_val == "On":
                        active_list.append(path_val)
                self._export_active_files = active_list
                self.write_log(f"ℹ️ Export target list adjusted in INFO: {len(active_list)} of {len(tree.get_children())} files active.", "info")
                info_pop.destroy()
                
            # Close button
            btn_close = ttk.Button(info_pop, text="Close", command=save_and_close)
            btn_close.pack(pady=15)

        # Buttons Panel
        btn_frm = ttk.Frame(pop, style="TFrame")
        btn_frm.pack(fill="x", pady=(10, 15), padx=20)
        
        btn_start = ttk.Button(btn_frm, text="Start", style="Primary.TButton", command=start_action)
        btn_start.pack(side="left", expand=True, fill="x", padx=(0, 5))
        
        btn_info = ttk.Button(btn_frm, text="INFO", style="TButton", command=info_action)
        btn_info.pack(side="left", expand=True, fill="x", padx=5)
        
        btn_cancel = ttk.Button(btn_frm, text="Cancel", style="TButton", command=pop.destroy)
        btn_cancel.pack(side="right", expand=True, fill="x", padx=(5, 0))



    @queue_during_bg_tasks
    def open_solidworks(self):
        files = self.get_selected_file_abs_paths()
        if not files:
            self.write_log("Please select at least one SolidWorks file first.", "warning")
            return
            
        def run():
            self.increment_tasks()
            try:
                # 1. Fetch remote locks to adjust local file write permissions for ALL files locked by us/others in the repo.
                locks = {}
                if self.git_service.is_git_repo():
                    try:
                        self.write_log("Analyzing LFS locks to adjust local file write permissions...", "info")
                        locks = self.git_service.get_lfs_locks()
                        
                        import stat
                        cleared_count = 0
                        marked_ro_count = 0
                        for rel_path, lock_info in locks.items():
                            abs_path = os.path.abspath(os.path.join(self.git_service.repo_path, rel_path))
                            if os.path.exists(abs_path):
                                try:
                                    mode = os.stat(abs_path).st_mode
                                    if lock_info.get('is_ours'):
                                        if not (mode & stat.S_IWRITE):
                                            os.chmod(abs_path, mode | stat.S_IWRITE)
                                            cleared_count += 1
                                    else:
                                        if (mode & stat.S_IWRITE):
                                            os.chmod(abs_path, mode & ~stat.S_IWRITE)
                                            marked_ro_count += 1
                                except Exception as ce:
                                    print(f"Failed to adjust attributes on lock file '{abs_path}': {ce}")
                        
                        log_msg = []
                        if cleared_count > 0:
                            log_msg.append(f"cleared read-only on {cleared_count} of your locked files")
                        if marked_ro_count > 0:
                            log_msg.append(f"marked {marked_ro_count} files locked by others as read-only")
                        
                        if log_msg:
                            self.write_log("Synced file permissions: " + ", ".join(log_msg) + ".", "success")
                        else:
                            self.write_log("No repository files needed write-permission adjustments.", "info")
                    except Exception as le:
                        self.write_log(f"Failed to check locks and adjust attributes: {le}", "error")

                # Normalize locks keys to lowercase with forward slashes
                locks_lower = {}
                if self.git_service.is_git_repo():
                    try:
                        locks_lower = {k.lower().replace('\\', '/'): v for k, v in locks.items()}
                    except Exception:
                        pass

                # Determine open options and disk attribute for each file
                file_open_settings = {}
                import stat
                
                for file in files:
                    abs_file_path = os.path.abspath(file)
                    file_rel = os.path.relpath(abs_file_path, self.git_service.repo_path).replace('\\', '/').lower()
                    
                    is_ours = False
                    if not self.git_service.is_git_repo():
                        is_ours = True
                    else:
                        if file_rel in locks_lower:
                            is_ours = locks_lower[file_rel].get('is_ours', False)
                        else:
                            # Try to lock it
                            try:
                                self.write_log(f"Attempting to lock '{os.path.basename(file)}'...", "info")
                                self.git_service.lock_file(file)
                                is_ours = True
                                locks_lower[file_rel] = {'is_ours': True, 'owner': 'me'}
                                self.write_log(f"Successfully locked '{os.path.basename(file)}'.", "success")
                            except Exception as le:
                                self.write_log(f"Failed to lock '{os.path.basename(file)}' (will open as Read-Only): {le}", "warning")
                                is_ours = False
                    
                    # Set appropriate permissions on disk and record target open options
                    if is_ours:
                        try:
                            if os.path.exists(abs_file_path):
                                mode = os.stat(abs_file_path).st_mode
                                os.chmod(abs_file_path, mode | stat.S_IWRITE)
                        except Exception as chmod_e:
                            print(f"Failed to clear read-only on '{abs_file_path}': {chmod_e}")
                        file_open_settings[abs_file_path] = {
                            'options': 1,  # swOpenDocOptions_Silent (read-write)
                            'is_ours': True
                        }
                    else:
                        try:
                            if os.path.exists(abs_file_path):
                                mode = os.stat(abs_file_path).st_mode
                                os.chmod(abs_file_path, mode & ~stat.S_IWRITE)
                        except Exception as chmod_e:
                            print(f"Failed to set read-only on '{abs_file_path}': {chmod_e}")
                        file_open_settings[abs_file_path] = {
                            'options': 1 | 2,  # swOpenDocOptions_Silent | swOpenDocOptions_ReadOnly
                            'is_ours': False
                        }

                # 1. Try to connect to an existing SolidWorks instance
                sw_app = self.sw_service._get_sw_app()
                if sw_app:
                    import pythoncom
                    import win32com.client
                    pythoncom.CoInitialize()
                    
                    errors_ref = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
                    warnings_ref = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
                    
                    for file in files:
                        abs_file_path = os.path.abspath(file)
                        ext = os.path.splitext(file)[1].lower()
                        
                        doc_type = 1  # swDocPART
                        if ext == '.sldprt':
                            doc_type = 1
                        elif ext == '.sldasm':
                            doc_type = 2
                        elif ext == '.slddrw':
                            doc_type = 3
                            
                        try:
                            settings = file_open_settings.get(abs_file_path, {'options': 1, 'is_ours': True})
                            open_opt = settings['options']
                            is_ours_lock = settings['is_ours']
                            
                            # Open Doc
                            doc = sw_app.OpenDoc6(abs_file_path, doc_type, open_opt, "", errors_ref, warnings_ref)
                            if doc:
                                try:
                                    title = self.sw_service._call_com_method(doc, 'GetTitle')
                                    # Option: 2 = swUserDecision
                                    self.sw_service._call_com_method(sw_app, 'ActivateDoc3', title, True, 2, errors_ref)
                                    if is_ours_lock:
                                        # Add to active locked files set in UI to track local status
                                        self.files_locked_by_us.add(abs_file_path.lower())
                                except Exception as ae:
                                    print(f"Failed to activate document: {ae}")
                                self.task_queue.put(('success', f"Opened '{os.path.basename(file)}' in the running SolidWorks.", None))
                            else:
                                self.task_queue.put(('error', f"Failed to open '{os.path.basename(file)}' in running SolidWorks. (OpenDoc6 returned NULL)", None))
                        except Exception as e:
                            self.task_queue.put(('error', f"Failed to open '{os.path.basename(file)}' in running SolidWorks: {e}", None))
                    return
                
                # 2. SolidWorks is not running, launch a new instance
                path = self.load_solidworks_path()
                if os.path.exists(path):
                    errors = []
                    import subprocess
                    for file in files:
                        abs_file_path = os.path.abspath(file)
                        settings = file_open_settings.get(abs_file_path, {'options': 1, 'is_ours': True})
                        is_ours_lock = settings['is_ours']
                        
                        try:
                            subprocess.Popen([path, abs_file_path])
                            if is_ours_lock:
                                self.files_locked_by_us.add(abs_file_path.lower())
                        except Exception as e:
                            errors.append(f"Failed to open {os.path.basename(file)} in SolidWorks: {e}")
                    if errors:
                        self.task_queue.put(('error', "\n".join(errors), None))
                else:
                    errors = []
                    for file in files:
                        abs_file_path = os.path.abspath(file)
                        settings = file_open_settings.get(abs_file_path, {'options': 1, 'is_ours': True})
                        is_ours_lock = settings['is_ours']
                        
                        try:
                            os.startfile(abs_file_path)
                            if is_ours_lock:
                                self.files_locked_by_us.add(abs_file_path.lower())
                        except Exception as e:
                            errors.append(f"Failed to open {os.path.basename(file)} in SolidWorks: {e}")
                    if errors:
                        err_msg = f"SolidWorks executable not found at path: {path}. Fallback failed:\n" + "\n".join(errors)
                        self.task_queue.put(('error', err_msg, None))
                    else:
                        warn_msg = f"SolidWorks executable not found at '{path}'. Opened using system default association."
                        self.task_queue.put(('success', warn_msg, None))
            finally:
                self.decrement_tasks()
                
        threading.Thread(target=run, daemon=True).start()

    @queue_during_bg_tasks
    def discard_changes(self):
        selected_items = self.tree.selection()
        if not selected_items:
            self.write_log("Select at least one file to discard changes.", "warning")
            return
            
        files_to_discard = [self.tree.item(item, 'values')[0] for item in selected_items]
        
        ans = messagebox.askyesno(
            "Confirm Discard", 
            f"Are you sure you want to discard all local changes for the selected {len(files_to_discard)} files?\n\n"
            "⚠️ Warning: This will overwrite or delete your local modifications and cannot be undone."
        )
        if not ans:
            return
            
        self.btn_discard.state(["disabled"])
        
        def run():
            self.increment_tasks()
            try:
                # Run check_sw_open_state in worker thread
                for file_rel_path in files_to_discard:
                    if not self.check_sw_open_state(file_rel_path):
                        return
                        
                success_count = 0
                errors = []
                for file_rel_path in files_to_discard:
                    try:
                        self.git_service.discard_changes([file_rel_path])
                        success_count += 1
                    except Exception as e:
                        errors.append(f"Failed to discard {file_rel_path}: {e}")
                
                if errors:
                    err_msg = "\n".join(errors)
                    self.task_queue.put(('error', f"Discarded changes for {success_count} files, but errors occurred:\n\n{err_msg}", self.refresh_file_list))
                else:
                    self.task_queue.put(('success', f"Discarded changes for {success_count} files successfully!", self.refresh_file_list))
            finally:
                self.decrement_tasks()
                
        threading.Thread(target=run, daemon=True).start()


    def _show_conflict_dialog(self, conflicted_files, source_branch, current_branch):
        """Shows a modal dialog for merge conflict resolution.
        
        Lets the user pick:
          - source (main) branch version  → git checkout --theirs
          - current branch version         → git checkout --ours
          - Abort merge                    → git merge --abort
        """
        dialog = tk.Toplevel(self)
        dialog.title("⚠️ Merge Conflict Occurred")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()

        # --- Header ---
        tk.Label(
            dialog,
            text="A merge conflict has occurred",
            fg="#b91c1c",
            bg="#fff7f7",
            padx=20, pady=12
        ).pack(fill="x")

        tk.Label(
            dialog,
            text=f"  Conflict detected in the following files during merge from '{source_branch}' to '{current_branch}':\n"
                 f"  Please select which version to adopt.",
            fg="#374151",
            bg="#ffffff",
            justify="left",
            padx=20, pady=6
        ).pack(fill="x")

        # --- Conflicted files list ---
        list_frm = tk.Frame(dialog, bg="#f3f4f6", bd=1, relief="solid")
        list_frm.pack(fill="both", padx=20, pady=(0, 12))

        scrollbar = tk.Scrollbar(list_frm)
        scrollbar.pack(side="right", fill="y")

        listbox = tk.Listbox(
            list_frm,
            yscrollcommand=scrollbar.set,
            bg="#f3f4f6",
            fg="#1f2937",
            selectbackground="#e5e7eb",
            height=min(len(conflicted_files), 8),
            bd=0,
            highlightthickness=0
        )
        for f in conflicted_files:
            listbox.insert("end", f"  ⚠  {f}")
        listbox.pack(fill="both", expand=True)
        scrollbar.config(command=listbox.yview)

        # --- Choice buttons ---
        choice = tk.StringVar(value="")

        btn_frm = tk.Frame(dialog, bg="#ffffff", pady=12)
        btn_frm.pack(fill="x", padx=20)

        def choose(c):
            choice.set(c)
            dialog.destroy()

        tk.Button(
            btn_frm,
            text=f"🔵  Adopt version from '{source_branch}'",
            bg="#2563eb", fg="white",
            activebackground="#1d4ed8", activeforeground="white",
            padx=12, pady=6, bd=0, cursor="hand2",
            command=lambda: choose("theirs")
        ).pack(fill="x", pady=(0, 6))

        tk.Button(
            btn_frm,
            text=f"🟢  Adopt version from current '{current_branch}'",
            bg="#059669", fg="white",
            activebackground="#047857", activeforeground="white",
            padx=12, pady=6, bd=0, cursor="hand2",
            command=lambda: choose("ours")
        ).pack(fill="x", pady=(0, 6))

        tk.Button(
            btn_frm,
            text="🔴  Abort Merge",
            bg="#e5e7eb", fg="#374151",
            activebackground="#d1d5db", activeforeground="#111827",
            padx=12, pady=6, bd=0, cursor="hand2",
            command=lambda: choose("abort")
        ).pack(fill="x")

        # Center dialog over parent
        dialog.update_idletasks()
        pw = self.winfo_rootx() + self.winfo_width() // 2 - dialog.winfo_width() // 2
        ph = self.winfo_rooty() + self.winfo_height() // 2 - dialog.winfo_height() // 2
        dialog.geometry(f"+{pw}+{ph}")

        # Block until user responds
        self.wait_window(dialog)

        if not choice.get() or choice.get() == "abort":
            # Abort merge in background
            def do_abort():
                self.increment_tasks()
                try:
                    try:
                        self.git_service.abort_merge()
                        self.task_queue.put(('success', "Merge aborted. Restored to pre-merge state.", self.refresh_dashboard))
                    except Exception as e:
                        self.task_queue.put(('error', f"Error while aborting merge:\n{e}", None))
                finally:
                    self.decrement_tasks()
            threading.Thread(target=do_abort, daemon=True).start()
        else:
            strategy = choice.get()  # 'ours' or 'theirs'
            label = f"'{source_branch}' (main)" if strategy == "theirs" else f"'{current_branch}' (current)"

            def do_resolve():
                self.increment_tasks()
                try:
                    try:
                        result = self.git_service.resolve_merge_conflicts(strategy, conflicted_files)
                        
                        # 1. Push current branch to remote
                        push_msg = ""
                        remote_url = self.git_service.get_remote_url()
                        if remote_url and current_branch:
                            self.write_log(f"Pushing merged branch '{current_branch}' to remote...", "info")
                            try:
                                self.git_service._run_lfs_cmd(["git", "push", "-u", "origin", current_branch])
                                push_msg = f"\n\nSuccessfully pushed branch '{current_branch}' to remote."
                            except Exception as pe:
                                push_msg = f"\n\nWarning: Push failed:\n{pe}"
                                
                        # 2. Return to the original branch state (ensure checkout)
                        try:
                            self.git_service.switch_branch(current_branch, force=False)
                        except Exception as se:
                            print(f"Failed to switch back to original branch '{current_branch}': {se}")

                        self.task_queue.put((
                            'success',
                            f"Resolved conflicts using version from {label} and completed merge.\n\n{result}{push_msg}",
                            self.refresh_dashboard
                        ))
                    except Exception as e:
                        self.task_queue.put(('error', f"Error while resolving conflicts:\n{e}", None))
                finally:
                    self.decrement_tasks()
            threading.Thread(target=do_resolve, daemon=True).start()

    def process_queue(self):
        """Processes signals from background threads to avoid freezing the UI thread."""
        try:
            while True:
                msg_type, content, callback = self.task_queue.get_nowait()
                
                # Update bg_tasks_count first
                if msg_type == 'bg_task_start':
                    self.bg_tasks_count += 1
                    self.lbl_status_indicator.config(text="● Working", fg="#ef4444")
                    self.update_terminate_btn_state(True)
                    self.on_file_selected_change()
                elif msg_type == 'bg_task_end':
                    self.bg_tasks_count = max(0, self.bg_tasks_count - 1)
                    if self.bg_tasks_count == 0:
                        self.lbl_status_indicator.config(text="● Idle", fg="#10b981")
                        self.update_terminate_btn_state(False)
                        if self.pending_button_tasks:
                            next_task = self.pending_button_tasks.pop(0)
                            self.after(10, next_task)
                
                # Only restore button states when no background tasks are running
                if self.bg_tasks_count == 0:
                    is_repo = self.git_service.is_git_repo()
                    self.btn_lock.config(text="Lock")
                    if is_repo:
                        self.btn_lock.state(["!disabled"])
                        self.btn_unlock.state(["!disabled"])
                        self.btn_force_unlock.state(["!disabled"])
                        self.btn_save_ver.state(["!disabled"])
                        self.btn_save_all.state(["!disabled"])
                        self.btn_sync.state(["!disabled"])
                        if hasattr(self, 'btn_cleanup_lfs'):
                            self.btn_cleanup_lfs.state(["!disabled"])
                        self.btn_merge.state(["!disabled"])
                        self.btn_discard.state(["!disabled"])
                        if hasattr(self, 'btn_clone'):
                            self.btn_clone.state(["!disabled"])
                    else:
                        self.btn_lock.state(["disabled"])
                        self.btn_unlock.state(["disabled"])
                        self.btn_force_unlock.state(["disabled"])
                        self.btn_save_ver.state(["disabled"])
                        self.btn_save_all.state(["disabled"])
                        self.btn_sync.state(["disabled"])
                        if hasattr(self, 'btn_cleanup_lfs'):
                            self.btn_cleanup_lfs.state(["disabled"])
                        self.btn_merge.state(["disabled"])
                        self.btn_discard.state(["disabled"])
                        if hasattr(self, 'btn_clone'):
                            self.btn_clone.state(["!disabled"])
                            
                    self.btn_save_ver.config(text="Upload Selected File Version")
                    self.btn_save_all.config(text="Upload Every Files Version")
                    self.btn_sync.config(text="Get Latest Version (Sync)")
                    self.btn_restore.state(["!disabled"])
                    self.btn_restore_latest.state(["!disabled"])
                    self.btn_edrawings.state(["!disabled"])
                    self.btn_solidworks.state(["!disabled"])
                    self.on_file_selected_change()
                
                if msg_type == 'success':
                    self.write_log(content, "success")
                elif msg_type == 'error':
                    self.write_log(content, "error")
                elif msg_type == 'silent_error':
                    print(content)
                elif msg_type == 'log':
                    msg_content, log_type = content
                    self.write_log(msg_content, log_type)
                elif msg_type == 'merge_conflict':
                    self._show_conflict_dialog(
                        content['files'],
                        content['source'],
                        content['current']
                    )
                elif msg_type == 'sw_status':
                    # SolidWorks live monitor state update
                    if content is not None:
                        if isinstance(content, dict):
                            self.lbl_sw_status_active.config(text=f"• SolidWorks: {content.get('active', '-')}")
                            self.lbl_sw_status_open.config(text=f"• Opened Files: {content.get('open_files', '-')}")
                            self.lbl_sw_status_locked.config(text=f"• Locked Files: {content.get('locked_files', '-')}")
                            self.lbl_sw_status_total.config(text=f"• Total Files: {content.get('total_files', '-')}")
                            self.lbl_sw_status_repo_size.config(text=f"• Repository Size: {content.get('repo_size', '-')}")
                        else:
                            self.write_log(content, "info")
                    
                if callback:
                    callback()
                    
        except queue.Empty:
            pass
            
        self.after(100, self.process_queue)

    def _monitor_sw_loop(self):
        """Runs in a background thread to poll SolidWorks active files without blocking UI thread."""
        while self.sw_monitor_active:
            try:
                open_docs = self.sw_service.get_all_open_documents()
                current_open_files = set()
                repo_path_norm = os.path.abspath(self.git_service.repo_path).replace("\\", "/")
                
                # 1. Resolve relative paths of open files in repo
                for doc in open_docs:
                    filepath = doc['path']
                    if filepath:
                        filepath_norm = os.path.abspath(filepath).replace("\\", "/")
                        if filepath_norm.lower().startswith(repo_path_norm.lower()):
                            rel_path = filepath_norm[len(repo_path_norm):].strip("/")
                            if rel_path:
                                corrected_path = self.git_service.get_correct_filepath_casing(rel_path)
                                current_open_files.add(corrected_path)
                
                curr_open_lower = {f.lower() for f in current_open_files}
                last_open_lower = {f.lower() for f in self.last_open_files}

                # 2. Fetch locks only when the set of open files actually changes
                locks = {}
                if curr_open_lower != last_open_lower:
                    try:
                        locks = self.git_service.get_lfs_locks()
                    except Exception as e:
                        print(f"Error fetching LFS locks: {e}")
                
                locks_lower = {k.lower().replace("\\", "/"): v for k, v in locks.items()}
                
                # 3. Detect newly opened files -> Auto Lock (case-insensitive checks)
                for rel_path in current_open_files:
                    was_open = any(f.lower() == rel_path.lower() for f in self.last_open_files)
                    if not was_open:
                        # File just opened!
                        rel_path_lower = rel_path.lower().replace("\\", "/")
                        locked_by_others = False
                        
                        # A. Check newly fetched locks from remote
                        if rel_path_lower in locks_lower:
                            is_ours = locks_lower[rel_path_lower]['is_ours']
                            if is_ours:
                                # Already locked by us, track it
                                self.files_locked_by_us.add(rel_path)
                            else:
                                locked_by_others = True
                        
                        # B. Check cached files_data in case remote lock fetch was bypassed or failed
                        if not locked_by_others and getattr(self, 'files_data', None):
                            for f in self.files_data:
                                if f['file'].lower().replace("\\", "/") == rel_path_lower:
                                    if f.get('locked', False):
                                        if f.get('is_our_lock', False):
                                            self.files_locked_by_us.add(rel_path)
                                        else:
                                            locked_by_others = True
                                    break
                                    
                        if locked_by_others:
                            # Locked by someone else, we shouldn't lock it
                            pass
                        else:
                            # Not locked, lock it!
                            is_already_locked_by_us = any(x.lower() == rel_path_lower for x in self.files_locked_by_us)
                            if not is_already_locked_by_us:
                                def run_lock(path_to_lock):
                                    self.increment_tasks()
                                    success = False
                                    try:
                                        self.git_service.lock_file(path_to_lock)
                                        self.files_locked_by_us.add(path_to_lock)
                                        self.task_queue.put(('sw_status', f"Automatically locked {path_to_lock}", None))
                                        success = True
                                    except Exception as e:
                                        self.task_queue.put(('silent_error', f"Auto-lock failed for {path_to_lock}: {e}", None))
                                    finally:
                                        self.decrement_tasks()
                                        
                                    if success:
                                        import time
                                        time.sleep(1.5)
                                        if self.bg_tasks_count == 0:
                                            self.task_queue.put(('callback', None, self.refresh_file_list))
                                        
                                threading.Thread(target=run_lock, args=(rel_path,), daemon=True).start()
                
                # 4. Detect closed files -> Auto Unlock (case-insensitive checks)
                for rel_path in self.last_open_files:
                    is_still_open = any(f.lower() == rel_path.lower() for f in current_open_files)
                    if not is_still_open:
                        # File was closed!
                        is_locked_by_us = False
                        matched_path = None
                        for f in self.files_locked_by_us:
                            if f.lower() == rel_path.lower():
                                is_locked_by_us = True
                                matched_path = f
                                break
                                
                        if is_locked_by_us:
                            # Verify that the file is not locked by another user
                            rel_path_lower = rel_path.lower().replace("\\", "/")
                            locked_by_others = False
                            
                            # A. Check remote locks
                            if rel_path_lower in locks_lower:
                                is_ours = locks_lower[rel_path_lower]['is_ours']
                                if not is_ours:
                                    locked_by_others = True
                                    
                            # B. Check cached files_data
                            if not locked_by_others and getattr(self, 'files_data', None):
                                for f in self.files_data:
                                    if f['file'].lower().replace("\\", "/") == rel_path_lower:
                                        if f.get('locked', False) and not f.get('is_our_lock', False):
                                            locked_by_others = True
                                        break
                                        
                            if locked_by_others:
                                # Locked by someone else, clean up our tracking set and do not unlock
                                if matched_path in self.files_locked_by_us:
                                    self.files_locked_by_us.remove(matched_path)
                            else:
                                def run_unlock(path_to_unlock, path_to_remove):
                                    self.increment_tasks()
                                    success = False
                                    try:
                                        self.git_service.unlock_file(path_to_unlock)
                                        if path_to_remove in self.files_locked_by_us:
                                            self.files_locked_by_us.remove(path_to_remove)
                                        self.task_queue.put(('sw_status', f"Automatically unlocked {path_to_unlock}", None))
                                        success = True
                                    except Exception as e:
                                        self.task_queue.put(('silent_error', f"Auto-unlock failed for {path_to_unlock}: {e}", None))
                                    finally:
                                        self.decrement_tasks()
                                        
                                    if success:
                                        import time
                                        time.sleep(1.5)
                                        if self.bg_tasks_count == 0:
                                            self.task_queue.put(('callback', None, self.refresh_file_list))
                                        
                                threading.Thread(target=run_unlock, args=(rel_path, matched_path), daemon=True).start()
                            
                # 5. Build status message/dictionary for Dashboard
                is_active = self.sw_service._get_sw_app() is not None
                active_text = "Active" if is_active else "Inactive"
                total_files = len(self.files_data) if getattr(self, 'files_data', None) else 0
                num_open = len(open_docs) if open_docs else 0
                num_locked = sum(1 for f in self.files_data if f.get('locked')) if getattr(self, 'files_data', None) else 0
                
                # Calculate Repository Size
                repo_size = 0
                try:
                    for dirpath, dirnames, filenames in os.walk(self.git_service.repo_path):
                        for f in filenames:
                            fp = os.path.join(dirpath, f)
                            if not os.path.islink(fp):
                                repo_size += os.path.getsize(fp)
                except Exception as e:
                    print(f"Error walking repo path for size: {e}")

                def format_size(size_in_bytes):
                    if size_in_bytes < 1024:
                        return f"{size_in_bytes} B"
                    elif size_in_bytes < 1024 * 1024:
                        return f"{size_in_bytes / 1024:.2f} KB"
                    elif size_in_bytes < 1024 * 1024 * 1024:
                        return f"{size_in_bytes / (1024 * 1024):.2f} MB"
                    else:
                        return f"{size_in_bytes / (1024 * 1024 * 1024):.2f} GB"

                repo_size_str = format_size(repo_size)

                status_data = {
                    'active': active_text,
                    'total_files': total_files,
                    'open_files': num_open,
                    'locked_files': num_locked,
                    'repo_size': repo_size_str
                }
                
                # Update status label
                # If the set of open files changed (comparing normalized sets case-insensitively), trigger refresh
                if curr_open_lower != last_open_lower:
                    if self.bg_tasks_count == 0:
                        self.task_queue.put(('sw_status', status_data, self.refresh_file_list))
                    else:
                        self.task_queue.put(('sw_status', status_data, None))
                else:
                    self.task_queue.put(('sw_status', status_data, None))
                
                # 6. Save current set for next iteration
                self.last_open_files = current_open_files
                
            except Exception as e:
                print(f"Error in SolidWorks background monitor loop: {e}")
                
            # Dynamic polling interval: 6.0 seconds if SolidWorks is running, 12.0 seconds if not.
            poll_interval = 6.0 if (self.sw_service._get_sw_app() is not None) else 12.0
            threading.Event().wait(poll_interval)

    def destroy(self):
        self.sw_monitor_active = False
        super().destroy()