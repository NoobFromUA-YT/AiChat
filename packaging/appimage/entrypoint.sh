#!/bin/bash
# Start AI Chat with the Python that is bundled inside the AppImage.
{{ python-executable }} -m aichat_desktop "$@"
