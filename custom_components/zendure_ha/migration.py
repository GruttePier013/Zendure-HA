"""Migration helpers for Zendure integration."""

import logging
import re
import shutil
from pathlib import Path

from homeassistant.components.persistent_notification import async_create
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import restore_state as rs

from .const import DOMAIN
from .device import ZendureBattery
from .entity import get_registry_device, snakecase

_LOGGER = logging.getLogger(__name__)

MAX_FILE_SIZE = 5 * 1024 * 1024  # larger files are not touched
BACKUP_SUFFIX = ".zendure-backup"


def replace_entity_ids(content: str, changes: list[tuple[str, str]]) -> str:
    """Replace entity ids as whole ids: sensor.x_power must not change sensor.x_power_limit."""
    for old_id, new_id in changes:
        content = re.sub(rf"(?<![A-Za-z0-9_.]){re.escape(old_id)}(?![A-Za-z0-9_])", new_id, content)
    return content


class Migration:
    """Handles device/entity rename migrations."""

    repairs = {"i_o_t_state": "iotstate", "o_t_a_state": "otastate", "l_c_n_state": "lcnstate", "local_a_p_i_enable": "local_apienable", "is_error": ""}

    @staticmethod
    def check_device(hass: HomeAssistant, device_id: str, name: str, model: str, sn: str) -> None:
        """Track cloud-side device renames via name_by_user for the next migration."""
        device_registry = dr.async_get(hass)

        fallback = f"{model.replace(' ', '').replace('SolarFlow', 'Sf')} {sn[-3:] if sn is not None else ''}".strip()
        unique = "".join(name.split())
        identifier = device_id or name
        if not identifier:
            return

        existing = get_registry_device(hass, identifier)
        if existing is None:
            for ident in [name, name.lower(), unique, fallback, fallback.lower()]:
                existing = get_registry_device(hass, ident)
                if existing is not None:
                    break

        if not existing:
            return

        if name != existing.name and existing.name_by_user is None:
            _LOGGER.info("Device '%s' renamed to '%s' in cloud, storing for next migration", existing.name, name)
            device_registry.async_update_device(existing.id, name_by_user=name)

    @staticmethod
    def _update_files(hass: HomeAssistant, changes: list[tuple[str, str]]) -> bool:
        """Replace old entity IDs with new ones in storage and config files."""
        file_modified = False

        def update_file(path: Path) -> None:
            nonlocal file_modified
            try:
                if path.name.endswith(BACKUP_SUFFIX) or path.stat().st_size > MAX_FILE_SIZE:
                    return
                content = path.read_text(encoding="utf-8")
                updated = replace_entity_ids(content, changes)
                if updated != content:
                    shutil.copy2(path, path.with_name(path.name + BACKUP_SUFFIX))  # keep the original, this is a one-time migration
                    path.write_text(updated, encoding="utf-8")
                    file_modified = True
            except Exception as e:
                _LOGGER.error("Error migrating file %s: %s", path, e)

        storage_dir = Path(hass.config.path(".storage"))
        for path in storage_dir.iterdir():
            if any(path.name.startswith(f) for f in ["core.automation", "lovelace", "energy"]):
                update_file(path)

        config_path = Path(hass.config.config_dir)
        for path in config_path.rglob("*"):
            if path.is_dir():
                continue
            if any(part.startswith(".") for part in path.relative_to(config_path).parts):
                continue
            if path.suffix in (".yaml", ".json"):
                update_file(path)

        return file_modified

    @staticmethod
    async def async_migrate(hass: HomeAssistant, entryid: str) -> None:
        """One-time migration run via async_migrate_entry: fix device identifiers and entity IDs."""
        device_registry = dr.async_get(hass)
        entity_registry = er.async_get(hass)
        data = rs.async_get(hass)
        changes: list[tuple[str, str]] = []

        devices = dr.async_entries_for_config_entry(device_registry, entryid)
        for device in devices:
            if not any(ident[0] == DOMAIN for ident in device.identifiers):
                continue

            try:
                if device.serial_number:
                    new_identifiers = set(device.identifiers) | {(DOMAIN, device.serial_number)}
                    if new_identifiers != set(device.identifiers):
                        device_registry.async_update_device(device.id, new_identifiers=new_identifiers)

                # Get the best possible name for the device: prefer name_by_user, then name, then try to infer from entities
                entities = er.async_entries_for_device(entity_registry, device.id, True)
                if not (name := device.name_by_user or device.name) or "_" in name:
                    continue

                if device.via_device_id:
                    # is a battery device, change name to the new format
                    name, _, _ = ZendureBattery.get_battery_type(device.serial_number)
                if name != device.name:
                    _LOGGER.info("Promoting device name '%s' -> '%s'", device.name, name)
                    device_registry.async_update_device(device.id, name=name, name_by_user=None)

                for entity in entities:
                    try:
                        # rename only entities which belong to the zendure_ha domain
                        if entity.platform == DOMAIN:
                            if entity.translation_key is None:
                                entity_registry.async_remove(entity.entity_id)
                                _LOGGER.debug("Removed orphan entity %s", entity.entity_id)
                                continue

                            uniqueid = snakecase(v) if (v := Migration.repairs.get(entity.translation_key)) is not None else snakecase(entity.translation_key)
                            if uniqueid == "":
                                entity_registry.async_remove(entity.entity_id)
                                continue

                            if uniqueid.startswith("aggr") and uniqueid.endswith("total"):
                                uniqueid = uniqueid.replace("_total", "")
                            unique_id = snakecase(f"{name.lower()}_{uniqueid}")
                            entityid = f"{entity.domain}.{unique_id}"

                            if entity.entity_id != entityid or entity.unique_id != unique_id or entity.translation_key != uniqueid:
                                if entity.entity_id != entityid:
                                    entity_registry.async_remove(entityid)
                                if (rstate := data.last_states.pop(entity.entity_id, None)) is not None:
                                    data.last_states[entityid] = rstate
                                entity_registry.async_update_entity(
                                    entity.entity_id,
                                    new_unique_id=unique_id,
                                    new_entity_id=entityid,
                                    translation_key=uniqueid,
                                )
                                _LOGGER.debug("Migrated entity %s -> %s", entity.entity_id, entityid)
                                changes.append((entity.entity_id, entityid))
                    except Exception as e:
                        _LOGGER.error("Failed to migrate entity %s: %s", entity.entity_id, e)
            except Exception as e:
                _LOGGER.error("Failed to migrate device %s: %s", device.name, e)

        # update template config entries
        modified = 0
        for entry in hass.config_entries.async_entries():
            new_data = dict(entry.data or {})
            new_options = dict(entry.options or {})
            if len(new_data) == 0 and len(new_options) == 0:
                continue

            def change_id(data: dict, oid: str, nid: str) -> bool:
                changed = False
                for key, value in data.items():
                    if isinstance(value, dict):
                        changed |= change_id(value, oid, nid)
                    elif isinstance(value, list):
                        for i, item in enumerate(value):
                            if isinstance(item, str) and (new_item := replace_entity_ids(item, [(oid, nid)])) != item:
                                value[i] = new_item
                                changed = True
                    elif isinstance(value, str) and (new_value := replace_entity_ids(value, [(oid, nid)])) != value:
                        data[key] = new_value
                        changed = True
                return changed

            changed = False
            for oid, nid in changes:
                changed |= change_id(new_data, oid, nid)
                changed |= change_id(new_options, oid, nid)

            if changed:
                hass.config_entries.async_update_entry(entry, data=new_data, options=new_options)
                hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))
                modified += 1
        _LOGGER.info("Modified %d template entities", modified)

        if changes and await hass.async_add_executor_job(Migration._update_files, hass, changes):
            await rs.RestoreStateData.async_save_persistent_states(hass)
            async_create(
                hass,
                f"Zendure migration updated {len(changes)} entities. Please restart Home Assistant to ensure all automations and dashboards use the new entity IDs.",
                title="Zendure Migration",
                notification_id="zendure_migration",
            )
        _LOGGER.info("Zendure async_migrate complete: %d entity changes", len(changes))
