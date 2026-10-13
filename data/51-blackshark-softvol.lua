-- Razer BlackShark V2 Pro: ignore the dongle's hardware volume control and
-- let PipeWire scale the audio in software (as it already does on the
-- digital/IEC958 profile). Remove this file to go back to hardware volume.
table.insert(alsa_monitor.rules, {
  matches = { { { "device.name", "matches", "alsa_card.usb-1532_Razer_BlackShark_V2_Pro*" } } },
  apply_properties = { ["api.alsa.soft-mixer"] = true },
})
