"""P3-6 / B101 + Cloud-DocPerm: apply the VAT role + DocPerm baseline on Frappe Cloud.

ensure_vat_roles_and_perms() is wired to after_migrate, but (1) after_migrate hooks
don't run on Frappe Cloud (B101) and (2) FC's migrate deliberately SKIPS re-applying
tabDocPerm for existing doctypes (the Cloud DocPerm rule). Result on a cloud site: the
_VAT_DOCPERMS baseline — which grants System Manager read on UK MTD VAT Return — never
lands, so `frappe.desk.query_report.get_script` 403s and the standard VAT Return report
is dead for a System Manager. A patch runs once per site on FC and fixes it. Idempotent
(the helper reconciles from a full-0 baseline), so it is safe to run anywhere.
"""

from zikpro_uk_vat.install import ensure_vat_roles_and_perms


def execute():
	ensure_vat_roles_and_perms()
