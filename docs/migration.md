# Switching from Zendure-HA

[← README](../README.md)

Zendure HA-GP013 uses the same domain (`zendure_ha`) as Zendure-HA. It takes over the existing config entry, so your devices, entity IDs, history, energy dashboard and settings stay. The two cannot be installed side by side.

The settings live in Home Assistant's `.storage`, not in the integration folder. Replacing the files is therefore safe, as long as you **never delete the integration under *Settings → Devices & services***: that would remove the config entry and the settings with it.

## With HACS

1. **Make a backup** (*Settings → System → Backups*).
2. **HACS → Zendure-HA (shown as *Zendure Home Assistant Integration*) → ⋮ → Remove.** This removes the files and HACS stops tracking Zendure-HA. Do *not* restart yet, and do not touch the integration under *Devices & services*.
3. **HACS → ⋮ → Custom repositories** → add `https://github.com/GruttePier013/Zendure-HA-GP013`, type *Integration*.
4. **Download Zendure HA-GP013** (latest release).
5. **Restart Home Assistant.** The integration loads with the existing config entry. New settings get their defaults; review them under *Configure*.

If you restarted between step 2 and 4 by accident: the integration shows as *not loaded* until the new files are there. Nothing is lost; download and restart.

## Copied by hand, HACS still shows Zendure-HA

If you copied this integration into `custom_components/zendure_ha` yourself, HACS still lists Zendure-HA with its old version and may offer it as an update. Installing that "update" would overwrite this integration. Follow the steps above: *Remove* in HACS deletes the folder you copied, and the download in step 4 puts it back, now tracked by HACS under this repository.

## Back to Zendure-HA

The same steps the other way round. Settings that only exist here are ignored by Zendure-HA.
