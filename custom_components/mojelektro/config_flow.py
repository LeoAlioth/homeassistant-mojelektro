"""Config flow for Moj Elektro."""

import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_DECIMAL, CONF_IMPORT_HISTORY, CONF_METER_ID, CONF_TOKEN, DOMAIN
from .moj_elektro_api import (
    MojElektroApi,
    MojElektroAuthError,
    MojElektroError,
    MojElektroRequestError,
)

_LOGGER = logging.getLogger(__name__)


class MojeElektroFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Moj Elektro."""

    VERSION = 1

    async def _async_validate(self, token: str, meter_id: str, decimal) -> str | None:
        """Validate credentials and return an error key on failure."""
        session = async_get_clientsession(self.hass)
        api = MojElektroApi(token, meter_id, decimal, session)

        try:
            await api.validate_token()
        except (MojElektroAuthError, MojElektroRequestError):
            return "invalid_auth"
        except MojElektroError as err:
            _LOGGER.debug("Moj Elektro connection validation failed: %s", err)
            return "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error while validating Moj Elektro")
            return "unknown"

        return None

    async def async_step_user(self, user_input=None):
        """Handle the initial configuration step."""
        errors = {}

        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            meter_id = user_input[CONF_METER_ID].strip()
            decimal = user_input.get(CONF_DECIMAL)

            error = await self._async_validate(token, meter_id, decimal)
            if error is not None:
                errors["base"] = error
            else:
                await self.async_set_unique_id(meter_id)
                self._abort_if_unique_id_configured()

                data = dict(user_input)
                data[CONF_TOKEN] = token
                data[CONF_METER_ID] = meter_id

                return self.async_create_entry(
                    title=f"Moj Elektro {meter_id}",
                    data=data,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TOKEN): str,
                    vol.Required(CONF_METER_ID): str,
                    vol.Optional(CONF_DECIMAL): vol.All(
                        vol.Coerce(int), vol.Range(min=0, max=10)
                    ),
                    vol.Optional(CONF_IMPORT_HISTORY, default=True): bool,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data):
        """Start reauthentication after the API rejects the stored token."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Request and validate a replacement API token."""
        errors = {}
        reauth_entry = self._get_reauth_entry()
        meter_id = reauth_entry.data[CONF_METER_ID]
        decimal = reauth_entry.data.get(CONF_DECIMAL)

        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            error = await self._async_validate(token, meter_id, decimal)

            if error is not None:
                errors["base"] = error
            else:
                await self.async_set_unique_id(meter_id)
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={CONF_TOKEN: token},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}),
            errors=errors,
            description_placeholders={"meter_id": meter_id},
        )
