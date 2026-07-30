"""KASBench Controller service layer.

Service functions contain the core logic extracted from CLI commands.
They accept typed parameters, raise KasbenchError on failure, and do not
call sys.exit() or depend on Click context.
"""
