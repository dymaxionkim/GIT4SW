"""Snapshot cache, deterministic SolidWorks Diff engine, and Zero-Interference
background indexer for GIT4SW LLM commit message generation.
"""

import hashlib
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time

from sw_diff_runner import is_solidworks_foreground, SNAPSHOT_SCHEMA_VERSION

SW_EXTENSIONS = (".sldprt", ".sldasm", ".slddrw")
DIM_EPS_MM = 0.001
DIM_EPS_DEG = 0.01
BBOX_EPS_MM = 0.05
MASS_EPS_G = 0.1
PCT_EPS = 0.1


def _sha1_hex(text):
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()


def _safe_read_json(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _safe_write_json(path, data):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = f"{path}.tmp.{os.getpid()}.{threading.get_ident()}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def _diff_dicts(before, after, max_items=30):
    b = before or {}
    a = after or {}
    diff = {}
    for k in sorted(set(b.keys()) | set(a.keys())):
        bv = str(b.get(k, "")).strip()
        av = str(a.get(k, "")).strip()
        if bv != av:
            diff[k] = [bv, av]
            if len(diff) >= max_items:
                break
    return diff


def _summarize_new_snapshot(snap, rel_path):
    """Build a concise summary for a newly created (untracked) SolidWorks file."""
    summary = {
        "file": rel_path,
        "status": "added",
        "type": os.path.splitext(rel_path)[1].lower(),
    }
    if not snap:
        return summary

    props = snap.get("custom_properties") or {}
    interesting_props = {
        k: v for k, v in props.items()
        if v and k.lower() in ("partnumber", "partname", "material", "treatment", "description", "revision")
    }
    if interesting_props:
        summary["properties"] = interesting_props

    if snap.get("material"):
        summary["material"] = snap["material"]
    if snap.get("mass_props", {}).get("mass_g") is not None:
        summary["mass_g"] = snap["mass_props"]["mass_g"]
    if snap.get("bbox_mm"):
        summary["bbox_mm"] = snap["bbox_mm"]
    if snap.get("body_count") is not None:
        summary["body_count"] = snap["body_count"]

    ext = summary["type"]
    if ext == ".sldprt":
        feats = snap.get("features") or []
        summary["feature_count"] = len(feats)
        if feats:
            summary["main_features"] = [f"{f['name']}({f['type']})" for f in feats[:12]]
    elif ext == ".sldasm":
        comps = snap.get("components") or []
        summary["component_count"] = sum(c.get("qty", 1) for c in comps)
        if comps:
            summary["components"] = [
                f"{c['file']} x{c['qty']}" + (f" [{c['config']}]" if c.get("config") else "")
                for c in comps[:15]
            ]
        elif snap.get("dependencies"):
            summary["dependencies"] = snap["dependencies"][:15]
    elif ext == ".slddrw":
        if snap.get("sheets"):
            summary["sheets"] = snap["sheets"]
        views = snap.get("views") or []
        if views:
            summary["views"] = [
                f"{v['name']}" + (f" ({v['ref_model']})" if v.get("ref_model") else "")
                for v in views[:10]
            ]
        if snap.get("dimensions"):
            summary["dimension_count"] = len(snap["dimensions"])
        if snap.get("notes"):
            summary["notes_sample"] = snap["notes"][:5]

    return summary


def _diff_part(before, after):
    delta = {}

    # Material
    b_mat = (before.get("material") or "").strip()
    a_mat = (after.get("material") or "").strip()
    if b_mat != a_mat and (b_mat or a_mat):
        delta["material_diff"] = [b_mat or "(none)", a_mat or "(none)"]

    # Mass & volume
    b_mp = before.get("mass_props") or {}
    a_mp = after.get("mass_props") or {}
    bm = b_mp.get("mass_g")
    am = a_mp.get("mass_g")
    if bm is not None and am is not None:
        diff_g = am - bm
        pct = (diff_g / bm * 100.0) if bm > 0 else (100.0 if am > 0 else 0.0)
        if abs(diff_g) >= MASS_EPS_G or abs(pct) >= PCT_EPS:
            delta["mass_diff_g"] = [bm, am, f"{pct:+.1f}%"]

    bv = b_mp.get("volume_mm3")
    av = a_mp.get("volume_mm3")
    if bv is not None and av is not None and bv > 0:
        vpct = (av - bv) / bv * 100.0
        if abs(vpct) >= PCT_EPS:
            delta["volume_diff_pct"] = f"{vpct:+.1f}%"

    # Bounding box
    bb = before.get("bbox_mm")
    ab = after.get("bbox_mm")
    if isinstance(bb, list) and isinstance(ab, list) and len(bb) == 3 and len(ab) == 3:
        axis_diff = {}
        for idx, ax in enumerate(("X", "Y", "Z")):
            if abs(ab[idx] - bb[idx]) >= BBOX_EPS_MM:
                axis_diff[ax] = [bb[idx], ab[idx]]
        if axis_diff:
            delta["bbox_diff_mm"] = axis_diff

    # Body count
    bbc = before.get("body_count")
    abc = after.get("body_count")
    if bbc is not None and abc is not None and bbc != abc:
        delta["body_count_diff"] = [bbc, abc]

    # Features & driving dimensions
    b_feats = {f["name"]: f for f in (before.get("features") or []) if f.get("name")}
    a_feats = {f["name"]: f for f in (after.get("features") or []) if f.get("name")}

    added_feats = []
    for name, f in a_feats.items():
        if name not in b_feats:
            dims_preview = []
            for dn, dv in list((f.get("dimensions") or {}).items())[:3]:
                dims_preview.append(f"{dn}={dv['value']}{dv['unit']}")
            desc = f"{name} ({f.get('type', '')}" + (f", {', '.join(dims_preview)}" if dims_preview else "") + ")"
            added_feats.append(desc)

    removed_feats = [f"{name} ({f.get('type', '')})" for name, f in b_feats.items() if name not in a_feats]

    supp_changed = []
    dims_changed = []
    for name in sorted(set(b_feats.keys()) & set(a_feats.keys())):
        bf = b_feats[name]
        af = a_feats[name]
        if bool(bf.get("suppressed")) != bool(af.get("suppressed")):
            state = "Suppressed" if af.get("suppressed") else "Unsuppressed"
            supp_changed.append(f"{name}: {state}")

        b_dims = bf.get("dimensions") or {}
        a_dims = af.get("dimensions") or {}
        for dkey in sorted(set(b_dims.keys()) & set(a_dims.keys())):
            bd = b_dims[dkey]
            ad = a_dims[dkey]
            unit = ad.get("unit", "mm")
            eps = DIM_EPS_DEG if unit == "deg" else DIM_EPS_MM
            val_changed = abs(float(ad["value"]) - float(bd["value"])) >= eps
            tol_changed = bd.get("tol") != ad.get("tol")
            if val_changed or tol_changed:
                item = {
                    "feature": name,
                    "dim": dkey,
                    "before": f"{bd['value']}{bd.get('unit', 'mm')}",
                    "after": f"{ad['value']}{unit}",
                }
                if tol_changed:
                    item["tolerance_changed"] = [bd.get("tol"), ad.get("tol")]
                dims_changed.append(item)

    if added_feats:
        delta["features_added"] = added_feats[:20]
    if removed_feats:
        delta["features_removed"] = removed_feats[:20]
    if supp_changed:
        delta["features_suppression_changed"] = supp_changed[:20]
    if dims_changed:
        delta["dimensions_changed"] = dims_changed[:30]

    return delta


def _diff_assembly(before, after):
    delta = {}

    # Dependencies
    b_deps = set(before.get("dependencies") or [])
    a_deps = set(after.get("dependencies") or [])
    if a_deps - b_deps:
        delta["dependencies_added"] = sorted(a_deps - b_deps)[:20]
    if b_deps - a_deps:
        delta["dependencies_removed"] = sorted(b_deps - a_deps)[:20]

    # Per-configuration, per-instance component diff (captures component Configuration & suppression edits!)
    b_by_cfg = before.get("components_by_config") or {}
    a_by_cfg = after.get("components_by_config") or {}

    if b_by_cfg and a_by_cfg:
        shared_cfgs = sorted(set(b_by_cfg.keys()) & set(a_by_cfg.keys()))
        multi_cfg = len(set(b_by_cfg.keys()) | set(a_by_cfg.keys())) > 1

        # Group identical changes across assembly configs so output stays concise & readable
        cfg_change_map = {}   # (inst, file, b_c, a_c) -> [asm_cfg_names]
        supp_change_map = {}  # (inst, file, state) -> [asm_cfg_names]
        bom_change_map = {}   # (inst, file, state) -> [asm_cfg_names]
        inst_added_map = {}   # (inst, file, a_c) -> [asm_cfg_names]
        inst_removed_map = {} # (inst, file, b_c) -> [asm_cfg_names]

        for asm_cfg in shared_cfgs:
            b_list = b_by_cfg.get(asm_cfg) or []
            a_list = a_by_cfg.get(asm_cfg) or []
            # Skip incomplete side if one side was truncated by budget
            if (not b_list and a_list and before.get("truncated_by_budget")) or (
                not a_list and b_list and after.get("truncated_by_budget")
            ):
                continue
            b_insts = {c["instance"]: c for c in b_list if c.get("instance")}
            a_insts = {c["instance"]: c for c in a_list if c.get("instance")}

            for inst, ac in a_insts.items():
                if inst not in b_insts:
                    inst_added_map.setdefault((inst, ac.get("file", ""), ac.get("config", "")), []).append(asm_cfg)
            for inst, bc in b_insts.items():
                if inst not in a_insts:
                    inst_removed_map.setdefault((inst, bc.get("file", ""), bc.get("config", "")), []).append(asm_cfg)

            for inst in sorted(set(b_insts.keys()) & set(a_insts.keys())):
                bc = b_insts[inst]
                ac = a_insts[inst]
                cfile = ac.get("file") or bc.get("file") or inst
                b_ref_cfg = (bc.get("config") or "").strip()
                a_ref_cfg = (ac.get("config") or "").strip()
                if b_ref_cfg != a_ref_cfg:
                    cfg_change_map.setdefault((inst, cfile, b_ref_cfg, a_ref_cfg), []).append(asm_cfg)
                if bool(bc.get("suppressed")) != bool(ac.get("suppressed")):
                    state = "Suppressed" if ac.get("suppressed") else "Unsuppressed"
                    supp_change_map.setdefault((inst, cfile, state), []).append(asm_cfg)
                if bool(bc.get("exclude_bom")) != bool(ac.get("exclude_bom")):
                    state = "ExcludeFromBOM=True" if ac.get("exclude_bom") else "ExcludeFromBOM=False"
                    bom_change_map.setdefault((inst, cfile, state), []).append(asm_cfg)

        def _fmt_scope(cfg_names):
            if not multi_cfg:
                return ""
            if len(cfg_names) == len(shared_cfgs) and len(shared_cfgs) > 1:
                return "[All Configs] "
            return f"[{', '.join(cfg_names)}] "

        comp_cfgs_changed = []
        for (inst, cfile, b_c, a_c), cfgs in sorted(cfg_change_map.items()):
            comp_cfgs_changed.append(f"{_fmt_scope(cfgs)}{inst} ({cfile}): config '{b_c}' -> '{a_c}'")

        comps_changed = []
        for (inst, cfile, state), cfgs in sorted(supp_change_map.items()):
            comps_changed.append(f"{_fmt_scope(cfgs)}{inst} ({cfile}): {state}")
        for (inst, cfile, state), cfgs in sorted(bom_change_map.items()):
            comps_changed.append(f"{_fmt_scope(cfgs)}{inst} ({cfile}): {state}")

        comps_added = []
        for (inst, cfile, a_c), cfgs in sorted(inst_added_map.items()):
            cfg_tag = f" [{a_c}]" if a_c else ""
            comps_added.append(f"{_fmt_scope(cfgs)}{inst} ({cfile}){cfg_tag}")

        comps_removed = []
        for (inst, cfile, b_c), cfgs in sorted(inst_removed_map.items()):
            cfg_tag = f" [{b_c}]" if b_c else ""
            comps_removed.append(f"{_fmt_scope(cfgs)}{inst} ({cfile}){cfg_tag}")

        if comp_cfgs_changed:
            delta["component_configurations_changed"] = comp_cfgs_changed[:30]
        if comps_added:
            delta["components_added"] = comps_added[:25]
        if comps_removed:
            delta["components_removed"] = comps_removed[:25]
        if comps_changed:
            delta["components_changed"] = comps_changed[:25]
    else:
        # Fallback: grouped components (legacy / single-snapshot compatibility)
        def _comp_key(c):
            return f"{c.get('file', '')}::{c.get('config', '')}" if c.get("config") else c.get("file", "")

        b_comps = {_comp_key(c): c for c in (before.get("components") or [])}
        a_comps = {_comp_key(c): c for c in (after.get("components") or [])}

        comps_added = []
        comps_removed = []
        comps_changed = []

        for k, ac in a_comps.items():
            if k not in b_comps:
                comps_added.append(f"{ac['file']} x{ac['qty']}" + (f" [{ac['config']}]" if ac.get("config") else ""))
        for k, bc in b_comps.items():
            if k not in a_comps:
                comps_removed.append(f"{bc['file']} x{bc['qty']}" + (f" [{bc['config']}]" if bc.get("config") else ""))

        for k in sorted(set(b_comps.keys()) & set(a_comps.keys())):
            bc = b_comps[k]
            ac = a_comps[k]
            changes = []
            if bc.get("qty") != ac.get("qty"):
                changes.append(f"qty {bc.get('qty')} -> {ac.get('qty')}")
            if bc.get("suppressed_qty") != ac.get("suppressed_qty"):
                changes.append(f"suppressed {bc.get('suppressed_qty')} -> {ac.get('suppressed_qty')}")
            if bc.get("exclude_bom_qty") != ac.get("exclude_bom_qty"):
                changes.append(f"exclude_bom {bc.get('exclude_bom_qty')} -> {ac.get('exclude_bom_qty')}")
            if changes:
                comps_changed.append(f"{ac['file']}: {', '.join(changes)}")

        if comps_added:
            delta["components_added"] = comps_added[:25]
        if comps_removed:
            delta["components_removed"] = comps_removed[:25]
        if comps_changed:
            delta["components_changed"] = comps_changed[:25]

    # Mates
    b_mates = {m["name"]: m for m in (before.get("mates") or []) if m.get("name")}
    a_mates = {m["name"]: m for m in (after.get("mates") or []) if m.get("name")}

    mates_added = [f"{n} ({m.get('type', '')})" for n, m in a_mates.items() if n not in b_mates]
    mates_removed = [f"{n} ({m.get('type', '')})" for n, m in b_mates.items() if n not in a_mates]
    mates_changed = []
    for n in sorted(set(b_mates.keys()) & set(a_mates.keys())):
        bm = b_mates[n]
        am = a_mates[n]
        if bool(bm.get("suppressed")) != bool(am.get("suppressed")):
            mates_changed.append(f"{n}: {'Suppressed' if am.get('suppressed') else 'Unsuppressed'}")
        bd = bm.get("dimensions") or {}
        ad = am.get("dimensions") or {}
        for dk in sorted(set(bd.keys()) & set(ad.keys())):
            unit = ad[dk].get("unit", "mm")
            eps = DIM_EPS_DEG if unit == "deg" else DIM_EPS_MM
            if abs(float(ad[dk]["value"]) - float(bd[dk]["value"])) >= eps:
                mates_changed.append(f"{n}[{dk}]: {bd[dk]['value']}{unit} -> {ad[dk]['value']}{unit}")

    if mates_added:
        delta["mates_added"] = mates_added[:20]
    if mates_removed:
        delta["mates_removed"] = mates_removed[:20]
    if mates_changed:
        delta["mates_changed"] = mates_changed[:20]

    # Assembly-level features
    b_af = {f["name"]: f for f in (before.get("assembly_features") or []) if f.get("name")}
    a_af = {f["name"]: f for f in (after.get("assembly_features") or []) if f.get("name")}
    if set(a_af.keys()) - set(b_af.keys()):
        delta["assembly_features_added"] = sorted(set(a_af.keys()) - set(b_af.keys()))[:15]
    if set(b_af.keys()) - set(a_af.keys()):
        delta["assembly_features_removed"] = sorted(set(b_af.keys()) - set(a_af.keys()))[:15]

    return delta


def _diff_drawing(before, after):
    delta = {}

    # Sheets & scale
    b_sheets = set(before.get("sheets") or [])
    a_sheets = set(after.get("sheets") or [])
    if a_sheets - b_sheets:
        delta["sheets_added"] = sorted(a_sheets - b_sheets)
    if b_sheets - a_sheets:
        delta["sheets_removed"] = sorted(b_sheets - a_sheets)
    if before.get("active_sheet_scale") != after.get("active_sheet_scale"):
        if before.get("active_sheet_scale") or after.get("active_sheet_scale"):
            delta["sheet_scale_changed"] = [before.get("active_sheet_scale"), after.get("active_sheet_scale")]

    # Views
    b_views = {v["name"]: v for v in (before.get("views") or []) if v.get("name")}
    a_views = {v["name"]: v for v in (after.get("views") or []) if v.get("name")}
    if set(a_views.keys()) - set(b_views.keys()):
        delta["views_added"] = sorted(set(a_views.keys()) - set(b_views.keys()))[:15]
    if set(b_views.keys()) - set(a_views.keys()):
        delta["views_removed"] = sorted(set(b_views.keys()) - set(a_views.keys()))[:15]
    views_changed = []
    for vn in sorted(set(b_views.keys()) & set(a_views.keys())):
        bv, av = b_views[vn], a_views[vn]
        if bv.get("scale") != av.get("scale"):
            views_changed.append(f"{vn}: scale {bv.get('scale')} -> {av.get('scale')}")
        if bv.get("ref_config") != av.get("ref_config"):
            views_changed.append(f"{vn}: config {bv.get('ref_config')} -> {av.get('ref_config')}")
    if views_changed:
        delta["views_changed"] = views_changed[:15]

    # Dimensions (keyed by view + name)
    def _dim_sig(d):
        callout = f" ({d['callout']})" if d.get("callout") else ""
        tol = f" tol={d['tol']}" if d.get("tol") else ""
        return f"{d['value']}{d.get('unit', 'mm')}{callout}{tol}"

    b_dims = {f"{d.get('view', '')}::{d.get('name', idx)}": d for idx, d in enumerate(before.get("dimensions") or [])}
    a_dims = {f"{d.get('view', '')}::{d.get('name', idx)}": d for idx, d in enumerate(after.get("dimensions") or [])}

    dims_added = [_dim_sig(a_dims[k]) for k in sorted(set(a_dims) - set(b_dims))]
    dims_removed = [_dim_sig(b_dims[k]) for k in sorted(set(b_dims) - set(a_dims))]
    dims_changed = []
    for k in sorted(set(a_dims) & set(b_dims)):
        bd, ad = b_dims[k], a_dims[k]
        unit = ad.get("unit", "mm")
        eps = DIM_EPS_DEG if unit == "deg" else DIM_EPS_MM
        if (
            abs(float(ad["value"]) - float(bd["value"])) >= eps
            or bd.get("callout") != ad.get("callout")
            or bd.get("tol") != ad.get("tol")
        ):
            dims_changed.append(f"{ad.get('name', k)}: {_dim_sig(bd)} -> {_dim_sig(ad)}")

    if dims_added:
        delta["dimensions_added"] = dims_added[:20]
    if dims_removed:
        delta["dimensions_removed"] = dims_removed[:20]
    if dims_changed:
        delta["dimensions_changed"] = dims_changed[:25]

    # Notes
    b_notes = set(before.get("notes") or [])
    a_notes = set(after.get("notes") or [])
    notes_added = sorted(a_notes - b_notes)
    notes_removed = sorted(b_notes - a_notes)
    if notes_added:
        delta["notes_added"] = [n[:160] for n in notes_added[:10]]
    if notes_removed:
        delta["notes_removed"] = [n[:160] for n in notes_removed[:10]]

    # Tables (compare row lines)
    b_tbl_rows = []
    for t in (before.get("tables") or []):
        b_tbl_rows.extend(t.get("content") or [])
    a_tbl_rows = []
    for t in (after.get("tables") or []):
        a_tbl_rows.extend(t.get("content") or [])
    b_tset, a_tset = set(b_tbl_rows), set(a_tbl_rows)
    if a_tset - b_tset:
        delta["table_rows_added"] = [r[:140] for r in sorted(a_tset - b_tset)[:10]]
    if b_tset - a_tset:
        delta["table_rows_removed"] = [r[:140] for r in sorted(b_tset - a_tset)[:10]]

    # GD&T / Surface Finish counts
    if before.get("gtol_count", 0) != after.get("gtol_count", 0):
        delta["gtol_count_diff"] = [before.get("gtol_count", 0), after.get("gtol_count", 0)]
    if before.get("sf_symbol_count", 0) != after.get("sf_symbol_count", 0):
        delta["surface_finish_count_diff"] = [before.get("sf_symbol_count", 0), after.get("sf_symbol_count", 0)]

    return delta


def compute_snapshot_diff(before_snap, after_snap, rel_path, status):
    """Compute a deterministic engineering delta dictionary for a single SolidWorks file."""
    ext = os.path.splitext(rel_path)[1].lower()

    if status == "untracked":
        return _summarize_new_snapshot(after_snap, rel_path)

    if status == "deleted":
        res = {"file": rel_path, "status": "deleted", "type": ext}
        if before_snap and before_snap.get("custom_properties", {}).get("Partname"):
            res["partname"] = before_snap["custom_properties"]["Partname"]
        return res

    # Modified file
    delta = {
        "file": rel_path,
        "status": "modified",
        "type": ext,
    }
    if not before_snap or not after_snap:
        delta["note"] = "partial_metadata_only"
        if after_snap:
            delta["current_summary"] = _summarize_new_snapshot(after_snap, rel_path)
        return delta

    # Common: custom properties & configurations
    props_diff = _diff_dicts(before_snap.get("custom_properties"), after_snap.get("custom_properties"))
    if props_diff:
        delta["properties_diff"] = props_diff

    b_cfgs = set(before_snap.get("configurations") or [])
    a_cfgs = set(after_snap.get("configurations") or [])
    if a_cfgs - b_cfgs:
        delta["configs_added"] = sorted(a_cfgs - b_cfgs)
    if b_cfgs - a_cfgs:
        delta["configs_removed"] = sorted(b_cfgs - a_cfgs)

    if ext == ".sldprt":
        delta.update(_diff_part(before_snap, after_snap))
    elif ext == ".sldasm":
        delta.update(_diff_assembly(before_snap, after_snap))
    elif ext == ".slddrw":
        delta.update(_diff_drawing(before_snap, after_snap))

    # Flag pure rebuild / camera-save updates so LLM doesn't hallucinate edits
    change_keys = [k for k in delta.keys() if k not in ("file", "status", "type")]
    if not change_keys:
        delta["structural_changes"] = "none"
        if after_snap.get("truncated_by_budget") or before_snap.get("truncated_by_budget"):
            delta["note"] = "large_file_partial_scan"

    return delta


def build_llm_payload(file_diffs, max_chars=12000):
    """Compress list of file diffs to safely fit within LLM prompt token budgets."""
    payload = {
        "total_files": len(file_diffs),
        "changes": [],
    }
    used = 0
    for idx, diff_item in enumerate(file_diffs):
        serialized = json.dumps(diff_item, ensure_ascii=False)
        if used + len(serialized) <= max_chars or idx == 0:
            payload["changes"].append(diff_item)
            used += len(serialized)
        else:
            # Compact fallback for remaining files beyond budget
            compact = {
                "file": diff_item.get("file"),
                "status": diff_item.get("status"),
                "type": diff_item.get("type"),
                "omitted_details": True,
            }
            payload["changes"].append(compact)
            used += len(json.dumps(compact, ensure_ascii=False))
            if used > max_chars + 2500:
                payload["additional_files_omitted"] = len(file_diffs) - (idx + 1)
                break
    return payload


class DiffCacheStore:
    """Manages persistent snapshot & working-delta caches inside .git/git4sw_ai_cache/."""

    def __init__(self, git_service):
        self.git_service = git_service

    def _cache_root(self):
        repo_path = self.git_service.repo_path
        git_dir = None
        try:
            if self.git_service.repo and self.git_service.repo.git_dir:
                git_dir = self.git_service.repo.git_dir
        except Exception:
            pass
        if not git_dir:
            git_dir = os.path.join(repo_path, ".git")
        return os.path.join(git_dir, "git4sw_ai_cache")

    def _blob_path(self, oid):
        safe_oid = "".join(c for c in str(oid) if c.isalnum() or c in ("_", "-"))
        return os.path.join(self._cache_root(), "blobs", f"{safe_oid}.json")

    def _working_path(self, rel_path):
        norm = rel_path.replace("\\", "/").lower()
        return os.path.join(self._cache_root(), "working", f"{_sha1_hex(norm)}.json")

    def get_blob_snapshot(self, oid):
        if not oid:
            return None
        snap = _safe_read_json(self._blob_path(oid))
        if snap and snap.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
            return None
        return snap

    def save_blob_snapshot(self, oid, snapshot):
        if not oid or snapshot is None:
            return
        _safe_write_json(self._blob_path(oid), snapshot)

    def get_working_entry(self, rel_path):
        return _safe_read_json(self._working_path(rel_path))

    def get_valid_cached_delta(self, rel_path, status, abs_path, head_oid):
        """Return cached delta dict if (schema_version, mtime_ns, size, status, head_oid) match current disk state."""
        entry = self.get_working_entry(rel_path)
        if (
            not entry
            or entry.get("schema_version") != SNAPSHOT_SCHEMA_VERSION
            or entry.get("status") != status
            or entry.get("head_oid") != head_oid
        ):
            return None
        if status == "deleted":
            return entry.get("delta")
        if not os.path.isfile(abs_path):
            return None
        try:
            st = os.stat(abs_path)
        except OSError:
            return None
        if entry.get("mtime_ns") == st.st_mtime_ns and entry.get("size") == st.st_size:
            return entry.get("delta")
        return None

    def save_working_entry(self, rel_path, status, abs_path, head_oid, working_snapshot, delta):
        mtime_ns = 0
        size = 0
        if status != "deleted" and os.path.isfile(abs_path):
            try:
                st = os.stat(abs_path)
                mtime_ns = st.st_mtime_ns
                size = st.st_size
            except OSError:
                pass
        _safe_write_json(
            self._working_path(rel_path),
            {
                "schema_version": SNAPSHOT_SCHEMA_VERSION,
                "rel_path": rel_path.replace("\\", "/"),
                "status": status,
                "head_oid": head_oid,
                "mtime_ns": mtime_ns,
                "size": size,
                "working_snapshot": working_snapshot,
                "delta": delta,
                "updated_at": time.time(),
            },
        )


def _get_non_cad_file_diff(git_service, rel_path, status):
    """Lightweight git diff summary for non-CAD text files (e.g. README.md)."""
    item = {
        "file": rel_path,
        "status": "added" if status == "untracked" else status,
        "type": os.path.splitext(rel_path)[1].lower() or "file",
    }
    if status == "modified":
        try:
            diff_out = git_service._run_lfs_cmd(["git", "diff", "--unified=1", "--", rel_path], check=False)
            if diff_out:
                item["text_diff_excerpt"] = diff_out[:1000]
        except Exception:
            pass
    return item


class BackgroundDiffWorker:
    """Zero-Interference background indexer that pre-computes SolidWorks diffs
    only when the user is NOT actively working in SolidWorks.
    """

    def __init__(
        self,
        get_git_service_cb,
        get_workspace_path_cb,
        load_config_cb,
        is_busy_cb,
        get_open_files_cb,
        set_suppress_auto_lock_cb,
        on_status_change_cb=None,
        log_cb=None,
    ):
        self._get_git_service = get_git_service_cb
        self._get_workspace_path = get_workspace_path_cb
        self._load_config = load_config_cb
        self._is_busy = is_busy_cb
        self._get_open_files = get_open_files_cb
        self._set_suppress_auto_lock = set_suppress_auto_lock_cb
        self._on_status_change = on_status_change_cb
        self._log = log_cb

        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._lock = threading.Lock()
        self._run_lock = threading.Lock()  # Serialize sw_diff_runner.py executions
        self._pending = {}  # lower_rel_path -> (rel_path, status)
        self._tracked_targets = {}  # lower_rel_path -> (rel_path, status)
        self._is_indexing = False
        self._failed_signatures = {}  # lower_rel_path -> (mtime_ns, size, head_oid) to avoid infinite retry loops

        self._thread = threading.Thread(target=self._loop, name="GIT4SW-BgDiffWorker", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        self._wake_event.set()

    def clear_queue(self):
        with self._lock:
            self._pending.clear()
            self._tracked_targets.clear()
            self._failed_signatures.clear()
        self._emit_status()

    def schedule_files(self, file_entries):
        """Queue changed SolidWorks files (list of dicts {'file': rel, 'status': st})."""
        cfg = self._load_config() or {}
        auto_bg = cfg.get("auto_bg_diff", True)
        if isinstance(auto_bg, str):
            auto_bg = auto_bg.strip().lower() not in ("false", "0", "no")

        git_service = self._get_git_service()
        workspace_path = self._get_workspace_path()
        if not git_service or not git_service.is_git_repo():
            return

        cache = DiffCacheStore(git_service)
        new_tracked = {}
        enqueued_any = False

        for entry in (file_entries or []):
            rel = entry.get("file", "").replace("\\", "/")
            st = entry.get("status", "unmodified")
            if st not in ("modified", "untracked", "deleted"):
                continue
            if os.path.splitext(rel)[1].lower() not in SW_EXTENSIONS:
                continue
            key = rel.lower()
            new_tracked[key] = (rel, st)

            if not auto_bg:
                continue

            abs_path = os.path.join(workspace_path, rel)
            head_oid = None if st == "untracked" else git_service.get_file_head_oid(rel)
            if cache.get_valid_cached_delta(rel, st, abs_path, head_oid) is not None:
                continue

            # Skip if this exact file signature already failed once in background
            sig = self._file_signature(abs_path, st, head_oid)
            with self._lock:
                if self._failed_signatures.get(key) == sig:
                    continue
                self._pending[key] = (rel, st)
                enqueued_any = True

        with self._lock:
            self._tracked_targets = new_tracked
            # Remove files no longer changed from pending
            for k in list(self._pending.keys()):
                if k not in new_tracked:
                    del self._pending[k]

        if enqueued_any:
            self._wake_event.set()
        self._emit_status()

    @staticmethod
    def _file_signature(abs_path, status, head_oid):
        if status == "deleted" or not os.path.isfile(abs_path):
            return (0, 0, status, head_oid)
        try:
            st = os.stat(abs_path)
            return (st.st_mtime_ns, st.st_size, status, head_oid)
        except OSError:
            return (0, 0, status, head_oid)

    def get_cache_progress(self, target_entries=None):
        """Return (ready_count, total_count, is_indexing)."""
        git_service = self._get_git_service()
        workspace_path = self._get_workspace_path()
        if not git_service or not git_service.is_git_repo():
            return 0, 0, False

        if target_entries is None:
            with self._lock:
                items = list(self._tracked_targets.values())
                indexing = self._is_indexing
        else:
            items = [
                (e["file"].replace("\\", "/"), e["status"])
                for e in target_entries
                if e.get("status") in ("modified", "untracked", "deleted")
                and os.path.splitext(e.get("file", ""))[1].lower() in SW_EXTENSIONS
            ]
            with self._lock:
                indexing = self._is_indexing

        cache = DiffCacheStore(git_service)
        ready = 0
        for rel, st in items:
            abs_path = os.path.join(workspace_path, rel)
            head_oid = None if st == "untracked" else git_service.get_file_head_oid(rel)
            if cache.get_valid_cached_delta(rel, st, abs_path, head_oid) is not None:
                ready += 1
        return ready, len(items), indexing

    def _emit_status(self):
        if not self._on_status_change:
            return
        try:
            ready, total, indexing = self.get_cache_progress()
            self._on_status_change(ready, total, indexing)
        except Exception:
            pass

    def _run_extractor_subprocess(self, jobs, solidworks_exe, allow_launch=False, respect_foreground=True, time_budget=2.0):
        """Execute sw_diff_runner.py in an isolated subprocess with auto-lock suppressed."""
        if not jobs:
            return {}, False, None

        runner_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sw_diff_runner.py")
        python_exe = sys.executable

        with tempfile.TemporaryDirectory(prefix="git4sw_diff_") as tmpdir:
            jobs_json = os.path.join(tmpdir, "jobs.json")
            out_json = os.path.join(tmpdir, "out.json")
            with open(jobs_json, "w", encoding="utf-8") as f:
                json.dump({"jobs": jobs}, f, ensure_ascii=False)

            cmd = [
                python_exe,
                runner_script,
                "--jobs-json", jobs_json,
                "--output-json", out_json,
                "--allow-launch", "true" if allow_launch else "false",
                "--respect-foreground", "true" if respect_foreground else "false",
                "--time-budget", str(time_budget),
            ]
            if solidworks_exe:
                cmd.extend(["--solidworks-path", solidworks_exe])

            kwargs = {}
            if sys.platform == "win32":
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = subprocess.SW_HIDE
                kwargs["startupinfo"] = si
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            timeout_total = max(30, int(len(jobs) * (time_budget + 8) + (50 if allow_launch else 15)))

            with self._run_lock:
                self._set_suppress_auto_lock(True)
                try:
                    proc = subprocess.run(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=timeout_total,
                        **kwargs,
                    )
                except subprocess.TimeoutExpired:
                    return {}, False, f"Snapshot extraction timed out after {timeout_total}s"
                finally:
                    self._set_suppress_auto_lock(False)

            if os.path.isfile(out_json):
                data = _safe_read_json(out_json) or {}
                return (
                    data.get("results") or {},
                    bool(data.get("aborted_foreground")),
                    data.get("error"),
                )

            stderr_txt = proc.stderr.decode("utf-8", errors="replace").strip() if proc.stderr else ""
            return {}, False, stderr_txt or f"sw_diff_runner exited with code {proc.returncode}"

    def _extract_batch(self, batch_items, allow_launch=False, respect_foreground=True, progress_cb=None):
        """Extract missing HEAD/Working snapshots for batch_items and compute/cache deltas.

        batch_items: list of (rel_path, status)
        Returns (deltas_by_lower_path, aborted_foreground, error_msg).
        """
        git_service = self._get_git_service()
        workspace_path = self._get_workspace_path()
        cfg = self._load_config() or {}
        solidworks_exe = cfg.get("solidworks_path") or None
        cache = DiffCacheStore(git_service)

        deltas = {}
        jobs = []
        plan = []  # list of dicts describing how to assemble each file's diff
        temp_head_files = []

        backup_dir = os.path.normpath(os.path.join(workspace_path, ".backup"))

        try:
            for rel_path, status in batch_items:
                abs_path = os.path.normpath(os.path.join(workspace_path, rel_path))
                head_oid = None if status == "untracked" else git_service.get_file_head_oid(rel_path)

                cached = cache.get_valid_cached_delta(rel_path, status, abs_path, head_oid)
                if cached is not None:
                    deltas[rel_path.lower()] = cached
                    continue

                item_plan = {
                    "rel_path": rel_path,
                    "status": status,
                    "abs_path": abs_path,
                    "head_oid": head_oid,
                    "before_snap": cache.get_blob_snapshot(head_oid) if head_oid else None,
                    "after_snap": None,
                    "head_job_id": None,
                    "work_job_id": None,
                }

                # Need HEAD extraction?
                if status in ("modified", "deleted") and head_oid and item_plan["before_snap"] is None:
                    base, ext = os.path.splitext(os.path.basename(rel_path))
                    short_id = _sha1_hex(f"{rel_path}:{head_oid}")[:10]
                    head_tmp = os.path.join(backup_dir, f"{base}__GIT4SW_HEAD_{short_id}{ext}")
                    try:
                        git_service.extract_head_version_to_file(rel_path, head_tmp)
                        temp_head_files.append(head_tmp)
                        h_job_id = f"head::{short_id}"
                        item_plan["head_job_id"] = h_job_id
                        jobs.append({"id": h_job_id, "path": head_tmp, "logical_path": rel_path})
                    except Exception as e:
                        item_plan["head_error"] = str(e)

                # Need Working Tree extraction?
                if status in ("modified", "untracked") and os.path.isfile(abs_path):
                    w_job_id = f"work::{_sha1_hex(rel_path.lower())[:10]}"
                    item_plan["work_job_id"] = w_job_id
                    jobs.append({"id": w_job_id, "path": abs_path, "logical_path": rel_path})

                plan.append(item_plan)

            aborted_fg = False
            run_err = None
            results = {}
            if jobs:
                if progress_cb:
                    progress_cb(f"Extracting SolidWorks metadata ({len(jobs)} snapshot(s))...")
                results, aborted_fg, run_err = self._run_extractor_subprocess(
                    jobs,
                    solidworks_exe=solidworks_exe,
                    allow_launch=allow_launch,
                    respect_foreground=respect_foreground,
                    time_budget=2.0,
                )

            for item in plan:
                rel_path = item["rel_path"]
                status = item["status"]
                abs_path = item["abs_path"]
                head_oid = item["head_oid"]

                before_snap = item["before_snap"]
                after_snap = item["after_snap"]

                h_id = item["head_job_id"]
                if h_id and h_id in results and results[h_id].get("ok"):
                    before_snap = results[h_id]["snapshot"]
                    cache.save_blob_snapshot(head_oid, before_snap)

                w_id = item["work_job_id"]
                if w_id and w_id in results and results[w_id].get("ok"):
                    after_snap = results[w_id]["snapshot"]

                # Only cache if we got the required snapshots (avoid caching partial results when aborted)
                have_needed_work = (status == "deleted") or (after_snap is not None)
                have_needed_head = (status == "untracked") or (head_oid is None) or (before_snap is not None)

                if have_needed_work and have_needed_head:
                    delta = compute_snapshot_diff(before_snap, after_snap, rel_path, status)
                    cache.save_working_entry(rel_path, status, abs_path, head_oid, after_snap, delta)
                    deltas[rel_path.lower()] = delta
                elif not aborted_fg and (h_id in results or w_id in results):
                    # Extraction attempted and partially/fully failed — build safe fallback delta
                    delta = compute_snapshot_diff(before_snap, after_snap, rel_path, status)
                    err_detail = (
                        (results.get(w_id) or {}).get("error")
                        or (results.get(h_id) or {}).get("error")
                        or item.get("head_error")
                        or run_err
                    )
                    if err_detail:
                        delta["extraction_note"] = str(err_detail)[:180]
                    deltas[rel_path.lower()] = delta
                    if not allow_launch:
                        sig = self._file_signature(abs_path, status, head_oid)
                        with self._lock:
                            self._failed_signatures[rel_path.lower()] = sig

            return deltas, aborted_fg, run_err
        finally:
            for tmp_f in temp_head_files:
                if os.path.exists(tmp_f):
                    try:
                        os.remove(tmp_f)
                    except Exception:
                        pass

    def _loop(self):
        """Idle background loop with strict Zero-Interference gating."""
        while not self._stop_event.is_set():
            self._wake_event.wait(timeout=4.0)
            self._wake_event.clear()
            if self._stop_event.is_set():
                break

            with self._lock:
                if not self._pending:
                    continue

            cfg = self._load_config() or {}
            auto_bg = cfg.get("auto_bg_diff", True)
            if isinstance(auto_bg, str):
                auto_bg = auto_bg.strip().lower() not in ("false", "0", "no")
            if not auto_bg:
                continue

            # Guard 1: Do not compete with active GIT4SW tasks (EXPORT, BOM, Git sync)
            if self._is_busy():
                continue

            # Guard 2: NEVER run background COM work while user is actively focused on SolidWorks!
            if is_solidworks_foreground():
                continue

            # Guard 3: Pick only files that are NOT currently open in SolidWorks
            open_lower = {p.replace("\\", "/").lower() for p in (self._get_open_files() or [])}
            batch = []
            with self._lock:
                for key, pair in list(self._pending.items()):
                    if key not in open_lower:
                        batch.append(pair)
                    if len(batch) >= 3:  # Process in small batches so we yield immediately if user returns to SW
                        break

            if not batch:
                continue

            with self._lock:
                self._is_indexing = True
            self._emit_status()

            try:
                deltas, aborted_fg, run_err = self._extract_batch(
                    batch,
                    allow_launch=False,  # Background worker NEVER unexpectedly launches SLDWORKS.exe
                    respect_foreground=True,
                )
                with self._lock:
                    for rel, _ in batch:
                        if rel.lower() in deltas:
                            self._pending.pop(rel.lower(), None)
                    # If SolidWorks isn't running, keep items in pending silently until SW starts or user clicks AI Draft
                    if run_err and "not running" in str(run_err).lower():
                        pass
            except Exception as e:
                if self._log:
                    print(f"[BgDiffWorker] Error: {e}", flush=True)
            finally:
                with self._lock:
                    self._is_indexing = False
                self._emit_status()

            # Brief cooldown between small batches
            if not self._stop_event.is_set():
                self._stop_event.wait(timeout=1.5)

    def prepare_diffs_for_commit(self, target_entries, progress_cb=None):
        """Synchronously gather diffs for [AI Draft]:
        - Immediately consumes all pre-computed cache hits (0ms).
        - Runs sw_diff_runner.py ONLY for any remaining uncached SolidWorks files.
        - Also collects lightweight text diff excerpts for non-CAD files.
        """
        git_service = self._get_git_service()
        workspace_path = self._get_workspace_path()
        cache = DiffCacheStore(git_service)

        ordered_results = []
        sw_missing = []
        cached_map = {}

        for entry in target_entries:
            rel = entry["file"].replace("\\", "/")
            st = entry.get("status", "modified")
            ext = os.path.splitext(rel)[1].lower()

            if ext not in SW_EXTENSIONS:
                cached_map[rel.lower()] = _get_non_cad_file_diff(git_service, rel, st)
                continue

            abs_path = os.path.normpath(os.path.join(workspace_path, rel))
            head_oid = None if st == "untracked" else git_service.get_file_head_oid(rel)
            cached = cache.get_valid_cached_delta(rel, st, abs_path, head_oid)
            if cached is not None:
                cached_map[rel.lower()] = cached
            else:
                sw_missing.append((rel, st))

        if sw_missing:
            # Cap synchronous on-demand COM extraction to 12 files for responsiveness; summarize overflow cleanly
            cap = 12
            to_extract = sw_missing[:cap]
            overflow = sw_missing[cap:]
            if progress_cb:
                progress_cb(
                    f"Using {len(cached_map)} cached diff(s); analyzing {len(to_extract)} remaining SolidWorks file(s)..."
                )
            fresh_deltas, _, run_err = self._extract_batch(
                to_extract,
                allow_launch=True,
                respect_foreground=False,
                progress_cb=progress_cb,
            )
            cached_map.update(fresh_deltas)

            # Fallback for any files that still failed (e.g. SolidWorks unavailable)
            for rel, st in to_extract:
                if rel.lower() not in cached_map:
                    cached_map[rel.lower()] = {
                        "file": rel,
                        "status": "added" if st == "untracked" else st,
                        "type": os.path.splitext(rel)[1].lower(),
                        "extraction_note": run_err or "SolidWorks metadata unavailable",
                    }
            for rel, st in overflow:
                cached_map[rel.lower()] = {
                    "file": rel,
                    "status": "added" if st == "untracked" else st,
                    "type": os.path.splitext(rel)[1].lower(),
                    "note": "batch_cap_summary_only",
                }
        else:
            if progress_cb:
                progress_cb(f"All {len(cached_map)} file diff(s) ready from background cache (0ms extraction).")

        for entry in target_entries:
            rel = entry["file"].replace("\\", "/")
            if rel.lower() in cached_map:
                ordered_results.append(cached_map[rel.lower()])

        with self._lock:
            for k in cached_map:
                self._pending.pop(k, None)
        self._emit_status()

        return ordered_results
