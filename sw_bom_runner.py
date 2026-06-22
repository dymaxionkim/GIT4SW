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
        return win32com.client.Dispatch(raw_obj)
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
    print("Connecting to SolidWorks...", flush=True)
    try:
        raw_obj = win32com.client.GetActiveObject("SldWorks.Application")
        swApp = get_dynamic_sw_app(raw_obj)
        print("Connected to active SolidWorks instance.", flush=True)
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
        except Exception as e:
            print(f"Failed to connect to SolidWorks: {e}", file=sys.stderr, flush=True)
            
    return swApp

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
    args = parser.parse_args()

    assembly_file_path = os.path.abspath(args.assembly_path)
    if not os.path.exists(assembly_file_path):
        print(f"Error: File does not exist at {assembly_file_path}", file=sys.stderr, flush=True)
        sys.exit(1)

    pythoncom.CoInitialize()

    swApp = connect_to_solidworks()
    if not swApp:
        print("Error: Could not connect to or start SolidWorks.", file=sys.stderr, flush=True)
        sys.exit(1)

    model = None
    all_bom_rows = []
    try:
        # Load SolidWorks early-bound typelibs
        load_sw_typelib()

        # Open document silently (swOpenDocOptions_Silent = 1)
        doc_type = 2 # swDocASSEMBLY
        options = 1 
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
        # Close document and release COM references cleanly to prevent lock hangs
        try:
            # Gather all referenced paths + main assembly path to close them all
            paths_to_close = {os.path.normpath(assembly_file_path).lower()}
            if all_bom_rows:
                for row in all_bom_rows:
                    if 'File Path' in row and row['File Path']:
                        paths_to_close.add(os.path.normpath(row['File Path']).lower())

            print(f"Closing {len(paths_to_close)} referenced documents in SolidWorks...", flush=True)

            # Close referenced files in multiple iterations to resolve dependency locks
            # Assemblies must be closed before the parts they reference can be successfully closed.
            for iteration in range(5):
                docs_left = None
                try:
                    val = getattr(swApp, 'GetDocuments')
                    docs_left = val() if callable(val) else val
                except Exception as doc_err:
                    print(f"Warning: Failed to get open documents: {doc_err}", flush=True)
                    break

                if not docs_left:
                    break

                closed_any = False
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
                        if norm_path in paths_to_close:
                            title_val = d.GetTitle
                            title = title_val() if callable(title_val) else title_val
                            
                            # Clean up reference to current doc to avoid COM lock
                            d = None
                            gc.collect()
                            try:
                                pythoncom.CoCollectFreeUnusedLibraries()
                            except:
                                pass
                            
                            print(f"Closing referenced document: {title}", flush=True)
                            swApp.CloseDoc(title)
                            closed_any = True
                    except Exception as e_close_doc:
                        print(f"Warning: Failed to close document item: {e_close_doc}", flush=True)
                
                if not closed_any:
                    break
        except Exception as e_close_all:
            print(f"Warning: Error during referenced docs cleanup: {e_close_all}", file=sys.stderr, flush=True)

        model = None
        root_comp = None
        config = None
        gc.collect()
        pythoncom.CoUninitialize()

if __name__ == "__main__":
    main()
