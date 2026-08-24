"""Historic patch — kept so Patch Log entries stay resolvable.

The real work now lives in install.ensure_custom_fields, which runs on every
migrate (see install.py for why a one-shot patch was not repairable).
"""

from zikpro_uk_vat.install import ensure_custom_fields


def execute():
	ensure_custom_fields()


# Back-compat: older code/patches imported this name from here.
create_custom_fields = ensure_custom_fields
