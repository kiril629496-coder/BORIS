#!/usr/bin/env bash
cd /root/BORIS/frontend || exit 1
rm -f .boris-builds/acquisition-current-build.rc
npm run build > .boris-builds/acquisition-current-build.log 2>&1
printf '%s\n' "$?" > .boris-builds/acquisition-current-build.rc
