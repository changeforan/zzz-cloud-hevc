#!/bin/bash
# Launch ZZZ Cloud on the HEVC-enabled Wine build.
#
# Required bits, each learned the hard way:
#   USER/USERNAME=steamuser  -> Wine derives the Windows user from the Unix user;
#                               without this the client starts with a blank
#                               profile and sits at the login screen.
#   WINE_D3D_CONFIG=renderer=vulkan -> selects wined3d's Vulkan renderer, which
#                               is where the HEVC decoder lives.
#   WINEDEBUG=-all           -> tracing costs a lot of frames.
#   setsid/nohup/disown      -> survive the launching shell exiting.

PFX=/home/deck/wine-hevc/zzzpfx
EXE="$PFX/drive_c/users/steamuser/AppData/Local/Programs/miHoYo/ZenlessZoneZeroCloud/Zenless Zone Zero Cloud.exe"
LOG=/tmp/zzz-play.log

cd /home/deck/wine-hevc || exit 1

setsid nohup distrobox enter winebuild -- bash -c "
  cd /home/deck/wine-hevc
  export WINEPREFIX=$PFX
  export DISPLAY=:0
  export USER=steamuser USERNAME=steamuser
  export WINE_D3D_CONFIG='renderer=vulkan'
  export WINEDEBUG=-all
  exec ./build64/wine '$EXE'
" > "$LOG" 2>&1 < /dev/null &
disown

echo "launched (log: $LOG)"
