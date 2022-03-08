# SPDX-FileCopyrightText: 2022 Daniel Griswold
#
# SPDX-License-Identifier: MIT

"""

CircuitPython program to monitor the garage door state and garage environment.

Secrets should be set in secrets.py and configuration in config.py

"""

import board  # pylint: disable=wrong-import-order
import time
import ssl
import json
import asyncio
import digitalio
import microcontroller
import wifi
import socketpool
import adafruit_requests
import rtc

import adafruit_ahtx0
import adafruit_bh1750
import adafruit_minimqtt.adafruit_minimqtt as MQTT
import adafruit_hcsr04

from secrets import secrets  # pylint: disable=wrong-import-order
from config import config  # pylint: disable=wrong-import-order


class Garage_state:
    """Class to store the state of the garage and door."""

    def __init__(self):
        self.door_error = False
        self.motion = None
        self.door_status = None
        self.motion_timeout = 0
        self.pause_door_check = False

    @property
    def json(self):
        """outputs the data in a json format"""
        _data = {}
        _data["door_status"] = self.door_status
        _data["door_error"] = self.door_error
        _data["motion"] = self.motion
        return json.dumps(_data)


async def activate_opener():
    """Trigger the relay connected to the garage opener"""
    garage_state.pause_door_check = True
    relay.value = True
    await asyncio.sleep(2)
    relay.value = False
    await asyncio.sleep(config["DOOR_CLOSE_DELAY"])
    garage_state.pause_door_check = False


async def get_motion_state():
    """Monitors for motion to prevent door from closing with people inside."""
    pir = digitalio.DigitalInOut(board.IO14)
    pir.direction = digitalio.Direction.INPUT

    while True:
        if pir.value is True:
            garage_state.motion_timeout = time.monotonic() + config["MOTION_TIMEOUT"]
            garage_state.motion = True

        try:
            if time.monotonic() > garage_state.motion_timeout:
                garage_state.motion = False
        except KeyError:
            pass

        await asyncio.sleep(0)


async def get_door_state():
    """Uses the distance sensor to determine if door is open or closed."""
    while True:
        await asyncio.sleep(0)
        if garage_state.pause_door_check:
            continue
        _previous_state = garage_state.door_status
        if hcsr04.distance < config["DOOR_DISTANCE"]:
            garage_state.door_status = "Open"
        else:
            garage_state.door_status = "Closed"
        if _previous_state != garage_state.door_status:
            print("Updating door state")
            publish_to_mqtt(MQTT_GARAGE_STATE, garage_state.json)


async def get_sensor_data():
    """Creates dictionary of sensor values."""
    aht20 = adafruit_ahtx0.AHTx0(board.I2C())
    bh1750 = adafruit_bh1750.BH1750(board.I2C())

    _data = {}
    while True:
        _data["temperature"] = aht20.temperature
        _data["humidity"] = aht20.relative_humidity
        _data["light"] = bh1750.lux
        print("Sensors: {}".format(_data))
        publish_to_mqtt(MQTT_ENVIRONMENT, json.dumps(_data))
        await asyncio.sleep(60)


async def get_system_data():
    """Creates dictionary of system information"""
    _data = {}
    while True:
        _data["reset_reason"] = str(microcontroller.cpu.reset_reason)[28:]
        _data["time"] = time.monotonic()
        _data["ip_address"] = wifi.radio.ipv4_address
        _data["board_id"] = board.board_id
        print("System: {}".format(_data))
        publish_to_mqtt(MQTT_SYSTEM, json.dumps(_data))
        await asyncio.sleep(60)


async def mqtt_client_loop():
    """wrapping the mqtt_client.loop() function in error handling."""
    while True:
        try:
            mqtt_client.is_connected()
        except MQTT.MMQTTException:
            """MQTT is not connected.  Attempt reconnect and publish."""
            try:
                mqtt_client.reconnect()
            except (OSError, MQTT.MMQTTException):
                """Couldn't reconnect and publish."""
                microcontroller.reset()
        except OSError:
            """wifi is not connected."""
            microcontroller.reset()
        finally:
            try:
                mqtt_client.loop()
            except (OSError, MQTT.MMQTTException):
                microcontroller.reset()
        await asyncio.sleep(1)


async def mqtt_publish_loop():
    """Calls publish to mqtt at an interval for garage state."""
    while True:
        print("Garage State: {}".format(garage_state.json))
        publish_to_mqtt(MQTT_GARAGE_STATE, garage_state.json)
        await asyncio.sleep(60)


def message(
    client, topic, message
):  # pylint: disable=unused-argument, redefined-outer-name
    """Callback for MQTT message in subscribed topic."""
    if topic == MQTT_DOOR_REQUEST:
        if message == "True":
            publish_to_mqtt(MQTT_DOOR_REQUEST, "False")
            asyncio.gather(asyncio.create_task(activate_opener()))


def publish_to_mqtt(topic, message):  # pylint: disable=redefined-outer-name
    """Publish data to mqtt"""
    try:
        mqtt_client.is_connected()
    except MQTT.MMQTTException:
        """MQTT is not connected.  Attempt reconnect and publish."""
        try:
            mqtt_client.reconnect()
        except (OSError, MQTT.MMQTTException):
            """Couldn't reconnect and publish."""
            microcontroller.reset()
    except OSError:
        """wifi is not connected."""
        microcontroller.reset()
    finally:
        try:
            mqtt_client.publish(topic, message, retain=True)
        except (OSError, MQTT.MMQTTException):
            microcontroller.reset()


async def update_rtc():
    """Updates the rtc time from the Internet."""
    response = requests.get("http://worldclockapi.com/api/json/est/now").json()

    day_lookup = {
        "Monday": 0,
        "Tuesday": 1,
        "Wednesday": 2,
        "Thursday": 3,
        "Friday": 4,
        "Saturday": 5,
        "Sunday": 6,
    }

    datetime = response["currentDateTime"]
    tm_year = int(datetime[0:4])
    tm_mon = int(datetime[5:7])
    tm_mday = int(datetime[8:10])
    tm_hour = int(datetime[11:13])
    tm_min = int(datetime[14:16])
    tm_sec = int(datetime[17:19])
    tm_wday = day_lookup[response["dayOfTheWeek"]]
    tm_yday = -1
    tm_isdst = response["isDayLightSavingsTime"]

    rtc.datetime = time.struct_time(
        (tm_year, tm_mon, tm_mday, tm_hour, tm_min, tm_sec, tm_wday, tm_yday, tm_isdst)
    )


async def check_open_time():
    """Checks to see if the door is open (and closes) during a time that it should be closed."""
    if (
        rtc.datetime.tm_hour >= config["GARAGE_CLOSE_AFTER"]
        or rtc.datetime.tm_hour <= config["GARAGE_CLOSE_BEFORE"]
    ):
        if garage_state.door_status == "Open":
            if garage_state.door_error is False:
                if garage_state.motion is False:
                    asyncio.gather(asyncio.create_task(activate_opener()))
                    await asyncio.sleep(config["DOOR_CLOSE_DELAY"])
                    garage_state.door_status = get_door_state()
                    if garage_state.door_status == "Open":
                        garage_state.door_error = True
                        door_error_time = time.monotonic()
                    publish_to_mqtt(MQTT_GARAGE_STATE, json.dumps(garage_state))
            elif time.monotonic() > door_error_time + 900:
                garage_state.door_error = False


async def main():
    """main function to start other tasks."""
    tasks = []
    tasks.append(asyncio.create_task(get_motion_state()))
    tasks.append(asyncio.create_task(get_door_state()))
    tasks.append(asyncio.create_task(get_sensor_data()))
    tasks.append(asyncio.create_task(get_system_data()))
    tasks.append(asyncio.create_task(update_rtc()))
    tasks.append(asyncio.create_task(check_open_time()))
    tasks.append(asyncio.create_task(mqtt_client_loop()))
    tasks.append(asyncio.create_task(mqtt_publish_loop()))

    await asyncio.gather(*tasks)


rtc = rtc.RTC()

relay = digitalio.DigitalInOut(board.IO33)
relay.switch_to_output(False)

hcsr04 = adafruit_hcsr04.HCSR04(
    trigger_pin=board.IO17, echo_pin=board.IO18, timeout=0.5
)

garage_state = Garage_state()


try:
    print("Connecting to %s..." % secrets["ssid"])
    wifi.radio.connect(secrets["ssid"], secrets["password"])
    print("Connected to %s!" % secrets["ssid"])
    pool = socketpool.SocketPool(wifi.radio)
    requests = adafruit_requests.Session(pool, ssl.create_default_context())
except (OSError, RuntimeError) as error:
    print("Could not initialize network. {}".format(error))
    raise

MQTT_ENVIRONMENT = secrets["mqtt_topic"] + "/environment"
MQTT_GARAGE_STATE = secrets["mqtt_topic"] + "/garage-state"
MQTT_DOOR_REQUEST = secrets["mqtt_topic"] + "/door-request"
MQTT_SYSTEM = secrets["mqtt_topic"] + "/system"

try:
    mqtt_client = MQTT.MQTT(
        broker=secrets["mqtt_broker"],
        port=secrets["mqtt_port"],
        username=secrets["mqtt_username"],
        password=secrets["mqtt_password"],
        socket_pool=pool,
        ssl_context=ssl.create_default_context(),
    )
    mqtt_client.on_message = message

    print("Connecting to %s" % secrets["mqtt_broker"])
    mqtt_client.connect()
    print("Connected to %s" % secrets["mqtt_broker"])
    mqtt_client.subscribe(MQTT_DOOR_REQUEST)
except (MQTT.MMQTTException, OSError) as error:
    print("Could not connect to mqtt broker. {}".format(error))
    raise

asyncio.run(main())
