"""Abaqus/CAE menu entry for PRIME UEL Studio."""

import os

from abaqusGui import getAFXApp

# Importing the GUI module registers the viewport-picking procedures alongside
# the legacy desktop-launch menu entry.
import prime_uel_plugin_gui  # noqa: F401


toolset = getAFXApp().getAFXMainWindow().getPluginToolset()
directory = os.path.dirname(os.path.abspath(__file__))
toolset.registerKernelMenuButton(
    moduleName="prime_uel_plugin_kernel",
    functionName="open_studio()",
    buttonText="PRIME UEL Studio...",
    version="0.8.0+public",
    author="PRIME UEL Studio",
    description=(
        "Open PRIME UEL Studio from Abaqus/CAE.\n\n"
        "The current model, parts, instances, jobs and viewport are written to a "
        "temporary context file before the desktop workflow opens."
    ),
    helpUrl=os.path.join(directory, "prime_uel_plugin_help.html"),
)
