"""SolidWorks COM Snapshot Extractor for GIT4SW LLM Commit Messages.

Designed with Zero-Interference safeguards so background diff indexing never
degrades interactive SolidWorks modeling performance:
  1. Win32 foreground window detection (aborts/pauses background jobs if the user
     is actively focused on SLDWORKS.exe).
  2. Invisible ReadOnly document open (swApp.DocumentVisible(False, doc_type))
     with external reference loading disabled (swLoadExternalReferences = 0)
     and without LoadModel for drawings.
  3. Lightweight assembly inspection (never calls ResolveAllLightWeightComponents).
  4. Per-file time-budget circuit breaker (default 2.0s) so ultra-complex parts
     cannot stall COM.
"""

import argparse
import ctypes
from ctypes import wintypes
import gc
import json
import math
import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    import win32com.client
    import pythoncom
    WIN32_AVAILABLE = True
except ImportError:
    WIN32_AVAILABLE = False

try:
    from sw_export_runner import (
        SOLIDWORKS_PATH_ENV,
        get_configured_solidworks_exe,
        get_custom_property_value,
        get_builtin_material,
        get_dynamic_sw_app,
        load_sw_typelib,
        _model_body_count,
    )
except ImportError:
    SOLIDWORKS_PATH_ENV = "GIT4SW_SOLIDWORKS_PATH"

    def get_configured_solidworks_exe(explicit_path=None):
        return explicit_path or os.environ.get(SOLIDWORKS_PATH_ENV) or r"C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\SLDWORKS.exe"

    def load_sw_typelib():
        return None

    def get_dynamic_sw_app(raw_obj):
        import win32com.client.dynamic
        return win32com.client.dynamic.Dispatch(raw_obj)

    def get_custom_property_value(prop_mgr, name):
        return ""

    def get_builtin_material(model, config_name):
        return ""

    def _model_body_count(model):
        return None


IGNORED_FEATURE_TYPES = {
    "OriginProfileFeature",
    "RefPlane",
    "MaterialFolder",
    "HistoryFolder",
    "SensorFolder",
    "SolidBodyFolder",
    "SurfaceBodyFolder",
    "CommentsFolder",
    "FavoritesFolder",
    "DetailCabinet",
    "DocsFolder",
    "EqnFolder",
    "LightFolder",
    "AmbientLight",
    "DirectionLight",
    "PointLight",
    "SpotLight",
    "EnvFolder",
    "SelectionSetFolder",
    "LiveSectionFolder",
    "FtrFolder",
    "EndTag",
    "DesignTableFolder",
    "TableFolder",
    "InkMarkupFolder",
    "SmartComponentFeature",
}

SW_DOC_PART = 1
SW_DOC_ASSEMBLY = 2
SW_DOC_DRAWING = 3
SNAPSHOT_SCHEMA_VERSION = 3


def is_solidworks_foreground():
    """Return True if the currently focused Windows foreground window belongs to SLDWORKS.exe."""
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return False

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not h_proc:
            return False
        try:
            buf_len = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(1024)
            ok = kernel32.QueryFullProcessImageNameW(h_proc, 0, buf, ctypes.byref(buf_len))
            if ok and buf.value:
                exe_name = os.path.basename(buf.value).lower()
                return exe_name == "sldworks.exe"
        finally:
            kernel32.CloseHandle(h_proc)
    except Exception:
        return False
    return False


def _com_get(obj, name, *args):
    """Safely read a COM property or call a COM method regardless of early/late binding."""
    if obj is None:
        return None
    try:
        member = getattr(obj, name)
    except Exception:
        return None
    if callable(member):
        try:
            return member(*args)
        except TypeError as e:
            if not args and "not callable" in str(e):
                return member
            return None
        except Exception:
            return None
    return member


def _doc_type_from_path(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".sldprt":
        return SW_DOC_PART
    if ext == ".sldasm":
        return SW_DOC_ASSEMBLY
    if ext == ".slddrw":
        return SW_DOC_DRAWING
    return 0


def _round_num(val, ndigits=3):
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, ndigits)
    except Exception:
        return None


def _extract_custom_properties(model, active_config=""):
    """Read general and active-configuration custom properties."""
    props = {}
    ext = _com_get(model, "Extension")
    if not ext:
        return props

    def _read_scope(cfg_name):
        try:
            mgr = ext.CustomPropertyManager(cfg_name)
        except Exception:
            return
        if not mgr:
            return
        names = _com_get(mgr, "GetNames")
        if not names:
            return
        for name in names:
            try:
                key = str(name).strip()
                if not key:
                    continue
                val = get_custom_property_value(mgr, key)
                val_str = str(val).strip() if val is not None else ""
                if key not in props or val_str:
                    props[key] = val_str
            except Exception:
                pass

    _read_scope("")
    if active_config:
        _read_scope(active_config)
    return props


def _extract_dimension_entry(disp_dim):
    """Convert an IDisplayDimension into a normalized dict (mm / deg + tolerance)."""
    if disp_dim is None:
        return None
    dim = _com_get(disp_dim, "GetDimension2", 0)
    if dim is None:
        dim = _com_get(disp_dim, "GetDimension")
    if dim is None:
        return None

    name = str(_com_get(dim, "Name") or _com_get(dim, "FullName") or "").strip()
    sys_val = _com_get(dim, "SystemValue")
    if sys_val is None:
        return None

    dim_type = _com_get(dim, "GetType")
    # swDimensionType_e: swAngularDimension = 1
    if dim_type == 1:
        unit = "deg"
        value = _round_num(math.degrees(float(sys_val)), 2)
    else:
        unit = "mm"
        value = _round_num(float(sys_val) * 1000.0, 3)

    if value is None:
        return None

    entry = {"name": name, "value": value, "unit": unit}

    # Optional tolerance extraction
    try:
        tol_type = _com_get(dim, "GetToleranceType")
        if tol_type and int(tol_type) > 0:
            tol_vals = _com_get(dim, "GetToleranceValues")
            if isinstance(tol_vals, (tuple, list)) and len(tol_vals) >= 2:
                scale = 180.0 / math.pi if unit == "deg" else 1000.0
                tmin = _round_num(float(tol_vals[0]) * scale, 3)
                tmax = _round_num(float(tol_vals[1]) * scale, 3)
                entry["tol"] = [int(tol_type), tmin, tmax]
            else:
                entry["tol"] = [int(tol_type)]
    except Exception:
        pass

    return entry


def _extract_feature_dimensions(feat, deadline):
    """Extract driving dimensions attached to a feature (and its first-level subfeatures)."""
    dims = {}
    if feat is None or time.monotonic() > deadline:
        return dims

    def _collect_from(target_feat, prefix=""):
        try:
            disp_dim = _com_get(target_feat, "GetFirstDisplayDimension")
        except Exception:
            disp_dim = None
        guard = 0
        while disp_dim is not None and guard < 120:
            guard += 1
            if time.monotonic() > deadline:
                break
            entry = _extract_dimension_entry(disp_dim)
            if entry:
                dkey = entry["name"] or f"dim_{guard}"
                if prefix and prefix not in dkey:
                    dkey = f"{dkey}@{prefix}"
                dims[dkey] = {k: v for k, v in entry.items() if k != "name"}
            try:
                disp_dim = target_feat.GetNextDisplayDimension(disp_dim)
            except Exception:
                break

    _collect_from(feat)

    # Also inspect sketch sub-features for key driving dimensions
    try:
        sub = _com_get(feat, "GetFirstSubFeature")
    except Exception:
        sub = None
    sub_guard = 0
    while sub is not None and sub_guard < 15:
        sub_guard += 1
        if time.monotonic() > deadline:
            break
        sub_name = str(_com_get(sub, "Name") or "").strip()
        sub_type = str(_com_get(sub, "GetTypeName2") or "").strip()
        if sub_type in ("ProfileFeature", "3DProfileFeature", "HoleWzd"):
            _collect_from(sub, prefix=sub_name)
        sub = _com_get(sub, "GetNextSubFeature")

    return dims


def _is_feature_suppressed(feat):
    try:
        res = feat.IsSuppressed2(1, None)  # swThisConfiguration = 1
        if isinstance(res, (tuple, list)):
            return bool(res[0]) if res else False
        return bool(res)
    except Exception:
        try:
            return bool(_com_get(feat, "IsSuppressed"))
        except Exception:
            return False


def _extract_part_snapshot(model, active_config, deadline):
    snap = {}
    # 1. Material
    try:
        mat = get_builtin_material(model, active_config)
        if mat:
            snap["material"] = mat
    except Exception:
        pass

    # 2. Mass properties
    try:
        ext = _com_get(model, "Extension")
        mp = _com_get(ext, "CreateMassProperty") if ext else None
        if mp:
            mass_kg = _com_get(mp, "Mass")
            vol_m3 = _com_get(mp, "Volume")
            area_m2 = _com_get(mp, "SurfaceArea")
            com = _com_get(mp, "CenterOfMass")
            mass_info = {}
            if mass_kg is not None:
                mg = _round_num(float(mass_kg) * 1000.0, 2)
                if mg is not None and mg >= 0:
                    mass_info["mass_g"] = mg
            if vol_m3 is not None:
                vm = _round_num(float(vol_m3) * 1e9, 2)
                if vm is not None and vm >= 0:
                    mass_info["volume_mm3"] = vm
            if area_m2 is not None:
                am = _round_num(float(area_m2) * 1e6, 2)
                if am is not None and am >= 0:
                    mass_info["surface_area_mm2"] = am
            if isinstance(com, (tuple, list)) and len(com) >= 3:
                coords = [_round_num(float(c) * 1000.0, 2) for c in com[:3]]
                if all(c is not None for c in coords):
                    mass_info["com_mm"] = coords
            if mass_info:
                snap["mass_props"] = mass_info
    except Exception:
        pass

    # 3. Bounding box & body count
    try:
        box = _com_get(model, "GetPartBox", True)
        if isinstance(box, (tuple, list)) and len(box) >= 6:
            dx = _round_num((float(box[3]) - float(box[0])) * 1000.0, 2)
            dy = _round_num((float(box[4]) - float(box[1])) * 1000.0, 2)
            dz = _round_num((float(box[5]) - float(box[2])) * 1000.0, 2)
            if dx is not None and dy is not None and dz is not None:
                snap["bbox_mm"] = [dx, dy, dz]
    except Exception:
        pass

    try:
        bc = _model_body_count(model)
        if bc is not None:
            snap["body_count"] = int(bc)
    except Exception:
        pass

    # 4. FeatureManager tree & driving dimensions (budget-limited)
    features = []
    truncated = False
    feat = _com_get(model, "FirstFeature")
    guard = 0
    while feat is not None and guard < 600:
        guard += 1
        if time.monotonic() > deadline:
            truncated = True
            break
        ftype = str(_com_get(feat, "GetTypeName2") or "").strip()
        fname = str(_com_get(feat, "Name") or "").strip()
        if ftype and ftype not in IGNORED_FEATURE_TYPES and fname:
            suppressed = _is_feature_suppressed(feat)
            dims = _extract_feature_dimensions(feat, deadline)
            f_entry = {
                "name": fname,
                "type": ftype,
                "suppressed": suppressed,
            }
            if dims:
                f_entry["dimensions"] = dims
            features.append(f_entry)
        feat = _com_get(feat, "GetNextFeature")

    snap["features"] = features
    if truncated:
        snap["truncated_by_budget"] = True
    return snap


def _extract_assembly_dependencies(swApp, file_abs):
    """Fast metadata-only dependency extraction without opening child documents."""
    deps = set()
    file_norm = os.path.normpath(file_abs).replace("\\", "/").lower()
    for method_name, args in (
        ("GetDocumentDependencies2", (file_abs, False, True, False)),
        ("GetDocumentDependencies2", (file_abs, False, True, True)),
        ("GetDocumentDependencies", (file_abs, False, True, True)),
    ):
        try:
            method = getattr(swApp, method_name, None)
            if method is None:
                continue
            raw = method(*args)
            if not raw:
                continue
            for item in raw:
                p = str(item or "").strip()
                if not p:
                    continue
                low = p.lower()
                if low.endswith((".sldprt", ".sldasm")):
                    norm = os.path.normpath(p).replace("\\", "/").lower()
                    if norm != file_norm:
                        deps.add(os.path.basename(p))
            if deps:
                break
        except Exception:
            pass
    return sorted(deps)


def _read_assembly_components_current(model):
    """Read top-level component instances for the currently active assembly configuration.

    Uses IAssemblyDoc.GetComponents(True) as primary path because it works reliably
    under DocumentVisible(False) and swLoadExternalReferences=0 even when
    ConfigurationManager.ActiveConfiguration.GetRootComponent3 returns None.
    """
    raw_comps = None
    try:
        raw_comps = _com_get(model, "GetComponents", True)
    except Exception:
        raw_comps = None

    if not raw_comps:
        try:
            cfg_mgr = _com_get(model, "ConfigurationManager")
            cfg = _com_get(cfg_mgr, "ActiveConfiguration") if cfg_mgr else None
            if cfg is None:
                cfg = _com_get(model, "GetActiveConfiguration")
            root = None
            if cfg:
                root = (
                    _com_get(cfg, "GetRootComponent3", True)
                    or _com_get(cfg, "GetRootComponent2")
                    or _com_get(cfg, "GetRootComponent")
                )
            if root:
                raw_comps = _com_get(root, "GetChildren")
        except Exception:
            raw_comps = None

    instances = []
    comp_groups = {}
    if not raw_comps:
        return instances, []

    for ch in raw_comps:
        try:
            inst_name = str(_com_get(ch, "Name2") or _com_get(ch, "Name") or "").strip()
            c_path = str(_com_get(ch, "GetPathName") or "").strip()
            c_file = os.path.basename(c_path) if c_path else inst_name
            if not c_file and not inst_name:
                continue
            c_cfg = str(_com_get(ch, "ReferencedConfiguration") or "").strip()
            is_supp = bool(_com_get(ch, "IsSuppressed"))
            is_fixed = bool(_com_get(ch, "IsFixed"))
            excl_bom = bool(_com_get(ch, "ExcludeFromBOM"))

            instances.append({
                "instance": inst_name or c_file,
                "file": c_file,
                "config": c_cfg,
                "suppressed": is_supp,
                "fixed": is_fixed,
                "exclude_bom": excl_bom,
            })

            key = f"{c_file}::{c_cfg}" if c_cfg else c_file
            grp = comp_groups.setdefault(key, {
                "file": c_file,
                "config": c_cfg,
                "qty": 0,
                "suppressed_qty": 0,
                "fixed_qty": 0,
                "exclude_bom_qty": 0,
            })
            grp["qty"] += 1
            if is_supp:
                grp["suppressed_qty"] += 1
            if is_fixed:
                grp["fixed_qty"] += 1
            if excl_bom:
                grp["exclude_bom_qty"] += 1
        except Exception:
            pass

    instances.sort(key=lambda x: x["instance"].lower())
    grouped = sorted(comp_groups.values(), key=lambda x: (x["file"].lower(), x["config"].lower()))
    return instances, grouped


def _extract_assembly_snapshot(swApp, model, file_abs, configs, active_config, deadline):
    """Lightweight assembly extraction — NEVER calls ResolveAllLightWeightComponents.

    Captures component referenced configurations and suppression states across all
    assembly configurations (within time budget) so component configuration edits
    in any assembly configuration are detected accurately.
    """
    snap = {}
    deps = _extract_assembly_dependencies(swApp, file_abs)
    if deps:
        snap["dependencies"] = deps

    cfg_list = list(configs) if configs else []
    # Scale budget cleanly for multi-configuration assemblies (up to 15s max)
    asm_budget = min(15.0, max(6.0, len(cfg_list) * 2.5))
    asm_deadline = max(deadline, time.monotonic() + asm_budget)

    components_by_config = {}
    active_grouped = []
    orig_cfg = active_config or ""

    if len(cfg_list) > 1:
        # Read the already-active configuration FIRST (0ms switch cost!)
        if orig_cfg and orig_cfg in cfg_list:
            insts, grp = _read_assembly_components_current(model)
            components_by_config[orig_cfg] = insts
            active_grouped = grp

        for cfg_name in cfg_list[:15]:
            if cfg_name in components_by_config:
                continue
            if time.monotonic() > asm_deadline:
                snap["truncated_by_budget"] = True
                break
            # Note: ShowConfiguration2 returns False if cfg_name is ALREADY active!
            # Calling it and then reading components handles both already-active and switched states.
            try:
                _com_get(model, "ShowConfiguration2", cfg_name)
            except Exception:
                pass
            insts, grp = _read_assembly_components_current(model)
            components_by_config[cfg_name] = insts
            if not active_grouped:
                active_grouped = grp

        # Restore original active configuration cleanly
        if orig_cfg:
            try:
                _com_get(model, "ShowConfiguration2", orig_cfg)
            except Exception:
                pass
    else:
        single_cfg_name = orig_cfg or (cfg_list[0] if cfg_list else "Default")
        insts, grp = _read_assembly_components_current(model)
        components_by_config[single_cfg_name] = insts
        active_grouped = grp

    snap["components"] = active_grouped
    snap["components_by_config"] = components_by_config

    # 2. Mates (under MateGroup) & assembly-level features
    mates = []
    asm_features = []
    truncated = bool(snap.get("truncated_by_budget"))
    feat = _com_get(model, "FirstFeature")
    guard = 0
    while feat is not None and guard < 600:
        guard += 1
        if time.monotonic() > asm_deadline:
            truncated = True
            break
        ftype = str(_com_get(feat, "GetTypeName2") or "").strip()
        fname = str(_com_get(feat, "Name") or "").strip()

        if ftype == "MateGroup":
            sub = _com_get(feat, "GetFirstSubFeature")
            m_guard = 0
            while sub is not None and m_guard < 300:
                m_guard += 1
                if time.monotonic() > asm_deadline:
                    truncated = True
                    break
                m_name = str(_com_get(sub, "Name") or "").strip()
                m_type = str(_com_get(sub, "GetTypeName2") or "").strip()
                if m_name and m_type:
                    m_entry = {
                        "name": m_name,
                        "type": m_type,
                        "suppressed": _is_feature_suppressed(sub),
                    }
                    # Capture distance / angle mate values if present
                    m_dims = _extract_feature_dimensions(sub, asm_deadline)
                    if m_dims:
                        m_entry["dimensions"] = m_dims
                    mates.append(m_entry)
                sub = _com_get(sub, "GetNextSubFeature")
        elif ftype and ftype not in IGNORED_FEATURE_TYPES and ftype != "Reference" and fname:
            asm_features.append({
                "name": fname,
                "type": ftype,
                "suppressed": _is_feature_suppressed(feat),
            })

        feat = _com_get(feat, "GetNextFeature")

    snap["mates"] = mates
    snap["assembly_features"] = asm_features
    if truncated:
        snap["truncated_by_budget"] = True
    return snap


def _extract_drawing_snapshot(model, deadline):
    """Extract cached sheet/view/dimension/note/table metadata without loading 3D models."""
    snap = {}

    # 1. Sheets
    sheets = []
    try:
        sheet_names = _com_get(model, "GetSheetNames")
        if sheet_names:
            sheets = [str(s) for s in sheet_names if s]
    except Exception:
        pass
    snap["sheets"] = sheets

    # Active sheet properties (scale)
    try:
        curr_sheet = _com_get(model, "GetCurrentSheet")
        if curr_sheet:
            props = _com_get(curr_sheet, "GetProperties2") or _com_get(curr_sheet, "GetProperties")
            if isinstance(props, (tuple, list)) and len(props) >= 4:
                snap["active_sheet_scale"] = f"{_round_num(props[2], 3)}:{_round_num(props[3], 3)}"
    except Exception:
        pass

    # 2. Traverse views (FirstView is sheet itself, subsequent views are drawing views)
    views = []
    all_dims = []
    all_notes = []
    all_tables = []
    gtol_count = 0
    sf_count = 0
    truncated = False

    view = _com_get(model, "GetFirstView")
    v_guard = 0
    while view is not None and v_guard < 80:
        v_guard += 1
        if time.monotonic() > deadline:
            truncated = True
            break

        v_name = str(_com_get(view, "GetName2") or _com_get(view, "Name") or f"View{v_guard}").strip()
        ref_model = str(_com_get(view, "GetReferencedModelName") or "").strip()
        ref_cfg = str(_com_get(view, "ReferencedConfiguration") or "").strip()
        scale_dec = _com_get(view, "ScaleDecimal")

        if v_guard > 1:  # Skip sheet pseudo-view in view list, but still scan its notes/tables/dims below
            views.append({
                "name": v_name,
                "ref_model": os.path.basename(ref_model) if ref_model else "",
                "ref_config": ref_cfg,
                "scale": _round_num(scale_dec, 4) if scale_dec is not None else None,
            })

        # 2a. Display dimensions in view
        disp_dim = _com_get(view, "GetFirstDisplayDimension5")
        if disp_dim is None:
            disp_dim = _com_get(view, "GetFirstDisplayDimension")
        d_guard = 0
        while disp_dim is not None and d_guard < 250:
            d_guard += 1
            if time.monotonic() > deadline:
                truncated = True
                break
            entry = _extract_dimension_entry(disp_dim)
            if entry:
                # Capture prefix/suffix callout text if present
                try:
                    # swDimensionTextParts_e: Prefix=1, Suffix=2, CalloutAbove=3, CalloutBelow=4
                    prefix = str(_com_get(disp_dim, "GetText", 1) or "").strip()
                    suffix = str(_com_get(disp_dim, "GetText", 2) or "").strip()
                    callout = " ".join(p for p in (prefix, suffix) if p).strip()
                    if callout:
                        entry["callout"] = callout
                except Exception:
                    pass
                entry["view"] = v_name
                all_dims.append(entry)
            nxt = _com_get(disp_dim, "GetNext5")
            if nxt is None:
                nxt = _com_get(disp_dim, "GetNext3")
            disp_dim = nxt

        # 2b. Notes in view
        try:
            notes = _com_get(view, "GetNotes")
            if notes:
                for note in notes:
                    if time.monotonic() > deadline:
                        truncated = True
                        break
                    txt = str(_com_get(note, "GetText") or "").strip()
                    if txt:
                        # Collapse excessive internal whitespace while keeping meaningful lines
                        norm_txt = "\n".join(line.strip() for line in txt.splitlines() if line.strip())
                        if norm_txt:
                            all_notes.append(norm_txt)
        except Exception:
            pass

        # 2c. GD&T and surface finish counts
        try:
            gtols = _com_get(view, "GetGTols")
            if gtols:
                gtol_count += len(gtols)
        except Exception:
            pass
        try:
            sfs = _com_get(view, "GetSFSymbols")
            if sfs:
                sf_count += len(sfs)
        except Exception:
            pass

        # 2d. Tables (BOM / Revision / Hole / General)
        tbl = _com_get(view, "GetFirstTableAnnotation2")
        if tbl is None:
            tbl = _com_get(view, "GetFirstTableAnnotation")
        t_guard = 0
        while tbl is not None and t_guard < 10:
            t_guard += 1
            if time.monotonic() > deadline:
                truncated = True
                break
            try:
                t_type = _com_get(tbl, "Type")
                row_cnt = int(_com_get(tbl, "RowCount") or 0)
                col_cnt = int(_com_get(tbl, "ColumnCount") or 0)
                rows_data = []
                max_r = min(row_cnt, 40)
                max_c = min(col_cnt, 10)
                for r in range(max_r):
                    if time.monotonic() > deadline:
                        truncated = True
                        break
                    row_cells = []
                    for c in range(max_c):
                        cell_txt = str(_com_get(tbl, "DisplayedText", r, c) or _com_get(tbl, "Text", r, c) or "").strip()
                        row_cells.append(cell_txt)
                    if any(row_cells):
                        rows_data.append(" | ".join(row_cells))
                all_tables.append({
                    "type": int(t_type) if t_type is not None else -1,
                    "rows": row_cnt,
                    "cols": col_cnt,
                    "content": rows_data,
                })
            except Exception:
                pass
            tbl = _com_get(tbl, "GetNext")

        view = _com_get(view, "GetNextView")

    snap["views"] = views
    snap["dimensions"] = all_dims
    snap["notes"] = sorted(set(all_notes))
    snap["tables"] = all_tables
    snap["gtol_count"] = gtol_count
    snap["sf_symbol_count"] = sf_count
    if truncated:
        snap["truncated_by_budget"] = True
    return snap


def extract_snapshot_from_model(swApp, model, file_abs, doc_type=None, time_budget_sec=2.0):
    """Extract canonical engineering JSON snapshot from an open SolidWorks IModelDoc2."""
    if doc_type is None:
        doc_type = _doc_type_from_path(file_abs) or int(_com_get(model, "GetType") or 0)

    deadline = time.monotonic() + max(0.5, float(time_budget_sec))

    configs = []
    try:
        raw_cfgs = _com_get(model, "GetConfigurationNames")
        if raw_cfgs:
            configs = [str(c) for c in raw_cfgs if c]
    except Exception:
        pass

    active_config = ""
    try:
        # Fast & reliable even under DocumentVisible(False)
        hdr_cfg = _com_get(swApp, "GetActiveConfigurationName", os.path.normpath(file_abs))
        if hdr_cfg:
            active_config = str(hdr_cfg).strip()
    except Exception:
        pass
    if not active_config:
        try:
            cfg_mgr = _com_get(model, "ConfigurationManager")
            act = _com_get(cfg_mgr, "ActiveConfiguration") if cfg_mgr else None
            if act is None:
                act = _com_get(model, "GetActiveConfiguration")
            if act:
                active_config = str(_com_get(act, "Name") or "").strip()
        except Exception:
            pass

    snapshot = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "doc_type": doc_type,
        "file_ext": os.path.splitext(file_abs)[1].lower(),
        "configurations": configs,
        "active_config": active_config,
        "custom_properties": _extract_custom_properties(model, active_config),
    }

    if doc_type == SW_DOC_PART:
        snapshot.update(_extract_part_snapshot(model, active_config, deadline))
    elif doc_type == SW_DOC_ASSEMBLY:
        snapshot.update(_extract_assembly_snapshot(swApp, model, file_abs, configs, active_config, deadline))
    elif doc_type == SW_DOC_DRAWING:
        snapshot.update(_extract_drawing_snapshot(model, deadline))

    return snapshot


def _connect_solidworks(sldworks_exe=None, allow_launch=False):
    """Bind to running SolidWorks; launch only if allow_launch=True."""
    configured_exe = get_configured_solidworks_exe(sldworks_exe)
    swApp = None
    was_already_running = False

    try:
        raw_obj = win32com.client.GetActiveObject("SldWorks.Application")
        swApp = get_dynamic_sw_app(raw_obj)
        was_already_running = True
        return swApp, was_already_running
    except Exception:
        pass

    if not allow_launch:
        return None, False

    if not os.path.exists(configured_exe):
        return None, False

    import subprocess
    try:
        subprocess.Popen([configured_exe])
    except Exception:
        return None, False

    poll_start = time.time()
    while time.time() - poll_start < 45.0:
        try:
            raw_obj = win32com.client.GetActiveObject("SldWorks.Application")
            swApp = get_dynamic_sw_app(raw_obj)
            return swApp, False
        except Exception:
            time.sleep(0.5)
    return None, False


def _get_open_doc_paths(swApp):
    paths = set()
    try:
        docs = _com_get(swApp, "GetDocuments")
        if docs:
            for d in docs:
                p = str(_com_get(d, "GetPathName") or "").strip()
                if p:
                    paths.add(os.path.normpath(p).lower())
    except Exception:
        pass
    return paths


def _open_and_extract_single(swApp, file_abs, pre_existing_paths, time_budget_sec=2.0):
    """Open file invisibly in ReadOnly mode, extract snapshot, and close it cleanly."""
    doc_type = _doc_type_from_path(file_abs)
    if doc_type == 0:
        raise ValueError(f"Unsupported SolidWorks extension: {file_abs}")

    norm_abs = os.path.normpath(file_abs).lower()

    # Fast path: if the file is already open in SolidWorks, read directly without reopening/closing
    if norm_abs in pre_existing_paths:
        docs = _com_get(swApp, "GetDocuments") or []
        for d in docs:
            p = str(_com_get(d, "GetPathName") or "").strip()
            if p and os.path.normpath(p).lower() == norm_abs:
                return extract_snapshot_from_model(swApp, d, file_abs, doc_type=doc_type, time_budget_sec=time_budget_sec)

    # Save user preferences that prevent popups & external reference loading
    REF_PREF_SW_LOAD_EXT_REFS = 242
    orig_ext_ref = None
    orig_rebuild_err = None
    orig_warn_save = None
    orig_ref_prompt = None
    try:
        orig_ext_ref = swApp.GetUserPreferenceIntegerValue(REF_PREF_SW_LOAD_EXT_REFS)
        swApp.SetUserPreferenceIntegerValue(REF_PREF_SW_LOAD_EXT_REFS, 0)  # None
    except Exception:
        pass
    try:
        orig_rebuild_err = swApp.GetUserPreferenceToggle(119)
        swApp.SetUserPreferenceToggle(119, False)
    except Exception:
        pass
    try:
        orig_warn_save = swApp.GetUserPreferenceToggle(249)
        swApp.SetUserPreferenceToggle(249, False)
    except Exception:
        pass
    try:
        orig_ref_prompt = swApp.GetUserPreferenceToggle(15)
        swApp.SetUserPreferenceToggle(15, True)
    except Exception:
        pass

    # Invisible load: prevents viewport creation, GPU rerendering, and window flashing!
    invisible_set = False
    try:
        swApp.DocumentVisible(False, doc_type)
        invisible_set = True
    except Exception:
        pass

    model = None
    try:
        # swOpenDocOptions_Silent (1) | swOpenDocOptions_ReadOnly (2)
        # | swOpenDocOptions_IgnoreActivationAndSuppression (64) | swOpenDocOptions_AutoMissing (128)
        # Deliberately omit LoadModel (16/32) so drawings load cached views rapidly without 3D models.
        open_options = 1 | 2 | 64 | 128
        error = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        warning = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)

        model = swApp.OpenDoc6(os.path.normpath(file_abs), doc_type, open_options, "", error, warning)
        if not model:
            raise RuntimeError(f"OpenDoc6 failed for {os.path.basename(file_abs)} (error={error.value})")

        return extract_snapshot_from_model(swApp, model, file_abs, doc_type=doc_type, time_budget_sec=time_budget_sec)
    finally:
        # Close document if we opened it
        if model is not None:
            doc_title = str(_com_get(model, "GetTitle") or "").strip()
            model = None
            gc.collect()
            for identifier in (os.path.normpath(file_abs), doc_title, os.path.basename(file_abs)):
                if identifier:
                    try:
                        swApp.CloseDoc(identifier)
                        break
                    except Exception:
                        pass

        # Close any incidental child docs that were not open prior to this call
        try:
            after_docs = _com_get(swApp, "GetDocuments") or []
            for d in after_docs:
                p = str(_com_get(d, "GetPathName") or "").strip()
                t = str(_com_get(d, "GetTitle") or "").strip()
                if p and os.path.normpath(p).lower() not in pre_existing_paths:
                    try:
                        swApp.CloseDoc(t or p)
                    except Exception:
                        pass
        except Exception:
            pass

        if invisible_set:
            try:
                swApp.DocumentVisible(True, doc_type)
            except Exception:
                pass
        if orig_ext_ref is not None:
            try:
                swApp.SetUserPreferenceIntegerValue(REF_PREF_SW_LOAD_EXT_REFS, orig_ext_ref)
            except Exception:
                pass
        if orig_rebuild_err is not None:
            try:
                swApp.SetUserPreferenceToggle(119, orig_rebuild_err)
            except Exception:
                pass
        if orig_warn_save is not None:
            try:
                swApp.SetUserPreferenceToggle(249, orig_warn_save)
            except Exception:
                pass
        if orig_ref_prompt is not None:
            try:
                swApp.SetUserPreferenceToggle(15, orig_ref_prompt)
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description="SolidWorks Zero-Interference Snapshot Extractor")
    parser.add_argument("--jobs-json", required=True, help="Path to JSON file containing extraction jobs")
    parser.add_argument("--output-json", required=True, help="Path to write extraction results JSON")
    parser.add_argument("--solidworks-path", default=None, help="Configured SLDWORKS.exe path")
    parser.add_argument("--allow-launch", choices=["true", "false"], default="false")
    parser.add_argument("--respect-foreground", choices=["true", "false"], default="true")
    parser.add_argument("--time-budget", type=float, default=2.0, help="Per-file COM time budget in seconds")
    args = parser.parse_args()

    allow_launch = (args.allow_launch == "true")
    respect_fg = (args.respect_foreground == "true")

    if not WIN32_AVAILABLE:
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump({"results": {}, "error": "pywin32 not available", "aborted_foreground": False}, f)
        sys.exit(1)

    with open(args.jobs_json, "r", encoding="utf-8") as f:
        jobs_data = json.load(f)
    jobs = jobs_data.get("jobs", [])

    if respect_fg and is_solidworks_foreground():
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump({"results": {}, "error": None, "aborted_foreground": True}, f)
        sys.exit(0)

    if args.solidworks_path:
        os.environ[SOLIDWORKS_PATH_ENV] = get_configured_solidworks_exe(args.solidworks_path)

    pythoncom.CoInitialize()
    load_sw_typelib()

    swApp, was_already_running = _connect_solidworks(args.solidworks_path, allow_launch=allow_launch)
    if swApp is None:
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump({
                "results": {},
                "error": "SolidWorks is not running" if not allow_launch else "Could not connect to or launch SolidWorks",
                "aborted_foreground": False,
            }, f)
        pythoncom.CoUninitialize()
        sys.exit(2 if not allow_launch else 1)

    results = {}
    aborted_fg = False
    try:
        try:
            swApp.SetUserPreferenceToggle(389, False)  # swShowStartupScreen
        except Exception:
            pass

        pre_existing = _get_open_doc_paths(swApp)

        for job in jobs:
            job_id = str(job["id"])
            file_abs = os.path.abspath(job["path"])

            # Pause/abort gracefully if user clicked back into SolidWorks mid-batch
            if respect_fg and is_solidworks_foreground():
                aborted_fg = True
                break

            if not os.path.exists(file_abs):
                results[job_id] = {"ok": False, "error": f"File not found: {file_abs}"}
                continue

            try:
                snap = _open_and_extract_single(
                    swApp,
                    file_abs,
                    pre_existing_paths=pre_existing,
                    time_budget_sec=args.time_budget,
                )
                results[job_id] = {"ok": True, "snapshot": snap}
            except Exception as e:
                results[job_id] = {"ok": False, "error": str(e)}

        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump({
                "results": results,
                "error": None,
                "aborted_foreground": aborted_fg,
            }, f, ensure_ascii=False)
    finally:
        if not was_already_running:
            try:
                swApp.ExitApp()
            except Exception:
                pass
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    main()
