import os
import sys

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
        """Attempts to connect to SolidWorks COM interface."""
        if not WIN32_AVAILABLE:
            return None
            
        try:
            # CoInitialize is required for thread-safe COM access
            pythoncom.CoInitialize()
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
            
        try:
            if choice == 'save_and_close':
                # SolidWorks API ModelDoc2::Save3
                # 1 = swSaveAsOptions_Silent
                # We pass reference values as long, but pythoncom allows simple arguments
                # doc_obj.Save3(1, 0, 0)
                try:
                    # In some python environments, dynamic dispatch is needed
                    doc_obj.Save3(1, 0, 0)
                except Exception as e:
                    print(f"Standard Save3 failed, trying Save: {e}")
                    doc_obj.Save()
            
            # Close document using SW Application CloseDoc
            # CloseDoc takes the Title of the document
            sw.CloseDoc(target_doc['title'])
            return True
        except Exception as e:
            print(f"COM Error while closing file: {e}")
            # If we fail to close, ask the user if they want to proceed anyway
            return False
