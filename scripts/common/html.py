# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared HTML snippets for the generated pages.

The explain and Shots pages both keep their ``<details data-keep>`` open state in
``sessionStorage``; the key prefix keeps the two pages apart.
"""


def open_state_script(prefix):
    """The <script> that keeps <details data-keep> open state in sessionStorage under `<prefix>:<id>`; returns the full `<script>...</script>` block."""
    return (
        "<script>\n"
        "(function(){var ds=document.querySelectorAll('details[data-keep]');\n"
        "for(var i=0;i<ds.length;i++){(function(d){var k='" + prefix + ":'+d.id;\n"
        "try{if(sessionStorage.getItem(k)==='open')d.open=true;}catch(e){}\n"
        "d.addEventListener('toggle',function(){try{sessionStorage.setItem(k,d.open?'open':'closed');}catch(e){}});})(ds[i]);}})();\n"
        "</script>\n"
    )
