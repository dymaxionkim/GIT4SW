import os
import sys
import gc
import time
import argparse
import pandas as pd

# Configure stdout and stderr to use UTF-8 and replace encoding errors to avoid crashes with localized chars
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
    print("Error: pywin32 or pythoncom is not installed.", file=sys.stderr)
    sys.exit(1)

# Import helpers from sw_export_runner
try:
    from sw_export_runner import load_sw_typelib, get_dynamic_sw_app, get_component_model, get_custom_property_value
except ImportError:
    # Inline fallback definitions if import fails
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
    def get_custom_property_value(prop_mgr, name):
        try:
            res = prop_mgr.Get5(name, False)
            if isinstance(res, tuple) and len(res) >= 3:
                return res[2]
        except:
            pass
        return ""

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
                    time.sleep(1.0)
                    
    if swApp is None:
        try:
            raw_obj = win32com.client.GetObject(Class="SldWorks.Application")
            swApp = get_dynamic_sw_app(raw_obj)
            print("Connected to SolidWorks via GetObject.", flush=True)
            was_already_running = True
        except Exception as e:
            print(f"Failed to connect to SolidWorks: {e}", file=sys.stderr, flush=True)
            
    return swApp, was_already_running

def save_dataframe_to_excel_with_autowidth(df, filepath):
    print(f"Saving to Excel: {filepath}", flush=True)
    with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Sheet1')
        
        # Auto-adjust columns width
        worksheet = writer.sheets['Sheet1']
        for col in worksheet.columns:
            max_len = 0
            col_letter = col[0].column_letter
            for cell in col:
                val = str(cell.value or '')
                if len(val) > max_len:
                    max_len = len(val)
            # Set column width with padding
            worksheet.column_dimensions[col_letter].width = max(max_len + 3, 10)
        
def lookup_property(props, key_name, aliases=None):
    if not props:
        return ""
    # Try direct match
    if key_name in props:
        return props[key_name]
    # Try case-insensitive match
    key_lower = key_name.lower()
    for k, v in props.items():
        if k.lower() == key_lower:
            return v
    # Try aliases
    if aliases:
        for alias in aliases:
            alias_lower = alias.lower()
            for k, v in props.items():
                if k.lower() == alias_lower:
                    return v
    return ""

def main():
    parser = argparse.ArgumentParser(description="SolidWorks Assembly BOM Extractor")
    parser.add_argument("assembly_path", help="Absolute path to the .sldasm file")
    parser.add_argument("--config", help="Specific configuration name to activate", default=None)
    parser.add_argument("--was-running", choices=["true", "false"], default=None, help="Whether SolidWorks was already running before the task started")
    parser.add_argument("--open-before", help="Comma-separated absolute paths of documents open before the BOM button was clicked", default=None)
    args = parser.parse_args()

    assembly_file_path = os.path.abspath(args.assembly_path)
    if not os.path.exists(assembly_file_path):
        print(f"Error: File does not exist at {assembly_file_path}", file=sys.stderr, flush=True)
        sys.exit(1)

    pythoncom.CoInitialize()

    swApp, detected_running = connect_to_solidworks()
    if not swApp:
        print("Error: Could not connect to or start SolidWorks.", file=sys.stderr, flush=True)
        sys.exit(1)

    if args.was_running is not None:
        was_already_running = (args.was_running == "true")
    else:
        was_already_running = detected_running

    model = None
    all_bom_rows = []
    already_open_paths = set()
    try:
        if args.open_before:
            # Populate already_open_paths from the passed arguments
            for path_str in args.open_before.split(','):
                path_str_clean = path_str.strip()
                if path_str_clean:
                    already_open_paths.add(os.path.normpath(path_str_clean).lower())
        else:
            # Get list of already open documents before opening our target assembly
            try:
                val_docs = getattr(swApp, 'GetDocuments', None)
                open_docs_before = val_docs() if callable(val_docs) else val_docs
                if open_docs_before:
                    for d in open_docs_before:
                        try:
                            p_val = getattr(d, 'GetPathName', None)
                            p = p_val() if callable(p_val) else p_val
                            if p:
                                already_open_paths.add(os.path.normpath(p).lower())
                            else:
                                t_val = getattr(d, 'GetTitle', None)
                                t = t_val() if callable(t_val) else t_val
                                if t:
                                    already_open_paths.add(t.lower())
                        except:
                            pass
            except Exception as doc_err:
                print(f"Warning: Failed to get initial open documents: {doc_err}", flush=True)

        # Load SolidWorks early-bound typelibs
        load_sw_typelib()

        # Open document silently and read-only (swOpenDocOptions_Silent = 1, swOpenDocOptions_ReadOnly = 2)
        doc_type = 2 # swDocASSEMBLY
        options = 1 | 2 
        error = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        warning = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)

        print(f"Opening document: {assembly_file_path}", flush=True)
        model = swApp.OpenDoc6(assembly_file_path, doc_type, options, "", error, warning)
        if not model:
            print("Error: Failed to open assembly document in SolidWorks.", file=sys.stderr, flush=True)
            sys.exit(1)

        # Resolve lightweight components
        try:
            print("Resolving lightweight components...", flush=True)
            model.ResolveAllLightWeightComponents(True)
        except Exception as e:
            print(f"Warning resolving lightweight components: {e}", flush=True)

        # If a specific configuration is requested, activate it before extracting the BOM
        if args.config:
            print(f"Activating configuration: {args.config}", flush=True)
            try:
                show_cfg_val = model.ShowConfiguration2
                if callable(show_cfg_val):
                    show_cfg_val(args.config)
                else:
                    model.ShowConfiguration2 = args.config
            except Exception as e_cfg:
                print(f"Warning: Failed to activate configuration '{args.config}': {e_cfg}", flush=True)

        # Get active configuration in a highly robust manner
        config = None
        try:
            cfg_mgr = getattr(model, 'ConfigurationManager', None)
            if cfg_mgr:
                config = getattr(cfg_mgr, 'ActiveConfiguration', None)
        except:
            pass

        if config is None:
            try:
                act_cfg_val = getattr(model, 'GetActiveConfiguration', None)
                if act_cfg_val:
                    config = act_cfg_val() if callable(act_cfg_val) else act_cfg_val
            except:
                pass

        root_comp = None
        if config:
            # Try GetRootComponent3
            try:
                root_val = config.GetRootComponent3
                root_comp = root_val(True) if callable(root_val) else root_val
            except Exception as e:
                print(f"GetRootComponent3 failed: {e}. Trying GetRootComponent2...", flush=True)

            if root_comp is None:
                try:
                    root_val = config.GetRootComponent2
                    root_comp = root_val() if callable(root_val) else root_val
                except Exception as e:
                    print(f"GetRootComponent2 failed: {e}. Trying GetRootComponent...", flush=True)

            if root_comp is None:
                try:
                    root_val = config.GetRootComponent
                    root_comp = root_val() if callable(root_val) else root_val
                except:
                    pass

        if not root_comp:
            print("Error: Could not get the root component of the assembly.", file=sys.stderr, flush=True)
            sys.exit(1)

        all_bom_rows = []
        all_custom_prop_names = set()

        def traverse(comp, depth, parent_absolute_qty):
            try:
                children_val = comp.GetChildren
                children = children_val() if callable(children_val) else children_val
            except Exception as e:
                print(f"Warning: Failed to get children for component: {e}", flush=True)
                return

            if not children:
                return

            # Group children by path & configuration to calculate local quantity at this level
            groups = {}
            for child in children:
                try:
                    # Skip suppressed
                    suppressed_val = child.IsSuppressed
                    is_suppressed = suppressed_val() if callable(suppressed_val) else suppressed_val
                    if is_suppressed:
                        continue
                    
                    # Exclude from BOM check
                    try:
                        if child.ExcludeFromBOM:
                            continue
                    except:
                        pass

                    path_val = child.GetPathName
                    path = path_val() if callable(path_val) else path_val
                    
                    ref_config_val = child.ReferencedConfiguration
                    config_name = ref_config_val() if callable(ref_config_val) else ref_config_val
                    if not path or not config_name:
                        continue

                    key = (path.lower(), config_name.lower())
                    if key not in groups:
                        groups[key] = []
                    groups[key].append(child)
                except Exception as e:
                    print(f"Warning: Failed to process child component: {e}", flush=True)

            # Process groups and recurse
            for key, child_list in groups.items():
                local_qty = len(child_list)
                absolute_qty = parent_absolute_qty * local_qty
                rep_child = child_list[0]

                path_val = rep_child.GetPathName
                path = path_val() if callable(path_val) else path_val
                
                ref_config_val = rep_child.ReferencedConfiguration
                config_name = ref_config_val() if callable(ref_config_val) else ref_config_val

                # Extract properties
                props = {}
                child_model = None
                try:
                    child_model = get_component_model(rep_child)
                except Exception as e:
                    print(f"Warning: Failed to get component model for {path}: {e}", flush=True)

                if child_model:
                    try:
                        # 1. Config-specific custom properties
                        cfg_prop_mgr = child_model.Extension.CustomPropertyManager(config_name)
                        if cfg_prop_mgr:
                            names_val = cfg_prop_mgr.GetNames
                            cfg_names = names_val() if callable(names_val) else names_val
                            if cfg_names:
                                for name in cfg_names:
                                    val = get_custom_property_value(cfg_prop_mgr, name)
                                    props[name] = val
                                    all_custom_prop_names.add(name)
                    except:
                        pass

                    try:
                        # 2. General custom properties fallback
                        gen_prop_mgr = child_model.Extension.CustomPropertyManager("")
                        if gen_prop_mgr:
                            names_val = gen_prop_mgr.GetNames
                            gen_names = names_val() if callable(names_val) else names_val
                            if gen_names:
                                for name in gen_names:
                                    if name not in props or not props[name]:
                                        val = get_custom_property_value(gen_prop_mgr, name)
                                        props[name] = val
                                        all_custom_prop_names.add(name)
                    except:
                        pass

                    child_model = None

                # Append to raw BOM rows
                all_bom_rows.append({
                    'Depth': depth,
                    'Quantity': local_qty,
                    'AbsoluteQuantity': absolute_qty,
                    'File Path': path,
                    'Configuration': config_name,
                    'Properties': props
                })

                # Recursively traverse the sub-assembly if it is an assembly file (.sldasm)
                if path and path.lower().endswith('.sldasm'):
                    traverse(rep_child, depth + 1, absolute_qty)

        # Start traversal from Depth 1 (children of root)
        print("Traversing assembly components...", flush=True)
        traverse(root_comp, 1, 1)
        print(f"Traversal complete. Found {len(all_bom_rows)} nodes.", flush=True)

        # Define the exact columns order requested by the user
        column_order = [
            "Depth",
            "Type",
            "PartNumber",
            "Partname",
            "Qty",
            "Material",
            "Treatment",
            "Weight",
            "Description",
            "File Name",
            "Configuration",
            "File Path"
        ]

        def get_mapped_row(depth, qty, file_path, configuration, props):
            file_name = os.path.basename(file_path) if file_path else ""
            
            # Fallback for Partname: use base file name if null/empty
            part_name = lookup_property(props, "Partname", ["Part_Name", "Part Name", "Title"])
            if not part_name or str(part_name).strip() == "":
                part_name = os.path.splitext(file_name)[0]
                
            # Prepend {Depth - 1} spaces to Partname for visual hierarchy representation
            spaces_count = max(0, depth - 1)
            part_name = (" " * spaces_count) + str(part_name)
                
            # Type classification (ASM for sldasm, PRT for sldprt)
            ext = os.path.splitext(file_path)[1].lower() if file_path else ""
            if ext == '.sldasm':
                file_type = "ASM"
            elif ext == '.sldprt':
                file_type = "PRT"
            else:
                file_type = ""
                
            part_number = lookup_property(props, "PartNumber", ["PartNo", "Part_No", "Part_Number", "Part Number", "ItemNo", "Item Number"])
            material = lookup_property(props, "Material")
            treatment = lookup_property(props, "Treatment", ["SurfaceTreatment", "Surface Treatment", "Finish"])
            weight = lookup_property(props, "Weight", ["Weight(g)", "Mass", "Weight(kg)"])
            description = lookup_property(props, "Description", ["Desc"])

            return {
                "Depth": depth,
                "Type": file_type,
                "PartNumber": part_number,
                "Partname": part_name,
                "Qty": qty,
                "Material": material,
                "Treatment": treatment,
                "Weight": weight,
                "Description": description,
                "File Name": file_name,
                "Configuration": configuration,
                "File Path": file_path
            }

        # 1. Compile Hierarchical BOM Tree DataFrame
        bom_data = []
        for row in all_bom_rows:
            mapped = get_mapped_row(
                depth=row['Depth'],
                qty=row['Quantity'],
                file_path=row['File Path'],
                configuration=row['Configuration'],
                props=row['Properties']
            )
            bom_data.append(mapped)

        df_bom = pd.DataFrame(bom_data, columns=column_order)
        if df_bom.empty:
            df_bom = pd.DataFrame(columns=column_order)

        # 2. Compile Aggregated Partlist DataFrame (sum of absolute quantities)
        pl_groups = {}
        for row in all_bom_rows:
            key = (row['File Path'].lower(), row['Configuration'].lower())
            if key not in pl_groups:
                pl_groups[key] = {
                    'Depth': 1,
                    'Quantity': 0,
                    'File Path': row['File Path'],
                    'Configuration': row['Configuration'],
                    'Properties': row['Properties'].copy()
                }
            pl_groups[key]['Quantity'] += row['AbsoluteQuantity']

        pl_data = []
        for key, item in pl_groups.items():
            mapped = get_mapped_row(
                depth=item['Depth'],
                qty=item['Quantity'],
                file_path=item['File Path'],
                configuration=item['Configuration'],
                props=item['Properties']
            )
            pl_data.append(mapped)

        df_pl = pd.DataFrame(pl_data, columns=column_order)
        if df_pl.empty:
            df_pl = pd.DataFrame(columns=column_order)

        # 3. Create target directory: 2D/BOM relative to assembly path
        target_dir = os.path.dirname(assembly_file_path)
        bom_dir = os.path.join(target_dir, "2D", "BOM")
        os.makedirs(bom_dir, exist_ok=True)

        base_name = os.path.splitext(os.path.basename(assembly_file_path))[0]
        if args.config:
            bom_file_path = os.path.join(bom_dir, f"{base_name}__{args.config}__BOM.xlsx")
            pl_file_path = os.path.join(bom_dir, f"{base_name}__{args.config}__PL.xlsx")
        else:
            bom_file_path = os.path.join(bom_dir, f"{base_name}__BOM.xlsx")
            pl_file_path = os.path.join(bom_dir, f"{base_name}__PL.xlsx")

        # Save files
        save_dataframe_to_excel_with_autowidth(df_bom, bom_file_path)
        save_dataframe_to_excel_with_autowidth(df_pl, pl_file_path)
        print("BOM Tree and Partlist successfully saved to Excel.", flush=True)

    except Exception as e:
        print(f"Error during BOM generation: {e}", file=sys.stderr, flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)

    finally:
        # Release COM references first to allow SolidWorks to release document locks
        model = None
        root_comp = None
        config = None
        gc.collect()
        try:
            pythoncom.CoCollectFreeUnusedLibraries()
        except:
            pass

        # Close documents cleanly
        try:
            print(f"Closing documents opened during BOM extraction in SolidWorks...", flush=True)

            # Close referenced/newly opened files in multiple iterations to resolve dependency locks
            # Assemblies/Drawings must be closed before the parts they reference can be successfully closed.
            last_doc_count = -1
            stuck_count = 0
            for iteration in range(15):
                docs_left = None
                try:
                    val = getattr(swApp, 'GetDocuments')
                    docs_left = val() if callable(val) else val
                except Exception as doc_err:
                    print(f"Warning: Failed to get open documents: {doc_err}", flush=True)
                    break

                if not docs_left:
                    break

                current_count = len(docs_left)
                if current_count == last_doc_count:
                    stuck_count += 1
                    if stuck_count > 3:
                        print(f"BOM cleanup: detected stuck document count ({current_count} docs), breaking to prevent infinite loop.", flush=True)
                        break
                else:
                    stuck_count = 0
                last_doc_count = current_count

                parent_files = []  # list of dict
                child_files = []   # list of dict
                for d in docs_left:
                    try:
                        # Get path name
                        path_val = d.GetPathName
                        path = path_val() if callable(path_val) else path_val
                        if not path:
                            # fallback to title if empty (e.g. unsaved/virtual document)
                            title_val = d.GetTitle
                            title = title_val() if callable(title_val) else title_val
                            path = title

                        norm_path = os.path.normpath(path).lower()
                        title_val = d.GetTitle
                        title = title_val() if callable(title_val) else title_val
                        
                        # Check visibility
                        is_visible = True
                        try:
                            is_visible = d.Visible
                        except:
                            try:
                                is_visible = getattr(d, 'Visible')
                            except:
                                pass
                                
                        # Close only documents that were NOT already open before starting the script,
                        # but always close invisible referenced/background documents to release locks.
                        is_newly_opened = (norm_path not in already_open_paths) and (title.lower() not in already_open_paths)
                        if is_newly_opened or not is_visible:
                            try:
                                dtype = d.GetType()
                            except:
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
                            
                            # Generate unique closing identifiers
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
                                        
                            doc_entry = {
                                'title': title,
                                'path': path,
                                'uniq_ids': uniq_ids
                            }
                            if is_parent:
                                parent_files.append(doc_entry)
                            else:
                                child_files.append(doc_entry)
                    except Exception as e_inspect:
                        print(f"Warning: Failed to inspect document for cleanup: {e_inspect}", flush=True)

                # Clear COM references to docs_left before closing to avoid lock
                docs_left = None
                d = None
                gc.collect()
                try:
                    pythoncom.CoCollectFreeUnusedLibraries()
                except:
                    pass

                if not parent_files and not child_files:
                    break

                closed_any = False
                # Close parents first to release references on children
                for doc_entry in parent_files:
                    title = doc_entry['title']
                    path = doc_entry['path']
                    uniq_ids = doc_entry['uniq_ids']
                    print(f"Closing newly opened/referenced parent document: {title} (path: {path})", flush=True)
                    for identifier in uniq_ids:
                        try:
                            print(f"  Attempting CloseDoc('{identifier}')", flush=True)
                            res = swApp.CloseDoc(identifier)
                            print(f"    CloseDoc Result: {res}", flush=True)
                            if res:
                                closed_any = True
                        except Exception as e_close_parent:
                            print(f"    Warning: Failed to CloseDoc parent '{identifier}': {e_close_parent}", flush=True)
                        try:
                            print(f"  Attempting QuitDoc('{identifier}')", flush=True)
                            res = swApp.QuitDoc(identifier)
                            print(f"    QuitDoc Result: {res}", flush=True)
                            closed_any = True
                        except Exception as e_quit_parent:
                            print(f"    Warning: Failed to QuitDoc parent '{identifier}': {e_quit_parent}", flush=True)

                if closed_any:
                    time.sleep(0.2)

                # Close children second
                for doc_entry in child_files:
                    title = doc_entry['title']
                    path = doc_entry['path']
                    uniq_ids = doc_entry['uniq_ids']
                    print(f"Closing newly opened/referenced child document: {title} (path: {path})", flush=True)
                    for identifier in uniq_ids:
                        try:
                            print(f"  Attempting CloseDoc('{identifier}')", flush=True)
                            res = swApp.CloseDoc(identifier)
                            print(f"    CloseDoc Result: {res}", flush=True)
                            if res:
                                closed_any = True
                        except Exception as e_close_child:
                            print(f"    Warning: Failed to CloseDoc child '{identifier}': {e_close_child}", flush=True)
                        try:
                            print(f"  Attempting QuitDoc('{identifier}')", flush=True)
                            res = swApp.QuitDoc(identifier)
                            print(f"    QuitDoc Result: {res}", flush=True)
                            closed_any = True
                        except Exception as e_quit_child:
                            print(f"    Warning: Failed to QuitDoc child '{identifier}': {e_quit_child}", flush=True)

                time.sleep(0.2)
        except Exception as e_close_all:
            print(f"Warning: Error during referenced docs cleanup: {e_close_all}", file=sys.stderr, flush=True)

        # Close SolidWorks if we launched it and it wasn't already running before the script started
        if swApp:
            if not was_already_running:
                import threading
                def exit_sw_async(app):
                    try:
                        print("Exiting SolidWorks (launched by BOM runner)...", flush=True)
                        app.ExitApp()
                    except Exception as exit_e:
                        print(f"Warning: Error during swApp.ExitApp(): {exit_e}", flush=True)
                        
                try:
                    t_exit = threading.Thread(target=exit_sw_async, args=(swApp,), daemon=True)
                    t_exit.start()
                    t_exit.join(timeout=5.0)
                except Exception as t_err:
                    print(f"Failed to start async ExitApp thread: {t_err}", flush=True)
            else:
                print("SolidWorks was already running before BOM extraction. Keeping SolidWorks open.", flush=True)

        gc.collect()
        
        # Force terminate SolidWorks processes if we started it and it is still alive
        if not was_already_running:
            try:
                import subprocess
                subprocess.run("taskkill /F /IM SLDWORKS.exe", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run("taskkill /F /IM sldworks_fs.exe", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print("Forcefully terminated remaining SolidWorks processes spawned by BOM runner.", flush=True)
            except Exception as kill_e:
                print(f"Could not clean up SolidWorks processes: {kill_e}", flush=True)

        pythoncom.CoUninitialize()

if __name__ == "__main__":
    main()
