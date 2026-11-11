"""What starts every minute loads only what it uses (11 Nov).

Profiled on the server, the once-a-minute commands pass (the owner's Telegram kill switch and the watchdog; "no
snapshot, no Claude") took 3.9 s of processor, 3.3 of them loading the Anthropic SDK it never calls: `operator.run`
imported the Claude turn, and `operator.tools`, which every stage imports for its note writer, imported the SDK's tool
decorator at the top. The book and the measure stage (every 5 minutes) paid the same. The SDK is loaded by the code
that talks to Claude, when it does.
"""

import subprocess
import sys

import pytest

LIGHT = (
    "from snipetech.operator.run import commands_pass, operate",
    "import snipetech.book.run",
    "import snipetech.measure.run",
    "import snipetech.strategist.run",
    "import snipetech.operator.commands, snipetech.operator.autotest, snipetech.operator.live_test",
    "import snipetech.cli",
)


@pytest.mark.parametrize("statement", LIGHT)
def test_a_stage_that_calls_no_model_does_not_load_the_sdk(statement):
    code = f"import sys\n{statement}\nheavy = sorted(m for m in sys.modules if m.split('.')[0] in ('anthropic', 'pydantic'))\nprint(len(heavy), heavy[:3])"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    assert out.startswith("0 "), f"{statement}: loaded {out}"


def test_the_code_that_talks_to_claude_still_finds_the_sdk():
    code = (
        "import sys\n"
        "from snipetech.operator import tools, turn\n"
        "from snipetech.strategist import turn as sturn\n"
        "assert 'anthropic' in sys.modules\n"
        "assert callable(turn.run_turn) and callable(sturn.run_strategist_turn)\n"
        "print('ok')"
    )
    assert (
        subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip() == "ok"
    )
