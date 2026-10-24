# SPDX-License-Identifier: AGPL-3.0-or-later
"""Small CLI helpers shared by the tools."""

import sys

from common.exit_codes import EXIT_OK


def handle_help(usage_lines):
    """Print the usage lines and exit 0 when ``--help`` is passed."""
    if "--help" not in sys.argv[1:]:
        return
    for line in usage_lines:
        print(line)
    sys.exit(EXIT_OK)
