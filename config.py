# SPDX-FileCopyrightText: 2022 Daniel Griswold
#
# SPDX-License-Identifier: MIT
""" Configuration items """
config = {
    "SENSOR_UPDATE_INTERVAL": 60,
    "RTC_UPDATE_INTERVAL": 43200,
    "DOOR_CLOSE_DELAY": 12,  # Amount of time it takes for the door to close or open.
    "MOTION_TIMEOUT": 300,  # How long to wait for no motion before closing door.
    "DOOR_DISTANCE": 90,  # Distance from sonar to door when opened.
    "GARAGE_CLOSE_AFTER": 20,  # Hour number to close the door after.
    "GARAGE_CLOSE_BEFORE": 7,  # Hour number to stop closing the door after.
    # AFTER 20 and BEFORE 7 means the door will close automatically between 8pm and 7am
}
