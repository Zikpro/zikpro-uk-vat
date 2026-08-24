"""Re-reconcile the VAT DocPerm baseline after adding the `report`+`export` ptypes to
UK MTD VAT Return.

ensure_vat_docperms already ran once (its Patch Log row exists), so it won't re-apply
the newly-added grants. ensure_vat_roles_and_perms reconciles every managed row to the
current _VAT_DOCPERMS target, so running it again from a NEW patch lands the report
ptype on Frappe Cloud (where after_migrate doesn't run). Without `report`, the standard
'VAT Return' report 403s for every role. Idempotent.
"""

from zikpro_uk_vat.install import ensure_vat_roles_and_perms


def execute():
	ensure_vat_roles_and_perms()
