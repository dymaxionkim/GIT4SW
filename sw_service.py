import os
import sys

# We only import win32com if on Windows
try:
    import win32com.client
    import pythoncom
    import win32com.client.dynamic
    WIN32_AVAILABLE = True
except ImportError:
    WIN32_AVAILABLE = False

class SolidWorksService:
    def __init__(self):
        self.sw_app = None
        
    def connect(self):
        """Attempts to connect to a running instance of SolidWorks."""
        if not WIN32_AVAILABLE:
            return False
        
        try:
            # Initialize COM libraries for this thread (critical for PyQt apps running on separate threads/events)
            pythoncom.CoInitialize()
            
            # Connect to active SolidWorks object
            # Note: "SldWorks.Application" or version-specific CLSIDs like "SldWorks.Application.30"
            raw_sw = win32com.client.GetActiveObject("SldWorks.Application")
            self.sw_app = win32com.client.dynamic.Dispatch(raw_sw)
            return True
        except Exception:
            self.sw_app = None
            return False
            
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

    def get_active_document(self):
        """
        Retrieves details of the currently active document in SolidWorks.
        Returns:
            dict containing {'title': str, 'path': str, 'dirty': bool} or None
        """
        if not self.sw_app:
            connected = self.connect()
            if not connected:
                return None
                
        try:
            # Re-verify connection and retrieve active document
            active_doc = self.sw_app.ActiveDoc
            if active_doc:
                path = self._call_com_method(active_doc, 'GetPathName')
                title = self._call_com_method(active_doc, 'GetTitle')
                
                # GetSaveFlag returns True if there are unsaved changes
                # (SolidWorks API: dirty state)
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
            # Connection might have been lost
            self.sw_app = None
            print(f"COM Error reading ActiveDoc: {e}")
            
        return None

    def lock_active_document_in_git(self, git_service):
        """
        Helper method to lock the file currently open in SolidWorks, if it is in the repository.
        """
        doc_info = self.get_active_document()
        if not doc_info or not doc_info['path']:
            return "No document open in SolidWorks."
            
        doc_path = doc_info['path']
        repo_path = git_service.repo_path
        
        # Check if the document path is within our git repository
        if doc_path.lower().startswith(repo_path.lower()):
            rel_path = os.path.relpath(doc_path, repo_path).replace("\\", "/")
            try:
                git_service.lock_file(rel_path)
                return f"Successfully locked open file: {rel_path}"
            except Exception as e:
                return f"Failed to lock {rel_path}: {e}"
        else:
            return "Active SolidWorks document is not within the current Git repository."
