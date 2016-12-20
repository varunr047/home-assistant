"""
Support for Broadlink RM devices.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/switch.broadlink/
"""
from datetime import timedelta
from base64 import b64encode, b64decode
import asyncio
import binascii
import logging
import socket
import voluptuous as vol

import homeassistant.loader as loader
from homeassistant.util.dt import utcnow
from homeassistant.components.switch import (SwitchDevice, PLATFORM_SCHEMA)
from homeassistant.const import (CONF_FRIENDLY_NAME, CONF_SWITCHES,
                                 CONF_COMMAND_OFF, CONF_COMMAND_ON,
                                 CONF_TIMEOUT, CONF_HOST, CONF_MAC,
                                 CONF_TYPE)
import homeassistant.helpers.config_validation as cv

REQUIREMENTS = ['broadlink==0.2']

_LOGGER = logging.getLogger(__name__)

DOMAIN = "broadlink"
DEFAULT_NAME = 'Broadlink switch'
DEFAULT_TIMEOUT = 10
SERVICE_LEARN = "learn_command"

SENSOR_TYPES = ["rm", "sp1", "sp2"]

SWITCH_SCHEMA = vol.Schema({
    vol.Optional(CONF_COMMAND_OFF, default=None): cv.string,
    vol.Optional(CONF_COMMAND_ON, default=None): cv.string,
    vol.Optional(CONF_FRIENDLY_NAME, default=DEFAULT_NAME): cv.string,
})

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_SWITCHES): vol.Schema({cv.slug: SWITCH_SCHEMA}),
    vol.Required(CONF_HOST): cv.string,
    vol.Required(CONF_MAC): cv.string,
    vol.Optional(CONF_TYPE, default=SENSOR_TYPES[0]): vol.In(SENSOR_TYPES),
    vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): cv.positive_int
})


def setup_platform(hass, config, add_devices, discovery_info=None):
    """Setup Broadlink switches."""
    import broadlink
    devices = config.get(CONF_SWITCHES, {})
    switches = []
    ip_addr = config.get(CONF_HOST)
    mac_addr = binascii.unhexlify(
        config.get(CONF_MAC).encode().replace(b':', b''))
    sensor_type = config.get(CONF_TYPE)

    persistent_notification = loader.get_component('persistent_notification')

    @asyncio.coroutine
    def _learn_command(call):
        try:
            yield from hass.loop.run_in_executor(None, broadlink_device.auth)
        except socket.timeout:
            _LOGGER.error("Failed to connect to device.")
            return
        yield from hass.loop.run_in_executor(None,
                                             broadlink_device.enter_learning)

        _LOGGER.info("Press the key you want HASS to learn")
        start_time = utcnow()
        while (utcnow() - start_time) < timedelta(seconds=20):
            packet = yield from hass.loop.run_in_executor(None,
                                                          broadlink_device.
                                                          check_data)
            if packet:
                log_msg = 'Recieved packet is: {}'.\
                          format(b64encode(packet).decode('utf8'))
                _LOGGER.info(log_msg)
                persistent_notification.async_create(hass, log_msg,
                                                     title='Broadlink switch')
                return
            yield from asyncio.sleep(1, loop=hass.loop)
        _LOGGER.error('Did not received any signal.')
        persistent_notification.async_create(hass,
                                             "Did not received any signal",
                                             title='Broadlink switch')

    if sensor_type == "rm":
        broadlink_device = broadlink.rm((ip_addr, 80), mac_addr)
        switch = BroadlinkRMSwitch
        hass.services.register(DOMAIN, SERVICE_LEARN + '_' + ip_addr,
                               _learn_command)
    elif sensor_type == "sp1":
        broadlink_device = broadlink.sp1((ip_addr, 80), mac_addr)
        switch = BroadlinkSP1Switch
    elif sensor_type == "sp2":
        broadlink_device = broadlink.sp2((ip_addr, 80), mac_addr)
        switch = BroadlinkSP2Switch

    broadlink_device.timeout = config.get(CONF_TIMEOUT)
    try:
        broadlink_device.auth()
    except socket.timeout:
        _LOGGER.error("Failed to connect to device.")

    for object_id, device_config in devices.items():
        switches.append(
            switch(
                device_config.get(CONF_FRIENDLY_NAME, object_id),
                device_config.get(CONF_COMMAND_ON),
                device_config.get(CONF_COMMAND_OFF),
                broadlink_device
            )
        )

    add_devices(switches)


class BroadlinkRMSwitch(SwitchDevice):
    """Representation of an Broadlink switch."""

    def __init__(self, friendly_name, command_on, command_off, device):
        """Initialize the switch."""
        self._name = friendly_name
        self._state = False
        self._command_on = b64decode(command_on) if command_on else None
        self._command_off = b64decode(command_off) if command_off else None
        self._device = device

    @property
    def name(self):
        """Return the name of the switch."""
        return self._name

    @property
    def assumed_state(self):
        """Return true if unable to access real state of entity."""
        return True

    @property
    def should_poll(self):
        """No polling needed."""
        return False

    @property
    def is_on(self):
        """Return true if device is on."""
        return self._state

    def turn_on(self, **kwargs):
        """Turn the device on."""
        if self._sendpacket(self._command_on):
            self._state = True

    def turn_off(self, **kwargs):
        """Turn the device off."""
        if self._sendpacket(self._command_off):
            self._state = False

    def _sendpacket(self, packet, retry=2):
        """Send packet to device."""
        if packet is None:
            _LOGGER.debug("Empty packet.")
            return True
        try:
            self._device.send_data(packet)
        except socket.timeout as error:
            if retry < 1:
                _LOGGER.error(error)
                return False
            try:
                self._device.auth()
            except socket.timeout:
                pass
            return self._sendpacket(packet, max(0, retry-1))
        return True


class BroadlinkSP1Switch(BroadlinkRMSwitch):
    """Representation of an Broadlink switch."""

    def __init__(self, friendly_name, command_on, command_off, device):
        """Initialize the switch."""
        super().__init__(friendly_name, command_on, command_off, device)
        self._command_on = 1
        self._command_off = 0

    def _sendpacket(self, packet, retry=2):
        """Send packet to device."""
        try:
            self._device.set_power(packet)
        except socket.timeout as error:
            if retry < 1:
                _LOGGER.error(error)
                return False
            try:
                self._device.auth()
            except socket.timeout:
                pass
            return self._sendpacket(packet, max(0, retry-1))
        return True


class BroadlinkSP2Switch(BroadlinkSP1Switch):
    """Representation of an Broadlink switch."""

    def __init__(self, friendly_name, command_on, command_off, device):
        """Initialize the switch."""
        super().__init__(friendly_name, command_on, command_off, device)

    @property
    def assumed_state(self):
        """Return true if unable to access real state of entity."""
        return False

    @property
    def should_poll(self):
        """Polling needed."""
        return True

    def update(self):
        """Synchronize state with switch."""
        self._update()

    def _update(self, retry=2):
        try:
            state = self._device.check_power()
        except socket.timeout as error:
            if retry < 1:
                _LOGGER.error(error)
                return
            try:
                self._device.auth()
            except socket.timeout:
                pass
            return self._update(max(0, retry-1))
        if state is None and retry > 0:
            return self._update(max(0, retry-1))
        self._state = state
