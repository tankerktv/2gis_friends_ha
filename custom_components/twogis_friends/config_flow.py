"""Настройка интеграции через UI."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_TOKEN
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_VIEWPORT_RADIUS,
    DEFAULT_VIEWPORT_RADIUS,
    DOMAIN,
)
from .zond import ZondAuthError, async_validate_token

_LOGGER = logging.getLogger(__name__)

TOKEN_SCHEMA = vol.Schema({
    vol.Required(CONF_TOKEN): TextSelector(
        TextSelectorConfig(type=TextSelectorType.PASSWORD)
    ),
})


class TwoGisConfigFlow(ConfigFlow, domain=DOMAIN):
    """Просим токен, проверяем его через api.auth.2gis.com."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            profile, error = await self._async_check(token)

            if error:
                errors["base"] = error
            else:
                await self.async_set_unique_id(profile["public_user_id"])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=profile.get("display_name") or "2GIS",
                    data={CONF_TOKEN: token},
                )

        return self.async_show_form(
            step_id="user", data_schema=TOKEN_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            profile, error = await self._async_check(token)

            if error:
                errors["base"] = error
            else:
                await self.async_set_unique_id(profile["public_user_id"])
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_TOKEN: token}
                )

        return self.async_show_form(
            step_id="reauth_confirm", data_schema=TOKEN_SCHEMA, errors=errors
        )

    async def _async_check(self, token: str) -> tuple[dict[str, Any], str | None]:
        """Возвращает (профиль, код_ошибки). Профиль пуст, если ошибка."""
        session = async_get_clientsession(self.hass)
        try:
            profile = await async_validate_token(session, token)
        except ZondAuthError:
            return {}, "invalid_auth"
        except (aiohttp.ClientError, TimeoutError):
            return {}, "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Неожиданная ошибка при проверке токена")
            return {}, "unknown"

        if not profile.get("public_user_id"):
            return {}, "invalid_auth"
        return profile, None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return TwoGisOptionsFlow()


class TwoGisOptionsFlow(OptionsFlow):
    """Радиус области, по которой 2ГИС отдаёт апдейты."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options.get(
            CONF_VIEWPORT_RADIUS, DEFAULT_VIEWPORT_RADIUS
        )
        schema = vol.Schema({
            vol.Required(CONF_VIEWPORT_RADIUS, default=current): NumberSelector(
                NumberSelectorConfig(
                    min=0.1, max=20.0, step=0.1, mode=NumberSelectorMode.BOX
                )
            ),
        })
        return self.async_show_form(step_id="init", data_schema=schema)
