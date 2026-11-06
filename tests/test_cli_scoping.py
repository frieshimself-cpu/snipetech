"""A local `import X` inside the CLI's big dispatch functions shadows the module-level X for the whole function and
breaks every other branch that uses it (the operator was down for an hour this way on 6 Nov)."""

from snipetech import cli

SHARED = ("Service", "time", "json", "asyncio", "Path", "os", "sys", "Config")


def test_dispatch_functions_do_not_shadow_module_names():
    for fn in (cli.main, cli._run):
        bad = sorted(n for n in SHARED if n in fn.__code__.co_varnames)
        assert bad == [], f"{fn.__name__} rebinds module-level names locally: {bad}"
