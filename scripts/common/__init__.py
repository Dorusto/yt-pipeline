# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared code for yt-pipeline tools.

Only infrastructure that more than one tool needs lives here: notifications,
encoder selection, schema validation, error types. Tools never import each
other; they import from ``common``.
"""