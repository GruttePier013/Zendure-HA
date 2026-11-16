# Switching from Zendure-HA

[← README](../README.md)

Zendure HA-GP013 uses the same domain (`zendure_ha`) as Zendure-HA. It takes over the existing config entry, so your devices, entity IDs, history, energy dashboard and settings stay. The two cannot be installed side by side.

> **Do not use *Remove* in HACS (or *Delete* under *Devices & services*).** Recent HACS / Home Assistant versions also remove the config entry, and with it your settings and entities.

Instead, tell HACS to stop tracking Zendure-HA and download this integration over the existing files.

## With HACS

1. **Make a full backup** (*Settings → System → Backups*).
2. **Add this repository to HACS** (do not download yet): HACS → ⋮ → *Custom repositories* → `https://github.com/GruttePier013/Zendure-HA-GP013`, type *Integration*.
3. **Stop Home Assistant** and mark Zendure-HA as not installed in HACS' own storage. With the *Terminal & SSH* add-on:

   ```sh
   ha core stop
   cd /config/.storage
   cp hacs.repositories hacs.repositories.bak
   jq '(.data[] | select(.full_name == "Zendure/Zendure-HA")) |= (.installed = false | del(.version_installed, .installed_commit))' \
     hacs.repositories > /tmp/hacs.repositories && mv /tmp/hacs.repositories hacs.repositories
   ha core start
   ```

   Home Assistant must be stopped while you edit, otherwise HACS writes its old state back on shutdown. Without `jq`: open `hacs.repositories`, find the entry with `"full_name": "Zendure/Zendure-HA"` and set `"installed": false`.
4. **HACS → Zendure HA-GP013 → Download** (latest release). HACS writes the files over the existing `custom_components/zendure_ha`; nothing is removed.
5. **Restart Home Assistant.** The integration loads with the existing config entry. Review new settings under *Configure*.

Zendure-HA now shows in HACS as not installed. Never download it again: it would overwrite this integration.

## If the config entry was already removed

1. **Restore a backup from before the removal** if you have one: *Settings → System → Backups* → restore only the Home Assistant part. Then follow the steps above.
2. **No backup:** download Zendure HA-GP013 in HACS, restart and add the integration again with the same Zendure token. Home Assistant remembers removed entities for a while and gives them back their old entity IDs, so history, statistics and the energy dashboard continue. Check that no entity got a `_2` suffix.
3. Enter the settings again under *Configure*. If the P1 recording was on, `<config>/zendure_ha/p1_<date>.meta.json` lists all settings you had. Also set the fuse group per device and the Zendure Manager entities (mode, P1 target, manual power) again.
