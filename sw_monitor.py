import os
import sys

# Enforce UTF-8 output streams with replacement errors to prevent encoding crashes on Windows
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


try:
    import win32com.client
    import pythoncom
    import win32com.client.dynamic
    WIN32_AVAILABLE = True
except ImportError:
    WIN32_AVAILABLE = False

class SolidWorksMonitorService:
    def __init__(self):
        self.sw_app = None

    def _call_com_method(self, obj, name, *args):
        """Safely retrieves a COM property or calls a COM method depending on win32com type caching state."""
        try:
            val = getattr(obj, name)
            return val(*args)
        except TypeError as e:
            if "not callable" in str(e):
                return getattr(obj, name)
            raise
        except Exception:
            return getattr(obj, name)

    def _get_sw_app(self):
        """Attempts to connect to SolidWorks COM interface, caching the connection."""
        if not WIN32_AVAILABLE:
            return None
            
        try:
            # CoInitialize is required for thread-safe COM access
            pythoncom.CoInitialize()
            if self.sw_app:
                try:
                    # Simple property access to test connection validity
                    self.sw_app.Visible
                    return self.sw_app
                except Exception:
                    self.sw_app = None
                    
            # Try to get active SolidWorks instance
            self.sw_app = win32com.client.GetActiveObject("SldWorks.Application")
            return self.sw_app
        except Exception:
            self.sw_app = None
            return None

    def get_active_document(self):
        """Gets info about the currently active open document in SolidWorks."""
        sw = self._get_sw_app()
        if not sw:
            return None
        try:
            active_doc = sw.ActiveDoc
            if active_doc:
                path = self._call_com_method(active_doc, 'GetPathName')
                title = self._call_com_method(active_doc, 'GetTitle')
                try:
                    dirty = self._call_com_method(active_doc, 'GetSaveFlag')
                except Exception:
                    dirty = False
                return {
                    'title': title,
                    'path': path.replace("\\", "/"),
                    'dirty': bool(dirty)
                }
        except Exception as e:
            print(f"COM Error in get_active_document: {e}")
            self.sw_app = None
        return None

    def get_all_open_documents(self):
        """Returns a list of dicts for all open documents in SolidWorks."""
        sw = self._get_sw_app()
        if not sw:
            return []
        docs = []
        try:
            # sw.GetDocuments() returns an array/tuple of ModelDoc2 objects
            sw_docs = self._call_com_method(sw, 'GetDocuments')
            if sw_docs:
                for doc in sw_docs:
                    path = self._call_com_method(doc, 'GetPathName')
                    title = self._call_com_method(doc, 'GetTitle')
                    try:
                        dirty = self._call_com_method(doc, 'GetSaveFlag')
                    except Exception:
                        dirty = False
                    docs.append({
                        'title': title,
                        'path': path.replace("\\", "/"),
                        'dirty': bool(dirty),
                        'doc_obj': doc # reference to the COM object
                    })
        except Exception as e:
            print(f"COM Error in get_all_open_documents: {e}")
            self.sw_app = None
        return docs

    def check_and_close_file(self, file_rel_path, repo_path, prompt_callback):
        """
        Checks if a file (relative to repo) is open in SolidWorks.
        If it is open, calls prompt_callback(filename, is_dirty) which returns:
          'save_and_close': Save changes and close document in SW.
          'close_only': Close document in SW without saving.
          'cancel': Cancel the Git operation.
          'ignore': Proceed with Git operation without closing.
        Returns:
            bool: True if it's safe to proceed with the Git operation, False if cancelled.
        """
        abs_target_path = os.path.abspath(os.path.join(repo_path, file_rel_path)).replace("\\", "/")
        open_docs = self.get_all_open_documents()
        
        target_doc = None
        for doc in open_docs:
            if doc['path'].lower() == abs_target_path.lower():
                target_doc = doc
                break
                
        if not target_doc:
            # File is not open in SolidWorks, safe to proceed
            return True
            
        # File is open. Ask the user what to do.
        choice = prompt_callback(target_doc['title'], target_doc['dirty'])
        
        if choice == 'cancel':
            return False
        elif choice == 'ignore':
            return True
            
        # We need to save and/or close
        doc_obj = target_doc['doc_obj']
        sw = self._get_sw_app()
        if not sw:
            return True # If SW crashed meanwhile, proceed anyway
            
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
            if choice == 'save_and_close':
                # SolidWorks API ModelDoc2::Save3
                # 5 = swSaveAsOptions_Silent (1) | swSaveAsOptions_SaveReferenced (4)
                # We pass reference values as long, but pythoncom allows simple arguments
                # doc_obj.Save3(5, 0, 0)
                try:
                    # In some python environments, dynamic dispatch is needed
                    doc_obj.Save3(5, 0, 0)
                except Exception as e:
                    print(f"Standard Save3 failed, trying Save: {e}")
                    doc_obj.Save()
            elif choice == 'close_only':
                pass
            
            # Add a small delay for SolidWorks internal engine state synchronization
            import time
            time.sleep(0.2)

            orig_user_control = True
            try:
                orig_user_control = sw.UserControl
                sw.UserControl = False
                print(f"check_and_close_file: Set UserControl to False (was {orig_user_control})")
            except Exception as uc_err:
                print(f"check_and_close_file: Failed to set UserControl to False: {uc_err}")

            try:
                # Step 1: Close the main target document using CloseDoc FIRST.
                # This releases the active document lock and parent-child reference links.
                title_to_close = target_doc.get('title')
                if not title_to_close:
                    title_to_close = os.path.basename(abs_target_path)
                
                # Release target document COM object references before CloseDoc
                doc_obj = None
                target_doc = None
                open_docs = None
                import gc
                gc.collect()
                try:
                    pythoncom.CoCollectFreeUnusedLibraries()
                except:
                    pass
                
                print(f"check_and_close_file: closing main document '{title_to_close}' (path: '{abs_target_path}') via CloseDoc")
                sw.CloseDoc(title_to_close)

                # Allow SolidWorks to settle and release COM reference locks
                time.sleep(0.3)

                # Step 2: Clean up all REMAINING open documents (referenced/linked assemblies and skeletons)
                # using a dependency-aware iterative cleanup loop to avoid reference prompts.
                try:
                    iteration = 0
                    last_doc_count = -1
                    stuck_count = 0
                    
                    while iteration < 10:  # Try up to 10 passes to resolve nested references
                        all_docs = self._call_com_method(sw, 'GetDocuments')
                        if not all_docs:
                            break
                        
                        current_count = len(all_docs)
                        if current_count == last_doc_count:
                            stuck_count += 1
                            if stuck_count > 2:
                                break
                        else:
                            stuck_count = 0
                        last_doc_count = current_count
                        
                        parent_titles = []  # list of title
                        child_titles = []   # list of title
                        
                        for d in all_docs:
                            try:
                                try:
                                    path = self._call_com_method(d, 'GetPathName')
                                except Exception:
                                    path = getattr(d, 'GetPathName')
                                    if callable(path):
                                        path = path()
                                        
                                try:
                                    title = self._call_com_method(d, 'GetTitle')
                                except Exception:
                                    title = getattr(d, 'GetTitle')
                                    if callable(title):
                                        title = title()
                                        
                                if not title:
                                    title = path
                                    
                                if not title:
                                    continue
                                    
                                try:
                                    dtype = d.GetType()
                                except Exception:
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
                                             
                                if is_parent:
                                    parent_titles.append(title)
                                else:
                                    child_titles.append(title)
                            except Exception:
                                pass
                        
                        # Clear COM document references for cleanup loop
                        all_docs = None
                        d = None
                        gc.collect()
                        try:
                            pythoncom.CoCollectFreeUnusedLibraries()
                        except:
                            pass
                        
                        closed_any = False
                        # Close parents first to release references on children
                        for title in parent_titles:
                            try:
                                print(f"check_and_close_file (cleanup): closing parent '{title}' via CloseDoc")
                                sw.CloseDoc(title)
                                closed_any = True
                            except Exception:
                                pass
                                
                        if closed_any:
                            time.sleep(0.2)
                            
                        # Close children
                        for title in child_titles:
                            try:
                                print(f"check_and_close_file (cleanup): closing child '{title}' via CloseDoc")
                                sw.CloseDoc(title)
                                closed_any = True
                            except Exception:
                                pass
                                
                        if not closed_any:
                            break
                        time.sleep(0.2)
                        iteration += 1
                except Exception as e_post:
                    print(f"Warning: Failed to cleanup remaining referenced docs: {e_post}")
            finally:
                try:
                    sw.UserControl = orig_user_control
                    print(f"check_and_close_file: Restored UserControl to {orig_user_control}")
                except Exception as uc_err:
                    print(f"check_and_close_file: Failed to restore UserControl: {uc_err}")

            return True
        except Exception as e:
            print(f"COM Error while closing file: {e}")
            # If we fail to close, ask the user if they want to proceed anyway
            return False
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
