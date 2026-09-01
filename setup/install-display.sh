#!/usr/bin/env bash
# Bring up the Waveshare 3.5" RPi LCD (A) on Raspberry Pi OS Bookworm.
#
# The panel is an ILI9486 on SPI.  The fbtft "waveshare35a" overlay exposes it
# as a plain Linux framebuffer, which is what status-pi draws to -- no X, no
# Wayland, no browser.  This is deliberately less invasive than Waveshare's
# LCD-show script: it only appends to config.txt and never rewrites your
# display stack.
#
# Run over SSH.  Reboot afterwards, then verify with:
#     dmesg | grep -i fb_ili9486
#     ls -l /dev/fb*
set -euo pipefail

BOOT_DIR=/boot/firmware
[ -d "$BOOT_DIR" ] || BOOT_DIR=/boot          # pre-Bookworm layout
CONFIG="$BOOT_DIR/config.txt"
OVERLAY="$BOOT_DIR/overlays/waveshare35a.dtbo"
MARKER="# --- status-pi: waveshare 3.5in (A) ---"

[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
[ -f "$CONFIG" ] || { echo "no $CONFIG -- is this a Raspberry Pi?" >&2; exit 1; }

echo "==> boot config: $CONFIG"
cp -n "$CONFIG" "$CONFIG.status-pi.bak" && echo "    backup: $CONFIG.status-pi.bak"

if [ ! -f "$OVERLAY" ]; then
    echo "==> waveshare35a overlay missing; fetching a Bookworm-compatible build"
    tmp=$(mktemp -d)
    url=https://raw.githubusercontent.com/caliston/waveshare-LCD35-bookworm/master/waveshare35a-overlay.dtb
    if curl -fsSL "$url" -o "$tmp/waveshare35a.dtbo"; then
        install -m 0644 "$tmp/waveshare35a.dtbo" "$OVERLAY"
        echo "    installed $OVERLAY"
    else
        echo "    could not download the overlay; install it manually:" >&2
        echo "    $url -> $OVERLAY" >&2
        exit 1
    fi
    rm -rf "$tmp"
else
    echo "==> overlay already present: $OVERLAY"
fi

# The fbtft driver needs the legacy (non-KMS) graphics path.
echo "==> disabling the KMS driver so fbtft can own the panel"
sed -i 's/^\s*dtoverlay=vc4-kms-v3d/#&/; s/^\s*dtoverlay=vc4-fkms-v3d/#&/; s/^\s*max_framebuffers=/#&/' "$CONFIG"

if grep -qF "$MARKER" "$CONFIG"; then
    echo "==> status-pi block already in config.txt, leaving it alone"
else
    echo "==> appending display settings"
    cat >> "$CONFIG" <<EOF

$MARKER
dtparam=spi=on
dtparam=i2c_arm=on
disable_fw_kms_setup=1
dtoverlay=waveshare35a:rotate=90
hdmi_force_hotplug=1
hdmi_group=2
hdmi_mode=87
hdmi_cvt 480 320 60 6 0 0 0
hdmi_drive=2
# --- end status-pi ---
EOF
fi

echo
echo "Done.  Reboot, then check:"
echo "    dmesg | grep -i fb_ili9486     # expect: graphics fb1: fb_ili9486 frame buffer"
echo "    cat /sys/class/graphics/fb1/virtual_size   # expect: 480,320"
echo
echo "If the panel is upside down once mounted, change rotate=90 to rotate=270"
echo "in $CONFIG and reboot."
