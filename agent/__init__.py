"""OmSree MS Access <-> CRM sync agent.

A small Windows-side agent that pulls web-origin changes from the OmSree CRM
and applies them to a local MS Access database. Web is the source of truth;
Access is overwritten on the agreed columns (see docs/implementation-plan.md).
"""

__version__ = "0.1.0"
