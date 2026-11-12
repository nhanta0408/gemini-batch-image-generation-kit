#!/bin/bash
# Double-click in Finder to open the dashboard. It keeps running after this window closes.
cd "$(dirname "$0")" || exit 1
./gbig open
echo
echo "You can close this window now."
