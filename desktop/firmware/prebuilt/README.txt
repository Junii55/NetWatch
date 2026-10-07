Prebuilt firmware images go here, one folder per chip:

    prebuilt/esp32/{bootloader.bin,partitions.bin,firmware.bin,manifest.json}
    prebuilt/esp32c3/...
    prebuilt/esp32c6/...
    prebuilt/esp32s3/...

They are NOT checked in -- build them with:

    .\firmware\build_prebuilt.ps1

then verify before shipping:

    python app\tests\test_prebuilt.py

Until this folder is populated the app runs fine but the flasher will report
"No bundled firmware for <chip> yet."
