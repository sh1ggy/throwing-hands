# RPS-Bot XIAO RP2040 boot (design doc D3-B).
# Second USB serial channel: usb_cdc.data carries the §9.2 JSON-lines protocol,
# the console/REPL stays free for debugging. Changes here need a hard reset.
import usb_cdc

usb_cdc.enable(console=True, data=True)
