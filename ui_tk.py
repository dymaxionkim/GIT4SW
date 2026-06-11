import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import tkinter.font as tkfont
import threading
import queue
import json
import webbrowser

from git_service import GitService, MergeConflictError
from sw_monitor import SolidWorksMonitorService


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
    def __init__(self, parent, conflicted_files, ours_label, theirs_label):
        super().__init__(parent)
        self.title("Resolve Merge Conflicts")
        self.geometry("680x480")
        self.configure(bg="#f3f4f6")
        self.transient(parent)
        self.grab_set()
        
        self.result = None  # Dict {file: 'ours'|'theirs'} or None
        self.resolutions = {}  # Dict {file: StringVar}
        
        # Title/Header
        lbl = ttk.Label(self, text="Conflicts detected! Choose which version to adopt for each file:", 
                        wraplength=640, justify="left", style="TLabel", font=("TkDefaultFont", 10, "bold"))
        lbl.pack(padx=16, pady=12, fill="x")
        
        # Scrollable container for files
        container = ttk.Frame(self, style="Card.TFrame")
        container.pack(padx=16, pady=4, fill="both", expand=True)
        
        # Canvas and scrollbar for scrolling if many files
        canvas = tk.Canvas(container, bg="#ffffff", bd=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg="#ffffff")
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(
                scrollregion=canvas.bbox("all")
            )
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # List of conflicted files
        for idx, f in enumerate(conflicted_files):
            row_frm = tk.Frame(scrollable_frame, bg="#ffffff")
            row_frm.pack(fill="x", padx=12, pady=6)
            
            # File name
            lbl_file = tk.Label(row_frm, text=f, bg="#ffffff", fg="#1f2937", font=("TkDefaultFont", 9), width=30, anchor="w", wraplength=220, justify="left")
            lbl_file.pack(side="left", padx=(0, 10))
            
            # Choice Var (default to ours)
            var = tk.StringVar(value="ours")
            self.resolutions[f] = var
            
            # Toggle buttons (Radiobutton styled as buttons)
            choice_frm = tk.Frame(row_frm, bg="#ffffff")
            choice_frm.pack(side="left", fill="x", expand=True)
            
            rb_ours = tk.Radiobutton(
                choice_frm, 
                text=ours_label, 
                value="ours", 
                variable=var, 
                indicatoron=False,
                width=18,
                padx=8,
                pady=4,
                bg="#f3f4f6",
                fg="#1f2937",
                selectcolor="#d1fae5",    # Green background when selected
                activebackground="#e5e7eb",
                relief="flat",
                bd=1,
                highlightthickness=0
            )
            rb_ours.pack(side="left", padx=4)
            
            rb_theirs = tk.Radiobutton(
                choice_frm, 
                text=theirs_label, 
                value="theirs", 
                variable=var, 
                indicatoron=False,
                width=18,
                padx=8,
                pady=4,
                bg="#f3f4f6",
                fg="#1f2937",
                selectcolor="#ffe4e6",    # Rose/red background when selected
                activebackground="#e5e7eb",
                relief="flat",
                bd=1,
                highlightthickness=0
            )
            rb_theirs.pack(side="left", padx=4)
            
            # Divider between rows
            if idx < len(conflicted_files) - 1:
                row_div = tk.Frame(scrollable_frame, bg="#f3f4f6", height=1)
                row_div.pack(fill="x", padx=12, pady=4)
            
        # Action Buttons Frame
        btn_frm = ttk.Frame(self, style="TFrame")
        btn_frm.pack(padx=16, pady=16, fill="x", side="bottom")
        
        btn_ok = ttk.Button(btn_frm, text="Apply Resolution", style="Primary.TButton", command=self.on_ok)
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
        self.result = {f: var.get() for f, var in self.resolutions.items()}
        self.destroy()
        
    def on_cancel(self):
        self.result = None
        self.destroy()


class GIT4SWApp(tk.Tk):
    def __init__(self, workspace_path):
        super().__init__()
        self.workspace_path = workspace_path
        self.check_and_load_config()
        
        # Initialize Services
        self.git_service = GitService(self.workspace_path)
        self.sw_service = SolidWorksMonitorService()
        
        # Queue for thread communication
        self.task_queue = queue.Queue()
        
        # State tracking for branch switching
        self.is_switching_branch = False
        self.bg_tasks_count = 0
        
        self.title("GIT4SW - SolidWorks Git Client (Tkinter)")
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
                  foreground=[("active", "#111827"), ("disabled", "#f3f4f6")])
                  
        style.configure("Primary.TButton", padding=6, background="#059669", foreground="#ffffff", borderwidth=0)
        style.map("Primary.TButton",
                  background=[("active", "#047857"), ("disabled", "#f3f4f6")],
                  foreground=[("active", "#ffffff"), ("disabled", "#f3f4f6")])
                  
        # [수정] Make my branch 전용 스타일 추가 (비활성화 시 배경 및 텍스트 모두 흰색)
        style.configure("MakeBranch.TButton", padding=6, background="#059669", foreground="#ffffff", borderwidth=0)
        style.map("MakeBranch.TButton",
                  background=[("active", "#047857"), ("disabled", "#ffffff")],
                  foreground=[("active", "#ffffff"), ("disabled", "#ffffff")])

        style.configure("Danger.TButton", padding=6, background="#ef4444", foreground="#ffffff", borderwidth=0)
        style.map("Danger.TButton",
                  background=[("active", "#dc2626"), ("disabled", "#f3f4f6")],
                  foreground=[("active", "#ffffff"), ("disabled", "#f3f4f6")])

        # Treeview (Modern styling)
        style.configure("Treeview", 
                        background=card_color, 
                        fieldbackground=card_color, 
                        foreground=text_color,
                        rowheight=20)
        style.map("Treeview", 
                  background=[("selected", "#e5e7eb")],
                  foreground=[("selected", "#111827")])
        style.configure("Treeview.Heading", 
                        background="#f3f4f6", 
                        foreground="#374151", 
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
        
        self.lbl_repo_branch_info = tk.Label(log_header, text="", fg="#059669", bg="#ffffff", font=("TkDefaultFont", 10, "bold"))
        self.lbl_repo_branch_info.pack(side="left", padx=(12, 0))
        
        btn_clear_log = ttk.Button(log_header, text="Clear", width=8, command=self.clear_log)
        btn_clear_log.pack(side="right")
        
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
        self.btn_dash = tk.Button(sidebar, text=" Dashboard", fg="#374151", bg="#e5e7eb", activebackground="#d1d5db", activeforeground="#111827",
                               font=self.sidebar_font, bd=0, anchor="w", padx=20, command=lambda: self.switch_view(0))
        self.btn_dash.pack(fill="x", pady=(24, 4))
        
        self.btn_files = tk.Button(sidebar, text=" File Manager", fg="#374151", bg="#e5e7eb", activebackground="#d1d5db", activeforeground="#111827",
                               font=self.sidebar_font, bd=0, anchor="w", padx=20, command=lambda: self.switch_view(1))
        self.btn_files.pack(fill="x", pady=4)
        
        self.btn_history = tk.Button(sidebar, text=" History log", fg="#374151", bg="#e5e7eb", activebackground="#d1d5db", activeforeground="#111827",
                                font=self.sidebar_font, bd=0, anchor="w", padx=20, command=lambda: self.switch_view(2))
        self.btn_history.pack(fill="x", pady=4)
               
        self.btn_about = tk.Button(sidebar, text=" About", fg="#374151", bg="#e5e7eb", activebackground="#d1d5db", activeforeground="#111827",
                               font=self.sidebar_font, bd=0, anchor="w", padx=20, command=lambda: self.switch_view(6))
        self.btn_about.pack(fill="x", side="bottom", pady=(4, 24))

        self.btn_help = tk.Button(sidebar, text=" Help", fg="#374151", bg="#e5e7eb", activebackground="#d1d5db", activeforeground="#111827",
                               font=self.sidebar_font, bd=0, anchor="w", padx=20, command=lambda: self.switch_view(5))
        self.btn_help.pack(fill="x", side="bottom", pady=4)

        self.btn_config = tk.Button(sidebar, text=" Config", fg="#374151", bg="#e5e7eb", activebackground="#d1d5db", activeforeground="#111827",
                                font=self.sidebar_font, bd=0, anchor="w", padx=20, command=lambda: self.switch_view(4))
        self.btn_config.pack(fill="x", side="bottom", pady=4)

        self.btn_maintainer = tk.Button(sidebar, text=" Maintainer", fg="#374151", bg="#e5e7eb", activebackground="#d1d5db", activeforeground="#111827",
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

    def update_repo_branch_info(self):
        if not self.git_service or not self.git_service.is_git_repo():
            self.lbl_repo_branch_info.config(text="")
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
                
        self.lbl_repo_branch_info.config(text=f"({repo_name} @ {branch_name})")

    def increment_tasks(self):
        self.task_queue.put(('bg_task_start', None, None))

    def decrement_tasks(self):
        self.task_queue.put(('bg_task_end', None, None))

    def switch_view(self, index):
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
            "github_token",
            "default_local_path",
            "organization_name"
        ]
        
        for k in config_data.keys():
            if k != "workspace_path" and k not in keys_order:
                keys_order.append(k)
                
        for row_idx, key in enumerate(keys_order):
            if key not in config_data:
                continue
                
            val = config_data[key]
            display_name = key.replace("_", " ").title()
            
            # Label
            lbl = ttk.Label(self.config_fields_frame, text=f"{display_name}:", font=("TkDefaultFont", 9, "bold"), anchor="w", background="#ffffff")
            lbl.grid(row=row_idx, column=0, padx=(0, 10), pady=8, sticky="w")
            
            # Entry widget
            ent = ttk.Entry(self.config_fields_frame)
            ent.insert(0, str(val))
            ent.grid(row=row_idx, column=1, padx=0, pady=8, sticky="ew")
            
            # Save a reference to the entry
            self.config_entries[key] = ent

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
        
        lbl_card_title = ttk.Label(card, text="GIT4SW User Guide", style="CardTitle.TLabel")
        lbl_card_title.pack(anchor="w", padx=16, pady=(12, 8))
        
        # Text/Instructions Container
        container = tk.Frame(card, bg="#ffffff")
        container.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        
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
            
        txt_help = tk.Text(container, bg="#ffffff", fg="#1f2937", font="TkDefaultFont", wrap="word", relief="flat")
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
        container = tk.Frame(card, bg="#ffffff")
        container.pack(fill="both", expand=True, padx=16, pady=16)
        
        lbl_app_name = tk.Label(container, text="GIT4SW", fg="#059669", bg="#ffffff", font=("TkDefaultFont", 24, "bold"))
        lbl_app_name.pack(anchor="w", pady=(0, 4))
        
        lbl_subtitle = tk.Label(container, text="SolidWorks Git Version Control Client", fg="#4b5563", bg="#ffffff", font=("TkDefaultFont", 12, "italic"))
        lbl_subtitle.pack(anchor="w", pady=(0, 20))
        
        txt_about = tk.Text(container, bg="#ffffff", fg="#1f2937", font="TkDefaultFont", wrap="word", relief="flat")
        
        # Configure hyperlink tag
        txt_about.tag_config("link", foreground="#2563eb", underline=1)
        txt_about.tag_bind("link", "<Button-1>", lambda e: webbrowser.open_new("https://codeberg.org/dymaxionkim/GIT4SW"))
        txt_about.tag_bind("link", "<Enter>", lambda e: txt_about.config(cursor="hand2"))
        txt_about.tag_bind("link", "<Leave>", lambda e: txt_about.config(cursor=""))
        
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
        
        # Search for link URL and apply tag to make it clickable
        link_url = "https://codeberg.org/dymaxionkim/GIT4SW"
        start_idx = "1.0"
        while True:
            pos = txt_about.search(link_url, start_idx, stopindex=tk.END)
            if not pos:
                break
            end_pos = f"{pos} + {len(link_url)}c"
            txt_about.tag_add("link", pos, end_pos)
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
        
        # Sync Card
        sync_card = ttk.Frame(view, style="Card.TFrame")
        sync_card.pack(fill="x", padx=16, pady=4)
        
        lbl_sync_title = ttk.Label(sync_card, text="Synchronization", style="CardTitle.TLabel")
        lbl_sync_title.pack(anchor="w", padx=12, pady=(8, 2))
        
        lbl_sync_desc = ttk.Label(sync_card, text="Fetch the latest CAD documents from the remote Git server.", style="Card.TLabel")
        lbl_sync_desc.pack(anchor="w", padx=12, pady=1)
        
        sync_btn_frm = ttk.Frame(sync_card, style="Card.TFrame")
        sync_btn_frm.pack(anchor="w", padx=12, pady=(4, 6))

        self.btn_sync = ttk.Button(sync_btn_frm, text="Get Latest Version (Sync)", style="Primary.TButton", command=self.sync_repository)
        self.btn_sync.pack(side="left", padx=(0, 8))

        self.btn_merge = ttk.Button(sync_btn_frm, text="Merge main branch into current branch", style="Primary.TButton", command=self.merge_main_branch)
        self.btn_merge.pack(side="left")

        # SolidWorks Monitor Card
        sw_card = ttk.Frame(view, style="Card.TFrame")
        sw_card.pack(fill="x", padx=16, pady=4)
        
        lbl_sw_title = ttk.Label(sw_card, text="Live Monitor", style="CardTitle.TLabel")
        lbl_sw_title.pack(anchor="w", padx=12, pady=(8, 2))
        
        self.lbl_sw_status = ttk.Label(sw_card, text="No SolidWorks connection or active file.", style="Card.TLabel", wraplength=700)
        self.lbl_sw_status.pack(anchor="w", padx=12, pady=(2, 6))
        
        return view

    def refresh_dashboard(self):
        self.update_repo_branch_info()
        self.ent_local_dir.delete(0, tk.END)
        self.ent_local_dir.insert(0, os.path.normpath(self.workspace_path))
        
        if not self.git_service.is_git_repo():
            self.lbl_local_status.config(text="⚠️ Not a Git Repo", foreground="#ef4444")
            self.btn_sync.state(["disabled"])
            self.btn_clone.state(["!disabled"])
            self.cb_branch.config(values=[], state="disabled")
            # [수정] 비활성화 시 전용 스타일 강제 적용
            self.btn_make_my_branch.config(style="MakeBranch.TButton", state="disabled")
        else:
            self.lbl_local_status.config(text="🟢 Git Repo Active", foreground="#10b981")
            url = self.git_service.get_remote_url()
            self.ent_remote_url.delete(0, tk.END)
            if url:
                self.ent_remote_url.insert(0, url)
            self.btn_sync.state(["!disabled"])
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
                
                # Check if username branch exists
                branch_exists = False
                if username:
                    branch_exists = (username in local_branches) or (username in branches)
                
                def update_ui():
                    self.cb_branch.config(values=branches, state="readonly")
                    if current and current.upper() != "HEAD" and current in branches:
                        self.cb_branch.set(current)
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
        lbl_file_title = ttk.Label(header_frm, text="File Checkout & Check-in", style="Title.TLabel")
        lbl_file_title.pack(side="left")
        btn_refresh = ttk.Button(header_frm, text="Refresh", command=self.refresh_file_list)
        btn_refresh.pack(side="right")
        btn_open = ttk.Button(header_frm, text="Open", command=self.open_workspace_in_explorer)
        btn_open.pack(side="right", padx=(0, 8))
        
        # Path filter combobox (placed to the left of "Open" button)
        self.cb_path_filter = ttk.Combobox(header_frm, state="readonly", width=25)
        self.cb_path_filter.pack(side="right", padx=(0, 8))
        self.cb_path_filter.config(values=["All Files"])
        self.cb_path_filter.set("All Files")
        self.cb_path_filter.bind("<<ComboboxSelected>>", self.on_path_filter_selected)
        
        # Sort order combobox (placed to the left of path filter)
        self.cb_sort_order = ttk.Combobox(header_frm, state="readonly", width=15, values=["by Name", "by Extension"])
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
        
        self.btn_lock = ttk.Button(actions_frm, text="Lock (Checkout)", style="Primary.TButton", command=self.lock_file)
        self.btn_lock.pack(side="left", padx=4)
        
        self.btn_unlock = ttk.Button(actions_frm, text="Unlock File", command=self.unlock_file)
        self.btn_unlock.pack(side="left", padx=4)
        
        self.btn_force_unlock = ttk.Button(actions_frm, text="Force Unlock", style="Danger.TButton", command=self.force_unlock_file)
        self.btn_force_unlock.pack(side="left", padx=4)
        
        self.btn_discard = ttk.Button(actions_frm, text="Discard", style="Danger.TButton", command=self.discard_changes)
        self.btn_discard.pack(side="left", padx=4)
        
        self.btn_edrawings = ttk.Button(actions_frm, text="eDrawings", style="Primary.TButton", command=self.open_external_viewer)
 
        self.btn_edrawings.pack(side="left", padx=4)
        
        self.btn_solidworks = ttk.Button(actions_frm, text="Solidworks", style="Primary.TButton", command=self.open_solidworks)
        self.btn_solidworks.pack(side="left", padx=4)
        
        # Save Version Card (Commit form)
        save_card = ttk.Frame(main_panel, style="Card.TFrame")
        save_card.pack(fill="x", pady=(6, 0))
        
        lbl_save_title = ttk.Label(save_card, text="Save Version & Upload (Check-in)", style="CardTitle.TLabel")
        lbl_save_title.pack(anchor="w", padx=8, pady=(4, 2))
        
        lbl_msg = ttk.Label(save_card, text="Version Description:", style="Card.TLabel")
        lbl_msg.pack(anchor="w", padx=8)
        
        self.txt_message = tk.Text(save_card, height=3, bg="#ffffff", fg="#1f2937", insertbackground="#000000", relief="solid", bd=1, highlightthickness=0)
        self.txt_message.pack(fill="x", padx=8, pady=2)
        
        btn_save_frm = ttk.Frame(save_card, style="TFrame")
        btn_save_frm.pack(fill="x", padx=8, pady=(2, 6))
        
        # Pack buttons first on the right side
        self.btn_save_all = ttk.Button(btn_save_frm, text="Upload Every Files Version", style="Primary.TButton", command=self.save_all_versions)
        self.btn_save_all.pack(side="right")
        
        self.btn_save_ver = ttk.Button(btn_save_frm, text="Upload Selected File Version", command=self.save_version)
        self.btn_save_ver.pack(side="right", padx=(0, 8))
        
        # Commit message selection combobox (stretched to the left end)
        self.cb_commit_msg = ttk.Combobox(btn_save_frm, state="readonly")
        self.cb_commit_msg.set("")
        self.cb_commit_msg.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.cb_commit_msg.bind("<<ComboboxSelected>>", self.on_commit_msg_selected)
        
        # Load predefined commit messages from commit.json
        self.load_commit_messages()
        
        return view

    def refresh_file_list(self):
        if getattr(self, 'is_refreshing_file_list', False):
            return
            
        if not self.git_service.is_git_repo():
            for item in self.tree.get_children():
                self.tree.delete(item)
            return

        self.is_refreshing_file_list = True
        self.increment_tasks()

        def run():
            try:
                files_data = self.git_service.get_status()
                
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
            
        if os.path.exists(commit_json_path):
            try:
                import json
                with open(commit_json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        commit_messages.extend(data)
            except Exception as e:
                print(f"Error loading commit.json: {e}")
        else:
            commit_messages.extend([
                "feat: Add new parts or assemblies",
                "fix: Correct geometry errors or modeling issues",
                "refactor: Clean up design feature tree or references",
                "docs: Update drawings, annotations, or bill of materials (BOM)",
                "chore: Clean up temporary files or modify configurations"
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
            filtered_files.sort(key=lambda f: (os.path.splitext(f['file'])[1].lower(), os.path.basename(f['file']).lower(), f['file'].lower()))
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
                    try:
                        self.git_service.unlock_file(file_rel_path)
                        success_count += 1
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
                # Run check_sw_open_state in worker thread
                for file_rel_path in files_to_save:
                    if not self.check_sw_open_state(file_rel_path):
                        return
                        
                import git
                # 1. Stage selected files
                rel_paths = []
                for fp in files_to_save:
                    rel_path = self.git_service.get_correct_filepath_casing(fp)
                    rel_paths.append(rel_path)
                
                try:
                    self.git_service.repo.index.add(rel_paths)
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
                    self.git_service.repo.index.commit(msg, author=author, committer=author)
                    self.write_log("Saved changes locally.", "success")
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
                                ours_label="Keep Ours (Local)",
                                theirs_label="Keep Theirs (Remote)"
                            )
                            if resolutions is None:
                                self.write_log("Upload cancelled by user. Rolling back local commit...", "warning")
                                self.git_service._run_lfs_cmd(["git", "reset", "--soft", "HEAD~1"])
                                return
                            
                            self.write_log("Applying resolutions and completing sync...", "info")
                            self.git_service.sync_pull_with_resolutions(resolutions)
                        else:
                            self.write_log("No conflicts detected. Performing standard pull...", "info")
                            self.git_service.sync_pull_clean()
                            
                        # 3. Push to remote
                        self.write_log("Pushing committed changes to remote server...", "info")
                        self.git_service._run_lfs_cmd(["git", "push", "origin", branch])
                    except Exception as sync_err:
                        # If fetch/pull/push failed, rollback local commit too
                        self.write_log(f"Upload sync failed: {sync_err}. Rolling back local commit...", "error")
                        self.git_service._run_lfs_cmd(["git", "reset", "--soft", "HEAD~1"])
                        raise sync_err
                        
                # 4. Try to automatically unlock if they were ours
                for file_rel_path in files_to_save:
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
                    
                self.task_queue.put(('success', f"Version saved and uploaded to server successfully for {len(files_to_save)} files!", on_done))
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
                # Check if there are open SolidWorks files in this repo using cache first
                if self.last_open_files:
                    try:
                        open_docs = self.sw_service.get_all_open_documents()
                    except Exception:
                        open_docs = []
                        
                    if open_docs:
                        repo_path_norm = os.path.abspath(self.workspace_path).replace("\\", "/").lower()
                        repo_open_docs = []
                        for doc in open_docs:
                            filepath = doc['path']
                            if filepath:
                                filepath_norm = os.path.abspath(filepath).replace("\\", "/").lower()
                                if filepath_norm.startswith(repo_path_norm):
                                    repo_open_docs.append(doc)
                                    
                        if repo_open_docs:
                            res_q = queue.Queue()
                            
                            def show_prompt():
                                file_list = "\n".join(
                                    f"  • {doc['title']}" + (" (⚠️ Unsaved changes)" if doc['dirty'] else "")
                                    for doc in repo_open_docs
                                )
                                ans = messagebox.askyesnocancel(
                                    "SolidWorks Open Files Detected",
                                    f"The following workspace files are open in SolidWorks:\n\n{file_list}\n\n"
                                    f"[Yes] Save and close files\n"
                                    f"[No] Close files without saving (discard changes)\n"
                                    f"[Cancel] Cancel upload operation"
                                )
                                res_q.put(ans)
                                
                            self.task_queue.put(('callback', None, show_prompt))
                            ans = res_q.get() # block worker thread until user responds
                            
                            if ans is None:
                                return # cancel
                                
                            sw = self.sw_service._get_sw_app()
                            if sw:
                                for doc in repo_open_docs:
                                    doc_obj = doc.get('doc_obj')
                                    if not doc_obj:
                                        continue
                                    try:
                                        if ans is True: # Yes
                                            try:
                                                doc_obj.Save3(1, 0, 0)
                                            except Exception:
                                                try:
                                                    doc_obj.Save()
                                                except Exception:
                                                    pass
                                        sw.CloseDoc(doc['title'])
                                    except Exception as ce:
                                        print(f"DEBUG: Failed to close {doc['title']}: {ce}")

                import git
                if not self.git_service.is_git_repo():
                    raise RuntimeError("Not a git repository.")
                repo = self.git_service.repo
                
                # 1. Stage all changes
                self.write_log("Staging all changes via GitPython...", "info")
                repo.git.add(all=True)
                
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
                    commit = repo.index.commit(msg, author=author, committer=author)
                    self.write_log(f"Created commit locally: {commit.hexsha[:7]}", "success")
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
                                ours_label="Keep Ours (Local)",
                                theirs_label="Keep Theirs (Remote)"
                            )
                            if resolutions is None:
                                self.write_log("Upload cancelled by user. Rolling back local commit...", "warning")
                                self.git_service._run_lfs_cmd(["git", "reset", "--soft", "HEAD~1"])
                                return
                            
                            self.write_log("Applying resolutions and completing sync...", "info")
                            self.git_service.sync_pull_with_resolutions(resolutions)
                        else:
                            self.write_log("No conflicts detected. Performing standard pull...", "info")
                            self.git_service.sync_pull_clean()
                            
                        # 4. Push to remote
                        self.write_log("Pushing committed changes to remote server...", "info")
                        self.git_service._run_lfs_cmd(["git", "push", "-u", "origin", branch])
                        self.write_log(f"Successfully pushed branch '{branch}' to remote server.", "success")
                    except Exception as sync_err:
                        self.write_log(f"Upload sync failed: {sync_err}. Rolling back local commit...", "error")
                        self.git_service._run_lfs_cmd(["git", "reset", "--soft", "HEAD~1"])
                        raise sync_err
                
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
        self.hist_tree.tag_configure("current_commit", font=bold_font)
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

    def _prompt_resolve_conflict(self, filename, branch_name):
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

    def prompt_multi_conflict_resolution(self, conflicted_files, ours_label="Keep Ours (Local)", theirs_label="Keep Theirs (Incoming)"):
        res_queue = queue.Queue()
        def ask():
            dialog = MultiConflictResolutionDialog(self, conflicted_files, ours_label, theirs_label)
            self.wait_window(dialog)
            res_queue.put(dialog.result)
        self.after(0, ask)
        return res_queue.get()

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
                    self.write_log(f"Merging local branch '{b}' into main...", "info")
                    conflicted_files = self.git_service.check_merge_conflicts(b)
                    if conflicted_files:
                        self.write_log(f"Conflicts pre-detected while merging '{b}' into main! Showing resolution dialog...", "warning")
                        resolutions = self.prompt_multi_conflict_resolution(
                            conflicted_files,
                            ours_label="Keep Main (Local)",
                            theirs_label=f"Keep Branch '{b}' (Incoming)"
                        )
                        if resolutions is None:
                            self.write_log(f"Merge of branch '{b}' cancelled by user. Aborting...", "warning")
                            raise RuntimeError(f"Merge cancelled by user on branch '{b}'.")
                            
                        self.write_log(f"Merging branch '{b}' with resolutions...", "info")
                        self.git_service.merge_branch_with_resolutions(b, resolutions)
                        self.write_log(f"Branch '{b}' merged into main successfully with resolutions.", "success")
                    else:
                        self.write_log(f"No conflicts detected. Performing standard merge for branch '{b}'...", "info")
                        self.git_service.merge_branch(b)
                        self.write_log(f"Branch '{b}' merged into main successfully.", "success")
                        
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
                res = subprocess.run(["git", "lfs", "install"], cwd=local_repo_path, capture_output=True, text=True)
                if res.returncode != 0:
                    self.write_log(f"Warning: git lfs install failed: {res.stderr}", "warning")
                else:
                    self.write_log("Git LFS initialized successfully.", "success")
                    
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
                res_push = subprocess.run(["git", "push", "-u", "origin", "main"], cwd=local_repo_path, capture_output=True, text=True)
                if res_push.returncode != 0:
                    raise RuntimeError(f"Failed to push main branch: {res_push.stderr}")
                self.write_log("Successfully pushed initial commit to main branch.", "success")
                
                # 12. Create and switch to developer branch
                self.write_log(f"Creating and switching to developer branch '{username}'...", "info")
                repo.create_head(username)
                repo.git.checkout(username)
                
                # 13. Committing and pushing developer branch files (empty commit allowed)
                self.write_log("Committing files on developer branch...", "info")
                repo.index.commit("Initial commit on this branch", author=author, committer=author)
                
                self.write_log(f"Pushing developer branch '{username}' to origin...", "info")
                res_push_dev = subprocess.run(["git", "push", "-u", "origin", username], cwd=local_repo_path, capture_output=True, text=True)
                if res_push_dev.returncode != 0:
                    raise RuntimeError(f"Failed to push developer branch: {res_push_dev.stderr}")
                self.write_log(f"Successfully pushed branch '{username}' to origin.", "success")
                
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
                    
                self.task_queue.put(('success', f"Repository '{repo_name}' created successfully!", on_done))
                
            except Exception as e:
                self.task_queue.put(('error', f"Maintainer setup failed:\n{e}", None))
            finally:
                self.decrement_tasks()
                
        threading.Thread(target=run, daemon=True).start()

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

    def restore_latest(self):
        ans = messagebox.askyesno("Confirm Return", "Do you want to discard checked-out state and return files to latest master/main branch?")
        if not ans:
            return
            
        self.btn_restore_latest.state(["disabled"])
        
        def run():
            self.increment_tasks()
            try:
                try:
                    self.git_service.restore_latest()
                    def on_done():
                        self.refresh_file_list()
                        self.refresh_history()
                        self.load_branches_in_combo()
                    self.task_queue.put(('success', "Successfully returned to latest version trunk!", on_done))
                except Exception as e:
                    self.task_queue.put(('error', f"Failed to restore latest:\n{e}", None))
            finally:
                self.decrement_tasks()
                
        threading.Thread(target=run, daemon=True).start()

    # ==========================================
    # GENERAL ACTIONS & QUEUE PROCESSING
    # ==========================================
    def sync_repository(self):
        # --- Step 1: Check for open SolidWorks files (runs in GUI thread) ---
        try:
            open_docs = self.sw_service.get_all_open_documents()
        except Exception:
            open_docs = []

        if open_docs:
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
                for doc in open_docs:
                    doc_obj = doc.get('doc_obj')
                    if not doc_obj:
                        continue
                    try:
                        if ans is True:  # Yes: save and close
                            try:
                                doc_obj.Save3(1, 0, 0)
                            except Exception:
                                try:
                                    doc_obj.Save()
                                except Exception:
                                    pass
                        # Close regardless of save choice
                        sw.CloseDoc(doc['title'])
                    except Exception as ce:
                        print(f"DEBUG: Failed to close {doc['title']}: {ce}")

        # --- Step 2: Proceed with git sync in background thread ---
        self.btn_sync.config(text="Syncing...")
        self.btn_sync.state(["disabled"])
        
        def run():
            self.increment_tasks()
            try:
                try:
                    res = self.git_service.sync_pull()
                    self.task_queue.put(('success', f"Synchronization complete:\n{res}", self.refresh_dashboard))
                except Exception as e:
                    self.task_queue.put(('error', f"Sync failed:\n{e}", None))
            finally:
                self.decrement_tasks()
                
        threading.Thread(target=run, daemon=True).start()

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
                    
                    def on_success():
                        self.workspace_path = local_dir
                        self.git_service = temp_service
                        self.save_workspace_to_config(local_dir)
                        self.load_commit_messages()
                        self.refresh_dashboard()
                        self.refresh_file_list()
                        self.refresh_history()
                        self.write_log(f"Clone completed successfully and workspace updated to: {local_dir}", "success")
                        
                    self.task_queue.put(('success', f"Clone complete successfully!\n{clean_res}", on_success))
                except Exception as e:
                    clean_err = redact_token(str(e))
                    def on_failure():
                        self.workspace_path = orig_workspace_path
                        self.git_service = orig_git_service
                        self.refresh_dashboard()
                        
                    self.task_queue.put(('error', f"Clone failed:\n{clean_err}", on_failure))
            finally:
                self.decrement_tasks()
                # Restore button text
                def restore_btn():
                    self.btn_clone.config(text="Clone")
                self.task_queue.put(('callback', None, restore_btn))
                
        threading.Thread(target=run, daemon=True).start()

    def merge_main_branch(self):
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
                    conflicted_files = self.git_service.check_merge_conflicts(source)
                    if conflicted_files:
                        self.write_log(f"Conflicts pre-detected in {len(conflicted_files)} files! Showing resolution dialog...", "warning")
                        resolutions = self.prompt_multi_conflict_resolution(
                            conflicted_files,
                            ours_label=f"Keep Current ({current})",
                            theirs_label=f"Keep Source ({source})"
                        )
                        if resolutions is None:
                            self.write_log("Merge cancelled by user.", "warning")
                            return
                        
                        self.write_log("Merging main branch with resolutions...", "info")
                        self.git_service.merge_branch_with_resolutions(source, resolutions)
                        result = "Merge completed with resolutions."
                    else:
                        self.write_log("No conflicts detected. Performing standard merge...", "info")
                        result = self.git_service.merge_branch(source)
                    
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

    def open_solidworks(self):
        files = self.get_selected_file_abs_paths()
        if not files:
            self.write_log("Please select at least one SolidWorks file first.", "warning")
            return
            
        def run():
            self.increment_tasks()
            try:
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
                            # Open Doc (Options: 1 = swOpenDocOptions_Silent)
                            doc = sw_app.OpenDoc6(abs_file_path, doc_type, 1, "", errors_ref, warnings_ref)
                            if doc:
                                try:
                                    title = self.sw_service._call_com_method(doc, 'GetTitle')
                                    # Option: 2 = swUserDecision
                                    self.sw_service._call_com_method(sw_app, 'ActivateDoc3', title, True, 2, errors_ref)
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
                        try:
                            subprocess.Popen([path, os.path.abspath(file)])
                        except Exception as e:
                            errors.append(f"Failed to open {os.path.basename(file)} in SolidWorks: {e}")
                    if errors:
                        self.task_queue.put(('error', "\n".join(errors), None))
                else:
                    errors = []
                    for file in files:
                        try:
                            os.startfile(os.path.abspath(file))
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
                elif msg_type == 'bg_task_end':
                    self.bg_tasks_count = max(0, self.bg_tasks_count - 1)
                    if self.bg_tasks_count == 0:
                        self.lbl_status_indicator.config(text="● Idle", fg="#10b981")
                
                # Only restore button states when no background tasks are running
                if self.bg_tasks_count == 0:
                    is_repo = self.git_service.is_git_repo()
                    self.btn_lock.config(text="Lock (Checkout)")
                    if is_repo:
                        self.btn_lock.state(["!disabled"])
                        self.btn_unlock.state(["!disabled"])
                        self.btn_force_unlock.state(["!disabled"])
                        self.btn_save_ver.state(["!disabled"])
                        self.btn_save_all.state(["!disabled"])
                        self.btn_sync.state(["!disabled"])
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
                        self.lbl_sw_status.config(text=content)
                    
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
                
                # 2. Fetch locks once for this loop to verify status
                locks = {}
                try:
                    if current_open_files or self.last_open_files:
                        locks = self.git_service.get_lfs_locks()
                except Exception as e:
                    print(f"Error fetching LFS locks: {e}")
                
                locks_lower = {k.lower(): v for k, v in locks.items()}
                
                # 3. Detect newly opened files -> Auto Lock (case-insensitive checks)
                for rel_path in current_open_files:
                    was_open = any(f.lower() == rel_path.lower() for f in self.last_open_files)
                    if not was_open:
                        # File just opened!
                        rel_path_lower = rel_path.lower()
                        if rel_path_lower in locks_lower:
                            is_ours = locks_lower[rel_path_lower]['is_ours']
                            if is_ours:
                                # Already locked by us, track it
                                self.files_locked_by_us.add(rel_path)
                            else:
                                # Locked by someone else, we can't lock it
                                pass
                        else:
                            # Not locked, lock it!
                            def run_lock(path_to_lock):
                                self.increment_tasks()
                                success = False
                                try:
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
                            def run_unlock(path_to_unlock, path_to_remove):
                                self.increment_tasks()
                                success = False
                                try:
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
                                    self.task_queue.put(('callback', None, self.refresh_file_list))
                                    
                            threading.Thread(target=run_unlock, args=(rel_path, matched_path), daemon=True).start()
                            
                # 5. Build status message for Dashboard
                is_active = self.sw_service._get_sw_app() is not None
                active_text = "Active" if is_active else "Inactive"
                total_files = len(self.files_data) if getattr(self, 'files_data', None) else 0
                num_open = len(open_docs) if open_docs else 0
                num_locked = sum(1 for f in self.files_data if f.get('locked')) if getattr(self, 'files_data', None) else 0
                
                status_text = (
                    f"• SolidWorks Status: {active_text}\n"
                    f"• Total Files: {total_files}\n"
                    f"• Open Files: {num_open}\n"
                    f"• Locked Files: {num_locked}"
                )
                
                # Update status label
                # If the set of open files changed (comparing normalized sets case-insensitively), trigger refresh
                curr_open_lower = {f.lower() for f in current_open_files}
                last_open_lower = {f.lower() for f in self.last_open_files}
                if curr_open_lower != last_open_lower:
                    self.task_queue.put(('sw_status', status_text, self.refresh_file_list))
                else:
                    self.task_queue.put(('sw_status', status_text, None))
                
                # 6. Save current set for next iteration
                self.last_open_files = current_open_files
                
            except Exception as e:
                print(f"Error in SolidWorks background monitor loop: {e}")
                
            # Sleep for 2.5 seconds before next polling
            threading.Event().wait(2.5)

    def destroy(self):
        self.sw_monitor_active = False
        super().destroy()