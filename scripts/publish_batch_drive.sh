#!/bin/sh
set -eu

CONFIG=/home/ubuntu/.config/rclone/rclone.conf
ROOT=1upVRLwqDo1agfYwXekrQP2-2PeKGzvN4
BASE=/tmp/lesson-batch-preview-20260809

for spec in \
  '01|01. 20260308|01-20260308' \
  '02|02. 20260315|02-20260315' \
  '03|03. 20260322|03-20260322' \
  '04|04. 20260329|04-20260329' \
  '05|05. 20260405|05-20260405' \
  '06|06. 20260412|06-20260412' \
  '07|07. 20260419|07-20260419' \
  '08|08. 20260426|08-20260426' \
  '09|09. 20260510|09-20260510' \
  '10|10. 20260524|10-20260524' \
  '11|11. 20260531|11-20260531' \
  '12|12. 20260607|12-20260607' \
  '13|13. 20260614|13-20260614' \
  '14|14. 20260621|14-20260621' \
  '15|15. 20260628|15-20260628' \
  '16|16. 20260705|20260705'
do
  idx=${spec%%|*}
  rest=${spec#*|}
  folder=${rest%%|*}
  stem=${rest#*|}
  for ext in srt txt json
  do
    if [ "$ext" = txt ]; then
      src="$BASE/$idx/transcript-preview-dedup.txt"
    else
      src="$BASE/$idx/subtitles-preview-dedup.$ext"
    fi
    dst="gdrive:$folder/$stem.$ext"
    test -s "$src"
    echo "PUBLISH $idx $stem.$ext"
    sudo rclone --config "$CONFIG" --drive-root-folder-id="$ROOT" copyto "$src" "$dst"
  done
done
