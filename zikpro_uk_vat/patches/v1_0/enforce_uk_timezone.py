"""P1-6 / B101: guarantee the site timezone is Europe/London on Frappe Cloud.

enforce_uk_timezone() is already wired to after_migrate, but broadcast B101 is
that after_migrate hooks DO NOT run on Frappe Cloud deploys — only patches do.
So a fresh cloud tenant could keep Frappe's Asia/Kolkata default and mis-date UK
transactions across VAT periods (see install.enforce_uk_timezone for why that is
a correctness bug, not a cosmetic one). A patch runs once per site on FC, so this
closes the gap for every new tenant. Idempotent — a no-op if already correct.
"""

from zikpro_uk_vat.install import enforce_uk_timezone


def execute():
	enforce_uk_timezone()
