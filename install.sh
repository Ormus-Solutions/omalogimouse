#!/usr/bin/env bash
set -euo pipefail

# Optional. omarchy plugin add does not run this script.
# Installs Solaar for logitech_receiver and hidraw udev rules.
# Does not start the Solaar GUI or daemon.

echo "Omalogimouse"
echo "Installs Solaar (HID++ library + Logitech hidraw udev rules)."
echo "Does not start solaar --window=hide."
echo

OMARCHY=/usr/bin/omarchy
if [[ ! -x $OMARCHY ]] && command -v omarchy >/dev/null 2>&1; then
  OMARCHY=$(command -v omarchy)
fi

if [[ ! -x $OMARCHY ]]; then
  echo "omarchy CLI not found. Install the solaar package with your distro tools," >&2
  echo "then reconnect the mouse so /dev/hidraw* is user-accessible." >&2
  exit 1
fi

"$OMARCHY" pkg add solaar

echo
echo "Reconnect the mouse, or reload udev hidraw rules, if the bar is still offline."
echo "Mask Solaar GUI autostart (conflicts with this plugin):"
echo "  mkdir -p ~/.config/autostart"
echo "  printf '%s\\n' '[Desktop Entry]' 'Hidden=true' > ~/.config/autostart/solaar.desktop"
