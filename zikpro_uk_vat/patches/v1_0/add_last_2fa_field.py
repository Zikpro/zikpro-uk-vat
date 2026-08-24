# your_app/patches/v1_0/add_last_2fa_field.py
import frappe
from frappe import _

def before_migrate():
    """Optional: Can add pre-migration checks here"""
    pass

def after_migrate():
    """Main patch execution"""
    if not frappe.db.exists("Custom Field", {"dt": "User", "fieldname": "last_2fa_login"}):
        try:
            doc = frappe.get_doc({
                "doctype": "Custom Field",
                "dt": "User",
                "module": "Core",
                "fieldname": "last_2fa_login",
                "label": _("Last 2FA Login"),
                "fieldtype": "Datetime",
                "insert_after": "last_login",
                "read_only": 1,
                "no_copy": 1,
                "allow_on_submit": 1,
                "owner": "Administrator"
            }).insert(ignore_permissions=True)
            
            frappe.db.commit()
            frappe.clear_cache(doctype="User")
            
        except Exception as e:
            frappe.log_error(_("Failed to add 2FA field"), str(e))
            raise

def execute():
    """Standard patch entry point"""
    after_migrate()